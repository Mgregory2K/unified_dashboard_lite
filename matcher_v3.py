"""
matcher.py — Green Gregory Group Opportunity OS
Phase 3: Config-driven — reads buyer profiles and scoring from config.json

Usage:
    python matcher.py               # run matching
    python matcher.py --board       # print hot board only
    python matcher.py --dry-run
    python matcher.py --fallback    # use built-in defaults
"""

import sqlite3
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from config_reader import load_config

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("matcher")

MATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id               TEXT PRIMARY KEY,
    listing_id       TEXT REFERENCES raw_listings(id),
    buyer_id         TEXT,
    buyer_name       TEXT,
    score            INTEGER,
    price_est        REAL,
    margin_est       REAL,
    profit_est       REAL,
    matched_keywords TEXT,
    matched_at       TEXT,
    status           TEXT DEFAULT 'hot',
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_match_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_match_score  ON matches(score DESC);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(MATCH_SCHEMA)
    conn.commit()
    return conn


def score_listing(listing, profile, scoring):
    title    = (listing["title"] or "").lower()
    desc     = (listing["description"] or "").lower()
    combined = f"{title} {desc}"

    hits  = [kw for kw in profile["keywords"] if kw.lower() in combined]
    if not hits:
        return None

    score = min(len(hits) * profile["score_weight"], scoring["score_cap"])

    price      = listing["price_est"]
    margin_est = None
    profit_est = None

    if price and price > 0:
        max_buy = profile["max_buy_price"]
        if price <= max_buy:
            sell_est   = price / (1 - profile["target_margin"])
            profit_est = sell_est - price
            margin_est = profile["target_margin"]
            ratio = price / max_buy
            if ratio < 0.25:
                score = min(score + scoring["price_boost_low"], scoring["score_cap"])
            elif ratio < 0.50:
                score = min(score + scoring["price_boost_mid"], scoring["score_cap"])
        else:
            score = max(score - scoring["over_budget_penalty"], 0)

    return {
        "id":               f"{listing['id']}_{profile['id']}",
        "listing_id":       listing["id"],
        "buyer_id":         profile["id"],
        "buyer_name":       profile["name"],
        "score":            score,
        "price_est":        price,
        "margin_est":       margin_est,
        "profit_est":       profit_est,
        "matched_keywords": json.dumps(hits),
        "matched_at":       datetime.now(timezone.utc).isoformat(),
        "status":           "hot",
        "notes":            profile.get("notes",""),
    }


def run_matching(conn, profiles, scoring, dry_run=False):
    min_score = scoring["min_score_match"]
    listings  = conn.execute(
        "SELECT * FROM raw_listings WHERE status = 'new' ORDER BY scraped_at DESC"
    ).fetchall()
    active_profiles = [p for p in profiles if p.get("active", True)]

    log.info(f"  Raw listings (new):  {len(listings)}")
    log.info(f"  Active buyer profiles: {len(active_profiles)}")
    log.info(f"  Min score:           {min_score}")

    n_matches = 0
    for listing in listings:
        best = 0
        for profile in active_profiles:
            m = score_listing(listing, profile, scoring)
            if not m or m["score"] < min_score:
                continue
            best = max(best, m["score"])
            if conn.execute("SELECT id FROM matches WHERE id = ?", (m["id"],)).fetchone():
                continue
            n_matches += 1
            if not dry_run:
                conn.execute("""
                    INSERT INTO matches
                        (id, listing_id, buyer_id, buyer_name, score, price_est,
                         margin_est, profit_est, matched_keywords, matched_at, status, notes)
                    VALUES
                        (:id, :listing_id, :buyer_id, :buyer_name, :score, :price_est,
                         :margin_est, :profit_est, :matched_keywords, :matched_at, :status, :notes)
                """, m)
                log.info(f"  {'MATCH':5} [{m['score']:3d}]  {(listing['title'] or '')[:48]:<48}  →  {m['buyer_name'][:28]}")
            else:
                log.info(f"  {'DRY':5} [{m['score']:3d}]  {(listing['title'] or '')[:48]:<48}  →  {m['buyer_name'][:28]}")

        if best >= min_score and not dry_run:
            conn.execute("UPDATE raw_listings SET status = 'matched' WHERE id = ?", (listing["id"],))

    if not dry_run:
        conn.commit()

    n_hot = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'hot'").fetchone()[0]
    return {"listings_read": len(listings), "matches_created": n_matches, "hot_count": n_hot}


def print_hot_board(conn, limit=25):
    rows = conn.execute("""
        SELECT m.score, m.buyer_name, m.price_est, m.profit_est,
               r.title, r.source, r.location, r.url
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT ?
    """, (limit,)).fetchall()

    total_hot = conn.execute("SELECT COUNT(*) FROM matches WHERE status='hot'").fetchone()[0]
    total_all = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    print()
    print("═" * 95)
    print(f"  🔥  GREEN GREGORY GROUP — HOT MATCH BOARD  ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")
    print(f"  Total matches: {total_all}   Hot: {total_hot}   Showing top {min(limit, total_hot)}")
    print("═" * 95)
    if not rows:
        print("  (no hot matches — run: python scanner.py --seed-test-data  then re-run matcher)")
        print("═" * 95)
        return
    print(f"  {'SCR':>3}  {'TITLE':<42}  {'SOURCE':<13}  {'BUY':>8}  {'EST PROFIT':>11}  BUYER")
    print("─" * 95)
    for r in rows:
        ps = f"${r['profit_est']:>8,.0f}" if r["profit_est"] else "       N/A"
        bs = f"${r['price_est']:>6,.0f}"  if r["price_est"]  else "     N/A"
        print(f"  {r['score']:3d}  {(r['title'] or '')[:42]:<42}  {r['source']:<13}  {bs}  {ps}  {r['buyer_name'][:28]}")
    print("═" * 95)
    print()


def main():
    parser = argparse.ArgumentParser(description="Green Gregory matcher")
    parser.add_argument("--min-score", type=int, default=None, help="Override config min score")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--board",     action="store_true", help="Print hot board and exit")
    parser.add_argument("--fallback",  action="store_true")
    args = parser.parse_args()

    cfg      = load_config()
    profiles = cfg["buyer_profiles"]
    scoring  = cfg["scoring"]
    if args.min_score is not None:
        scoring["min_score_match"] = args.min_score

    conn = get_db()
    if args.board:
        print_hot_board(conn)
        conn.close()
        return

    log.info("── Running matching ──")
    stats = run_matching(conn, profiles, scoring, dry_run=args.dry_run)

    print()
    print("── Matching summary ─────────────────────────")
    print(f"  Raw listings read  : {stats['listings_read']}")
    print(f"  New matches created: {stats['matches_created']}")
    print(f"  Hot matches total  : {stats['hot_count']}")
    print()

    print_hot_board(conn)
    conn.close()


if __name__ == "__main__":
    main()
