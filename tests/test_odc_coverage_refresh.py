# CUI // SP-CTI
"""Tests for the odc_coverage_refresh Genesis reflex (obx-cov-02).

The reflex recomputes ODC MITRE ATT&CK coverage on a schedule and flags any
design whose coverage_pct dropped by more than the configured threshold (default
15 pts) since its previous odc_gap_scores snapshot — matching the
rb-odc-siem-gap-detected runbook trigger. A drift records an od_audit row AND a
status='suggested' kanban card (idempotency-keyed so re-runs never duplicate).

Verifies:
  * a scheduled recompute persists a fresh odc_gap_scores snapshot per design
  * a >threshold coverage DROP → od_audit drift row + suggested card, created
    exactly once even across a re-run (idempotent)
  * a below-threshold change → NO card, NO drift audit row
  * the per-run design cap is respected (overflow deferred)
  * config defaults apply when the yaml file / block is missing, overrides apply
  * dry_run touches nothing
  * the reflex is registered (dispatched, not exempt) with a 6h config block

Shim-aware: the module under test and the shared init_db.get_connection /
task_factory.create_tasks it dispatches to are patched via
importlib.import_module + setattr so we hit the exact objects the daemon uses
(tools.* is the live copy; the icdev.* genesis mirror is stale).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from tests import _sql_compat

_MODPATH = "tools.genesis.reflexes.odc_coverage_refresh"

# Minimal SQLite DDL for the four tables the reflex + compute_gap_score touch
# (mirrors tools/observability_canvas/db/init_db.py schema, permissive).
_DDL = {
    "observability_designs": """
        CREATE TABLE observability_designs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            template_id TEXT, classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
    "odc_gap_scores": """
        CREATE TABLE odc_gap_scores (
            id TEXT PRIMARY KEY, design_id TEXT NOT NULL,
            total_techniques INTEGER NOT NULL DEFAULT 0,
            covered_count INTEGER NOT NULL DEFAULT 0,
            partial_count INTEGER NOT NULL DEFAULT 0,
            gap_count INTEGER NOT NULL DEFAULT 0,
            overall_gap_score REAL NOT NULL DEFAULT 0.0,
            by_tactic TEXT NOT NULL DEFAULT '{}', assessed_at TEXT NOT NULL
        )""",
    "odc_technique_coverage": """
        CREATE TABLE odc_technique_coverage (
            id TEXT PRIMARY KEY, design_id TEXT NOT NULL, technique_id TEXT NOT NULL,
            coverage_state TEXT NOT NULL,
            signal_sources_present TEXT NOT NULL DEFAULT '[]',
            signal_sources_missing TEXT NOT NULL DEFAULT '[]',
            gap_score REAL NOT NULL DEFAULT 0.0, assessed_at TEXT NOT NULL
        )""",
    "od_audit": """
        CREATE TABLE od_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, design_id TEXT, actor TEXT,
            action TEXT NOT NULL, detail TEXT,
            classification TEXT DEFAULT 'CUI // SP-CTI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
}

# Rich signal-source graph → build_coverage_catalog() covers >=1 technique
# (coverage_pct > 0). Mirrors tests/test_odc_coverage_routes.py::_RICH_GRAPH.
_RICH_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "src-os-log"}, {"id": "n2", "type": "src-endpoint"},
        {"id": "n3", "type": "src-container-log"}, {"id": "n4", "type": "src-iam"},
        {"id": "n5", "type": "src-cloud-log"}, {"id": "n6", "type": "src-network-log"},
        {"id": "n7", "type": "src-flow"}, {"id": "n8", "type": "src-app-log"},
        {"id": "n9", "type": "plt-splunk"},
    ],
    "edges": [],
}
_EMPTY_GRAPH = {"nodes": [], "edges": []}  # no signal sources → coverage_pct 0


def _make_db(db_path) -> None:
    conn = sqlite3.connect(str(db_path))
    for ddl in _DDL.values():
        conn.execute(ddl)
    conn.commit()
    conn.close()


def _seed_design(db_path, graph, name="Coverage Design") -> str:
    did = "odc-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO observability_designs (id, name, graph_json, updated_at) VALUES (?,?,?,?)",
        (did, name, json.dumps(graph), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return did


def _seed_prior_score(db_path, design_id, covered, total, when=None) -> None:
    when = when or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO odc_gap_scores "
        "(id, design_id, total_techniques, covered_count, partial_count, gap_count, "
        "overall_gap_score, by_tactic, assessed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), design_id, total, covered, 0, total - covered, 0.0, "{}", when),
    )
    conn.commit()
    conn.close()


def _count(db_path, table, where="", params=()) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        sql = f"SELECT COUNT(*) FROM {table}"  # noqa: S608
        if where:
            sql += f" WHERE {where}"  # noqa: S608
        return conn.execute(sql, params).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _patch(monkeypatch, db_path, cfg=None):
    """Point the reflex + compute_gap_score persistence at a throwaway sqlite db,
    stub create_tasks with an idempotency-aware in-memory store (shim-aware)."""
    mod = importlib.import_module(_MODPATH)

    # init_db.get_connection is imported (deferred) by BOTH the reflex and
    # mitre_coverage_twin._persist_gap_score — one patch covers both.
    init_db = importlib.import_module("tools.observability_canvas.db.init_db")
    monkeypatch.setattr(
        init_db,
        "get_connection",
        lambda *a, **k: _sql_compat.connect(db_path),
        raising=True,
    )

    if cfg is not None:
        monkeypatch.setattr(mod, "_load_config", lambda: cfg)

    # Stub task_factory.create_tasks — the reflex does a deferred
    # `from tools.kanban.task_factory import create_tasks`, so patching the
    # module attribute is what it resolves at call time.
    tf = importlib.import_module("tools.kanban.task_factory")
    store: dict[str, dict] = {}
    idem_seen: set[str] = set()

    def _fake_create_tasks(specs):
        inserted = []
        for s in specs:
            sid = s["id"]
            key = s.get("idempotency_key")
            if sid in store or (key and key in idem_seen):
                continue
            store[sid] = s
            if key:
                idem_seen.add(key)
            inserted.append(sid)
        return inserted

    monkeypatch.setattr(tf, "create_tasks", _fake_create_tasks, raising=True)
    return mod, store


# ---------------------------------------------------------------------------
# recompute persists
# ---------------------------------------------------------------------------

def test_recompute_persists_new_gap_scores(tmp_path, monkeypatch):
    db = tmp_path / "oc.db"
    _make_db(db)
    did = _seed_design(db, _RICH_GRAPH)  # no prior score row
    mod, store = _patch(monkeypatch, db, cfg={})

    result = mod.run({})

    assert result["success"] is True
    assert result["designs_processed"] == 1
    # Recompute persisted a fresh snapshot for the design.
    assert _count(db, "odc_gap_scores", "design_id=?", (did,)) == 1
    assert _count(db, "odc_technique_coverage", "design_id=?", (did,)) > 0
    # No baseline existed → nothing to drop from → no drift, no card.
    assert result["drift_count"] == 0
    assert store == {}


# ---------------------------------------------------------------------------
# drift → audit + suggested card, exactly once
# ---------------------------------------------------------------------------

def test_drift_creates_audit_and_card_once(tmp_path, monkeypatch):
    db = tmp_path / "oc.db"
    _make_db(db)
    # Empty graph → new coverage 0%. Prior snapshot = 100% (covered==total).
    did = _seed_design(db, _EMPTY_GRAPH, name="SOC Baseline")
    _seed_prior_score(db, did, covered=50, total=50)  # 100% baseline
    mod, store = _patch(monkeypatch, db, cfg={})

    result = mod.run({})

    assert result["drift_count"] == 1
    assert result["cards_created"] == 1
    dr = result["drifted"][0]
    assert dr["design_id"] == did
    assert dr["prev_coverage_pct"] == 100.0
    assert dr["new_coverage_pct"] == 0.0
    assert dr["drop_pct"] > 15.0

    # od_audit drift row written exactly once.
    assert _count(db, "od_audit", "action='coverage_drift_detected' AND design_id=?", (did,)) == 1
    # Exactly one suggested card with the documented id/idempotency shape.
    assert len(store) == 1
    card = next(iter(store.values()))
    assert card["status"] == "suggested"
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert card["id"] == f"odc-drift-{did[:8]}-{day}"
    assert card["idempotency_key"] == f"odc-drift-{did}-{day}"
    assert "SOC Baseline" in card["title"]
    assert f"/observability/coverage/{did}" in card["description"]

    # Idempotent re-run: recompute updated the baseline to 0%, so no NEW drift,
    # and the idempotency_key/id would dedupe the card regardless → still one.
    result2 = mod.run({})
    assert result2["drift_count"] == 0
    assert len(store) == 1
    assert _count(db, "od_audit", "action='coverage_drift_detected' AND design_id=?", (did,)) == 1


# ---------------------------------------------------------------------------
# below threshold → no drift
# ---------------------------------------------------------------------------

def test_below_threshold_no_card(tmp_path, monkeypatch):
    db = tmp_path / "oc.db"
    _make_db(db)
    # Prior coverage 0%; rich graph recompute yields HIGHER coverage → coverage
    # went UP, not down → never a drift regardless of catalog specifics.
    did = _seed_design(db, _RICH_GRAPH)
    _seed_prior_score(db, did, covered=0, total=50)  # 0% baseline
    mod, store = _patch(monkeypatch, db, cfg={})

    result = mod.run({})

    assert result["designs_processed"] == 1
    assert result["drift_count"] == 0
    assert result["cards_created"] == 0
    assert store == {}
    assert _count(db, "od_audit", "action='coverage_drift_detected'") == 0
    # Recompute still persisted a fresh snapshot.
    assert _count(db, "odc_gap_scores", "design_id=?", (did,)) == 2  # prior + new


# ---------------------------------------------------------------------------
# cap respected
# ---------------------------------------------------------------------------

def test_design_cap_respected(tmp_path, monkeypatch):
    db = tmp_path / "oc.db"
    _make_db(db)
    for _ in range(3):
        _seed_design(db, _RICH_GRAPH)
    mod, store = _patch(monkeypatch, db, cfg={"coverage_drift": {"max_designs": 2}})

    result = mod.run({})

    assert result["designs_total"] == 3
    assert result["designs_processed"] == 2
    assert result["designs_skipped"] == 1
    assert result["max_designs"] == 2


# ---------------------------------------------------------------------------
# config defaults / overrides
# ---------------------------------------------------------------------------

def test_config_defaults_when_yaml_missing(monkeypatch):
    mod = importlib.import_module(_MODPATH)
    monkeypatch.setattr(mod, "_CONFIG_PATH", "/no/such/observability_config.yaml")
    assert mod._load_config() == {}
    assert mod._resolve_threshold({}) == 15.0
    assert mod._resolve_max_designs({}) == 50


def test_config_overrides_applied():
    mod = importlib.import_module(_MODPATH)
    cfg = {"coverage_drift": {"threshold_pct": 25.0, "max_designs": 10}}
    assert mod._resolve_threshold(cfg) == 25.0
    assert mod._resolve_max_designs(cfg) == 10


def test_stable_card_key_across_runs():
    """Same design + same day → identical id and idempotency_key (dedupe key)."""
    mod = importlib.import_module(_MODPATH)
    a = mod._card_specs("odc-abcdef12", "D", 90.0, 60.0, 30.0, "20260718")
    b = mod._card_specs("odc-abcdef12", "D", 90.0, 60.0, 30.0, "20260718")
    assert a["id"] == b["id"] == "odc-drift-odc-abcd-20260718"
    assert a["idempotency_key"] == b["idempotency_key"] == "odc-drift-odc-abcdef12-20260718"
    assert a["status"] == "suggested"


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------

def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    db = tmp_path / "oc.db"
    _make_db(db)
    did = _seed_design(db, _EMPTY_GRAPH)
    _seed_prior_score(db, did, covered=50, total=50)  # would drift if recomputed
    mod, store = _patch(monkeypatch, db, cfg={})

    result = mod.run({"dry_run": True})

    assert result["success"] is True
    assert result["dry_run"] is True
    # No recompute, no drift audit, no card.
    assert _count(db, "odc_gap_scores", "design_id=?", (did,)) == 1  # only the prior seed
    assert _count(db, "od_audit", "action='coverage_drift_detected'") == 0
    assert store == {}


# ---------------------------------------------------------------------------
# registration (mirror tests/test_observability_retention.py)
# ---------------------------------------------------------------------------

def test_reflex_is_registered_and_dispatched():
    from tools.genesis.daemon import REFLEX_NAMES
    from tests.test_reflex_registration import EXEMPT

    assert "odc_coverage_refresh" in REFLEX_NAMES, "must be dispatched by the daemon"
    assert "odc_coverage_refresh" not in EXEMPT, "must be dispatched, not exempt"


def test_reflex_importable_with_run():
    mod = importlib.import_module(_MODPATH)
    assert callable(getattr(mod, "run", None))


def test_genesis_config_block_present():
    import yaml
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    block = cfg.get("reflexes", {}).get("odc_coverage_refresh")
    assert isinstance(block, dict), "genesis_config.yaml must carry a schedule block"
    for key in ("enabled", "risk_tier", "schedule", "description", "success_metric"):
        assert key in block, f"missing config key: {key}"
    assert block["schedule"] == "every 6h"
    assert block["enabled"] is True
