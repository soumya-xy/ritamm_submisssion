from datetime import date
from decimal import Decimal as D

import pytest

from agent_b import fx, nav, review
from agent_b.close import close_quarter
from agent_b.data import load_q3, load_q4


@pytest.fixture(scope="module")
def q3(pack):
    return load_q3(pack)


@pytest.fixture(scope="module")
def q4(pack):
    return load_q4(pack)


@pytest.fixture(scope="module")
def closed(pack, rule_txns, q4):
    return close_quarter(pack, rule_txns, {"marks": q4.marks, "notes": q4.notes}, "15")


def test_fx_uses_last_ecb_fixing_before_a_weekend():
    assert date(2022, 12, 31).weekday() == 5                       # Saturday
    r = fx.eurusd_for("2022-12-31")
    assert (r.rate_date, r.eurusd) == ("2022-12-30", D("1.0666"))
    assert fx.eurusd_for("2022-09-30").eurusd == D("0.9748")       # the rate CVP used in Q3


def test_q3_files_are_internally_consistent(q3):
    assert q3.nav == D("160224800.00") and q3.total_liabilities == D("185000.00")
    assert q3.holdings["Ember Retail GmbH"].cost_usd == D("14184000.00")   # not re-converted


def test_golden_nav(closed):
    _, n = closed
    assert n["closing_cash"] == D("23896579.25")
    assert n["investments_usd"] == D("146062460.00")
    assert n["liabilities"] == D("195000.00")
    assert n["nav_q4"] == D("169764039.25")
    b = n["bridge"]
    assert (b["capital_called"], b["distributions"], b["income"], b["expenses"]) == (
        D("20000000.00"), D("22500000.00"), D("459429.25"), D("1522850.00"))
    assert (b["realized_gain"], b["change_in_unrealized"]) == (D("9000000.00"), D("4102660.00"))
    assert b["difference"] == D("0.00") and n["checks"]["bridge_balances"] and n["checks"]["cash_proof_ok"]


def test_accrual_settlements_are_nav_neutral(closed):
    _, n = closed
    assert n["accrual_settlements_nav_neutral"] == D("185000.00")
    assert n["expense_breakdown"]["q4_accruals"] == D("195000.00")     # only the NEW accruals hit expenses


def test_miscategorizing_a_settlement_breaks_the_bridge(pack, rule_txns, q4):
    broken = [dict(t, category="expense") if t["category"] == "accrual_settlement" else t for t in rule_txns]
    _, n = close_quarter(pack, broken, {"marks": q4.marks, "notes": q4.notes}, "15")
    assert n["bridge"]["difference"] == D("185000.00") and not n["checks"]["bridge_balances"]


def test_review_flags(closed):
    r, _ = closed
    flags = {e["company"]: e["flags"] for e in r["companies"]}
    assert flags["Bluetarn Software Inc"] == ["R1"] and flags["Cedar Health Holdings"] == ["R1"]
    assert flags["Harbor Analytics Inc"] == ["R1"]
    assert flags["Delta Agritech Ltd"] == ["R2"]
    assert flags["Ionmere Materials Ltd"] == ["R3"]
    assert flags["Aurora Logistics Pvt Ltd"] == flags["Granite Energy Corp"] == flags["Fjord Payments AS"] == []
    assert r["threshold_pct"] == D("15") and r["eurusd_rate"] == D("1.0666")


def test_r1_is_measured_in_local_currency_not_usd(closed, q3, q4):
    r, _ = closed
    ember = next(e for e in r["companies"] if e["company"] == "Ember Retail GmbH")
    # EUR value fell 2.96%, but the USD value "rose" 6.18% purely because EUR/USD moved from 0.9748 to 1.0666
    assert ember["change_pct"] == D("-2.96") and ember["change_pct_usd"] == D("6.18")
    # With a 5% threshold a USD-based test would wrongly flag Ember; the local-currency test must not.
    low = review.review_marks(q3, q4.marks, q4.notes, q4.purchases, q4.sales, D("1.0666"), D("5"))
    assert next(e for e in low if e["company"] == "Ember Retail GmbH")["flags"] == []


def test_threshold_is_configurable(pack, q3, q4):
    at20 = review.review_marks(q3, q4.marks, q4.notes, q4.purchases, q4.sales, D("1.0666"), D("20"))
    assert [e["company"] for e in at20 if "R1" in e["flags"]] == ["Bluetarn Software Inc", "Cedar Health Holdings"]


def test_r3_new_purchase_is_carried_at_cost_not_dropped(closed):
    _, n = closed
    ion = next(l for l in n["holdings_q4"] if l["company"] == "Ionmere Materials Ltd")
    assert ion["fair_value_usd"] == ion["cost_usd"] == D("14000000.00") and "cost" in ion["basis"]


def test_r4_mark_for_unheld_company_is_excluded_and_flagged(q3, q4):
    extra = q4.marks + [{"company": "Fjord Payments AS", "currency": "USD", "fair_value": D("30000000.00"),
                         "valuation_method": "Discounted cash flow", "valuation_date": "2022-12-31"}]
    rev = review.review_marks(q3, extra, q4.notes, q4.purchases, q4.sales, D("1.0666"), D("15"))
    assert "R4" in next(e for e in rev if e["company"] == "Fjord Payments AS")["flags"]
    lines = nav.valuation_usd(q3, extra, q4.purchases, q4.sales, D("1.0666"))
    assert "Fjord Payments AS" not in [l["company"] for l in lines]


def test_eur_mark_converted_at_ecb_rate(closed):
    _, n = closed
    ember = next(l for l in n["holdings_q4"] if l["company"] == "Ember Retail GmbH")
    assert ember["fair_value_usd"] == D("13972460.00")       # EUR 13,100,000 x 1.0666
    assert ember["cost_usd"] == D("14184000.00")
