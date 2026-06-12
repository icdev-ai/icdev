"""Procurement Quote vs IGCE Comparison Engine.

icdev namespace mirror of tools.govcon.procurement_quote_compare.
"""
from __future__ import annotations

# Re-export everything from the canonical tools.* implementation so both
# `from tools.govcon.procurement_quote_compare import ...` and
# `from icdev.tools.govcon.procurement_quote_compare import ...` work.
import importlib
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _root not in sys.path:
    sys.path.insert(0, _root)

_mod = importlib.import_module("tools.govcon.procurement_quote_compare")

# Public surface
_globals = {
    "MAX_WARN_PCT": _mod.MAX_WARN_PCT,
    "MAX_FAIL_PCT": _mod.MAX_FAIL_PCT,
    "UNREASONABLE_LOW_PCT": _mod.UNREASONABLE_LOW_PCT,
    "GATE_VERDICTS": _mod.GATE_VERDICTS,
    "QUOTE_STATUSES": _mod.QUOTE_STATUSES,
    "create_procurement": _mod.create_procurement,
    "list_procurements": _mod.list_procurements,
    "add_igce_line": _mod.add_igce_line,
    "list_igce": _mod.list_igce,
    "add_quote": _mod.add_quote,
    "list_quotes": _mod.list_quotes,
    "compare_procurement": _mod.compare_procurement,
    "vendor_summary": _mod.vendor_summary,
    "gate_procurement": _mod.gate_procurement,
    "main": _mod.main,
}
globals().update(_globals)
__all__ = list(_globals.keys())
