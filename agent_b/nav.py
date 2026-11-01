"""Q4 NAV and the NAV bridge. Pure functions over Decimal; no I/O.

NAV(31 Dec) = cash + investments at fair value (USD) - liabilities
Bridge: NAV(30 Sep) + capital called - distributions + income - expenses
        + realized gain + change in unrealized gain = NAV(31 Dec)
The two sides are computed independently; `difference` is their gap and must be 0.00.
"""
from __future__ import annotations

from decimal import Decimal

from agent_b.data import Q3Data
from agent_b.review import held_at_q4
from common.money import ZERO, q2


class NAVError(ValueError):
    pass


def _sum(values) -> Decimal:
    return sum(values, ZERO)


def valuation_usd(q3: Q3Data, marks: list[dict], purchases: list[dict], sales: list[dict], eurusd: Decimal):
    """Fair value in USD and cost in USD of every stake held at 31 Dec.

    Rules: use CVP's mark (EUR converted at the ECB rate); a stake bought in Q4 with no
    mark is carried at cost (R3); R4 marks for companies not held are excluded."""
    held = held_at_q4(q3, purchases, sales)
    mark_by_co = {m["company"]: m for m in marks}
    bought = {p["company"]: p for p in purchases}
    lines = []
    for company in sorted(held):
        m = mark_by_co.get(company)
        if company in q3.holdings:
            cost = q3.holdings[company].cost_usd          # historic USD cost, never re-converted
        else:
            cost = bought[company]["amount_usd"]
        if m is not None:
            if m["currency"] == "USD":
                fv = q2(m["fair_value"])
            elif m["currency"] == "EUR":
                fv = q2(m["fair_value"] * eurusd)
            else:
                raise NAVError(f"Unsupported currency {m['currency']} for {company}")
            basis = f"CVP mark ({m['currency']})"
        elif company in bought:
            fv, basis = cost, "carried at cost (no Q4 mark yet)"
        else:
            fv, basis = q3.holdings[company].fair_value_usd, "carried at Q3 fair value (no Q4 mark)"
        lines.append({"company": company, "cost_usd": cost, "fair_value_usd": fv, "unrealized_gain": fv - cost,
                      "basis": basis})
    return lines


def compute_nav(q3: Q3Data, transactions: list[dict], marks: list[dict], purchases: list[dict],
                sales: list[dict], eurusd: Decimal) -> dict:
    by_cat: dict[str, Decimal] = {}
    for t in transactions:
        amt = q2(t["credit"]) - q2(t["debit"])           # +ve = cash in
        by_cat[t["category"]] = by_cat.get(t["category"], ZERO) + amt
    g = lambda c: by_cat.get(c, ZERO)

    capital_called = g("capital_call")
    distributions = -g("distribution")
    income = g("dividend_income") + g("interest_income")
    mgmt_fee = -g("management_fee")
    other_exp_net = -(g("expense") + g("expense_refund"))          # paid, net of refunds
    bank_charges = -g("bank_charges")
    accrued_q4 = q3.total_accruals_q4
    expenses = mgmt_fee + other_exp_net + bank_charges + accrued_q4
    settlements = -g("accrual_settlement")                          # cash out; NAV-neutral

    # realized gain = proceeds - cost of the stake sold
    sale_lines, realized = [], ZERO
    for s in sales:
        if s["company"] not in q3.holdings:
            raise NAVError(f"Sale of unknown holding {s['company']}")
        cost = q3.holdings[s["company"]].cost_usd
        gain = q2(s["proceeds_usd"]) - cost
        realized += gain
        sale_lines.append({"company": s["company"], "proceeds_usd": q2(s["proceeds_usd"]), "cost_usd": cost, "realized_gain": gain})

    lines = valuation_usd(q3, marks, purchases, sales, eurusd)
    investments = _sum(l["fair_value_usd"] for l in lines)
    unrealized_q4 = _sum(l["unrealized_gain"] for l in lines)
    unrealized_q3 = _sum(h.fair_value_usd - h.cost_usd for h in q3.holdings.values())
    change_unrealized = unrealized_q4 - unrealized_q3

    # --- position side ---
    closing_cash = q2(transactions[-1]["balance"])
    liabilities = accrued_q4
    nav_q4 = closing_cash + investments - liabilities

    # --- flow side (the bridge) ---
    bridge_total = q3.nav + capital_called - distributions + income - expenses + realized + change_unrealized
    difference = q2(nav_q4 - bridge_total)

    # independent cash proof: Q3 cash + every categorized movement == closing cash
    purchases_cash = -g("investment_purchase")
    cash_proof = q3.cash + capital_called - distributions + income - mgmt_fee - other_exp_net - bank_charges \
        - settlements + g("sale_proceeds") - purchases_cash
    cash_proof_ok = cash_proof == closing_cash

    return {
        "closing_cash": closing_cash,
        "investments_usd": investments,
        "liabilities": liabilities,
        "nav_q4": nav_q4,
        "management_fee": mgmt_fee,
        "bridge": {
            "nav_q3": q3.nav, "capital_called": capital_called, "distributions": distributions,
            "income": income, "expenses": expenses, "realized_gain": realized,
            "change_in_unrealized": change_unrealized, "nav_q4": bridge_total, "difference": difference,
        },
        "expense_breakdown": {"management_fee": mgmt_fee, "other_expenses_net_of_refunds": other_exp_net,
                              "bank_charges": bank_charges, "q4_accruals": accrued_q4, "total": expenses},
        "income_breakdown": {"dividends": g("dividend_income"), "interest": g("interest_income")},
        "unrealized": {"q3_total": unrealized_q3, "q4_total": unrealized_q4},
        "accrual_settlements_nav_neutral": settlements,
        "holdings_q4": lines,
        "realized_sales": sale_lines,
        "checks": {"bridge_balances": difference == ZERO, "cash_proof_ok": cash_proof_ok,
                   "cash_proof_value": cash_proof,
                   "q3_liabilities_fully_settled": settlements == q3.total_liabilities},
        "eurusd_rate": eurusd,
    }
