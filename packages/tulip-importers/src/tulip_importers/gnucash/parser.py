"""GnuCash account-tree CSV parser (#432).

The CSV shape (one row per account, header on row 1) is:

    Type,Full Account Name,Account Name,Account Code,Description,
    Account Color,Notes,Symbol,Namespace,Hidden,Tax Info,Placeholder

Where:

- ``Type`` is the GnuCash type — one of ``ASSET``, ``LIABILITY``,
  ``EQUITY``, ``INCOME``, ``EXPENSE``, ``BANK``, ``CREDIT``, ``CASH``,
  ``STOCK``, ``MUTUAL``. The first five are Tulip's five canonical
  types; the rest collapse to a Tulip type + GnuCash-as-subtype
  (BANK → asset/subtype=bank, CREDIT → liability/subtype=credit_card,
  etc. — see :func:`type_for_gnucash`).
- ``Full Account Name`` is the colon-delimited path (``Assets:Current
  Assets:Checking Account``) — the importer uses it to resolve
  parents.
- ``Account Code`` is GnuCash's chart code; maps to Tulip's
  ``code``.
- ``Description`` and ``Notes`` both map to Tulip's ``notes`` field
  (#50) — when both are present, ``Notes`` wins.
- ``Symbol`` + ``Namespace`` together encode the currency. When
  ``Namespace='CURRENCY'``, ``Symbol`` is the ISO code. Anything else
  (``Fidelity`` for ``SPAXX``, ``Rewards`` for ``CHASE_PT``) is a
  non-currency holding — the parser flags these as
  ``warning='non_currency_holding'`` and the CLI defaults to landing
  them in the operator's ``--default-currency`` with the original
  symbol/namespace stashed in ``notes``.
- ``Hidden`` is a T/F GnuCash flag — maps to ``is_active=False`` in
  Tulip (#52: archived account).
- ``Placeholder`` is a T/F GnuCash flag — maps to
  ``is_placeholder=True`` in Tulip (#52).
- ``Tax Info`` and ``Account Color`` are out of scope.

The parser is pure: input bytes → list of :class:`ParsedAccount`
records. No HTTP, no DB, no ordering — the CLI command sorts by
depth before posting.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

# GnuCash type → (Tulip type, subtype suggestion, was_lossy_currency)
#
# The five canonical types map straight through with no subtype. The
# leaf GnuCash types collapse to a Tulip type + GnuCash-as-subtype so
# the operator can still tell a credit card from a regular liability
# from the chart, without Tulip needing to know about credit-card-
# specific semantics yet.
_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "ASSET": ("asset", None),
    "BANK": ("asset", "bank"),
    "CASH": ("asset", "cash"),
    "STOCK": ("asset", "stock"),
    "MUTUAL": ("asset", "mutual"),
    "LIABILITY": ("liability", None),
    "CREDIT": ("liability", "credit_card"),
    "EQUITY": ("equity", None),
    "INCOME": ("revenue", None),  # GnuCash 'Income' = IFRS 'revenue'
    "EXPENSE": ("expense", None),
}


# Note: Tulip uses ``revenue`` for the type GnuCash calls ``Income``.
# The accounts schema accepts ``income`` as well per the validation
# regex; we emit ``income`` to match the existing API contract.
# Update this when the schema renames to ``revenue``.
_TYPE_MAP["INCOME"] = ("income", None)


class GnuCashParseError(ValueError):
    """Malformed input — header mismatch, unknown Type, etc."""


@dataclass(frozen=True, slots=True)
class ParsedAccount:
    """One row from the CSV, normalised to Tulip's vocabulary.

    ``full_path`` is the colon-delimited GnuCash hierarchy
    (``Assets:Current Assets:Checking``) used for parent resolution
    by the CLI command. ``depth`` is the count of colons (0 for a
    root account, 1 for one parent, etc.).

    ``warning`` is a non-fatal flag the importer surfaces in the dry-
    run summary:

    - ``'non_currency_holding'`` — ``Symbol``/``Namespace`` doesn't
      look like a CURRENCY pair; the CLI lands this in the
      operator's ``--default-currency`` and stashes the original
      ``Symbol`` in ``notes``.
    - ``None`` — clean row.
    """

    type: str
    name: str
    code: str | None
    full_path: str
    depth: int
    currency: str
    notes: str | None
    is_active: bool
    is_placeholder: bool
    subtype: str | None
    warning: str | None
    # Raw fields preserved for the warning path so the CLI can stash
    # them in ``notes`` when ``warning='non_currency_holding'``.
    raw_symbol: str
    raw_namespace: str


_EXPECTED_HEADER: tuple[str, ...] = (
    "Type",
    "Full Account Name",
    "Account Name",
    "Account Code",
    "Description",
    "Account Color",
    "Notes",
    "Symbol",
    "Namespace",
    "Hidden",
    "Tax Info",
    "Placeholder",
)


def parse(
    text: str,
    *,
    default_currency: str = "USD",
) -> tuple[ParsedAccount, ...]:
    """Parse the GnuCash account-tree CSV text into ``ParsedAccount`` rows.

    Raises :class:`GnuCashParseError` on header mismatch, unknown
    ``Type``, blank ``Account Name`` / ``Full Account Name``, or any
    other structural problem. Non-fatal issues (non-currency
    holdings) become ``ParsedAccount.warning`` instead.
    """
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or ()
    if tuple(fields) != _EXPECTED_HEADER:
        raise GnuCashParseError(
            f"GnuCash CSV header mismatch; expected {_EXPECTED_HEADER!r}, got {tuple(fields)!r}"
        )

    out: list[ParsedAccount] = []
    for idx, row in enumerate(reader, start=2):  # row 1 is header
        try:
            account = _row_to_parsed(row, default_currency=default_currency)
        except GnuCashParseError as exc:
            raise GnuCashParseError(f"row {idx}: {exc}") from exc
        out.append(account)
    return tuple(out)


def type_for_gnucash(gnucash_type: str) -> tuple[str, str | None]:
    """Return ``(tulip_type, suggested_subtype)`` for a GnuCash type token.

    Raises :class:`GnuCashParseError` for unknown values.
    """
    key = gnucash_type.strip().upper()
    if key not in _TYPE_MAP:
        raise GnuCashParseError(
            f"unknown GnuCash type {gnucash_type!r}; expected one of {sorted(_TYPE_MAP)}"
        )
    return _TYPE_MAP[key]


def sort_by_depth(accounts: tuple[ParsedAccount, ...]) -> tuple[ParsedAccount, ...]:
    """Sort accounts by depth so parents land before children.

    Stable within a depth — order matches the source file. The CLI
    relies on this so when it walks the rows in order, every parent
    referenced by a child has already been POSTed and is in the
    ``full_path → id`` lookup.
    """
    return tuple(sorted(accounts, key=lambda a: (a.depth, a.full_path)))


# ---- internals ------------------------------------------------------


def _row_to_parsed(row: dict[str, str], *, default_currency: str) -> ParsedAccount:
    name = (row.get("Account Name") or "").strip()
    full_path = (row.get("Full Account Name") or "").strip()
    if not name:
        raise GnuCashParseError("empty Account Name")
    if not full_path:
        raise GnuCashParseError("empty Full Account Name")
    gnucash_type = row.get("Type") or ""
    tulip_type, suggested_subtype = type_for_gnucash(gnucash_type)

    code = (row.get("Account Code") or "").strip() or None

    description = (row.get("Description") or "").strip()
    notes_field = (row.get("Notes") or "").strip()
    notes = notes_field or description or None

    symbol = (row.get("Symbol") or "").strip()
    namespace = (row.get("Namespace") or "").strip()
    warning: str | None = None
    if namespace.upper() == "CURRENCY" and len(symbol) == 3 and symbol.isalpha():
        currency = symbol.upper()
    else:
        # Non-currency holding (stock symbol, rewards points, …).
        # Land in the operator's default currency, but stash the
        # original Symbol / Namespace in the notes so the operator
        # can find these later when investment tracking lands.
        currency = default_currency
        warning = "non_currency_holding"
        original = f"original Symbol={symbol!r} Namespace={namespace!r}"
        notes = f"{notes}\n{original}" if notes else original

    is_active = (row.get("Hidden") or "F").strip().upper() != "T"
    is_placeholder = (row.get("Placeholder") or "F").strip().upper() == "T"

    depth = full_path.count(":")

    return ParsedAccount(
        type=tulip_type,
        name=name,
        code=code,
        full_path=full_path,
        depth=depth,
        currency=currency,
        notes=notes,
        is_active=is_active,
        is_placeholder=is_placeholder,
        subtype=suggested_subtype,
        warning=warning,
        raw_symbol=symbol,
        raw_namespace=namespace,
    )
