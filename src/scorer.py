import yaml
from models import RawItem, Opportunity

with open("config/keywords.yaml") as f:
    KEYWORDS = yaml.safe_load(f)

with open("config/sources.yaml") as f:
    SOURCES = yaml.safe_load(f)

with open("config/scoring.yaml") as f:
    SCORING = yaml.safe_load(f)

SUPPLY = [s.lower() for s in KEYWORDS["supply_signals"]]
NEGATIVE = [s.lower() for s in KEYWORDS.get("negative_signals", [])]
WEIGHTS = SCORING["weights"]
PENALTIES = SCORING["penalties"]
THRESHOLDS = SCORING["thresholds"]


def match_category(item: RawItem):
    text = (item.title + " " + item.description).lower()
    for cat_key, cat in SOURCES["categories"].items():
        for kw in cat["keywords"]:
            if kw.lower() in text:
                return cat_key, cat
    return None, None


def score_item(item: RawItem, cat_key: str, cat: dict) -> tuple[int, list]:
    text = (item.title + " " + item.description).lower()
    score = 0
    flags = []

    # Urgency
    if any(s in text for s in SUPPLY):
        score += WEIGHTS["urgency_language"]

    # B2B commercial
    if cat.get("b2b"):
        score += WEIGHTS["commercial_b2b"]
    else:
        score += PENALTIES["consumer_only"]
        flags.append("consumer item")

    # Price checks
    if item.price is None:
        score += PENALTIES["missing_price"]
        flags.append("missing price")
    else:
        min_p = cat.get("min_price", 0)
        max_p = cat.get("max_price", 99999)
        if item.price < min_p or item.price > max_p:
            score += PENALTIES["thin_margin"]
            flags.append("price out of range")
        else:
            multiplier = cat.get("estimated_multiplier", 1.5)
            estimated_low = item.price * multiplier * 0.85
            estimated_high = item.price * multiplier * 1.15
            spread = estimated_low - item.price
            if spread >= SCORING["min_spread_dollars"]:
                score += WEIGHTS["price_below_comp"]
            if item.price >= SCORING["high_ticket_threshold"]:
                score += WEIGHTS["high_ticket_value"]

    # Negative condition signals
    if any(n in text for n in NEGATIVE):
        score += PENALTIES["negative_condition"]
        flags.append("condition concern")

    # Category bonus
    score += cat.get("score_bonus", 0)

    # Contact path
    if item.url:
        score += WEIGHTS["clear_contact"]

    return max(0, score), flags


def evaluate(item: RawItem, market: str) -> Opportunity | None:
    cat_key, cat = match_category(item)
    if not cat_key or item.price is None:
        return None

    score, flags = score_item(item, cat_key, cat)

    if score < THRESHOLDS["pass"]:
        return None

    multiplier = cat.get("estimated_multiplier", 1.5)
    est_low = round(item.price * multiplier * 0.85, 2)
    est_high = round(item.price * multiplier * 1.15, 2)

    status = "pass"
    if score >= THRESHOLDS["review"]:
        status = "review"
    elif score >= THRESHOLDS["watch"]:
        status = "watch"

    if status == "pass":
        return None

    return Opportunity(
        raw_item_id=0,
        category=cat_key,
        market=market,
        asking_price=item.price,
        estimated_low=est_low,
        estimated_high=est_high,
        spread_low=round(est_low - item.price, 2),
        spread_high=round(est_high - item.price, 2),
        score=score,
        risk_flags=", ".join(flags),
        status=status
    )
