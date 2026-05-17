"""Textual terminal UI client for the Tulip Accounting API.

Per ADR-0007 and ADR-0001 architecture boundary: this package is an HTTP
client of `tulip-api`, the same way `tulip-cli` is. It must not import
storage, server, or domain internals; the only allowed channel to the
running tulip server is the HTTP API. See
``tests/test_architecture.py`` for the enforced import set.
"""
