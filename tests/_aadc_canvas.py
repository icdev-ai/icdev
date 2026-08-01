# CUI // SP-CTI
"""One canvas-DB fixture for the Agentic AI Canvas (``aadc_*``) test suite.

Pin the canvas store by patching the *data* that
``tools/agentic_ai_canvas/db/init_db.py::get_connection`` reads at call time
(``_BACKEND`` and ``DB_PATH``) — never by replacing ``get_connection`` itself.

Why that distinction is load-bearing:

``canvas_bridge`` and its siblings bind the factory at module scope::

    from tools.agentic_ai_canvas.db.init_db import get_connection

and ``agentic_engine.assess_design`` imports ``canvas_bridge`` lazily, *inside*
the function. So the first import of ``canvas_bridge`` in a pytest session can
happen in the middle of some other file's test. A fixture that replaces
``init_db.get_connection`` with a closure over a ``tmp_path`` DB gets that
closure captured permanently by that late import: monkeypatch restores
``init_db.get_connection``, but ``canvas_bridge.get_connection`` keeps pointing
at a temp file that has since been deleted. Every later test in the session
then reads an empty database and reports its own freshly-seeded designs as
"not found" — while passing in isolation.

Patching ``_BACKEND``/``DB_PATH`` has no such failure mode: ``get_connection``
resolves both as module globals on every call, so a module that captured the
function object still lands on whichever DB is pinned right now.

This is the third leak found in this suite (after PR #1048's shared Flask app
singleton and PR #1049's unrestored ``sys.modules.pop``); the shared fixture
exists so there is only one place left to get this right.

Usage::

    from _aadc_canvas import canvas_db  # noqa: F401

    def test_something(canvas_db):
        conn = canvas_db.get_connection()   # the fixture yields the module
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

__all__ = ["pin_canvas_db", "canvas_db"]


def pin_canvas_db(monkeypatch, db_path: Path):
    """Pin the AADC canvas store at ``db_path`` (SQLite) and initialize schema.

    Returns the ``init_db`` module so callers can reach ``get_connection()``,
    which applies the same translating shim and RLS-off handling production
    uses. Requires a function-scoped ``monkeypatch`` so both globals are
    restored at teardown.
    """
    initdb = importlib.import_module("tools.agentic_ai_canvas.db.init_db")
    monkeypatch.setattr(initdb, "_BACKEND", "sqlite", raising=True)
    monkeypatch.setattr(initdb, "DB_PATH", Path(db_path), raising=True)
    initdb.init_db()
    return initdb


@pytest.fixture
def canvas_db(tmp_path, monkeypatch):
    """AADC canvas DB pinned to a temp SQLite file, schema initialized."""
    return pin_canvas_db(monkeypatch, tmp_path / "aadc_canvas.db")
