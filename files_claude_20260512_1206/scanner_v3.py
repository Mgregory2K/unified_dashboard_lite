"""
scanner.py — Green Gregory Group Opportunity OS
Phase 3: Config-driven — reads all feed URLs and keywords from config.json

Run config_reader.py first (or use --fallback to use built-in defaults).

Usage:
    python scanner.py                    # scan all live sources
    python scanner.py --seed-test-data   # insert 5 realistic test rows
    python scanner.py --source craigslist
    python scanner.py --dry-run
    python scanner.py --fallback         # skip config.json, use built-in defaults
"""

import sqlite3
import feedparser
import requests
import hashlib
import json
import re
import argparse
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from pathlib import Path

from config_reader import load_config

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scanner")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# ── Seed data (unchanged — used for local testing only) ───────────────────────

SEED_LISTINGS = [
    {
        "source": "govdeals",
        "title": "Commercial Ice Machine — Manitowoc ID-0606A, 600 lb/day",
        "url": "https://www.govdeals.com/seed/ice-machine-001",
        "price_raw": "$425", "price_est": 425.0,
        "location": "Cincinnati, OH", "category": "govdeals/food-equipment",
        "description": (
            "Manitowoc commercial ice machine, 600 lb per day capacity. "
            "Removed from county cafeteria. Powers on, tested. "
            "Lot of restaurant/food service equipment."
        ),
        "end_date": "2026-05-20",
        "keywords_hit": json.dumps(["equipment", "lot", "ice machine"]),
    },
    {
        "source": "govdeals",
        "title": "Pallet Racking System — 24 uprights, 96 crossbeams, teardrop style",
        "url": "https://www.govdeals.com/seed/pallet-racking-002",
        "price_raw": "$850", "price_est": 850.0,
        "location": "Indianapolis, IN", "category": "govdeals/industrial",
        "description": (
            "Complete warehouse pallet racking system, teardrop style. "
            "24 uprights, 96 crossbeams. Surplus state warehouse consolidation. "
            "Industrial shelving and racking lot."
        ),
        "end_date": "2026-05-18",
        "keywords_hit": json.dumps(["pallet", "rack", "surplus", "lot", "industrial", "racking", "shelving"]),
    },
    {
        "source": "publicsurplus",
        "title": "Cisco Catalyst 2960X-48FPD-L Switch Lot — 6 units",
        "url": "https://www.publicsurplus.com/seed/cisco-switches-003",
        "price_raw": "$1,200", "price_est": 1200.0,
        "location": "Dayton, OH", "category": "publicsurplus/electronics",
        "description": (
            "Lot of 6 Cisco Catalyst 2960X switches from school district network refresh. "
            "All power on. Includes rack ears. Network equipment surplus auction."
        ),
        "end_date": "2026-05-22",
        "keywords_hit": json.dumps(["cisco", "network", "switch", "rack", "equipment", "lot", "surplus"]),
    },
    {
        "source": "craigslist",
        "title": "Gas Pressure Washer 4000 PSI — Honda engine, commercial grade",
        "url": "https://cincinnati.craigslist.org/seed/pressure-washer-004",
        "price_raw": "$375", "price_est": 375.0,
        "location": "Cincinnati, OH", "category": "craigslist/cincinnati/sss",
        "description": (
            "Commercial gas pressure washer, 4000 PSI, Honda GX390 engine. "
            "Landscaping company liquidating equipment. Tools and equipment lot."
        ),
        "end_date": "",
        "keywords_hit": json.dumps(["equipment", "tools", "liquidation", "pressure washer"]),
    },
    {
        "source": "publicsurplus",
        "title": "Industrial Floor Scrubber — Tennant T5, ride-on, 28 inch",
        "url": "https://www.publicsurplus.com/seed/floor-scrubber-005",
        "price_raw": "$2,200", "price_est": 2200.0,
        "location": "Louisville, KY", "category": "publicsurplus/industrial",
        "description": (
            "Tennant T5 ride-on floor scrubber, 28 inch. Surplus county facility. "
            "Industrial machinery surplus auction."
        ),
        "end_date": "2026-05-25",
        "keywords_hit": json.dumps(["industrial", "machinery", "surplus", "equipment", "scrubber"]),
    },
]


def insert_seed_data(conn, dry_run=False) -> int:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for item in SEED_LISTINGS:
        item_id = make_id(item["source"], item["url"])
        if conn.execute("SELECT id FROM raw_listings WHERE id = ?", (item_id,)).fetchone():
            log.info(f"  SEED already exists: {item['title'][:60]}")
            continue
        row = {**item, "id": item_id, "scraped_at": now, "status": "new"}
        if not dry_run:
            conn.execute("""
                INSERT INTO raw_listings
                    (id, source, title, url, price_raw, price_est, location,
                     category, description, end_date, scraped_at, keywords_hit, status)
                VALUES
                    (:id, :source, :title, :url, :price_raw, :price_est, :location,
                     :category, :description, :end_date, :scraped_at, :keywords_hit, :status)
            """, row)
        inserted += 1
        log.info(f"  SEED {'DRY ' if dry_run else '    '}[{item['price_raw']:>7}]  {item['title'][:65]}")
    if not dry_run:
        conn.commit()
    return inserted


# ── Database ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_listings (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    price_raw    TEXT,
    price_est    REAL,
    location     TEXT,
    category     TEXT,
    description  TEXT,
    end_date     TEXT,
    scraped_at   TEXT,
    keywords_hit TEXT,
    status       TEXT DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_status  ON raw_listings(status);
CREATE INDEX IF NOT EXISTS idx_source  ON raw_listings(source);
CREATE INDEX IF NOT EXISTS idx_scraped ON raw_listings(scraped_at);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def make_id(source, url):
    return hashlib.sha256(f"{source}|{url}".encode()).hexdigest()[:16]


def extract_price(text):
    if not text:
        return None
    m = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
    return float(m.group().replace("$","").replace(",","")) if m else None


def keyword_hits(text, keywords):
    t = text.lower()
    return [kw for kw in keywords if kw.lower() in t]


def upsert(conn, row, dry_run=False):
    if conn.execute("SELECT id FROM raw_listings WHERE id = ?", (row["id"],)).fetchone():
        return False
    if not dry_run:
        conn.execute("""
            INSERT INTO raw_listings
                (id, source, title, url, price_raw, price_est, location,
                 category, description, end_date, scraped_at, keywords_hit, status)
            VALUES
                (:id, :source, :title, :url, :price_raw, :price_est, :location,
                 :category, :description, :end_date, :scraped_at, :keywords_hit, 'new')
        """, row)
    return True


# ── Feed fetching ─────────────────────────────────────────────────────────────

def fetch_feed(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        ct   = resp.headers.get("content-type", "unknown")
        log.info(f"    HTTP {resp.status_code}  Content-Type: {ct}")
        if resp.status_code != 200:
            log.warning(f"    Non-200. Body preview: {resp.text[:300]!r}")
            return None
        raw  = resp.text
        feed = feedparser.parse(raw)
        if not feed.entries:
            log.info("    feedparser: 0 entries — trying BS4 fallback")
            soup  = BeautifulSoup(raw, "xml")
            items = soup.find_all("item") or soup.find_all("entry")
            if items:
                log.info(f"    BS4 found {len(items)} items")
            else:
                log.warning(f"    BS4 also 0 items. Preview: {raw[:300]!r}")
        return feed
    except requests.RequestException as e:
        log.error(f"    Request error: {e}")
        return None


def parse_entry(entry, source, keywords):
    title = entry.get("title","").strip()
    url   = entry.get("link","").strip()
    if not title or not url:
        return None
    desc  = BeautifulSoup(entry.get("summary",""), "html.parser").get_text(" ")
    hits  = keyword_hits(f"{title} {desc}", keywords)
    if not hits:
        return None
    price_raw = ""
    for f in (title, desc):
        m = re.search(r"\$[\d,]+(?:\.\d{2})?", f)
        if m:
            price_raw = m.group()
            break
    return {
        "id":           make_id(source, url),
        "source":       source,
        "title":        title[:400],
        "url":          url[:800],
        "price_raw":    price_raw,
        "price_est":    extract_price(price_raw),
        "location":     (entry.get("tags") or [{}])[0].get("term",""),
        "category":     source,
        "description":  desc[:1000],
        "end_date":     entry.get("published",""),
        "scraped_at":   datetime.now(timezone.utc).isoformat(),
        "keywords_hit": json.dumps(hits),
    }


def scan_rss(feed_urls, source, conn, keywords, dry_run=False):
    inserted = 0
    for url in feed_urls:
        log.info(f"  {source}: {url[:90]}...")
        feed = fetch_feed(url)
        if not feed:
            continue
        log.info(f"    {len(feed.entries)} entries")
        for entry in feed.entries:
            row = parse_entry(entry, source, keywords)
            if row and upsert(conn, row, dry_run):
                inserted += 1
    if not dry_run:
        conn.commit()
    return inserted


def scan_craigslist(searches, conn, keywords, dry_run=False):
    inserted = 0
    for s in searches:
        city, cat, query = s["city"], s["category"], s["query"]
        url = f"https://{city}.craigslist.org/search/{cat}/rss?query={query.replace(' ','+')}&format=rss"
        log.info(f"  Craigslist {city}/{query}")
        feed = fetch_feed(url)
        if not feed:
            continue
        log.info(f"    {len(feed.entries)} entries")
        for entry in feed.entries:
            title = entry.get("title","").strip()
            link  = entry.get("link","").strip()
            if not title or not link:
                continue
            desc  = BeautifulSoup(entry.get("summary",""), "html.parser").get_text(" ")
            hits  = keyword_hits(f"{title} {desc} {query}", keywords) or [query]
            m     = re.search(r"\$[\d,]+(?:\.\d{2})?", title + " " + desc)
            row   = {
                "id":           make_id("craigslist", link),
                "source":       "craigslist",
                "title":        title[:400],
                "url":          link[:800],
                "price_raw":    m.group() if m else "",
                "price_est":    extract_price(m.group() if m else ""),
                "location":     (entry.get("tags") or [{}])[0].get("term","") or city,
                "category":     f"craigslist/{city}/{cat}",
                "description":  desc[:1000],
                "end_date":     entry.get("published",""),
                "scraped_at":   datetime.now(timezone.utc).isoformat(),
                "keywords_hit": json.dumps(hits),
            }
            if upsert(conn, row, dry_run):
                inserted += 1
    if not dry_run:
        conn.commit()
    return inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory scanner")
    parser.add_argument("--source", choices=["govdeals","publicsurplus","craigslist","all"], default="all")
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--seed-test-data", action="store_true")
    parser.add_argument("--fallback",       action="store_true",
                        help="Use built-in defaults, skip config.json")
    args = parser.parse_args()

    cfg  = load_config() if not args.fallback else None
    if cfg is None:
        from config_reader import build_default_config
        cfg = build_default_config()

    sc       = cfg["search_criteria"]
    keywords = sc["keywords"]

    conn  = get_db()
    total = 0
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info(f"=== Green Gregory Scanner — {ts} ===")
    log.info(f"Config source: {cfg.get('_source','?')}  |  dry_run={args.dry_run}")

    if args.seed_test_data:
        log.info("── Seeding test data ──")
        n = insert_seed_data(conn, dry_run=args.dry_run)
        total += n
        log.info(f"   {n} rows {'previewed' if args.dry_run else 'inserted'}")
        for r in conn.execute("SELECT status, COUNT(*) n FROM raw_listings GROUP BY status").fetchall():
            log.info(f"   status={r['status']:12}  count={r['n']}")
        conn.close()
        log.info(f"=== Seed done — {total} rows ===")
        return

    if args.source in ("govdeals","all"):
        log.info("── GovDeals ──")
        n = scan_rss(sc["govdeals_feeds"], "govdeals", conn, keywords, args.dry_run)
        log.info(f"   GovDeals: {n} new"); total += n

    if args.source in ("publicsurplus","all"):
        log.info("── PublicSurplus ──")
        n = scan_rss(sc["publicsurplus_feeds"], "publicsurplus", conn, keywords, args.dry_run)
        log.info(f"   PublicSurplus: {n} new"); total += n

    if args.source in ("craigslist","all"):
        log.info("── Craigslist ──")
        n = scan_craigslist(sc["craigslist_searches"], conn, keywords, args.dry_run)
        log.info(f"   Craigslist: {n} new"); total += n

    log.info(f"=== Scan complete — {total} new listings ===")
    conn.close()


if __name__ == "__main__":
    main()
