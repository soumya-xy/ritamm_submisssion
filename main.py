#!/usr/bin/env python3
"""One command to close the quarter:   python main.py [PACK_DIR] [--output DIR]

Starts Agent A (bank & ingestion) and Agent B (valuation & NAV) as local A2A servers, asks Agent A to
ingest the bank statement and obtain the marks from Agent B (two-turn A2A conversation), then has
Agent B review the marks and compute the NAV + bridge. Writes everything to ./output.
Credentials come from the environment / .env: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from agent_a.llm import LLMClient, LLMError
from agent_a.server import create_app as create_agent_a
from agent_b.close import close_quarter
from agent_b.data import load_q4
from agent_b.server import create_app as create_agent_b
from common import a2a, jsonio
from common.config import Settings, load_settings

log = logging.getLogger("main")
OUTPUT_FILES = ["transactions.json", "a2a_log.jsonl", "marks.json", "review.json", "nav.json", "reconciliation.json"]


class _Server:
    """A uvicorn server running in a background thread that we can stop cleanly."""

    def __init__(self, name: str, app, host: str, port: int):
        self.name, self.host, self.port = name, host, port
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, name=name, daemon=True)

    def start(self, timeout: float = 20.0) -> None:
        with socket.socket() as s:
            if s.connect_ex((self.host, self.port)) == 0:
                raise RuntimeError(f"Port {self.port} is already in use (needed by {self.name}). "
                                   f"Set a different port via AGENT_A_PORT / AGENT_B_PORT.")
        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.server.started:
                return
            if not self.thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError(f"{self.name} failed to start on port {self.port}")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def run(settings: Settings, chat_json=None) -> dict:
    """Run the full pipeline; returns the nav document. `chat_json` can be injected (tests)."""
    out = settings.output_dir
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        (out / name).unlink(missing_ok=True)
    log_path = out / "a2a_log.jsonl"

    if chat_json is None:
        chat_json = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model).chat_json

    b_app = create_agent_b(load_q4(settings.pack_dir), settings.agent_b_url)
    a_app = create_agent_a(settings.pack_dir, chat_json, settings.agent_b_url, settings.agent_a_url, log_path,
                           settings.llm_batch_size)
    servers = [_Server("Agent B", b_app, settings.host, settings.agent_b_port),
               _Server("Agent A", a_app, settings.host, settings.agent_a_port)]
    try:
        for s in servers:
            s.start()
            log.info("%s listening on http://%s:%s", s.name, s.host, s.port)

        log.info("Asking Agent A to ingest the bank statement and fetch the Q4 marks over A2A ...")
        client = a2a.A2AClient(settings.agent_a_url, log_path=None, sender="main", receiver="agent_a", timeout=1800)
        task = client.send(a2a.make_message("user", [a2a.data_part(
            {"fund": settings.fund_code, "valuation_date": settings.valuation_date})]))
        if task["status"]["state"] != "completed":
            raise RuntimeError(f"Agent A did not complete: {task['status']}")
        arts = {a["artifactId"]: a["parts"][0]["data"] for a in task["artifacts"]}
        ingestion = arts["bank-ingestion"]
        marks_payload = next(v for k, v in arts.items() if k.startswith("marks-"))

        txns = ingestion["transactions"]
        jsonio.write_json(out / "transactions.json", txns)
        jsonio.write_json(out / "marks.json", marks_payload)
        jsonio.write_json(out / "reconciliation.json", {"reconciliation": ingestion["reconciliation"],
                                                         "categorization_checks": ingestion["checks"]})

        log.info("Agent B: reviewing marks and computing NAV ...")
        review_doc, nav_doc = close_quarter(settings.pack_dir, txns, marks_payload, settings.review_threshold_pct)
        jsonio.write_json(out / "review.json", review_doc)
        jsonio.write_json(out / "nav.json", nav_doc)
        _print_summary(txns, review_doc, nav_doc, ingestion["checks"])
        if not nav_doc["checks"]["bridge_balances"]:
            raise RuntimeError("NAV bridge does not balance - a transaction is probably mis-categorized")
        return nav_doc
    finally:
        for s in reversed(servers):
            s.stop()


def _print_summary(txns, review_doc, nav_doc, checks) -> None:
    b = nav_doc["bridge"]
    flagged = [e["company"] for e in review_doc["companies"] if e["flags"]]
    rows = [("Closing cash", nav_doc["closing_cash"]), ("Capital called", b["capital_called"]),
            ("Distributions paid", b["distributions"]), ("Management fee", nav_doc["management_fee"]),
            ("Income", b["income"]), ("Total expenses", b["expenses"]), ("Realized gain", b["realized_gain"]),
            ("Change in unrealized", b["change_in_unrealized"]), ("Investments at FV", nav_doc["investments_usd"]),
            ("Liabilities", nav_doc["liabilities"]), ("NAV at 31 Dec 2022", nav_doc["nav_q4"]),
            ("Bridge difference", b["difference"])]
    print("\n=== Q4 2022 close - numbers for the submission form ===")
    for label, val in rows:
        print(f"  {label:<24}{val:>20,.2f}")
    print(f"  {'EUR/USD rate used':<24}{review_doc['eurusd_rate']:>20}  (ECB, {review_doc['eurusd_rate_date']})")
    print(f"  {'Materiality threshold':<24}{review_doc['threshold_pct']:>19}%")
    print(f"  {'Flagged companies':<24}  {', '.join(flagged)}")
    print(f"  {'Transactions':<24}{len(txns):>20}")
    print(f"  LLM/rules disagreements: {len(checks['llm_rules_disagreements'])}; "
          f"rows on rules fallback: {checks['rows_using_rules_fallback']}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Close the quarter for GPF with two A2A agents.")
    ap.add_argument("pack_dir", nargs="?", default=None, help="input directory (default ./pack or $PACK_DIR)")
    ap.add_argument("--output", default=None, help="output directory (default ./output)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings(args.pack_dir, args.output)

    missing = [n for n, v in (("LLM_API_KEY", settings.llm_api_key), ("LLM_BASE_URL", settings.llm_base_url),
                              ("LLM_MODEL", settings.llm_model)) if not v]
    if missing:
        print(f"Missing environment variable(s): {', '.join(missing)}. Copy .env.example to .env and fill them in.",
              file=sys.stderr)
        return 2
    if not settings.pack_dir.is_dir():
        print(f"Pack directory not found: {settings.pack_dir}", file=sys.stderr)
        return 2
    try:
        run(settings)
    except (LLMError, httpx.HTTPError, RuntimeError, ValueError, a2a.A2AError) as exc:
        log.error("Run failed: %s", exc)
        return 1
    log.info("Done. Outputs written to %s", settings.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
