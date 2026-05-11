"""Pin the QUICKSTART OFX fixture against the OFX parser (#138).

The fixture lives in ``docs/quickstart-fixtures/`` so it's discoverable
next to ``docs/QUICKSTART.md``. This test gives us a cheap signal that
a future parser-side change hasn't silently broken the only file a
fresh internal-beta user is going to run.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tulip_importers.ofx import parse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO_ROOT / "docs" / "quickstart-fixtures" / "sample-statement.ofx"


def test_quickstart_fixture_parses() -> None:
    """The OFX file the QUICKSTART points at must parse without error."""
    assert _FIXTURE.exists(), f"missing QUICKSTART fixture at {_FIXTURE}"
    lines = parse(_FIXTURE.read_bytes())
    assert len(lines) == 6, "QUICKSTART narrative documents 6 transactions"


def test_quickstart_fixture_math_balances() -> None:
    """Tx amounts sum to the LEDGERBAL the QUICKSTART reconcile step uses.

    The reconcile step uses ``--starting 0.00 --ending 3611.88``; if the
    fixture's net total drifts, that reconciliation example will fail
    to balance and the QUICKSTART breaks.
    """
    lines = parse(_FIXTURE.read_bytes())
    total = sum((line.amount.amount for line in lines), start=Decimal("0"))
    assert total == Decimal("3611.88")
