"""
scanner.py — Green Gregory Group Opportunity OS
Phase 2: Real feed scanner for GovDeals, PublicSurplus, Craigslist RSS

Pulls live listings → normalizes → deduplicates → inserts into SQLite.
Run manually or via GitHub Actions (every 12h).

Usage:
    python scanner.py                    # scan all sources
    python scanner.py --source craigslist  # single source
    python scanner.py --dry-run           # print without writing
"""

import sqlite3
import feedparser
import requests
import hashlib
import json
import re
import argparse
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = Path("opportunity_os.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("scanner")

# Keywords that flag a listing as relevant to the ghost arbitrage model.
# Tune this list to your target categories.
KEYWORDS = [
    "network", "cisco", "juniper", "server", "rack", "switch", "router",
    "pallet", "lot", "surplus", "liquidation", "equipment", "tools",
    "forklift", "generator", "HVAC", "pump", "compressor", "fleet",
    "vehicle", "trailer", "IT", "telecom", "AV", "audio", "video",
    "medical", "industrial", "machinery", "office furniture", "desks",
]

# Craigslist searches — add/remove cities and categories as needed.
# Format: (city_subdomain, category_code, search_query)
CRAIGSLIST_SEARCHES = [
    ("cincinnati", "sss", "network equipment"),
    ("cincinnati", "sss", "server rack"),
    ("indianapolis", "sss", "surplus liquidation"),
    ("indianapolis", "bfs", "equipment lot"),
    ("dayton",       "sss", "pallet lot"),
    ("louisville",   "sss", "cisco switch"),
]

# GovDeals RSS — keyword search via their auction search RSS endpoint.
# URL format confirmed as of 2025. If GovDeals changes their URL structure,
# update the kWord parameter. Results are also keyword-filtered client-side.
GOVDEALS_FEEDS = [
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=network+equipment&kField=titl&sortBy=ad&sortOrder=d&Category=45",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=server+rack&kField=titl&sortBy=ad&sortOrder=d&Category=45",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=cisco+switch&kField=titl&sortBy=ad&sortOrder=d",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=surplus+equipment&kField=titl&sortBy=ad&sortOrder=d",
]

# PublicSurplus RSS — their search RSS endpoint
# Category 0 = All, adjust catid for specific categories:
#   57 = Electronics/Computers, 26 = Heavy Equipment
PUBLICSURPLUS_FEEDS = [
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=network",
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=cisco",
    "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=server",
    "https://www.publicsurplus.com/sms/browse/rss?catid=0&search=surplus+lot",
    "https://www.publicsurplus.com/sms/browse/rss?catid=26&search=equipment",
]

# ── Database setup ─────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_listings (
    id          TEXT PRIMARY KEY,   -- SHA-256 of (source+url)
    source      TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    price_raw   TEXT,
    price_est   REAL,
    location    TEXT,
    category    TEXT,
    description TEXT,
    end_date    TEXT,
    scraped_at  TEXT,
    keywords_hit TEXT,
    status      TEXT DEFAULT 'new'  -- new | reviewed | matched | archived
);

CREATE INDEX IF NOT EXISTS idx_status   ON raw_listings(status);
CREATE INDEX IF NOT EXISTS idx_source   ON raw_listings(source);
CREATE INDEX IF NOT EXISTS idx_scraped  ON raw_listings(scraped_at);
"""

def get_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def make_id(source: str, url: str) -> str:
    return hashlib.sha256(f"{source}|{url}".encode()).hexdigest()[:16]


def extract_price(text: str) -> float | None:
    """Pull first dollar amount from a string."""
    if not text:
        return None
    m = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
    if m:
        return float(m.group().replace("$", "").replace(",", ""))
    return None


def keyword_hits(text: str) -> list[str]:
    text_lower = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in text_lower]


def upsert(conn, row: dict, dry_run=False) -> bool:
    """Insert if new. Returns True if inserted."""
    existing = conn.execute(
        "SELECT id FROM raw_listings WHERE id = ?", (row["id"],)
    ).fetchone()
    if existing:
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


# ── Source parsers ─────────────────────────────────────────────────────────────

def parse_feed_entry(entry, source: str) -> dict | None:
    """Normalize a feedparser entry into our row schema."""
    title = entry.get("title", "").strip()
    url   = entry.get("link", "").strip()
    if not title or not url:
        return None

    # Combine all text for keyword matching
    desc  = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ")
    combined = f"{title} {desc}"
    hits = keyword_hits(combined)
    if not hits:
        return None  # Skip irrelevant listings

    price_raw = ""
    # Try to pull price from title or description
    for field in (title, desc):
        m = re.search(r"\$[\d,]+(?:\.\d{2})?", field)
        if m:
            price_raw = m.group()
            break

    end_date = ""
    if hasattr(entry, "published"):
        end_date = entry.get("published", "")

    return {
        "id":           make_id(source, url),
        "source":       source,
        "title":        title[:400],
        "url":          url[:800],
        "price_raw":    price_raw,
        "price_est":    extract_price(price_raw),
        "location":     entry.get("tags", [{}])[0].get("term", "") if entry.get("tags") else "",
        "category":     source,
        "description":  desc[:1000],
        "end_date":     end_date,
        "scraped_at":   datetime.utcnow().isoformat(),
        "keywords_hit": json.dumps(hits),
    }


def scan_rss(feed_urls: list[str], source: str, conn, dry_run=False) -> int:
    inserted = 0
    # Spoof a browser UA — some auction sites block Python/feedparser defaults
    feedparser.USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 GreenGregoryOS/2.0"
    )
    for url in feed_urls:
        log.info(f"  Fetching {source} feed: {url[:80]}...")
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                log.warning(f"    Feed parse error: {feed.bozo_exception}")
                continue
            log.info(f"    {len(feed.entries)} entries found")
            for entry in feed.entries:
                row = parse_feed_entry(entry, source)
                if row and upsert(conn, row, dry_run):
                    inserted += 1
                    log.debug(f"    + {row['title'][:60]}")
        except Exception as e:
            log.error(f"    Error fetching {url}: {e}")
    if not dry_run:
        conn.commit()
    return inserted


def scan_craigslist(searches: list[tuple], conn, dry_run=False) -> int:
    """Craigslist RSS feeds — one per city/category/query combo."""
    inserted = 0
    for city, category, query in searches:
        encoded_query = query.replace(" ", "+")
        url = f"https://{city}.craigslist.org/search/{category}/rss?query={encoded_query}&format=rss"
        log.info(f"  Craigslist {city}/{query}: {url}")
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                log.warning(f"    Parse issue: {feed.bozo_exception}")
                continue
            log.info(f"    {len(feed.entries)} entries")
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link  = entry.get("link",  "").strip()
                if not title or not link:
                    continue
                desc  = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ")
                combined = f"{title} {desc} {query}"
                hits = keyword_hits(combined) or [query]

                # Craigslist often has price in title: "$450 Cisco switch"
                price_raw = ""
                m = re.search(r"\$[\d,]+(?:\.\d{2})?", title + " " + desc)
                if m:
                    price_raw = m.group()

                location_tag = ""
                if entry.get("tags"):
                    location_tag = entry["tags"][0].get("term", "")

                row = {
                    "id":           make_id("craigslist", link),
                    "source":       "craigslist",
                    "title":        title[:400],
                    "url":          link[:800],
                    "price_raw":    price_raw,
                    "price_est":    extract_price(price_raw),
                    "location":     location_tag or city,
                    "category":     f"craigslist/{city}/{category}",
                    "description":  desc[:1000],
                    "end_date":     entry.get("published", ""),
                    "scraped_at":   datetime.utcnow().isoformat(),
                    "keywords_hit": json.dumps(hits),
                }
                if upsert(conn, row, dry_run):
                    inserted += 1
        except Exception as e:
            log.error(f"    Error: {e}")
    if not dry_run:
        conn.commit()
    return inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory Group scanner")
    parser.add_argument("--source", choices=["govdeals", "publicsurplus", "craigslist", "all"],
                        default="all")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing to DB")
    args = parser.parse_args()

    conn = get_db()
    total = 0

    log.info(f"=== Green Gregory Scanner — {datetime.utcnow().isoformat()} ===")
    log.info(f"DB: {DB_PATH.resolve()}  |  dry_run={args.dry_run}")

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
    return total


if __name__ == "__main__":
    main()
