"""Agent B's downstream work once it has the bank data: review marks, compute NAV + bridge."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from agent_b import fx, nav, review
from agent_b.data import load_q3, load_q4
from common.money import D


def close_quarter(pack_dir: Path, transactions: list[dict], marks_payload: dict, threshold_pct: str | Decimal):
    q3, q4 = load_q3(pack_dir), load_q4(pack_dir)
    rate = fx.eurusd_for(q4.valuation_date)
    threshold = D(threshold_pct)

    # Marks come from the A2A artifact (what Agent A received); purchases/sales are Agent B's own
    # records, which Agent A already proved equal to the bank statement during the handshake.
    marks = marks_payload["marks"]
    notes = marks_payload.get("notes", [])
    entries = review.review_marks(q3, marks, notes, q4.purchases, q4.sales, rate.eurusd, threshold)
    review_doc = {
        "fund": q4.fund_name, "valuation_date": q4.valuation_date,
        "threshold_pct": threshold, "eurusd_rate": rate.eurusd,
        "eurusd_rate_date": rate.rate_date, "eurusd_rate_source": rate.source_url,
        "companies": entries,
    }
    nav_doc = nav.compute_nav(q3, transactions, marks, q4.purchases, q4.sales, rate.eurusd)
    return review_doc, nav_doc
