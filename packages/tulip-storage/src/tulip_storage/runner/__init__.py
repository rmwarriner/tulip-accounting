"""Scheduler runner — see ADR-0002.

Public surface:

- :class:`Runner` — the four-method runner: ``register_handler``,
  ``schedule_one``, ``schedule_recurring``, ``cancel``.
- :data:`Clock` / :func:`default_clock` — the time-injection seam.
- :func:`compute_next_fire` — RRULE wrapper.

Handlers register at module-import time; the runner's loop is started
from the FastAPI ``lifespan`` hook in :func:`tulip_api.main.create_app`.
"""

from tulip_storage.runner.clock import Clock, default_clock
from tulip_storage.runner.rrule import compute_next_fire
from tulip_storage.runner.runner import (
    HandlerCallback,
    IdempotencyKeyConflictError,
    Runner,
)

__all__ = [
    "Clock",
    "HandlerCallback",
    "IdempotencyKeyConflictError",
    "Runner",
    "compute_next_fire",
    "default_clock",
]
