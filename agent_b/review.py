"""Review rules R1-R4: flag anomalies in CVP's Q4 marks vs Q3.

R1  material change vs Q3, measured in LOCAL currency (an FX move is not a change in value)
R2  valuation method changed vs Q3
R3  fund holds the company at 31 Dec but CVP gave no Q4 mark
R4  CVP gave a mark for a company the fund does not hold at 31 Dec
"""
from __future__ import annotations

from decimal import Decimal

from agent_b.data import Q3Data
from common.money import D, q2

PCT = Decimal("0.01")


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    if old == 0:
        return None
    return ((new - old) / old * 100).quantize(PCT)


def held_at_q4(q3: Q3Data, purchases: list[dict], sales: list[dict]) -> set[str]:
    held = set(q3.holdings)
    held |= {p["company"] for p in purchases}
    held -= {s["company"] for s in sales}
    return held


def review_marks(q3: Q3Data, marks: list[dict], notes: list[dict], purchases: list[dict], sales: list[dict],
                 eurusd: Decimal, threshold_pct: Decimal) -> list[dict]:
    held = held_at_q4(q3, purchases, sales)
    mark_by_co = {m["company"]: m for m in marks}
    note_by_co = {n["company"]: n["note"] for n in notes}
    sold = {s["company"]: s for s in sales}
    bought = {p["company"]: p for p in purchases}

    def to_usd(amount: Decimal, ccy: str) -> Decimal:
        if ccy == "USD":
            return q2(amount)
        if ccy == "EUR":
            return q2(amount * eurusd)
        raise ValueError(f"No FX rate for currency {ccy}")

    entries = []
    for company in sorted(set(q3.holdings) | set(mark_by_co) | set(bought) | set(sold)):
        h = q3.holdings.get(company)
        m = mark_by_co.get(company)
        flags: list[str] = []
        reasons: list[str] = []
        entry = {
            "company": company,
            "currency": (m or {}).get("currency") or (h.currency if h else "USD"),
            "q3_fair_value": h.fair_value_local if h else None,
            "q4_fair_value": m["fair_value"] if m else None,
            "change_pct": None,
            "q3_method": h.method if h else None,
            "q4_method": m["valuation_method"] if m else None,
            "q3_fair_value_usd": h.fair_value_usd if h else None,
            "q4_fair_value_usd": to_usd(m["fair_value"], m["currency"]) if m else None,
            "change_pct_usd": None,
            "held_at_q4": company in held,
        }
        if h and m:
            if h.currency != m["currency"]:
                reasons.append(f"currency changed {h.currency}->{m['currency']}")
            chg = pct_change(h.fair_value_local, m["fair_value"])
            entry["change_pct"] = chg
            entry["change_pct_usd"] = pct_change(h.fair_value_usd, entry["q4_fair_value_usd"])
            if chg is not None and abs(chg) >= threshold_pct:
                flags.append("R1")
                reasons.append(f"fair value moved {chg:+}% in {entry['currency']} vs Q3 (threshold {threshold_pct}%)")
            if h.method != m["valuation_method"]:
                flags.append("R2")
                reasons.append(f"method changed from '{h.method}' to '{m['valuation_method']}'")
        if company in held and m is None:
            flags.append("R3")
            if company in bought:
                reasons.append(f"no Q4 mark (bought {bought[company]['date']}); carried at cost USD "
                               f"{bought[company]['amount_usd']:,.2f} until first valuation"
                               + (f" - CVP note: {note_by_co[company]}" if company in note_by_co else ""))
            else:
                reasons.append("no Q4 mark for a stake held since Q3; carried at Q3 fair value pending CVP")
        if m is not None and company not in held:
            flags.append("R4")
            reasons.append("CVP supplied a mark but the fund does not hold this company at 31 Dec; excluded from NAV")
        if not flags and m is None and company in sold:
            reasons.append(f"sold {sold[company]['date']}; no Q4 mark expected, correctly absent")
        if not reasons:
            reasons.append("No flags: change within threshold and method unchanged")
        entry["flags"] = flags
        entry["reason"] = "; ".join(reasons)
        entries.append(entry)
    return entries
