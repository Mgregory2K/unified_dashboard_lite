"""
matcher.py — Green Gregory Group Opportunity OS  Phase 4
New: sell_est, match_reason, next_action, is_seed flag, assembly_opps table
"""

import sqlite3, json, argparse, logging, hashlib
from datetime import datetime, timezone
from pathlib import Path
from config_reader import load_config

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("matcher")

MATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY, listing_id TEXT, buyer_id TEXT, buyer_name TEXT,
    score INTEGER, price_est REAL, sell_est REAL, margin_est REAL, profit_est REAL,
    matched_keywords TEXT, match_reason TEXT, next_action TEXT,
    is_seed INTEGER DEFAULT 0, matched_at TEXT, status TEXT DEFAULT 'hot', notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_match_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_match_score  ON matches(score DESC);

CREATE TABLE IF NOT EXISTS assembly_opps (
    id TEXT PRIMARY KEY, title TEXT, description TEXT, listing_ids TEXT,
    categories TEXT, total_buy REAL, revenue_est REAL, profit_est REAL,
    confidence INTEGER, next_action TEXT, is_seed INTEGER DEFAULT 0, created_at TEXT
);
"""

# ── Assembly templates ────────────────────────────────────────────────────────
ASSEMBLY_TEMPLATES = [
    {
        "id": "mobile_cleaning",
        "title": "Mobile Pressure Washing Business",
        "description": (
            "Pressure washer + trailer = turnkey mobile cleaning business. "
            "High demand from property managers, fleet operators, and municipalities. "
            "Low overhead, recurring commercial contracts possible. "
            "Ghost model: source both items, sell the combo as a running business."
        ),
        "required_signals": [["pressure washer", "psi", "washer"]],
        "bonus_signals":    [["trailer", "vehicle", "truck", "van"]],
        "revenue_multiple": 3.0, "confidence": 75,
        "next_action": "Price the combo. Post on Facebook Marketplace as a turnkey business package.",
    },
    {
        "id": "warehouse_resale",
        "title": "Warehouse Resale / Storage Flip",
        "description": (
            "Pallet racking system = resaleable warehouse infrastructure. "
            "Sell to logistics companies, 3PLs, or growing e-commerce businesses. "
            "Can also rent racked storage space to local small businesses at monthly rates."
        ),
        "required_signals": [["pallet", "rack", "racking", "shelving"]],
        "bonus_signals":    [["forklift", "warehouse", "logistics", "storage", "industrial"]],
        "revenue_multiple": 2.5, "confidence": 70,
        "next_action": "Photo and list on EquipmentTrader + local Facebook B2B groups. Tag logistics contacts.",
    },
    {
        "id": "it_resale_lot",
        "title": "IT Equipment Resale Lot",
        "description": (
            "Network/server items = bulk eBay or direct resale. "
            "Cisco switches and rack hardware command 2-4x resale on eBay. "
            "Ghost model: acquire the lot, split into individual SKUs, sell each unit separately."
        ),
        "required_signals": [["cisco", "juniper", "server", "switch", "router", "network", "rack"]],
        "bonus_signals":    [["lot", "bulk", "surplus", "refresh", "6 units", "quantity"]],
        "revenue_multiple": 2.5, "confidence": 80,
        "next_action": "Check eBay completed listings for per-unit prices. Split lot into individual listings.",
    },
    {
        "id": "food_service_package",
        "title": "Food Service Equipment Package",
        "description": (
            "Commercial ice machine + kitchen equipment = food truck, ghost kitchen, or catering package. "
            "Sell as a bundle to restaurant entrepreneurs or lease to pop-up operators. "
            "Manitowoc/Hoshizaki ice machines alone resell at 2-3x auction prices."
        ),
        "required_signals": [["ice machine", "refrigerator", "freezer", "fryer", "cafeteria", "commercial kitchen"]],
        "bonus_signals":    [["restaurant", "food service", "catering", "commercial"]],
        "revenue_multiple": 2.2, "confidence": 70,
        "next_action": "List on RestaurantEquipment.com and local Facebook restaurant groups.",
    },
    {
        "id": "floor_care_biz",
        "title": "Commercial Floor Care Business",
        "description": (
            "Floor scrubber = commercial floor care service or resale. "
            "Facilities managers, schools, and hospitals need regular floor maintenance. "
            "Tennant/Nilfisk machines hold value and sell well on eBay and MachineryTrader."
        ),
        "required_signals": [["scrubber", "floor scrubber", "buffer", "burnisher", "sweeper"]],
        "bonus_signals":    [["commercial", "industrial", "facility", "tennant", "nilfisk", "ride-on"]],
        "revenue_multiple": 2.2, "confidence": 70,
        "next_action": "Check Tennant/Nilfisk resale prices on eBay. List on MachineryTrader.",
    },
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(MATCH_SCHEMA)
    for col, typedef in [("sell_est","REAL"),("match_reason","TEXT"),
                          ("next_action","TEXT"),("is_seed","INTEGER DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def is_seed_url(url: str) -> bool:
    return bool(url and "/seed/" in url.lower())


def derive_match_reason(hits, profile, score, price_est) -> str:
    kw_str = ", ".join(hits[:5])
    parts  = [f"Keywords matched: {kw_str}."]
    if price_est and price_est <= profile["max_buy_price"]:
        pct = price_est / profile["max_buy_price"] * 100
        parts.append(f"Price ${price_est:,.0f} is {pct:.0f}% of your ${profile['max_buy_price']:,.0f} ceiling — leaves room for margin.")
    if score >= 80:
        parts.append("Strong confidence — multiple distinct signals hit.")
    elif score >= 60:
        parts.append("Moderate confidence — worth pursuing.")
    else:
        parts.append("Speculative — watch for price drop or more info.")
    return " ".join(parts)


def derive_next_action(profile, score, price_est) -> str:
    buyer = profile["name"]
    if score >= 80:
        return f"HIGH PRIORITY — Contact {buyer} today. Confirm condition and ask for photos."
    if score >= 60:
        return f"Verify item condition, then pitch to {buyer}."
    if price_est and price_est < 500:
        return f"Low-cost entry. Quick flip via eBay or direct to {buyer}."
    return f"Monitor — revisit if price drops. Target buyer: {buyer}."


def score_listing(listing, profile, scoring):
    combined = ((listing["title"] or "") + " " + (listing["description"] or "")).lower()
    hits     = [kw for kw in profile["keywords"] if kw.lower() in combined]
    if not hits:
        return None

    score = min(len(hits) * profile["score_weight"], scoring["score_cap"])
    price = listing["price_est"]
    sell_est = margin_est = profit_est = None

    if price and price > 0:
        max_buy = profile["max_buy_price"]
        if price <= max_buy:
            sell_est   = price / (1 - profile["target_margin"])
            profit_est = sell_est - price
            margin_est = profile["target_margin"]
            ratio = price / max_buy
            if ratio < 0.25:
                score = min(score + scoring["price_boost_low"],  scoring["score_cap"])
            elif ratio < 0.50:
                score = min(score + scoring["price_boost_mid"],  scoring["score_cap"])
        else:
            score = max(score - scoring["over_budget_penalty"], 0)

    return {
        "id":               f"{listing['id']}_{profile['id']}",
        "listing_id":       listing["id"],
        "buyer_id":         profile["id"],
        "buyer_name":       profile["name"],
        "score":            score,
        "price_est":        price,
        "sell_est":         round(sell_est, 2)   if sell_est   else None,
        "margin_est":       margin_est,
        "profit_est":       round(profit_est, 2) if profit_est else None,
        "matched_keywords": json.dumps(hits),
        "match_reason":     derive_match_reason(hits, profile, score, price),
        "next_action":      derive_next_action(profile, score, price),
        "is_seed":          1 if is_seed_url(listing["url"] or "") else 0,
        "matched_at":       datetime.now(timezone.utc).isoformat(),
        "status":           "hot",
        "notes":            profile.get("notes", ""),
    }


def detect_assembly(conn, listings, dry_run=False) -> int:
    inserted = 0
    for tmpl in ASSEMBLY_TEMPLATES:
        hit_ids = []; hit_cats = []; total_buy = 0.0; any_seed = False
        for r in listings:
            text = ((r["title"] or "") + " " + (r["description"] or "")).lower()
            req  = sum(1 for sig in tmpl["required_signals"] if any(s in text for s in sig))
            if req < len(tmpl["required_signals"]):
                continue
            hit_ids.append(r["id"]); hit_cats.append((r["title"] or "")[:40])
            total_buy += r["price_est"] or 0
            if is_seed_url(r["url"] or ""):
                any_seed = True
        if not hit_ids:
            continue
        opp_id = hashlib.sha256((tmpl["id"] + "|".join(sorted(hit_ids))).encode()).hexdigest()[:16]
        if conn.execute("SELECT id FROM assembly_opps WHERE id=?", (opp_id,)).fetchone():
            continue
        rev = total_buy * tmpl["revenue_multiple"]
        row = {
            "id": opp_id, "title": tmpl["title"], "description": tmpl["description"],
            "listing_ids": json.dumps(hit_ids), "categories": json.dumps(hit_cats),
            "total_buy": round(total_buy, 2), "revenue_est": round(rev, 2),
            "profit_est": round(rev - total_buy, 2),
            "confidence": tmpl["confidence"], "next_action": tmpl["next_action"],
            "is_seed": 1 if any_seed else 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if not dry_run:
            conn.execute("""INSERT INTO assembly_opps
                (id,title,description,listing_ids,categories,total_buy,revenue_est,
                 profit_est,confidence,next_action,is_seed,created_at)
                VALUES (:id,:title,:description,:listing_ids,:categories,:total_buy,
                        :revenue_est,:profit_est,:confidence,:next_action,:is_seed,:created_at)
            """, row)
        inserted += 1
        log.info(f"  ASSEMBLY {'DRY ' if dry_run else ''}[{tmpl['confidence']}%]: {tmpl['title']}")
    if not dry_run:
        conn.commit()
    return inserted


def run_matching(conn, profiles, scoring, dry_run=False) -> dict:
    min_score = scoring["min_score_match"]
    listings  = conn.execute("SELECT * FROM raw_listings WHERE status='new' ORDER BY scraped_at DESC").fetchall()
    active    = [p for p in profiles if p.get("active", True)]
    log.info(f"  New listings: {len(listings)}  Buyer profiles: {len(active)}  Min score: {min_score}")

    n_new = 0
    for listing in listings:
        best = 0
        for profile in active:
            m = score_listing(listing, profile, scoring)
            if not m or m["score"] < min_score:
                continue
            best = max(best, m["score"])
            if conn.execute("SELECT id FROM matches WHERE id=?", (m["id"],)).fetchone():
                continue
            n_new += 1
            if not dry_run:
                conn.execute("""INSERT INTO matches
                    (id,listing_id,buyer_id,buyer_name,score,price_est,sell_est,margin_est,
                     profit_est,matched_keywords,match_reason,next_action,is_seed,matched_at,status,notes)
                    VALUES (:id,:listing_id,:buyer_id,:buyer_name,:score,:price_est,:sell_est,:margin_est,
                            :profit_est,:matched_keywords,:match_reason,:next_action,:is_seed,:matched_at,:status,:notes)
                """, m)
                log.info(f"  MATCH [{m['score']:3d}] {(listing['title'] or '')[:45]:<45} → {m['buyer_name'][:25]}")
            else:
                log.info(f"  DRY  [{m['score']:3d}] {(listing['title'] or '')[:45]:<45} → {m['buyer_name'][:25]}")
        if best >= min_score and not dry_run:
            conn.execute("UPDATE raw_listings SET status='matched' WHERE id=?", (listing["id"],))
    if not dry_run:
        conn.commit()

    all_listings = conn.execute("SELECT * FROM raw_listings").fetchall()
    n_asm = detect_assembly(conn, all_listings, dry_run)
    n_hot = conn.execute("SELECT COUNT(*) FROM matches WHERE status='hot'").fetchone()[0]
    return {"listings_read": len(listings), "matches_created": n_new, "hot_count": n_hot, "assembly_opps": n_asm}


def print_hot_board(conn, limit=25):
    rows = conn.execute("""
        SELECT m.score,m.buyer_name,m.price_est,m.sell_est,m.profit_est,m.is_seed,
               r.title,r.source,r.location
        FROM matches m JOIN raw_listings r ON r.id=m.listing_id
        WHERE m.status='hot' ORDER BY m.score DESC,m.profit_est DESC LIMIT ?
    """, (limit,)).fetchall()
    n_hot = conn.execute("SELECT COUNT(*) FROM matches WHERE status='hot'").fetchone()[0]
    n_asm = conn.execute("SELECT COUNT(*) FROM assembly_opps").fetchone()[0]
    print(f"\n{'═'*105}")
    print(f"  🔥  GREEN GREGORY OS  |  Hot: {n_hot}  Assembly Opps: {n_asm}")
    print(f"{'═'*105}")
    if not rows:
        print("  (no matches — run scanner.py --seed-test-data then matcher.py)")
        print(f"{'═'*105}\n"); return
    print(f"  {'SCR':>3}  {'TITLE':<40}  {'SOURCE':<13}  {'BUY':>8}  {'SELL EST':>9}  {'PROFIT':>8}  BUYER")
    print(f"{'─'*105}")
    for r in rows:
        stag = " [SEED]" if r["is_seed"] else ""
        bs = f"${r['price_est']:>6,.0f}"  if r["price_est"]  else "     N/A"
        ss = f"${r['sell_est']:>7,.0f}" if r["sell_est"]   else "      N/A"
        ps = f"${r['profit_est']:>6,.0f}" if r["profit_est"] else "     N/A"
        print(f"  {r['score']:3d}  {(r['title'] or '')[:40]:<40}  {r['source']:<13}  {bs}  {ss}  {ps}  {r['buyer_name'][:22]}{stag}")
    print(f"{'═'*105}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=None)
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--board",     action="store_true")
    args = parser.parse_args()

    cfg = load_config(); profiles = cfg["buyer_profiles"]; scoring = cfg["scoring"]
    if args.min_score: scoring["min_score_match"] = args.min_score

    conn = get_db()
    if args.board:
        print_hot_board(conn); conn.close(); return

    stats = run_matching(conn, profiles, scoring, args.dry_run)
    print(f"\n── Summary ──\n  Listings read:   {stats['listings_read']}\n"
          f"  Matches created: {stats['matches_created']}\n"
          f"  Hot total:       {stats['hot_count']}\n"
          f"  Assembly opps:   {stats['assembly_opps']}\n")
    print_hot_board(conn); conn.close()

if __name__ == "__main__":
    main()
