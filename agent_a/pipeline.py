"""Agent A's work, end to end, as plain functions: parse -> reconcile -> categorize -> derive."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from agent_a.categorizer import Categorizer
from agent_a.pdf_parser import parse_statement
from agent_a.reconcile import reconcile
from agent_a.reference import load_reference
from common.money import ZERO

log = logging.getLogger("agent_a.pipeline")


class IngestionError(RuntimeError):
    pass


@dataclass
class IngestionResult:
    transactions: list[dict]
    purchases: list[dict]
    sales: list[dict]
    reconciliation: dict
    checks: dict


def find_statement(pack_dir: Path) -> Path:
    hits = sorted(Path(pack_dir).glob("*Bank_Statement*.pdf"))
    if not hits:
        raise IngestionError(f"No bank statement PDF (*Bank_Statement*.pdf) in {pack_dir}")
    return hits[0]


def derive_purchases_sales(txns: list[dict]) -> tuple[list[dict], list[dict]]:
    purchases = [{"company": t["company"], "date": t["date"], "amount_usd": t["debit"]}
                 for t in txns if t["category"] == "investment_purchase"]
    sales = [{"company": t["company"], "date": t["date"], "proceeds_usd": t["credit"]}
             for t in txns if t["category"] == "sale_proceeds"]
    return purchases, sales


def semantic_checks(txns: list[dict], ref) -> dict:
    """Sanity checks on the CATEGORIZATION (the arithmetic is proven separately by reconcile())."""
    calls: dict[str, Decimal] = {}
    for t in txns:
        if t["category"] == "capital_call":
            calls[t["lp_code"]] = calls.get(t["lp_code"], ZERO) + t["credit"]
    settled = sum((t["debit"] for t in txns if t["category"] == "accrual_settlement"), ZERO)
    open_total = sum((l["amount"] for l in ref.open_liabilities), ZERO)
    over = {lp: str(amt) for lp, amt in calls.items() if amt > ref.lp_roster[lp]["commitment"]}
    return {
        "every_row_categorized": all(t.get("category") for t in txns),
        "accrual_settlements_equal_q3_liabilities": settled == open_total,
        "accrual_settlements_total": settled, "q3_open_liabilities_total": open_total,
        "capital_calls_within_commitments": not over, "over_commitment": over,
        "llm_rules_disagreements": [
            {"date": t["date"], "description": t["description"], "llm_category": t["category"]}
            for t in txns if not t.get("rules_agree", True)],
        "rows_using_rules_fallback": sum(1 for t in txns if t.get("categorization_source") == "rules_fallback"),
    }


def ingest(pack_dir: Path, chat_json: Callable[[str, str], dict], batch_size: int = 8) -> IngestionResult:
    ref = load_reference(pack_dir)
    stmt = parse_statement(find_statement(pack_dir))
    report = reconcile(stmt)
    log.info("Parsed %d transactions; reconciliation OK (closing %s)", len(stmt.transactions), report.closing_balance)

    raw = [{"date": t.date, "description": t.description, "reference": t.reference,
            "debit": t.debit, "credit": t.credit, "balance": t.balance} for t in stmt.transactions]
    txns = Categorizer(chat_json, ref, batch_size).categorize(raw)
    purchases, sales = derive_purchases_sales(txns)
    checks = semantic_checks(txns, ref)
    for name in ("accrual_settlements_equal_q3_liabilities", "capital_calls_within_commitments"):
        if not checks[name]:
            log.warning("Semantic check failed: %s", name)
    return IngestionResult(txns, purchases, sales, report.as_dict(), checks)
