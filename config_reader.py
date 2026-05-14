"""
config_reader.py — Green Gregory Group Opportunity OS
Phase 3: Externalised config — reads 6 tabs from Google Sheets → config.json

All scripts import from config.json instead of hardcoded values.
Run this first in every GitHub Actions workflow step.

Sheet tabs read:
  Search Criteria   — feed URLs and keyword lists per source
  Markets           — zip codes + coordinates + cities to scan
  Buyer Profiles    — who you sell to, what they want, what they pay
  Leadgen Profiles  — target business categories for lead gen
  Scoring           — score weights, min thresholds, margin targets
  Settings          — email recipients, Sheets ID, schedule, flags

Usage:
    python config_reader.py                  # fetch from Sheets → config.json
    python config_reader.py --dry-run        # print parsed config, don't write
    python config_reader.py --fallback       # use built-in defaults (no Sheets needed)

Environment variables:
    GOOGLE_CREDENTIALS_JSON   service account key JSON
    GGG_SPREADSHEET_ID        overrides hardcoded SPREADSHEET_ID below
"""

import json
import os
import sys
import argparse
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("config_reader")

CONFIG_PATH    = Path("config.json")
SPREADSHEET_ID = "173j8XDlDeBvsFdRyqBZ6oGhNzIyCntDO_esqRMvdZ1w"
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# ── Default / fallback config (mirrors what the Sheets tabs should contain) ───
# Edit these here first; once the Sheet tabs are populated they take precedence.

DEFAULTS = {
    "search_criteria": {
        "keywords": [
            "network", "cisco", "juniper", "server", "rack", "switch", "router",
            "pallet", "lot", "surplus", "liquidation", "equipment", "tools",
            "forklift", "generator", "hvac", "pump", "compressor",
            "vehicle", "trailer", "telecom", "audio", "video", "ice machine",
            "pressure washer", "scrubber", "racking", "shelving", "industrial",
            "machinery", "office furniture",
        ],
        "govdeals_feeds": [
            "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=network+equipment&kField=titl&sortBy=ad&sortOrder=d&Category=45",
            "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=server+rack&kField=titl&sortBy=ad&sortOrder=d&Category=45",
            "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=cisco+switch&kField=titl&sortBy=ad&sortOrder=d",
            "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsRSS&kWord=surplus+equipment&kField=titl&sortBy=ad&sortOrder=d",
        ],
        "publicsurplus_feeds": [
            "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=network",
            "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=cisco",
            "https://www.publicsurplus.com/sms/browse/rss?catid=57&search=server",
            "https://www.publicsurplus.com/sms/browse/rss?catid=0&search=surplus+lot",
        ],
        "craigslist_searches": [
            {"city": "cincinnati",   "category": "sss", "query": "network equipment"},
            {"city": "cincinnati",   "category": "sss", "query": "server rack"},
            {"city": "indianapolis", "category": "sss", "query": "surplus liquidation"},
            {"city": "indianapolis", "category": "bfs", "query": "equipment lot"},
            {"city": "dayton",       "category": "sss", "query": "pallet lot"},
            {"city": "louisville",   "category": "sss", "query": "cisco switch"},
        ],
    },
    "markets": [
        {"zip": "47226", "label": "Batesville IN",      "lat": 39.3003, "lng": -85.4852, "active": True},
        {"zip": "47025", "label": "Lawrenceburg IN",    "lat": 39.0920, "lng": -84.8571, "active": True},
        {"zip": "45219", "label": "Cincinnati OH",      "lat": 39.1322, "lng": -84.5158, "active": True},
        {"zip": "45202", "label": "Cincinnati DT OH",   "lat": 39.1031, "lng": -84.5120, "active": True},
        {"zip": "47374", "label": "Richmond IN",        "lat": 39.8286, "lng": -84.8901, "active": True},
        {"zip": "46201", "label": "Indianapolis E IN",  "lat": 39.7684, "lng": -86.1099, "active": True},
        {"zip": "46107", "label": "Indianapolis S IN",  "lat": 39.6870, "lng": -86.0916, "active": True},
    ],
    "buyer_profiles": [
        {
            "id": "buyer_network_it",
            "name": "IT Resellers / eBay / NetworkHardware",
            "keywords": ["cisco", "juniper", "switch", "router", "firewall",
                         "network", "server", "rack", "sfp", "access point",
                         "wifi", "wireless", "telecom"],
            "max_buy_price": 5000,
            "target_margin": 0.40,
            "score_weight": 25,
            "notes": "High velocity on eBay. Cisco and Juniper move fastest.",
            "active": True,
        },
        {
            "id": "buyer_pallet_lots",
            "name": "Amazon FBA / Liquidators",
            "keywords": ["pallet", "lot", "bulk", "liquidation", "surplus",
                         "overstock", "wholesale", "racking", "shelving"],
            "max_buy_price": 3000,
            "target_margin": 0.50,
            "score_weight": 20,
            "notes": "Pallet lots can 3x if you know the manifest.",
            "active": True,
        },
        {
            "id": "buyer_industrial",
            "name": "Industrial Equipment Dealers",
            "keywords": ["forklift", "generator", "compressor", "pump", "hvac",
                         "industrial", "machinery", "scrubber", "pressure washer"],
            "max_buy_price": 15000,
            "target_margin": 0.30,
            "score_weight": 20,
            "notes": "Larger ticket. Slower turn but higher margin dollars.",
            "active": True,
        },
        {
            "id": "buyer_office",
            "name": "Office Liquidators / Facebook Marketplace",
            "keywords": ["desk", "chair", "furniture", "office", "cubicle",
                         "copier", "printer", "monitor"],
            "max_buy_price": 1500,
            "target_margin": 0.45,
            "score_weight": 15,
            "notes": "Local pickup plays well. Quick flip in metro areas.",
            "active": True,
        },
        {
            "id": "buyer_food_equip",
            "name": "Restaurant Equipment Dealers / eBay",
            "keywords": ["ice machine", "refrigerator", "freezer", "fryer",
                         "commercial kitchen", "cafeteria", "food service"],
            "max_buy_price": 3000,
            "target_margin": 0.45,
            "score_weight": 25,
            "notes": "Manitowoc / Hoshizaki ice machines flip 2-3x.",
            "active": True,
        },
    ],
    "leadgen_profiles": [
        {"query": "surplus equipment dealer",   "osm_key": "shop",    "osm_val": "second_hand",  "role": "supplier", "category": "surplus_dealer",  "active": True},
        {"query": "liquidation warehouse",       "osm_key": "shop",    "osm_val": "wholesale",    "role": "supplier", "category": "liquidator",      "active": True},
        {"query": "used IT equipment dealer",    "osm_key": "shop",    "osm_val": "electronics",  "role": "supplier", "category": "it_reseller",     "active": True},
        {"query": "industrial equipment dealer", "osm_key": "shop",    "osm_val": "hardware",     "role": "supplier", "category": "industrial",      "active": True},
        {"query": "IT equipment buyer reseller", "osm_key": "shop",    "osm_val": "computer",     "role": "buyer",    "category": "it_buyer",        "active": True},
        {"query": "electronics recycler",        "osm_key": "amenity", "osm_val": "recycling",    "role": "buyer",    "category": "recycler",        "active": True},
        {"query": "used equipment reseller",     "osm_key": "shop",    "osm_val": "second_hand",  "role": "buyer",    "category": "reseller",        "active": True},
        {"query": "industrial machinery dealer", "osm_key": "shop",    "osm_val": "hardware",     "role": "buyer",    "category": "industrial_buyer","active": True},
    ],
    "scoring": {
        "min_score_match":  40,
        "min_score_alert":  40,
        "price_boost_low":  20,   # bonus when price < 25% of max_buy
        "price_boost_mid":  10,   # bonus when price < 50% of max_buy
        "over_budget_penalty": 15,
        "score_cap": 100,
    },
    "settings": {
        "email_to":              "",   # overridden by GGG_EMAIL_TO env var
        "alert_min_score":       40,
        "scan_radius_meters":    16000,
        "leadgen_limit_per_query": 10,
        "dry_run_default":       False,
        "schedule_enabled":      True,
        "spreadsheet_id":        SPREADSHEET_ID,
    },
}

# ── Sheets auth ────────────────────────────────────────────────────────────────

def get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("Run: pip install gspread google-auth")

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(creds_json)
            tmp = f.name
        creds = Credentials.from_service_account_file(tmp, scopes=SCOPES)
        Path(tmp).unlink(missing_ok=True)
    elif Path("credentials.json").exists():
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "No Google credentials. Set GOOGLE_CREDENTIALS_JSON or place credentials.json here."
        )
    import gspread
    return gspread.authorize(creds)


def get_spreadsheet():
    sid = os.environ.get("GGG_SPREADSHEET_ID", SPREADSHEET_ID).strip()
    client = get_gspread_client()
    return client.open_by_key(sid)


# ── Tab parsers ────────────────────────────────────────────────────────────────

def parse_search_criteria(ws) -> dict:
    """
    Expected columns: Type | Value
    Type values: keyword, govdeals_feed, publicsurplus_feed, craigslist
    For craigslist rows, Value is: city|category|query
    """
    rows = ws.get_all_records()
    keywords, gd_feeds, ps_feeds, cl_searches = [], [], [], []
    for r in rows:
        t = str(r.get("Type", "")).strip().lower()
        v = str(r.get("Value", "")).strip()
        if not v:
            continue
        if t == "keyword":
            keywords.append(v)
        elif t == "govdeals_feed":
            gd_feeds.append(v)
        elif t == "publicsurplus_feed":
            ps_feeds.append(v)
        elif t == "craigslist":
            parts = [p.strip() for p in v.split("|")]
            if len(parts) == 3:
                cl_searches.append({"city": parts[0], "category": parts[1], "query": parts[2]})
    return {
        "keywords":            keywords or DEFAULTS["search_criteria"]["keywords"],
        "govdeals_feeds":      gd_feeds  or DEFAULTS["search_criteria"]["govdeals_feeds"],
        "publicsurplus_feeds": ps_feeds  or DEFAULTS["search_criteria"]["publicsurplus_feeds"],
        "craigslist_searches": cl_searches or DEFAULTS["search_criteria"]["craigslist_searches"],
    }


def parse_markets(ws) -> list:
    """
    Expected columns: ZIP | Label | Lat | Lng | Active
    """
    rows = ws.get_all_records()
    markets = []
    for r in rows:
        active = str(r.get("Active", "yes")).strip().lower() not in ("no", "false", "0", "")
        try:
            markets.append({
                "zip":    str(r.get("ZIP", "")).strip(),
                "label":  str(r.get("Label", "")).strip(),
                "lat":    float(r.get("Lat", 0)),
                "lng":    float(r.get("Lng", 0)),
                "active": active,
            })
        except (ValueError, TypeError):
            continue
    return markets or DEFAULTS["markets"]


def parse_buyer_profiles(ws) -> list:
    """
    Expected columns: ID | Name | Keywords (comma-sep) | MaxBuyPrice | TargetMargin |
                      ScoreWeight | Notes | Active
    """
    rows = ws.get_all_records()
    profiles = []
    for r in rows:
        active = str(r.get("Active", "yes")).strip().lower() not in ("no", "false", "0", "")
        kws_raw = str(r.get("Keywords", "")).strip()
        kws = [k.strip() for k in kws_raw.split(",") if k.strip()]
        try:
            profiles.append({
                "id":            str(r.get("ID", "")).strip(),
                "name":          str(r.get("Name", "")).strip(),
                "keywords":      kws,
                "max_buy_price": float(r.get("MaxBuyPrice", 5000)),
                "target_margin": float(r.get("TargetMargin", 0.40)),
                "score_weight":  int(r.get("ScoreWeight", 20)),
                "notes":         str(r.get("Notes", "")).strip(),
                "active":        active,
            })
        except (ValueError, TypeError):
            continue
    return profiles or DEFAULTS["buyer_profiles"]


def parse_leadgen_profiles(ws) -> list:
    """
    Expected columns: Query | OSM_Key | OSM_Val | Role | Category | Active
    """
    rows = ws.get_all_records()
    profiles = []
    for r in rows:
        active = str(r.get("Active", "yes")).strip().lower() not in ("no", "false", "0", "")
        profiles.append({
            "query":    str(r.get("Query", "")).strip(),
            "osm_key":  str(r.get("OSM_Key", "shop")).strip(),
            "osm_val":  str(r.get("OSM_Val", "")).strip(),
            "role":     str(r.get("Role", "supplier")).strip(),
            "category": str(r.get("Category", "")).strip(),
            "active":   active,
        })
    return [p for p in profiles if p["query"]] or DEFAULTS["leadgen_profiles"]


def parse_scoring(ws) -> dict:
    """
    Expected columns: Setting | Value
    """
    rows = ws.get_all_records()
    scoring = dict(DEFAULTS["scoring"])  # start from defaults
    for r in rows:
        key = str(r.get("Setting", "")).strip()
        val = r.get("Value", "")
        if key and val != "":
            try:
                scoring[key] = int(val) if str(val).isdigit() else float(val)
            except (ValueError, TypeError):
                pass
    return scoring


def parse_settings(ws) -> dict:
    """
    Expected columns: Setting | Value
    """
    rows = ws.get_all_records()
    settings = dict(DEFAULTS["settings"])
    for r in rows:
        key = str(r.get("Setting", "")).strip()
        val = str(r.get("Value", "")).strip()
        if not key:
            continue
        # Type coerce
        if val.lower() in ("true", "yes"):
            settings[key] = True
        elif val.lower() in ("false", "no"):
            settings[key] = False
        elif val.isdigit():
            settings[key] = int(val)
        else:
            try:
                settings[key] = float(val)
            except ValueError:
                settings[key] = val
    return settings


def parse_sources(ws) -> list:
    """
    Reads the Sources tab from Google Sheets.
    Expected columns (exact names):
      enabled | source_name | source_type | signal_type | query | category |
      market | zip | radius_miles | include_keywords | exclude_keywords | notes | scraper_status

    Returns list of source dicts written to sources_config.json by _write_sources_config.
    Falls back to sources_config.json on disk if sheet is empty.
    """
    rows = ws.get_all_records()
    sources = []
    for r in rows:
        raw_enabled = str(r.get("enabled", "true")).strip().lower()
        enabled = raw_enabled not in ("no", "false", "0", "")
        try:
            sources.append({
                "enabled":          enabled,
                "source_name":      str(r.get("source_name",  "")).strip(),
                "source_type":      str(r.get("source_type",  "")).strip(),
                "signal_type":      str(r.get("signal_type",  "supply")).strip(),
                "query":            str(r.get("query",         "")).strip(),
                "category":         str(r.get("category",      "")).strip(),
                "market":           str(r.get("market",        "")).strip(),
                "zip":              str(r.get("zip",           "")).strip(),
                "radius_miles":     int(r.get("radius_miles",  50) or 50),
                "include_keywords": str(r.get("include_keywords", "")).strip(),
                "exclude_keywords": str(r.get("exclude_keywords", "")).strip(),
                "notes":            str(r.get("notes",          "")).strip(),
                "scraper_status":   str(r.get("scraper_status", "manual")).strip(),
            })
        except (ValueError, TypeError) as e:
            log.warning(f"  Skipping malformed Sources row: {e}")
            continue
    if not sources:
        log.warning("  Sources tab empty — checking sources_config.json on disk")
        if Path("sources_config.json").exists():
            try:
                data = json.loads(Path("sources_config.json").read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    log.info(f"  Loaded {len(data)} sources from sources_config.json")
                    return data
            except Exception:
                pass
        log.warning("  No sources found in Sheet or disk — export_json.py will use built-in defaults")
        return []
    return sources


# ── Main fetch ─────────────────────────────────────────────────────────────────

TAB_NAMES = {
    "search_criteria":  "Search Criteria",
    "markets":          "Markets",
    "buyer_profiles":   "Buyer Profiles",
    "leadgen_profiles": "Leadgen Profiles",
    "scoring":          "Scoring",
    "settings":         "Settings",
    "sources_config":   "Sources",        # NEW: drives sources_config.json + dashboard lanes
}

PARSERS = {
    "search_criteria":  parse_search_criteria,
    "markets":          parse_markets,
    "buyer_profiles":   parse_buyer_profiles,
    "leadgen_profiles": parse_leadgen_profiles,
    "scoring":          parse_scoring,
    "settings":         parse_settings,
    "sources_config":   parse_sources,    # NEW
}


def fetch_config_from_sheets() -> dict:
    spreadsheet = get_spreadsheet()
    config = {}
    for key, tab_name in TAB_NAMES.items():
        try:
            ws = spreadsheet.worksheet(tab_name)
            config[key] = PARSERS[key](ws)
            log.info(f"  ✓ {tab_name}")
        except Exception as e:
            log.warning(f"  ✗ {tab_name}: {e} — using defaults")
            # sources_config has no DEFAULTS entry — fall back to empty list
            config[key] = DEFAULTS.get(key, [])
    config["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    config["_source"]     = "google_sheets"

    # Write sources_config.json from Sheet data so export_json.py picks it up
    _write_sources_config(config.get("sources_config") or [])
    return config


SOURCES_CFG_PATH = Path("sources_config.json")


def _write_sources_config(sources: list):
    """Write sources_config.json so export_json.py and the dashboard pick up Sheet-driven sources."""
    if not sources:
        log.info("  sources_config: empty — sources_config.json not overwritten")
        return
    SOURCES_CFG_PATH.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    log.info(f"  Wrote {len(sources)} sources to {SOURCES_CFG_PATH.resolve()}")


def build_default_config() -> dict:
    config = dict(DEFAULTS)
    config["sources_config"] = []   # export_json.py will use its own DEFAULT_SOURCES when empty
    config["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    config["_source"]     = "defaults"
    return config


# ── Convenience loader (used by all other scripts) ────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict:
    """
    Called by scanner.py, matcher.py, leadgen.py, etc.
    Returns config dict. Falls back to DEFAULTS if config.json missing.
    """
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    log.warning(f"config.json not found at {path} — using built-in defaults")
    return build_default_config()


# ── Sheet tab setup (creates tabs with headers if they don't exist) ────────────

TAB_HEADERS = {
    "Search Criteria":  ["Type", "Value", "Notes"],
    "Markets":          ["ZIP", "Label", "Lat", "Lng", "Active"],
    "Buyer Profiles":   ["ID", "Name", "Keywords", "MaxBuyPrice", "TargetMargin", "ScoreWeight", "Notes", "Active"],
    "Leadgen Profiles": ["Query", "OSM_Key", "OSM_Val", "Role", "Category", "Active"],
    "Scoring":          ["Setting", "Value", "Description"],
    "Settings":         ["Setting", "Value", "Description"],
    "Sources":          ["enabled", "source_name", "source_type", "signal_type", "query",
                         "category", "market", "zip", "radius_miles",
                         "include_keywords", "exclude_keywords", "notes", "scraper_status"],
}

def ensure_config_tabs(spreadsheet):
    """Create config tabs with correct headers if they don't exist."""
    import gspread
    existing = {ws.title for ws in spreadsheet.worksheets()}
    for tab_name, headers in TAB_HEADERS.items():
        if tab_name not in existing:
            ws = spreadsheet.add_worksheet(title=tab_name, rows=200, cols=len(headers)+2)
            ws.update("A1", [headers])
            log.info(f"  Created tab: {tab_name}")
        else:
            log.info(f"  Tab exists: {tab_name}")


def seed_config_tabs(spreadsheet):
    """Write default values into config tabs so the Sheet is immediately usable."""
    # Search Criteria
    sc_rows = [["keyword", kw, ""] for kw in DEFAULTS["search_criteria"]["keywords"]]
    sc_rows += [["govdeals_feed", u, ""] for u in DEFAULTS["search_criteria"]["govdeals_feeds"]]
    sc_rows += [["publicsurplus_feed", u, ""] for u in DEFAULTS["search_criteria"]["publicsurplus_feeds"]]
    sc_rows += [["craigslist", f"{c['city']}|{c['category']}|{c['query']}", ""]
                for c in DEFAULTS["search_criteria"]["craigslist_searches"]]
    ws = spreadsheet.worksheet("Search Criteria")
    ws.update("A2", sc_rows)

    # Markets
    m_rows = [[m["zip"], m["label"], m["lat"], m["lng"], "yes"] for m in DEFAULTS["markets"]]
    spreadsheet.worksheet("Markets").update("A2", m_rows)

    # Buyer Profiles
    bp_rows = [
        [p["id"], p["name"], ", ".join(p["keywords"]),
         p["max_buy_price"], p["target_margin"], p["score_weight"], p["notes"], "yes"]
        for p in DEFAULTS["buyer_profiles"]
    ]
    spreadsheet.worksheet("Buyer Profiles").update("A2", bp_rows)

    # Leadgen Profiles
    lp_rows = [
        [p["query"], p["osm_key"], p["osm_val"], p["role"], p["category"], "yes"]
        for p in DEFAULTS["leadgen_profiles"]
    ]
    spreadsheet.worksheet("Leadgen Profiles").update("A2", lp_rows)

    # Scoring
    sc2_rows = [
        ["min_score_match",      40,   "Minimum score to create a match"],
        ["min_score_alert",      40,   "Minimum score to include in email alert"],
        ["price_boost_low",      20,   "Score bonus when price < 25% of max_buy"],
        ["price_boost_mid",      10,   "Score bonus when price < 50% of max_buy"],
        ["over_budget_penalty",  15,   "Score penalty when price > max_buy"],
        ["score_cap",           100,   "Maximum possible score"],
    ]
    spreadsheet.worksheet("Scoring").update("A2", sc2_rows)

    # Settings
    s_rows = [
        ["alert_min_score",           40,      "Min score for email alerts"],
        ["scan_radius_meters",        16000,   "Radius for leadgen searches"],
        ["leadgen_limit_per_query",   10,      "Max leads per OSM query"],
        ["dry_run_default",           "false", "Set to true to always dry-run"],
        ["schedule_enabled",          "true",  "Whether cron is active"],
    ]
    spreadsheet.worksheet("Settings").update("A2", s_rows)

    # Sources — 12 default rows
    src_defaults = [
        ["true",  "Craigslist For Sale — Equipment",      "craigslist_for_sale",       "supply",           "pressure washer OR pallet racking OR restaurant equipment", "equipment",          "Cincinnati", "45219", 50,  "commercial,equipment,tools,restaurant,pallet,rack,pressure washer", "toy,junk,broken,parts only", "General supply feed",                  "live"],
        ["true",  "Craigslist Wanted — ISO / Services",   "craigslist_wanted",         "demand",           "ISO pressure washing OR wanted driveway cleaning",           "service_demand",     "Cincinnati", "45219", 50,  "ISO,wanted,need,looking for,driveway,cleaning,junk removal",       "spam",                       "Demand-side / ISO feed",               "live"],
        ["true",  "Craigslist Services — Operators",      "craigslist_services",       "operator",         "pressure washing OR junk removal OR hauling OR handyman",    "service_provider",   "Cincinnati", "45219", 50,  "pressure washing,junk removal,hauling,handyman,cheap,local",       "franchise,national",         "Operator / capability feed",           "live"],
        ["true",  "GovDeals — Surplus Equipment",         "govdeals",                  "supply",           "equipment tools vehicles",                                   "government_surplus", "Regional",   "45219", 200, "equipment,vehicle,fleet,tools,industrial",                         "parts only",                 "Government surplus auctions",          "live"],
        ["true",  "PublicSurplus — Auctions",             "publicsurplus",             "supply",           "equipment fleet tools",                                      "government_surplus", "Regional",   "45219", 200, "equipment,vehicle,fleet,tools",                                    "parts only",                 "Public surplus auctions",              "live"],
        ["true",  "Facebook Marketplace — Manual Import", "facebook_manual",           "supply",           "manual paste",                                               "manual_supply",      "Cincinnati", "45219", 50,  "equipment,tools,business,garage sale,moving sale",                "",                           "Manual source for pasted Facebook posts","manual"],
        ["true",  "Garage / Estate Sale — Manual",        "garage_sale_manual",        "garage_sale",      "manual paste",                                               "local_event",        "Cincinnati", "45219", 50,  "garage sale,moving sale,estate sale,tools,equipment",              "baby clothes",               "Manual / import source for local sales","manual"],
        ["true",  "Business For Sale — Manual",           "business_for_sale_manual",  "business_for_sale","manual paste",                                               "business_acquisition","Cincinnati","45219", 100, "business for sale,established,route,equipment included,cash flow", "",                           "Manual import of business-for-sale listings","manual"],
        ["false", "OSM Local Business Leads",             "osm_leadgen",               "lead",             "used equipment reseller OR electronics recycler",            "buyer_lead",         "Cincinnati", "45219", 50,  "reseller,recycler,equipment dealer,repair shop",                  "",                           "Buyer/demand leadgen from OpenStreetMap","not_implemented"],
        ["false", "Nextdoor — Manual",                    "nextdoor_manual",           "demand",           "manual paste",                                               "community_demand",   "Cincinnati", "45219", 25,  "looking for,need,ISO,anyone know,recommend",                       "",                           "Paste Nextdoor posts manually",        "manual"],
        ["false", "BizBuySell — Scrape",                  "bizbuysell",                "business_for_sale","Cincinnati OR Ohio",                                         "business_acquisition","Cincinnati","45219", 100, "equipment,route,cash flow,established",                            "",                           "Future scraper target",                "not_implemented"],
        ["false", "RSS Feed — Generic",                   "rss",                       "supply",           "",                                                           "rss_feed",           "Cincinnati", "45219", 50,  "",                                                                 "",                           "Generic RSS feed import",              "not_implemented"],
    ]
    spreadsheet.worksheet("Sources").update("A2", src_defaults)

    log.info("Config tabs seeded with defaults.")


# ── Main ──────────────────────────────────────────────────────────────────────

def _print_config_summary(config: dict):
    """Print validation summary — called after every successful config write."""
    bp    = config.get("buyer_profiles", [])
    sc    = config.get("scoring", {})
    mkts  = config.get("markets", [])
    lp    = config.get("leadgen_profiles", [])
    kws   = config.get("search_criteria", {}).get("keywords", [])
    srcs  = config.get("sources_config", [])
    print(f"  buyer_profiles    : {len(bp)} profiles")
    print(f"  scoring keys      : {list(sc.keys())}")
    print(f"  markets           : {len(mkts)} entries")
    print(f"  leadgen_profiles  : {len(lp)} profiles")
    print(f"  keywords          : {len(kws)}")
    print(f"  sources_config    : {len(srcs)} sources (written to sources_config.json if >0)")
    print(f"  wrote             : {CONFIG_PATH.resolve()}")
    for key in ("buyer_profiles", "scoring", "markets"):
        if not config.get(key):
            log.warning(f"  WARNING: '{key}' is empty — matcher.py will use built-in defaults")


def main():
    parser = argparse.ArgumentParser(description="Green Gregory config reader")
    parser.add_argument("--dry-run",  action="store_true", help="Print config, don't write file")
    parser.add_argument("--fallback", action="store_true", help="Use built-in defaults, skip Sheets")
    parser.add_argument("--setup-tabs", action="store_true",
                        help="Create config tabs in Sheet and seed with defaults")
    args = parser.parse_args()

    if args.setup_tabs:
        log.info("Setting up config tabs in Google Sheet...")
        ss = get_spreadsheet()
        ensure_config_tabs(ss)
        seed_config_tabs(ss)
        log.info("Done. Open the Sheet and edit to your liking.")
        return

    if args.fallback:
        log.info("Using built-in defaults (--fallback)")
        config = build_default_config()
    else:
        log.info("Fetching config from Google Sheets...")
        try:
            config = fetch_config_from_sheets()
            log.info(f"Config fetched from Sheets ({config['_source']})")
        except Exception as e:
            log.warning(f"Sheets fetch failed ({e}) — falling back to defaults")
            config = build_default_config()

    if args.dry_run:
        print(json.dumps(config, indent=2))
        log.info("Dry-run — config.json not written.")
        return

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    log.info(f"Config written to {CONFIG_PATH.resolve()}")
    _print_config_summary(config)


if __name__ == "__main__":
    main()
