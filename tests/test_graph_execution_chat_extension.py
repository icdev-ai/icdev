# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""Tests for the graph execution chat extension (hgx-doc-01).

Covers tools/extensions/builtins/031_graph_execution_chat.py:
  - load order: 031 sorts after 030 and does not shadow 040
  - a running graph produces an advisory, rate-limited by the cooldown
  - no active run injects nothing
  - node counts, barrier attribution, and the gate's release command
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import connect as _sqlite_connect  # noqa: E402

_BUILTINS = ROOT / "tools" / "extensions" / "builtins"
_HANDLER = _BUILTINS / "031_graph_execution_chat.py"
_spec = importlib.util.spec_from_file_location("graph_execution_chat", str(_HANDLER))
graph_chat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(graph_chat)

handle = graph_chat.handle
ADVISORY_COOLDOWN_TURNS = graph_chat.ADVISORY_COOLDOWN_TURNS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEMPLATE = """
name: Nightly Compliance
max_parallel: 4
steps:
  - id: fetch
    name: Fetch evidence
    tool: tools/testing/health_check.py
  - id: scan
    name: Security scan
    tool: tools/testing/health_check.py
  - id: merge
    name: Merge findings
    depends_on: [fetch, scan]
  - id: sign_off
    name: Approve release
    node_type: approval
    depends_on: [merge]
"""

_SCHEMA = """
CREATE TABLE studio_workflows (
    workflow_id TEXT PRIMARY KEY,
    name TEXT,
    template_yaml TEXT
);
CREATE TABLE studio_workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT,
    workflow_name TEXT,
    status TEXT,
    started_at TEXT,
    project_id TEXT
);
CREATE TABLE studio_workflow_run_steps (
    step_run_id TEXT PRIMARY KEY,
    run_id TEXT,
    step_id TEXT,
    step_name TEXT,
    status TEXT,
    started_at TEXT
);
"""


@pytest.fixture
def graph_db():
    """In-memory Studio run tables behind a %s-translating connection.

    The connection is handed to production code, so it must translate exactly
    as StorageConnection does — a bare sqlite3.connect would raise inside the
    handler's own `except` and make every assertion pass against a no-op.
    """
    conn = _sqlite_connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO studio_workflows (workflow_id, name, template_yaml) VALUES (?, ?, ?)",
        ("wf1", "Nightly Compliance", _TEMPLATE),
    )
    conn.commit()
    yield conn
    conn.close()


def _seed_run(conn, status="running", project_id="p1"):
    conn.execute(
        "INSERT INTO studio_workflow_runs "
        "(run_id, workflow_id, workflow_name, status, started_at, project_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("r1", "wf1", "Nightly Compliance", status, "2026-08-09T10:00:00", project_id),
    )
    conn.commit()


def _seed_step(conn, step_run_id, step_id, status, started_at):
    conn.execute(
        "INSERT INTO studio_workflow_run_steps "
        "(step_run_id, run_id, step_id, step_name, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (step_run_id, "r1", step_id, step_id, status, started_at),
    )
    conn.commit()


def _empty_db():
    """A translating connection with no Studio tables at all."""
    return _sqlite_connect(":memory:")


@pytest.fixture(autouse=True)
def reset_cooldown():
    graph_chat._last_advisory_turn.clear()
    yield
    graph_chat._last_advisory_turn.clear()


@pytest.fixture
def use_db(graph_db, monkeypatch):
    """Point the handler at the fixture DB. Close is a no-op — pytest owns it."""
    monkeypatch.setattr(graph_db, "close", lambda: None, raising=False)
    monkeypatch.setattr(graph_chat, "get_connection", lambda: graph_db)
    return graph_db


def _ctx(turn_number=10, project_id="p1", role="assistant"):
    return {
        "role": role,
        "content": "ok",
        "context_id": "c1",
        "turn_number": turn_number,
        "project_id": project_id,
    }


# ===========================================================================
# Load order
# ===========================================================================


class TestLoadOrder:
    def test_sorts_between_030_and_040(self):
        """_auto_load_builtins sorts filenames, so the number is the precedence."""
        names = sorted(p.name for p in _BUILTINS.glob("*.py") if not p.name.startswith("_"))
        assert names.index("030_workflow_loop_chat.py") < names.index(
            "031_graph_execution_chat.py"
        ) < names.index("040_bayesian_learning_chat.py")

    def test_does_not_shadow_040(self):
        assert (_BUILTINS / "040_bayesian_learning_chat.py").exists()
        assert (_BUILTINS / "031_graph_execution_chat.py").exists()

    def test_registers_chat_message_after_hook(self):
        hooks = graph_chat.EXTENSION_HOOKS
        assert "chat_message_after" in hooks
        assert hooks["chat_message_after"]["handler"] is handle
        assert hooks["chat_message_after"]["name"] == "graph_execution_chat"

    def test_advisory_key_is_registered_in_chat_manager(self):
        """An unregistered key is silently dropped by _inject_advisories."""
        from tools.dashboard.chat_manager import ChatManager

        assert "graph_advisory" in ChatManager._ADVISORY_TYPES
        label, content_type, _ = ChatManager._ADVISORY_TYPES["graph_advisory"]
        assert label == "[Graph Run]"
        # Reused on purpose: already in the live chat_messages CHECK constraint.
        assert content_type == "workflow_status"


# ===========================================================================
# Quiet paths
# ===========================================================================


class TestInjectsNothing:
    def test_no_active_run(self, use_db):
        result = handle(_ctx())
        assert "graph_advisory" not in result

    def test_completed_run_is_not_active(self, use_db):
        _seed_run(use_db, status="success")
        assert "graph_advisory" not in handle(_ctx())

    def test_failed_run_is_not_active(self, use_db):
        _seed_run(use_db, status="failed")
        assert "graph_advisory" not in handle(_ctx())

    def test_other_project(self, use_db):
        _seed_run(use_db, project_id="other")
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")
        assert "graph_advisory" not in handle(_ctx(project_id="p1"))

    def test_user_message(self, use_db):
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")
        assert "graph_advisory" not in handle(_ctx(role="user"))

    def test_pending_run_with_no_dispatched_step(self, use_db):
        """Created but not yet started — nothing has happened to report."""
        _seed_run(use_db, status="pending")
        assert "graph_advisory" not in handle(_ctx())

    def test_missing_tables_are_survivable(self, monkeypatch):
        monkeypatch.setattr(graph_chat, "get_connection", _empty_db)
        assert "graph_advisory" not in handle(_ctx())

    def test_no_connection_is_survivable(self, monkeypatch):
        def _boom():
            raise RuntimeError("no database")

        monkeypatch.setattr(graph_chat, "get_connection", _boom)
        assert "graph_advisory" not in handle(_ctx())


# ===========================================================================
# Running graph
# ===========================================================================


class TestRunningGraph:
    def test_reports_done_and_running_nodes(self, use_db):
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "running", "2026-08-09T10:00:02")

        advisory = handle(_ctx())["graph_advisory"]
        assert advisory["gap_id"] == "graph_run_in_progress"
        assert advisory["run_id"] == "r1"
        assert "1/4 nodes done" in advisory["message"]
        assert "1 running (Security scan)" in advisory["message"]

    def test_reports_what_the_barrier_waits_for(self, use_db):
        """'merge' declares two depends_on; only one is done."""
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "running", "2026-08-09T10:00:02")

        message = handle(_ctx())["graph_advisory"]["message"]
        assert "'Merge findings' is waiting on Security scan" in message
        assert "Fetch evidence" not in message.split("waiting on")[1]

    def test_barrier_clears_when_every_dependency_is_done(self, use_db):
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "skipped", "2026-08-09T10:00:02")
        _seed_step(use_db, "s3", "merge", "running", "2026-08-09T10:00:03")

        message = handle(_ctx())["graph_advisory"]["message"]
        assert "waiting on" not in message
        assert "2/4 nodes done" in message

    def test_single_dependency_chain_is_not_a_barrier(self, use_db):
        """'sign_off' depends on one node — that is progress, not a barrier."""
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "success", "2026-08-09T10:00:02")
        _seed_step(use_db, "s3", "merge", "running", "2026-08-09T10:00:03")

        assert "waiting on" not in handle(_ctx())["graph_advisory"]["message"]

    def test_replayed_step_uses_its_latest_status(self, use_db):
        """A resumed run re-records a step_id; the newest row wins."""
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "failed", "2026-08-09T10:00:01")
        _seed_step(use_db, "s1b", "fetch", "success", "2026-08-09T10:00:05")

        assert "1/4 nodes done" in handle(_ctx())["graph_advisory"]["message"]

    def test_survives_a_missing_template(self, use_db):
        """A run outlives an edit of its workflow; counts come from step rows."""
        use_db.execute("DELETE FROM studio_workflows")
        use_db.commit()
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "running", "2026-08-09T10:00:02")

        message = handle(_ctx())["graph_advisory"]["message"]
        assert "1/2 nodes done" in message
        assert "waiting on" not in message


# ===========================================================================
# Approval gate
# ===========================================================================


class TestApprovalGate:
    def test_gate_outranks_a_running_node(self, use_db):
        _seed_run(use_db, status="awaiting_approval")
        _seed_step(use_db, "s1", "fetch", "success", "2026-08-09T10:00:01")
        _seed_step(use_db, "s2", "scan", "success", "2026-08-09T10:00:02")
        _seed_step(use_db, "s3", "merge", "success", "2026-08-09T10:00:03")
        _seed_step(use_db, "s4", "sign_off", "awaiting_approval", "2026-08-09T10:00:04")

        advisory = handle(_ctx())["graph_advisory"]
        assert advisory["gap_id"] == "graph_gate_pending"
        assert advisory["severity"] == "high"
        assert "'Approve release'" in advisory["message"]
        assert "3/4 nodes done" in advisory["message"]

    def test_action_names_the_parked_step_run_id(self, use_db):
        _seed_run(use_db, status="awaiting_approval")
        _seed_step(use_db, "s4", "sign_off", "awaiting_approval", "2026-08-09T10:00:04")

        action = handle(_ctx())["graph_advisory"]["action"]
        assert "approve_step('s4')" in action
        assert "tools.studio.workflow_runner" in action

    def test_action_targets_a_real_callable(self):
        """workflow_runner is a library — the advisory documents the import form."""
        from tools.studio import workflow_runner

        assert callable(workflow_runner.approve_step)

    def test_parked_run_is_preferred_over_a_running_one(self, use_db):
        use_db.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, started_at, project_id) "
            "VALUES ('r0', 'wf1', 'Older run', 'running', '2026-08-09T09:00:00', 'p1')"
        )
        _seed_run(use_db, status="awaiting_approval")
        _seed_step(use_db, "s4", "sign_off", "awaiting_approval", "2026-08-09T10:00:04")

        assert handle(_ctx())["graph_advisory"]["run_id"] == "r1"


# ===========================================================================
# Cooldown
# ===========================================================================


class TestCooldown:
    def test_advisory_is_rate_limited(self, use_db):
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")

        assert "graph_advisory" in handle(_ctx(turn_number=10))
        for turn in range(11, 10 + ADVISORY_COOLDOWN_TURNS):
            assert "graph_advisory" not in handle(_ctx(turn_number=turn))
        assert "graph_advisory" in handle(_ctx(turn_number=10 + ADVISORY_COOLDOWN_TURNS))

    def test_cooldown_is_per_context(self, use_db):
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")

        handle(_ctx(turn_number=10))
        other = _ctx(turn_number=11)
        other["context_id"] = "c2"
        assert "graph_advisory" in handle(other)

    def test_quiet_turn_does_not_consume_the_cooldown(self, use_db):
        """No run -> no advisory -> the next eligible turn still fires."""
        assert "graph_advisory" not in handle(_ctx(turn_number=10))
        _seed_run(use_db)
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")
        assert "graph_advisory" in handle(_ctx(turn_number=11))


# ===========================================================================
# Project scoping
# ===========================================================================


class TestProjectScoping:
    def test_unscoped_chat_sees_the_default_project(self, use_db):
        """start_run's project_id default is 'default'."""
        _seed_run(use_db, project_id="default")
        _seed_step(use_db, "s1", "fetch", "running", "2026-08-09T10:00:01")

        ctx = _ctx()
        ctx["project_id"] = ""
        assert "graph_advisory" in handle(ctx)
