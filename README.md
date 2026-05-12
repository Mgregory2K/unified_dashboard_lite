# Ghost Arbitrage Scanner — v1

Scans GovDeals, PublicSurplus, and Craigslist RSS for underpriced commercial assets.
Scores every item. Sends a daily email digest ranked by spread and urgency.
You review. You decide. You take the position.

---

## Setup

### 1. Install Python 3.10+
https://python.org

### 2. Install dependencies
```
pip install -r requirements.txt
```

### 3. Configure email
```
cp .env.template .env
```
Edit .env with your Gmail address and App Password.
Generate App Password: https://myaccount.google.com/apppasswords

### 4. Create data directories
```
mkdir -p data/exports data/logs
```

---

## Run

### One-time scan + digest
```
python src/main.py --market cincinnati --digest
```

### Scan only (no email)
```
python src/main.py --market cincinnati
```

### Run on daily schedule (7am)
```
python src/main.py --market cincinnati --schedule --hour 7
```

### Expand to new market
Edit config/markets.yaml — set active: true for the market you want.
Then run:
```
python src/main.py --market dayton --digest
```

---

## Output

- SQLite DB: data/scanner.db
- CSV export: data/exports/opportunities.csv
- Daily email digest with scored opportunities

---

## Opportunity Statuses

| Status    | Meaning                        |
|-----------|-------------------------------|
| review    | Score 65+ — act on this       |
| watch     | Score 45–64 — monitor         |
| pass      | Below threshold — ignored     |
| contacted | You reached out to seller     |
| validated | Confirmed deal viable         |
| closed    | Deal done                     |
| dead      | Fell through                  |

---

## Kill Criteria (stop wasting time if after 2-3 weeks)

- No item over $300 possible spread
- Too many false positives to manage
- No repeatable category emerging
- Too much manual verification needed
- Sources breaking faster than you fix them

---

## Tuning

All scoring weights live in config/scoring.yaml.
All categories and keywords live in config/sources.yaml and config/keywords.yaml.
Adjust multipliers after you see real comps on eBay/Facebook.

---

## Week 2 Additions (when v1 is stable)

- eBay sold listings comp lookup
- Better confidence scoring per category
- Watchlist auto-expiry
- Notion/Airtable sync

---

Built for: Cincinnati market, commercial equipment, ghost arbitrage.
Stack: Python, SQLite, Gmail SMTP, Jinja2.
