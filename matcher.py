"""
matcher.py — Green Gregory Group Opportunity OS
Phase 2: Two-sided matching engine

Reads raw_listings (supply side) → scores against buyer profiles →
writes to matches table → prints hot board.

Usage:
    python matcher.py               # run matching on all new listings
    python matcher.py --board       # print hot board, skip matching
    python matcher.py --min-score 60
    python matcher.py --dry-run
"""

import sqlite3
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("matcher")

# ── Buyer profiles ────────────────────────────────────────────────────────────
# Add a real buyer: copy one block, update id/buyer_name/keywords/prices.

BUYER_PROFILES = [
    {
        "id":            "buyer_network_it",
        "buyer_name":    "IT Resellers / eBay / NetworkHardware",
        "keywords":      ["cisco", "juniper", "switch", "router", "firewall",
                          "network", "server", "rack", "module", "sfp", "ap",
                          "access point", "wifi", "wireless", "telecom"],
        "max_buy_price": 5000,
        "target_margin": 0.40,
        "score_weight":  25,
        "notes":         "High velocity on eBay. Cisco and Juniper move fastest.",
    },
    {
        "id":            "buyer_pallet_lots",
        "buyer_name":    "Amazon FBA / Liquidators",
        "keywords":      ["pallet", "lot", "bulk", "liquidation", "surplus",
                          "overstock", "mixed", "wholesale", "truckload",
                          "racking", "shelving"],
        "max_buy_price": 3000,
        "target_margin": 0.50,
        "score_weight":  20,
        "notes":         "Pallet lots can 3x if you know the manifest.",
    },
    {
        "id":            "buyer_industrial",
        "buyer_name":    "Industrial Equipment Dealers",
        "keywords":      ["forklift", "generator", "compressor", "pump", "hvac",
                          "industrial", "machinery", "crane", "lift", "motor",
                          "scrubber", "floor scrubber", "pressure washer"],
        "max_buy_price": 15000,
        "target_margin": 0.30,
        "score_weight":  20,
        "notes":         "Larger ticket. Slower turn but higher margin dollars.",
    },
    {
        "id":            "buyer_office",
        "buyer_name":    "Office Liquidators / Facebook Marketplace",
        "keywords":      ["desk", "chair", "furniture", "office", "cubicle",
                          "filing", "copier", "printer", "monitor"],
        "max_buy_price": 1500,
        "target_margin": 0.45,
        "score_weight":  15,
        "notes":         "Local pickup plays well. Quick flip in metro areas.",
    },
    {
        "id":            "buyer_food_equip",
        "buyer_name":    "Restaurant Equipment Dealers / eBay",
        "keywords":      ["ice machine", "refrigerator", "freezer", "fryer",
                          "commercial kitchen", "oven", "grill", "dishwasher",
                          "cafeteria", "food service"],
        "max_buy_price": 3000,
        "target_margin": 0.45,
        "score_weight":  25,
        "notes":         "Manitowoc / Hoshizaki ice machines flip 2-3x. Strong eBay market.",
    },
]

# ── DB setup ──────────────────────────────────────────────────────────────────

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


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_listing(listing, profile: dict):
    title    = (listing["title"] or "").lower()
    desc     = (listing["description"] or "").lower()
    combined = f"{title} {desc}"

    hits = [kw for kw in profile["keywords"] if kw.lower() in combined]
    if not hits:
        return None

    raw_score = len(hits) * profile["score_weight"]
    score     = min(raw_score, 100)

    price      = listing["price_est"]
    margin_est = None
    profit_est = None

    if price and price > 0:
        max_buy = profile["max_buy_price"]
        if price <= max_buy:
            sell_est   = price / (1 - profile["target_margin"])
            profit_est = sell_est - price
            margin_est = profile["target_margin"]
            # Price well under ceiling → boost confidence
            ratio = price / max_buy
            if ratio < 0.25:
                score = min(score + 20, 100)
            elif ratio < 0.50:
                score = min(score + 10, 100)
        else:
            score = max(score - 15, 0)

    return {
        "id":               f"{listing['id']}_{profile['id']}",
        "listing_id":       listing["id"],
        "buyer_id":         profile["id"],
        "buyer_name":       profile["buyer_name"],
        "score":            score,
        "price_est":        price,
        "margin_est":       margin_est,
        "profit_est":       profit_est,
        "matched_keywords": json.dumps(hits),
        "matched_at":       datetime.now(timezone.utc).isoformat(),
        "status":           "hot",
        "notes":            profile["notes"],
    }


# ── Matching run ──────────────────────────────────────────────────────────────

def run_matching(conn, min_score: int = 40, dry_run: bool = False) -> dict:
    listings = conn.execute(
        "SELECT * FROM raw_listings WHERE status = 'new' ORDER BY scraped_at DESC"
    ).fetchall()

    n_listings  = len(listings)
    n_matches   = 0
    n_hot       = 0

    log.info(f"  Raw listings (status=new): {n_listings}")
    log.info(f"  Buyer profiles:            {len(BUYER_PROFILES)}")
    log.info(f"  Min score threshold:       {min_score}")

    for listing in listings:
        best_score = 0
        for profile in BUYER_PROFILES:
            match = score_listing(listing, profile)
            if not match or match["score"] < min_score:
                continue
            best_score = max(best_score, match["score"])

            existing = conn.execute(
                "SELECT id FROM matches WHERE id = ?", (match["id"],)
            ).fetchone()
            if existing:
                continue

            n_matches += 1
            n_hot     += 1
            if not dry_run:
                conn.execute("""
                    INSERT INTO matches
                        (id, listing_id, buyer_id, buyer_name, score, price_est,
                         margin_est, profit_est, matched_keywords, matched_at, status, notes)
                    VALUES
                        (:id, :listing_id, :buyer_id, :buyer_name, :score, :price_est,
                         :margin_est, :profit_est, :matched_keywords, :matched_at, :status, :notes)
                """, match)
                log.info(
                    f"  MATCH [{match['score']:3d}]  "
                    f"{(listing['title'] or '')[:48]:<48}  →  {match['buyer_name'][:28]}"
                )
            else:
                log.info(
                    f"  DRY   [{match['score']:3d}]  "
                    f"{(listing['title'] or '')[:48]:<48}  →  {match['buyer_name'][:28]}"
                )

        if best_score >= min_score and not dry_run:
            conn.execute(
                "UPDATE raw_listings SET status = 'matched' WHERE id = ?",
                (listing["id"],)
            )

    if not dry_run:
        conn.commit()

    return {"listings_read": n_listings, "matches_created": n_matches, "hot_count": n_hot}


# ── Hot board ─────────────────────────────────────────────────────────────────

def print_hot_board(conn, limit: int = 25):
    rows = conn.execute("""
        SELECT m.score, m.buyer_name, m.price_est, m.profit_est,
               r.title, r.source, r.location, r.url, m.status
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT ?
    """, (limit,)).fetchall()

    total_hot = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status = 'hot'"
    ).fetchone()[0]
    total_all = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    print()
    print("═" * 95)
    print(f"  🔥  GREEN GREGORY GROUP — HOT MATCH BOARD  "
          f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")
    print(f"  Total matches in DB: {total_all}   Hot: {total_hot}   Showing top {min(limit, total_hot)}")
    print("═" * 95)

    if not rows:
        print("  (no hot matches yet — run:  python scanner.py --seed-test-data  then re-run matcher)")
        print("═" * 95)
        return

    print(f"  {'SCR':>3}  {'TITLE':<42}  {'SOURCE':<13}  {'BUY':>8}  {'EST PROFIT':>11}  BUYER")
    print("─" * 95)
    for r in rows:
        profit_s = f"${r['profit_est']:>8,.0f}" if r["profit_est"] else "       N/A"
        price_s  = f"${r['price_est']:>6,.0f}"  if r["price_est"]  else "     N/A"
        title    = (r["title"] or "")[:42]
        print(
            f"  {r['score']:3d}  {title:<42}  {r['source']:<13}  "
            f"{price_s}  {profit_s}  {r['buyer_name'][:28]}"
        )
    print("═" * 95)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory matcher")
    parser.add_argument("--min-score", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--board", action="store_true",
                        help="Print hot board and exit without re-running matching")
    args = parser.parse_args()

    conn = get_db()

    if args.board:
        print_hot_board(conn)
        conn.close()
        return

    log.info("── Running matching ──")
    stats = run_matching(conn, min_score=args.min_score, dry_run=args.dry_run)

    print()
    print("── Matching summary ──────────────────────────────")
    print(f"  Raw listings read  : {stats['listings_read']}")
    print(f"  New matches created: {stats['matches_created']}")
    print(f"  Hot matches total  : {stats['hot_count']}")
    print()

    print_hot_board(conn)
    conn.close()


if __name__ == "__main__":
    main()
