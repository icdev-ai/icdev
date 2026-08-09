# CUI // SP-CTI
"""HGX — per-node harness evaluation (hgx-eval-01).

``harness_eval`` correlated on ``task_id`` alone, so a graph run's nodes were
indistinguishable once recorded: a node that is consistently right and one that
is consistently wrong averaged into a single number and the meta-harness could
not tell them apart. These tests lock in the four nullable columns that give the
table a node grain, and the three properties that make them worth having —
existing queries unaffected, per-node precision/recall computable, and each
node_type getting its own adaptive baseline.
"""
from __future__ import annotations

import sqlite3

import pytest

# The post-migration schema. Deliberately spelled out here rather than imported:
# a test that reads its expectations from the code under test cannot catch the
# code drifting away from the migration.
_HARNESS_EVAL_DDL = """
CREATE TABLE IF NOT EXISTS harness_eval (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL DEFAULT '',
    reflex         TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL,
    metadata_json  TEXT DEFAULT '{}',
    actual_outcome TEXT,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL,
    run_id         TEXT,
    node_id        TEXT,
    node_type      TEXT,
    edge_condition TEXT
);
CREATE INDEX IF NOT EXISTS idx_harness_eval_run_node ON harness_eval (run_id, node_id);
CREATE INDEX IF NOT EXISTS idx_harness_eval_node_type ON harness_eval (node_type, created_at);
"""

# The schema as it stood BEFORE the migration, used to prove the degradation path.
_PRE_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS harness_eval (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL DEFAULT '',
    reflex         TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL,
    metadata_json  TEXT DEFAULT '{}',
    actual_outcome TEXT,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL
);
"""

_GRAPH_COLUMNS = ("run_id", "node_id", "node_type", "edge_condition")


def _wire_db(tmp_path, monkeypatch, ddl: str):
    """Scratch SQLite DB carrying just harness_eval, wired into get_connection()."""
    import tools.genesis.harness.eval_harness as eh

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    # The graph-column probe is cached for the life of the process; each test
    # gets a different database, so the cache has to be cleared between them.
    monkeypatch.setattr(eh, "_graph_columns_present", None, raising=False)
    return db_path


@pytest.fixture
def harness_db(tmp_path, monkeypatch):
    return _wire_db(tmp_path, monkeypatch, _HARNESS_EVAL_DDL)


@pytest.fixture
def legacy_harness_db(tmp_path, monkeypatch):
    """A database that has NOT applied migration 20260809041642."""
    return _wire_db(tmp_path, monkeypatch, _PRE_MIGRATION_DDL)


# ---------------------------------------------------------------------------
# The migration itself
# ---------------------------------------------------------------------------

def test_migration_module_adds_columns_and_is_idempotent(legacy_harness_db):
    """Load up.py by path and run it twice against the pre-migration schema."""
    import importlib.util
    from pathlib import Path

    from tools.db.storage import column_exists, get_connection

    up_path = (
        Path(__file__).resolve().parents[1]
        / "tools" / "db" / "migrations"
        / "20260809041642_harness_eval_graph_node_columns" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("hgx_eval_01_up", up_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.up()
    assert first["status"] == "applied"
    assert sorted(first["added"]) == sorted(_GRAPH_COLUMNS)

    conn = get_connection()
    try:
        for column in _GRAPH_COLUMNS:
            assert column_exists(conn, "harness_eval", column), column
        # Nullable is the entire backward-compatibility story: a row written by
        # the old INSERT must still be insertable.
        conn.execute(
            "INSERT INTO harness_eval (id, task_id, reflex, decision, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("legacy-row", "kt-1", "oracle_triage", "promote", "2026-08-09T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    second = module.up()
    assert second["added"] == [], "re-running the migration must add nothing"


# ---------------------------------------------------------------------------
# Existing queries are unaffected
# ---------------------------------------------------------------------------

def test_reflex_metrics_ignore_graph_rows_grain(harness_db):
    """compute_metrics still answers at the reflex grain with node columns present."""
    from tools.genesis.harness.eval_harness import (
        compute_metrics,
        record_decision,
        record_graph_node_decision,
        record_outcome,
    )

    record_decision(task_id="hgx-t1", reflex="oracle_triage", decision="promote", confidence=0.9)
    record_outcome("hgx-t1", "resolved")
    record_graph_node_decision(
        task_id="hgx-t2", reflex="oracle_triage", decision="promote", confidence=0.8,
        run_id="run-1", node_id="verify", node_type="agent",
    )

    m = compute_metrics("oracle_triage", window_days=30)

    # Both rows count for the reflex — the node row is not hidden from the
    # pre-existing read, it just carries extra identity.
    assert m["total_decisions"] == 2
    assert m["resolved_count"] == 1
    assert m["reflex"] == "oracle_triage"


def test_record_decision_still_leaves_graph_columns_null(harness_db):
    from tools.db.storage import get_connection
    from tools.genesis.harness.eval_harness import record_decision

    record_decision(task_id="hgx-t3", reflex="heal", decision="heal", confidence=0.7)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT run_id, node_id, node_type, edge_condition FROM harness_eval "
            "WHERE task_id = %s",
            ("hgx-t3",),
        ).fetchone()
    finally:
        conn.close()
    assert [row[i] for i in range(4)] == [None, None, None, None]


# ---------------------------------------------------------------------------
# Per-node precision / recall
# ---------------------------------------------------------------------------

def _seed_run(run_id: str = "run-42") -> None:
    """One run, two nodes, deliberately opposite track records.

    `strong` is right 3/3; `weak` is wrong 2/3. Rolled up by task_id they are a
    single 4/6 — the exact blend the node grain exists to separate.
    """
    from tools.genesis.harness.eval_harness import (
        record_graph_node_decision,
        record_node_outcome,
    )

    for outcome in ["resolved", "resolved", "resolved"]:
        record_graph_node_decision(
            task_id="hgx-run-task", reflex="studio_workflow", decision="proceed",
            confidence=0.9, run_id=run_id, node_id="strong", node_type="tool",
            edge_condition="tests_passed",
        )
        record_node_outcome(run_id=run_id, node_id="strong", actual_outcome=outcome)

    for outcome in ["false_positive", "false_positive", "resolved"]:
        record_graph_node_decision(
            task_id="hgx-run-task", reflex="studio_workflow", decision="proceed",
            confidence=0.9, run_id=run_id, node_id="weak", node_type="agent",
        )
        record_node_outcome(run_id=run_id, node_id="weak", actual_outcome=outcome)


def test_per_node_precision_and_recall_are_computable(harness_db):
    from tools.genesis.harness.eval_harness import compute_node_metrics

    _seed_run()

    strong = compute_node_metrics(node_id="strong", run_id="run-42")
    weak = compute_node_metrics(node_id="weak", run_id="run-42")

    assert strong["total_decisions"] == 3
    assert strong["precision"] == 1.0
    assert strong["recall"] == 1.0

    assert weak["total_decisions"] == 3
    assert weak["precision"] == pytest.approx(1 / 3, abs=1e-4)
    assert weak["recall"] == pytest.approx(1 / 3, abs=1e-4)

    # The point of the grain: the two nodes are distinguishable, and neither
    # equals the pooled number.
    assert strong["precision"] != weak["precision"]


def test_graph_run_metrics_breaks_the_run_down_worst_node_first(harness_db):
    from tools.genesis.harness.eval_harness import graph_run_metrics

    _seed_run()

    report = graph_run_metrics("run-42")

    assert report["run_id"] == "run-42"
    assert report["node_count"] == 2
    assert report["total_decisions"] == 6
    # Run total is the blend the node grain exists to decompose.
    assert report["precision"] == pytest.approx(4 / 6, abs=1e-4)

    assert [n["node_id"] for n in report["nodes"]] == ["weak", "strong"]
    assert report["nodes"][0]["node_type"] == "agent"
    assert report["nodes"][1]["edge_condition"] == "tests_passed"


def test_node_outcome_does_not_stamp_every_node_of_the_task(harness_db):
    """record_outcome matches on task_id and would blanket the whole run."""
    from tools.genesis.harness.eval_harness import (
        compute_node_metrics,
        record_graph_node_decision,
        record_node_outcome,
    )

    for node in ("a", "b"):
        record_graph_node_decision(
            task_id="shared-task", reflex="studio_workflow", decision="proceed",
            confidence=0.8, run_id="run-7", node_id=node, node_type="tool",
        )

    result = record_node_outcome(run_id="run-7", node_id="a", actual_outcome="resolved")
    assert result["status"] == "recorded"
    assert result["rows"] == 1

    assert compute_node_metrics(node_id="a", run_id="run-7")["resolved_count"] == 1
    assert compute_node_metrics(node_id="b", run_id="run-7")["resolved_count"] == 0


def test_node_outcome_reports_when_nothing_matched(harness_db):
    from tools.genesis.harness.eval_harness import record_node_outcome

    result = record_node_outcome(run_id="run-none", node_id="ghost", actual_outcome="resolved")

    assert result["status"] == "no_decision_row"
    assert result["rows"] == 0


# ---------------------------------------------------------------------------
# Per-node_type anomaly baselines
# ---------------------------------------------------------------------------

def test_anomaly_detector_keys_thresholds_per_node_type(harness_db):
    """A tool node's baseline must not be served from the agent node's history."""
    from tools.genesis.harness.eval_harness import _AnomalyDetector

    detector = _AnomalyDetector()
    detector.get_thresholds("studio_workflow", node_type="tool")
    detector.get_thresholds("studio_workflow", node_type="agent")
    detector.get_thresholds("studio_workflow")

    assert ("studio_workflow", "tool") in detector._cache
    assert ("studio_workflow", "agent") in detector._cache
    assert ("studio_workflow", None) in detector._cache


def test_anomaly_detector_partitions_history_by_node_type(harness_db, monkeypatch):
    """The node_type filter must reach the SQL, not just the cache key."""
    from tools.genesis.harness.eval_harness import _AnomalyDetector

    _seed_run()
    detector = _AnomalyDetector()
    # chunk_size drives the snapshot window; the seeded corpus is small.
    detector.chunk_size = 2

    tool_snaps = detector._collect_snapshots("studio_workflow", node_type="tool")
    agent_snaps = detector._collect_snapshots("studio_workflow", node_type="agent")
    pooled = detector._collect_snapshots("studio_workflow")

    # 3 rows per node_type, 6 pooled → strictly more snapshots when pooled.
    assert len(pooled) > len(tool_snaps)
    # The strong node's snapshots are all perfect; the weak node's are not.
    assert all(s.get("precision") == 1.0 for s in tool_snaps if "precision" in s)
    assert any(s.get("precision", 1.0) < 1.0 for s in agent_snaps)


# ---------------------------------------------------------------------------
# Degradation on an unmigrated database
# ---------------------------------------------------------------------------

def test_graph_decision_degrades_to_task_grain_without_the_columns(legacy_harness_db):
    """Losing the grain is recoverable; losing the row is not."""
    import json

    from tools.db.storage import get_connection
    from tools.genesis.harness.eval_harness import record_graph_node_decision

    row_id = record_graph_node_decision(
        task_id="hgx-legacy", reflex="studio_workflow", decision="proceed",
        confidence=0.8, run_id="run-x", node_id="n1", node_type="agent",
    )
    assert row_id

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT metadata_json FROM harness_eval WHERE task_id = %s", ("hgx-legacy",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "the decision row must still be written"
    folded = json.loads(row[0])["graph_node"]
    assert folded["node_id"] == "n1"
    assert folded["node_type"] == "agent"
    assert folded["run_id"] == "run-x"


def test_per_node_metrics_report_zero_rather_than_lying_without_the_columns(legacy_harness_db):
    from tools.genesis.harness.eval_harness import compute_node_metrics, graph_run_metrics

    assert compute_node_metrics(node_id="n1", run_id="run-x")["total_decisions"] == 0
    assert graph_run_metrics("run-x")["nodes"] == []


# ---------------------------------------------------------------------------
# meta_harness read the wrong column name
# ---------------------------------------------------------------------------

def test_meta_harness_reads_the_column_that_actually_exists(harness_db, monkeypatch):
    """``_get_error_case_heuristic_hits`` selected ``metadata``.

    That column has never existed on harness_eval — it is ``metadata_json`` in
    migration 302, in pg_consolidated.sql and in MINIMAL_ICDEV_SCHEMA. Every
    call raised, was caught by the surrounding handler and returned ``{}``, so
    ``_propose_heuristic_retirements`` was always handed an empty hit map and
    the meta-harness has never once proposed retiring a heuristic — however bad
    precision got. This asserts the read reaches a row.
    """
    from tools.genesis.harness import meta_harness
    from tools.genesis.harness.eval_harness import record_decision, record_outcome

    monkeypatch.setattr(
        meta_harness, "_load_oracle_heuristics",
        lambda: [{"name": "stale_manifest", "reason": "tool not in manifest"}],
    )

    record_decision(
        task_id="hgx-meta-1", reflex="oracle_triage", decision="promote",
        confidence=0.9, metadata={"reason": "tool not in manifest: tools/foo.py"},
    )
    record_outcome("hgx-meta-1", "false_positive")

    hits = meta_harness._get_error_case_heuristic_hits("oracle_triage")

    assert hits == {"stale_manifest": 1}


# ---------------------------------------------------------------------------
# Schema mirrors — a migration alone leaves fresh installs behind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "relative_path",
    [
        "tests/conftest.py",
        "tools/db/schema/pg_consolidated.sql",
        "icdev/tools/db/schema/pg_consolidated.sql",
    ],
)
def test_schema_mirrors_carry_the_graph_columns(relative_path):
    """A fresh PostgreSQL bootstrap is built from pg_consolidated.sql, not by
    replaying migrations, and the test suite is built from MINIMAL_ICDEV_SCHEMA.
    Omitting either breaks only fresh installs — the hardest failure to notice."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / relative_path).read_text(encoding="utf-8")

    match = re.search(
        r"CREATE TABLE (?:IF NOT EXISTS |public\.)?harness_eval \((.*?)\);",
        text,
        re.DOTALL,
    )
    assert match, f"{relative_path} declares no harness_eval table"
    body = match.group(1)
    for column in _GRAPH_COLUMNS:
        assert column in body, f"{relative_path} harness_eval is missing {column}"
