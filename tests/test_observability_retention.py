# CUI // SP-CTI
"""Tests for the observability_retention Genesis reflex (obx-trc-05).

Verifies archive-then-prune behaviour across the five append-only observability
tables (otel_spans, prov_entities, prov_activities, prov_relations,
shap_attributions):

  * new rows survive, old rows are archived into "<table>_archive" AND pruned
  * batching respects the per-run iteration cap (backlog can't wedge the daemon)
  * config defaults apply when the yaml file / block is missing
  * the reflex is registered (dispatched, not exempt)

Shim-aware: the module under test is patched via importlib.import_module +
setattr so we hit the exact object the daemon dispatches (tools.* is the live
copy; the icdev.* mirror is stale for reflexes).
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

_MODPATH = "tools.genesis.reflexes.observability_retention"

# Column DDL for the five hot tables (SQLite dialect; mirrors pg_consolidated).
_DDL = {
    "otel_spans": """
        CREATE TABLE otel_spans (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_span_id TEXT,
            name TEXT NOT NULL, kind TEXT DEFAULT 'INTERNAL', start_time TEXT NOT NULL,
            end_time TEXT, duration_ms INTEGER DEFAULT 0, status_code TEXT DEFAULT 'UNSET',
            status_message TEXT, attributes TEXT, events TEXT, agent_id TEXT, project_id TEXT,
            classification TEXT DEFAULT 'CUI', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    "prov_entities": """
        CREATE TABLE prov_entities (
            id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, label TEXT, content_hash TEXT,
            content TEXT, attributes TEXT, trace_id TEXT, span_id TEXT, agent_id TEXT,
            project_id TEXT, classification TEXT DEFAULT 'CUI', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    "prov_activities": """
        CREATE TABLE prov_activities (
            id TEXT PRIMARY KEY, activity_type TEXT NOT NULL, label TEXT, start_time TEXT,
            end_time TEXT, attributes TEXT, trace_id TEXT, span_id TEXT, agent_id TEXT,
            project_id TEXT, classification TEXT DEFAULT 'CUI', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    "prov_relations": """
        CREATE TABLE prov_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, relation_type TEXT NOT NULL,
            subject_id TEXT NOT NULL, object_id TEXT NOT NULL, attributes TEXT, trace_id TEXT,
            project_id TEXT, classification TEXT DEFAULT 'CUI', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    "shap_attributions": """
        CREATE TABLE shap_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            shapley_value REAL NOT NULL, coalition_size INTEGER, confidence_low REAL,
            confidence_high REAL, outcome_metric TEXT DEFAULT 'success', outcome_value REAL,
            analysis_params TEXT, agent_id TEXT, project_id TEXT, classification TEXT DEFAULT 'CUI',
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
}

_NOW = datetime.now(timezone.utc)
_OLD = (_NOW - timedelta(days=200)).isoformat()   # older than every window
_NEW = _NOW.isoformat()                            # brand new — must survive


def _make_db(db_path) -> None:
    conn = sqlite3.connect(str(db_path))
    for ddl in _DDL.values():
        conn.execute(ddl)
    conn.commit()
    conn.close()


def _insert_span(conn, sid: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO otel_spans (id, trace_id, name, start_time, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, f"trace-{sid}", "op", ts, ts),
    )


def _seed_all_tables(db_path) -> None:
    """Seed 2 old + 2 new rows in every table."""
    conn = sqlite3.connect(str(db_path))
    for i in range(2):
        _insert_span(conn, f"old-{i}", _OLD)
        _insert_span(conn, f"new-{i}", _NEW)
        conn.execute(
            "INSERT INTO prov_entities (id, entity_type, created_at) VALUES (?, ?, ?)",
            (f"pe-old-{i}", "artifact", _OLD),
        )
        conn.execute(
            "INSERT INTO prov_entities (id, entity_type, created_at) VALUES (?, ?, ?)",
            (f"pe-new-{i}", "artifact", _NEW),
        )
        conn.execute(
            "INSERT INTO prov_activities (id, activity_type, created_at) VALUES (?, ?, ?)",
            (f"pa-old-{i}", "generate", _OLD),
        )
        conn.execute(
            "INSERT INTO prov_activities (id, activity_type, created_at) VALUES (?, ?, ?)",
            (f"pa-new-{i}", "generate", _NEW),
        )
        conn.execute(
            "INSERT INTO prov_relations (relation_type, subject_id, object_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("used", f"a{i}", f"b{i}", _OLD),
        )
        conn.execute(
            "INSERT INTO prov_relations (relation_type, subject_id, object_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("used", f"c{i}", f"d{i}", _NEW),
        )
        conn.execute(
            "INSERT INTO shap_attributions (trace_id, tool_name, shapley_value, analyzed_at) "
            "VALUES (?, ?, ?, ?)",
            (f"t-old-{i}", "grep", 0.5, _OLD),
        )
        conn.execute(
            "INSERT INTO shap_attributions (trace_id, tool_name, shapley_value, analyzed_at) "
            "VALUES (?, ?, ?, ?)",
            (f"t-new-{i}", "grep", 0.5, _NEW),
        )
    conn.commit()
    conn.close()


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _patch_module(monkeypatch, db_path, cfg):
    """Point the reflex at a throwaway sqlite db and a fixed config (shim-aware)."""
    mod = importlib.import_module(_MODPATH)
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: sqlite3.connect(str(db_path)))
    monkeypatch.setattr(mod, "_load_config", lambda: cfg)
    return mod


# ---------------------------------------------------------------------------
# archive-then-prune correctness
# ---------------------------------------------------------------------------

def test_old_rows_archived_and_pruned_new_rows_survive(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    _make_db(db)
    _seed_all_tables(db)
    mod = _patch_module(monkeypatch, db, {})  # empty cfg → documented defaults

    result = mod.run({})

    assert result["success"] is True
    assert result["total_archived"] == 10  # 2 old rows * 5 tables

    for hot in _DDL:
        # New rows survive in the hot table.
        assert _count(db, hot) == 2, f"{hot}: new rows should remain"
        # Old rows are relocated into the cold twin (not destroyed).
        assert _count(db, f"{hot}_archive") == 2, f"{hot}_archive: old rows archived"


def test_archive_table_created_idempotently(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    _make_db(db)
    _seed_all_tables(db)
    mod = _patch_module(monkeypatch, db, {})

    # Two runs back-to-back — second run must not error on the pre-existing twin.
    mod.run({})
    result2 = mod.run({})
    assert result2["success"] is True
    # Nothing new to archive on the second pass (old rows already relocated).
    assert result2["total_archived"] == 0
    for hot in _DDL:
        assert _count(db, f"{hot}_archive") == 2


# ---------------------------------------------------------------------------
# batching cap
# ---------------------------------------------------------------------------

def test_batching_respects_iteration_cap(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    _make_db(db)
    conn = sqlite3.connect(str(db))
    for i in range(25):  # 25 old spans
        _insert_span(conn, f"old-{i}", _OLD)
    conn.commit()
    conn.close()

    # batch_size=10, max_iterations=2 → at most 20 archived this run.
    cfg = {"trace_retention": {"batch_size": 10, "max_iterations": 2}}
    mod = _patch_module(monkeypatch, db, cfg)

    result = mod.run({})

    spans = result["tables"]["otel_spans"]
    assert spans["archived"] == 20
    assert spans["iterations"] == 2
    assert spans["capped"] is True          # more remained than one run drained
    assert _count(db, "otel_spans") == 5    # 25 - 20 left in the hot table
    assert _count(db, "otel_spans_archive") == 20


# ---------------------------------------------------------------------------
# config defaults
# ---------------------------------------------------------------------------

def test_config_defaults_when_yaml_missing(monkeypatch):
    mod = importlib.import_module(_MODPATH)

    # Missing file → _load_config tolerates and returns {} → defaults resolve.
    monkeypatch.setattr(mod, "_CONFIG_PATH", "/no/such/observability_config.yaml")
    assert mod._load_config() == {}

    windows = mod._resolve_windows({})
    assert windows == {"spans_days": 30, "provenance_days": 180, "shap_days": 90}

    batch_size, max_iterations = mod._resolve_batch({})
    assert (batch_size, max_iterations) == (5000, 20)


def test_config_overrides_applied():
    mod = importlib.import_module(_MODPATH)
    cfg = {"trace_retention": {"spans_days": 7, "provenance_days": 45,
                               "shap_days": 14, "batch_size": 100, "max_iterations": 3}}
    assert mod._resolve_windows(cfg) == {
        "spans_days": 7, "provenance_days": 45, "shap_days": 14,
    }
    assert mod._resolve_batch(cfg) == (100, 3)


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    _make_db(db)
    _seed_all_tables(db)
    mod = _patch_module(monkeypatch, db, {})

    result = mod.run({"dry_run": True})

    assert result["success"] is True
    assert result["dry_run"] is True
    for hot in _DDL:
        assert _count(db, hot) == 4                 # nothing pruned
        assert _count(db, f"{hot}_archive") == 0    # nothing archived


# ---------------------------------------------------------------------------
# registration (mirror test_reflex_registration.py)
# ---------------------------------------------------------------------------

def test_reflex_is_registered_and_dispatched():
    from tools.genesis.daemon import REFLEX_NAMES
    from tests.test_reflex_registration import EXEMPT

    assert "observability_retention" in REFLEX_NAMES, "must be dispatched by the daemon"
    assert "observability_retention" not in EXEMPT, "must be dispatched, not exempt"


def test_reflex_importable_with_run():
    mod = importlib.import_module(_MODPATH)
    assert callable(getattr(mod, "run", None))


def test_genesis_config_block_present():
    import yaml
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    block = cfg.get("reflexes", {}).get("observability_retention")
    assert isinstance(block, dict), "genesis_config.yaml must carry a schedule block"
    for key in ("enabled", "risk_tier", "schedule", "description", "success_metric"):
        assert key in block, f"missing config key: {key}"
    assert block["schedule"] == "every 24h"
    assert block["enabled"] is True
