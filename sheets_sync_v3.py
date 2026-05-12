"""
sheets_sync.py — Green Gregory Group Opportunity OS
Phase 3: Writes output tabs to Google Sheet

Output tabs written:
  Pipeline      — hot matches ranked by score
  P&L Summary   — totals by source/status
  Feed Summary  — raw listing counts
  Leads         — business contacts from leadgen

Usage:
    python sheets_sync.py                   # sync all tabs (requires creds)
    python sheets_sync.py --dry-run         # preview without credentials
    python sheets_sync.py --sheet pipeline  # one tab only

Target spreadsheet ID:
    173j8XDlDeBvsFdRyqBZ6oGhNzIyCntDO_esqRMvdZ1w

Secrets:
    GOOGLE_CREDENTIALS_JSON   service account key JSON
    GGG_SPREADSHEET_ID        overrides the hardcoded ID above
"""

import sqlite3
import json
import os
import argparse
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DB_PATH        = Path("opportunity_os.db")
SPREADSHEET_ID = "173j8XDlDeBvsFdRyqBZ6oGhNzIyCntDO_esqRMvdZ1w"
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sheets_sync")

TAB_HEADERS = {
    "Pipeline": [
        "Score", "Title", "Source", "Location",
        "Price (Raw)", "Price Est $", "Profit Est $", "Margin %",
        "Buyer", "End Date", "Status", "Matched At", "URL"
    ],
    "P&L Summary": [
        "Source", "Status", "Deal Count", "Avg Score",
        "Total Buy $", "Total Profit Est $", "Avg Margin %", "Last Match"
    ],
    "Feed Summary": [
        "Source", "Status", "Count", "Last Scraped"
    ],
    "Leads": [
        "Name", "Role", "Category", "City", "State", "ZIP",
        "Phone", "Website", "Rating", "Source ZIP", "Status", "Created"
    ],
}


# ── DB queries ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_pipeline(conn):
    rows = conn.execute("""
        SELECT m.score, r.title, r.source, r.location,
               COALESCE(r.price_raw,''),
               COALESCE(CAST(ROUND(r.price_est,2) AS TEXT),''),
               COALESCE(CAST(ROUND(m.profit_est,2) AS TEXT),''),
               COALESCE(CAST(ROUND(m.margin_est*100,1) AS TEXT)||'%',''),
               m.buyer_name,
               COALESCE(r.end_date,''),
               m.status,
               COALESCE(m.matched_at,'')[:10],
               COALESCE(r.url,'')
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 500
    """).fetchall()
    return [list(r) for r in rows]


def get_pl_summary(conn):
    rows = conn.execute("""
        SELECT r.source, m.status,
               COUNT(*) AS deal_count,
               ROUND(AVG(m.score),1) AS avg_score,
               ROUND(SUM(r.price_est),2) AS total_buy,
               ROUND(SUM(m.profit_est),2) AS total_profit_est,
               ROUND(AVG(m.margin_est)*100,1) AS avg_margin_pct,
               MAX(m.matched_at) AS last_match
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        GROUP BY r.source, m.status
        ORDER BY total_profit_est DESC
    """).fetchall()
    return [list(r) for r in rows]


def get_feed_summary(conn):
    rows = conn.execute("""
        SELECT source, status, COUNT(*) AS count, MAX(scraped_at) AS last_scraped
        FROM raw_listings
        GROUP BY source, status
        ORDER BY last_scraped DESC
    """).fetchall()
    return [list(r) for r in rows]


def get_leads(conn):
    try:
        rows = conn.execute("""
            SELECT name, role, category, city, state, zip,
                   phone, website, rating, source_zip, status, created_at
            FROM leads
            ORDER BY created_at DESC
            LIMIT 1000
        """).fetchall()
        return [list(r) for r in rows]
    except Exception:
        return []  # leads table may not exist yet


# ── Dry-run preview ────────────────────────────────────────────────────────────

def print_dry_run(tabs_data, spreadsheet_id):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print()
    print("╔" + "═" * 72 + "╗")
    print("║  SHEETS SYNC DRY-RUN — no credentials needed                        ║")
    print(f"║  Target: {spreadsheet_id[:60]:<60} ║")
    print(f"║  Time:   {now:<60} ║")
    print("╠" + "═" * 72 + "╣")
    for tab_name, (data, header) in tabs_data.items():
        print(f"║")
        print(f"║  TAB: {tab_name}  →  {len(data)} rows")
        print(f"║  Cols: {', '.join(h[:10] for h in header[:6])}")
        print("║  " + "─" * 70)
        for row in data[:3]:
            print("║    " + "  |  ".join(str(v)[:12] for v in row[:5]))
        if len(data) > 3:
            print(f"║    ... {len(data)-3} more rows")
    print("╚" + "═" * 72 + "╝")
    print()


# ── Auth ───────────────────────────────────────────────────────────────────────

def get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")

    creds_raw = os.environ.get("GOOGLE_CREDENTIALS_JSON","").strip()
    if creds_raw:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(creds_raw)
            tmp = f.name
        creds = Credentials.from_service_account_file(tmp, scopes=SCOPES)
        Path(tmp).unlink(missing_ok=True)
    elif Path("credentials.json").exists():
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "No Google credentials.\n"
            "  Set GOOGLE_CREDENTIALS_JSON env var or place credentials.json here.\n"
            "  Use --dry-run to preview without credentials."
        )
    import gspread
    return gspread.authorize(creds)


def ensure_ws(ss, title):
    try:
        return ss.worksheet(title)
    except Exception:
        return ss.add_worksheet(title=title, rows=1000, cols=20)


def write_tab(ws, header, data):
    ws.clear()
    ws.update("A1", [header] + data, value_input_option="RAW")
    log.info(f"  '{ws.title}': {len(data)} rows written")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory sheets sync")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sheet",
                        choices=["pipeline","pl","feed","leads","all"],
                        default="all")
    args = parser.parse_args()

    conn     = get_db()
    pipeline = get_pipeline(conn)
    pl       = get_pl_summary(conn)
    feed     = get_feed_summary(conn)
    leads    = get_leads(conn)
    conn.close()

    spreadsheet_id = os.environ.get("GGG_SPREADSHEET_ID", SPREADSHEET_ID).strip()
    log.info(f"Data: Pipeline={len(pipeline)} PL={len(pl)} Feed={len(feed)} Leads={len(leads)}")

    all_tabs = {
        "Pipeline":    (pipeline, TAB_HEADERS["Pipeline"]),
        "P&L Summary": (pl,       TAB_HEADERS["P&L Summary"]),
        "Feed Summary": (feed,    TAB_HEADERS["Feed Summary"]),
        "Leads":       (leads,    TAB_HEADERS["Leads"]),
    }

    filter_map = {
        "pipeline": ["Pipeline"],
        "pl":       ["P&L Summary"],
        "feed":     ["Feed Summary"],
        "leads":    ["Leads"],
        "all":      list(all_tabs.keys()),
    }
    tabs_to_write = {k: v for k, v in all_tabs.items() if k in filter_map[args.sheet]}

    if args.dry_run:
        print_dry_run(tabs_to_write, spreadsheet_id)
        log.info("Dry-run complete — nothing written to Sheets.")
        return

    client = get_gspread_client()
    ss     = client.open_by_key(spreadsheet_id)
    log.info(f"Connected: '{ss.title}'")

    for tab_name, (data, header) in tabs_to_write.items():
        write_tab(ensure_ws(ss, tab_name), header, data)

    # _meta tab
    try:
        meta = ensure_ws(ss, "_meta")
        meta.update("A1", [
            ["Last Sync", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ["Spreadsheet ID", spreadsheet_id],
        ])
    except Exception:
        pass

    log.info("Sheets sync complete.")


if __name__ == "__main__":
    main()
