"""Parse the bank statement PDF into raw transactions.

Approach: use word coordinates (pdfplumber), not flat text. A transaction starts
on a line whose first word is a date in the Date column; following lines with no
date are wrapped description text. Amounts are assigned to the Debit / Credit /
Balance column by the x position of their RIGHT edge, then cross-checked against
the running balance (see reconcile.py), so a mis-assigned column cannot slip by.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

from common.money import ZERO, parse_money

DATE_RE = re.compile(r"^\d{2}$")
MONTH_RE = re.compile(r"^[A-Z][a-z]{2}$")
AMOUNT_RE = re.compile(r"^\d{1,3}(,\d{3})*\.\d{2}$")

# x-coordinates (PDF points) of the column boundaries, measured on this statement layout.
DATE_MAX_X0 = 98          # date words sit left of the description column
REF_MIN_X0, REF_MAX_X1 = 300, 375
DEBIT_MAX_X1 = 460        # right edge <= 460 -> Debit
CREDIT_MAX_X1 = 520       # 460..520 -> Credit ; beyond -> Balance


class StatementParseError(ValueError):
    pass


@dataclass
class RawTxn:
    date: str                 # YYYY-MM-DD
    description: str
    reference: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    balance: Decimal = ZERO
    page: int = 0


@dataclass
class Statement:
    account_no: str
    opening_balance: Decimal
    transactions: list[RawTxn] = field(default_factory=list)
    stated_total_debits: Decimal | None = None
    stated_total_credits: Decimal | None = None


def _group_lines(words, tol: float = 2.5):
    """Group words into visual lines by their top coordinate."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _parse_date(line) -> str | None:
    if len(line) >= 3 and line[0]["x0"] < DATE_MAX_X0:
        d, m, y = line[0]["text"], line[1]["text"], line[2]["text"]
        if DATE_RE.match(d) and MONTH_RE.match(m) and re.match(r"^\d{4}$", y):
            return datetime.strptime(f"{d} {m} {y}", "%d %b %Y").date().isoformat()
    return None


def _is_noise(text: str) -> bool:
    return (text.startswith("Meridian Trust Bank (Cayman) Ltd ·") or text.startswith("Date Description")
            or text.startswith("This statement is generated"))


def parse_statement(path: str | Path) -> Statement:
    stmt = Statement(account_no="", opening_balance=ZERO)
    current: RawTxn | None = None
    desc_parts: list[str] = []
    started = False

    def flush():
        nonlocal current, desc_parts
        if current is not None:
            current.description = " ".join(desc_parts).strip()
            stmt.transactions.append(current)
        current, desc_parts = None, []

    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for line in _group_lines(page.extract_words(keep_blank_chars=False, use_text_flow=False)):
                text = " ".join(w["text"] for w in line)

                if not started:
                    m = re.search(r"GPF-USD-\d+", text)
                    if m and not stmt.account_no:
                        stmt.account_no = m.group(0)
                    if _parse_date(line) is None:
                        continue
                    started = True

                if text.startswith("TOTAL DEBITS"):
                    flush()
                    amts = [parse_money(w["text"]) for w in line if AMOUNT_RE.match(w["text"])]
                    if len(amts) == 2:
                        stmt.stated_total_debits, stmt.stated_total_credits = amts
                    continue
                if _is_noise(text):
                    continue

                iso = _parse_date(line)
                if iso:
                    flush()
                    desc_words, ref_words = [], []
                    debit = credit = balance = None
                    for w in line[3:]:
                        t, x0, x1 = w["text"], w["x0"], w["x1"]
                        if AMOUNT_RE.match(t) and x1 > REF_MAX_X1:
                            amt = parse_money(t)
                            if x1 <= DEBIT_MAX_X1:
                                debit = amt
                            elif x1 <= CREDIT_MAX_X1:
                                credit = amt
                            else:
                                balance = amt
                        elif x0 >= REF_MIN_X0 and x1 <= REF_MAX_X1:
                            ref_words.append(t)
                        else:
                            desc_words.append(t)
                    if balance is None:
                        raise StatementParseError(f"No balance found on line: {text!r}")
                    current = RawTxn(date=iso, description="", reference=" ".join(ref_words),
                                     debit=debit or ZERO, credit=credit or ZERO, balance=balance, page=page_no)
                    desc_parts = [" ".join(desc_words)]
                elif current is not None:
                    # continuation of a wrapped description (may also carry a reference token)
                    desc_parts.append(" ".join(w["text"] for w in line if w["x0"] < REF_MIN_X0))
                    ref_extra = [w["text"] for w in line if REF_MIN_X0 <= w["x0"] and w["x1"] <= REF_MAX_X1]
                    if ref_extra and not current.reference:
                        current.reference = " ".join(ref_extra)
    flush()

    if not stmt.transactions:
        raise StatementParseError("No transactions found in statement")
    first = stmt.transactions[0]
    if "BALANCE BROUGHT FORWARD" not in first.description.upper():
        raise StatementParseError("First line is not the opening balance")
    stmt.opening_balance = first.balance
    stmt.transactions = stmt.transactions[1:]
    return stmt
