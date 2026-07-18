# CUI // SP-CTI
"""cvx-sql-03: canvas init_db connections must not trip the global RLS predicate.

Each canvas init routine below either uses get_canvas_connection() or a local
wrapper that disables RLS (set_security_context(None)). Under the SQLite test
harness these run without the UndefinedColumn error the storage-global
get_connection() would raise on canvas tables (no classification/tenant_id cols).

We assert init() completes and the expected tables exist.
"""
from __future__ import annotations

import sqlite3

import pytest


def _tables(db_path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def test_aimc_init_db_no_rls_error(tmp_path, monkeypatch):
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.aimc.db import init_db as aimc_init

    monkeypatch.setattr(aimc_init, "_IS_PG", False)
    aimc_init.init_db()  # must not raise UndefinedColumn / RLS error

    tables = _tables(db_path)
    assert {"aimc_models", "aimc_deployment"} <= tables


def test_govlift_init_db_no_rls_error(tmp_path, monkeypatch):
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.govlift.db.init_db import init_govlift_db

    init_govlift_db()  # must not raise

    tables = _tables(db_path)
    assert any(t.startswith("govlift_") for t in tables)


def test_infra_canvas_init_db_no_rls_error(tmp_path, monkeypatch):
    from tools.infra_canvas.db import init_db as idc_init

    db_file = tmp_path / "infra_canvas.db"
    monkeypatch.setattr(idc_init, "_IDC_BACKEND", "sqlite")
    monkeypatch.setattr(idc_init, "DB_PATH", db_file)

    idc_init.init_db()  # must not raise

    tables = _tables(db_file)
    assert {"infra_designs", "idc_templates"} <= tables


def test_network_init_db_no_rls_error(tmp_path, monkeypatch):
    from tools.network.db import init_db as nc_init

    db_file = tmp_path / "network_canvas.db"
    monkeypatch.setattr(nc_init, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(nc_init, "DB_PATH", db_file)

    nc_init.init_db()  # must not raise

    tables = _tables(db_file)
    assert "topologies" in tables


def test_pipeline_init_db_no_rls_error(tmp_path, monkeypatch):
    from tools.pipeline.db import init_db as pc_init

    db_file = tmp_path / "pipeline_canvas.db"
    monkeypatch.setattr(pc_init, "_PC_BACKEND", "sqlite")
    monkeypatch.setattr(pc_init, "DB_PATH", db_file)

    pc_init.init_db()  # must not raise

    tables = _tables(db_file)
    assert "pipelines" in tables


@pytest.mark.parametrize(
    "modpath",
    [
        "tools.aimc.db.init_db",
        "icdev.tools.aimc.db.init_db",
        "tools.govlift.db.init_db",
        "icdev.tools.govlift.db.init_db",
        "tools.infra_canvas.db.init_db",
        "icdev.tools.infra_canvas.db.init_db",
    ],
)
def test_canvas_init_uses_canvas_connection_pattern(modpath):
    """The three fixed modules must use get_canvas_connection() or disable RLS."""
    import importlib

    mod = importlib.import_module(modpath)
    src = importlib.util.find_spec(modpath).origin
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "get_canvas_connection" in text or "set_security_context(None)" in text
    assert mod is not None
