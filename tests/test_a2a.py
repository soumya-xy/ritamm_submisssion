from decimal import Decimal

import pytest

from agent_b.data import load_q4
from agent_b.handler import MAX_ATTEMPTS, ValuationHandler, match_counts
from common import a2a


@pytest.fixture
def handler(pack):
    return ValuationHandler(load_q4(pack))


def rpc(handler, message, method="message/send"):
    return handler.handle_rpc(a2a.rpc_request(message) | {"method": method})


def open_task(handler):
    res = rpc(handler, a2a.make_message("user", [a2a.data_part({"fund": "GPF", "valuation_date": "2022-12-31"})]))
    return res["result"]


def answer(task, purchases, sales):
    return a2a.make_message("user", [a2a.data_part({"purchases": purchases, "sales": sales})], task["id"], task["contextId"])


GOOD_P = [{"company": "Ionmere Materials Ltd", "date": "2022-12-15", "amount_usd": Decimal("14000000.00")}]
GOOD_S = [{"company": "Fjord Payments AS", "date": "2022-11-18", "proceeds_usd": Decimal("29000000.00")}]


def test_first_turn_asks_a_question_and_withholds_marks(handler):
    task = open_task(handler)
    assert task["status"]["state"] == "input-required"
    assert task["id"] and task["contextId"] and "artifacts" not in task
    assert "purchases and sales" in task["status"]["message"]["parts"][0]["text"]


def test_matching_lists_release_marks(handler):
    task = open_task(handler)
    res = rpc(handler, answer(task, GOOD_P, GOOD_S))["result"]
    assert res["status"]["state"] == "completed"
    data = res["artifacts"][0]["parts"][0]["data"]
    assert len(data["marks"]) == 7 and data["notes"][0]["company"] == "Ionmere Materials Ltd"
    assert res["id"] == task["id"] and res["contextId"] == task["contextId"]


def test_matching_is_case_insensitive_and_exact_to_the_cent(handler):
    task = open_task(handler)
    p = [{"company": "IONMERE MATERIALS LTD", "date": "2022-12-15", "amount_usd": 14000000.0}]
    assert rpc(handler, answer(task, p, GOOD_S))["result"]["status"]["state"] == "completed"
    task = open_task(handler)
    off_by_a_cent = [{**GOOD_P[0], "amount_usd": Decimal("14000000.01")}]
    res = rpc(handler, answer(task, off_by_a_cent, GOOD_S))["result"]
    assert res["status"]["state"] == "input-required" and "artifacts" not in res


def test_mismatch_reports_how_many_matched_and_does_not_leak_records(handler):
    task = open_task(handler)
    res = rpc(handler, answer(task, GOOD_P, []))["result"]
    text = res["status"]["message"]["parts"][0]["text"]
    assert res["status"]["state"] == "input-required" and "Matched 1 of 2" in text
    assert "29000000" not in text and "Fjord" not in text


def test_extra_item_blocks_release(handler):
    task = open_task(handler)
    extra = GOOD_S + [{"company": "Ghost Co", "date": "2022-12-01", "proceeds_usd": 1}]
    assert rpc(handler, answer(task, GOOD_P, extra))["result"]["status"]["state"] == "input-required"


def test_gives_up_after_max_attempts(handler):
    task = open_task(handler)
    state = None
    for _ in range(MAX_ATTEMPTS):
        state = rpc(handler, answer(task, [], []))["result"]["status"]["state"]
    assert state == "failed"


def test_protocol_errors(handler):
    assert rpc(handler, a2a.make_message("user", []), method="tasks/get")["error"]["code"] == a2a.METHOD_NOT_FOUND
    bad_task = a2a.make_message("user", [a2a.data_part({})], "task-nope", "ctx")
    assert rpc(handler, bad_task)["error"]["code"] == a2a.TASK_NOT_FOUND
    wrong_fund = a2a.make_message("user", [a2a.data_part({"fund": "XYZ", "valuation_date": "2022-12-31"})])
    assert rpc(handler, wrong_fund)["error"]["code"] == a2a.INVALID_PARAMS
    wrong_date = a2a.make_message("user", [a2a.data_part({"fund": "GPF", "valuation_date": "2023-03-31"})])
    assert rpc(handler, wrong_date)["error"]["code"] == a2a.INVALID_PARAMS
    assert handler.handle_rpc({"nonsense": 1})["error"]["code"] == a2a.INVALID_REQUEST


def test_completed_task_cannot_be_reused(handler):
    task = open_task(handler)
    rpc(handler, answer(task, GOOD_P, GOOD_S))
    assert "error" in rpc(handler, answer(task, GOOD_P, GOOD_S))


def test_match_counts_is_a_multiset():
    two = [{"company": "A", "date": "d", "amount_usd": 1}, {"company": "A", "date": "d", "amount_usd": 1}]
    assert match_counts(two, two[:1], "amount_usd") == (1, 0)
    assert match_counts(two[:1], two, "amount_usd") == (1, 1)
