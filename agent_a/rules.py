"""Deterministic keyword rules.

These are NOT the primary classifier (the brief requires an LLM). They serve as
  (1) an independent cross-check on every LLM answer, and
  (2) a last-resort fallback for an individual row whose LLM answer fails validation.
"""
from __future__ import annotations

import re

from agent_a.reference import ReferenceData
from common.money import D

CATEGORIES = [
    "capital_call", "distribution", "management_fee", "investment_purchase", "sale_proceeds",
    "dividend_income", "interest_income", "bank_charges", "expense", "expense_refund", "accrual_settlement",
]
LP_CATEGORIES = {"capital_call", "distribution"}
COMPANY_CATEGORIES = {"investment_purchase", "sale_proceeds"}
DEBIT_CATEGORIES = {"distribution", "management_fee", "investment_purchase", "bank_charges", "expense",
                    "accrual_settlement"}
CREDIT_CATEGORIES = {"capital_call", "sale_proceeds", "dividend_income", "interest_income", "expense_refund"}

_UPPER_TOKENS = {"AS", "AG", "NV", "BV", "SA", "AB", "LLC", "LLP", "LP", "PLC", "SE", "OY", "ASA"}
_SPECIAL = {"GMBH": "GmbH", "PVT": "Pvt", "LTD": "Ltd", "INC": "Inc", "CORP": "Corp"}

LP_CODE_RE = re.compile(r"\bLP(\d{2})\b")


def pretty_company(raw: str, known: list[str]) -> str:
    """Return the canonical spelling: a known company if it matches, else tidy title case."""
    norm = " ".join(raw.split()).casefold()
    for k in known:
        if k.casefold() == norm:
            return k
    for k in known:
        if k.casefold() in norm:
            return k
    words = set(re.findall(r"[a-z0-9]+", norm))
    matches = [k for k in known if k.split()[0].casefold() in words]
    if len(matches) == 1:
        return matches[0]
    out = []
    for tok in raw.split():
        up = tok.upper()
        out.append(up if up in _UPPER_TOKENS else _SPECIAL.get(up, tok.capitalize()))
    return " ".join(out)


def _name_tokens(name: str) -> set[str]:
    stop = {"the", "of", "co", "ltd", "inc", "board", "fund", "family", "office"}
    return {t for t in re.findall(r"[a-z]+", name.casefold()) if t not in stop and len(t) > 1}


def find_lp_code(text: str, ref: ReferenceData) -> str | None:
    m = LP_CODE_RE.search(text.upper())
    if m and f"LP{m.group(1)}" in ref.lp_roster:
        return f"LP{m.group(1)}"
    # No explicit code: match investor names. A word that appears in exactly one roster name
    # (e.g. "mehta") is distinctive enough on its own; otherwise require two shared words.
    words = set(re.findall(r"[a-z]+", text.casefold()))
    token_owners: dict[str, list[str]] = {}
    for code, info in ref.lp_roster.items():
        for tok in _name_tokens(info["name"]):
            token_owners.setdefault(tok, []).append(code)
    best, best_score = None, 0
    for code, info in ref.lp_roster.items():
        shared = _name_tokens(info["name"]) & words
        score = len(shared) + sum(2 for t in shared if len(token_owners[t]) == 1 and len(t) >= 4)
        if score > best_score:
            best, best_score = code, score
    return best if best_score >= 2 else None


def classify(txn: dict, ref: ReferenceData) -> dict:
    """txn needs: description, debit, credit. Returns {category, lp_code, company}."""
    desc = txn["description"].upper()
    debit, credit = D(txn["debit"]), D(txn["credit"])
    cat, lp, company = "expense", None, None

    if credit > 0:
        if "CC" in desc.split() and LP_CODE_RE.search(desc) or "CALL NO" in desc:
            cat = "capital_call"
        elif "DIVIDEND" in desc:
            cat = "dividend_income"
        elif "INTEREST" in desc:
            cat = "interest_income"
        elif "REFUND" in desc:
            cat = "expense_refund"
        elif "SPA" in desc.split() or "COMPLETION" in desc:
            cat = "sale_proceeds"
        else:
            cat = "capital_call" if find_lp_code(desc, ref) else "expense_refund"
    else:
        if "DISTRIBUTION" in desc:
            cat = "distribution"
        elif "MGMT FEE" in desc or "MANAGEMENT FEE" in desc:
            cat = "management_fee"
        elif "MAINTENANCE FEE" in desc or "WIRE CHARGES" in desc:
            cat = "bank_charges"
        elif "SUBSCRIPTION" in desc:
            cat = "investment_purchase"
        else:
            cat = "expense"
            for liab in ref.open_liabilities:
                payee = re.findall(r"\(([^)]+)\)", liab["label"])
                if payee and payee[0].split()[0].upper().rstrip(",") in desc and D(liab["amount"]) == debit:
                    cat = "accrual_settlement"
                elif "Harbourside" in liab["label"] and "HARBOURSIDE" in desc and D(liab["amount"]) == debit:
                    cat = "accrual_settlement"

    if cat in LP_CATEGORIES:
        lp = find_lp_code(desc, ref)
    if cat == "investment_purchase":
        m = re.search(r"OUTWARD WIRE (.+?) SUBSCRIPTION", desc)
        company = pretty_company(m.group(1), ref.known_companies) if m else None
    elif cat == "sale_proceeds":
        for k in ref.known_companies:
            if k.upper() in desc:
                company = k
                break
        if company is None:
            m = re.search(r"SPA (.+?) COMPLETION", desc)
            company = pretty_company(m.group(1), ref.known_companies) if m else None
    return {"category": cat, "lp_code": lp, "company": company}
