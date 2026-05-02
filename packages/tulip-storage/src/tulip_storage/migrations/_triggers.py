"""Trigger SQL for the initial migration, factored out for reuse in tests.

The initial migration runs these against a live engine; tests with an
in-memory engine need to run the same DDL so the balance safety net is
identical between production and test.
"""

from __future__ import annotations

from typing import Final

_TX_STATUS_BALANCE = """
CREATE TRIGGER trg_transactions_balanced_on_post
AFTER UPDATE OF status ON transactions
WHEN NEW.status IN ('POSTED', 'RECONCILED')
  AND (OLD.status IS NULL OR OLD.status != NEW.status)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'transaction postings do not balance per currency')
  END;
END;
"""

_POSTING_INSERT_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_insert
AFTER INSERT ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot insert posting that breaks balance on a posted transaction')
  END;
END;
"""

_POSTING_UPDATE_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_update
AFTER UPDATE ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot update posting that breaks balance on a posted transaction')
  END;
END;
"""

_POSTING_DELETE_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_delete
AFTER DELETE ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = OLD.household_id
    AND id = OLD.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = OLD.household_id
        AND transaction_id = OLD.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot delete posting that breaks balance on a posted transaction')
  END;
END;
"""

INITIAL_TRIGGERS: Final[tuple[str, ...]] = (
    _TX_STATUS_BALANCE,
    _POSTING_INSERT_BALANCE,
    _POSTING_UPDATE_BALANCE,
    _POSTING_DELETE_BALANCE,
)

INITIAL_TRIGGER_NAMES: Final[tuple[str, ...]] = (
    "trg_transactions_balanced_on_post",
    "trg_postings_balanced_on_insert",
    "trg_postings_balanced_on_update",
    "trg_postings_balanced_on_delete",
)


# ---- P4.0 shadow-ledger balance triggers --------------------------------
#
# Mirror the main-ledger triggers above. Pool balances are derived from
# `sum(shadow_postings)`, so the invariant we enforce at the trigger layer
# is that every `shadow_transactions` row in `posted` status has its
# `shadow_postings` summing to zero per currency. Pending and voided rows
# are exempt — they don't contribute to derived balances.

_SHADOW_TX_STATUS_BALANCE = """
CREATE TRIGGER trg_shadow_transactions_balanced_on_post
AFTER UPDATE OF status ON shadow_transactions
WHEN NEW.status = 'posted'
  AND (OLD.status IS NULL OR OLD.status != NEW.status)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM shadow_postings
      WHERE household_id = NEW.household_id
        AND shadow_transaction_id = NEW.id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'shadow transaction postings do not balance per currency')
  END;
END;
"""

_SHADOW_POSTING_INSERT_BALANCE = """
CREATE TRIGGER trg_shadow_postings_balanced_on_insert
AFTER INSERT ON shadow_postings
WHEN EXISTS (
  SELECT 1 FROM shadow_transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.shadow_transaction_id
    AND status = 'posted'
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM shadow_postings
      WHERE household_id = NEW.household_id
        AND shadow_transaction_id = NEW.shadow_transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'shadow posting insert breaks balance on a posted shadow tx')
  END;
END;
"""

_SHADOW_POSTING_UPDATE_BALANCE = """
CREATE TRIGGER trg_shadow_postings_balanced_on_update
AFTER UPDATE ON shadow_postings
WHEN EXISTS (
  SELECT 1 FROM shadow_transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.shadow_transaction_id
    AND status = 'posted'
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM shadow_postings
      WHERE household_id = NEW.household_id
        AND shadow_transaction_id = NEW.shadow_transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'shadow posting update breaks balance on a posted shadow tx')
  END;
END;
"""

_SHADOW_POSTING_DELETE_BALANCE = """
CREATE TRIGGER trg_shadow_postings_balanced_on_delete
AFTER DELETE ON shadow_postings
WHEN EXISTS (
  SELECT 1 FROM shadow_transactions
  WHERE household_id = OLD.household_id
    AND id = OLD.shadow_transaction_id
    AND status = 'posted'
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM shadow_postings
      WHERE household_id = OLD.household_id
        AND shadow_transaction_id = OLD.shadow_transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'shadow posting delete breaks balance on a posted shadow tx')
  END;
END;
"""

P4_0_SHADOW_TRIGGERS: Final[tuple[str, ...]] = (
    _SHADOW_TX_STATUS_BALANCE,
    _SHADOW_POSTING_INSERT_BALANCE,
    _SHADOW_POSTING_UPDATE_BALANCE,
    _SHADOW_POSTING_DELETE_BALANCE,
)

P4_0_SHADOW_TRIGGER_NAMES: Final[tuple[str, ...]] = (
    "trg_shadow_transactions_balanced_on_post",
    "trg_shadow_postings_balanced_on_insert",
    "trg_shadow_postings_balanced_on_update",
    "trg_shadow_postings_balanced_on_delete",
)
