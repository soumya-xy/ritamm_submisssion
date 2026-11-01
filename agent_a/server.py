"""Agent A - Bank & Ingestion Agent: HTTP wrapper + its side of the A2A conversation with Agent B.

Agent A's own A2A endpoint accepts the same opening message as Agent B
({"fund", "valuation_date"}), does the ingestion, then talks to Agent B over A2A to
obtain the marks, and returns both results as artifacts of a completed task.
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response

from agent_a.pipeline import ingest
from common import a2a, jsonio
from common.a2a import A2AClient, A2AError

log = logging.getLogger("agent_a.server")


class HandshakeError(RuntimeError):
    pass


def fetch_marks(client: A2AClient, fund: str, valuation_date: str, purchases: list[dict], sales: list[dict]) -> dict:
    """Run the two-turn conversation with Agent B and return the marks artifact data."""
    client.discover()
    task = client.send(a2a.make_message("user", [a2a.data_part({"fund": fund, "valuation_date": valuation_date})]))
    if task["status"]["state"] != "input-required":
        raise HandshakeError(f"Expected Agent B to ask a question, got state {task['status']['state']}")
    log.info("Agent B asked: %s", _status_text(task))

    answer = a2a.make_message("user", [a2a.data_part({"purchases": purchases, "sales": sales})],
                              task_id=task["id"], context_id=task["contextId"])
    task = client.send(answer)
    state = task["status"]["state"]
    if state != "completed":
        raise HandshakeError(f"Agent B did not release the marks (state={state}): {_status_text(task)}")
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "data":
                return part["data"]
    raise HandshakeError("Completed task carried no data artifact")


def _status_text(task: dict) -> str:
    msg = task["status"].get("message") or {}
    return " ".join(p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text")


def create_app(pack_dir: Path, chat_json: Callable[[str, str], dict], b_url: str, public_url: str,
               a2a_log_path: Path | None, batch_size: int = 8) -> FastAPI:
    app = FastAPI(title="Agent A - Bank & Ingestion Agent")
    card = a2a.agent_card(
        name="Bank & Ingestion Agent (Agent A)",
        description="Parses the fund's bank statement, categorizes every transaction with an LLM, proves the "
                    "arithmetic, derives Q4 purchases/sales and obtains the valuer's marks from Agent B over A2A.",
        url=f"{public_url}/a2a",
        skills=[{"id": "ingest-and-fetch-marks", "name": "Ingest bank statement and fetch marks",
                 "description": "Input data part {fund, valuation_date}; output artifacts: transactions and marks.",
                 "tags": ["bank", "reconciliation", "private-equity"],
                 "examples": ['{"fund": "GPF", "valuation_date": "2022-12-31"}']}])

    def _json(obj, status=200):
        return Response(content=jsonio.dumps(obj), status_code=status, media_type="application/json")

    @app.get(a2a.CARD_PATH)
    def get_card():
        return _json(card)

    @app.post("/a2a")
    async def rpc(request: Request):
        try:
            req = jsonio.loads(await request.body())
        except ValueError:
            return _json(a2a.rpc_error(None, a2a.PARSE_ERROR, "Parse error"))
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(req, dict) or req.get("method") != "message/send":
            return _json(a2a.rpc_error(rid, a2a.METHOD_NOT_FOUND, "Only message/send is supported"))
        data = a2a.first_data_part((req.get("params") or {}).get("message") or {})
        if not data or "fund" not in data or "valuation_date" not in data:
            return _json(a2a.rpc_error(rid, a2a.INVALID_PARAMS, 'Expected data part {"fund", "valuation_date"}'))
        try:
            result = ingest(pack_dir, chat_json, batch_size)
            client = A2AClient(b_url, log_path=a2a_log_path, sender="agent_a", receiver="agent_b")
            marks = fetch_marks(client, data["fund"], data["valuation_date"], result.purchases, result.sales)
        except (A2AError, HandshakeError) as exc:
            log.error("Handshake with Agent B failed: %s", exc)
            return _json(a2a.rpc_error(rid, a2a.INTERNAL_ERROR, f"Handshake with Agent B failed: {exc}"))
        except Exception as exc:  # surface the real cause to the orchestrator instead of a bare 500
            log.error("Ingestion failed:\n%s", traceback.format_exc())
            return _json(a2a.rpc_error(rid, a2a.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))

        artifacts = [
            {"artifactId": "bank-ingestion", "name": "Categorized Q4 bank transactions", "parts": [a2a.data_part({
                "transactions": result.transactions, "purchases": result.purchases, "sales": result.sales,
                "reconciliation": result.reconciliation, "checks": result.checks})]},
            {"artifactId": f"marks-{data['fund']}-{data['valuation_date']}", "name": "Q4 marks (as received from Agent B)",
             "parts": [a2a.data_part(marks)]},
        ]
        task = a2a.make_task(f"task-{a2a.new_id()}", a2a.new_id(), "completed", artifacts=artifacts)
        return _json(a2a.rpc_result(rid, task))

    return app
