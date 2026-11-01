"""Full run over real localhost HTTP A2A servers, with a local stand-in for the LLM (no internet)."""
import socket
from decimal import Decimal as D
from pathlib import Path

import pytest

import main
from common import jsonio
from common.config import Settings
from tests.fake_llm import make_server


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_once(pack, tmp_path, **fake_kw):
    httpd, state = make_server(pack, **fake_kw)
    try:
        from agent_a.llm import LLMClient
        llm = LLMClient("test-key", f"http://127.0.0.1:{httpd.server_port}/v1", "fake-model", max_attempts=2)
        settings = Settings(pack_dir=pack, output_dir=tmp_path, llm_api_key="k", llm_base_url="u", llm_model="m",
                            agent_a_port=free_port(), agent_b_port=free_port())
        nav = main.run(settings, chat_json=llm.chat_json)
        return nav, state
    finally:
        httpd.shutdown()


def test_full_pipeline_writes_all_outputs(pack, tmp_path):
    nav, state = run_once(pack, tmp_path)
    assert nav["nav_q4"] == D("169764039.25") and nav["bridge"]["difference"] == D("0.00")
    for name in ("transactions.json", "a2a_log.jsonl", "marks.json", "review.json", "nav.json"):
        assert (tmp_path / name).exists(), name

    txns = jsonio.loads((tmp_path / "transactions.json").read_text())
    assert len(txns) == 39
    assert {"date", "description", "reference", "debit", "credit", "balance", "category", "lp_code", "company"} <= set(txns[0])

    log = [jsonio.loads(l) for l in (tmp_path / "a2a_log.jsonl").read_text().splitlines()]
    assert [l["direction"] for l in log] == ["request", "response", "request", "response"]
    assert all("ts" in l for l in log)
    assert log[1]["payload"]["result"]["status"]["state"] == "input-required"
    assert log[3]["payload"]["result"]["status"]["state"] == "completed"
    # marks.json is exactly the artifact data Agent B returned
    assert jsonio.loads((tmp_path / "marks.json").read_text()) == log[3]["payload"]["result"]["artifacts"][0]["parts"][0]["data"]


def test_pipeline_survives_a_bad_llm_answer(pack, tmp_path):
    nav, _ = run_once(pack, tmp_path, bad_first_for={"D&O PREMIUM", "HARBOURSIDE"})
    assert nav["nav_q4"] == D("169764039.25")
