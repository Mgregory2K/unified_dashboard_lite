import sqlite3
import json
from datetime import datetime
from models import RawItem, Opportunity

DB_PATH = "data/scanner.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            source_item_id TEXT,
            url TEXT UNIQUE,
            title TEXT,
            description TEXT,
            price REAL,
            location TEXT,
            posted_at TEXT,
            scraped_at TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_item_id INTEGER,
            category TEXT,
            market TEXT,
            asking_price REAL,
            estimated_low REAL,
            estimated_high REAL,
            spread_low REAL,
            spread_high REAL,
            score INTEGER,
            risk_flags TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(raw_item_id) REFERENCES raw_items(id)
        );

        CREATE TABLE IF NOT EXISTS review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER,
            decision TEXT,
            reason TEXT,
            next_action TEXT,
            reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
        );
    """)
    conn.commit()
    conn.close()


def item_exists(url: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT id FROM raw_items WHERE url = ?", (url,)).fetchone()
    conn.close()
    return row is not None


def insert_raw_item(item: RawItem) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO raw_items
        (source, source_item_id, url, title, description, price, location, posted_at, scraped_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item.source, item.source_item_id, item.url,
        item.title, item.description, item.price, item.location,
        item.posted_at.isoformat() if item.posted_at else None,
        item.scraped_at.isoformat(), item.raw_json
    ))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def insert_opportunity(opp: Opportunity):
    conn = get_conn()
    conn.execute("""
        INSERT INTO opportunities
        (raw_item_id, category, market, asking_price, estimated_low, estimated_high,
         spread_low, spread_high, score, risk_flags, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        opp.raw_item_id, opp.category, opp.market,
        opp.asking_price, opp.estimated_low, opp.estimated_high,
        opp.spread_low, opp.spread_high, opp.score, opp.risk_flags, opp.status
    ))
    conn.commit()
    conn.close()


def get_opportunities_for_digest(min_score=45, limit=20):
    conn = get_conn()
    rows = conn.execute("""
        SELECT o.*, r.title, r.url, r.location, r.description
        FROM opportunities o
        JOIN raw_items r ON o.raw_item_id = r.id
        WHERE o.status IN ('new', 'review', 'watch')
        AND o.score >= ?
        ORDER BY o.score DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_csv(path="data/exports/opportunities.csv"):
    import csv
    conn = get_conn()
    rows = conn.execute("""
        SELECT o.*, r.title, r.url, r.location
        FROM opportunities o
        JOIN raw_items r ON o.raw_item_id = r.id
        ORDER BY o.score DESC
    """).fetchall()
    conn.close()
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
