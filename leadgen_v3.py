"""
leadgen_v3.py — Green Gregory Group Opportunity OS
Phase 3: Config-driven lead generation — reads markets and profiles from config.json

Usage:
    python leadgen_v3.py
    python leadgen_v3.py --force-osm --zips 47226 47025 45219 --limit 10 --dry-run
    python leadgen_v3.py --fallback
    python leadgen_v3.py --output leads.csv
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
from datetime import datetime, timezone
from pathlib import Path

from config_reader import load_config

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("leadgen")

HEADERS = {
    "User-Agent": (
        "GreenGregoryOS/2.0 LeadGen "
        "(github.com/Mgregory2K/unified_dashboard_lite)"
    )
}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

LEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    address      TEXT,
    city         TEXT,
    state        TEXT,
    zip          TEXT,
    phone        TEXT,
    website      TEXT,
    rating       REAL,
    review_count INTEGER,
    role         TEXT,
    category     TEXT,
    source_zip   TEXT,
    notes        TEXT,
    status       TEXT DEFAULT 'new',
    created_at   TEXT
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


def make_lead_id(name, key):
    return hashlib.sha256(f"{name.lower()}|{key.lower()}".encode()).hexdigest()[:12]


# ── Google Places ─────────────────────────────────────────────────────────────

def search_places(query, lat, lng, api_key, radius=16000, limit=10):
    url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "location": f"{lat},{lng}", "radius": radius, "key": api_key}
    try:
        r    = requests.get(url, params=params, timeout=12)
        data = r.json()
        status = data.get("status","")
        if status == "REQUEST_DENIED":
            log.warning(f"    Places API REQUEST_DENIED: {data.get('error_message','')}")
            return [], True
        if status not in ("OK","ZERO_RESULTS"):
            log.warning(f"    Places API status: {status}")
        return data.get("results",[])[:limit], False
    except Exception as e:
        log.error(f"    Places API error: {e}")
        return [], False


def parse_places_result(result, role, category, source_zip):
    address = result.get("formatted_address","")
    parts   = address.split(",")
    city    = parts[1].strip() if len(parts) > 1 else ""
    sz      = parts[2].strip() if len(parts) > 2 else ""
    return {
        "id":           make_lead_id(result.get("name",""), address),
        "name":         result.get("name","")[:200],
        "address":      address[:300],
        "city":         city,
        "state":        sz[:2] if sz else "",
        "zip":          sz[3:8].strip() if len(sz) > 3 else "",
        "phone":        "",
        "website":      "",
        "rating":       result.get("rating"),
        "review_count": result.get("user_ratings_total"),
        "role":         role,
        "category":     category,
        "source_zip":   source_zip,
        "notes":        "",
        "status":       "new",
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }


# ── Overpass ──────────────────────────────────────────────────────────────────

def build_overpass_query(lat, lng, osm_key, osm_val, radius=16000):
    return f"""[out:json][timeout:30];
(
  node["{osm_key}"="{osm_val}"](around:{radius},{lat},{lng});
  way["{osm_key}"="{osm_val}"](around:{radius},{lat},{lng});
);
out body;
"""


def search_overpass(lat, lng, osm_key, osm_val, radius=16000, limit=10):
    query = build_overpass_query(lat, lng, osm_key, osm_val, radius)
    for endpoint in OVERPASS_ENDPOINTS:
        log.info(f"    Overpass: {endpoint}")
        try:
            r = requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=35)
            if r.status_code != 200:
                log.warning(f"    HTTP {r.status_code}. Preview: {r.text[:300]!r}")
                time.sleep(1)
                continue
            elements = r.json().get("elements", [])
            log.info(f"    {len(elements)} OSM elements")
            return elements[:limit]
        except requests.exceptions.JSONDecodeError as e:
            log.warning(f"    JSON error: {e}")
        except requests.RequestException as e:
            log.warning(f"    Request error: {e}")
        time.sleep(1)
    log.error("    All Overpass endpoints failed.")
    return []


def parse_overpass_result(el, role, category, source_zip):
    tags = el.get("tags", {})
    name = tags.get("name","").strip()
    if not name:
        return None
    address = " ".join(filter(None, [
        tags.get("addr:housenumber",""), tags.get("addr:street","")
    ]))
    return {
        "id":           make_lead_id(name, address + tags.get("addr:city","")),
        "name":         name[:200],
        "address":      address[:300],
        "city":         tags.get("addr:city",""),
        "state":        tags.get("addr:state",""),
        "zip":          tags.get("addr:postcode",""),
        "phone":        tags.get("phone", tags.get("contact:phone","")),
        "website":      tags.get("website", tags.get("contact:website","")),
        "rating":       None,
        "review_count": None,
        "role":         role,
        "category":     category,
        "source_zip":   source_zip,
        "notes":        "",
        "status":       "new",
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }


def upsert_lead(conn, lead, dry_run=False):
    if conn.execute("SELECT id FROM leads WHERE id = ?", (lead["id"],)).fetchone():
        return False
    if not dry_run:
        conn.execute("""
            INSERT INTO leads
                (id, name, address, city, state, zip, phone, website,
                 rating, review_count, role, category, source_zip,
                 notes, status, created_at)
            VALUES
                (:id, :name, :address, :city, :state, :zip, :phone, :website,
                 :rating, :review_count, :role, :category, :source_zip,
                 :notes, :status, :created_at)
        """, lead)
    return True


def export_csv(conn, output_path):
    rows = conn.execute("SELECT * FROM leads ORDER BY role, category, name").fetchall()
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id","name","address","city","state","zip","phone","website",
            "rating","review_count","role","category","source_zip",
            "notes","status","created_at"
        ])
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    log.info(f"Exported {len(rows)} leads to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Green Gregory leadgen v3")
    parser.add_argument("--zips",       nargs="+", default=None,
                        help="Override zip codes from config")
    parser.add_argument("--role",       choices=["buyer","supplier","all"], default="all")
    parser.add_argument("--output",     help="Export leads to CSV path")
    parser.add_argument("--radius",     type=int, default=None)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--force-osm",  action="store_true")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--fallback",   action="store_true")
    args = parser.parse_args()

    cfg      = load_config()
    settings = cfg["settings"]
    radius   = args.radius  or settings.get("scan_radius_meters", 16000)
    limit    = args.limit   or settings.get("leadgen_limit_per_query", 10)

    # Markets
    markets = cfg["markets"]
    if args.zips:
        markets = [m for m in markets if m["zip"] in args.zips]
        # allow ad-hoc zips not in config
        cfg_zips = {m["zip"] for m in cfg["markets"]}
        for z in args.zips:
            if z not in cfg_zips:
                log.warning(f"Zip {z} not in config markets — skipping. Add it to the Markets tab.")

    active_markets = [m for m in markets if m.get("active", True)]

    # Leadgen profiles
    profiles = cfg["leadgen_profiles"]
    if args.role != "all":
        profiles = [p for p in profiles if p["role"] == args.role]
    active_profiles = [p for p in profiles if p.get("active", True)]

    api_key  = "" if args.force_osm else os.environ.get("GOOGLE_PLACES_API_KEY","").strip()
    use_osm  = args.force_osm or not api_key
    conn     = get_db()
    total    = 0

    log.info(f"Mode: {'OSM/Overpass' if use_osm else 'Google Places'}")
    log.info(f"Markets: {len(active_markets)}  Profiles: {len(active_profiles)}")

    for market in active_markets:
        lat, lng = market["lat"], market["lng"]
        log.info(f"── {market['zip']} {market['label']}  ({lat:.4f}, {lng:.4f}) ──")

        for profile in active_profiles:
            log.info(f"  [{profile['role']}] {profile['query']}")

            if not use_osm:
                results, denied = search_places(profile["query"], lat, lng, api_key, radius, limit)
                if denied:
                    log.info("  REQUEST_DENIED — switching to OSM")
                    use_osm = True
                else:
                    for r in results:
                        lead = parse_places_result(r, profile["role"], profile["category"], market["zip"])
                        if upsert_lead(conn, lead, args.dry_run):
                            total += 1
                            if args.dry_run:
                                log.info(f"    DRY [{profile['role']}] {lead['name'][:50]} — {lead['city']}")
                    time.sleep(0.2)
                    continue

            # OSM
            elements = search_overpass(
                lat, lng, profile["osm_key"], profile["osm_val"], radius, limit
            )
            for el in elements:
                lead = parse_overpass_result(el, profile["role"], profile["category"], market["zip"])
                if lead and upsert_lead(conn, lead, args.dry_run):
                    total += 1
                    if args.dry_run:
                        log.info(f"    DRY [{profile['role']}] {lead['name'][:50]} — {lead['city']}")
            time.sleep(0.5)

        if not args.dry_run:
            conn.commit()

    counts = conn.execute("SELECT role, COUNT(*) n FROM leads GROUP BY role").fetchall()
    log.info(f"=== Leadgen complete — {total} new leads this run ===")
    for r in counts:
        log.info(f"  {r['role']:10}: {r['n']} total in DB")

    if args.output and not args.dry_run:
        export_csv(conn, args.output)
    conn.close()


if __name__ == "__main__":
    main()
