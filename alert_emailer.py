"""
alert_emailer.py — Green Gregory Group Opportunity OS
Phase 2: Hot match email digest via Gmail SMTP

Reads unalerted hot matches from DB → builds HTML digest → sends email.
Marks alerted rows so they don't re-send next run.

Environment variables (set in GitHub Actions secrets or .env):
    GGG_EMAIL_USER     — Gmail address (e.g. michael@gmail.com)
    GGG_EMAIL_PASS     — Gmail App Password (NOT your real password)
    GGG_EMAIL_TO       — Recipient(s), comma-separated
    GGG_EMAIL_FROM     — From display name/address (optional, defaults to USER)

Gmail App Password: https://myaccount.google.com/apppasswords
    (Requires 2FA enabled on the account)

Usage:
    python alert_emailer.py
    python alert_emailer.py --min-score 60   # only really hot ones
    python alert_emailer.py --dry-run        # print email, don't send
"""

import sqlite3
import smtplib
import json
import os
import argparse
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("emailer")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Add alerted_at column if it doesn't exist (idempotent migration)
    conn.execute("""
        ALTER TABLE matches ADD COLUMN alerted_at TEXT
    """) if not _column_exists(conn, "matches", "alerted_at") else None
    conn.commit()
    return conn


def _column_exists(conn, table, col):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols


def get_hot_unalerted(conn, min_score: int) -> list:
    return conn.execute("""
        SELECT m.id AS match_id, m.score, m.buyer_name, m.price_est,
               m.profit_est, m.margin_est, m.matched_keywords, m.notes,
               r.title, r.url, r.source, r.location, r.end_date,
               r.scraped_at
        FROM matches m
        JOIN raw_listings r ON r.id = m.listing_id
        WHERE m.status = 'hot'
          AND m.alerted_at IS NULL
          AND m.score >= ?
        ORDER BY m.score DESC, m.profit_est DESC
    """, (min_score,)).fetchall()


def mark_alerted(conn, match_ids: list[str]):
    now = datetime.utcnow().isoformat()
    conn.executemany(
        "UPDATE matches SET alerted_at = ? WHERE id = ?",
        [(now, mid) for mid in match_ids]
    )
    conn.commit()


# ── Email builder ──────────────────────────────────────────────────────────────

def score_color(score: int) -> str:
    if score >= 80: return "#16a34a"  # green
    if score >= 60: return "#d97706"  # amber
    return "#dc2626"                  # red


def build_html(rows: list, run_ts: str) -> str:
    rows_html = ""
    for r in rows:
        keywords = ", ".join(json.loads(r["matched_keywords"] or "[]"))
        profit   = f"${r['profit_est']:,.0f}" if r["profit_est"] else "—"
        price    = f"${r['price_est']:,.0f}"  if r["price_est"]  else "—"
        margin   = f"{r['margin_est']*100:.0f}%" if r["margin_est"] else "—"
        end      = r["end_date"][:10] if r["end_date"] else "—"
        color    = score_color(r["score"])

        rows_html += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;">
            <span style="display:inline-block;background:{color};color:white;
              border-radius:4px;padding:2px 8px;font-weight:700;font-size:13px;">
              {r['score']}
            </span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;">
            <a href="{r['url']}" style="color:#1d4ed8;text-decoration:none;font-weight:600;">
              {r['title'][:70]}
            </a><br>
            <span style="font-size:12px;color:#6b7280;">
              {r['source'].upper()} · {r['location'] or '—'} · Ends {end}
            </span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;
            font-weight:600;white-space:nowrap;">{price}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;
            color:#16a34a;font-weight:700;white-space:nowrap;">{profit}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;">{margin}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;
            font-size:12px;color:#374151;">{r['buyer_name'][:30]}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;
            font-size:11px;color:#9ca3af;">{keywords[:60]}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:#f9fafb;margin:0;padding:20px;">
  <div style="max-width:900px;margin:0 auto;background:white;border-radius:8px;
    box-shadow:0 1px 3px rgba(0,0,0,.1);overflow:hidden;">

    <!-- Header -->
    <div style="background:#15803d;padding:20px 24px;">
      <h1 style="color:white;margin:0;font-size:20px;font-weight:700;">
        🟢 Green Gregory Group — Hot Opportunity Alert
      </h1>
      <p style="color:#bbf7d0;margin:4px 0 0;font-size:14px;">
        {len(rows)} new match{"es" if len(rows) != 1 else ""} found · {run_ts} UTC · Ghost Arbitrage OS
      </p>
    </div>

    <!-- Table -->
    <div style="overflow-x:auto;padding:0 24px 24px;">
      <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:14px;">
        <thead>
          <tr style="background:#f3f4f6;">
            <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Score</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Listing</th>
            <th style="padding:8px;text-align:right;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Buy</th>
            <th style="padding:8px;text-align:right;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Est Profit</th>
            <th style="padding:8px;text-align:right;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Margin</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Buyer</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em;">Keywords</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="background:#f9fafb;border-top:1px solid #e5e7eb;
      padding:16px 24px;font-size:12px;color:#9ca3af;">
      Green Gregory Group LLC · St. Leon, IN · Ghost Arbitrage OS v2<br>
      Reply to this email to mark deals as reviewed.
    </div>
  </div>
</body>
</html>"""


def build_text(rows: list, run_ts: str) -> str:
    lines = [
        f"GREEN GREGORY GROUP — Hot Opportunity Alert",
        f"{len(rows)} match(es) · {run_ts} UTC",
        "=" * 70,
    ]
    for r in rows:
        profit = f"${r['profit_est']:,.0f}" if r["profit_est"] else "N/A"
        price  = f"${r['price_est']:,.0f}"  if r["price_est"]  else "N/A"
        lines += [
            f"\n[{r['score']}] {r['title'][:70]}",
            f"  Source:  {r['source'].upper()} | {r['location'] or '—'}",
            f"  Buy:     {price}   Est Profit: {profit}",
            f"  Buyer:   {r['buyer_name']}",
            f"  URL:     {r['url']}",
        ]
    lines.append("\n" + "=" * 70)
    lines.append("Green Gregory Group LLC · Ghost Arbitrage OS v2")
    return "\n".join(lines)


# ── Sender ─────────────────────────────────────────────────────────────────────

def send_email(html: str, text: str, subject: str, dry_run: bool = False):
    user     = os.environ.get("GGG_EMAIL_USER", "")
    password = os.environ.get("GGG_EMAIL_PASS", "")
    to_raw   = os.environ.get("GGG_EMAIL_TO", user)
    from_    = os.environ.get("GGG_EMAIL_FROM", f"Green Gregory OS <{user}>")

    if not user or not password:
        log.error("GGG_EMAIL_USER or GGG_EMAIL_PASS not set. Cannot send email.")
        log.info("Set them as env vars or GitHub Actions secrets.")
        return False

    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if dry_run:
        log.info(f"DRY RUN — would send to {recipients}:\n{text[:500]}")
        return True

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, recipients, msg.as_string())
        log.info(f"Email sent to {recipients}")
        return True
    except Exception as e:
        log.error(f"SMTP error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory alert emailer")
    parser.add_argument("--min-score", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_db()
    rows = get_hot_unalerted(conn, args.min_score)

    if not rows:
        log.info("No new hot matches to alert. All clear.")
        conn.close()
        return

    run_ts  = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    subject = f"[GGG] 🔥 {len(rows)} Hot Deals Found — {run_ts}"
    html    = build_html(rows, run_ts)
    text    = build_text(rows, run_ts)

    success = send_email(html, text, subject, dry_run=args.dry_run)

    if success and not args.dry_run:
        match_ids = [r["match_id"] for r in rows]
        mark_alerted(conn, match_ids)
        log.info(f"Marked {len(match_ids)} matches as alerted.")

    conn.close()


if __name__ == "__main__":
    main()
