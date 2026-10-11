# CUI // SP-CTI
"""omx-guard-02: the runner refuses AUTONOMOUS dispatch to an unguarded adapter.

Covers the card's acceptance criteria:

  1. enforce: an unguarded adapter is never handed back to the runner, the
     refusal is recorded on the task, and the next GUARDED adapter in
     fallback_order is picked instead,
  2. report: the refusal is logged and recorded but the adapter still runs,
  3. claude_cli counts as guarded only via an un-neutralised settings.json hook,
  4. ``--survey`` lists every registered adapter's verdict.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools.agents import guard_parity as gp
from tools.agents import registry


GUARDED = {"claude_cli", "pi_cli"}


def _verdict(name: str) -> gp.GuardVerdict:
    return gp.GuardVerdict(name, name in GUARDED, "fake verdict for " + name)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(gp.MODE_ENV, raising=False)
    monkeypatch.delenv("ICDEV_PRETOOLUSE_ENFORCE", raising=False)
    gp.reset_cache()
    yield
    gp.reset_cache()


# ── gate() ──────────────────────────────────────────────────────────────────
def test_enforce_refuses_unguarded_and_falls_back_to_a_guarded_adapter():
    d = gp.gate("local_agent", ["local_agent", "goose_cli", "claude_cli"],
                verdict=_verdict, available=lambda _n: True, gate_mode=gp.ENFORCE)
    assert d.adapter == "claude_cli"
    assert [v.adapter for v in d.refused] == ["local_agent", "goose_cli"]
    assert "refused" in d.note and "local_agent" in d.note


def test_enforce_with_no_guarded_fallback_spawns_nothing():
    d = gp.gate("local_agent", ["local_agent"], verdict=_verdict,
                available=lambda _n: True, gate_mode=gp.ENFORCE)
    assert d.adapter is None
    assert "no guarded adapter available" in d.note


def test_enforce_skips_an_unavailable_guarded_fallback():
    d = gp.gate("goose_cli", ["pi_cli", "claude_cli"], verdict=_verdict,
                available=lambda n: n != "pi_cli", gate_mode=gp.ENFORCE)
    assert d.adapter == "claude_cli"


def test_report_mode_keeps_the_adapter_but_reports_the_refusal():
    d = gp.gate("local_agent", ["claude_cli"], verdict=_verdict,
                available=lambda _n: True, gate_mode=gp.REPORT)
    assert d.adapter == "local_agent"
    assert "mode=report" in d.note


def test_guarded_adapter_passes_silently():
    d = gp.gate("claude_cli", ["claude_cli"], verdict=_verdict,
                available=lambda _n: True, gate_mode=gp.ENFORCE)
    assert d.adapter == "claude_cli" and d.note == ""


def test_mode_defaults_to_enforce_and_reads_env(monkeypatch):
    assert gp.mode() == gp.ENFORCE
    monkeypatch.setenv(gp.MODE_ENV, "report")
    assert gp.mode() == gp.REPORT
    monkeypatch.setenv(gp.MODE_ENV, "bogus")
    assert gp.mode() == gp.DEFAULT_MODE


# ── claude_cli: settings.json probe ─────────────────────────────────────────
def _settings(tmp_path, command, matcher=""):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": matcher, "hooks": [{"type": "command", "command": command}]},
    ]}}), encoding="utf-8")
    return p


def test_claude_guarded_by_unwrapped_hook(tmp_path):
    p = _settings(tmp_path, "python $CLAUDE_PROJECT_DIR/.claude/hooks/pre_tool_use.py")
    assert gp.claude_settings_verdict(p).guarded


@pytest.mark.parametrize("suffix", [" || true", " || exit 0", "; true", " ||:"])
def test_claude_unguarded_when_hook_is_neutralised(tmp_path, suffix):
    p = _settings(tmp_path, "python .claude/hooks/pre_tool_use.py" + suffix)
    v = gp.claude_settings_verdict(p)
    assert not v.guarded and "neutraliser" in v.reason


def test_claude_unguarded_when_hook_only_matches_some_tools(tmp_path):
    p = _settings(tmp_path, "python .claude/hooks/pre_tool_use.py", matcher="Bash")
    assert not gp.claude_settings_verdict(p).guarded


def test_claude_unguarded_when_enforcement_is_stood_down(tmp_path, monkeypatch):
    p = _settings(tmp_path, "python .claude/hooks/pre_tool_use.py")
    monkeypatch.setenv("ICDEV_PRETOOLUSE_ENFORCE", "0")
    assert not gp.claude_settings_verdict(p).guarded


def test_shipped_settings_guard_claude_cli():
    """The live default adapter is guarded -- the precondition for enforce."""
    assert gp.claude_settings_verdict().guarded


# ── survey ──────────────────────────────────────────────────────────────────
def test_survey_lists_every_registered_adapter(monkeypatch):
    monkeypatch.setattr(gp, "verdict_for", lambda n, use_cache=True: _verdict(n))
    monkeypatch.setattr(gp, "_available", lambda _n: True)
    out = gp.survey()
    assert {r["adapter"] for r in out["adapters"]} == set(registry.list_adapters())
    assert "local_agent" in out["refused"] and "claude_cli" not in out["refused"]


# ── the runner: tools/genesis/reflexes/kanban.py ────────────────────────────
class _Conn:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=()):
        self.sink.append((sql, params))


@pytest.fixture
def runner(monkeypatch):
    from tools.genesis.reflexes import kanban

    writes = []

    @contextmanager
    def _fake_conn(*_a, **_k):
        yield _Conn(writes)

    monkeypatch.setattr(kanban, "get_connection", _fake_conn)
    monkeypatch.setattr(gp, "verdict_for", lambda n, use_cache=True: _verdict(n))
    monkeypatch.setattr(gp, "_available", lambda _n: True)
    monkeypatch.setattr(registry, "get_adapter", lambda n: SimpleNamespace(name=n))
    return SimpleNamespace(kanban=kanban, writes=writes)


def test_runner_never_spawns_an_unguarded_adapter_in_enforce(runner, monkeypatch):
    monkeypatch.setenv(gp.MODE_ENV, "enforce")
    outcomes = []
    got = runner.kanban._guard_parity_screen(
        SimpleNamespace(name="local_agent"), ["local_agent", "claude_cli"],
        "omx-test-01", outcomes)
    assert got.name == "claude_cli"
    assert outcomes and "local_agent" in outcomes[0]
    sql, params = runner.writes[0]
    assert "last_failure_reason" in sql and params[-1] == "omx-test-01"
    assert "refused" in params[0]


def test_runner_spawns_nothing_when_no_guarded_adapter_is_left(runner, monkeypatch):
    monkeypatch.setenv(gp.MODE_ENV, "enforce")
    got = runner.kanban._guard_parity_screen(
        SimpleNamespace(name="local_agent"), ["local_agent"], "omx-test-01", [])
    assert got is None
    assert runner.writes, "the refusal reason must be recorded on the task"


def test_runner_report_mode_logs_without_refusing(runner, monkeypatch):
    monkeypatch.setenv(gp.MODE_ENV, "report")
    adapter = SimpleNamespace(name="local_agent")
    got = runner.kanban._guard_parity_screen(adapter, ["local_agent", "claude_cli"],
                                             "omx-test-01", [])
    assert got is adapter
    assert "mode=report" in runner.writes[0][1][0]


def test_runner_leaves_a_guarded_adapter_alone(runner):
    adapter = SimpleNamespace(name="claude_cli")
    assert runner.kanban._guard_parity_screen(adapter, ["claude_cli"], "t", []) is adapter
    assert runner.writes == []
