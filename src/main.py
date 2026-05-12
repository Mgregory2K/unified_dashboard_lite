import argparse
import os
import sys
import schedule
import time
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from storage import init_db, insert_raw_item, insert_opportunity, export_csv
from normalizer import normalize
from deduper import is_duplicate
from scorer import evaluate
from digest import send_digest
from collectors.govdeals import fetch_govdeals, fetch_publicsurplus
from collectors.rss import fetch_rss


def run_scan(market: str = "cincinnati"):
    print(f"\n[Scanner] Starting scan — market: {market}")
    init_db()

    all_items = []
    print("[Scanner] Fetching GovDeals...")
    all_items += fetch_govdeals(market)
    print("[Scanner] Fetching PublicSurplus...")
    all_items += fetch_publicsurplus(market)
    print("[Scanner] Fetching RSS/Craigslist...")
    all_items += fetch_rss(market)

    print(f"[Scanner] {len(all_items)} raw items collected")

    new_count = 0
    opp_count = 0

    for item in all_items:
        item = normalize(item)

        if is_duplicate(item.url):
            continue

        raw_id = insert_raw_item(item)
        if raw_id == 0:
            continue
        new_count += 1

        opp = evaluate(item, market)
        if opp:
            opp.raw_item_id = raw_id
            insert_opportunity(opp)
            opp_count += 1
            print(f"  [+] Score {opp.score:>3} | ${opp.asking_price:>8,.0f} | {item.title[:60]}")

    print(f"[Scanner] Done — {new_count} new items, {opp_count} opportunities flagged")
    export_csv()


def run_digest():
    to_email = os.getenv("DIGEST_TO_EMAIL")
    from_email = os.getenv("DIGEST_FROM_EMAIL")
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not all([to_email, from_email, app_password]):
        print("[Digest] Email env vars not set. Check your .env file.")
        return

    send_digest(to_email, from_email, app_password)


def schedule_daily(market: str, hour: int = 7):
    print(f"[Scheduler] Running daily at {hour:02d}:00 for market: {market}")
    schedule.every().day.at(f"{hour:02d}:00").do(run_scan, market=market)
    schedule.every().day.at(f"{hour:02d}:30").do(run_digest)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ghost Arbitrage Scanner")
    parser.add_argument("--market", default="cincinnati", help="Market to scan")
    parser.add_argument("--digest", action="store_true", help="Send digest after scan")
    parser.add_argument("--schedule", action="store_true", help="Run on daily schedule")
    parser.add_argument("--hour", type=int, default=7, help="Hour to run daily scan (24hr)")
    args = parser.parse_args()

    if args.schedule:
        schedule_daily(args.market, args.hour)
    else:
        run_scan(args.market)
        if args.digest:
            run_digest()
