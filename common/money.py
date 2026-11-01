"""Money helpers. All amounts in this project are decimal.Decimal, never float."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def D(value) -> Decimal:
    """Convert str/int/Decimal to Decimal. Floats go through str() so 0.1 stays 0.1."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return Decimal(value)


def q2(value) -> Decimal:
    """Round to cents (half-up, the usual accounting convention)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_money(text: str) -> Decimal:
    """'1,250,000.00' -> Decimal('1250000.00')."""
    return q2(text.replace(",", ""))
