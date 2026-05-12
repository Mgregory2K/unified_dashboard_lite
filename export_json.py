"""
export_json.py — Green Gregory Group Opportunity OS
Phase 3: Generates dashboard.json for GitHub Pages dashboard

Reads SQLite → builds compact JSON → writes to docs/dashboard.json
(GitHub Pages serves the docs/ folder of the repo).

Usage:
    python export_json.py                      # write docs/dashboard.json
    python export_json.py --out dashboard.json # custom output path
    python export_json.py --dry-run            # print JSON, don't write
"""

import sqlite3
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("export_json")

DEFAULT_OUT = Path("docs/dashboard.json")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT,
            state TEXT, zip TEXT, phone TEXT, website TEXT,
            rating REAL, review_count INTEGER, role TEXT, category TEXT,
            source_zip TEXT, notes TEXT, status TEXT DEFAULT 'new', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY, listing_id TEXT, buyer_id TEXT, buyer_name TEXT,
            score INTEGER, price_est REAL, margin_est REAL, profit_est REAL,
            matched_keywords TEXT, matched_at TEXT, status TEXT DEFAULT 'hot', notes TEXT
        );
    """)
    conn.commit()
    return conn


def build_dashboard(conn) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    # ── Pipeline (hot matches, top 100) ───────────────────────────────────────
    pipeline_rows = conn.execute("""
        SELECT
            m.score,
            m.buyer_name,
            m.status        AS match_status,
            m.price_est,
            m.profit_est,
            m.margin_est,
            m.matched_at,
            r.title,
            r.source,
            r.location,
            r.url,
            r.end_date,
            r.price_raw
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 100
    """).fetchall()

    pipeline = []
    for r in pipeline_rows:
        pipeline.append({
            "score":       r["score"],
            "title":       r["title"] or "",
            "source":      r["source"] or "",
            "location":    r["location"] or "",
            "url":         r["url"] or "",
            "price_raw":   r["price_raw"] or "",
            "price_est":   round(r["price_est"], 2) if r["price_est"] else None,
            "profit_est":  round(r["profit_est"], 2) if r["profit_est"] else None,
            "margin_pct":  round(r["margin_est"] * 100, 1) if r["margin_est"] else None,
            "buyer":       r["buyer_name"] or "",
            "end_date":    (r["end_date"] or "")[:10],
            "matched_at":  (r["matched_at"] or "")[:10],
        })

    # ── P&L summary ───────────────────────────────────────────────────────────
    pl_rows = conn.execute("""
        SELECT
            r.source,
            m.status,
            COUNT(*)                          AS deal_count,
            ROUND(AVG(m.score), 1)            AS avg_score,
            ROUND(SUM(r.price_est), 2)        AS total_buy,
            ROUND(SUM(m.profit_est), 2)       AS total_profit_est,
            ROUND(AVG(m.margin_est) * 100, 1) AS avg_margin_pct
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        GROUP BY r.source, m.status
        ORDER BY total_profit_est DESC
    """).fetchall()

    pl_summary = [dict(r) for r in pl_rows]

    # ── Feed stats ────────────────────────────────────────────────────────────
    feed_rows = conn.execute("""
        SELECT source, status, COUNT(*) AS count, MAX(scraped_at) AS last_scraped
        FROM raw_listings
        GROUP BY source, status
        ORDER BY last_scraped DESC
    """).fetchall()

    feed_summary = [dict(r) for r in feed_rows]

    # ── Leads (top 200 by newest) ─────────────────────────────────────────────
    lead_rows = conn.execute("""
        SELECT name, address, city, state, zip, phone, website,
               rating, role, category, source_zip, status, created_at
        FROM leads
        ORDER BY created_at DESC
        LIMIT 200
    """).fetchall()

    leads = [dict(r) for r in lead_rows]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    kpi = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM raw_listings)                       AS total_listings,
            (SELECT COUNT(*) FROM raw_listings WHERE status='new')    AS new_listings,
            (SELECT COUNT(*) FROM matches WHERE status='hot')         AS hot_matches,
            (SELECT COUNT(*) FROM leads)                              AS total_leads,
            (SELECT ROUND(SUM(profit_est),2) FROM matches
             WHERE status='hot' AND profit_est IS NOT NULL)           AS total_profit_est,
            (SELECT ROUND(AVG(score),1) FROM matches WHERE status='hot') AS avg_score
    """).fetchone()

    return {
        "generated_at":  now,
        "kpi": {
            "total_listings":  kpi["total_listings"]  or 0,
            "new_listings":    kpi["new_listings"]    or 0,
            "hot_matches":     kpi["hot_matches"]     or 0,
            "total_leads":     kpi["total_leads"]     or 0,
            "total_profit_est": kpi["total_profit_est"] or 0,
            "avg_score":       kpi["avg_score"]       or 0,
        },
        "pipeline":     pipeline,
        "pl_summary":   pl_summary,
        "feed_summary": feed_summary,
        "leads":        leads,
    }


def main():
    parser = argparse.ArgumentParser(description="Green Gregory export_json")
    parser.add_argument("--out",     default=str(DEFAULT_OUT), help="Output path for dashboard.json")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON, don't write file")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error(f"DB not found: {DB_PATH}. Run scanner.py --seed-test-data first.")
        return

    conn = get_db()
    data = build_dashboard(conn)
    conn.close()

    out_json = json.dumps(data, indent=2)

    if args.dry_run:
        # Print summary, not the whole blob
        print(f"Generated at:    {data['generated_at']}")
        print(f"KPIs:            {data['kpi']}")
        print(f"Pipeline rows:   {len(data['pipeline'])}")
        print(f"P&L rows:        {len(data['pl_summary'])}")
        print(f"Feed rows:       {len(data['feed_summary'])}")
        print(f"Lead rows:       {len(data['leads'])}")
        print(f"JSON size:       {len(out_json):,} bytes")
        log.info("Dry-run — nothing written.")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_json, encoding="utf-8")

    log.info(f"dashboard.json written → {out_path.resolve()}")
    log.info(f"  Pipeline:  {len(data['pipeline'])} deals")
    log.info(f"  P&L rows:  {len(data['pl_summary'])}")
    log.info(f"  Leads:     {len(data['leads'])}")
    log.info(f"  Size:      {len(out_json):,} bytes")


if __name__ == "__main__":
    main()
