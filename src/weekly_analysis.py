"""
Weekly Analysis — runs every Sunday.
Reads the SQLite DB, analyzes the week's opportunities,
and emails a plain-English summary with kill/continue signal.
"""

import os
import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "data/scanner.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def analyze_week():
    conn = get_conn()
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM raw_items WHERE scraped_at >= ?", (week_ago,)
    ).fetchone()[0]

    opps = conn.execute("""
        SELECT o.*, r.title, r.url, r.location
        FROM opportunities o
        JOIN raw_items r ON o.raw_item_id = r.id
        WHERE o.created_at >= ?
        ORDER BY o.score DESC
    """, (week_ago,)).fetchall()
    opps = [dict(r) for r in opps]

    review = [o for o in opps if o["status"] == "review"]
    watch = [o for o in opps if o["status"] == "watch"]

    # Best opportunities this week
    top5 = sorted(opps, key=lambda x: x["score"], reverse=True)[:5]

    # Category breakdown
    cat_counts = defaultdict(int)
    cat_spreads = defaultdict(list)
    for o in opps:
        cat_counts[o["category"]] += 1
        if o["spread_low"]:
            cat_spreads[o["category"]].append(o["spread_low"])

    # Average spread
    all_spreads = [o["spread_low"] for o in opps if o["spread_low"]]
    avg_spread = sum(all_spreads) / len(all_spreads) if all_spreads else 0
    max_spread = max(all_spreads) if all_spreads else 0

    # Kill signal assessment
    kill_signals = []
    continue_signals = []

    if max_spread < 300:
        kill_signals.append("No opportunity over $300 spread this week")
    else:
        continue_signals.append(f"Best spread this week: ${max_spread:,.0f}")

    if len(review) == 0:
        kill_signals.append("Zero high-signal items — scanner may need tuning")
    else:
        continue_signals.append(f"{len(review)} high-signal items worth acting on")

    if total < 20:
        kill_signals.append(f"Only {total} items scanned — sources may be breaking")
    else:
        continue_signals.append(f"Scanner pulled {total} raw items this week")

    repeatable_cats = [c for c, spreads in cat_spreads.items() if len(spreads) >= 3]
    if not repeatable_cats:
        kill_signals.append("No repeatable category emerging yet — too early to tell")
    else:
        continue_signals.append(f"Repeatable categories: {', '.join(repeatable_cats)}")

    verdict = "CONTINUE" if len(continue_signals) >= len(kill_signals) else "NEEDS TUNING"
    if len(kill_signals) >= 3:
        verdict = "KILL — reassess the model"

    conn.close()
    return {
        "total_scanned": total,
        "total_opps": len(opps),
        "review_count": len(review),
        "watch_count": len(watch),
        "avg_spread": avg_spread,
        "max_spread": max_spread,
        "top5": top5,
        "cat_counts": dict(cat_counts),
        "cat_spreads": {k: round(sum(v)/len(v)) for k, v in cat_spreads.items() if v},
        "kill_signals": kill_signals,
        "continue_signals": continue_signals,
        "verdict": verdict,
    }


def build_weekly_email(data: dict) -> str:
    verdict_color = {
        "CONTINUE": "#2a9d2a",
        "NEEDS TUNING": "#e6a817",
        "KILL — reassess the model": "#c0392b",
    }.get(data["verdict"], "#333")

    top5_rows = ""
    for i, o in enumerate(data["top5"], 1):
        top5_rows += f"""
        <tr>
          <td style="padding:6px 8px;">{i}</td>
          <td style="padding:6px 8px;"><a href="{o['url']}">{o['title'][:55]}</a></td>
          <td style="padding:6px 8px;">${o['asking_price']:,.0f}</td>
          <td style="padding:6px 8px; color:#2a9d2a;">${o['spread_low']:,.0f}–${o['spread_high']:,.0f}</td>
          <td style="padding:6px 8px; font-weight:bold;">{o['score']}</td>
        </tr>"""

    cat_rows = ""
    for cat, count in sorted(data["cat_counts"].items(), key=lambda x: -x[1]):
        avg = data["cat_spreads"].get(cat, 0)
        cat_rows += f"<tr><td style='padding:5px 8px;'>{cat.replace('_',' ').title()}</td><td style='padding:5px 8px;'>{count}</td><td style='padding:5px 8px; color:#2a9d2a;'>${avg:,}</td></tr>"

    kill_li = "".join(f"<li style='color:#c0392b;'>{s}</li>" for s in data["kill_signals"])
    cont_li = "".join(f"<li style='color:#2a9d2a;'>{s}</li>" for s in data["continue_signals"])

    week_end = datetime.now().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: Arial, sans-serif; max-width: 680px; margin: auto; color: #222; font-size: 14px; }}
  h1 {{ font-size: 18px; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 14px; color: #555; margin-top: 24px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .verdict {{ font-size: 22px; font-weight: bold; color: {verdict_color}; padding: 12px 16px; border: 2px solid {verdict_color}; border-radius: 6px; display: inline-block; margin: 12px 0; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 12px 0; }}
  .stat {{ background: #f5f5f5; border-radius: 6px; padding: 12px; text-align: center; }}
  .stat-val {{ font-size: 24px; font-weight: bold; }}
  .stat-label {{ font-size: 11px; color: #888; text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 6px 8px; background: #f0f0f0; font-size: 11px; text-transform: uppercase; color: #666; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  ul {{ margin: 6px 0; padding-left: 18px; }}
  li {{ margin-bottom: 4px; line-height: 1.5; }}
  .footer {{ font-size: 11px; color: #aaa; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>
<h1>Ghost Scanner — Weekly Report</h1>
<p style="color:#888; font-size:13px;">Week ending {week_end} &nbsp;|&nbsp; St. Leon / Cincinnati corridor</p>

<h2>Verdict</h2>
<div class="verdict">{data["verdict"]}</div>

<h2>Week at a Glance</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{data["total_scanned"]}</div><div class="stat-label">Items Scanned</div></div>
  <div class="stat"><div class="stat-val">{data["review_count"]}</div><div class="stat-label">High Signal</div></div>
  <div class="stat"><div class="stat-val">${data["max_spread"]:,.0f}</div><div class="stat-label">Best Spread</div></div>
</div>

<h2>Top 5 Opportunities This Week</h2>
<table>
  <thead><tr><th>#</th><th>Item</th><th>Ask</th><th>Spread</th><th>Score</th></tr></thead>
  <tbody>{top5_rows}</tbody>
</table>

<h2>Category Breakdown</h2>
<table>
  <thead><tr><th>Category</th><th>Items Found</th><th>Avg Spread</th></tr></thead>
  <tbody>{cat_rows}</tbody>
</table>

<h2>Signals</h2>
<ul>{cont_li}</ul>
<ul>{kill_li}</ul>

<div class="footer">Ghost Scanner v1 &nbsp;|&nbsp; Auto-generated Sunday report &nbsp;|&nbsp; {week_end}</div>
</body>
</html>"""


def send_weekly_report():
    to_email = os.getenv("DIGEST_TO_EMAIL")
    from_email = os.getenv("DIGEST_FROM_EMAIL")
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not all([to_email, from_email, app_password]):
        print("[Weekly] Email env vars not set.")
        return

    data = analyze_week()
    html = build_weekly_email(data)

    subject = f"Ghost Scanner Weekly — {data['verdict']} | {data['review_count']} High Signal | Best Spread ${data['max_spread']:,.0f}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_email, app_password)
        server.sendmail(from_email, to_email, msg.as_string())

    print(f"[Weekly] Sent — verdict: {data['verdict']}, best spread: ${data['max_spread']:,.0f}")


if __name__ == "__main__":
    send_weekly_report()
