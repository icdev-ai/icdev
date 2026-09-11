# CUI // SP-CTI
"""ONE pre-dispatch verdict, with the budget rung nothing consulted (xrv-run-01).

Four USD budget guards exist and ALL sit inside router.invoke; a claude_cli
dispatch never enters the router, so every cap was bypassed and the budget
refused the task's calls MID-RUN (kpr-dup-09: 95 done<->backlog flips, every
transition legitimate). `should_run.assess` asks every predicate the dispatcher
already consults PLUS the budget rung, folds them through ONE pure `classify`,
and re-implements none of them.

What is pinned here:
  1. the fold -- every verdict reachable; unmeasurable NEVER blocks; all
     unmeasurable is unmeasurable, never proceed;
  2. the mode -- report is the default and changes no outcome; enforce blocks
     wait/refuse only;
  3. the budget rung -- each seam's OWN answer mapped, exhausted -> wait with
     `resets_at`, a downgrade tier -> proceed with the reason recorded, an
     unreadable ledger -> unmeasurable;
  4. assess CALLS the module objects (patched at the module the code imports);
  5. the AST -- no SQL naming kanban_tasks, the admission rule imported and
     never respelled, the three budget seams actually called;
  6. the wiring -- in report mode `_dispatch_to_claude`'s outcome is unchanged
     for a fixture task, one should_run log line per dispatch, enforce parks
     through `_move_task` under actor `should-run`, and a verdict that raises
     never wedges dispatch.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import should_run as sr  # noqa: E402
from tools.kanban import dispatch_admission as da  # noqa: E402

SRC = Path(sr.__file__)


def _c(verdict, reason="r", **extra):
    return {"verdict": verdict, "reason": reason, **extra}


# --------------------------------------------------------------------------- #
# 1. The fold
# --------------------------------------------------------------------------- #
def test_every_verdict_is_reachable_from_classify():
    assert sr.classify("t", {"a": _c(sr.PROCEED)}).verdict == sr.PROCEED
    assert sr.classify("t", {"a": _c(sr.WAIT)}).verdict == sr.WAIT
    assert sr.classify("t", {"a": _c(sr.ASK)}).verdict == sr.ASK
    assert sr.classify("t", {"a": _c(sr.REFUSE)}).verdict == sr.REFUSE
    assert sr.classify("t", {"a": _c(sr.UNMEASURABLE)}).verdict == sr.UNMEASURABLE


def test_precedence_refuse_over_wait_over_ask():
    got = sr.classify("t", {"a": _c(sr.ASK), "b": _c(sr.WAIT), "c": _c(sr.REFUSE)})
    assert got.verdict == sr.REFUSE
    assert got.reasons == ["c: r"], "only the deciding checks are the reasons"
    got = sr.classify("t", {"a": _c(sr.ASK), "b": _c(sr.WAIT)})
    assert got.verdict == sr.WAIT


def test_unmeasurable_never_blocks_and_is_named_beside_a_proceed(monkeypatch):
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    got = sr.classify("t", {"a": _c(sr.PROCEED), "b": _c(sr.UNMEASURABLE, "ledger down")})
    assert got.verdict == sr.PROCEED
    assert got.blocks is False
    assert any("unmeasurable: b" in r for r in got.reasons), \
        "a proceed over a rung nobody could see must say so"


def test_all_checks_unmeasurable_is_unmeasurable_never_proceed():
    got = sr.classify("t", {"a": _c(sr.UNMEASURABLE), "b": _c(sr.UNMEASURABLE)})
    assert got.verdict == sr.UNMEASURABLE
    assert got.blocks is False


def test_no_checks_at_all_is_proceed_and_no_task_id_is_unmeasurable():
    assert sr.classify("t", {}).verdict == sr.PROCEED
    assert sr.classify("", {"a": _c(sr.REFUSE)}).verdict == sr.UNMEASURABLE


def test_a_check_with_no_verdict_is_recorded_unmeasurable():
    got = sr.classify("t", {"a": {"reason": "half a dict"}, "b": {"verdict": "bogus"}})
    assert got.checks["a"]["verdict"] == sr.UNMEASURABLE
    assert got.checks["b"]["verdict"] == sr.UNMEASURABLE
    assert got.verdict == sr.UNMEASURABLE


def test_wait_carries_the_latest_resets_at():
    got = sr.classify("t", {
        "a": _c(sr.WAIT, resets_at="2026-10-01T00:00:00+00:00"),
        "b": _c(sr.WAIT, resets_at="2026-09-12T00:00:00+00:00"),
        "c": _c(sr.WAIT),
    })
    assert got.resets_at == "2026-10-01T00:00:00+00:00"
    assert sr.classify("t", {"c": _c(sr.WAIT)}).resets_at is None


def test_a_noted_proceed_keeps_its_reason():
    got = sr.classify("t", {"budget": _c(sr.PROCEED, "downgrade tier", noted=True),
                            "x": _c(sr.PROCEED, "fine")})
    assert got.verdict == sr.PROCEED
    assert got.reasons == ["budget: downgrade tier"]


# --------------------------------------------------------------------------- #
# 2. The mode
# --------------------------------------------------------------------------- #
def test_the_default_mode_is_report(monkeypatch):
    monkeypatch.delenv(sr.MODE_ENV, raising=False)
    assert sr.mode() == "report"
    assert sr.classify("t", {"a": _c(sr.REFUSE)}).blocks is False


@pytest.mark.parametrize("verdict", [sr.WAIT, sr.REFUSE])
def test_enforce_blocks_wait_and_refuse_only(monkeypatch, verdict):
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    assert sr.classify("t", {"a": _c(verdict)}).blocks is True
    assert sr.classify("t", {"a": _c(sr.ASK)}).blocks is False
    assert sr.classify("t", {"a": _c(sr.PROCEED)}).blocks is False


def test_off_and_unknown_modes(monkeypatch):
    monkeypatch.setenv(sr.MODE_ENV, "off")
    assert sr.classify("t", {"a": _c(sr.REFUSE)}).blocks is False
    monkeypatch.setenv(sr.MODE_ENV, "ENFORCED")
    assert sr.mode() == "report"


def test_to_dict_carries_mode_and_blocks(monkeypatch):
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    d = sr.classify("t", {"a": _c(sr.WAIT, resets_at="2026-10-01")}).to_dict()
    assert d["mode"] == "enforce" and d["blocks"] is True
    assert d["resets_at"] == "2026-10-01" and d["checks"]["a"]["verdict"] == sr.WAIT


# --------------------------------------------------------------------------- #
# 3. The budget rung
# --------------------------------------------------------------------------- #
def test_module_block_is_wait_until_the_month_rolls():
    got = sr.budget_check({"action": "block", "message": "Token cap: 400013 would exceed 400000"},
                          {"status": "ok", "action": "allow", "reason": "$0 of $200",
                           "telemetry_available": True},
                          {"action": "allow", "message": "within"}, month="2026-09")
    assert got["verdict"] == sr.WAIT
    assert got["resets_at"] == "2026-10-01T00:00:00+00:00"
    assert "400000" in got["reason"]
    assert got["arms"]["module"]["verdict"] == sr.WAIT
    assert got["arms"]["cost"]["verdict"] == sr.PROCEED


def test_december_rolls_into_january():
    got = sr.budget_check({"action": "block", "message": "m"}, None, None, month="2026-12")
    assert got["resets_at"] == "2027-01-01T00:00:00+00:00"


def test_warn_is_proceed_with_the_reason_recorded():
    got = sr.budget_check({"action": "warn", "message": "approaching"},
                          {"status": "ok", "action": "allow", "reason": "", "telemetry_available": True},
                          {"action": "warn", "message": "82%"}, month="2026-09")
    assert got["verdict"] == sr.PROCEED
    assert got["noted"] is True
    assert "approaching" in got["reason"] and "82%" in got["reason"]


def test_cost_downgrade_tier_is_proceed_with_reason():
    got = sr.budget_check({"action": "allow", "message": "ok"},
                          {"status": "hard", "action": "downgrade", "downgraded": True,
                           "reason": "hard limit reached", "telemetry_available": True},
                          {"action": "allow", "message": "ok"}, month="2026-09")
    assert got["verdict"] == sr.PROCEED
    assert "downgrade tier" in got["reason"]


def test_cost_block_is_wait_until_the_period_end():
    got = sr.budget_check({"action": "allow", "message": "ok"},
                          {"status": "hard", "action": "block", "reason": "hard_action block",
                           "period": "daily", "period_start": "2026-09-11",
                           "telemetry_available": True},
                          {"action": "allow", "message": "ok"}, month="2026-09")
    assert got["verdict"] == sr.WAIT
    assert got["resets_at"] == "2026-09-12T00:00:00+00:00"
    monthly = sr._fold_cost({"status": "hard", "action": "block", "reason": "r",
                             "period": "monthly", "period_start": "2026-09-01",
                             "telemetry_available": True})
    assert monthly["resets_at"] == "2026-10-01T00:00:00+00:00"


def test_cost_soft_ask_is_ask():
    got = sr.budget_check({"action": "allow", "message": "ok"},
                          {"status": "soft", "action": "ask", "reason": "0.5 crossed",
                           "telemetry_available": True},
                          {"action": "allow", "message": "ok"}, month="2026-09")
    assert got["verdict"] == sr.ASK


def test_unreadable_ledgers_are_unmeasurable_never_proceed(monkeypatch):
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    got = sr.budget_check(None, None, None, month="2026-09")
    assert got["verdict"] == sr.UNMEASURABLE
    assert all(a["verdict"] == sr.UNMEASURABLE for a in got["arms"].values())
    # telemetry unavailable on the cost arm alone: that arm is unmeasurable,
    # the rung proceeds and names it.
    got = sr.budget_check({"action": "allow", "message": "ok"},
                          {"status": "unmeasurable", "action": "allow",
                           "telemetry_available": False, "reason": "ai_telemetry unreadable"},
                          {"action": "allow", "message": "ok"}, month="2026-09")
    assert got["verdict"] == sr.PROCEED
    assert got["arms"]["cost"]["verdict"] == sr.UNMEASURABLE
    assert sr.classify("t", {"budget": got}).blocks is False


def test_live_budget_calls_the_three_seams(monkeypatch):
    """The live arm calls check_module_budget, cost_budget.evaluate and
    token_tracker.check_budget -- patched at the modules the code imports."""
    from tools.budget import module_budget_tracker as mbt
    from tools.llm import cost_budget
    from tools.agent import token_tracker

    called = {}
    monkeypatch.setattr(mbt, "module_for_function", lambda fn: called.setdefault("fn", fn) and "generative_intelligence")
    monkeypatch.setattr(mbt, "check_module_budget",
                        lambda module, **kw: called.setdefault("module", module) and
                        {"action": "block", "message": "exhausted"})

    class _V:
        def to_dict(self):
            return {"status": "ok", "action": "allow", "reason": "", "telemetry_available": True}
    monkeypatch.setattr(cost_budget, "evaluate", lambda fn, *a, **kw: called.setdefault("cost", fn) and _V())
    monkeypatch.setattr(token_tracker, "check_budget",
                        lambda agent_id, *a, **kw: called.setdefault("agent", agent_id) and
                        (_ for _ in ()).throw(token_tracker.BudgetConfigError("corrupt")))

    got = sr._live_budget_check()
    assert called == {"fn": "code_generation", "module": "generative_intelligence",
                      "cost": "code_generation", "agent": "kanban-scheduler"}
    assert got["verdict"] == sr.WAIT
    assert got["arms"]["token"]["verdict"] == sr.UNMEASURABLE, \
        "a corrupt budget config is unmeasurable, not allow"
    assert "BudgetConfigError" in got["errors"]["token"]


# --------------------------------------------------------------------------- #
# 4. assess calls the module objects
# --------------------------------------------------------------------------- #
@pytest.fixture
def quiet_board(monkeypatch):
    """Every predicate answers 'no objection', patched where the code imports it."""
    kanban = importlib.import_module("tools.genesis.reflexes.kanban")
    from tools.kanban import scheduler_control, gates, lease_liveness, backpressure

    monkeypatch.setattr(scheduler_control, "should_pause", lambda: {"paused": False})
    monkeypatch.setattr(kanban, "_manual_build", lambda: False)
    monkeypatch.setattr(gates, "is_manual_gate", lambda tid, title: False)
    monkeypatch.setattr(lease_liveness, "task_lease_verdict",
                        lambda tid: lease_liveness.LeaseVerdict(tid, "kanban:task:" + tid,
                                                                lease_liveness.STATE_FREE,
                                                                None, None, None))
    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.ALLOW, "nothing merged"))
    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(kanban, "_circuit_breaker_tripped", lambda task: (False, 0, 5))
    monkeypatch.setattr(backpressure, "status", lambda **kw: {"enabled": False})
    monkeypatch.setattr(sr, "_live_budget_check",
                        lambda: sr.budget_check({"action": "allow", "message": "ok"},
                                                {"status": "ok", "action": "allow", "reason": "",
                                                 "telemetry_available": True},
                                                {"action": "allow", "message": "ok"}))
    return kanban


def test_assess_proceeds_on_a_quiet_board(quiet_board):
    got = sr.assess({"id": "t-1", "title": "x"})
    assert got.verdict == sr.PROCEED
    assert set(got.checks) == set(sr.CHECKS)
    assert all(c["verdict"] == sr.PROCEED for c in got.checks.values())


def test_assess_maps_each_predicate(quiet_board, monkeypatch):
    kanban = quiet_board
    from tools.kanban import scheduler_control, gates, lease_liveness, backpressure

    monkeypatch.setattr(scheduler_control, "should_pause", lambda: {"paused": True, "mode": "manual"})
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.WAIT and got.checks["pause"]["reason"].startswith("paused")

    monkeypatch.setattr(scheduler_control, "should_pause", lambda: {"paused": False})
    monkeypatch.setattr(gates, "is_manual_gate", lambda tid, title: True)
    assert sr.assess({"id": "t", "title": "x"}).verdict == sr.REFUSE
    monkeypatch.setattr(gates, "is_manual_gate", lambda tid, title: False)

    monkeypatch.setattr(lease_liveness, "task_lease_verdict",
                        lambda tid: lease_liveness.LeaseVerdict(tid, "r", lease_liveness.STATE_LIVE,
                                                                {"pid": 1}, True, None))
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.WAIT and got.checks["lease"]["state"] == "live"
    monkeypatch.setattr(lease_liveness, "task_lease_verdict",
                        lambda tid: lease_liveness.LeaseVerdict(tid, "r", lease_liveness.STATE_FREE,
                                                                None, None, None))

    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.REFUSE, "PR #1 merged",
                                                                    prior_merged=[1]))
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.REFUSE and got.checks["admission"]["prior_merged"] == [1]
    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.UNMEASURABLE, "no forge"))
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.PROCEED and got.checks["admission"]["verdict"] == sr.UNMEASURABLE
    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.ALLOW, "ok"))

    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: True)
    assert sr.assess({"id": "t", "title": "x"}).verdict == sr.REFUSE
    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: False)

    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: True)
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.WAIT and "review_bound" in got.checks["open_pr"]["reason"]
    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: False)

    monkeypatch.setattr(kanban, "_circuit_breaker_tripped", lambda task: (True, 5, 5))
    got = sr.assess({"id": "t", "title": "x"})
    assert got.verdict == sr.REFUSE and got.checks["circuit_breaker"]["failure_count"] == 5
    monkeypatch.setattr(kanban, "_circuit_breaker_tripped", lambda task: (False, 0, 5))

    monkeypatch.setattr(backpressure, "status",
                        lambda **kw: {"enabled": True, "holding": True, "unreviewed": 3, "ceiling": 3})
    assert sr.assess({"id": "t", "title": "x"}).verdict == sr.WAIT

    monkeypatch.setattr(backpressure, "status", lambda **kw: {"enabled": False})
    monkeypatch.setattr(kanban, "_manual_build", lambda: True)
    assert sr.assess({"id": "t", "title": "x"}).verdict == sr.WAIT


def test_a_raising_predicate_is_unmeasurable_and_never_blocks(quiet_board, monkeypatch):
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    from tools.kanban import lease_liveness
    monkeypatch.setattr(lease_liveness, "task_lease_verdict",
                        lambda tid: (_ for _ in ()).throw(OSError("lease store gone")))
    got = sr.assess({"id": "t", "title": "x"})
    assert got.checks["lease"]["verdict"] == sr.UNMEASURABLE
    assert "OSError" in got.checks["lease"]["reason"]
    assert got.verdict == sr.PROCEED and got.blocks is False


def test_assess_with_no_id_is_unmeasurable():
    assert sr.assess({}).verdict == sr.UNMEASURABLE


# --------------------------------------------------------------------------- #
# 5. The AST: no rule re-implemented
# --------------------------------------------------------------------------- #
def _tree():
    return ast.parse(SRC.read_text(encoding="utf-8"))


_SQL_VERB = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|JOIN)\b")


def _is_board_sql(text: str) -> bool:
    """A string literal that is SQL naming the board. PROSE IS NOT A LITERAL: the
    module docstring names the table to say it does not read it, so the
    predicate is a conjunction -- the table name AND a SQL verb."""
    return "kanban_tasks" in text and bool(_SQL_VERB.search(text))


def test_no_sql_names_kanban_tasks():
    assert _is_board_sql("SELECT id FROM kanban_tasks WHERE id = %s"), "positive control"
    assert not _is_board_sql("the table kanban_tasks is never read here"), "prose control"
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not _is_board_sql(node.value), \
                f"should_run must read the board through an existing seam, not {node.value!r}"


def test_the_admission_rule_is_imported_never_respelled():
    tree = _tree()
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and node.module == "tools.kanban" for alias in node.names
    }
    assert "dispatch_admission" in imported
    # The admission rule decides on prior_merged / open_prs. Those names may be
    # READ off the verdict (attribute access) but never TESTED here.
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.Compare, ast.BoolOp)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    assert sub.id not in ("prior_merged", "open_prs", "prior", "opened"), \
                        f"admission rule respelled at line {node.lineno}"
    assert "already merged" not in SRC.read_text(encoding="utf-8").replace(
        "PR already carries", "")


def test_the_budget_seams_are_called_not_recomputed_on_the_live_path():
    tree = _tree()
    live = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_live_budget_check")
    called = {n.func.attr for n in ast.walk(live)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert {"check_module_budget", "module_for_function", "evaluate", "check_budget"} <= called
    # And the live path compares no spend against a cap itself.
    for node in ast.walk(live):
        if isinstance(node, ast.Compare):
            names = {s.id for s in ast.walk(node) if isinstance(s, ast.Name)}
            assert not names & {"spent", "cap", "budget_usd", "spent_usd", "limit"}


def test_the_circuit_breaker_is_one_helper_in_the_reflex():
    """Both dispatch sites and should_run read `_circuit_breaker_tripped`; the
    inline `failure_count >= max_retries` comparison must not survive."""
    kanban_src = (ROOT / "tools" / "genesis" / "reflexes" / "kanban.py").read_text(encoding="utf-8")
    assert kanban_src.count("_circuit_breaker_tripped(task)") >= 2
    assert "_task_failures >= _task_max_retries" not in kanban_src
    assert "_circuit_breaker_tripped" in SRC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 6. The wiring: report mode changes no outcome
# --------------------------------------------------------------------------- #
class _Log:
    def __init__(self):
        self.lines = []

    def _rec(self, level):
        def _w(msg, *args, **kw):
            try:
                self.lines.append((level, msg % args if args else msg))
            except Exception:  # noqa: BLE001
                self.lines.append((level, msg))
        return _w

    def __getattr__(self, name):
        return self._rec(name)


@pytest.fixture
def dispatchable(monkeypatch, tmp_path):
    kanban = importlib.import_module("tools.genesis.reflexes.kanban")
    from tools.kanban import build_mode, model_override

    monkeypatch.setattr(build_mode, "_FLAG", tmp_path / "manual_build.json")
    monkeypatch.setattr(model_override, "_FLAG", tmp_path / "model.json")
    prompt = tmp_path / "p.md"
    prompt.write_text("do it", encoding="utf-8")

    spawned, moved = [], []
    monkeypatch.setattr(kanban, "_dispatch_via_claude_cli", lambda *a, **kw: spawned.append(a))
    monkeypatch.setattr(kanban, "_claude_code_available", lambda: True)
    monkeypatch.setattr(kanban, "_pick_chain_adapter",
                        lambda *a, **kw: type("_A", (), {"name": "claude_cli"})())
    monkeypatch.setattr(kanban, "_create_worktree", lambda tid: str(tmp_path))
    monkeypatch.setattr(kanban, "_pre_dispatch_check", lambda t: (False, ""))
    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(kanban, "_set_executor_type", lambda *a, **kw: None)
    monkeypatch.setattr(kanban, "_move_task",
                        lambda tid, st, **kw: moved.append((tid, st, kw.get("actor"), kw.get("reason"))))
    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.ALLOW, "ok"))
    log = _Log()
    monkeypatch.setattr(kanban, "logger", log)
    return kanban, str(prompt), spawned, moved, log


def _wait_verdict(task):
    return sr.classify(task["id"], {"budget": _c(sr.WAIT, "module budget exhausted",
                                                 resets_at="2026-10-01T00:00:00+00:00")})


def test_report_mode_changes_no_outcome_and_logs_one_line(dispatchable, monkeypatch):
    kanban, prompt, spawned, moved, log = dispatchable
    monkeypatch.delenv(sr.MODE_ENV, raising=False)
    monkeypatch.setattr(sr, "assess", _wait_verdict)

    kanban._dispatch_to_claude({"id": "xrv-run-01", "title": "x"}, prompt)

    assert len(spawned) == 1, "report mode must dispatch exactly as before"
    assert not [m for m in moved if m[2] == "should-run"]
    lines = [m for _, m in log.lines if m.startswith("kanban: should_run ")]
    assert len(lines) == 1, lines
    assert lines[0].startswith("kanban: should_run wait for xrv-run-01: budget: ")


def test_enforce_parks_a_wait_through_move_task(dispatchable, monkeypatch):
    kanban, prompt, spawned, moved, log = dispatchable
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    monkeypatch.setattr(sr, "assess", _wait_verdict)

    kanban._dispatch_to_claude({"id": "xrv-run-01", "title": "x"}, prompt)

    assert spawned == []
    assert moved == [("xrv-run-01", "validating", "should-run",
                      "should_run wait: budget: module budget exhausted")]


def test_enforce_still_dispatches_a_proceed(dispatchable, monkeypatch):
    kanban, prompt, spawned, moved, log = dispatchable
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    monkeypatch.setattr(sr, "assess", lambda task: sr.classify(task["id"], {"a": _c(sr.PROCEED)}))
    kanban._dispatch_to_claude({"id": "xrv-run-01", "title": "x"}, prompt)
    assert len(spawned) == 1


def test_a_verdict_that_raises_never_wedges_dispatch(dispatchable, monkeypatch):
    kanban, prompt, spawned, moved, log = dispatchable
    monkeypatch.setenv(sr.MODE_ENV, "enforce")
    monkeypatch.setattr(sr, "assess", lambda task: (_ for _ in ()).throw(RuntimeError("boom")))
    kanban._dispatch_to_claude({"id": "xrv-run-01", "title": "x"}, prompt)
    assert len(spawned) == 1


# --------------------------------------------------------------------------- #
# 7. The survey replays the SAME fold
# --------------------------------------------------------------------------- #
class _Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class _Conn:
    def __init__(self, transitions, period=None, usage=None, agent_usd=0.0, telemetry_usd=0.0,
                 broken=False):
        self.transitions, self.period, self.usage = transitions, period, usage
        self.agent_usd, self.telemetry_usd, self.broken = agent_usd, telemetry_usd, broken
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.broken:
            raise RuntimeError("no such table")
        conn = self

        class _Cur:
            def fetchall(self_inner):
                return [_Row(r) for r in conn.transitions]

            def fetchone(self_inner):
                if "module_budget_periods" in sql:
                    return _Row(conn.period) if conn.period else None
                if "module_budget_usage" in sql:
                    return _Row(conn.usage or {"usd": 0.0, "tokens": 0})
                if "agent_token_usage" in sql:
                    return _Row({"usd": conn.agent_usd})
                if "ai_telemetry" in sql:
                    return _Row({"s": conn.telemetry_usd})
                return None
        return _Cur()

    def close(self):
        pass


@pytest.fixture
def survey_env(monkeypatch):
    from tools.budget import module_budget_tracker as mbt
    from tools.agent import token_tracker
    from tools.llm import cost_budget
    monkeypatch.setattr(mbt, "module_for_function", lambda fn: "generative_intelligence")
    monkeypatch.setattr(token_tracker, "_get_agent_budget",
                        lambda agent: {"enabled": True, "budget_usd": 50.0,
                                       "warning_threshold": 0.8, "hard_stop": True})
    monkeypatch.setattr(cost_budget, "settings_for",
                        lambda fn, *a, **kw: {"enabled": True, "limit_usd": 200.0,
                                              "period": "monthly", "soft_thresholds": [0.5],
                                              "hard_action": "downgrade"})


def _dispatch(task_id, at="2026-09-10T12:00:00+00:00"):
    return {"task_id": task_id, "recorded_at": at}


def test_the_survey_calls_the_same_classify(survey_env, monkeypatch):
    seen = []
    real = sr.classify

    def _spy(task_id, checks):
        seen.append((task_id, set(checks)))
        return real(task_id, checks)
    monkeypatch.setattr(sr, "classify", _spy)
    conn = _Conn([_dispatch("a"), _dispatch("b")],
                 period={"budget_usd": 200.0, "budget_tokens": 400000,
                         "warning_threshold": 0.8, "hard_stop": 1},
                 usage={"usd": 1.0, "tokens": 400013})
    report = sr.survey(conn=conn)
    assert report["state"] == "measured"
    assert ("a", {"budget"}) in seen and ("b", {"budget"}) in seen
    assert report["verdicts"][sr.WAIT] == 2 and report["fires"] == 2
    assert report["fire_rate_pct"] == 100.0
    assert report["arms"]["module"][sr.WAIT] == 2
    assert report["fired"][0]["task_id"] == "a"


def test_the_survey_reads_the_ledger_as_it_was(survey_env):
    conn = _Conn([_dispatch("a")],
                 period={"budget_usd": 200.0, "budget_tokens": 0, "warning_threshold": 0.8,
                         "hard_stop": 1},
                 usage={"usd": 10.0, "tokens": 0}, agent_usd=1.0, telemetry_usd=120.0)
    report = sr.survey(conn=conn)
    assert report["verdicts"][sr.ASK] == 1, "120 of 200 crosses the 0.5 soft threshold"
    assert report["fires"] == 0 and report["fire_rate_pct"] == 0.0
    usage_sql = [s for s in conn.sql if "module_budget_usage" in s][0]
    assert "created_at <" in usage_sql, "spend is summed UP TO the dispatch instant"


def test_a_missing_period_row_is_unmeasurable_not_allow(survey_env):
    conn = _Conn([_dispatch("a")], period=None, agent_usd=0.0, telemetry_usd=0.0)
    report = sr.survey(conn=conn)
    assert report["arms"]["module"][sr.UNMEASURABLE] == 1
    assert report["verdicts"][sr.PROCEED] == 1


def test_a_board_with_no_dispatches_is_unmeasurable_not_a_clean_survey(survey_env):
    report = sr.survey(conn=_Conn([]))
    assert report["state"] == "unmeasurable"
    assert report["fires"] is None and report["fire_rate_pct"] is None


def test_an_unreadable_transitions_table_is_unmeasurable(survey_env):
    report = sr.survey(conn=_Conn([], broken=True))
    assert report["state"] == "unmeasurable"
    assert report["fire_rate_pct"] is None


def test_fire_rate_is_none_when_every_dispatch_was_unmeasurable(survey_env, monkeypatch):
    monkeypatch.setattr(sr, "_replay_module", lambda *a, **kw: None)
    monkeypatch.setattr(sr, "_replay_token", lambda *a, **kw: None)
    monkeypatch.setattr(sr, "_replay_cost", lambda *a, **kw: None)
    report = sr.survey(conn=_Conn([_dispatch("a")]))
    assert report["state"] == "measured"
    assert report["verdicts"][sr.UNMEASURABLE] == 1
    assert report["fire_rate_pct"] is None
    assert report["unmeasurable_pct"] == 100.0


# --------------------------------------------------------------------------- #
# 8. CLI
# --------------------------------------------------------------------------- #
def test_cli_task_prints_one_verdict_with_a_budget_check(quiet_board, capsys, monkeypatch):
    monkeypatch.setattr(sr, "_load_task", lambda tid: {"id": tid, "title": "x"})
    assert sr.main(["--task", "t-1", "--json"]) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == sr.PROCEED and "budget" in out["checks"]
    assert out["mode"] == "report"


def test_cli_survey_exit_codes(survey_env, monkeypatch, capsys):
    monkeypatch.setattr(sr, "survey", lambda **kw: {"state": "unmeasurable", "reason": "r",
                                                    "dispatches": 0, "fires": None,
                                                    "fire_rate_pct": None})
    assert sr.main(["--survey"]) == 2
    assert "UNMEASURABLE" in capsys.readouterr().out


def test_period_end_helpers():
    assert sr._month_after("2026-09") == "2026-10-01T00:00:00+00:00"
    assert sr._period_end("2026-09-30", "daily") == "2026-10-01T00:00:00+00:00"
    assert sr._period_end("garbage", "monthly") is None
    assert datetime.fromisoformat(sr._month_after("2026-01")).tzinfo == timezone.utc
