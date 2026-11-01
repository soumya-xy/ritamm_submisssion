"""Agent B's A2A conversation logic (framework-free, so it is easy to unit-test).

Turn 1: Agent A asks for marks            -> task goes to `input-required`, B asks for purchases/sales.
Turn 2: Agent A sends purchases & sales   -> if they match B's records the task is `completed` with the marks
                                             artifact; otherwise it stays `input-required` and says how many
                                             items matched. A may retry up to MAX_ATTEMPTS times.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from agent_b.data import Q4Data
from common import a2a
from common.money import q2

MAX_ATTEMPTS = 5


@dataclass
class TaskState:
    task_id: str
    context_id: str
    state: str = "input-required"
    attempts: int = 0


def _norm(name) -> str:
    return " ".join(str(name).replace(".", " ").replace(",", " ").split()).casefold()


def _key(item: dict, amount_field: str):
    try:
        return (_norm(item["company"]), str(item["date"]), q2(item[amount_field]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


def match_counts(expected: list[dict], received: list[dict], amount_field: str) -> tuple[int, int]:
    """(matched, unexpected). Multiset match on (company, date, amount-to-the-cent)."""
    pool = [_key(e, amount_field) for e in expected]
    matched = unexpected = 0
    for r in received or []:
        k = _key(r, amount_field)
        if k is not None and k in pool:
            pool.remove(k)
            matched += 1
        else:
            unexpected += 1
    return matched, unexpected


class ValuationHandler:
    def __init__(self, q4: Q4Data):
        self.q4 = q4
        self.tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()

    # ---- public entry point: one JSON-RPC request dict in, one response dict out ----
    def handle_rpc(self, req: dict) -> dict:
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or "method" not in req:
            return a2a.rpc_error(rid, a2a.INVALID_REQUEST, "Invalid JSON-RPC request")
        if req["method"] != "message/send":
            return a2a.rpc_error(rid, a2a.METHOD_NOT_FOUND, f"Method not supported: {req['method']}")
        message = (req.get("params") or {}).get("message")
        if not isinstance(message, dict):
            return a2a.rpc_error(rid, a2a.INVALID_PARAMS, "params.message is required")
        try:
            with self._lock:
                return a2a.rpc_result(rid, self._on_message(message))
        except _RpcFault as f:
            return a2a.rpc_error(rid, f.code, f.message)

    # ---- conversation ----
    def _on_message(self, message: dict) -> dict:
        data = a2a.first_data_part(message)
        task_id = message.get("taskId")
        if task_id is None:
            return self._first_turn(data)
        task = self.tasks.get(task_id)
        if task is None:
            raise _RpcFault(a2a.TASK_NOT_FOUND, f"Task not found: {task_id}")
        if message.get("contextId") not in (None, task.context_id):
            raise _RpcFault(a2a.INVALID_PARAMS, "contextId does not match the task")
        if task.state in ("completed", "failed"):
            raise _RpcFault(a2a.INVALID_PARAMS, f"Task is already {task.state}")
        return self._answer_turn(task, data)

    def _first_turn(self, data: dict | None) -> dict:
        if not data or "fund" not in data or "valuation_date" not in data:
            raise _RpcFault(a2a.INVALID_PARAMS, 'Expected a data part {"fund": ..., "valuation_date": ...}')
        if str(data["fund"]).casefold() not in (self.q4.fund_code.casefold(), self.q4.fund_name.casefold()):
            raise _RpcFault(a2a.INVALID_PARAMS, f"Unknown fund: {data['fund']}")
        if data["valuation_date"] != self.q4.valuation_date:
            raise _RpcFault(a2a.INVALID_PARAMS, f"No marks for valuation date {data['valuation_date']}")
        task = TaskState(task_id=f"task-{a2a.new_id()}", context_id=a2a.new_id())
        self.tasks[task.task_id] = task
        ask = a2a.make_message("agent", [a2a.text_part(
            "Before I release the marks I need to confirm the fund's Q4 activity. Please send the fund's Q4 "
            "purchases and sales of portfolio companies as a data part "
            '{"purchases": [{"company", "date", "amount_usd"}], "sales": [{"company", "date", "proceeds_usd"}]}. '
            "Send empty lists if there were none.")], task.task_id, task.context_id)
        return a2a.make_task(task.task_id, task.context_id, "input-required", ask)

    def _answer_turn(self, task: TaskState, data: dict | None) -> dict:
        if data is None or ("purchases" not in data and "sales" not in data):
            raise _RpcFault(a2a.INVALID_PARAMS, 'Expected a data part with "purchases" and "sales"')
        task.attempts += 1
        p_ok, p_extra = match_counts(self.q4.purchases, data.get("purchases", []), "amount_usd")
        s_ok, s_extra = match_counts(self.q4.sales, data.get("sales", []), "proceeds_usd")
        total = len(self.q4.purchases) + len(self.q4.sales)
        matched, extra = p_ok + s_ok, p_extra + s_extra

        if matched == total and extra == 0:
            task.state = "completed"
            artifact = {"artifactId": f"marks-{self.q4.fund_code}-{self.q4.valuation_date}", "name": "Q4 marks",
                        "parts": [a2a.data_part(self.marks_payload())]}
            return a2a.make_task(task.task_id, task.context_id, "completed", artifacts=[artifact])

        if task.attempts >= MAX_ATTEMPTS:
            task.state = "failed"
            msg = a2a.make_message("agent", [a2a.text_part(
                f"Matched {matched} of {total} items after {task.attempts} attempts; giving up. Marks not released.")],
                task.task_id, task.context_id)
            return a2a.make_task(task.task_id, task.context_id, "failed", msg)

        msg = a2a.make_message("agent", [a2a.text_part(
            f"Matched {matched} of {total} expected items "
            f"(purchases {p_ok}/{len(self.q4.purchases)}, sales {s_ok}/{len(self.q4.sales)}); "
            f"{extra} unexpected item(s). Marks not released. Please check company, date and amount to the cent "
            "and send corrected lists.")], task.task_id, task.context_id)
        return a2a.make_task(task.task_id, task.context_id, "input-required", msg)

    def marks_payload(self) -> dict:
        return {"fund": self.q4.fund_name, "valuation_date": self.q4.valuation_date,
                "marks": self.q4.marks, "notes": self.q4.notes}


class _RpcFault(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
