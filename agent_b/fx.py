"""ECB EUR/USD reference rate lookup with weekend roll-back."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from common import jsonio
from common.money import D

TABLE = Path(__file__).parent / "reference" / "ecb_eurusd.json"


class FXError(ValueError):
    pass


@dataclass(frozen=True)
class FXRate:
    requested_date: str
    rate_date: str       # the date the ECB actually published the rate
    eurusd: Decimal      # USD per 1 EUR
    source_url: str


def last_business_day_on_or_before(d: date) -> date:
    while d.weekday() >= 5:      # Sat=5, Sun=6: the ECB publishes no fixing
        d -= timedelta(days=1)
    return d


def eurusd_for(valuation_date: str, table_path: Path = TABLE) -> FXRate:
    table = jsonio.loads(table_path.read_text(encoding="utf-8"))
    rate_day = last_business_day_on_or_before(date.fromisoformat(valuation_date)).isoformat()
    if rate_day not in table["rates"]:
        raise FXError(f"No ECB EUR/USD rate on file for {rate_day} (needed for valuation date {valuation_date})")
    return FXRate(valuation_date, rate_day, D(table["rates"][rate_day]), table["source_urls"].get(rate_day, table["source"]))
