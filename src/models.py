from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class RawItem:
    source: str
    source_item_id: str
    url: str
    title: str
    description: str
    price: Optional[float]
    location: str
    posted_at: Optional[datetime]
    scraped_at: datetime = field(default_factory=datetime.utcnow)
    raw_json: str = ""


@dataclass
class Opportunity:
    raw_item_id: int
    category: str
    market: str
    asking_price: float
    estimated_low: float
    estimated_high: float
    spread_low: float
    spread_high: float
    score: int
    risk_flags: str
    status: str = "new"
