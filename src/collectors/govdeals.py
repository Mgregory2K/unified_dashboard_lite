import requests
from bs4 import BeautifulSoup
from datetime import datetime
from models import RawItem
import time
import random

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

SEARCH_TERMS = [
    "restaurant equipment",
    "commercial refrigerator",
    "generator",
    "pressure washer",
    "pallet rack",
    "floor scrubber",
    "office furniture",
    "trailer",
]


def fetch_govdeals(market: str = "cincinnati") -> list[RawItem]:
    items = []
    base_url = "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsNew"

    for term in SEARCH_TERMS:
        try:
            params = {
                "searchType": "ALL",
                "category": "0",
                "query": term,
                "sortBy": "ts",
                "sortOrder": "d",
            }
            resp = requests.get(base_url, params=params, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            listings = soup.select(".listing-item, .auction-item, article")
            for listing in listings[:10]:
                try:
                    title_el = listing.select_one("h2, h3, .title, .item-title")
                    link_el = listing.select_one("a[href]")
                    price_el = listing.select_one(".price, .current-bid, .bid-amount")
                    loc_el = listing.select_one(".location, .item-location")

                    if not title_el or not link_el:
                        continue

                    title = title_el.get_text(strip=True)
                    href = link_el.get("href", "")
                    url = href if href.startswith("http") else "https://www.govdeals.com" + href
                    price_text = price_el.get_text(strip=True) if price_el else ""
                    location = loc_el.get_text(strip=True) if loc_el else market

                    import re
                    price = None
                    m = re.search(r'[\d,]+\.?\d*', price_text.replace('$', '').replace(',', ''))
                    if m:
                        try:
                            price = float(m.group())
                        except ValueError:
                            pass

                    items.append(RawItem(
                        source="govdeals",
                        source_item_id=url,
                        url=url,
                        title=title,
                        description=f"Search term: {term}",
                        price=price,
                        location=location,
                        posted_at=datetime.utcnow(),
                    ))
                except Exception:
                    continue

            time.sleep(random.uniform(1.5, 3.0))

        except Exception as e:
            print(f"[GovDeals] Error fetching '{term}': {e}")

    return items


def fetch_publicsurplus(market: str = "cincinnati") -> list[RawItem]:
    items = []
    base_url = "https://www.publicsurplus.com/sms/browse/home"

    for term in SEARCH_TERMS:
        try:
            params = {"searchStr": term, "catId": 0}
            resp = requests.get(base_url, params=params, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            rows = soup.select("tr.auction-row, .auction-item, table tbody tr")
            for row in rows[:10]:
                try:
                    cells = row.select("td")
                    if len(cells) < 3:
                        continue
                    title = cells[0].get_text(strip=True)
                    link_el = cells[0].select_one("a")
                    if not link_el:
                        continue
                    href = link_el.get("href", "")
                    url = href if href.startswith("http") else "https://www.publicsurplus.com" + href
                    price_text = cells[-1].get_text(strip=True)

                    import re
                    price = None
                    m = re.search(r'[\d,]+\.?\d*', price_text.replace('$', '').replace(',', ''))
                    if m:
                        try:
                            price = float(m.group())
                        except ValueError:
                            pass

                    items.append(RawItem(
                        source="publicsurplus",
                        source_item_id=url,
                        url=url,
                        title=title,
                        description=f"Search term: {term}",
                        price=price,
                        location=market,
                        posted_at=datetime.utcnow(),
                    ))
                except Exception:
                    continue

            time.sleep(random.uniform(1.5, 2.5))

        except Exception as e:
            print(f"[PublicSurplus] Error fetching '{term}': {e}")

    return items
