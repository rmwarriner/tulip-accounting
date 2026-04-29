"""Architecture tests for tulip-core.

Phase 0 placeholder: this file establishes the architecture-test slot in CI.
Real boundary rules (e.g., "tulip-core may not import tulip-storage,
tulip-api, sqlalchemy, fastapi, etc.") will land in Phase 1 when the
boundary surface exists. See ARCHITECTURE.md §9.
"""

from __future__ import annotations

import tulip_core


def test_tulip_core_module_imports() -> None:
    """Smoke check: tulip_core is importable and has the expected docstring."""
    assert tulip_core.__doc__ is not None
