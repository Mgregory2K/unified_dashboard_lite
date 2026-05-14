"""
export_json.py — Green Gregory Opportunity OS  Phase 7B
Changes vs Phase 7A:
  - all_signals: every included raw_listings row regardless of match status
  - unmatched_signals: rows with no entry in matches table
  - Lane splits: supply_items, demand_items, operator_items, infrastructure_items,
                 business_for_sale_items, event_items, lead_items
  - match_status per signal: unmatched / candidate / matched / manual_match
  - normalized_signal_type: canonical string for lane routing
  - data_origin per signal: live / manual / seed
  - Updated data_mode: EMPTY / SIGNALS_ONLY / MATCHED / DEMO / STALE / MIXED
  - SIGNALS_ONLY: records exist but no matches yet — never shows as EMPTY
  - sources_config.json only — never writes config.json
"""

import sqlite3, json, argparse, logging
from datetime import datetime, timezone
from pathlib import Path

DB_PATH     = Path("opportunity_os.db")
DEFAULT_OUT = Path("docs/dashboard.json")
SOURCES_CFG = Path("sources_config.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("export_json")


# ── Signal type normalization ──────────────────────────────────────────────────
# Maps raw signal_type values → canonical lane name

SIGNAL_TYPE_NORM = {
    "supply":             "supply",
    "demand":             "demand",
    "operator":           "operator",
    "infrastructure":     "infrastructure",
    "business_for_sale":  "business_for_sale",
    "garage_sale":        "event",
    "estate_sale":        "event",
    "event":              "event",
    "lead":               "lead",
    "garage_sale_manual": "event",
    "local_business":     "lead",
    # fallback
    "":                   "supply",
}

LANE_KEYS = [
    "supply", "demand", "operator", "infrastructure",
    "business_for_sale", "event", "lead",
]


# ── Default sources config ─────────────────────────────────────────────────────
DEFAULT_SOURCES = [
    {
        "enabled": True,
        "source_name": "Craigslist For Sale — Equipment",
        "source_type": "craigslist_for_sale",
        "signal_type": "supply",
        "query": "pressure washer OR pallet racking OR restaurant equipment",
        "category": "equipment",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "commercial,equipment,tools,restaurant,pallet,rack,pressure washer",
        "exclude_keywords": "toy,junk,broken,parts only",
        "notes": "General supply feed",
        "scraper_status": "live",
    },
    {
        "enabled": True,
        "source_name": "Craigslist Wanted — ISO / Services",
        "source_type": "craigslist_wanted",
        "signal_type": "demand",
        "query": "ISO pressure washing OR wanted driveway cleaning OR need junk removal",
        "category": "service_demand",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "ISO,wanted,need,looking for,driveway,cleaning,junk removal",
        "exclude_keywords": "spam",
        "notes": "Demand-side / ISO feed",
        "scraper_status": "live",
    },
    {
        "enabled": True,
        "source_name": "Craigslist Services — Operators",
        "source_type": "craigslist_services",
        "signal_type": "operator",
        "query": "pressure washing OR junk removal OR hauling OR handyman",
        "category": "service_provider",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "pressure washing,junk removal,hauling,handyman,cheap,local",
        "exclude_keywords": "franchise,national",
        "notes": "Operator / capability feed",
        "scraper_status": "live",
    },
    {
        "enabled": True,
        "source_name": "GovDeals — Surplus Equipment",
        "source_type": "govdeals",
        "signal_type": "supply",
        "query": "equipment tools vehicles",
        "category": "government_surplus",
        "market": "Regional",
        "zip": "45219",
        "radius_miles": 200,
        "include_keywords": "equipment,vehicle,fleet,tools,industrial",
        "exclude_keywords": "parts only",
        "notes": "Government surplus auctions",
        "scraper_status": "live",
    },
    {
        "enabled": True,
        "source_name": "PublicSurplus — Auctions",
        "source_type": "publicsurplus",
        "signal_type": "supply",
        "query": "equipment fleet tools",
        "category": "government_surplus",
        "market": "Regional",
        "zip": "45219",
        "radius_miles": 200,
        "include_keywords": "equipment,vehicle,fleet,tools",
        "exclude_keywords": "parts only",
        "notes": "Public surplus auctions",
        "scraper_status": "live",
    },
    {
        "enabled": True,
        "source_name": "Facebook Marketplace — Manual Import",
        "source_type": "facebook_manual",
        "signal_type": "supply",
        "query": "manual paste",
        "category": "manual_supply",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "equipment,tools,business,garage sale,moving sale",
        "exclude_keywords": "",
        "notes": "Manual source for pasted Facebook posts",
        "scraper_status": "manual",
    },
    {
        "enabled": True,
        "source_name": "Garage / Estate Sale — Manual",
        "source_type": "garage_sale_manual",
        "signal_type": "garage_sale",
        "query": "manual paste",
        "category": "local_event",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "garage sale,moving sale,estate sale,tools,equipment",
        "exclude_keywords": "baby clothes",
        "notes": "Manual / import source for local sales",
        "scraper_status": "manual",
    },
    {
        "enabled": True,
        "source_name": "Business For Sale — Manual",
        "source_type": "business_for_sale_manual",
        "signal_type": "business_for_sale",
        "query": "manual paste",
        "category": "business_acquisition",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 100,
        "include_keywords": "business for sale,established,route,equipment included,cash flow",
        "exclude_keywords": "",
        "notes": "Manual import of business-for-sale listings",
        "scraper_status": "manual",
    },
    {
        "enabled": False,
        "source_name": "OSM Local Business Leads",
        "source_type": "osm_leadgen",
        "signal_type": "lead",
        "query": "used equipment reseller OR electronics recycler OR restaurant equipment dealer",
        "category": "buyer_lead",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "reseller,recycler,equipment dealer,repair shop",
        "exclude_keywords": "",
        "notes": "Buyer/demand leadgen from OpenStreetMap",
        "scraper_status": "not_implemented",
    },
    {
        "enabled": False,
        "source_name": "Nextdoor — Manual",
        "source_type": "nextdoor_manual",
        "signal_type": "demand",
        "query": "manual paste",
        "category": "community_demand",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 25,
        "include_keywords": "looking for,need,ISO,anyone know,recommend",
        "exclude_keywords": "",
        "notes": "Paste Nextdoor posts manually",
        "scraper_status": "manual",
    },
    {
        "enabled": False,
        "source_name": "BizBuySell — Scrape",
        "source_type": "bizbuysell",
        "signal_type": "business_for_sale",
        "query": "Cincinnati OR Ohio",
        "category": "business_acquisition",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 100,
        "include_keywords": "equipment,route,cash flow,established",
        "exclude_keywords": "",
        "notes": "Future scraper target",
        "scraper_status": "not_implemented",
    },
    {
        "enabled": False,
        "source_name": "RSS Feed — Generic",
        "source_type": "rss",
        "signal_type": "supply",
        "query": "",
        "category": "rss_feed",
        "market": "Cincinnati",
        "zip": "45219",
        "radius_miles": 50,
        "include_keywords": "",
        "exclude_keywords": "",
        "notes": "Generic RSS feed import — configure URL separately",
        "scraper_status": "not_implemented",
    },
]


# ── Source config helpers ──────────────────────────────────────────────────────

def load_sources_config() -> list:
    if SOURCES_CFG.exists():
        try:
            data = json.loads(SOURCES_CFG.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                log.info(f"Loaded {len(data)} sources from {SOURCES_CFG}")
                return data
        except Exception as e:
            log.warning(f"Could not parse {SOURCES_CFG}: {e} — using defaults")
    log.info("Using default sources config")
    return DEFAULT_SOURCES


def save_sources_config(sources: list):
    """Write sources_config.json only. Never touches config.json."""
    SOURCES_CFG.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    log.info(f"Saved {len(sources)} sources to {SOURCES_CFG}")


# ── DB ─────────────────────────────────────────────────────────────────────────

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
            keywords_hit TEXT, status TEXT DEFAULT 'new',
            signal_type TEXT DEFAULT 'supply',
            is_manual INTEGER DEFAULT 0
        );
    """)
    for col, typedef in [
        ("signal_type", "TEXT DEFAULT 'supply'"),
        ("is_manual",   "INTEGER DEFAULT 0"),
        ("description", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE raw_listings ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


# ── URL / field helpers ────────────────────────────────────────────────────────

def _r(v, decimals=2):
    return round(v, decimals) if v is not None else None


def _is_live_url(url: str) -> bool:
    if not url:
        return False
    if "/seed/" in url.lower():
        return False
    return url.startswith("http") and len(url) > 20


def _is_seed_url(url: str) -> bool:
    return bool(url and "/seed/" in url.lower())


def _source_site(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _norm_signal_type(raw: str) -> str:
    """Map raw signal_type to canonical lane key."""
    return SIGNAL_TYPE_NORM.get((raw or "").strip().lower(), "supply")


def _data_origin(is_seed: bool, is_manual: bool) -> str:
    if is_seed:   return "seed"
    if is_manual: return "manual"
    return "live"


# ── Data mode ──────────────────────────────────────────────────────────────────

def compute_data_mode(
    total_signals: int,
    seed_signals: int,
    live_signals: int,
    manual_signals: int,
    total_matches: int,
    stale_count: int,
) -> str:
    """
    EMPTY        — no records at all
    SIGNALS_ONLY — records exist but zero matches (never shows as empty)
    DEMO         — seed records only, no matches or seed-only matches
    STALE        — matches exist but source rows are excluded/absent
    MATCHED      — at least one confirmed match exists
    MANUAL       — manual records only, possibly with matches
    LIVE         — live records only
    MIXED        — two or more origin types present
    """
    has_any     = total_signals > 0
    has_live    = live_signals > 0
    has_manual  = manual_signals > 0
    has_seed    = seed_signals > 0
    has_matches = total_matches > 0

    if not has_any:
        return "STALE" if stale_count > 0 else "EMPTY"

    if has_seed and not has_live and not has_manual:
        if not has_matches:
            return "SIGNALS_ONLY"
        return "DEMO"

    if not has_matches:
        return "SIGNALS_ONLY"

    # has records AND matches
    origins = sum([has_live, has_manual, has_seed])
    if origins >= 2:
        return "MIXED"
    if has_manual and not has_live and not has_seed:
        return "MANUAL"
    if has_live and not has_manual and not has_seed:
        return "MATCHED"
    return "MIXED"


# ── Clear stale matches ────────────────────────────────────────────────────────

def clear_stale_matches(conn, included_ids: set) -> int:
    if not included_ids:
        cur = conn.execute("DELETE FROM matches")
        conn.commit()
        log.warning(f"clear_stale: no included supply — deleted all {cur.rowcount} matches")
        return cur.rowcount
    placeholders = ",".join("?" * len(included_ids))
    cur = conn.execute(
        f"DELETE FROM matches WHERE listing_id NOT IN ({placeholders})",
        list(included_ids),
    )
    conn.commit()
    log.info(f"clear_stale: deleted {cur.rowcount} stale matches")
    return cur.rowcount


# ── Build signal record ────────────────────────────────────────────────────────

def _build_signal(r, match_status: str) -> dict:
    """Build a normalized signal dict from a raw_listings row."""
    raw_url   = r["url"] or ""
    is_seed   = _is_seed_url(raw_url)
    is_manual = bool(r["is_manual"])
    url       = raw_url if _is_live_url(raw_url) else ""
    raw_st    = r["signal_type"] or "supply"

    return {
        "id":                   r["id"],
        "title":                r["title"] or "",
        "description":          r["description"] or "",
        "signal_type":          raw_st,
        "normalized_signal_type": _norm_signal_type(raw_st),
        "source":               r["source"] or "",
        "source_site":          _source_site(raw_url),
        "url":                  url,
        "url_live":             bool(url),
        "price_raw":            r["price_raw"] or "",
        "price_est":            _r(r["price_est"]),
        "location":             r["location"] or "",
        "category":             r["category"] or "",
        "keywords":             json.loads(r["keywords_hit"] or "[]"),
        "status":               r["status"] or "new",
        "scraped_at":           (r["scraped_at"] or "")[:10],
        "end_date":             (r["end_date"] or "")[:10],
        "match_status":         match_status,
        "data_origin":          _data_origin(is_seed, is_manual),
        "is_seed":              is_seed,
        "is_manual":            is_manual,
        # These fields may be empty for most scraped records — populated by manual entry or leadgen
        "phone":                "",
        "website":              "",
        "email":                "",
        "notes":                "",
        "score":                None,   # filled in by matcher if present
    }


# ── Build ──────────────────────────────────────────────────────────────────────

def build(conn, include_seed: bool = True, sources_config: list = None,
          do_clear_stale: bool = False) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    if sources_config is None:
        sources_config = load_sources_config()

    # ── Step 1: Load all raw_listings (included set) ──────────────────────────
    seed_clause = "" if include_seed else "AND (url NOT LIKE '%/seed/%' OR url IS NULL)"
    all_rows = conn.execute(f"""
        SELECT id, source, title, url, price_raw, price_est, location,
               category, description, end_date, scraped_at, keywords_hit, status,
               signal_type, is_manual
        FROM raw_listings
        WHERE 1=1 {seed_clause}
        ORDER BY scraped_at DESC LIMIT 500
    """).fetchall()

    # ── Step 2: Build set of listing IDs that have a match ────────────────────
    seed_match_clause = "" if include_seed else "AND is_seed = 0"
    matched_ids_rows = conn.execute(
        f"SELECT DISTINCT listing_id FROM matches WHERE status='hot' {seed_match_clause}"
    ).fetchall()
    matched_listing_ids = {r["listing_id"] for r in matched_ids_rows}

    # ── Step 3: Build all_signals with match_status ───────────────────────────
    all_signals      = []
    unmatched_signals = []
    included_ids      = set()

    seed_signals   = 0
    live_signals   = 0
    manual_signals = 0

    last_live_scraped = None

    # Lane buckets
    lanes = {k: [] for k in LANE_KEYS}

    for r in all_rows:
        raw_url   = r["url"] or ""
        is_seed   = _is_seed_url(raw_url)
        is_manual = bool(r["is_manual"])

        if is_seed:
            seed_signals += 1
        elif is_manual:
            manual_signals += 1
        else:
            live_signals += 1
            ts = r["scraped_at"] or ""
            if ts and (last_live_scraped is None or ts > last_live_scraped):
                last_live_scraped = ts

        included_ids.add(r["id"])

        ms = "matched" if r["id"] in matched_listing_ids else "unmatched"
        sig = _build_signal(r, ms)
        all_signals.append(sig)

        if ms == "unmatched":
            unmatched_signals.append(sig)

        lane = sig["normalized_signal_type"]
        if lane in lanes:
            lanes[lane].append(sig)

    total_signals = len(all_signals)

    # ── Step 4: Optionally purge stale matches ────────────────────────────────
    if do_clear_stale:
        clear_stale_matches(conn, included_ids)

    # ── Step 5: Matched deals (only those whose supply is in included set) ─────
    full_seed_clause = "" if include_seed else "AND m.is_seed = 0"
    match_rows = conn.execute(f"""
        SELECT m.id, m.score, m.buyer_id, m.buyer_name, m.price_est, m.sell_est,
               m.margin_est, m.profit_est, m.matched_keywords, m.match_reason,
               m.next_action, m.is_seed, m.matched_at, m.status, m.notes,
               r.id AS listing_id, r.title, r.source, r.url, r.location,
               r.price_raw, r.category, r.end_date, r.is_manual,
               r.description AS supply_desc, r.keywords_hit AS supply_keywords
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot' {full_seed_clause}
        ORDER BY m.score DESC, m.profit_est DESC
        LIMIT 500
    """).fetchall()

    matched_deals  = []
    seed_matches   = 0
    live_matches   = 0
    manual_matches = 0
    stale_count    = 0

    for r in match_rows:
        if r["listing_id"] not in included_ids:
            stale_count += 1
            continue

        raw_url = r["url"] or ""
        url     = raw_url if _is_live_url(raw_url) else ""

        spread = None
        if r["sell_est"] and r["price_est"]:
            spread = _r(r["sell_est"] - r["price_est"])

        is_seed_supply   = _is_seed_url(raw_url)
        is_manual_supply = bool(r["is_manual"])
        is_seed_match    = bool(r["is_seed"])

        if is_seed_supply or is_seed_match:
            seed_matches += 1
        elif is_manual_supply:
            manual_matches += 1
        else:
            live_matches += 1

        matched_deals.append({
            "match_id":           r["id"],
            "score":              r["score"],
            "is_seed":            is_seed_supply or is_seed_match,
            "is_manual":          is_manual_supply,
            "listing_id":         r["listing_id"],
            "supply_title":       r["title"] or "",
            "supply_source":      r["source"] or "",
            "supply_source_site": _source_site(raw_url),
            "supply_url":         url,
            "supply_url_live":    bool(url),
            "supply_location":    r["location"] or "",
            "supply_category":    r["category"] or "",
            "supply_end_date":    (r["end_date"] or "")[:10],
            "supply_description": r["supply_desc"] or "",
            "supply_keywords":    json.loads(r["supply_keywords"] or "[]"),
            "buy_price":          _r(r["price_est"]),
            "buy_price_raw":      r["price_raw"] or "",
            "sell_est":           _r(r["sell_est"]),
            "spread":             spread,
            "margin_pct":         _r(r["margin_est"] * 100, 1) if r["margin_est"] else None,
            "profit_est":         _r(r["profit_est"]),
            "buyer_id":           r["buyer_id"] or "",
            "buyer_name":         r["buyer_name"] or "",
            "matched_keywords":   json.loads(r["matched_keywords"] or "[]"),
            "match_reason":       r["match_reason"] or "",
            "next_action":        r["next_action"] or "",
            "notes":              r["notes"] or "",
            "matched_at":         (r["matched_at"] or "")[:10],
            "confidence":         min(100, int(r["score"])) if r["score"] else 0,
        })

    # ── Step 6: Buyer/demand leads ────────────────────────────────────────────
    lead_rows = conn.execute("""
        SELECT id, name, role, category, city, state, zip,
               phone, website, rating, review_count, source_zip, notes, status, created_at
        FROM leads ORDER BY created_at DESC LIMIT 300
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
            "notes":        r["notes"] or "",
            "status":       r["status"] or "new",
            "is_seed":      False,
        })

    # ── Step 7: Assembly opps ─────────────────────────────────────────────────
    asm_seed_clause = "" if include_seed else "WHERE is_seed = 0"
    asm_rows = conn.execute(f"""
        SELECT id, title, description, listing_ids, categories, total_buy,
               revenue_est, profit_est, confidence, next_action, is_seed, created_at
        FROM assembly_opps {asm_seed_clause}
        ORDER BY confidence DESC, profit_est DESC
    """).fetchall()

    assembly_opps = []
    for r in asm_rows:
        raw_ids = json.loads(r["listing_ids"] or "[]")
        if raw_ids and not any(lid in included_ids for lid in raw_ids):
            stale_count += 1
            continue
        assembly_opps.append({
            "id":          r["id"],
            "title":       r["title"] or "",
            "description": r["description"] or "",
            "listing_ids": raw_ids,
            "categories":  json.loads(r["categories"] or "[]"),
            "total_buy":   _r(r["total_buy"]),
            "revenue_est": _r(r["revenue_est"]),
            "profit_est":  _r(r["profit_est"]),
            "confidence":  r["confidence"] or 0,
            "next_action": r["next_action"] or "",
            "is_seed":     bool(r["is_seed"]),
        })

    # ── Step 8: Pipeline + P&L + Feed health ──────────────────────────────────
    pipeline = []
    seen = {}
    for d in matched_deals:
        k = d["supply_source"]
        if k not in seen:
            seen[k] = {"source": k, "count": 0, "total_buy": 0, "total_profit": 0, "scores": []}
        seen[k]["count"]        += 1
        seen[k]["total_buy"]    += d["buy_price"] or 0
        seen[k]["total_profit"] += d["profit_est"] or 0
        seen[k]["scores"].append(d["score"])
    for k, v in seen.items():
        v["avg_score"]    = round(sum(v["scores"]) / len(v["scores"]), 1) if v["scores"] else 0
        del v["scores"]
        v["total_buy"]    = _r(v["total_buy"])
        v["total_profit"] = _r(v["total_profit"])
        pipeline.append(v)
    pipeline.sort(key=lambda x: x["total_profit"], reverse=True)

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

    feed_rows = conn.execute("""
        SELECT source, status, COUNT(*) AS count, MAX(scraped_at) AS last_scraped
        FROM raw_listings GROUP BY source, status ORDER BY last_scraped DESC
    """).fetchall()
    feed_summary = [dict(r) for r in feed_rows]

    # ── Step 9: KPIs + data_mode ──────────────────────────────────────────────
    scores    = [d["score"] for d in matched_deals if d["score"]]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    total_matches = len(matched_deals)
    data_mode = compute_data_mode(
        total_signals, seed_signals, live_signals, manual_signals,
        total_matches, stale_count,
    )

    # Lane counts for KPI grid
    lane_counts = {f"{k}_count": len(v) for k, v in lanes.items()}

    return {
        "generated_at":        now,
        "last_live_scan_at":   last_live_scraped,
        "data_mode":           data_mode,
        "include_seed_data":   include_seed,

        # Signal counts
        "total_signal_count":    total_signals,
        "unmatched_signal_count": len(unmatched_signals),
        "seed_signal_count":     seed_signals,
        "live_signal_count":     live_signals,
        "manual_signal_count":   manual_signals,

        # Match counts (included only)
        "seed_match_count":    seed_matches,
        "live_match_count":    live_matches,
        "manual_match_count":  manual_matches,
        "stale_match_count":   stale_count,
        "has_stale_data":      stale_count > 0,

        # Legacy compat
        "seed_supply_count":   seed_signals,
        "live_supply_count":   live_signals,
        "manual_supply_count": manual_signals,
        "has_seed_data":       seed_signals > 0,

        "kpi": {
            # Workbench counts
            "total_signals":      total_signals,
            "unmatched_signals":  len(unmatched_signals),
            "total_matches":      total_matches,
            "assembly_count":     len(assembly_opps),
            "total_buyers":       len(buyer_leads),
            # Financial
            "total_profit_est":   _r(sum(d["profit_est"] or 0 for d in matched_deals)) or 0,
            "total_sell_est":     _r(sum(d["sell_est"] or 0 for d in matched_deals)) or 0,
            "avg_score":          avg_score,
            # Origins
            "live_signals":       live_signals,
            "manual_signals":     manual_signals,
            "seed_signals":       seed_signals,
            "stale_matches":      stale_count,
            # Lane counts
            **lane_counts,
        },

        "sources_config":   sources_config,

        # ── All signal sections ─────────────────────────────────────────────
        "all_signals":            all_signals,
        "unmatched_signals":      unmatched_signals,

        # Lane splits (pre-filtered by normalized_signal_type)
        "supply_items":           lanes["supply"],
        "demand_items":           lanes["demand"],
        "operator_items":         lanes["operator"],
        "infrastructure_items":   lanes["infrastructure"],
        "business_for_sale_items": lanes["business_for_sale"],
        "event_items":            lanes["event"],
        "lead_items":             lanes["lead"],

        # Matches + assembly
        "matched_deals":   matched_deals,
        "assembly_opps":   assembly_opps,
        "buyer_leads":     buyer_leads,

        # Legacy aliases so old dashboard code doesn't break
        "supply_opps":     lanes["supply"],

        # Analytics
        "pipeline":        pipeline,
        "pl_summary":      pl_summary,
        "feed_summary":    feed_summary,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export Opportunity OS dashboard JSON")
    parser.add_argument("--out",               default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run",           action="store_true")
    parser.add_argument("--no-seed",           action="store_true")
    parser.add_argument("--include-seed-data", dest="include_seed", action="store_true", default=True)
    parser.add_argument("--exclude-seed-data", dest="include_seed", action="store_false")
    parser.add_argument("--clear-stale",       action="store_true",
                        help="Delete matches with no included supply record")
    args = parser.parse_args()

    include_seed   = args.include_seed and not args.no_seed
    do_clear_stale = args.clear_stale

    if not DB_PATH.exists():
        log.error("DB not found. Run scanner.py --seed-test-data first.")
        return

    sources_config = load_sources_config()
    save_sources_config(sources_config)

    conn = get_db()
    data = build(conn, include_seed=include_seed, sources_config=sources_config,
                 do_clear_stale=do_clear_stale)
    conn.close()

    log.info(
        f"data_mode={data['data_mode']}  "
        f"total_signals={data['total_signal_count']}  "
        f"unmatched={data['unmatched_signal_count']}  "
        f"matches={data['live_match_count']+data['seed_match_count']+data['manual_match_count']}"
    )
    log.info(
        f"supply={len(data['supply_items'])}  "
        f"demand={len(data['demand_items'])}  "
        f"operators={len(data['operator_items'])}  "
        f"events={len(data['event_items'])}  "
        f"leads={len(data['lead_items'])}"
    )

    if data["has_stale_data"]:
        log.warning(
            f"STALE: {data['stale_match_count']} match(es) excluded. "
            f"Run --clear-stale to remove from DB."
        )

    if args.dry_run:
        print(f"generated_at         : {data['generated_at']}")
        print(f"data_mode            : {data['data_mode']}")
        print(f"include_seed_data    : {include_seed}")
        print(f"total_signals        : {data['total_signal_count']}")
        print(f"unmatched_signals    : {data['unmatched_signal_count']}")
        print(f"seed_signals         : {data['seed_signal_count']}")
        print(f"live_signals         : {data['live_signal_count']}")
        print(f"manual_signals       : {data['manual_signal_count']}")
        print(f"supply_items         : {len(data['supply_items'])}")
        print(f"demand_items         : {len(data['demand_items'])}")
        print(f"operator_items       : {len(data['operator_items'])}")
        print(f"infrastructure_items : {len(data['infrastructure_items'])}")
        print(f"business_for_sale    : {len(data['business_for_sale_items'])}")
        print(f"event_items          : {len(data['event_items'])}")
        print(f"lead_items           : {len(data['lead_items'])}")
        print(f"matched_deals        : {len(data['matched_deals'])}")
        print(f"assembly_opps        : {len(data['assembly_opps'])}")
        print(f"buyer_leads          : {len(data['buyer_leads'])}")
        print(f"stale_matches        : {data['stale_match_count']}")
        print(f"sources_config       : {len(data['sources_config'])}")
        log.info("Dry-run — nothing written.")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    sz = out_path.stat().st_size
    log.info(f"Written: {out_path}  ({sz:,} bytes)")

if __name__ == "__main__":
    main()
