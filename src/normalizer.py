import re
from models import RawItem


def extract_price(text: str):
    if not text:
        return None
    matches = re.findall(r'\$[\d,]+(?:\.\d{1,2})?', text)
    if matches:
        raw = matches[0].replace('$', '').replace(',', '')
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize(item: RawItem) -> RawItem:
    item.title = clean_text(item.title)
    item.description = clean_text(item.description)
    item.location = clean_text(item.location)

    if item.price is None and item.description:
        item.price = extract_price(item.description)
    if item.price is None and item.title:
        item.price = extract_price(item.title)

    return item
