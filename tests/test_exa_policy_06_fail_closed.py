# CUI // SP-CTI
"""exa-policy-06 — two enforcement points that used to fail OPEN now fail CLOSED.

An enforcement point that grants permission when it malfunctions is not an
enforcement point. Two of them shipped that way:

1. ``tools/workflow_hitl/gate.py::HITLGate.get_pending`` wrapped its lookup in
   ``except Exception: return None``. A DB outage returned the same value as
   "no human approval is outstanding", so the Kanban ``in_progress -> done``
   transition proceeded and the HITL gate silently approved.

2. ``tools/llm/gateway.py::_check_cost_cap`` caught every exception and returned
   ``{"allowed": True}``. Any hiccup in the budget backend lifted the spend cap.

Both are now deny-by-default. Each test below *injects* the failure and asserts
the deny — a test that merely exercises the happy path would not have caught
either bug, because both bugs only appear when something else is already broken.

The final class pins the shipped ``token_budgets`` defaults, since
``check_budget`` allows unconditionally when the block is disabled or the cap is
zero. Those defaults are load-bearing, not cosmetic.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools.db.storage import get_connection as _real_get_connection


# ── 1. HITL gate ─────────────────────────────────────────────────────────────

class _ExplodingConn:
    """A connection whose query raises — i.e. the DB is reachable but broken."""

    def execute(self, *_a, **_kw):
        raise sqlite3.OperationalError("no such table: wf_approvals")

    def close(self):
        pass


class TestHITLGateFailsClosed:
    def _gate_mod(self):
        return importlib.import_module("tools.workflow_hitl.gate")

    def test_query_error_raises_instead_of_returning_none(self, monkeypatch):
        """The bug: a broken query returned None == 'no approval pending'."""
        mod = self._gate_mod()
        monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _ExplodingConn())

        with pytest.raises(mod.HITLGateUnavailable):
            mod.HITLGate().get_pending("task-001")

    def test_connect_error_raises_instead_of_returning_none(self, monkeypatch):
        mod = self._gate_mod()

        def _no_db(*_a, **_kw):
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(mod, "get_connection", _no_db)

        with pytest.raises(mod.HITLGateUnavailable):
            mod.HITLGate().get_pending("task-001")

    def test_should_gate_is_true_when_state_is_undeterminable(self, monkeypatch):
        """should_gate() feeds the transition decision — undeterminable must gate."""
        mod = self._gate_mod()
        monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _ExplodingConn())

        assert mod.HITLGate().should_gate("task-001") is True

    def test_get_instance_for_task_also_fails_closed(self, monkeypatch):
        mod = self._gate_mod()
        monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _ExplodingConn())

        with pytest.raises(mod.HITLGateUnavailable):
            mod.HITLGate().get_instance_for_task("task-001")

    def test_explicit_env_override_restores_fail_open(self, monkeypatch):
        """The staged-rollout escape hatch — opt-in only, never the default."""
        mod = self._gate_mod()
        monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _ExplodingConn())
        monkeypatch.setenv("ICDEV_HITL_GATE_FAIL_OPEN", "1")

        assert mod.HITLGate().get_pending("task-001") is None
        assert mod.HITLGate().should_gate("task-001") is False

    def test_fail_open_is_not_the_default(self, monkeypatch):
        mod = self._gate_mod()
        monkeypatch.delenv("ICDEV_HITL_GATE_FAIL_OPEN", raising=False)
        assert mod._fail_open_enabled() is False


# ── 2. The Kanban transition the gate actually guards ────────────────────────

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'medium',
    status                TEXT DEFAULT 'backlog',
    scheduled_at          TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    completed_at          TEXT,
    executor_type         TEXT,
    execution_id          TEXT,
    executor_url          TEXT,
    source_prediction_id  TEXT,
    depends_on_task_id    TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    dispatch_source       TEXT,
    dispatch_attempt_id   TEXT
);
CREATE TABLE kanban_status_transitions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    from_status        TEXT,
    to_status          TEXT NOT NULL,
    actor              TEXT,
    reason             TEXT,
    recorded_at        TEXT
);
CREATE TABLE audit_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT,
    actor       TEXT,
    action      TEXT,
    project_id  TEXT,
    details     TEXT,
    created_at  TEXT
);
"""


@pytest.fixture
def kanban_db(tmp_path, monkeypatch):
    """Isolated kanban DB plus a capture list for recorded transitions."""
    db_path = tmp_path / "k.db"

    def _fake_conn(*_a, **_kw):
        # Go through tools.db.storage so %s placeholders are translated. A raw
        # sqlite3 connection would make every runtime %s a syntax error, which
        # _move_task swallows — the test would then pass for the wrong reason.
        return _real_get_connection(db_path=str(db_path))

    setup = _fake_conn()
    for stmt in _SCHEMA.strip().split(";\n"):
        if stmt.strip():
            setup.execute(stmt)
    setup.execute(
        "INSERT INTO kanban_tasks (id, title, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("task-001", "gated task", "in_progress",
         "2026-08-12T00:00:00+00:00", "2026-08-12T00:00:00+00:00"),
    )
    setup.commit()
    setup.close()

    _storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(_storage, "get_connection", _fake_conn)
    kanban_mod = importlib.import_module("tools.genesis.reflexes.kanban")
    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    recorded: list[tuple] = []
    monkeypatch.setattr(
        kanban_mod, "_record_status_transition",
        lambda tid, frm, to, **k: recorded.append((tid, frm, to, k.get("reason"))),
    )
    # Isolate from the merge-verify gate — that guard has its own tests.
    monkeypatch.setenv("KANBAN_REQUIRE_MERGE_FOR_DONE", "0")

    return db_path, kanban_mod, recorded


def _status(db_path, tid):
    c = _real_get_connection(db_path=str(db_path))
    try:
        row = c.execute("SELECT status FROM kanban_tasks WHERE id = %s", (tid,)).fetchone()
        return dict(row)["status"] if row else None
    finally:
        c.close()


class TestMoveTaskRefusesDoneWhenGateUnavailable:
    def test_done_is_refused_when_hitl_state_cannot_be_read(self, kanban_db, monkeypatch):
        """The whole point: a DB error must not buy the task a free approval."""
        db, km, recorded = kanban_db
        monkeypatch.setenv("ICDEV_HITL_KANBAN_GATE", "true")
        gate_mod = importlib.import_module("tools.workflow_hitl.gate")
        monkeypatch.setattr(gate_mod, "get_connection", lambda *a, **k: _ExplodingConn())

        km._move_task("task-001", "done")

        assert _status(db, "task-001") == "in_progress", (
            "task advanced to done while the HITL gate was unreadable — fail-open"
        )
        assert any(t[2] == "REFUSED_done_hitl_unavailable" for t in recorded), (
            f"refusal was not recorded for audit; got {recorded}"
        )

    def test_done_proceeds_when_gate_reports_no_pending_approval(self, kanban_db, monkeypatch):
        """Fail-closed must not become always-closed."""
        db, km, _recorded = kanban_db
        monkeypatch.setenv("ICDEV_HITL_KANBAN_GATE", "true")
        gate_mod = importlib.import_module("tools.workflow_hitl.gate")
        monkeypatch.setattr(gate_mod.HITLGate, "get_pending", lambda self, tid: None)

        km._move_task("task-001", "done")

        assert _status(db, "task-001") == "done"


# ── 3. LLM gateway cost cap ──────────────────────────────────────────────────

_CFG = {"pre_invoke": {"cost_cap": {"enabled": True}}}


class TestCostCapFailsClosed:
    def _tracker(self):
        return importlib.import_module("tools.agent.token_tracker")

    def _gateway(self):
        return importlib.import_module("tools.llm.gateway")

    def test_backend_error_denies(self, monkeypatch):
        """The bug: any exception returned allowed=True, lifting the cap."""
        tracker = self._tracker()

        def _boom(_agent_id, db_path=None):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(tracker, "check_budget", _boom)

        result = self._gateway()._check_cost_cap("builder-agent", _CFG)

        assert result["allowed"] is False, "budget backend error lifted the cost cap"
        assert "fail-closed" in (result["reason"] or "")

    def test_corrupt_budget_config_denies_end_to_end(self, monkeypatch, tmp_path):
        """A corrupt token_budgets block used to read as 'no cap configured'."""
        tracker = self._tracker()
        bad = tmp_path / "args"
        bad.mkdir()
        (bad / "llm_config.yaml").write_text(
            "token_budgets:\n  enabled: true\n   bad-indent: [\n", encoding="utf-8",
        )
        monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
        monkeypatch.setattr(tracker, "_budget_config_cache", None)

        with pytest.raises(tracker.BudgetConfigError):
            tracker.check_budget("builder-agent")

        assert self._gateway()._check_cost_cap("builder-agent", _CFG)["allowed"] is False

    def test_config_read_failure_is_not_cached(self, monkeypatch, tmp_path):
        """Fixing the file must recover without a process restart."""
        tracker = self._tracker()
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        cfg = args_dir / "llm_config.yaml"
        cfg.write_text("token_budgets:\n  enabled: true\n   bad: [\n", encoding="utf-8")
        monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)
        monkeypatch.setattr(tracker, "_budget_config_cache", None)

        with pytest.raises(tracker.BudgetConfigError):
            tracker._load_budget_config()

        cfg.write_text("token_budgets:\n  enabled: true\n  default_monthly_usd: 5.0\n",
                       encoding="utf-8")
        assert tracker._load_budget_config()["default_monthly_usd"] == 5.0

    def test_missing_config_file_is_still_unconfigured_not_an_error(self, monkeypatch, tmp_path):
        """No config at all is a legitimate state, distinct from a broken one."""
        tracker = self._tracker()
        monkeypatch.setattr(tracker, "BASE_DIR", tmp_path)  # no args/ inside
        monkeypatch.setattr(tracker, "_budget_config_cache", None)

        assert tracker._load_budget_config() == {}

    def test_explicit_env_override_restores_fail_open(self, monkeypatch):
        tracker = self._tracker()

        def _boom(_agent_id, db_path=None):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(tracker, "check_budget", _boom)
        monkeypatch.setenv("ICDEV_COST_CAP_FAIL_OPEN", "1")

        assert self._gateway()._check_cost_cap("builder-agent", _CFG)["allowed"] is True

    def test_fail_open_is_not_the_default(self, monkeypatch):
        monkeypatch.delenv("ICDEV_COST_CAP_FAIL_OPEN", raising=False)
        assert self._gateway()._cost_cap_fail_open() is False

    def test_block_decision_still_denies(self, monkeypatch):
        tracker = self._tracker()
        monkeypatch.setattr(
            tracker, "check_budget",
            lambda _a, db_path=None: {"action": "block", "message": "exhausted"},
        )
        assert self._gateway()._check_cost_cap("builder-agent", _CFG)["allowed"] is False

    def test_allow_decision_still_allows(self, monkeypatch):
        """Fail-closed must not become always-closed."""
        tracker = self._tracker()
        monkeypatch.setattr(
            tracker, "check_budget",
            lambda _a, db_path=None: {"action": "allow", "message": ""},
        )
        assert self._gateway()._check_cost_cap("builder-agent", _CFG)["allowed"] is True

    def test_disabled_cap_allows(self):
        cfg = {"pre_invoke": {"cost_cap": {"enabled": False}}}
        assert self._gateway()._check_cost_cap("builder-agent", cfg)["allowed"] is True


# ── 4. The shipped defaults the cap actually depends on ──────────────────────

class TestShippedTokenBudgetDefaults:
    """``check_budget`` returns ``allow`` unconditionally when ``enabled`` is
    falsy or the resolved cap is <= 0. So these YAML values ARE the cost cap;
    a well-meaning edit to ``0`` would disable enforcement with no other signal.
    Pinned here so such an edit fails a test instead of shipping.
    """

    def _budgets(self):
        import yaml
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        with open(root / "args" / "llm_config.yaml", encoding="utf-8") as fh:
            return (yaml.safe_load(fh) or {}).get("token_budgets", {})

    def test_enforcement_is_enabled(self):
        assert self._budgets().get("enabled") is True

    def test_default_cap_is_positive(self):
        # <= 0 is read by check_budget as "unlimited", not as "deny everything".
        assert self._budgets().get("default_monthly_usd", 0) > 0

    def test_hard_stop_is_on(self):
        # hard_stop False downgrades an exhausted budget from block to warn.
        assert self._budgets().get("hard_stop") is True

    def test_no_per_agent_override_silently_disables_its_cap(self):
        zeroed = [
            name for name, over in (self._budgets().get("per_agent") or {}).items()
            if float(over.get("monthly_usd", 1)) <= 0
        ]
        assert not zeroed, f"per-agent caps of 0 mean unlimited, not blocked: {zeroed}"
