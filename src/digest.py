import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from storage import get_opportunities_for_digest
from jinja2 import Template

DIGEST_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: Arial, sans-serif; max-width: 700px; margin: auto; color: #222; }
  h1 { font-size: 18px; border-bottom: 2px solid #333; padding-bottom: 8px; }
  h2 { font-size: 14px; color: #555; margin-top: 24px; }
  .item { border: 1px solid #ddd; border-radius: 6px; padding: 14px; margin-bottom: 14px; }
  .item.review { border-left: 4px solid #2a9d2a; }
  .item.watch { border-left: 4px solid #e6a817; }
  .score { font-size: 22px; font-weight: bold; float: right; color: #333; }
  .label { font-size: 11px; color: #888; text-transform: uppercase; }
  .val { font-size: 14px; margin-bottom: 4px; }
  .spread { color: #2a9d2a; font-weight: bold; }
  .flags { font-size: 11px; color: #c0392b; margin-top: 6px; }
  a { color: #1a73e8; }
  .footer { font-size: 11px; color: #aaa; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px; }
</style>
</head>
<body>
<h1>Ghost Scanner — Daily Digest</h1>
<p style="color:#555; font-size:13px;">{{ date }} &nbsp;|&nbsp; {{ total }} opportunities &nbsp;|&nbsp; {{ high }} high signal</p>

{% if review_items %}
<h2>🟢 HIGH SIGNAL — REVIEW</h2>
{% for item in review_items %}
<div class="item review">
  <span class="score">{{ item.score }}</span>
  <div class="val"><strong><a href="{{ item.url }}">{{ item.title }}</a></strong></div>
  <div class="label">Location</div><div class="val">{{ item.location }}</div>
  <div class="label">Category</div><div class="val">{{ item.category }}</div>
  <div class="label">Ask</div><div class="val">${{ "%.0f"|format(item.asking_price) }}</div>
  <div class="label">Est. Resale</div><div class="val">${{ "%.0f"|format(item.estimated_low) }} – ${{ "%.0f"|format(item.estimated_high) }}</div>
  <div class="label">Possible Spread</div><div class="val spread">${{ "%.0f"|format(item.spread_low) }} – ${{ "%.0f"|format(item.spread_high) }}</div>
  {% if item.risk_flags %}<div class="flags">⚠ {{ item.risk_flags }}</div>{% endif %}
</div>
{% endfor %}
{% endif %}

{% if watch_items %}
<h2>🟡 WATCHLIST</h2>
{% for item in watch_items %}
<div class="item watch">
  <span class="score">{{ item.score }}</span>
  <div class="val"><strong><a href="{{ item.url }}">{{ item.title }}</a></strong></div>
  <div class="label">Ask</div><div class="val">${{ "%.0f"|format(item.asking_price) }}</div>
  <div class="label">Spread Est.</div><div class="val">${{ "%.0f"|format(item.spread_low) }} – ${{ "%.0f"|format(item.spread_high) }}</div>
  {% if item.risk_flags %}<div class="flags">⚠ {{ item.risk_flags }}</div>{% endif %}
</div>
{% endfor %}
{% endif %}

<div class="footer">Ghost Scanner v1 &nbsp;|&nbsp; Cincinnati Market &nbsp;|&nbsp; {{ date }}</div>
</body>
</html>
"""


def build_digest():
    rows = get_opportunities_for_digest()
    review_items = [r for r in rows if r["status"] == "review"]
    watch_items = [r for r in rows if r["status"] == "watch"]

    tmpl = Template(DIGEST_TEMPLATE)
    html = tmpl.render(
        date=datetime.now().strftime("%B %d, %Y"),
        total=len(rows),
        high=len(review_items),
        review_items=review_items,
        watch_items=watch_items,
    )
    return html, len(review_items), len(rows)


def send_digest(to_email: str, from_email: str, app_password: str):
    html, high, total = build_digest()
    if total == 0:
        print("[Digest] Nothing to send today.")
        return

    subject = f"Ghost Scanner Daily Digest — {total} Opportunities / {high} High Signal"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_email, app_password)
        server.sendmail(from_email, to_email, msg.as_string())

    print(f"[Digest] Sent — {total} items, {high} high signal")
