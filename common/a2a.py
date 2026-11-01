"""Minimal A2A (v0.3, JSON-RPC 2.0 over HTTP) helpers shared by both agents.

Only `message/send` is implemented, as the brief requires. Everything here is
plain dicts so the wire format is easy to read and to test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx

from common import jsonio

PROTOCOL_VERSION = "0.3.0"
CARD_PATH = "/.well-known/agent-card.json"

# JSON-RPC / A2A error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


# ---------- building blocks ----------
def text_part(text: str) -> dict:
    return {"kind": "text", "text": text}


def data_part(data: dict) -> dict:
    return {"kind": "data", "data": data}


def make_message(role: str, parts: list[dict], task_id: str | None = None, context_id: str | None = None) -> dict:
    msg = {"kind": "message", "role": role, "messageId": new_id(), "parts": parts}
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    return msg


def make_task(task_id: str, context_id: str, state: str, message: dict | None = None,
              artifacts: list[dict] | None = None) -> dict:
    status = {"state": state, "timestamp": now_iso()}
    if message is not None:
        status["message"] = message
    task = {"kind": "task", "id": task_id, "contextId": context_id, "status": status}
    if artifacts:
        task["artifacts"] = artifacts
    return task


def rpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def rpc_request(message: dict) -> dict:
    return {"jsonrpc": "2.0", "id": new_id(), "method": "message/send", "params": {"message": message}}


def first_data_part(message: dict) -> dict | None:
    for part in message.get("parts", []):
        if part.get("kind") == "data":
            return part.get("data") or {}
    return None


def agent_card(name: str, description: str, url: str, skills: list[dict]) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": name,
        "description": description,
        "url": url,
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": skills,
    }


# ---------- client ----------
class A2AError(RuntimeError):
    pass


class A2AClient:
    """Tiny JSON-RPC client. Optionally logs every request/response to a JSONL file."""

    def __init__(self, base_url: str, log_path=None, sender: str = "agent_a", receiver: str = "agent_b",
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.log_path = log_path
        self.sender, self.receiver = sender, receiver
        self.timeout = timeout
        self.endpoint: str | None = None

    def _log(self, kind: str, payload, http_status: int | None = None) -> None:
        if not self.log_path:
            return
        line = {"ts": now_iso(), "direction": kind, "from": self.sender if kind == "request" else self.receiver,
                "to": self.receiver if kind == "request" else self.sender, "payload": payload}
        if http_status is not None:
            line["http_status"] = http_status
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(jsonio.dumps(line, ensure_ascii=False) + "\n")

    def discover(self) -> dict:
        """Fetch the public agent card; the POST endpoint is the card's `url` field."""
        resp = httpx.get(self.base_url + CARD_PATH, timeout=self.timeout)
        resp.raise_for_status()
        card = jsonio.loads(resp.text)
        self.endpoint = card["url"]
        return card

    def send(self, message: dict) -> dict:
        if self.endpoint is None:
            self.discover()
        request = rpc_request(message)
        self._log("request", request)
        resp = httpx.post(self.endpoint, content=jsonio.dumps(request),
                          headers={"Content-Type": "application/json"}, timeout=self.timeout)
        try:
            body = jsonio.loads(resp.text)
        except ValueError as exc:
            self._log("response", {"raw": resp.text[:2000]}, resp.status_code)
            raise A2AError(f"non-JSON response (HTTP {resp.status_code})") from exc
        self._log("response", body, resp.status_code)
        if "error" in body:
            raise A2AError(f"JSON-RPC error {body['error'].get('code')}: {body['error'].get('message')}")
        return body["result"]
