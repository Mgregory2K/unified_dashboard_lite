"""
leadgen.py — Green Gregory Group Opportunity OS
Phase 2: Lead generation — suppliers + buyers in target markets

Replaces leadgen_places_45219.py. Covers Cincy/Indy corridor + St. Leon area.

Usage:
    python leadgen.py                                  # all zips, auto-detect API/OSM
    python leadgen.py --zips 47226 47025 45219         # specific zips
    python leadgen.py --force-osm                      # skip Places API, use OSM
    python leadgen.py --limit 10                       # max results per query
    python leadgen.py --dry-run                        # print leads, no DB writes
    python leadgen.py --force-osm --zips 47226 47025 45219 --limit 10 --dry-run
    python leadgen.py --output leads.csv               # also export CSV

Environment (optional):
    GOOGLE_PLACES_API_KEY  — if absent or REQUEST_DENIED, falls back to OSM
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

DB_PATH = Path("opportunity_os.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("leadgen")

HEADERS = {
    "User-Agent": (
        "GreenGregoryOS/2.0 LeadGen (research; contact via github.com/Mgregory2K/unified_dashboard_lite)"
    )
}

# ── Overpass endpoints (tried in order on failure) ────────────────────────────
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# ── Target zip codes + coordinates ────────────────────────────────────────────
DEFAULT_ZIPS = [
    "47226",  # Batesville IN
    "47025",  # Lawrenceburg IN
    "45219",  # Cincinnati OH (original)
    "45202",  # Cincinnati downtown
    "47374",  # Richmond IN
    "46201",  # Indianapolis east
    "46107",  # Indianapolis south
]

ZIP_COORDS = {
    "47226": (39.3003, -85.4852),
    "47025": (39.0920, -84.8571),
    "45219": (39.1322, -84.5158),
    "45202": (39.1031, -84.5120),
    "47374": (39.8286, -84.8901),
    "46201": (39.7684, -86.1099),
    "46107": (39.6870, -86.0916),
}

# ── Search targets ─────────────────────────────────────────────────────────────
# (google_query, osm_tags, role, category_label)
SEARCH_TARGETS = [
    # ── Suppliers (where you buy)
    ("surplus equipment dealer",    [("shop","second_hand"),("shop","wholesale")],   "supplier", "surplus_dealer"),
    ("liquidation warehouse",        [("shop","second_hand")],                        "supplier", "liquidator"),
    ("auction house equipment",      [("amenity","marketplace")],                     "supplier", "auction"),
    ("used IT equipment dealer",     [("shop","electronics"),("shop","computer")],    "supplier", "it_reseller"),
    ("used network equipment",       [("shop","electronics")],                        "supplier", "it_reseller"),
    ("industrial equipment dealer",  [("shop","hardware"),("man_made","works")],      "supplier", "industrial"),
    # ── Buyers (who you sell to)
    ("IT equipment buyer reseller",  [("shop","computer"),("shop","electronics")],    "buyer",    "it_buyer"),
    ("electronics recycler",         [("amenity","recycling")],                       "buyer",    "recycler"),
    ("used equipment reseller",      [("shop","second_hand")],                        "buyer",    "reseller"),
    ("computer repair resale shop",  [("shop","computer")],                           "buyer",    "it_buyer"),
    ("industrial machinery dealer",  [("shop","hardware")],                           "buyer",    "industrial_buyer"),
]

# ── DB setup ──────────────────────────────────────────────────────────────────

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


def make_lead_id(name: str, key: str) -> str:
    return hashlib.sha256(f"{name.lower()}|{key.lower()}".encode()).hexdigest()[:12]


# ── Google Places API ─────────────────────────────────────────────────────────

def search_places_api(query: str, lat: float, lng: float, api_key: str,
                      radius: int = 16000, limit: int = 10) -> tuple:
    """
    Returns (results_list, denied_flag).
    denied_flag=True means we hit REQUEST_DENIED → caller should fall back to OSM.
    """
    url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "location": f"{lat},{lng}", "radius": radius, "key": api_key}
    try:
        r    = requests.get(url, params=params, timeout=12)
        data = r.json()
        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            log.warning(f"    Places API REQUEST_DENIED: {data.get('error_message','no detail')}")
            return [], True
        if status not in ("OK", "ZERO_RESULTS"):
            log.warning(f"    Places API status: {status}")
        return data.get("results", [])[:limit], False
    except Exception as e:
        log.error(f"    Places API error: {e}")
        return [], False


def parse_places_result(result: dict, role: str, category: str, source_zip: str) -> dict:
    address   = result.get("formatted_address", "")
    parts     = address.split(",")
    city      = parts[1].strip() if len(parts) > 1 else ""
    state_zip = parts[2].strip() if len(parts) > 2 else ""
    state     = state_zip[:2] if state_zip else ""
    zip_      = state_zip[3:8].strip() if len(state_zip) > 3 else ""
    return {
        "id":           make_lead_id(result.get("name", ""), address),
        "name":         result.get("name", "")[:200],
        "address":      address[:300],
        "city":         city,
        "state":        state,
        "zip":          zip_,
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


# ── Overpass (OSM) fallback ───────────────────────────────────────────────────

def build_overpass_query(lat: float, lng: float, osm_tags: list,
                          radius: int = 16000) -> str:
    """Build valid Overpass QL for multiple tag pairs within radius."""
    tag_lines = ""
    for key, val in osm_tags:
        tag_lines += f'  node["{key}"="{val}"](around:{radius},{lat},{lng});\n'
        tag_lines += f'  way["{key}"="{val}"](around:{radius},{lat},{lng});\n'
    return f"""[out:json][timeout:30];
(
{tag_lines}
);
out body;
"""


def search_overpass(lat: float, lng: float, osm_tags: list,
                    radius: int = 16000, limit: int = 10) -> list:
    """
    Try each Overpass endpoint in order. Log status + body preview on failure.
    Returns list of OSM elements.
    """
    query = build_overpass_query(lat, lng, osm_tags, radius)

    for endpoint in OVERPASS_ENDPOINTS:
        log.info(f"    Overpass: {endpoint}")
        try:
            r = requests.post(
                endpoint,
                data={"data": query},
                headers=HEADERS,
                timeout=35,
            )
            if r.status_code != 200:
                log.warning(
                    f"    Overpass HTTP {r.status_code} from {endpoint}. "
                    f"Body preview: {r.text[:300]!r}"
                )
                continue
            elements = r.json().get("elements", [])
            log.info(f"    {len(elements)} OSM elements returned")
            return elements[:limit]
        except requests.exceptions.JSONDecodeError as e:
            log.warning(f"    JSON decode error from {endpoint}: {e} | body: {r.text[:300]!r}")
        except requests.RequestException as e:
            log.warning(f"    Request error from {endpoint}: {e}")
        time.sleep(1)

    log.error("    All Overpass endpoints failed for this query.")
    return []


def parse_overpass_result(el: dict, role: str, category: str, source_zip: str):
    tags    = el.get("tags", {})
    name    = tags.get("name", "").strip()
    if not name:
        return None
    address = " ".join(filter(None, [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]))
    return {
        "id":           make_lead_id(name, address + tags.get("addr:city", "")),
        "name":         name[:200],
        "address":      address[:300],
        "city":         tags.get("addr:city", ""),
        "state":        tags.get("addr:state", ""),
        "zip":          tags.get("addr:postcode", ""),
        "phone":        tags.get("phone", tags.get("contact:phone", "")),
        "website":      tags.get("website", tags.get("contact:website", "")),
        "rating":       None,
        "review_count": None,
        "role":         role,
        "category":     category,
        "source_zip":   source_zip,
        "notes":        "",
        "status":       "new",
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }


# ── DB insert ─────────────────────────────────────────────────────────────────

def upsert_lead(conn, lead: dict, dry_run=False) -> bool:
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


# ── CSV export ─────────────────────────────────────────────────────────────────

def export_csv(conn, output_path: str):
    rows = conn.execute("SELECT * FROM leads ORDER BY role, category, name").fetchall()
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "name", "address", "city", "state", "zip", "phone", "website",
            "rating", "review_count", "role", "category", "source_zip",
            "notes", "status", "created_at"
        ])
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    log.info(f"Exported {len(rows)} leads to {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Green Gregory lead gen")
    parser.add_argument("--zips", nargs="+", default=DEFAULT_ZIPS,
                        help="Zip codes to search")
    parser.add_argument("--role", choices=["buyer", "supplier", "all"], default="all")
    parser.add_argument("--output", help="Also export leads to CSV path")
    parser.add_argument("--radius", type=int, default=16000,
                        help="Search radius in meters (default 16000 ~10 mi)")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max results per query")
    parser.add_argument("--force-osm", action="store_true",
                        help="Skip Google Places API, always use OpenStreetMap / Overpass")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print found leads, do not write to DB")
    args = parser.parse_args()

    api_key   = "" if args.force_osm else os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    use_osm   = args.force_osm or not api_key
    conn      = get_db()
    total     = 0
    targets   = [t for t in SEARCH_TARGETS
                 if args.role == "all" or t[2] == args.role]

    if use_osm:
        log.info("Mode: OpenStreetMap / Overpass (--force-osm or no API key)")
    else:
        log.info("Mode: Google Places API")

    for zip_code in args.zips:
        coords = ZIP_COORDS.get(zip_code)
        if not coords:
            log.warning(f"No coords for zip {zip_code} — add to ZIP_COORDS dict")
            continue
        lat, lng = coords
        log.info(f"── Zip {zip_code}  ({lat:.4f}, {lng:.4f}) ──")

        for query, osm_tags, role, category in targets:
            log.info(f"  [{role}] {query}")

            if not use_osm:
                # Google Places
                results, denied = search_places_api(
                    query, lat, lng, api_key, args.radius, args.limit
                )
                if denied:
                    log.info("  REQUEST_DENIED — switching to OSM for remaining queries")
                    use_osm = True  # fall back for all subsequent calls

                if not use_osm:
                    for r in results:
                        lead = parse_places_result(r, role, category, zip_code)
                        if upsert_lead(conn, lead, args.dry_run):
                            total += 1
                            if args.dry_run:
                                log.info(f"    DRY  [{role}] {lead['name'][:50]} — {lead['city']}, {lead['state']}")
                    time.sleep(0.2)
                    continue

            # OSM / Overpass
            elements = search_overpass(lat, lng, osm_tags, args.radius, args.limit)
            for el in elements:
                lead = parse_overpass_result(el, role, category, zip_code)
                if lead and upsert_lead(conn, lead, args.dry_run):
                    total += 1
                    if args.dry_run:
                        log.info(f"    DRY  [{role}] {lead['name'][:50]} — {lead['city']}, {lead['state']}")
            time.sleep(0.5)  # Overpass rate-limit courtesy

        if not args.dry_run:
            conn.commit()

    # Summary
    counts = conn.execute(
        "SELECT role, COUNT(*) n FROM leads GROUP BY role"
    ).fetchall()
    log.info(f"=== Lead gen complete — {total} new leads this run ===")
    for r in counts:
        log.info(f"  {r['role']:10}: {r['n']} total in DB")

    if args.output and not args.dry_run:
        export_csv(conn, args.output)

    conn.close()


if __name__ == "__main__":
    main()
