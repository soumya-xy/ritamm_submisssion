"""Environment-driven settings. Nothing provider- or key-specific is hard-coded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    pack_dir: Path
    output_dir: Path
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    agent_a_port: int
    agent_b_port: int
    host: str = "127.0.0.1"
    llm_batch_size: int = 8
    review_threshold_pct: str = "15"
    fund_code: str = "GPF"
    valuation_date: str = "2022-12-31"

    @property
    def agent_a_url(self) -> str:
        return f"http://{self.host}:{self.agent_a_port}"

    @property
    def agent_b_url(self) -> str:
        return f"http://{self.host}:{self.agent_b_port}"


def load_settings(pack_dir: str | None = None, output_dir: str | None = None) -> Settings:
    # A real environment variable always wins over .env (override=False).
    load_dotenv(ROOT / ".env", override=False)
    pack = Path(pack_dir or os.getenv("PACK_DIR") or ROOT / "pack")
    out = Path(output_dir or os.getenv("OUTPUT_DIR") or ROOT / "output")
    return Settings(
        pack_dir=pack,
        output_dir=out,
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        agent_a_port=int(os.getenv("AGENT_A_PORT", "8001")),
        agent_b_port=int(os.getenv("AGENT_B_PORT", "8002")),
        llm_batch_size=int(os.getenv("LLM_BATCH_SIZE", "8")),
        review_threshold_pct=os.getenv("REVIEW_THRESHOLD_PCT", "15"),
        fund_code=os.getenv("FUND_CODE", "GPF"),
        valuation_date=os.getenv("VALUATION_DATE", "2022-12-31"),
    )
