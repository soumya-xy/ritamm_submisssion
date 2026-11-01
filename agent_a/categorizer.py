"""LLM categorization with code-side validation.

Flow per batch: LLM answers -> every answer validated against hard rules
(allowed category, debit/credit direction, LP code exists, company present) ->
invalid rows are re-asked once with the exact error -> still invalid rows fall
back to the deterministic rules and are marked as such. Every LLM answer is also
compared with the independent keyword rules; disagreements are recorded.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from agent_a import rules
from agent_a.reference import ReferenceData
from agent_a.rules import CATEGORIES, COMPANY_CATEGORIES, CREDIT_CATEGORIES, DEBIT_CATEGORIES, LP_CATEGORIES
from common.money import D

log = logging.getLogger("agent_a.categorizer")

SYSTEM_TEMPLATE = """You are a fund-accounting assistant classifying bank transactions for a private equity fund's USD operating account.

Classify EACH transaction into exactly one category:
- capital_call: money RECEIVED from an investor (LP) paying a capital call. Always a credit. Give lp_code.
- distribution: cash PAID to an investor (LP). Always a debit. Give lp_code.
- management_fee: fee paid to the fund's manager/adviser (Greatness Capital Advisors). Debit.
- investment_purchase: payment to acquire / subscribe for shares in a portfolio company. Debit. Give company.
- sale_proceeds: money received for the sale of a portfolio company. Credit. Give company (the portfolio company SOLD, not the buyer).
- dividend_income: dividend received from a portfolio company. Credit.
- interest_income: interest credited by the bank. Credit.
- bank_charges: bank account maintenance fees and wire charges. Debit.
- expense: any other operating cost paid in the quarter (legal, insurance, audit for the current period, etc.). Debit. An expense that covers a later period (e.g. a 2023 premium) is STILL an expense.
- expense_refund: a refund received for an expense previously paid. Credit.
- accrual_settlement: a payment that settles a liability ALREADY RECORDED at 30 September (list below). Match on payee, the period it covers and the amount. Debit.

Liabilities open at 30 September 2022 (a payment settling one of these is an accrual_settlement, NOT an expense):
{liabilities}

Investor roster (code - name). Some wires show only the investor's name, not the code - use this list to find the code:
{roster}

Portfolio companies held by the fund at 30 September 2022:
{companies}

Rules:
- lp_code is required for capital_call and distribution, otherwise null. It must be one of the roster codes.
- company is required for investment_purchase and sale_proceeds, otherwise null. For sale_proceeds, the company SOLD must be one of the portfolio companies listed above (e.g. "Fjord Payments AS"), NOT the buyer or wire intermediary (e.g. "Nordic Acquisitions AS") or transaction terms (e.g. "SPA"). For investment_purchase, use the company's proper name (e.g. "Ionmere Materials Ltd").
- Use the debit/credit direction given; it is reliable.
Reply with ONLY a JSON object: {{"results": [{{"id": <int>, "category": "<category>", "lp_code": <string or null>, "company": <string or null>}}, ...]}} with one entry per input id."""


def build_system_prompt(ref: ReferenceData) -> str:
    liabilities = "\n".join(f"- {l['label']}: USD {l['amount']}" for l in ref.open_liabilities) or "- (none)"
    roster = "\n".join(f"- {code} - {info['name']}" for code, info in sorted(ref.lp_roster.items()))
    companies = "\n".join(f"- {c}" for c in sorted(ref.known_companies)) or "- (none)"
    return SYSTEM_TEMPLATE.format(liabilities=liabilities, roster=roster, companies=companies)


def validate(item: dict, txn: dict, ref: ReferenceData) -> tuple[dict | None, str | None]:
    """Return (clean_answer, None) or (None, error_message)."""
    cat = item.get("category")
    if cat not in CATEGORIES:
        return None, f"category {cat!r} is not one of the allowed categories"
    debit, credit = D(txn["debit"]), D(txn["credit"])
    if cat in DEBIT_CATEGORIES and not debit > 0:
        return None, f"{cat} must be a debit but this transaction is a credit"
    if cat in CREDIT_CATEGORIES and not credit > 0:
        return None, f"{cat} must be a credit but this transaction is a debit"
    lp = item.get("lp_code")
    company = item.get("company")
    if cat in LP_CATEGORIES:
        if lp not in ref.lp_roster:
            return None, f"lp_code {lp!r} is not in the investor roster"
    else:
        lp = None
    if cat in COMPANY_CATEGORIES:
        if not (isinstance(company, str) and company.strip()):
            return None, f"{cat} requires a company name"
        company = rules.pretty_company(company.strip(), ref.known_companies)
        if cat == "sale_proceeds" and ref.known_companies:
            if company not in ref.known_companies:
                desc = txn.get("description", "")
                desc_words = set(re.findall(r"[a-z0-9]+", desc.casefold()))
                matches = [k for k in ref.known_companies if k.upper() in desc.upper() or k.split()[0].casefold() in desc_words]
                if len(matches) == 1:
                    company = matches[0]
                else:
                    return None, f"sale_proceeds company {company!r} is not in the portfolio companies list ({ref.known_companies})"
    else:
        company = None
    return {"category": cat, "lp_code": lp, "company": company}, None


class Categorizer:
    def __init__(self, chat_json: Callable[[str, str], dict], ref: ReferenceData, batch_size: int = 8):
        self.chat_json, self.ref, self.batch_size = chat_json, ref, max(1, batch_size)
        self.system = build_system_prompt(ref)

    def _ask(self, rows: list[tuple[int, dict]], hints: dict[int, str] | None = None) -> dict[int, dict]:
        payload = []
        for idx, t in rows:
            entry = {"id": idx, "date": t["date"], "description": t["description"], "reference": t["reference"],
                     "direction": "debit" if D(t["debit"]) > 0 else "credit",
                     "amount": str(D(t["debit"]) if D(t["debit"]) > 0 else D(t["credit"]))}
            if hints and idx in hints:
                entry["previous_answer_was_rejected_because"] = hints[idx]
            payload.append(entry)
        from common import jsonio
        reply = self.chat_json(self.system, "Transactions:\n" + jsonio.dumps(payload, indent=1))
        out = {}
        for r in reply.get("results", []):
            try:
                out[int(r["id"])] = r
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def categorize(self, txns: list[dict]) -> list[dict]:
        """Return the txns enriched with category, lp_code, company and provenance fields."""
        results: dict[int, dict] = {}
        for start in range(0, len(txns), self.batch_size):
            batch = [(i, txns[i]) for i in range(start, min(start + self.batch_size, len(txns)))]
            pending, hints = batch, {}
            for attempt in (1, 2):
                answers = self._ask(pending, hints)
                still = []
                for idx, t in pending:
                    clean, err = (validate(answers[idx], t, self.ref) if idx in answers
                                  else (None, "no answer returned for this id"))
                    if clean:
                        results[idx] = {**clean, "categorization_source": "llm"}
                    else:
                        hints[idx] = err
                        still.append((idx, t))
                pending = still
                if not pending:
                    break
            for idx, t in pending:      # last resort: deterministic rules, clearly labelled
                log.warning("LLM answer invalid for row %s (%s); using rules fallback", idx, hints.get(idx))
                results[idx] = {**rules.classify(t, self.ref), "categorization_source": "rules_fallback"}

        enriched = []
        for i, t in enumerate(txns):
            r = dict(results[i])
            rule = rules.classify(t, self.ref)
            r["rules_agree"] = (rule["category"] == r["category"] and rule["lp_code"] == r["lp_code"]
                                and (rule["company"] or "").casefold() == (r["company"] or "").casefold())
            enriched.append({**t, **r})
        return enriched
