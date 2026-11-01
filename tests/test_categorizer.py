from decimal import Decimal

from agent_a import rules
from agent_a.categorizer import Categorizer, validate
from agent_a.llm import extract_json


def _raw(statement):
    return [dict(date=t.date, description=t.description, reference=t.reference, debit=t.debit, credit=t.credit,
                 balance=t.balance) for t in statement.transactions]


def make_llm(ref, wrong_first=(), wrong_always=()):
    """A fake chat function that answers with the rules, optionally getting some rows wrong."""
    calls = {"n": 0}

    def chat(system, user):
        calls["n"] += 1
        import json
        items = json.loads(user.split("Transactions:\n", 1)[1])
        res = []
        for it in items:
            txn = {"description": it["description"], "debit": it["amount"] if it["direction"] == "debit" else "0",
                   "credit": it["amount"] if it["direction"] == "credit" else "0"}
            ans = rules.classify(txn, ref)
            retry = "previous_answer_was_rejected_because" in it
            if any(w in it["description"] for w in wrong_always) or (
                    any(w in it["description"] for w in wrong_first) and not retry):
                ans = {"category": "capital_call", "lp_code": "LP01", "company": None}   # wrong: debit as capital_call
            res.append({"id": it["id"], **ans})
        return {"results": res}
    return chat, calls


def test_clean_run_uses_llm_for_every_row(statement, ref):
    chat, calls = make_llm(ref)
    out = Categorizer(chat, ref, batch_size=8).categorize(_raw(statement))
    assert {t["categorization_source"] for t in out} == {"llm"}
    assert calls["n"] == 5                                  # 39 rows / batches of 8
    assert all(t["rules_agree"] for t in out)


def test_invalid_answer_is_retried_with_the_error(statement, ref):
    chat, _ = make_llm(ref, wrong_first=("D&O PREMIUM",))
    out = Categorizer(chat, ref).categorize(_raw(statement))
    row = next(t for t in out if "D&O" in t["description"])
    assert row["category"] == "expense" and row["categorization_source"] == "llm"


def test_persistently_invalid_answer_falls_back_to_rules_and_is_labelled(statement, ref):
    chat, _ = make_llm(ref, wrong_always=("D&O PREMIUM",))
    out = Categorizer(chat, ref).categorize(_raw(statement))
    row = next(t for t in out if "D&O" in t["description"])
    assert row["category"] == "expense" and row["categorization_source"] == "rules_fallback"


def test_validate_rejects_direction_and_roster_errors(ref):
    credit = {"description": "x", "debit": Decimal("0"), "credit": Decimal("5")}
    debit = {"description": "x", "debit": Decimal("5"), "credit": Decimal("0")}
    assert validate({"category": "expense"}, credit, ref)[1]                       # expense must be a debit
    assert validate({"category": "capital_call", "lp_code": "LP99"}, credit, ref)[1]   # unknown LP
    assert validate({"category": "investment_purchase", "company": ""}, debit, ref)[1]  # company required
    assert validate({"category": "bogus"}, debit, ref)[1]
    ok, err = validate({"category": "distribution", "lp_code": "LP03", "company": "ignored"}, debit, ref)
    assert err is None and ok == {"category": "distribution", "lp_code": "LP03", "company": None}


def test_validate_resolves_sale_proceeds_portfolio_company(ref):
    txn = {
        "description": "INWARD WIRE NORDIC ACQUISITIONS AS SPA FJORD PAYMENTS AS COMPLETION",
        "debit": Decimal("0"),
        "credit": Decimal("29000000.00"),
    }
    ok, err = validate({"category": "sale_proceeds", "company": "Nordic Acquisitions AS Spa Fjord"}, txn, ref)
    assert err is None
    assert ok["company"] == "Fjord Payments AS"


def test_rules_resolve_investor_named_without_a_code(ref):
    assert rules.find_lp_code("INCOMING TT R MEHTA FAM TRST /BNF GREATNESS PE FUND/ CALL NO 7", ref) == "LP10"


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": 2} hope that helps') == {"a": 2}
