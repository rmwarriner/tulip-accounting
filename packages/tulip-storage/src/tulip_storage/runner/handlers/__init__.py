"""First-party scheduler handlers — see ADR-0002.

Handlers are registered with a :class:`Runner` instance via
``register_handler(kind, callback)``. They consume :class:`ScheduledJob`
rows the runner has dispatched and produce side effects (shadow-ledger
writes, audit rows, etc.) within their own session scopes.

P4.3.b ships ``envelope_refill`` — the handler that materializes an
envelope's ``refill_rule`` into a ``REFILL`` shadow transaction on each
fire. Future handlers (reconciliation reminders, AI cost reports) will
register the same way.
"""

from tulip_storage.runner.handlers.daily_insights import make_daily_insights_handler
from tulip_storage.runner.handlers.envelope_refill import make_envelope_refill_handler

__all__ = ["make_daily_insights_handler", "make_envelope_refill_handler"]
