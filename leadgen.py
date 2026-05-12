"""
leadgen.py — Green Gregory Group Opportunity OS
Phase 2: Improved lead generation script (replaces leadgen_places_45219.py)

Finds businesses in target zip codes that:
  - Buy/sell used equipment
  - Could be supply sources (liquidators, surplus dealers)
  - Could be demand buyers (IT resellers, industrial dealers)

Uses Google Places API (free tier) or falls back to Overpass (OSM) if no key.
Outputs to SQLite leads table AND optionally CSV.

Usage:
    python leadgen.py                        # default zip: 45219 + nearby
    python leadgen.py --zips 47226 47025     # specific zips
    python leadgen.py --output leads.csv     # also export CSV
    python leadgen.py --role buyer           # only buyer-type businesses
    python leadgen.py --role supplier        # only supplier-type businesses

Environment:
    GOOGLE_PLACES_API_KEY  — Optional. Without it, uses Overpass (OSM) fallback.
"""

import sqlite3
import requests
import csv
import json
import os
import time
import argparse
import logging
import hashlib
from datetime import datetime
from pathlib import Path

DB_PATH = Path("opportunity_os.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("leadgen")

# ── Target areas ──────────────────────────────────────────────────────────────
# Covers Cincinnati/Indy corridor + St. Leon area
DEFAULT_ZIPS = [
    "47226",  # Batesville IN (home base area)
    "47025",  # Lawrenceburg IN
    "45219",  # Cincinnati OH (original)
    "45202",  # Cincinnati downtown
    "47374",  # Richmond IN
    "46201",  # Indianapolis east
    "46107",  # Indianapolis south
]

# Map zip to approximate lat/lng center for Places API
ZIP_COORDS = {
    "47226": (39.3003, -85.4852),
    "47025": (39.0920, -84.8571),
    "45219": (39.1322, -84.5158),
    "45202": (39.1031, -84.5120),
    "47374": (39.8286, -84.8901),
    "46201": (39.7684, -86.1099),
    "46107": (39.6870, -86.0916),
}

# ── Search categories ─────────────────────────────────────────────────────────
# Each entry: (search_query, role, category_label)
SEARCH_TARGETS = [
    # Supply sources — where you buy
    ("surplus equipment dealer",    "supplier", "surplus_dealer"),
    ("liquidation warehouse",        "supplier", "liquidator"),
    ("auction house equipment",      "supplier", "auction"),
    ("pallet liquidator",            "supplier", "liquidator"),
    ("used IT equipment",            "supplier", "it_reseller"),
    ("used network equipment",       "supplier", "it_reseller"),
    ("industrial equipment dealer",  "supplier", "industrial"),
    # Demand buyers — who you sell to
    ("IT equipment buyer",           "buyer",    "it_buyer"),
    ("electronics recycler",         "buyer",    "recycler"),
    ("used equipment reseller",      "buyer",    "reseller"),
    ("eBay reseller store",          "buyer",    "ebay_reseller"),
    ("computer resale shop",         "buyer",    "it_buyer"),
    ("network hardware reseller",    "buyer",    "it_buyer"),
    ("industrial machinery dealer",  "buyer",    "industrial_buyer"),
]

# ── DB setup ──────────────────────────────────────────────────────────────────

LEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    address     TEXT,
    city        TEXT,
    state       TEXT,
    zip         TEXT,
    phone       TEXT,
    website     TEXT,
    rating      REAL,
    review_count INTEGER,
    role        TEXT,  -- supplier | buyer | both
    category    TEXT,
    source_zip  TEXT,
    notes       TEXT,
    status      TEXT DEFAULT 'new',  -- new | contacted | qualified | passed
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_role   ON leads(role);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(LEADS_SCHEMA)
    conn.commit()
    return conn


def make_lead_id(name: str, address: str) -> str:
    return hashlib.sha256(f"{name}|{address}".lower().encode()).hexdigest()[:12]


# ── Google Places API ─────────────────────────────────────────────────────────

def search_places_api(query: str, lat: float, lng: float, api_key: str,
                      radius: int = 16000) -> list[dict]:
    """Search nearby places via Google Places Text Search."""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query":    query,
        "location": f"{lat},{lng}",
        "radius":   radius,
        "key":      api_key,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log.warning(f"Places API status: {data.get('status')} — {data.get('error_message','')}")
        return data.get("results", [])
    except Exception as e:
        log.error(f"Places API error: {e}")
        return []


def parse_places_result(result: dict, role: str, category: str,
                         source_zip: str) -> dict:
    address = result.get("formatted_address", "")
    parts   = address.split(",")
    city    = parts[1].strip() if len(parts) > 1 else ""
    state_zip = parts[2].strip() if len(parts) > 2 else ""
    state   = state_zip[:2] if state_zip else ""
    zip_    = state_zip[3:8] if len(state_zip) > 3 else ""

    return {
        "id":           make_lead_id(result.get("name", ""), address),
        "name":         result.get("name", "")[:200],
        "address":      address[:300],
        "city":         city,
        "state":        state,
        "zip":          zip_,
        "phone":        "",  # Requires Place Details API call
        "website":      "",
        "rating":       result.get("rating"),
        "review_count": result.get("user_ratings_total"),
        "role":         role,
        "category":     category,
        "source_zip":   source_zip,
        "notes":        "",
        "status":       "new",
        "created_at":   datetime.utcnow().isoformat(),
    }


# ── Overpass (OSM) fallback ───────────────────────────────────────────────────

OSM_TAGS_BY_CATEGORY = {
    "surplus_dealer":  [("shop", "second_hand"), ("shop", "wholesale")],
    "liquidator":      [("shop", "second_hand")],
    "auction":         [("amenity", "marketplace")],
    "it_reseller":     [("shop", "electronics"), ("shop", "computer")],
    "industrial":      [("shop", "hardware"), ("industrial", "warehouse")],
    "it_buyer":        [("shop", "electronics"), ("shop", "computer")],
    "recycler":        [("amenity", "recycling")],
    "reseller":        [("shop", "second_hand")],
    "ebay_reseller":   [("shop", "second_hand")],
    "industrial_buyer":[("shop", "hardware")],
}


def search_overpass(lat: float, lng: float, category: str,
                    radius: int = 16000) -> list[dict]:
    """OSM Overpass query for businesses near coordinates."""
    tags = OSM_TAGS_BY_CATEGORY.get(category, [("shop", "wholesale")])
    results = []

    for key, val in tags:
        query = f"""
        [out:json][timeout:25];
        (
          node["{key}"="{val}"](around:{radius},{lat},{lng});
          way["{key}"="{val}"](around:{radius},{lat},{lng});
        );
        out body;
        """
        try:
            r = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
                timeout=30,
            )
            r.raise_for_status()
            elements = r.json().get("elements", [])
            results.extend(elements)
            time.sleep(1)  # Be polite to Overpass
        except Exception as e:
            log.warning(f"Overpass error ({key}={val}): {e}")

    return results


def parse_overpass_result(el: dict, role: str, category: str,
                           source_zip: str) -> dict | None:
    tags = el.get("tags", {})
    name = tags.get("name", "")
    if not name:
        return None

    address = " ".join(filter(None, [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]))
    city    = tags.get("addr:city", "")
    state   = tags.get("addr:state", "")
    zip_    = tags.get("addr:postcode", "")
    phone   = tags.get("phone", tags.get("contact:phone", ""))
    website = tags.get("website", tags.get("contact:website", ""))

    return {
        "id":           make_lead_id(name, address + city),
        "name":         name[:200],
        "address":      address[:300],
        "city":         city,
        "state":        state,
        "zip":          zip_,
        "phone":        phone,
        "website":      website,
        "rating":       None,
        "review_count": None,
        "role":         role,
        "category":     category,
        "source_zip":   source_zip,
        "notes":        "",
        "status":       "new",
        "created_at":   datetime.utcnow().isoformat(),
    }


# ── DB insert ─────────────────────────────────────────────────────────────────

def upsert_lead(conn, lead: dict) -> bool:
    existing = conn.execute(
        "SELECT id FROM leads WHERE id = ?", (lead["id"],)
    ).fetchone()
    if existing:
        return False
    conn.execute("""
        INSERT INTO leads
            (id, name, address, city, state, zip, phone, website,
             rating, review_count, role, category, source_zip, notes, status, created_at)
        VALUES
            (:id, :name, :address, :city, :state, :zip, :phone, :website,
             :rating, :review_count, :role, :category, :source_zip, :notes, :status, :created_at)
    """, lead)
    return True


# ── Export ─────────────────────────────────────────────────────────────────────

def export_csv(conn, output_path: str):
    rows = conn.execute("SELECT * FROM leads ORDER BY role, category, name").fetchall()
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "name", "address", "city", "state", "zip", "phone", "website",
            "rating", "review_count", "role", "category", "source_zip", "notes",
            "status", "created_at"
        ])
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    log.info(f"Exported {len(rows)} leads to {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory lead gen")
    parser.add_argument("--zips", nargs="+", default=DEFAULT_ZIPS)
    parser.add_argument("--role", choices=["buyer", "supplier", "all"], default="all")
    parser.add_argument("--output", help="Also export to CSV path")
    parser.add_argument("--radius", type=int, default=16000, help="Search radius in meters")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if api_key:
        log.info("Using Google Places API")
    else:
        log.info("No GOOGLE_PLACES_API_KEY — using OpenStreetMap / Overpass fallback")

    conn    = get_db()
    total   = 0
    targets = [t for t in SEARCH_TARGETS
               if args.role == "all" or t[1] == args.role]

    for zip_code in args.zips:
        coords = ZIP_COORDS.get(zip_code)
        if not coords:
            log.warning(f"No coords for zip {zip_code} — add to ZIP_COORDS dict")
            continue

        lat, lng = coords
        log.info(f"── Searching zip {zip_code} ({lat:.4f}, {lng:.4f}) ──")

        for query, role, category in targets:
            log.info(f"  [{role}] {query}")

            if api_key:
                results = search_places_api(query, lat, lng, api_key, args.radius)
                for r in results:
                    lead = parse_places_result(r, role, category, zip_code)
                    if upsert_lead(conn, lead):
                        total += 1
                time.sleep(0.2)
            else:
                elements = search_overpass(lat, lng, category, args.radius)
                for el in elements:
                    lead = parse_overpass_result(el, role, category, zip_code)
                    if lead and upsert_lead(conn, lead):
                        total += 1

        conn.commit()

    # Summary
    counts = conn.execute(
        "SELECT role, COUNT(*) as n FROM leads GROUP BY role"
    ).fetchall()
    log.info(f"=== Lead gen complete — {total} new leads ===")
    for r in counts:
        log.info(f"  {r['role']:10}: {r['n']} total leads in DB")

    if args.output:
        export_csv(conn, args.output)

    conn.close()


if __name__ == "__main__":
    main()
