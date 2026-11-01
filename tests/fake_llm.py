"""A local stand-in for an OpenAI-compatible endpoint, used ONLY by tests and offline demos.

It answers with the deterministic keyword rules, and can be told to misbehave so the
validation / retry / fallback paths can be tested without a real model or the internet.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agent_a import rules
from agent_a.reference import load_reference


def make_server(pack_dir: Path, bad_first_for: set[str] | None = None, always_bad_for: set[str] | None = None,
                port: int = 0):
    """bad_first_for / always_bad_for: description substrings for which the fake model answers wrongly."""
    ref = load_reference(pack_dir)
    state = {"calls": 0, "seen_hint": set()}
    bad_first_for = bad_first_for or set()
    always_bad_for = always_bad_for or set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"] += 1
            user = body["messages"][-1]["content"]
            items = json.loads(user.split("Transactions:\n", 1)[1])
            results = []
            for it in items:
                txn = {"description": it["description"], "debit": it["amount"] if it["direction"] == "debit" else "0",
                       "credit": it["amount"] if it["direction"] == "credit" else "0"}
                ans = rules.classify(txn, ref)
                is_retry = "previous_answer_was_rejected_because" in it
                if any(s in it["description"] for s in always_bad_for) or (
                        any(s in it["description"] for s in bad_first_for) and not is_retry):
                    ans = {"category": "capital_call" if it["direction"] == "debit" else "expense",
                           "lp_code": None, "company": None}   # direction-violating, must be rejected
                results.append({"id": it["id"], **ans})
            payload = {"choices": [{"message": {"content": "```json\n" + json.dumps({"results": results}) + "\n```"}}]}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, state


if __name__ == "__main__":     # python -m tests.fake_llm [pack_dir]  -> stand-in LLM on :9999 for manual demos
    import sys
    srv, _ = make_server(Path(sys.argv[1] if len(sys.argv) > 1 else "pack"), port=9999)
    print("fake LLM listening on http://127.0.0.1:9999/v1  (Ctrl-C to stop)")
    threading.Event().wait()
