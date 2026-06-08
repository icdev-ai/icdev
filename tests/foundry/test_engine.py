# CUI // SP-CTI
"""Tests for tools/foundry/engine.py — orchestration CLI + rate-limit enforcement (acf-engine-02)
plus the circuit-breaker + self-vet wiring from acf-engine-03.

Hermetic: a throwaway file-backed SQLite DB holds the foundry_* tables plus a
minimal kanban_tasks slice. ``init_db`` is stubbed so the engine never touches the
repo database, and ``tools.db.storage.get_connection`` is pointed at the temp DB so
``main()`` runs end-to-end without a real backend.

Focus (the acf-engine-02 deliverable):
  * the active-project rate-limit counter,
  * run_cycle short-circuiting to status='rate_limited' before emit,
  * --dry-run never seeding,
  * the JSON shape returned by --run / --status via main().

Focus (acf-engine-03):
  * circuit breaker: a high V&V fail-rate in foundry_outcomes opens the breaker
    and writes exactly one kanban HITL card; a clean window does NOT trip,
  * self-vet: every emitted build task carries integrity_gate=True (the
    tools.foundry.task_graph writer) and the engine threads the self_vet config
    block through to the seeder.

The synthesizer / scorer / deliberator / seeder stage modules are intentionally
absent in this branch; run_cycle degrades to zero concepts, which is exactly the
condition the rate-limit gate must still handle correctly. The self-vet marker
is tested directly against tools.foundry.task_graph.build_task_graph (which is
shipped), independent of the seeder being missing.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools.foundry import engine
from tools.foundry.db.init_db import _SCHEMA_SQLITE

# Minimal kanban_tasks slice — the columns _active_project_count + the
# circuit-breaker HITL card insert touch. Other columns default to NULL.
_KANBAN_DDL = """
CREATE TABLE kanban_tasks (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    description         TEXT,
    task_type           TEXT,
    priority            TEXT,
    status              TEXT,
    dispatch_source     TEXT,
    hitl_stage          TEXT,
    last_failure_reason TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    tenant_id           TEXT,
    classification      TEXT
);
"""


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _seed_project(conn, slug, task_status):
    """Insert one ACF concept + its emitted kanban task with the given task status."""
    cur = conn.execute(
        "INSERT INTO foundry_concepts (run_id, name, slug, status) VALUES (?, ?, ?, 'approved')",
        ("r1", slug.upper(), slug),
    )
    concept_id = cur.lastrowid
    task_id = f"{slug}-db-01"
    conn.execute("INSERT INTO kanban_tasks (id, status) VALUES (?, ?)", (task_id, task_status))
    conn.execute(
        "INSERT INTO foundry_tasks_emitted (concept_id, kanban_task_id) VALUES (?, ?)",
        (concept_id, task_id),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB wired into the engine (init_db stubbed, get_connection
    repointed). Yields the db path; open fresh connections via _new_conn(path)."""
    path = str(tmp_path / "foundry_test.db")
    boot = _new_conn(path)
    boot.executescript(_SCHEMA_SQLITE)
    boot.executescript(_KANBAN_DDL)
    boot.commit()
    boot.close()

    # Never touch the platform DB: stub every init_db the engine / harvester call.
    monkeypatch.setattr(engine, "init_db", lambda *a, **k: True)
    from tools.foundry import harvester

    monkeypatch.setattr(harvester, "init_db", lambda *a, **k: True)
    # Force the SQLite lastrowid path in _open_run.
    monkeypatch.setattr(engine, "_is_pg", lambda: False)

    # Point get_connection at the temp DB (a new connection per call so the
    # engine's own conn.close() never breaks a later call). Patch the canonical
    # module object the engine's `from tools.db.storage import get_connection`
    # resolves to (the tools.* shim makes the `import ... as` form fail).
    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    yield path


# ---------------------------------------------------------------------------
# _active_project_count
# ---------------------------------------------------------------------------
def test_active_project_count_counts_undone_projects(db):
    conn = _new_conn(db)
    _seed_project(conn, "alpha", "scheduled")
    _seed_project(conn, "beta", "in_progress")
    _seed_project(conn, "gamma", "done")  # done -> not counted
    try:
        assert engine._active_project_count(conn) == 2
    finally:
        conn.close()


def test_active_project_count_zero_when_no_kanban_table(db, tmp_path):
    # A bare foundry DB without kanban_tasks must degrade to 0, not raise.
    bare = str(tmp_path / "bare.db")
    c = _new_conn(bare)
    c.executescript(_SCHEMA_SQLITE)
    c.commit()
    try:
        assert engine._active_project_count(c) == 0
    finally:
        c.close()


# ---------------------------------------------------------------------------
# run_cycle — rate-limit short-circuit
# ---------------------------------------------------------------------------
def test_run_cycle_rate_limited_when_active_at_cap(db):
    conn = _new_conn(db)
    for slug in ("alpha", "beta", "gamma"):  # 3 active == default max_active_projects
        _seed_project(conn, slug, "scheduled")
    conn.close()

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "rate_limited"
    assert result["rate_limited"] is True
    assert result["tasks_emitted"] == 0
    assert result["active_projects"] == 3
    assert "max_active_projects" in result["detail"]["reason"]

    # The run row is finalized as rate_limited (constants extended to allow it).
    chk = _new_conn(db)
    try:
        row = chk.execute(
            "SELECT status, tasks_emitted FROM foundry_runs WHERE id=?", (result["id"],)
        ).fetchone()
    finally:
        chk.close()
    assert row["status"] == "rate_limited"
    assert row["tasks_emitted"] == 0


def test_run_cycle_completes_under_cap(db):
    # Only 1 active project (< cap of 3) -> not rate limited.
    conn = _new_conn(db)
    _seed_project(conn, "alpha", "scheduled")
    conn.close()

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "completed"
    assert "rate_limited" not in result
    assert result["active_projects"] == 1
    assert result["tasks_emitted"] == 0  # no synthesizer/seeder -> nothing to emit


def test_run_cycle_custom_max_concepts_threads_through(db):
    result = engine.run_cycle(conn=_new_conn(db), max_concepts=2)
    assert result["detail"]["max_concepts_per_cycle"] == 2


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------
def test_status_shape(db):
    # One finished run + one active project.
    engine.run_cycle(conn=_new_conn(db))
    seed = _new_conn(db)
    _seed_project(seed, "alpha", "scheduled")
    seed.close()

    snap = engine.status()
    assert set(snap.keys()) == {"recent_runs", "active_projects", "pipeline", "rate_limits"}
    assert snap["active_projects"] == 1
    assert isinstance(snap["recent_runs"], list) and len(snap["recent_runs"]) >= 1
    assert snap["rate_limits"]["max_active_projects"] == 3
    # concept status counts (the seeded concept is 'approved').
    assert snap["pipeline"].get("approved") == 1


# ---------------------------------------------------------------------------
# main() — CLI JSON shape + rate-limit short-circuit
# ---------------------------------------------------------------------------
def test_main_status_json(db, capsys):
    rc = engine.main(["--status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "recent_runs" in payload
    assert "active_projects" in payload
    assert "rate_limits" in payload


def test_main_run_json_dry_run(db, capsys):
    rc = engine.main(["--run", "--dry-run", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["status"] == "completed"
    assert payload["tasks_emitted"] == 0
    for key in ("run_id", "harvested", "concepts_proposed", "concepts_approved", "active_projects"):
        assert key in payload


def test_main_run_json_rate_limited_short_circuit(db, capsys):
    seed = _new_conn(db)
    for slug in ("alpha", "beta", "gamma"):
        _seed_project(seed, slug, "scheduled")
    seed.close()

    rc = engine.main(["--run", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rate_limited"
    assert payload["rate_limited"] is True
    assert payload["tasks_emitted"] == 0


def test_main_requires_a_mode(db):
    with pytest.raises(SystemExit):
        engine.main([])  # neither --run nor --status -> argparse error


# ===========================================================================
# acf-engine-03 — CIRCUIT BREAKER + SELF-VET
# ===========================================================================
def _seed_outcome(conn, slug, outcome):
    """Insert one ACF concept + one outcome row tied to it (used for breaker tests).

    The slug is suffixed with a millisecond-precision stamp + row counter so
    repeated calls (e.g. test_run_cycle_circuit_open_idempotent_per_run runs
    run_cycle twice on the same DB) never collide on the foundry_concepts.slug
    UNIQUE constraint. Outcomes on the same concept are accepted by appending
    a second outcome row referencing the same concept_id.
    """
    import time as _time
    nonce = f"{_time.time_ns()}"
    unique_slug = f"{slug}-{nonce}"
    cur = conn.execute(
        "INSERT INTO foundry_concepts (run_id, name, slug, status) VALUES (?, ?, ?, 'approved')",
        ("r0", unique_slug.upper(), unique_slug),
    )
    concept_id = cur.lastrowid
    conn.execute(
        "INSERT INTO foundry_outcomes (concept_id, outcome) VALUES (?, ?)",
        (concept_id, outcome),
    )
    conn.commit()


def _seed_window(conn, outcomes):
    """Seed a sequence of (slug, outcome) pairs — most-recent outcomes are appended last."""
    for slug, outcome in outcomes:
        _seed_outcome(conn, slug, outcome)


# ---------------------------------------------------------------------------
# _recent_vv_fail_rate
# ---------------------------------------------------------------------------
def test_recent_vv_fail_rate_window_and_rate(db):
    # 6 fails + 2 passes + 2 abandoned (inconclusive) in a window of 10.
    # The breaker counts only known tokens: 6 fail + 2 pass = 8 counted, so
    # fail_rate = 6/8 = 0.75 (abandoned is dropped from both numerator + denom).
    seq = (
        [("c1", "vv_fail")] * 6
        + [("c2", "vv_pass")] * 2
        + [("c3", "abandoned"), ("c4", "abandoned")]
    )
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    conn = _new_conn(db)
    try:
        stats = engine._recent_vv_fail_rate(conn, window=10)
    finally:
        conn.close()
    assert stats is not None
    assert stats["total"] == 8       # abandoned is excluded from the denominator
    assert stats["fail_count"] == 6
    assert stats["window"] == 10
    assert stats["fail_rate"] == pytest.approx(0.75)


def test_recent_vv_fail_rate_no_outcomes_returns_none(db):
    conn = _new_conn(db)
    try:
        assert engine._recent_vv_fail_rate(conn, window=10) is None
    finally:
        conn.close()


def test_recent_vv_fail_rate_abandoned_is_inconclusive(db):
    # abandoned outcomes do NOT count as fails and do NOT count as passes —
    # the breaker must not be biased by abandoned rows in either direction.
    conn = _new_conn(db)
    _seed_window(conn, [("c1", "abandoned"), ("c2", "abandoned")])
    conn.close()

    conn = _new_conn(db)
    try:
        stats = engine._recent_vv_fail_rate(conn, window=10)
    finally:
        conn.close()
    # No vv_pass / vv_fail rows => the engine should not surface a stat that
    # the breaker would act on (an inconclusive window is silent).
    assert stats is None


# ---------------------------------------------------------------------------
# _circuit_breaker_open
# ---------------------------------------------------------------------------
def test_circuit_breaker_open_when_fail_rate_exceeds_threshold(db):
    # 5 fails + 1 pass over window=10 -> 5/6 = 0.83 > default 0.5 => open.
    seq = [("c1", "vv_fail")] * 5 + [("c2", "vv_pass")]
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    conn = _new_conn(db)
    try:
        opened, stats, cb_cfg = engine._circuit_breaker_open(conn, {})
    finally:
        conn.close()
    assert opened is True
    assert stats["fail_count"] == 5
    assert stats["total"] == 6
    assert cb_cfg["vv_fail_rate"] == 0.5
    assert cb_cfg["window"] == 10


def test_circuit_breaker_does_not_open_below_threshold(db):
    # 1 fail out of 10 => 0.1 < 0.5 => closed.
    seq = [("c1", "vv_fail")] + [("c2", "vv_pass")] * 9
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    conn = _new_conn(db)
    try:
        opened, stats, _cb = engine._circuit_breaker_open(conn, {})
    finally:
        conn.close()
    assert opened is False
    assert stats is not None and stats["fail_count"] == 1


def test_circuit_breaker_silent_when_no_outcomes(db):
    conn = _new_conn(db)
    try:
        opened, stats, cb = engine._circuit_breaker_open(conn, {})
    finally:
        conn.close()
    # An empty window must NOT trip the breaker (would be noise on a fresh DB).
    assert opened is False
    assert stats is None
    assert cb["vv_fail_rate"] == 0.5


# ---------------------------------------------------------------------------
# run_cycle — circuit-open short-circuit + exactly-one HITL card
# ---------------------------------------------------------------------------
def test_run_cycle_circuit_open_writes_exactly_one_hitl_card(db, monkeypatch):
    # High V&V fail-rate fixture: 4 fails + 1 pass => 0.8 > 0.5 => open.
    seq = [("c1", "vv_fail")] * 4 + [("c2", "vv_pass")]
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "circuit_open"
    assert result["circuit_open"] is True
    assert result["tasks_emitted"] == 0
    assert "circuit breaker open" in result["detail"]["reason"]
    assert result["detail"]["hitl_card_id"] == f"acf-hitl-circuit-{result['id']}"

    # Exactly one kanban HITL card was written, with the right shape.
    chk = _new_conn(db)
    try:
        rows = chk.execute(
            "SELECT id, status, task_type, priority, hitl_stage, dispatch_source "
            "FROM kanban_tasks WHERE id LIKE 'acf-hitl-circuit-%'"
        ).fetchall()
    finally:
        chk.close()
    assert len(rows) == 1
    card = dict(rows[0])
    assert card["status"] == "backlog"
    assert card["task_type"] == "hitl"
    assert card["priority"] == "critical"
    assert card["hitl_stage"] == "circuit_breaker"
    assert card["dispatch_source"] == "foundry_circuit_breaker"

    # The run row is finalized as circuit_open.
    chk = _new_conn(db)
    try:
        row = chk.execute(
            "SELECT status, tasks_emitted FROM foundry_runs WHERE id=?",
            (result["id"],),
        ).fetchone()
    finally:
        chk.close()
    assert dict(row)["status"] == "circuit_open"
    assert dict(row)["tasks_emitted"] == 0


def test_run_cycle_circuit_open_idempotent_per_run(db, monkeypatch):
    # A second cycle in the same run must not write a duplicate HITL card.
    seq = [("c1", "vv_fail")] * 4 + [("c2", "vv_pass")]
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    r1 = engine.run_cycle(conn=_new_conn(db))
    r2 = engine.run_cycle(conn=_new_conn(db))
    assert r1["status"] == r2["status"] == "circuit_open"

    chk = _new_conn(db)
    try:
        n_cards = chk.execute(
            "SELECT COUNT(*) AS n FROM kanban_tasks WHERE id LIKE 'acf-hitl-circuit-%'"
        ).fetchone()["n"]
    finally:
        chk.close()
    # One card per unique run_id — the test runs two separate run_ids so the
    # two cycles may legitimately write two cards, but each cycle writes EXACTLY ONE.
    assert n_cards == 2
    for rid in (r1["id"], r2["id"]):
        chk = _new_conn(db)
        try:
            row = chk.execute(
                "SELECT COUNT(*) AS n FROM kanban_tasks WHERE id = ?",
                (f"acf-hitl-circuit-{rid}",),
            ).fetchone()
        finally:
            chk.close()
        assert row["n"] == 1, f"expected exactly one card for run {rid}"


def test_run_cycle_circuit_closed_emits_normally(db):
    # Clean window: 1 fail + 9 passes => 0.1 < 0.5 => closed. Cycle completes
    # (no synthesizer / seeder in this branch => tasks_emitted stays 0, but
    # the status is 'completed', not 'circuit_open').
    seq = [("c1", "vv_fail")] + [("c2", "vv_pass")] * 9
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    result = engine.run_cycle(conn=_new_conn(db))
    assert result["status"] == "completed"
    assert "circuit_open" not in result

    chk = _new_conn(db)
    try:
        n_cards = chk.execute(
            "SELECT COUNT(*) AS n FROM kanban_tasks WHERE id LIKE 'acf-hitl-circuit-%'"
        ).fetchone()["n"]
    finally:
        chk.close()
    assert n_cards == 0


def test_run_cycle_circuit_open_with_custom_config(db):
    # A custom config with vv_fail_rate=0.95 + window=5 — only 1 fail in 5
    # must NOT trip the breaker (the new threshold is very high).
    seq = [("c1", "vv_fail")] * 3 + [("c2", "vv_pass")] * 2
    conn = _new_conn(db)
    _seed_window(conn, seq)
    conn.close()

    cfg = {
        "circuit": {"vv_fail_rate": 0.95, "window": 5},
    }
    result = engine.run_cycle(conn=_new_conn(db), config=cfg)
    assert result["status"] == "completed"
    assert result["detail"]["circuit"]["vv_fail_rate"] == 0.95
    assert result["detail"]["circuit"]["window"] == 5


# ---------------------------------------------------------------------------
# _ensure_hitl_circuit_card — direct unit
# ---------------------------------------------------------------------------
def test_ensure_hitl_circuit_card_inserts_once(db):
    conn = _new_conn(db)
    try:
        stats = {"fail_rate": 0.7, "fail_count": 7, "total": 10, "window": 10}
        cb_cfg = {"vv_fail_rate": 0.5, "window": 10}
        card1 = engine._ensure_hitl_circuit_card(
            conn=conn, run_id="42", tenant_id="t1", classification="CUI",
            stats=stats, cb_cfg=cb_cfg,
        )
        card2 = engine._ensure_hitl_circuit_card(
            conn=conn, run_id="42", tenant_id="t1", classification="CUI",
            stats=stats, cb_cfg=cb_cfg,
        )
    finally:
        conn.close()
    assert card1 == card2 == "acf-hitl-circuit-42"


# ---------------------------------------------------------------------------
# self-vet — engine threads the config block; task_graph stamps the marker
# ---------------------------------------------------------------------------
def test_engine_threads_self_vet_config_into_run_detail(db):
    cfg = {
        "self_vet": {
            "require_integrity_gate": True,
            "require_security_gate": False,  # operator flip — make sure it threads
        }
    }
    result = engine.run_cycle(conn=_new_conn(db), config=cfg)
    assert result["detail"]["self_vet"] == cfg["self_vet"]
    assert result["detail"]["self_vet"]["require_security_gate"] is False


def test_self_vet_defaults_when_config_absent(db):
    result = engine.run_cycle(conn=_new_conn(db))
    assert result["detail"]["self_vet"]["require_integrity_gate"] is True
    assert result["detail"]["self_vet"]["require_security_gate"] is True


def test_self_vet_marker_on_every_build_task():
    """Every task returned by build_task_graph that is a 'build' MUST carry
    ``integrity_gate=True`` and embed the textual marker — that's the
    foundry_self_vet gate's load-bearing input."""
    from tools.foundry.spec_generator import build_canvas_contract
    from tools.foundry.task_graph import (
        INTEGRITY_GATE_MARKER, build_task_graph,
    )

    concept = {
        "name": "Test Capability",
        "slug": "test-capability",
        "problem_statement": "x",
        "proposed_capability": "y",
        "target_users": "z",
    }
    contract = build_canvas_contract(concept)
    tasks = build_task_graph(concept, contract)

    build_tasks = [t for t in tasks if t["task_type"] == "build"]
    assert build_tasks, "task graph should produce at least one build task"
    for t in build_tasks:
        assert t.get("integrity_gate") is True, (
            f"build task {t['id']!r} missing integrity_gate=True"
        )
        assert INTEGRITY_GATE_MARKER in t["description"], (
            f"build task {t['id']!r} description missing {INTEGRITY_GATE_MARKER!r}"
        )

    # Non-build tasks (chore / test) intentionally do NOT carry the marker.
    non_build = [t for t in tasks if t["task_type"] != "build"]
    for t in non_build:
        assert not t.get("integrity_gate"), (
            f"non-build task {t['id']!r} unexpectedly carries integrity_gate"
        )


def test_foundry_self_vet_gate_present_in_security_gates_yaml():
    """The acf-engine-03 deliverable explicitly requires a 'Foundry Self-Vet'
    gate in args/security_gates.yaml. This test guards against accidental
    removal of that section (the gate is the policy the pre-merge hook
    enforces against ACF-emitted patches)."""
    import yaml
    from pathlib import Path

    base = Path(engine.__file__).resolve().parents[2]
    with (base / "args" / "security_gates.yaml").open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    assert "foundry_self_vet" in cfg, (
        "args/security_gates.yaml must define foundry_self_vet (acf-engine-03)"
    )
    gate = cfg["foundry_self_vet"]
    assert "blocking" in gate
    assert "acf_emitted_task_missing_integrity_gate" in gate["blocking"]
    assert "sipa_gate_failed_on_acf_patch" in gate["blocking"]
    assert "security_gate_failed_on_acf_patch" in gate["blocking"]
    assert "coherence_gate_failed_on_acf_patch" in gate["blocking"]
