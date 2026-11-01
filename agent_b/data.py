"""Loaders for everything Agent B owns: CVP's Q4 data and the Q3 files."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import openpyxl

from common import jsonio
from common.money import D, q2

VALUATION_Q3 = "CVP_Valuation_Report_Q3_2022.xlsx"
NAV_SUMMARY = "GPF_NAV_Summary_Q3_2022.xlsx"
CVP_Q4 = "agent_b_data.json"


@dataclass
class Q3Holding:
    company: str
    currency: str
    cost_local: Decimal
    fair_value_local: Decimal
    fx_to_usd: Decimal
    fair_value_usd: Decimal
    method: str
    cost_usd: Decimal            # from the NAV summary (Ember cost stays at the historic USD figure)


@dataclass
class Q3Data:
    holdings: dict[str, Q3Holding]
    cash: Decimal
    investments_fv: Decimal
    liabilities: dict[str, Decimal]
    nav: Decimal
    accruals_q4: dict[str, Decimal]
    commitments: dict[str, Decimal] = field(default_factory=dict)

    @property
    def total_liabilities(self) -> Decimal:
        return sum(self.liabilities.values(), Decimal("0.00"))

    @property
    def total_accruals_q4(self) -> Decimal:
        return sum(self.accruals_q4.values(), Decimal("0.00"))


@dataclass
class Q4Data:
    fund_name: str
    fund_code: str
    valuation_date: str
    marks: list[dict]
    notes: list[dict]
    purchases: list[dict]
    sales: list[dict]


def load_q4(pack: Path) -> Q4Data:
    raw = jsonio.loads((Path(pack) / CVP_Q4).read_text(encoding="utf-8"))
    return Q4Data(
        fund_name=raw["fund"]["name"], fund_code=raw["fund"]["code"], valuation_date=raw["valuation_date"],
        marks=[{**m, "fair_value": q2(m["fair_value"])} for m in raw["marks"]],
        notes=raw.get("notes", []),
        purchases=[{**p, "amount_usd": q2(p["amount_usd"])} for p in raw.get("purchases", [])],
        sales=[{**s, "proceeds_usd": q2(s["proceeds_usd"])} for s in raw.get("sales", [])],
    )


def load_q3(pack: Path) -> Q3Data:
    pack = Path(pack)
    val = openpyxl.load_workbook(pack / VALUATION_Q3, data_only=False).worksheets[0]
    nav_wb = openpyxl.load_workbook(pack / NAV_SUMMARY, data_only=False)
    nav = nav_wb.worksheets[0]

    # cost in USD per company comes from the NAV summary (historic USD cost; never re-converted)
    cost_usd, section = {}, None
    cash = inv_fv = nav_total = None
    liabilities: dict[str, Decimal] = {}
    commitments: dict[str, Decimal] = {}
    for a, b, c, *_ in (list(r) + [None] * 4 for r in nav.iter_rows(values_only=True)):
        if not isinstance(a, str):
            continue
        if a.startswith("Cash at bank"):
            cash = q2(b)
        elif a.startswith("Investments at fair value"):
            inv_fv = q2(b)
        elif a.strip() == "Liabilities":
            section = "liab"
        elif a.startswith("Net asset value"):
            nav_total, section = q2(b), None
        elif a.startswith("Portfolio company"):
            section = "inv"
        elif a.startswith("Investor (LP)"):
            section = "lp"
        elif a.startswith("Total"):
            section = None if section in ("liab", "inv", "lp") else section
        elif section == "liab" and isinstance(b, (int, float)):
            liabilities[a] = q2(b)
        elif section == "inv" and isinstance(b, (int, float)):
            cost_usd[a] = q2(b)
        elif section == "lp" and isinstance(b, str):
            commitments[b] = q2(c)

    holdings: dict[str, Q3Holding] = {}
    for row in val.iter_rows(min_row=6, values_only=True):
        name = row[0]
        if not isinstance(name, str) or name.startswith("Total") or row[1] is None:
            continue
        holdings[name] = Q3Holding(
            company=name, currency=row[1], cost_local=q2(row[2]), fair_value_local=q2(row[3]),
            fx_to_usd=D(row[4]), fair_value_usd=q2(row[5]), method=row[6], cost_usd=cost_usd[name])

    acc = nav_wb.worksheets[1]
    accruals_q4 = {}
    for a, b, *_ in (list(r) + [None] * 2 for r in acc.iter_rows(values_only=True)):
        if isinstance(a, str) and isinstance(b, (int, float)) and not a.startswith("Total"):
            accruals_q4[a] = q2(b)

    data = Q3Data(holdings=holdings, cash=cash, investments_fv=inv_fv, liabilities=liabilities,
                  nav=nav_total, accruals_q4=accruals_q4, commitments=commitments)
    _check_q3(data)
    return data


def _check_q3(d: Q3Data) -> None:
    """The Q3 files must agree with themselves before we build on them."""
    fv = sum((h.fair_value_usd for h in d.holdings.values()), Decimal("0.00"))
    if fv != d.investments_fv:
        raise ValueError(f"Q3 valuation report FV total {fv} != NAV summary investments {d.investments_fv}")
    if d.cash + d.investments_fv - d.total_liabilities != d.nav:
        raise ValueError("Q3 NAV summary does not foot (cash + investments - liabilities != NAV)")
