"""
export_json.py — Green Gregory Group Opportunity OS  Phase 4
Exports all 7 dashboard sections:
  supply_opps    - raw listings (supply side)
  buyer_leads    - demand leads (buyer side)
  matched_deals  - joined: supply item + buyer + spread
  assembly_opps  - business-in-a-box combinations
  pipeline       - summary of hot matches
  pl_summary     - P&L by source
  feed_summary   - feed health

is_seed flag propagates everywhere so dashboard can show DEMO banner.
"""

import sqlite3, json, argparse, logging
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
DEFAULT_OUT = Path("docs/dashboard.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("export_json")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT, state TEXT,
            zip TEXT, phone TEXT, website TEXT, rating REAL, review_count INTEGER,
            role TEXT, category TEXT, source_zip TEXT, notes TEXT,
            status TEXT DEFAULT 'new', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY, listing_id TEXT, buyer_id TEXT, buyer_name TEXT,
            score INTEGER, price_est REAL, sell_est REAL, margin_est REAL, profit_est REAL,
            matched_keywords TEXT, match_reason TEXT, next_action TEXT,
            is_seed INTEGER DEFAULT 0, matched_at TEXT, status TEXT DEFAULT 'hot', notes TEXT
        );
        CREATE TABLE IF NOT EXISTS assembly_opps (
            id TEXT PRIMARY KEY, title TEXT, description TEXT, listing_ids TEXT,
            categories TEXT, total_buy REAL, revenue_est REAL, profit_est REAL,
            confidence INTEGER, next_action TEXT, is_seed INTEGER DEFAULT 0, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS raw_listings (
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
            price_raw TEXT, price_est REAL, location TEXT, category TEXT,
            description TEXT, end_date TEXT, scraped_at TEXT,
            keywords_hit TEXT, status TEXT DEFAULT 'new'
        );
    """)
    conn.commit()
    return conn


def _r(v, decimals=2):
    """Round or None."""
    return round(v, decimals) if v is not None else None


def _is_live_url(url: str) -> bool:
    """True if the URL looks real (not a seed placeholder)."""
    if not url:
        return False
    if "/seed/" in url.lower():
        return False
    # Must start with http and have a real domain-looking path
    return url.startswith("http") and len(url) > 20


def build(conn) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Supply opportunities (raw listings, all) ───────────────────────────
    supply_rows = conn.execute("""
        SELECT id, source, title, url, price_raw, price_est, location,
               category, end_date, scraped_at, keywords_hit, status
        FROM raw_listings
        ORDER BY scraped_at DESC LIMIT 300
    """).fetchall()

    supply_opps = []
    for r in supply_rows:
        is_seed = "/seed/" in (r["url"] or "").lower()
        url     = r["url"] if _is_live_url(r["url"]) else ""
        supply_opps.append({
            "id":           r["id"],
            "title":        r["title"] or "",
            "source":       r["source"] or "",
            "url":          url,
            "url_live":     bool(url),
            "price_raw":    r["price_raw"] or "",
            "price_est":    _r(r["price_est"]),
            "location":     r["location"] or "",
            "category":     r["category"] or "",
            "end_date":     (r["end_date"] or "")[:10],
            "scraped_at":   (r["scraped_at"] or "")[:10],
            "keywords":     json.loads(r["keywords_hit"] or "[]"),
            "status":       r["status"] or "new",
            "is_seed":      is_seed,
        })

    any_seed = any(s["is_seed"] for s in supply_opps)

    # ── 2. Buyer/demand leads ─────────────────────────────────────────────────
    lead_rows = conn.execute("""
        SELECT id, name, role, category, city, state, zip,
               phone, website, rating, review_count, source_zip, status, created_at
        FROM leads
        ORDER BY created_at DESC LIMIT 300
    """).fetchall()

    buyer_leads = []
    for r in lead_rows:
        website = r["website"] if _is_live_url(r["website"]) else ""
        buyer_leads.append({
            "id":           r["id"],
            "name":         r["name"] or "",
            "role":         r["role"] or "",
            "category":     r["category"] or "",
            "location":     ", ".join(filter(None, [r["city"], r["state"]])),
            "zip":          r["zip"] or "",
            "phone":        r["phone"] or "",
            "website":      website,
            "website_live": bool(website),
            "rating":       r["rating"],
            "confidence":   min(100, int((r["rating"] or 3.0) / 5.0 * 100)) if r["rating"] else 50,
            "source_zip":   r["source_zip"] or "",
            "status":       r["status"] or "new",
            "is_seed":      False,
        })

    # ── 3. Matched deals (joined: supply + buyer + spread) ────────────────────
    match_rows = conn.execute("""
        SELECT m.id, m.score, m.buyer_id, m.buyer_name, m.price_est, m.sell_est,
               m.margin_est, m.profit_est, m.matched_keywords, m.match_reason,
               m.next_action, m.is_seed, m.matched_at, m.status, m.notes,
               r.id AS listing_id, r.title, r.source, r.url, r.location,
               r.price_raw, r.category, r.end_date
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 200
    """).fetchall()

    matched_deals = []
    for r in match_rows:
        url = r["url"] if _is_live_url(r["url"]) else ""
        spread = None
        if r["sell_est"] and r["price_est"]:
            spread = _r(r["sell_est"] - r["price_est"])
        matched_deals.append({
            "match_id":       r["id"],
            "score":          r["score"],
            "is_seed":        bool(r["is_seed"]),
            # Supply side
            "listing_id":     r["listing_id"],
            "supply_title":   r["title"] or "",
            "supply_source":  r["source"] or "",
            "supply_url":     url,
            "supply_url_live": bool(url),
            "supply_location": r["location"] or "",
            "supply_category": r["category"] or "",
            "supply_end_date": (r["end_date"] or "")[:10],
            # Pricing
            "buy_price":      _r(r["price_est"]),
            "buy_price_raw":  r["price_raw"] or "",
            "sell_est":       _r(r["sell_est"]),
            "spread":         spread,
            "margin_pct":     _r(r["margin_est"] * 100, 1) if r["margin_est"] else None,
            "profit_est":     _r(r["profit_est"]),
            # Demand side
            "buyer_id":       r["buyer_id"] or "",
            "buyer_name":     r["buyer_name"] or "",
            # Intelligence
            "matched_keywords": json.loads(r["matched_keywords"] or "[]"),
            "match_reason":   r["match_reason"] or "",
            "next_action":    r["next_action"] or "",
            "notes":          r["notes"] or "",
            "matched_at":     (r["matched_at"] or "")[:10],
        })

    # ── 4. Assembly opportunities ─────────────────────────────────────────────
    asm_rows = conn.execute("""
        SELECT id, title, description, listing_ids, categories, total_buy,
               revenue_est, profit_est, confidence, next_action, is_seed, created_at
        FROM assembly_opps
        ORDER BY confidence DESC, profit_est DESC
    """).fetchall()

    assembly_opps = []
    for r in asm_rows:
        assembly_opps.append({
            "id":           r["id"],
            "title":        r["title"] or "",
            "description":  r["description"] or "",
            "listing_ids":  json.loads(r["listing_ids"] or "[]"),
            "categories":   json.loads(r["categories"] or "[]"),
            "total_buy":    _r(r["total_buy"]),
            "revenue_est":  _r(r["revenue_est"]),
            "profit_est":   _r(r["profit_est"]),
            "confidence":   r["confidence"] or 0,
            "next_action":  r["next_action"] or "",
            "is_seed":      bool(r["is_seed"]),
        })

    # ── 5. Pipeline summary (matched deals grouped by source) ─────────────────
    pipeline = []
    seen = {}
    for d in matched_deals:
        k = d["supply_source"]
        if k not in seen:
            seen[k] = {"source": k, "count": 0, "total_buy": 0, "total_profit": 0, "avg_score": 0, "scores": []}
        seen[k]["count"]        += 1
        seen[k]["total_buy"]    += d["buy_price"] or 0
        seen[k]["total_profit"] += d["profit_est"] or 0
        seen[k]["scores"].append(d["score"])
    for k, v in seen.items():
        v["avg_score"] = round(sum(v["scores"]) / len(v["scores"]), 1) if v["scores"] else 0
        del v["scores"]
        v["total_buy"]    = _r(v["total_buy"])
        v["total_profit"] = _r(v["total_profit"])
        pipeline.append(v)
    pipeline.sort(key=lambda x: x["total_profit"], reverse=True)

    # ── 6. P&L summary ────────────────────────────────────────────────────────
    pl_rows = conn.execute("""
        SELECT r.source, m.status,
               COUNT(*) AS deal_count,
               ROUND(AVG(m.score),1) AS avg_score,
               ROUND(SUM(r.price_est),2) AS total_buy,
               ROUND(SUM(m.sell_est),2) AS total_sell_est,
               ROUND(SUM(m.profit_est),2) AS total_profit_est,
               ROUND(AVG(m.margin_est)*100,1) AS avg_margin_pct,
               MAX(m.matched_at) AS last_match
        FROM matches m JOIN raw_listings r ON r.id=m.listing_id
        GROUP BY r.source, m.status ORDER BY total_profit_est DESC
    """).fetchall()
    pl_summary = [dict(r) for r in pl_rows]

    # ── 7. Feed health ────────────────────────────────────────────────────────
    feed_rows = conn.execute("""
        SELECT source, status, COUNT(*) AS count, MAX(scraped_at) AS last_scraped
        FROM raw_listings GROUP BY source, status ORDER BY last_scraped DESC
    """).fetchall()
    feed_summary = [dict(r) for r in feed_rows]

    # ── KPIs ──────────────────────────────────────────────────────────────────
    kpi_row = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM raw_listings)                         AS total_supply,
            (SELECT COUNT(*) FROM leads)                                AS total_buyers,
            (SELECT COUNT(*) FROM matches WHERE status='hot')           AS hot_matches,
            (SELECT COUNT(*) FROM assembly_opps)                        AS assembly_count,
            (SELECT ROUND(SUM(profit_est),2) FROM matches
             WHERE status='hot' AND profit_est IS NOT NULL)             AS total_profit_est,
            (SELECT ROUND(SUM(sell_est),2) FROM matches
             WHERE status='hot' AND sell_est IS NOT NULL)               AS total_sell_est,
            (SELECT ROUND(AVG(score),1) FROM matches WHERE status='hot') AS avg_score,
            (SELECT COUNT(*) FROM matches WHERE is_seed=1)              AS seed_match_count
    """).fetchone()

    has_seed = bool(kpi_row["seed_match_count"]) or any_seed

    return {
        "generated_at":   now,
        "has_seed_data":  has_seed,
        "kpi": {
            "total_supply":    kpi_row["total_supply"]    or 0,
            "total_buyers":    kpi_row["total_buyers"]    or 0,
            "hot_matches":     kpi_row["hot_matches"]     or 0,
            "assembly_count":  kpi_row["assembly_count"]  or 0,
            "total_profit_est": _r(kpi_row["total_profit_est"]) or 0,
            "total_sell_est":   _r(kpi_row["total_sell_est"])   or 0,
            "avg_score":       kpi_row["avg_score"]       or 0,
        },
        "supply_opps":    supply_opps,
        "buyer_leads":    buyer_leads,
        "matched_deals":  matched_deals,
        "assembly_opps":  assembly_opps,
        "pipeline":       pipeline,
        "pl_summary":     pl_summary,
        "feed_summary":   feed_summary,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",     default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error(f"DB not found. Run scanner.py --seed-test-data first.")
        return

    conn = get_db()
    data = build(conn)
    conn.close()

    if args.dry_run:
        print(f"generated_at   : {data['generated_at']}")
        print(f"has_seed_data  : {data['has_seed_data']}")
        print(f"KPIs           : {data['kpi']}")
        print(f"supply_opps    : {len(data['supply_opps'])}")
        print(f"buyer_leads    : {len(data['buyer_leads'])}")
        print(f"matched_deals  : {len(data['matched_deals'])}")
        print(f"assembly_opps  : {len(data['assembly_opps'])}")
        print(f"pipeline rows  : {len(data['pipeline'])}")
        print(f"pl_summary rows: {len(data['pl_summary'])}")
        print(f"feed_summary   : {len(data['feed_summary'])}")
        log.info("Dry-run — nothing written.")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    sz = out_path.stat().st_size
    log.info(f"Written: {out_path}  ({sz:,} bytes)")
    log.info(f"  supply={len(data['supply_opps'])}  buyers={len(data['buyer_leads'])}  "
             f"matches={len(data['matched_deals'])}  assembly={len(data['assembly_opps'])}")

if __name__ == "__main__":
    main()
