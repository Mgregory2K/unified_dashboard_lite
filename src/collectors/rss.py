import feedparser
from datetime import datetime
from models import RawItem
import re

# Google Alerts RSS feeds — set these up at google.com/alerts
# Export as RSS and paste the feed URLs below
FEED_URLS = [
    # Add your Google Alert RSS URLs here after setup
    # Example: "https://www.google.com/alerts/feeds/YOUR_ID/YOUR_FEED_KEY"
]

# Fallback: public Craigslist RSS (more stable than scraping)
CRAIGSLIST_RSS = [
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=restaurant+equipment",
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=commercial+refrigerator",
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=generator",
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=pallet+rack",
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=pressure+washer",
    "https://cincinnati.craigslist.org/search/bia?format=rss&query=floor+scrubber",
    "https://cincinnati.craigslist.org/search/sss?format=rss&query=liquidation",
    "https://cincinnati.craigslist.org/search/sss?format=rss&query=business+closing",
]


def fetch_rss(market: str = "cincinnati") -> list[RawItem]:
    items = []
    all_feeds = CRAIGSLIST_RSS + FEED_URLS

    for url in all_feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")
                published = entry.get("published_parsed")

                posted_at = None
                if published:
                    posted_at = datetime(*published[:6])

                price = None
                price_match = re.search(r'\$[\d,]+', title + " " + summary)
                if price_match:
                    try:
                        price = float(price_match.group().replace('$', '').replace(',', ''))
                    except ValueError:
                        pass

                if not title or not link:
                    continue

                items.append(RawItem(
                    source="rss",
                    source_item_id=link,
                    url=link,
                    title=title,
                    description=summary,
                    price=price,
                    location=market,
                    posted_at=posted_at,
                ))
        except Exception as e:
            print(f"[RSS] Error fetching {url}: {e}")

    return items
