"""
matcher.py — Green Gregory Group Opportunity OS
Phase 2: Two-sided matching engine

Reads raw_listings (supply side) and buyer_profiles (demand side),
scores matches, writes to matches table, flags hot deals.

Run after scanner.py:
    python matcher.py
    python matcher.py --min-score 60  # raise threshold
    python matcher.py --dry-run
"""

import sqlite3
import json
import re
import argparse
import logging
from datetime import datetime
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("matcher")

# ── Buyer / Demand profiles ───────────────────────────────────────────────────
# These are your known buyers or target resale categories.
# Add real buyers as you build your network.
# Each profile defines what they want and what they'll pay (rough multiplier).
#
# score_weight: how much a keyword match counts (0-100 per hit)
# max_buy_price: upper limit you'd pay to flip this category
# target_margin: minimum gross margin % you require
# buyer_name: who you'd call to move this

BUYER_PROFILES = [
    {
        "id": "buyer_network_it",
        "buyer_name": "IT Resellers / eBay / NetworkHardware",
        "keywords": ["cisco", "juniper", "switch", "router", "firewall",
                     "network", "server", "rack", "module", "SFP", "AP",
                     "access point", "wifi", "wireless", "telecom"],
        "max_buy_price": 5000,
        "target_margin": 0.40,
        "score_weight": 25,
        "notes": "High velocity on eBay. Cisco and Juniper move fastest.",
    },
    {
        "id": "buyer_pallet_lots",
        "buyer_name": "Amazon FBA / Liquidators",
        "keywords": ["pallet", "lot", "bulk", "liquidation", "surplus",
                     "overstock", "mixed", "wholesale", "truckload"],
        "max_buy_price": 3000,
        "target_margin": 0.50,
        "score_weight": 20,
        "notes": "Pallet lots can 3x if you know the manifest.",
    },
    {
        "id": "buyer_industrial",
        "buyer_name": "Industrial Equipment Dealers",
        "keywords": ["forklift", "generator", "compressor", "pump", "HVAC",
                     "industrial", "machinery", "crane", "lift", "motor"],
        "max_buy_price": 15000,
        "target_margin": 0.30,
        "score_weight": 20,
        "notes": "Larger ticket. Slower turn but higher margin dollars.",
    },
    {
        "id": "buyer_office",
        "buyer_name": "Office Liquidators / Facebook Marketplace",
        "keywords": ["desk", "chair", "furniture", "office", "cubicle",
                     "filing", "copier", "printer", "monitor"],
        "max_buy_price": 1500,
        "target_margin": 0.45,
        "score_weight": 15,
        "notes": "Local pickup plays well. Quick flip in metro areas.",
    },
    {
        "id": "buyer_av_media",
        "buyer_name": "AV Integrators / Rental Companies",
        "keywords": ["projector", "display", "audio", "video", "AV",
                     "camera", "lighting", "stage", "PA system", "mixer"],
        "max_buy_price": 4000,
        "target_margin": 0.40,
        "score_weight": 20,
        "notes": "Strong demand from small event companies.",
    },
]

# ── Database setup ─────────────────────────────────────────────────────────────

MATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT REFERENCES raw_listings(id),
    buyer_id        TEXT,
    buyer_name      TEXT,
    score           INTEGER,
    price_est       REAL,
    margin_est      REAL,
    profit_est      REAL,
    matched_keywords TEXT,
    matched_at      TEXT,
    status          TEXT DEFAULT 'hot',  -- hot | reviewed | passed | won
    notes           TEXT
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


def score_listing(listing: sqlite3.Row, profile: dict) -> dict | None:
    """
    Score a listing against one buyer profile.
    Returns match dict if score > 0, else None.
    """
    title = (listing["title"] or "").lower()
    desc  = (listing["description"] or "").lower()
    combined = f"{title} {desc}"

    hits = [kw for kw in profile["keywords"] if kw.lower() in combined]
    if not hits:
        return None

    # Base score: keyword density
    raw_score = len(hits) * profile["score_weight"]
    score = min(raw_score, 100)  # cap at 100

    # Bonus: price below max_buy_price → higher confidence
    price = listing["price_est"]
    margin_est = None
    profit_est = None

    if price and price > 0:
        max_buy = profile["max_buy_price"]
        if price <= max_buy:
            # Estimated sell price = buy price / (1 - target_margin)
            sell_est = price / (1 - profile["target_margin"])
            profit_est = sell_est - price
            margin_est = profile["target_margin"]
            # Boost score if price is well under max
            price_ratio = price / max_buy
            if price_ratio < 0.25:
                score = min(score + 20, 100)
            elif price_ratio < 0.50:
                score = min(score + 10, 100)
        else:
            # Over budget — soft penalize
            score = max(score - 15, 0)

    match_id = f"{listing['id']}_{profile['id']}"

    return {
        "id":               match_id,
        "listing_id":       listing["id"],
        "buyer_id":         profile["id"],
        "buyer_name":       profile["buyer_name"],
        "score":            score,
        "price_est":        price,
        "margin_est":       margin_est,
        "profit_est":       profit_est,
        "matched_keywords": json.dumps(hits),
        "matched_at":       datetime.utcnow().isoformat(),
        "status":           "hot",
        "notes":            profile["notes"],
    }


def run_matching(conn, min_score: int = 40, dry_run: bool = False) -> int:
    """Match all 'new' listings against all buyer profiles."""
    listings = conn.execute(
        "SELECT * FROM raw_listings WHERE status = 'new' ORDER BY scraped_at DESC"
    ).fetchall()
    log.info(f"Matching {len(listings)} new listings against {len(BUYER_PROFILES)} buyer profiles...")

    inserted = 0
    for listing in listings:
        best_score = 0
        for profile in BUYER_PROFILES:
            match = score_listing(listing, profile)
            if not match or match["score"] < min_score:
                continue
            best_score = max(best_score, match["score"])

            # Check for duplicate match
            existing = conn.execute(
                "SELECT id FROM matches WHERE id = ?", (match["id"],)
            ).fetchone()
            if existing:
                continue

            if not dry_run:
                conn.execute("""
                    INSERT INTO matches
                        (id, listing_id, buyer_id, buyer_name, score, price_est,
                         margin_est, profit_est, matched_keywords, matched_at, status, notes)
                    VALUES
                        (:id, :listing_id, :buyer_id, :buyer_name, :score, :price_est,
                         :margin_est, :profit_est, :matched_keywords, :matched_at, :status, :notes)
                """, match)
                inserted += 1
                log.info(
                    f"  MATCH [{match['score']:3d}] {listing['title'][:50]:<50} "
                    f"→ {match['buyer_name'][:30]}"
                )
            else:
                log.info(
                    f"  DRY  [{match['score']:3d}] {listing['title'][:50]:<50} "
                    f"→ {match['buyer_name'][:30]}"
                )
                inserted += 1

        # Mark listing as matched if it hit anything
        if best_score >= min_score and not dry_run:
            conn.execute(
                "UPDATE raw_listings SET status = 'matched' WHERE id = ?",
                (listing["id"],)
            )

    if not dry_run:
        conn.commit()

    return inserted


def print_hot_board(conn, limit: int = 20):
    """Print current hot match leaderboard."""
    rows = conn.execute("""
        SELECT m.score, m.buyer_name, m.price_est, m.profit_est,
               r.title, r.source, r.url
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT ?
    """, (limit,)).fetchall()

    print("\n" + "═" * 90)
    print(f"  🔥  HOT MATCH BOARD  —  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print("═" * 90)
    print(f"  {'SCR':>3}  {'TITLE':<40}  {'SOURCE':<12}  {'BUY':>8}  {'PROFIT EST':>10}")
    print("─" * 90)
    for r in rows:
        profit_str = f"${r['profit_est']:,.0f}" if r["profit_est"] else "  N/A"
        price_str  = f"${r['price_est']:,.0f}"  if r["price_est"]  else "  N/A"
        print(f"  {r['score']:3d}  {(r['title'] or '')[:40]:<40}  {r['source']:<12}  "
              f"{price_str:>8}  {profit_str:>10}")
    print("═" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Green Gregory matcher")
    parser.add_argument("--min-score", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--board", action="store_true", help="Print hot board and exit")
    args = parser.parse_args()

    conn = get_db()

    if args.board:
        print_hot_board(conn)
        conn.close()
        return

    n = run_matching(conn, min_score=args.min_score, dry_run=args.dry_run)
    log.info(f"Matching complete — {n} new matches written")
    print_hot_board(conn)
    conn.close()


if __name__ == "__main__":
    main()
