# CUI // SP-CTI
"""Tests for tools/foundry/engine.py — run_cycle() orchestration + CLI + rate limits.

Hermetic: a throwaway file-backed SQLite DB holds the foundry_* tables plus a
minimal kanban_tasks slice. ``init_db`` is stubbed so the engine never touches the
repo database, and ``tools.db.storage.get_connection`` is pointed at the temp DB so
``run_cycle`` / ``main`` run end-to-end without a real backend.

Two layers of coverage:

  * acf-engine-01 — the run_cycle ORCHESTRATION contract: a seeded set drives a
    full harvest->synth->novelty->score->CoD->spec->task_graph->emit cycle that
    emits a concept's tasks (deliberator mocked 'build'); a duplicate-only set
    emits zero; a mocked 'reject' verdict blocks a score-passing concept. The
    sibling stage modules (synthesizer / scorer / deliberator / seeder) are wired
    in via the engine's own signature-aware ``_opt_module`` seam, so these tests
    exercise the real stage-wiring code path without depending on those modules
    having landed.

  * acf-engine-02 — the rate-limit + CLI deliverable: the active-project counter,
    run_cycle short-circuiting to status='rate_limited' before emit, --dry-run
    never seeding, and the JSON shape returned by --run / --status via main().
"""
from __future__ import annotations

import json
import sqlite3
import types

import pytest

from tools.foundry import engine
from tools.foundry.db.init_db import _SCHEMA_SQLITE

# Minimal kanban_tasks slice — only the columns _active_project_count reads.
_KANBAN_DDL = "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT);"


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
# Orchestration helpers — wire the not-yet-shipped stages in via the engine's
# own _opt_module seam so the FULL run_cycle code path is exercised.
# ---------------------------------------------------------------------------
def _mock_stages(monkeypatch, *, concepts, decision, score=0.9, emitted_per_concept=None):
    """Install mock synthesizer / scorer / deliberator / seeder stages.

    The deliberator returns ``decision`` for every concept; the scorer stamps
    ``score`` as the composite; the seeder reports one 'emitted' per task it is
    handed. Returns a dict the test can inspect (e.g. ``emitted_calls``).
    """
    state = {"emitted_calls": [], "synth_calls": 0, "delib_calls": 0}

    def synthesize(**kwargs):
        state["synth_calls"] += 1
        # Hand back deep copies so the engine's in-place status edits don't leak
        # across a parametrized re-run.
        return [dict(c) for c in concepts]

    def score(**kwargs):
        kwargs["concept"]["composite_score"] = score

    def deliberate_concept(**kwargs):
        state["delib_calls"] += 1
        return {"decision": decision, "cot_trace_id": "cot-test-1"}

    def emit(**kwargs):
        tasks = kwargs.get("tasks") or []
        state["emitted_calls"].append(len(tasks))
        n = emitted_per_concept if emitted_per_concept is not None else len(tasks)
        return {"emitted": n}

    mocks = {
        "synthesizer": types.SimpleNamespace(synthesize=synthesize),
        "scorer": types.SimpleNamespace(score=score),
        "deliberator": types.SimpleNamespace(deliberate_concept=deliberate_concept),
        "seeder": types.SimpleNamespace(emit=emit),
    }
    monkeypatch.setattr(engine, "_opt_module", lambda name: mocks.get(name))
    return state


def _patch_novelty(monkeypatch, *, reject=False):
    """Pin the (real, catalog-dependent) novelty gate to a deterministic verdict."""
    from tools.foundry import novelty_gate

    def apply(concept, **kwargs):
        concept["novelty_score"] = 0.2 if reject else 0.9
        if reject:
            concept["status"] = "rejected"
            concept["reject_reason"] = "duplicate"
        return concept

    monkeypatch.setattr(novelty_gate, "apply_novelty_gate", apply)


_CONCEPT = {
    "name": "Quantum Threat Path Simulator",
    "slug": "quantum-threat-path-sim",
    "proposed_capability": "Continuously simulate post-quantum attack paths across the ATO boundary.",
    "problem_statement": "Teams cannot see how a harvest-now-decrypt-later adversary traverses the network.",
    "target_users": "ISSO, red team, accreditation officers",
}


# ---------------------------------------------------------------------------
# acf-engine-01 — run_cycle orchestration contract
# ---------------------------------------------------------------------------
def test_run_cycle_full_pipeline_emits_tasks(db, monkeypatch):
    """A seeded signal set drives a full cycle that emits a concept's tasks
    (deliberator mocked 'build')."""
    _patch_novelty(monkeypatch, reject=False)
    state = _mock_stages(monkeypatch, concepts=[_CONCEPT], decision="build")

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "completed"
    assert result["concepts_proposed"] == 1
    assert result["concepts_approved"] == 1
    # spec_generator + task_graph ran for real and produced >=1 task that the
    # seeder reported as emitted.
    assert result["tasks_emitted"] >= 1
    assert state["emitted_calls"] and state["emitted_calls"][0] >= 1
    assert state["delib_calls"] == 1


def test_run_cycle_duplicate_only_emits_zero(db, monkeypatch):
    """A duplicate-only set is dropped by the novelty gate -> zero approved,
    zero emitted, and the deliberator is never consulted."""
    _patch_novelty(monkeypatch, reject=True)
    state = _mock_stages(monkeypatch, concepts=[_CONCEPT, dict(_CONCEPT, slug="dup-2")], decision="build")

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "completed"
    assert result["concepts_proposed"] == 2
    assert result["concepts_approved"] == 0
    assert result["tasks_emitted"] == 0
    assert state["emitted_calls"] == []   # seeder never invoked
    assert state["delib_calls"] == 0      # gate short-circuits before CoD


def test_run_cycle_reject_verdict_blocks_scoring_concept(db, monkeypatch):
    """A mocked 'reject' CoD verdict blocks a concept that passes novelty + score."""
    _patch_novelty(monkeypatch, reject=False)
    state = _mock_stages(monkeypatch, concepts=[_CONCEPT], decision="reject")

    result = engine.run_cycle(conn=_new_conn(db))

    assert result["status"] == "completed"
    assert result["concepts_proposed"] == 1
    assert result["concepts_approved"] == 0   # CoD blocked it despite a passing score
    assert result["tasks_emitted"] == 0
    assert state["delib_calls"] == 1          # the verdict WAS consulted
    assert state["emitted_calls"] == []       # ...and it stopped the emit


def test_run_cycle_dry_run_full_pipeline_no_write(db, monkeypatch):
    """dry_run runs synth/novelty/score/CoD/spec/task_graph but the seeder must
    not be asked to write -> tasks_emitted stays 0."""
    _patch_novelty(monkeypatch, reject=False)
    state = _mock_stages(monkeypatch, concepts=[_CONCEPT], decision="build")

    result = engine.run_cycle(conn=_new_conn(db), dry_run=True)

    assert result["status"] == "completed"
    assert result["dry_run"] is True
    assert result["concepts_approved"] == 1   # full pipeline still ran
    assert result["tasks_emitted"] == 0       # ...but nothing seeded
    assert state["emitted_calls"] == []       # seeder.emit never called in dry_run


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
# run_cycle — rate-limit short-circuit (acf-engine-02)
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
