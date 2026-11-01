import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PACK = ROOT / "pack"


@pytest.fixture(scope="session")
def pack():
    return PACK


@pytest.fixture(scope="session")
def ref(pack):
    from agent_a.reference import load_reference
    return load_reference(pack)


@pytest.fixture(scope="session")
def statement(pack):
    from agent_a.pdf_parser import parse_statement
    return parse_statement(pack / "GPF_Bank_Statement_Q4_2022.pdf")


@pytest.fixture(scope="session")
def rule_txns(statement, ref):
    """Transactions categorized by the deterministic rules (no LLM) - the offline golden path."""
    from agent_a import rules
    out = []
    for t in statement.transactions:
        d = dict(date=t.date, description=t.description, reference=t.reference, debit=t.debit, credit=t.credit,
                 balance=t.balance)
        d.update(rules.classify(d, ref))
        out.append(d)
    return out
