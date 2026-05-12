# Green Gregory Group Opportunity OS — Phase 2 Setup

## What's in this phase

| File | Purpose |
|------|---------|
| `scanner.py` | Pulls live listings from GovDeals, PublicSurplus, Craigslist RSS |
| `matcher.py` | Scores supply listings against buyer demand profiles |
| `alert_emailer.py` | Sends hot match digest email via Gmail |
| `sheets_sync.py` | Syncs pipeline + P&L to Google Sheets |
| `leadgen.py` | Lead gen for suppliers/buyers (replaces leadgen_places_45219.py) |
| `.github/workflows/scanner_schedule.yml` | GitHub Actions 12-hour schedule |

---

## Quick start (local test)

```bash
pip install feedparser requests beautifulsoup4 gspread google-auth

# Test scanner (dry run — no DB writes)
python scanner.py --dry-run

# Run full scan
python scanner.py

# Run matcher
python matcher.py

# Print hot board only
python matcher.py --board

# Test email (dry run)
python alert_emailer.py --dry-run

# Run lead gen (OSM fallback, no API key needed)
python leadgen.py --zips 47226 47025 45219
```

---

## GitHub Actions setup

### 1. Add secrets to your repo

Go to: `github.com/Mgregory2K/unified_dashboard_lite → Settings → Secrets → Actions`

| Secret | Value |
|--------|-------|
| `GGG_EMAIL_USER` | Your Gmail address |
| `GGG_EMAIL_PASS` | Gmail App Password (NOT your real password) |
| `GGG_EMAIL_TO` | Where to send alerts (can be same as USER) |
| `GGG_SPREADSHEET_ID` | ID from your Google Sheet URL |
| `GOOGLE_CREDENTIALS_JSON` | Full JSON content of service account key |

### 2. Gmail App Password
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. 2FA must be enabled on your Google account
3. Create app password → "Mail" → "Other" → name it "GGG OS"
4. Copy the 16-char password → use as `GGG_EMAIL_PASS`

### 3. Google Sheets service account
1. [console.cloud.google.com](https://console.cloud.google.com) → New project
2. Enable **Google Sheets API** and **Google Drive API**
3. IAM & Admin → Service Accounts → Create
4. Create Key → JSON → download
5. Copy the entire JSON content → paste as `GOOGLE_CREDENTIALS_JSON` secret
6. In your Google Sheet → Share → paste the service account email (ends in `@...iam.gserviceaccount.com`) → Editor

### 4. Deploy the workflow
```bash
mkdir -p .github/workflows
cp scanner_schedule.yml .github/workflows/
git add .github/workflows/scanner_schedule.yml scanner.py matcher.py \
        alert_emailer.py sheets_sync.py leadgen.py
git commit -m "feat: Phase 2 — scanner, matcher, alerts, sheets sync"
git push
```

The workflow runs at **6 AM and 6 PM UTC** (1 AM / 1 PM Central).
You can also trigger it manually from the Actions tab.

---

## Customization

### Add Craigslist markets
In `scanner.py`, add to `CRAIGSLIST_SEARCHES`:
```python
("chicago",    "sss", "network equipment"),
("columbus",   "bfs", "surplus lot"),
```

### Add a buyer profile
In `matcher.py`, add to `BUYER_PROFILES`:
```python
{
    "id": "buyer_jane_smith",
    "buyer_name": "Jane Smith — IT Refurb LLC",
    "keywords": ["cisco", "HP ProLiant", "server"],
    "max_buy_price": 2000,
    "target_margin": 0.45,
    "score_weight": 25,
    "notes": "Pays fast, picks up. Call before 3 PM.",
},
```

### Raise alert threshold
In `scanner_schedule.yml` or locally:
```bash
python alert_emailer.py --min-score 60
```

### Add lead gen zip codes
In `leadgen.py`, add to `ZIP_COORDS`:
```python
"46040": (40.0120, -85.9680),  # Fishers IN
```

---

## DB schema reference

```
raw_listings  — supply side (scanner output)
matches       — scored supply↔buyer pairs (matcher output)
leads         — business contacts (leadgen output)
```

All three tables live in `opportunity_os.db` (SQLite).
The DB persists between GitHub Actions runs via the cache action.
A backup copy uploads as an artifact after every run (30-day retention).

---

*Green Gregory Group LLC · St. Leon, IN · Ghost Arbitrage OS v2*
