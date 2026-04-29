"""Health probe."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a static OK marker. Used by load balancers and uptime probes."""
    return {"status": "ok"}
