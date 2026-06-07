# CUI // SP-CTI
"""Backward-compat shim — ACE IQE adapter aliases CWK collections.

``icdev.tools.iqe.adapters.ace`` was never shipped; the canonical adapter is
``cwk``.  This module re-exports the same data under the legacy ``ace.*
names so that ``_CANVAS_MAP["ace"]`` in ``app.py`` does not 500 on import.
"""
from __future__ import annotations

from tools.iqe.adapters import cwk as _cwk
from tools.iqe.executor import register_collection


def _wrap(orig, name: str):
    def adapter(conn):
        return orig(conn)
    adapter.__name__ = name
    adapter.__doc__ = f"Backward-compat shim for {name}"
    return adapter


register_collection("ace.coworkers", _wrap(_cwk.coworkers_adapter, "ace.coworkers"))
register_collection("ace.sessions", _wrap(_cwk.sessions_adapter, "ace.sessions"))
register_collection("ace.suggestions", lambda _conn: [])
