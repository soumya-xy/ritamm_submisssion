"""Reference data Agent A needs to categorize correctly: the LP roster and the
liabilities that were open at 30 Sep (so it can tell an accrual settlement from
a new expense). Read from the Q3 net-asset summary in the pack."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import openpyxl

from common.money import q2

NAV_SUMMARY = "GPF_NAV_Summary_Q3_2022.xlsx"


@dataclass
class ReferenceData:
    lp_roster: dict[str, dict] = field(default_factory=dict)   # code -> {name, commitment}
    open_liabilities: list[dict] = field(default_factory=list)  # [{label, amount}]
    known_companies: list[str] = field(default_factory=list)


def load_reference(pack_dir: Path) -> ReferenceData:
    wb = openpyxl.load_workbook(Path(pack_dir) / NAV_SUMMARY, data_only=False)
    ws = wb.worksheets[0]
    ref = ReferenceData()
    section = None
    for row in ws.iter_rows(values_only=True):
        a, b, c = (list(row) + [None] * 3)[:3]
        if isinstance(a, str):
            if a.strip() == "Liabilities":
                section = "liab"; continue
            if a.startswith("Net asset value"):
                section = None; continue
            if a.startswith("Portfolio company"):
                section = "inv"; continue
            if a.startswith("Investor (LP)"):
                section = "lp"; continue
            if a.startswith("Total"):
                if section in ("liab", "inv", "lp"):
                    section = None
                continue
        if section == "liab" and isinstance(a, str) and isinstance(b, (int, float)):
            ref.open_liabilities.append({"label": a, "amount": q2(b)})
        elif section == "inv" and isinstance(a, str) and isinstance(b, (int, float)):
            ref.known_companies.append(a)
        elif section == "lp" and isinstance(a, str) and isinstance(b, str) and b.startswith("LP"):
            ref.lp_roster[b] = {"name": a, "commitment": q2(c)}
    if not ref.lp_roster:
        raise ValueError("LP roster not found in NAV summary")
    return ref
