"""Allow ``python -m tulip_cli`` to invoke the Typer app."""

from __future__ import annotations

from tulip_cli.main import app

if __name__ == "__main__":
    app()
