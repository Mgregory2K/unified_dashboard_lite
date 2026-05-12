"""
scanner.py — Green Gregory Group Opportunity OS
Phase 2: Real feed scanner + seed data for local testing

Usage:
    python scanner.py                          # scan all live sources
    python scanner.py --seed-test-data         # insert 5 realistic test rows
    python scanner.py --source craigslist      # single source
    python scanner.py --dry-run                # parse + print, no DB writes
    python scanner.py --seed-test-data --dry-run  # preview seed rows only
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

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scanner")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

KEYWORDS = [
    "network", "cisco", "juniper", "server", "rack", "switch", "router",
    "pallet", "lot", "surplus", "liquidation", "equipment", "tools",
    "forklift", "generator", "hvac", "pump", "compressor", "fleet",
    "vehicle", "trailer", "telecom", "audio", "video", "ice machine",
    "pressure washer", "scrubber", "racking", "shelving", "industrial",
    "machinery", "office furniture",
]

GOVDEALS_FEEDS = [
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=network+equipment&kField=titl&sortBy=ad&sortOrder=d&Category=45",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=server+rack&kField=titl&sortBy=ad&sortOrder=d&Category=45",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=cisco+switch&kField=titl&sortBy=ad&sortOrder=d",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=surplus+equipment&kField=titl&sortBy=ad&sortOrder=d",
]

PUBLICSURPLUS_FEEDS = [
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=network",
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=cisco",
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=server",
    "https://www.publicsurplus.com/sms/browse/rss?catid=0&search=surplus+lot",
    "https://www.publicsurplus.com/sms/browse/rss?catid=26&search=equipment",
]

CRAIGSLIST_SEARCHES = [
    ("cincinnati",   "sss", "network equipment"),
    ("cincinnati",   "sss", "server rack"),
    ("indianapolis", "sss", "surplus liquidation"),
    ("indianapolis", "bfs", "equipment lot"),
    ("dayton",       "sss", "pallet lot"),
    ("louisville",   "sss", "cisco switch"),
]

# ── Seed data ─────────────────────────────────────────────────────────────────

SEED_LISTINGS = [
    {
        "source":       "govdeals",
        "title":        "Commercial Ice Machine — Manitowoc ID-0606A, 600 lb/day",
        "url":          "https://www.govdeals.com/seed/ice-machine-001",
        "price_raw":    "$425",
        "price_est":    425.0,
        "location":     "Cincinnati, OH",
        "category":     "govdeals/food-equipment",
        "description":  (
            "Manitowoc commercial ice machine, 600 lb per day capacity. "
            "Removed from county cafeteria. Powers on, tested. Pickup only. "
            "Auction ends in 5 days. Lot of restaurant/food service equipment."
        ),
        "end_date":     "2026-05-20",
        "keywords_hit": json.dumps(["equipment", "lot", "ice machine"]),
    },
    {
        "source":       "govdeals",
        "title":        "Pallet Racking System — 24 uprights, 96 crossbeams, teardrop style",
        "url":          "https://www.govdeals.com/seed/pallet-racking-002",
        "price_raw":    "$850",
        "price_est":    850.0,
        "location":     "Indianapolis, IN",
        "category":     "govdeals/industrial",
        "description":  (
            "Complete warehouse pallet racking system, teardrop style. "
            "24 uprights 8ft tall, 96 crossbeams 96 inches. "
            "Disassembled and ready for pickup. Selling as one lot. "
            "Surplus from state warehouse consolidation. Industrial shelving."
        ),
        "end_date":     "2026-05-18",
        "keywords_hit": json.dumps(["pallet", "rack", "surplus", "lot", "industrial", "racking", "shelving"]),
    },
    {
        "source":       "publicsurplus",
        "title":        "Cisco Catalyst 2960X-48FPD-L Switch Lot — 6 units",
        "url":          "https://www.publicsurplus.com/seed/cisco-switches-003",
        "price_raw":    "$1,200",
        "price_est":    1200.0,
        "location":     "Dayton, OH",
        "category":     "publicsurplus/electronics",
        "description":  (
            "Lot of 6 Cisco Catalyst 2960X-48FPD-L switches pulled from school "
            "district network refresh. All units power on. No configuration wiped — "
            "buyer responsible. Includes rack ears. Network equipment surplus auction."
        ),
        "end_date":     "2026-05-22",
        "keywords_hit": json.dumps(["cisco", "network", "switch", "rack", "equipment", "lot", "surplus"]),
    },
    {
        "source":       "craigslist",
        "title":        "Gas Pressure Washer 4000 PSI — Honda engine, commercial grade",
        "url":          "https://cincinnati.craigslist.org/seed/pressure-washer-004",
        "price_raw":    "$375",
        "price_est":    375.0,
        "location":     "Cincinnati, OH",
        "category":     "craigslist/cincinnati/sss",
        "description":  (
            "Commercial gas pressure washer, 4000 PSI, Honda GX390 engine. "
            "Used for 2 seasons by a landscaping company liquidating equipment. "
            "Starts first pull. Includes 50ft hose and 4 tips. "
            "Tools and equipment lot also available."
        ),
        "end_date":     "",
        "keywords_hit": json.dumps(["equipment", "tools", "liquidation", "pressure washer"]),
    },
    {
        "source":       "publicsurplus",
        "title":        "Industrial Floor Scrubber — Tennant T5, ride-on, 28 inch",
        "url":          "https://www.publicsurplus.com/seed/floor-scrubber-005",
        "price_raw":    "$2,200",
        "price_est":    2200.0,
        "location":     "Louisville, KY",
        "category":     "publicsurplus/industrial",
        "description":  (
            "Tennant T5 ride-on floor scrubber, 28 inch scrub path, 200Ah battery. "
            "Surplus from county facility maintenance department. Hours: 1,840. "
            "New squeegee blade installed. Industrial machinery surplus auction. "
            "Buyer must arrange freight or pickup within 10 days."
        ),
        "end_date":     "2026-05-25",
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
        label = "DRY " if dry_run else "    "
        log.info(f"  SEED {label}[{item['price_raw']:>7}]  {item['title'][:65]}")
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


def make_id(source: str, url: str) -> str:
    return hashlib.sha256(f"{source}|{url}".encode()).hexdigest()[:16]


def extract_price(text: str):
    if not text:
        return None
    m = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
    if m:
        return float(m.group().replace("$", "").replace(",", ""))
    return None


def keyword_hits(text: str) -> list:
    t = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in t]


def upsert(conn, row: dict, dry_run=False) -> bool:
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


# ── Feed fetching with diagnostics ────────────────────────────────────────────

def fetch_feed_with_diagnostics(url: str):
    """
    Fetches URL, logs HTTP status + content-type + body preview on any issue.
    Returns (feedparser_feed | None, raw_text).
    Falls back to BeautifulSoup XML extraction if feedparser returns 0 entries.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        ct   = resp.headers.get("content-type", "unknown")
        log.info(f"    HTTP {resp.status_code}  Content-Type: {ct}")

        if resp.status_code != 200:
            log.warning(f"    Non-200. Body preview: {resp.text[:300]!r}")
            return None, resp.text

        raw  = resp.text
        feed = feedparser.parse(raw)

        if feed.entries:
            return feed, raw

        # feedparser got 0 entries — try BS4 fallback
        log.info("    feedparser: 0 entries — trying BS4 XML fallback")
        soup  = BeautifulSoup(raw, "xml")
        items = soup.find_all("item") or soup.find_all("entry")
        if items:
            log.info(f"    BS4 found {len(items)} items (feedparser missed them)")
        else:
            log.warning(f"    BS4 also 0 items. Body preview: {raw[:300]!r}")

        return feed, raw

    except requests.RequestException as e:
        log.error(f"    Request error: {e}")
        return None, ""


# ── Source parsers ─────────────────────────────────────────────────────────────

def parse_feed_entry(entry, source: str):
    title = entry.get("title", "").strip()
    url   = entry.get("link",  "").strip()
    if not title or not url:
        return None
    desc     = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ")
    combined = f"{title} {desc}"
    hits     = keyword_hits(combined)
    if not hits:
        return None
    price_raw = ""
    for field in (title, desc):
        m = re.search(r"\$[\d,]+(?:\.\d{2})?", field)
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
        "location":     (entry.get("tags") or [{}])[0].get("term", ""),
        "category":     source,
        "description":  desc[:1000],
        "end_date":     entry.get("published", ""),
        "scraped_at":   datetime.now(timezone.utc).isoformat(),
        "keywords_hit": json.dumps(hits),
    }


def scan_rss(feed_urls: list, source: str, conn, dry_run=False) -> int:
    inserted = 0
    for url in feed_urls:
        log.info(f"  Fetching {source}: {url[:90]}...")
        feed, _ = fetch_feed_with_diagnostics(url)
        if feed is None:
            continue
        log.info(f"    {len(feed.entries)} entries parsed")
        for entry in feed.entries:
            row = parse_feed_entry(entry, source)
            if row and upsert(conn, row, dry_run):
                inserted += 1
    if not dry_run:
        conn.commit()
    return inserted


def scan_craigslist(searches: list, conn, dry_run=False) -> int:
    inserted = 0
    for city, category, query in searches:
        encoded = query.replace(" ", "+")
        url = f"https://{city}.craigslist.org/search/{category}/rss?query={encoded}&format=rss"
        log.info(f"  Craigslist {city}/{query}")
        feed, _ = fetch_feed_with_diagnostics(url)
        if feed is None:
            continue
        log.info(f"    {len(feed.entries)} entries")
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link  = entry.get("link",  "").strip()
            if not title or not link:
                continue
            desc     = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ")
            combined = f"{title} {desc} {query}"
            hits     = keyword_hits(combined) or [query]
            price_raw = ""
            m = re.search(r"\$[\d,]+(?:\.\d{2})?", title + " " + desc)
            if m:
                price_raw = m.group()
            row = {
                "id":           make_id("craigslist", link),
                "source":       "craigslist",
                "title":        title[:400],
                "url":          link[:800],
                "price_raw":    price_raw,
                "price_est":    extract_price(price_raw),
                "location":     (entry.get("tags") or [{}])[0].get("term", "") or city,
                "category":     f"craigslist/{city}/{category}",
                "description":  desc[:1000],
                "end_date":     entry.get("published", ""),
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
    parser = argparse.ArgumentParser(description="Green Gregory Group scanner")
    parser.add_argument("--source",
                        choices=["govdeals", "publicsurplus", "craigslist", "all"],
                        default="all")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse without writing to DB")
    parser.add_argument("--seed-test-data", action="store_true",
                        help="Insert 5 realistic test listings for local pipeline testing")
    args = parser.parse_args()

    conn  = get_db()
    total = 0
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info(f"=== Green Gregory Scanner — {ts} ===")
    log.info(f"DB: {DB_PATH.resolve()}  |  dry_run={args.dry_run}  |  seed={args.seed_test_data}")

    if args.seed_test_data:
        log.info("── Seeding test data ──")
        n = insert_seed_data(conn, dry_run=args.dry_run)
        total += n
        log.info(f"   Seed complete: {n} rows {'previewed (dry)' if args.dry_run else 'inserted'}")
        counts = conn.execute(
            "SELECT status, COUNT(*) n FROM raw_listings GROUP BY status"
        ).fetchall()
        log.info("── DB raw_listings current state ──")
        for r in counts:
            log.info(f"   status={r['status']:12}  count={r['n']}")
        conn.close()
        log.info(f"=== Seed done — {total} rows ===")
        return

    if args.source in ("govdeals", "all"):
        log.info("── GovDeals ──")
        n = scan_rss(GOVDEALS_FEEDS, "govdeals", conn, args.dry_run)
        log.info(f"   GovDeals: {n} new listings")
        total += n

    if args.source in ("publicsurplus", "all"):
        log.info("── PublicSurplus ──")
        n = scan_rss(PUBLICSURPLUS_FEEDS, "publicsurplus", conn, args.dry_run)
        log.info(f"   PublicSurplus: {n} new listings")
        total += n

    if args.source in ("craigslist", "all"):
        log.info("── Craigslist ──")
        n = scan_craigslist(CRAIGSLIST_SEARCHES, conn, args.dry_run)
        log.info(f"   Craigslist: {n} new listings")
        total += n

    log.info(f"=== Scan complete — {total} new listings total ===")
    conn.close()


if __name__ == "__main__":
    main()
