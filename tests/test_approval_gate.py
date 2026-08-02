# CUI // SP-CTI
"""Tests for the agent-loop approval gate (ars-appr-01).

Three acceptance properties, one section each:

1. An irreversible tool call halts for confirmation.
2. An unknown tool defaults to requiring approval.
3. Every decision is recorded append-only with an actor and a reason.

Plus the ordering properties that make the first two hold: a first-party
read-only tool never prompts, and an allowlisted shell tool cannot launder an
irreversible command through itself.
"""
from __future__ import annotations

import sqlite3

import pytest

# Patch the CANONICAL module, not the tools/ shim. The shim re-exports the same
# objects by value, so setattr on it would leave the functions that `evaluate`
# actually resolves from its own module globals untouched — the monkeypatch would
# appear to apply and change nothing.
from icdev.tools.llm import approval_gate as ag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_config_cache(monkeypatch):
    """The config is process-cached; every test starts from a clean read."""
    monkeypatch.setattr(ag, "_CONFIG_CACHE", None, raising=False)
    # Never let an ambient operator env leak into a gate assertion.
    monkeypatch.delenv("ICDEV_AGENT_APPROVAL_MODE", raising=False)
    monkeypatch.delenv("ICDEV_AGENT_APPROVAL_GATE", raising=False)
    yield
    monkeypatch.setattr(ag, "_CONFIG_CACHE", None, raising=False)


@pytest.fixture
def no_hard_block(monkeypatch):
    """Isolate the approval path from the platform's outright-block hook.

    The hard block is tested separately; elsewhere we want to observe the
    approver's decision, not the hook's.
    """
    monkeypatch.setattr(ag, "_hard_block_reason", lambda *_a, **_k: None)


@pytest.fixture
def approval_db(tmp_path, monkeypatch):
    """A throwaway SQLite DB holding only ``agent_approval_log``.

    Built from the same DDL the migration ships so a column drift between the
    migration and the recorder's INSERT fails here rather than in production.
    """
    root = ag._repo_root()
    assert root is not None, "could not resolve repo root"
    ddl = (
        root
        / "tools"
        / "db"
        / "migrations"
        / "20260802200931_agent_approval_log"
        / "up.sql"
    ).read_text(encoding="utf-8")

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    return db_path


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM agent_approval_log")]
    finally:
        conn.close()


def _approver(approved: bool, actor: str = "tester", reason: str = "because"):
    return lambda _req: ag.ApprovalDecision(approved=approved, actor=actor, reason=reason)


# ---------------------------------------------------------------------------
# 1. An irreversible tool call halts for confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["git_push", "merge_pr", "delete_branch", "terraform_apply", "send_email"],
)
def test_irreversible_tool_names_are_classified_irreversible(tool_name):
    result = ag.classify(tool_name, {})
    assert result.reversibility == ag.IRREVERSIBLE
    assert result.requires_approval is True


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push --force-with-lease origin feat/x",
        "gh pr merge 123 --merge",
        "git worktree remove /tmp/wt",
        "rm -rf build/",
        "DELETE FROM audit_trail WHERE id = 1",
        "python tools/kanban/cli.py --set-status ars-appr-01 done",
        "git stash",
    ],
)
def test_irreversible_commands_are_caught_inside_a_generic_tool(command):
    """The command text decides, not the tool name — a shell tool carries anything."""
    result = ag.classify("run_command", {"command": command})
    assert result.reversibility == ag.IRREVERSIBLE, result
    assert result.rule_id.startswith("irreversible_pattern:")


def test_allowlisted_shell_tool_cannot_launder_an_irreversible_command():
    """run_command is on the reversible allowlist; `git push` must still halt.

    This is the ordering property: content patterns are evaluated BEFORE the
    reversible allowlist. If that order flipped, every irreversible act in the
    platform could be smuggled through one allowlisted tool.
    """
    config = ag.load_config()
    assert "run_command" in config["reversible"]["tools"], "precondition: allowlisted"

    assert ag.classify("run_command", {"command": "ls -la"}).reversibility == ag.REVERSIBLE
    assert (
        ag.classify("run_command", {"command": "git push origin main"}).reversibility
        == ag.IRREVERSIBLE
    )


def test_irreversible_call_is_blocked_and_not_executed(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: "row-1")
    hook = ag.build_approval_hook(approver=_approver(False, "alice", "too risky"))

    message = hook("git_push", {"remote": "origin"})

    assert message, "an irreversible call must be blocked"
    assert "NOT EXECUTED" in message
    assert "alice" in message and "too risky" in message


def test_irreversible_call_proceeds_once_a_human_confirms(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: "row-1")
    hook = ag.build_approval_hook(approver=_approver(True, "alice", "reviewed the diff"))

    assert hook("git_push", {"remote": "origin"}) is None


def test_a_broken_approver_denies(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)

    def _explode(_req):
        raise RuntimeError("approver is down")

    outcome = ag.evaluate("git_push", {}, approver=_explode)

    assert outcome.allowed is False
    assert outcome.decision.actor == "system:approver_error"


def test_default_approver_denies_without_an_interactive_console(monkeypatch):
    """An unattended run — cron, CI, the kanban runner — can never self-approve."""

    class _NotATty:
        def isatty(self):
            return False

    monkeypatch.setattr(ag.sys, "stdin", _NotATty())
    decision = ag.default_approver(
        ag.ApprovalRequest("git_push", {}, ag.classify("git_push", {}))
    )
    assert decision.approved is False


def test_hard_blocked_action_is_denied_rather_than_offered_for_approval(monkeypatch):
    """A platform-forbidden action is not approvable, even by a yes-to-everything approver."""
    monkeypatch.setattr(ag, "_hard_block_reason", lambda *_a, **_k: "destructive git command")
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)

    outcome = ag.evaluate("run_command", {"command": "git push --force"},
                          approver=_approver(True, "alice", "trust me"))

    assert outcome.allowed is False
    assert outcome.decision.actor == "system:pre_tool_use_hook"


# ---------------------------------------------------------------------------
# 2. An unknown tool defaults to requiring approval
# ---------------------------------------------------------------------------


def test_unknown_tool_requires_approval():
    result = ag.classify("frobnicate_the_widget", {"anything": 1})
    assert result.reversibility == ag.UNKNOWN
    assert result.requires_approval is True
    assert result.rule_id == "no_rule_matched"


def test_unknown_tool_is_blocked_when_nobody_can_approve(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)
    hook = ag.build_approval_hook(approver=ag.deny_approver)

    assert hook("frobnicate_the_widget", {}), "an unknown tool must not run unapproved"


def test_a_missing_config_narrows_rather_than_opens_the_gate(monkeypatch):
    """A config file that cannot be read must not become an empty allowlist.

    An allowlist that fails open is decoration: with no config at all, every tool
    except the loop-terminating `done` falls through to UNKNOWN.
    """
    monkeypatch.setattr(ag, "_CONFIG_CACHE", None, raising=False)
    monkeypatch.setattr(ag, "_repo_root", lambda: None)

    config = ag.load_config(refresh=True)

    assert ag.classify("done", {}, config=config).reversibility == ag.REVERSIBLE
    for tool in ("write_file", "run_command", "run_tool", "browser_click"):
        assert ag.classify(tool, {}, config=config).requires_approval is True, tool


def test_read_only_schema_short_circuits_the_content_patterns():
    """grep_files("git push") is a search, not a push — it must not prompt.

    is_read_only lives in the tool schema, which is first-party code the model
    cannot write, so it is trustworthy in a way the input text is not.
    """
    schema = {"name": "grep_files", "is_read_only": True}
    result = ag.classify("grep_files", {"pattern": "git push"}, tool_schema=schema)

    assert result.reversibility == ag.REVERSIBLE
    assert result.rule_id == "read_only_schema"


def test_an_explicitly_irreversible_tool_name_beats_its_read_only_flag():
    """Rule order: a tool listed as irreversible is not rescued by a read-only claim."""
    schema = {"name": "delete_branch", "is_read_only": True}
    result = ag.classify("delete_branch", {}, tool_schema=schema)

    assert result.reversibility == ag.IRREVERSIBLE


def test_schema_can_declare_reversibility_explicitly():
    schema = {"name": "custom_tool", "reversibility": ag.IRREVERSIBLE}
    assert ag.classify("custom_tool", {}, tool_schema=schema).reversibility == ag.IRREVERSIBLE


# ---------------------------------------------------------------------------
# 3. Every decision is recorded append-only with actor and reason
# ---------------------------------------------------------------------------


def test_a_denial_is_recorded_with_actor_and_reason(approval_db, no_hard_block):
    ag.evaluate("git_push", {"remote": "origin"},
                approver=_approver(False, "alice", "unreviewed diff"),
                session_id="sess-1", trace_id="trace-1")

    rows = _rows(approval_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "denied"
    assert row["actor"] == "alice"
    assert row["reason"] == "unreviewed diff"
    assert row["reversibility"] == ag.IRREVERSIBLE
    assert row["tool_name"] == "git_push"
    assert row["session_id"] == "sess-1"
    assert row["rule_id"]


def test_an_approval_is_recorded_too(approval_db, no_hard_block):
    ag.evaluate("git_push", {}, approver=_approver(True, "bob", "release cut approved"))

    rows = _rows(approval_db)
    assert len(rows) == 1
    assert rows[0]["decision"] == "approved"
    assert rows[0]["actor"] == "bob"
    assert rows[0]["reason"] == "release cut approved"


def test_an_unknown_tool_decision_is_recorded_as_unknown(approval_db, no_hard_block):
    ag.evaluate("frobnicate_the_widget", {}, approver=ag.deny_approver)

    rows = _rows(approval_db)
    assert len(rows) == 1
    assert rows[0]["reversibility"] == ag.UNKNOWN
    assert rows[0]["decision"] == "denied"


def test_mode_off_auto_approves_but_still_records_who_and_why(approval_db, no_hard_block,
                                                              monkeypatch):
    monkeypatch.setenv(ag._ACTOR_ENV, "operator-on-call")

    outcome = ag.evaluate("git_push", {}, mode=ag.MODE_OFF)

    assert outcome.allowed is True
    rows = _rows(approval_db)
    assert len(rows) == 1
    assert rows[0]["decision"] == "approved"
    assert rows[0]["approval_mode"] == ag.MODE_OFF
    assert rows[0]["actor"] == "operator-on-call"
    assert "mode=off" in rows[0]["reason"]


def test_mode_deny_never_approves(approval_db, no_hard_block):
    outcome = ag.evaluate("git_push", {}, mode=ag.MODE_DENY,
                          approver=_approver(True, "alice", "please"))

    assert outcome.allowed is False
    assert _rows(approval_db)[0]["actor"] == "system:deny_mode"


def test_a_reversible_call_produces_no_decision_row(approval_db, no_hard_block):
    """The trail records DECISIONS. A reversible call has none to make."""
    outcome = ag.evaluate("read_file", {"path": "README.md"},
                          tool_schema={"name": "read_file", "is_read_only": True})

    assert outcome.allowed is True
    assert _rows(approval_db) == []


def test_an_approver_that_supplies_no_actor_is_not_recorded_as_null(approval_db,
                                                                   no_hard_block):
    """Anonymous is worse than attributed-to-the-system: never write a blank actor."""
    ag.evaluate("git_push", {}, approver=lambda _r: ag.ApprovalDecision(False, "", ""))

    row = _rows(approval_db)[0]
    assert row["actor"] == "system:unattributed"
    assert row["reason"] == "no reason recorded"


def test_recording_failure_is_logged_not_swallowed(monkeypatch, tmp_path, no_hard_block):
    """A silent INSERT failure would make the trail look complete while it is empty.

    The DB path points at a directory, so the real storage layer fails to open it.
    No connection factory is stubbed: a stubbed write is exactly what hides a
    schema mismatch, and this test exists to prove the failure is visible.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path))  # a directory, not a file

    warnings: list[tuple] = []
    monkeypatch.setattr(ag.logger, "warning", lambda *a, **_k: warnings.append(a))

    row_id = ag.record_decision(
        tool_name="git_push",
        tool_input={},
        classification=ag.classify("git_push", {}),
        decision=ag.ApprovalDecision(False, "alice", "no"),
        mode=ag.MODE_MANUAL,
    )

    assert row_id is None
    assert any("could not record" in str(call[0]) for call in warnings)


# ---------------------------------------------------------------------------
# Composition with a caller-supplied hook
# ---------------------------------------------------------------------------


def test_a_caller_hook_still_runs_and_can_still_block(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)
    hook = ag.build_approval_hook(
        approver=_approver(True, "alice", "fine by me"),
        chain=lambda name, _inp: "caller says no" if name == "read_file" else None,
    )

    assert hook("read_file", {}) == "caller says no"


def test_the_gate_still_applies_when_the_caller_hook_allows(no_hard_block, monkeypatch):
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)
    hook = ag.build_approval_hook(
        approver=ag.deny_approver, chain=lambda _n, _i: None
    )

    assert hook("git_push", {}), "a permissive caller hook must not disable the gate"


def test_a_bool_returning_approver_is_accepted(no_hard_block, monkeypatch):
    """tools/agent_runtime/safety.py approvers return a bare bool."""
    monkeypatch.setattr(ag, "record_decision", lambda **_k: None)

    assert ag.evaluate("git_push", {}, approver=lambda _r: True).allowed is True
    assert ag.evaluate("git_push", {}, approver=lambda _r: False).allowed is False


# ---------------------------------------------------------------------------
# Mode / enablement resolution
# ---------------------------------------------------------------------------


def test_mode_resolves_arg_then_env_then_config(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", ag.MODE_DENY)
    assert ag.resolve_mode() == ag.MODE_DENY
    assert ag.resolve_mode(ag.MODE_OFF) == ag.MODE_OFF

    monkeypatch.delenv("ICDEV_AGENT_APPROVAL_MODE")
    assert ag.resolve_mode() == ag.MODE_MANUAL


def test_an_unknown_mode_falls_back_to_manual_not_off(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", "yolo")
    assert ag.resolve_mode() == ag.MODE_MANUAL


def test_gate_is_enabled_by_default_and_switchable(monkeypatch):
    assert ag.is_enabled() is True
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_GATE", "0")
    assert ag.is_enabled() is False
    assert ag.is_enabled(True) is True, "an explicit argument wins over the env"


# ---------------------------------------------------------------------------
# Wiring: run_agent_loop gates by default, without the caller opting in
# ---------------------------------------------------------------------------


class _FakeProvider:
    provider_name = "anthropic"


class _ScriptedRouter:
    """Returns a scripted sequence: tool-call lists, then a final text answer."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get_provider_for_function(self, _function):
        return _FakeProvider(), "fake-model", {"supports_tools": True}

    def invoke(self, _function, _request):
        from icdev.tools.llm.provider import LLMResponse

        entry = self._responses[self.calls]
        self.calls += 1
        if isinstance(entry, str):
            return LLMResponse(content=entry, stop_reason="end_turn", provider="fake")
        return LLMResponse(
            content="", tool_calls=list(entry), stop_reason="tool_use", provider="fake"
        )


def _run_loop(tool_name, handler, **kwargs):
    from icdev.tools.llm.agent_loop import run_agent_loop

    router = _ScriptedRouter([
        [{"id": "c1", "name": tool_name, "input": {"remote": "origin"}}],
        "done",
    ])
    return run_agent_loop(
        router,
        system_prompt="sys",
        user_prompt="task",
        tools=[{"type": "function", "function": {"name": tool_name, "parameters": {}}}],
        tool_handlers={tool_name: handler},
        max_iterations=3,
        memory_enabled=False,
        _record_harness_decision=False,
        **kwargs,
    )


def test_run_agent_loop_gates_an_unknown_tool_by_default(approval_db, no_hard_block,
                                                         monkeypatch):
    """No approval kwargs, no on_pre_tool_use — the handler must still not run.

    This is the property that makes the gate a gate rather than a helper: a call
    site written before ars-appr-01 existed is covered anyway.
    """
    monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", ag.MODE_DENY)
    executed = []

    result = _run_loop("frobnicate_the_widget", lambda inp, stop: executed.append(inp))

    assert executed == [], "an unknown tool executed without approval"
    blocked = [e for e in result.tool_call_log if e["error"]]
    assert blocked and "APPROVAL REQUIRED" in blocked[0]["error"]
    assert _rows(approval_db)[0]["reversibility"] == ag.UNKNOWN


def test_run_agent_loop_halts_an_irreversible_tool_and_records_the_denial(
    approval_db, no_hard_block
):
    executed = []
    result = _run_loop(
        "git_push",
        lambda inp, stop: executed.append(inp),
        approver=lambda _r: ag.ApprovalDecision(False, "alice", "not for this branch"),
    )

    assert executed == []
    assert any("APPROVAL REQUIRED" in (e["error"] or "") for e in result.tool_call_log)
    row = _rows(approval_db)[0]
    assert (row["decision"], row["actor"], row["reason"]) == (
        "denied", "alice", "not for this branch",
    )


def test_run_agent_loop_executes_an_irreversible_tool_once_approved(approval_db,
                                                                    no_hard_block):
    executed = []

    def _handler(inp, _stop):
        executed.append(inp)
        return "pushed"

    _run_loop(
        "git_push",
        _handler,
        approver=lambda _r: ag.ApprovalDecision(True, "alice", "release approved"),
    )

    assert executed == [{"remote": "origin"}]
    assert _rows(approval_db)[0]["decision"] == "approved"


def test_run_agent_loop_leaves_a_read_only_tool_alone(approval_db, no_hard_block):
    """The gate must not tax ordinary reads — no prompt, no row."""
    from icdev.tools.llm.agent_loop import run_agent_loop

    router = _ScriptedRouter([
        [{"id": "c1", "name": "look", "input": {"path": "README.md"}}],
        "done",
    ])
    executed = []
    run_agent_loop(
        router,
        system_prompt="sys",
        user_prompt="task",
        tools=[{"type": "function",
                "function": {"name": "look", "parameters": {}, "is_read_only": True}}],
        tool_handlers={"look": lambda inp, _s: executed.append(inp) or "ok"},
        max_iterations=3,
        memory_enabled=False,
        approver=ag.deny_approver,
        _record_harness_decision=False,
    )

    assert executed == [{"path": "README.md"}]
    assert _rows(approval_db) == []


def test_the_shim_and_canonical_module_are_the_same_objects():
    """A physically separate copy is how tools/llm/agent_loop.py drifted before."""
    from icdev.tools.llm import approval_gate as canonical

    assert ag.classify is canonical.classify
    assert ag.evaluate is canonical.evaluate
    assert ag.build_approval_hook is canonical.build_approval_hook
