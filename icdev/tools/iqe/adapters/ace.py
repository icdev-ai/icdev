# CUI // SP-CTI
"""Backward-compat shim — ACE IQE adapter aliases CWK collections (icdev mirror).

See ``tools/iqe/adapters/ace.py`` for full documentation.
"""
from __future__ import annotations

from icdev.tools.iqe.adapters import cwk as _cwk
from icdev.tools.iqe.executor import register_collection


def _wrap(orig, name: str):
    def adapter(conn):
        return orig(conn)
    adapter.__name__ = name
    adapter.__doc__ = f"Backward-compat shim for {name}"
    return adapter


register_collection("ace.coworkers", _wrap(_cwk.coworkers_adapter, "ace.coworkers"))
register_collection("ace.sessions", _wrap(_cwk.sessions_adapter, "ace.sessions"))
register_collection("ace.suggestions", lambda _conn: [])
