import copy
from decimal import Decimal

import pytest

from agent_a.reconcile import ReconciliationError, reconcile


def test_parses_every_transaction(statement):
    assert len(statement.transactions) == 39          # opening-balance line excluded
    assert statement.opening_balance == Decimal("12450000.00")
    assert statement.account_no == "GPF-USD-00428817"


def test_wrapped_descriptions_are_joined(statement):
    first = statement.transactions[0]
    assert first.reference == "OW-221003-01"
    assert first.description.endswith("MGMT FEE Q4-22 PER LPA 8.1")      # 3 wrapped lines


def test_wide_amount_is_not_lost(statement):
    ionmere = next(t for t in statement.transactions if "IONMERE" in t.description)
    assert ionmere.debit == Decimal("14000000.00") and ionmere.credit == 0   # regression: x0 overlapped reference col


def test_reconciliation_passes(statement):
    rep = reconcile(statement)
    assert rep.closing_balance == Decimal("23896579.25")
    assert rep.total_debits == Decimal("38016350.00") and rep.total_credits == Decimal("49462929.25")


def test_reconciliation_detects_a_wrong_balance(statement):
    bad = copy.deepcopy(statement)
    bad.transactions[10].balance += Decimal("0.01")
    with pytest.raises(ReconciliationError):
        reconcile(bad)


def test_reconciliation_detects_a_dropped_row(statement):
    bad = copy.deepcopy(statement)
    del bad.transactions[5]
    with pytest.raises(ReconciliationError):
        reconcile(bad)
