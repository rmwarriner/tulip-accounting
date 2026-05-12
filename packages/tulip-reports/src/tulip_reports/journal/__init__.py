"""hledger-compatible journal export / import (P7.4 / P7.5).

The :mod:`tulip_reports.journal.export` module is the export side;
imports land in P7.5.
"""

from tulip_reports.journal.export import export_journal

__all__ = ["export_journal"]
