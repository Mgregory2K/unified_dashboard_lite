"""
sheets_sync.py — Green Gregory Group Opportunity OS
Phase 2: Google Sheets sync via gspread

Pushes pipeline (hot matches) and P&L summary to Google Sheets.
Ghost arbitrage view: supply side hidden, buyer side visible per sheet.

Prerequisites:
    pip install gspread google-auth

Setup (one-time):
    1. Go to console.cloud.google.com → New project
    2. Enable "Google Sheets API" and "Google Drive API"
    3. Create a Service Account → download JSON key
    4. Save key as credentials.json (or set GOOGLE_CREDENTIALS_JSON env var)
    5. Share your target Google Sheet with the service account email

Environment variables (or GitHub Actions secrets):
    GOOGLE_CREDENTIALS_JSON   — full JSON content of service account key
    GGG_SPREADSHEET_ID        — the ID from your sheet URL
                                 https://docs.google.com/spreadsheets/d/{THIS_PART}/edit

Usage:
    python sheets_sync.py
    python sheets_sync.py --dry-run
    python sheets_sync.py --sheet pipeline   # only update pipeline tab
"""

import sqlite3
import json
import os
import argparse
import logging
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

DB_PATH = Path("opportunity_os.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("sheets_sync")

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


# ── Auth ───────────────────────────────────────────────────────────────────────

def get_gspread_client():
    if not GSPREAD_AVAILABLE:
        raise ImportError("gspread not installed. Run: pip install gspread google-auth")

    # Prefer env var (for GitHub Actions / CI)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(creds_json)
            tmp_path = f.name
        creds = Credentials.from_service_account_file(tmp_path, scopes=SCOPES)
        Path(tmp_path).unlink(missing_ok=True)
    elif Path("credentials.json").exists():
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "No Google credentials found.\n"
            "Set GOOGLE_CREDENTIALS_JSON env var or place credentials.json in this directory."
        )

    return gspread.authorize(creds)


# ── DB queries ─────────────────────────────────────────────────────────────────

def get_pipeline_rows(conn) -> list[list]:
    """Hot matches for the pipeline tab."""
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
            r.end_date,
            m.status,
            m.matched_at,
            r.url
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 500
    """).fetchall()
    return [list(r) for r in rows]


def get_pl_summary(conn) -> list[list]:
    """P&L summary by source and status."""
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


def get_raw_feed_summary(conn) -> list[list]:
    """Recent raw listings summary."""
    rows = conn.execute("""
        SELECT
            source,
            status,
            COUNT(*) AS count,
            MAX(scraped_at) AS last_scraped
        FROM raw_listings
        GROUP BY source, status
        ORDER BY last_scraped DESC
    """).fetchall()
    return [list(r) for r in rows]


# ── Sheet updater ──────────────────────────────────────────────────────────────

def clear_and_write(ws, header: list[str], data_rows: list[list]):
    """Clear worksheet, write header row, then data."""
    ws.clear()
    all_rows = [header] + data_rows
    if all_rows:
        ws.update("A1", all_rows, value_input_option="RAW")
    log.info(f"  '{ws.title}' updated: {len(data_rows)} rows")


def ensure_worksheet(spreadsheet, title: str):
    """Get or create a worksheet by title."""
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=20)


# ── Main sync ──────────────────────────────────────────────────────────────────

def sync(spreadsheet_id: str, sheet_filter: str = "all", dry_run: bool = False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Fetch all data regardless of dry_run (we need to show counts)
    pipeline_rows = get_pipeline_rows(conn)
    pl_rows       = get_pl_summary(conn)
    feed_rows     = get_raw_feed_summary(conn)
    conn.close()

    log.info(f"Data ready — Pipeline: {len(pipeline_rows)} rows | P&L: {len(pl_rows)} | Feed: {len(feed_rows)}")

    if dry_run:
        log.info(f"DRY RUN — would sync to spreadsheet ID: {spreadsheet_id}")
        log.info(f"  [Pipeline] {len(pipeline_rows)} rows")
        log.info(f"  [P&L]      {len(pl_rows)} rows")
        log.info(f"  [Feed]     {len(feed_rows)} rows")
        return

    client      = get_gspread_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    log.info(f"Connected to: '{spreadsheet.title}'")

    if sheet_filter in ("pipeline", "all"):
        ws = ensure_worksheet(spreadsheet, "Pipeline")
        header = [
            "Score", "Title", "Source", "Location",
            "Price (Raw)", "Price Est", "Profit Est", "Margin Est",
            "Buyer", "End Date", "Status", "Matched At", "URL"
        ]
        clear_and_write(ws, header, pipeline_rows)

    if sheet_filter in ("pl", "all"):
        ws = ensure_worksheet(spreadsheet, "P&L Summary")
        header = [
            "Source", "Status", "Deal Count", "Avg Score",
            "Total Buy $", "Total Profit Est $", "Avg Margin %", "Last Match"
        ]
        clear_and_write(ws, header, pl_rows)

    if sheet_filter in ("feed", "all"):
        ws = ensure_worksheet(spreadsheet, "Feed Summary")
        header = ["Source", "Status", "Count", "Last Scraped"]
        clear_and_write(ws, header, feed_rows)

    # Meta tab — last sync timestamp
    try:
        meta_ws = ensure_worksheet(spreadsheet, "_meta")
        meta_ws.update("A1", [["Last Sync", now_str]])
    except Exception:
        pass

    log.info(f"Sheets sync complete — {now_str}")


def main():
    parser = argparse.ArgumentParser(description="Green Gregory Sheets sync")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sheet", choices=["pipeline", "pl", "feed", "all"], default="all")
    args = parser.parse_args()

    spreadsheet_id = os.environ.get("GGG_SPREADSHEET_ID", "")
    if not spreadsheet_id and not args.dry_run:
        log.error("GGG_SPREADSHEET_ID env var not set. See setup instructions at top of file.")
        return

    sync(spreadsheet_id, sheet_filter=args.sheet, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
