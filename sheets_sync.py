"""
sheets_sync.py — Green Gregory Group Opportunity OS
Phase 2: Google Sheets sync via gspread

Usage:
    python sheets_sync.py                   # sync to Sheets (requires creds)
    python sheets_sync.py --dry-run         # print what would be written, no creds needed
    python sheets_sync.py --sheet pipeline  # only sync one tab

Target spreadsheet ID:
    173j8XDlDeBvsFdRyqBZ6oGhNzIyCntDO_esqRMvdZ1w

Tabs written:
    Pipeline     — hot matches ranked by score
    P&L Summary  — totals by source/status
    Feed Summary — raw listing counts

Setup (one-time for live sync):
    1. console.cloud.google.com → New project → Enable Sheets API + Drive API
    2. IAM → Service Account → Create → Download JSON key
    3. Set GOOGLE_CREDENTIALS_JSON env var to the full JSON content
    4. Share the Google Sheet with the service account email (Editor)
    5. Set GGG_SPREADSHEET_ID env var  OR  edit SPREADSHEET_ID below

Environment variables:
    GOOGLE_CREDENTIALS_JSON   Full JSON of service account key
    GGG_SPREADSHEET_ID        Sheet ID (overrides hardcoded value below)
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
SPREADSHEET_ID = "173j8XDlDeBvsFdRyqBZ6oGhNzIyCntDO_esqRMvdZ1w"  # Green Gregory Opportunity OS
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sheets_sync")

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

TAB_HEADERS = {
    "Pipeline": [
        "Score", "Title", "Source", "Location",
        "Price (Raw)", "Price Est $", "Profit Est $", "Margin Est %",
        "Buyer", "End Date", "Status", "Matched At", "URL"
    ],
    "P&L Summary": [
        "Source", "Status", "Deal Count", "Avg Score",
        "Total Buy $", "Total Profit Est $", "Avg Margin %", "Last Match"
    ],
    "Feed Summary": [
        "Source", "Status", "Count", "Last Scraped"
    ],
}


# ── DB queries ─────────────────────────────────────────────────────────────────

def get_pipeline_rows(conn) -> list:
    rows = conn.execute("""
        SELECT
            m.score,
            r.title,
            r.source,
            r.location,
            COALESCE(r.price_raw, ''),
            COALESCE(CAST(ROUND(r.price_est, 2) AS TEXT), ''),
            COALESCE(CAST(ROUND(m.profit_est, 2) AS TEXT), ''),
            COALESCE(CAST(ROUND(m.margin_est * 100, 1) AS TEXT) || '%', ''),
            m.buyer_name,
            COALESCE(r.end_date, ''),
            m.status,
            m.matched_at,
            r.url
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 500
    """).fetchall()
    return [list(r) for r in rows]


def get_pl_summary(conn) -> list:
    rows = conn.execute("""
        SELECT
            r.source,
            m.status,
            COUNT(*) AS deal_count,
            ROUND(AVG(m.score), 1) AS avg_score,
            ROUND(SUM(r.price_est), 2) AS total_buy,
            ROUND(SUM(m.profit_est), 2) AS total_profit_est,
            ROUND(AVG(m.margin_est) * 100, 1) AS avg_margin_pct,
            MAX(m.matched_at) AS last_match
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        GROUP BY r.source, m.status
        ORDER BY total_profit_est DESC
    """).fetchall()
    return [list(r) for r in rows]


def get_feed_summary(conn) -> list:
    rows = conn.execute("""
        SELECT source, status, COUNT(*) AS count, MAX(scraped_at) AS last_scraped
        FROM raw_listings
        GROUP BY source, status
        ORDER BY last_scraped DESC
    """).fetchall()
    return [list(r) for r in rows]


# ── Dry-run preview ────────────────────────────────────────────────────────────

def print_dry_run(pipeline: list, pl: list, feed: list,
                  sheet_filter: str, spreadsheet_id: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print()
    print("╔" + "═" * 72 + "╗")
    print("║  SHEETS SYNC DRY-RUN PREVIEW — no credentials needed               ║")
    print(f"║  Target spreadsheet ID: {spreadsheet_id[:46]:<46} ║")
    print(f"║  Sync time: {now:<59} ║")
    print("╠" + "═" * 72 + "╣")

    tabs = {
        "Pipeline":    (pipeline, TAB_HEADERS["Pipeline"]),
        "P&L Summary": (pl,       TAB_HEADERS["P&L Summary"]),
        "Feed Summary": (feed,     TAB_HEADERS["Feed Summary"]),
    }

    for tab_name, (data, header) in tabs.items():
        if sheet_filter not in ("all",) and sheet_filter.lower() not in tab_name.lower():
            continue
        print(f"║")
        print(f"║  TAB: {tab_name}  ({len(data)} data rows)")
        print(f"║  {'  |  '.join(h[:12] for h in header[:6])}")
        print("║  " + "─" * 70)
        if not data:
            print("║    (no rows — run scanner.py --seed-test-data then matcher.py)")
        for row in data[:5]:
            cells = "  |  ".join(str(v)[:12] for v in row[:6])
            print(f"║    {cells}")
        if len(data) > 5:
            print(f"║    ... and {len(data)-5} more rows")

    print("╚" + "═" * 72 + "╝")
    print()


# ── Auth ───────────────────────────────────────────────────────────────────────

def get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError(
            "gspread not installed. Run:\n"
            "  pip install gspread google-auth"
        )

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(creds_json)
            tmp_path = f.name
        creds = Credentials.from_service_account_file(tmp_path, scopes=SCOPES)
        Path(tmp_path).unlink(missing_ok=True)
    elif Path("credentials.json").exists():
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "No Google credentials found.\n"
            "  Set GOOGLE_CREDENTIALS_JSON env var, or place credentials.json here.\n"
            "  Use --dry-run to preview without credentials."
        )

    import gspread
    return gspread.authorize(creds)


# ── Sheet writer ──────────────────────────────────────────────────────────────

def ensure_worksheet(spreadsheet, title: str):
    try:
        import gspread
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=20)


def clear_and_write(ws, header: list, data_rows: list):
    ws.clear()
    all_rows = [header] + data_rows
    if all_rows:
        ws.update("A1", all_rows, value_input_option="RAW")
    log.info(f"  '{ws.title}' updated — {len(data_rows)} rows")


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_live(spreadsheet_id: str, sheet_filter: str,
              pipeline: list, pl: list, feed: list):
    client      = get_gspread_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    log.info(f"Connected to: '{spreadsheet.title}'")

    if sheet_filter in ("pipeline", "all"):
        clear_and_write(
            ensure_worksheet(spreadsheet, "Pipeline"),
            TAB_HEADERS["Pipeline"], pipeline
        )
    if sheet_filter in ("pl", "all"):
        clear_and_write(
            ensure_worksheet(spreadsheet, "P&L Summary"),
            TAB_HEADERS["P&L Summary"], pl
        )
    if sheet_filter in ("feed", "all"):
        clear_and_write(
            ensure_worksheet(spreadsheet, "Feed Summary"),
            TAB_HEADERS["Feed Summary"], feed
        )

    # Meta tab
    try:
        meta = ensure_worksheet(spreadsheet, "_meta")
        meta.update("A1", [
            ["Last Sync", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ["Spreadsheet ID", spreadsheet_id],
        ])
    except Exception:
        pass

    log.info("Sheets sync complete.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory Sheets sync")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written — no credentials needed")
    parser.add_argument("--sheet",
                        choices=["pipeline", "pl", "feed", "all"], default="all")
    args = parser.parse_args()

    conn     = get_db_conn()
    pipeline = get_pipeline_rows(conn)
    pl       = get_pl_summary(conn)
    feed     = get_feed_summary(conn)
    conn.close()

    spreadsheet_id = os.environ.get("GGG_SPREADSHEET_ID", SPREADSHEET_ID).strip()

    log.info(f"Data ready — Pipeline: {len(pipeline)} | P&L: {len(pl)} | Feed: {len(feed)}")

    if args.dry_run:
        print_dry_run(pipeline, pl, feed, args.sheet, spreadsheet_id)
        log.info("Dry-run complete — nothing written to Sheets.")
        return

    sync_live(spreadsheet_id, args.sheet, pipeline, pl, feed)


def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


if __name__ == "__main__":
    main()
