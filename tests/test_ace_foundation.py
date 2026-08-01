"""Tests for ACE foundation modules: role_loader, problem_classifier, team_assembler, db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError
from icdev.tools.ace.problem_classifier import ProblemClassifierLens
from icdev.tools.ace.db.init_db import SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _roles_dir():
    return Path(__file__).parent.parent / "args" / "ace" / "roles"


@pytest.fixture()
def role_loader(_roles_dir):
    return RoleLoader(roles_dir=_roles_dir, hot_reload=False)


@pytest.fixture()
def ace_db_path(tmp_path):
    """Temp-file SQLite DB with ACE schema pre-applied."""
    db_path = tmp_path / "ace_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Role loader — loads YAML files
# ---------------------------------------------------------------------------

def test_role_loader_loads_yaml(role_loader):
    roles = {r.role_id for r in role_loader.list_roles()}
    assert "ai_developer" in roles
    assert "qa_manager" in roles


def test_role_loader_missing_raises(role_loader):
    with pytest.raises(RoleNotFoundError):
        role_loader.get_role("nonexistent_role_xyz")


# ---------------------------------------------------------------------------
# 2. Problem classifier — fallback + domain scoring
# ---------------------------------------------------------------------------

def test_problem_classifier_fallback(role_loader, monkeypatch):
    """Short/ambiguous input falls back to default team [ai_developer, qa_manager]."""
    monkeypatch.setattr(ProblemClassifierLens, "_llm_suggest_roles", lambda self: [])
    lens = ProblemClassifierLens("hi", role_loader=role_loader)
    manifest = lens.run()
    role_ids = {s.role_id for s in manifest.slots}
    assert "ai_developer" in role_ids
    assert "qa_manager" in role_ids


def test_problem_classifier_build_request(role_loader, monkeypatch):
    """'Build a REST API' scores the build domain above zero and includes ai_developer."""
    monkeypatch.setattr(ProblemClassifierLens, "_llm_suggest_roles", lambda self: [])
    text = "Build a REST API for managing user accounts."
    lens = ProblemClassifierLens(text, role_loader=role_loader)
    analysis = lens.analyze()
    assert analysis["keyword_scores"].get("build", 0.0) > 0.0

    manifest = lens.run()
    assert any(s.role_id == "ai_developer" for s in manifest.slots)


# ---------------------------------------------------------------------------
# 3. Team assembler — DB persistence
# ---------------------------------------------------------------------------

def test_team_assembler_creates_db_rows(monkeypatch, role_loader, ace_db_path):
    """TeamAssembler.assemble() inserts rows into ace_instances and ace_coworkers."""
    import icdev.tools.db.storage as _storage
    import icdev.tools.ace.db.init_db as _init_mod

    def _fake_conn(env_var=None):
        # Wrap in storage's own connection type rather than handing back a bare
        # sqlite3 one. The production code under test writes PostgreSQL-dialect
        # SQL ("... WHERE id = %s"), and get_canvas_connection returns a
        # StorageConnection whose execute() rewrites %s to ? on SQLite. A raw
        # sqlite3.Connection does not, so this fake used to make every statement
        # fail with `near "%": syntax error` — a defect in the double, reported as
        # AssemblyError from the module it was meant to be testing.
        return _storage.StorageConnection(
            sqlite3.connect(str(ace_db_path)), backend="sqlite"
        )

    monkeypatch.setattr(_storage, "get_canvas_connection", _fake_conn)
    monkeypatch.setattr(_init_mod, "init", lambda: None)

    from icdev.tools.ace.team_assembler import TeamAssembler
    from icdev.tools.ace.problem_classifier import TeamManifest, RoleSlot

    manifest = TeamManifest(slots=[
        RoleSlot(role_id="ai_developer", count=1),
        RoleSlot(role_id="qa_manager", count=1),
    ])
    assembler = TeamAssembler(role_loader=role_loader)
    instance = assembler.assemble(
        manifest=manifest,
        instance_id="test-inst-001",
        context={"problem_text": "Build a REST API", "name": "test-instance"},
    )

    conn = sqlite3.connect(str(ace_db_path))
    try:
        inst_rows = conn.execute(
            "SELECT id FROM ace_instances WHERE id = ?", (instance.instance_id,)
        ).fetchall()
        cw_rows = conn.execute(
            "SELECT id FROM ace_coworkers WHERE instance_id = ?", (instance.instance_id,)
        ).fetchall()
    finally:
        conn.close()

    assert len(inst_rows) == 1, "ace_instances row should be created"
    assert len(cw_rows) == 2, "one ace_coworkers row per slot expected"


# ---------------------------------------------------------------------------
# 4. DB schema — all tables created by init_db
# ---------------------------------------------------------------------------

def test_db_tables_exist():
    """init_db SCHEMA creates the 5 core ACE tables."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ace_%' ORDER BY name"
    ).fetchall()
    conn.close()

    table_names = {r[0] for r in rows}
    expected = {
        "ace_instances",
        "ace_coworkers",
        "ace_messages",
        "ace_artifacts",
        "ace_agent_workflows",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


def test_schema_creates_every_required_table():
    """Every table in controller._REQUIRED_ACE_TABLES must have DDL in SCHEMA.

    Guards against the latent "relation does not exist" class of bug: a table
    named in the required-tables contract (and written by runtime code) but with
    no CREATE TABLE anywhere -- exactly the ace_webhook_log gap fixed in
    hcx-ace-02. Iterates the constant and SELECTs from each table so a future
    addition to the contract without matching DDL fails here.
    """
    from icdev.tools.ace.controller import _REQUIRED_ACE_TABLES

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        missing = []
        for table in _REQUIRED_ACE_TABLES:
            try:
                conn.execute(f"SELECT * FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                missing.append(table)
    finally:
        conn.close()

    assert not missing, f"_REQUIRED_ACE_TABLES with no DDL in SCHEMA: {missing}"


def test_webhook_log_table_present_and_writable():
    """ace_webhook_log exists in SCHEMA with the columns webhook._log_attempt writes."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO ace_webhook_log"
            " (instance_id, url, status_code, response, attempt_count, last_attempted_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("inst-1", "http://x.test/hook", 200, "OK", 1, "2026-07-17T00:00:00", "2026-07-17T00:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT instance_id, status_code, attempt_count FROM ace_webhook_log ORDER BY id"
        ).fetchone()
    finally:
        conn.close()

    assert row == ("inst-1", 200, 1)


# ---------------------------------------------------------------------------
# 5. Controller dispatch — per-role trust parallelism (hcx-ace-06)
# ---------------------------------------------------------------------------

def test_controller_per_role_parallelism(monkeypatch):
    """A mixed-trust team dispatches each role at its OWN max_parallel.

    Builds a team of two roles — a trusted role (max_parallel=4) and a
    supervised role (max_parallel=1) — with FAKE coworker threads whose run
    bodies just sleep and record start/end timestamps.  Asserts:
      * the trusted role's coworkers overlap in time (all run at once), and
      * the supervised role's coworkers are serialized (non-overlapping), and
      * the whole team completes.

    This exercises the real ``ACEController._run`` dispatch path (per-role
    BoundedSemaphores + direct thread start + controller-level join loop) with
    every DB / LLM / event dependency stubbed out — NO live LLM.
    """
    import threading
    import time
    from types import SimpleNamespace

    from icdev.tools.ace.controller import ACEController

    TRUSTED = "autonomous_role"   # max_parallel = 4
    SUPERVISED = "supervised_role"  # max_parallel = 1
    SLEEP = 0.3

    records: list[tuple[str, float, float]] = []
    rec_lock = threading.Lock()

    # Fake coworker thread: a real threading.Thread whose run() sleeps briefly
    # and records its [start, end] wall-clock interval, keyed by role_id.
    class _FakeCoWorker(threading.Thread):
        def __init__(self, spec, instance_id, message_bus, trust_kernel, monitor_interval=None):
            super().__init__(name=f"fake-cw-{spec.coworker_id}", daemon=True)
            self.spec = spec
            self.instance_id = instance_id
            self._stop_event = threading.Event()

        def stop(self):
            self._stop_event.set()

        def run(self):
            start = time.monotonic()
            time.sleep(SLEEP)
            end = time.monotonic()
            with rec_lock:
                records.append((self.spec.role_id, start, end))

    # Fake team: 3 coworkers of each role.
    specs = (
        [SimpleNamespace(role_id=TRUSTED, coworker_id=f"{TRUSTED}-{i}") for i in range(3)]
        + [SimpleNamespace(role_id=SUPERVISED, coworker_id=f"{SUPERVISED}-{i}") for i in range(3)]
    )

    class _FakeTeam:
        def __init__(self, specs):
            self.specs = specs

    class _FakeAssembler:
        def __init__(self, *a, **k):
            pass

        def assemble(self, manifest, instance_id, context):
            return _FakeTeam(specs)

    def _fake_dispatch_config(role_id):
        mp = 4 if role_id == TRUSTED else 1
        return {
            "max_parallel": mp,
            "band": "autonomous" if mp == 4 else "supervised",
            "trust_score": 0.9 if mp == 4 else 0.5,
        }

    class _FakeStub:
        def __init__(self, *a, **k):
            pass

    def _boom(*a, **k):
        raise RuntimeError("no DB in this unit test")

    # Patch the SOURCE modules (_run uses local `from X import Y`).
    import icdev.tools.ace.team_assembler as _ta_mod
    import icdev.tools.ace.trust_calibrator as _tc_mod
    import icdev.tools.ace.coworker_thread as _cw_mod
    import icdev.tools.ace.message_bus as _mb_mod
    import icdev.tools.daemon.base as _daemon_mod
    import icdev.tools.db.storage as _storage_mod

    monkeypatch.setattr(_ta_mod, "TeamAssembler", _FakeAssembler)
    monkeypatch.setattr(_tc_mod, "get_dispatch_config", _fake_dispatch_config)
    monkeypatch.setattr(_cw_mod, "CoWorkerThread", _FakeCoWorker)
    monkeypatch.setattr(_mb_mod, "MessageBus", _FakeStub)
    monkeypatch.setattr(_daemon_mod, "TrustKernelBase", _FakeStub)
    monkeypatch.setattr(_storage_mod, "get_canvas_connection", _boom)

    ctrl = ACEController()
    # Stub every best-effort DB / event / wiki side effect to no-ops.
    for name in (
        "_set_instance_state",
        "_finalize_instance",
        "_emit_completion_event",
        "_emit_sse",
        "_emit_task_completed",
        "_file_session_to_wiki",
        "_record_trust_outcome",
    ):
        monkeypatch.setattr(ctrl, name, lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "_query_role_wiki", lambda *a, **k: "")

    # Drive the real dispatch path synchronously.
    ctrl._run(
        instance_id="test-parallel-001",
        problem_text="fake problem",
        trigger_source="test",
        trigger_ref="hcx-ace-06",
        user_id="tester",
        project_id="",
        role_ids=[TRUSTED, SUPERVISED],
    )

    trusted = sorted(
        [(s, e) for (r, s, e) in records if r == TRUSTED], key=lambda x: x[0]
    )
    supervised = sorted(
        [(s, e) for (r, s, e) in records if r == SUPERVISED], key=lambda x: x[0]
    )

    # Whole team completed — every coworker ran exactly once.
    assert len(trusted) == 3, "all trusted coworkers should complete"
    assert len(supervised) == 3, "all supervised coworkers should complete"

    # Trusted role (max_parallel=4 >= 3) — all three overlap: the latest start
    # happens before the earliest end, so there is a moment all three run.
    latest_start = max(s for s, _ in trusted)
    earliest_end = min(e for _, e in trusted)
    assert latest_start < earliest_end, (
        f"trusted coworkers should overlap in time "
        f"(latest_start={latest_start:.3f} !< earliest_end={earliest_end:.3f})"
    )

    # Supervised role (max_parallel=1) — coworkers are serialized: each starts
    # only after the previous one has finished (allow small scheduling jitter).
    for i in range(1, len(supervised)):
        prev_end = supervised[i - 1][1]
        this_start = supervised[i][0]
        assert this_start >= prev_end - 0.05, (
            f"supervised coworkers should be serialized "
            f"(coworker {i} start={this_start:.3f} < prev end={prev_end:.3f})"
        )
