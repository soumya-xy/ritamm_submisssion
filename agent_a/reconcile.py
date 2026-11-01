"""Arithmetic proof of the parsed statement. Pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from agent_a.pdf_parser import Statement
from common.money import ZERO


class ReconciliationError(ValueError):
    pass


@dataclass
class ReconciliationReport:
    opening_balance: Decimal
    closing_balance: Decimal
    total_debits: Decimal
    total_credits: Decimal
    transaction_count: int
    checks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"opening_balance": self.opening_balance, "closing_balance": self.closing_balance,
                "total_debits": self.total_debits, "total_credits": self.total_credits,
                "transaction_count": self.transaction_count, "checks": self.checks}


def reconcile(stmt: Statement) -> ReconciliationReport:
    """Raises ReconciliationError on any inconsistency.

    1. every running balance = previous balance - debit + credit
    2. every row has exactly one of debit/credit
    3. opening + credits - debits = last running balance (= closing balance)
    4. computed totals equal the totals printed on the statement
    """
    problems: list[str] = []
    prev = stmt.opening_balance
    for i, t in enumerate(stmt.transactions, start=1):
        if (t.debit > 0) == (t.credit > 0):
            problems.append(f"row {i} ({t.date}): needs exactly one of debit/credit, got {t.debit}/{t.credit}")
        expected = prev - t.debit + t.credit
        if expected != t.balance:
            problems.append(f"row {i} ({t.date}): running balance {t.balance} != expected {expected}")
        prev = t.balance

    total_debits = sum((t.debit for t in stmt.transactions), ZERO)
    total_credits = sum((t.credit for t in stmt.transactions), ZERO)
    closing = stmt.opening_balance + total_credits - total_debits
    if stmt.transactions and closing != stmt.transactions[-1].balance:
        problems.append(f"opening + movements = {closing} but last running balance = {stmt.transactions[-1].balance}")
    if stmt.stated_total_debits is not None and stmt.stated_total_debits != total_debits:
        problems.append(f"sum of debits {total_debits} != statement total {stmt.stated_total_debits}")
    if stmt.stated_total_credits is not None and stmt.stated_total_credits != total_credits:
        problems.append(f"sum of credits {total_credits} != statement total {stmt.stated_total_credits}")
    if problems:
        raise ReconciliationError("; ".join(problems))

    return ReconciliationReport(
        opening_balance=stmt.opening_balance, closing_balance=closing, total_debits=total_debits,
        total_credits=total_credits, transaction_count=len(stmt.transactions),
        checks={"running_balance_each_row": True, "opening_plus_movements_equals_closing": True,
                "matches_statement_printed_totals": stmt.stated_total_debits is not None})
