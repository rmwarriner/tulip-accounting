"""Cross-account transfer pairing for multi-account QIF imports (#195b).

A QIF transfer is two records: the money-out account carries
``L[Destination]`` with a negative amount, the money-in account carries
``L[Source]`` with the reciprocal positive amount. Imported naively
(195a), each leg becomes its own one-sided ``Imbalance`` transaction and
the money double-counts.

:func:`pair_transfers` matches the reciprocal legs so the import
endpoint can land each pair as a single balanced PENDING transaction
(account A -X / account B +X). Legs that can't be paired — the target
account isn't in the import's account map, or no reciprocal record
exists — are returned only as warnings; the caller lands those as
ordinary one-sided statement lines (the user's call to fix up).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tulip_importers.qif import transfer_target

if TYPE_CHECKING:
    from uuid import UUID

    from tulip_core.reconciliation import ParsedStatementLine


@dataclass(frozen=True, slots=True)
class TransferPair:
    """A matched cross-account transfer — two reciprocal QIF legs.

    ``from_account`` / ``to_account`` are QIF account names (keys of the
    import's account map); ``from_line`` is always the money-out leg
    (negative amount), ``to_line`` the money-in leg.
    """

    from_account: str
    to_account: str
    from_line: ParsedStatementLine
    to_line: ParsedStatementLine


@dataclass(frozen=True, slots=True)
class UnpairedTransferLeg:
    """A QIF transfer leg with no matching reciprocal in the same upload (#448).

    Banktivity sometimes exports only one side of a transfer (e.g.
    the 401(k) employer-side records reference ``L[Checking]`` but
    Checking's QIF doesn't have a reciprocal). The upload handler
    lands these as balanced PENDING transactions with an
    ``Imbalance:Unknown`` plug on the counter-posting side; the
    operator re-pairs during reconciliation.
    """

    account: str
    target: str
    line: ParsedStatementLine


def pair_transfers(
    parsed_by_account: dict[str, list[ParsedStatementLine]],
    account_map: dict[str, UUID],
) -> tuple[list[TransferPair], list[UnpairedTransferLeg], list[str]]:
    """Match reciprocal QIF transfer legs across the imported accounts.

    Two legs pair when they are reciprocal: ``A → B`` for ``-X`` on date
    ``D`` and ``B → A`` for ``+X`` on the same date, in the same
    currency, with both accounts present in ``account_map``. Matching is
    greedy — the first eligible reciprocal wins — which is correct for
    QIF exports, where a transfer's two legs are exact mirrors.

    Returns ``(pairs, unpaired_legs, warnings)``:

    - ``pairs`` — matched reciprocals; caller turns each into one
      balanced PENDING tx at upload time.
    - ``unpaired_legs`` — transfer legs whose counterpart wasn't in
      the upload (#448). Caller plugs each with ``Imbalance:Unknown``
      so the household lands balanced from the start.
    - ``warnings`` — operator-facing messages describing each
      unpaired leg (or unmapped target).
    """
    # Collect every transfer leg as (account, target, line). Non-transfer
    # records and legs pointing at an unmapped account are skipped here —
    # the latter with a warning, since the user probably meant to map it.
    legs: list[tuple[str, str, ParsedStatementLine]] = []
    unpaired: list[UnpairedTransferLeg] = []
    warnings: list[str] = []
    for account, lines in parsed_by_account.items():
        for line in lines:
            target = transfer_target(line.raw)
            if target is None:
                continue  # ordinary record — becomes a normal statement line
            if target not in account_map:
                # The target account isn't in this upload. We still want
                # the leg to land as a balanced tx, so plug with
                # Imbalance:Unknown via the unpaired path (#448).
                unpaired.append(UnpairedTransferLeg(account, target, line))
                warnings.append(
                    f"{account!r} line {line.line_number}: transfer to "
                    f"unmapped account {target!r} — landed with "
                    "Imbalance:Unknown plug; re-target during "
                    "reconciliation."
                )
                continue
            legs.append((account, target, line))

    pairs: list[TransferPair] = []
    consumed: set[int] = set()
    for i, (acct_a, target_b, line_a) in enumerate(legs):
        if i in consumed:
            continue
        partner_idx: int | None = None
        for j, (acct_b, target_a, line_b) in enumerate(legs):
            if j in consumed or j == i:
                continue
            if (
                acct_b == target_b
                and target_a == acct_a
                and line_b.amount.currency == line_a.amount.currency
                and line_b.amount.amount == -line_a.amount.amount
                and line_b.posted_date == line_a.posted_date
            ):
                partner_idx = j
                break
        if partner_idx is None:
            unpaired.append(UnpairedTransferLeg(acct_a, target_b, line_a))
            warnings.append(
                f"{acct_a!r} line {line_a.line_number}: transfer to "
                f"{target_b!r} has no matching reciprocal leg — landed "
                "with Imbalance:Unknown plug; re-target during "
                "reconciliation."
            )
            continue
        consumed.add(i)
        consumed.add(partner_idx)
        line_b = legs[partner_idx][2]
        # Orient the pair so from_line is always the money-out (negative) leg.
        if line_a.amount.amount < 0:
            pairs.append(TransferPair(acct_a, target_b, line_a, line_b))
        else:
            pairs.append(TransferPair(target_b, acct_a, line_b, line_a))
    return pairs, unpaired, warnings
