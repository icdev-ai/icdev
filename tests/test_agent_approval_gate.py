# CUI // SP-CTI
"""Approval gate for irreversible agent actions (ars-appr-01).

Three things have to be true, and each has a test class here:

  1. An irreversible tool call halts for confirmation.
  2. An unknown tool defaults to requiring approval (fail closed).
  3. Every decision — approved and denied — is recorded append-only with the
     actor and the reason.

The audit tests INSERT against the DDL from ``migration 342`` itself rather
than a hand-written schema, so a column added to one and not the other fails
here instead of at runtime inside a swallowed exception (CLAUDE.md: "every
column in an INSERT must exist in the LIVE schema").
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from icdev.tools.llm.agent_loop import (
    _compose_pre_tool_hooks,
    _resolve_approval_gate,
    run_agent_loop,
)
from tests.test_agent_loop import ScriptedRouter, _tool
from tools.agent_runtime.approval_gate import (
    IRREVERSIBLE,
    MODE_DRY_RUN,
    MODE_ENFORCE,
    MODE_OFF,
    RECOVERABLE,
    REVERSIBLE,
    UNKNOWN,
    ApprovalDecision,
    Classification,
    build_approval_hook,
    classify,
    load_policy,
    record_decision,
    resolve_actor,
    resolve_mode,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _approver(approved: bool, reason: str = "because"):
    """An approver that records what it was asked."""
    seen: list[Any] = []

    def approve(request):
        seen.append(request)
        return ApprovalDecision(approved, reason, "test-operator")

    approve.seen = seen  # type: ignore[attr-defined]
    return approve


def _gate(**kwargs):
    """Build a gate that never touches the DB or the pre_tool_use hook."""
    kwargs.setdefault("approver", _approver(False))
    kwargs.setdefault("mode", MODE_ENFORCE)
    kwargs.setdefault("actor", "test-operator")
    kwargs.setdefault("consult_pre_tool_check", False)
    return build_approval_hook(**kwargs)


@pytest.fixture(autouse=True)
def _no_db_writes(monkeypatch):
    """Default: swallow the audit write. The audit tests opt back in."""
    monkeypatch.setattr(
        "tools.agent_runtime.approval_gate.record_decision",
        lambda **kw: True,
    )


# ---------------------------------------------------------------------------
# 1. Classification
# ---------------------------------------------------------------------------
class TestClassification:
    def test_policy_loads_from_args(self):
        policy = load_policy(refresh=True)
        assert policy["default_tier"] == UNKNOWN
        assert UNKNOWN in policy["require_approval_tiers"]
        assert IRREVERSIBLE in policy["require_approval_tiers"]

    @pytest.mark.parametrize(
        "tool",
        ["git_push", "pr_merge", "delete_branch", "terraform_apply", "kanban_set_done"],
    )
    def test_named_irreversible_tools_require_approval(self, tool):
        cls = classify(tool, {})
        assert cls.tier == IRREVERSIBLE
        assert cls.requires_approval is True

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin main",
            "git push --force-with-lease origin feat/x",
            "gh pr merge 42 --merge",
            "git worktree remove /tmp/wt",
            "git branch -D feat/x",
            "rm -rf build/",
            "DROP TABLE audit_trail",
            "DELETE FROM audit_trail WHERE id = 1",
            "curl -X POST https://example.test/hook -d '{}'",
            "kubectl delete pod web-0",
            "docker push registry.test/img:1",
        ],
    )
    def test_irreversible_commands_inside_a_generic_tool(self, command):
        """A shell tool is only as reversible as the string it is handed."""
        cls = classify("run_command", {"command": command})
        assert cls.tier == IRREVERSIBLE, command
        assert cls.requires_approval is True

    def test_content_pattern_beats_a_merely_asserted_read_only_tool(self):
        """A tool's own claim to be read-only is not a fact; the policy's is.

        This test used to assert that ``read_file`` escalates on an argument
        containing ``git push --force``. That conflated two different claims:

        * a *tool schema* declaring itself ``is_read_only`` — untrusted, because
          it ships with the tool and asserts its own safety; and
        * the *operator* enumerating a tool under ``reversible`` in
          args/agent_approval_policy.yaml — trusted, because that file is
          already the source of truth for every other tier.

        ``read_file`` is the second kind, so it is now exempt (see
        TestReversibleToolsAreNotEscalatedByTheirArguments). The property this
        test actually protects — that a tool the operator has NOT vouched for
        cannot escape escalation by looking harmless — is pinned here with a
        subject that is genuinely unvouched.
        """
        cls = classify("totally_safe_reader", {"path": "x", "then": "git push --force"})
        assert cls.tier == IRREVERSIBLE
        assert cls.requires_approval is True

    def test_reversible_and_recoverable_do_not_halt(self):
        assert classify("read_file", {"path": "a"}).tier == REVERSIBLE
        assert classify("read_file", {"path": "a"}).requires_approval is False
        assert classify("write_file", {"path": "a", "content": "b"}).tier == RECOVERABLE
        assert classify("write_file", {"path": "a"}).requires_approval is False

    def test_unknown_tool_defaults_to_requiring_approval(self):
        cls = classify("frobnicate_the_widget", {"anything": 1})
        assert cls.tier == UNKNOWN
        assert cls.rule == "default_tier"
        assert cls.requires_approval is True

    @pytest.mark.parametrize(
        "tool,tool_input",
        [
            # Enumerated irreversible tools, downgraded by incidental argument text.
            ("git_push", {"note": "mkdir logs"}),
            ("delete_worktree", {"path": "touch"}),
            ("terraform_apply", {"msg": "git commit first"}),
            ("kanban_set_done", {"x": "mkdir"}),
            # Unenumerated tools, auto-allowed by the same incidental text.
            ("wipe_production_database", {"note": "mkdir the backup dir first"}),
            ("delete_customer_records", {"plan": "git add . then purge"}),
            ("exfiltrate", {"step": "touch file"}),
        ],
    )
    def test_incidental_text_cannot_downgrade_a_tool(self, tool, tool_input):
        """A downgrade pattern must not fire on a tool it was never written for.

        Regression: ``recoverable``/``reversible`` content patterns were matched
        against every tool and evaluated *before* the tool's own tier, so
        ``git_push`` with a ``{"note": "mkdir logs"}`` argument classified as
        recoverable and was auto-allowed — and so was any unenumerated tool
        whose input merely contained the word. Content patterns may now only
        escalate, except for the declared ``command_tools``.
        """
        cls = classify(tool, tool_input)
        assert cls.requires_approval is True, f"{tool} was auto-allowed: {cls}"
        assert cls.tier in (IRREVERSIBLE, UNKNOWN)

    def test_generic_executors_are_still_tiered_by_their_content(self):
        """The downgrade path still works where it was actually intended."""
        assert classify("run_command", {"command": "git add ."}).tier == RECOVERABLE
        assert classify("run_command", {"command": "git add ."}).requires_approval is False
        # ...but escalation still beats it.
        assert classify("run_command", {"command": "git push"}).tier == IRREVERSIBLE

    def test_compiled_pattern_cache_is_not_confused_by_address_reuse(self):
        """The cache is keyed on id(policy); it must verify identity, not just the key."""
        import tools.agent_runtime.approval_gate as ag

        a = {"default_tier": UNKNOWN, "require_approval_tiers": [UNKNOWN],
             "command_patterns": {IRREVERSIBLE: [{"pattern": "alpha", "detail": "a"}]}}
        assert len(ag._compiled_patterns(a)) == 1
        b = {"default_tier": UNKNOWN, "require_approval_tiers": [UNKNOWN],
             "command_patterns": {IRREVERSIBLE: []}}
        # Even if `b` were allocated at `a`'s address, it must not inherit them.
        ag._PATTERN_CACHE[id(b)] = (a, ag._compiled_patterns(a))
        assert ag._compiled_patterns(b) == []

    def test_missing_policy_file_fails_closed(self, monkeypatch):
        """A config file that cannot be read must not authorise anything."""
        import tools.agent_runtime.approval_gate as ag

        monkeypatch.setattr(ag, "_find_policy_path", lambda: None)
        # monkeypatch restores the cache at teardown, so the empty policy cannot
        # leak into whatever test runs next.
        monkeypatch.setattr(ag, "_POLICY_CACHE", None)
        policy = ag.load_policy(refresh=True)
        assert policy["default_tier"] == UNKNOWN
        # With no tool enumerated, even a plain read needs a human.
        assert classify("read_file", {}, policy=policy).requires_approval is True



class TestReversibleToolsAreNotEscalatedByTheirArguments:
    """A tool that cannot act cannot be made irreversible by its arguments.

    Before this, `read_file("how do I git push safely")` classified irreversible
    and halted for a human. A gate that prompts on a read teaches operators to
    approve reflexively, which costs more than the escalation buys.

    The trust boundary is what makes it safe: the claim comes from
    args/agent_approval_policy.yaml, the operator's own file, not from a tool
    schema's self-declared is_read_only flag.
    """

    @pytest.mark.parametrize("tool", ["read_file", "list_dir", "grep"])
    @pytest.mark.parametrize(
        "text",
        [
            "how do I git push safely",
            "git push --force",
            "rm -rf /",
            "drop table audit_trail",
        ],
    )
    def test_a_read_only_tool_is_not_escalated_by_its_input(self, tool, text):
        cls = classify(tool, {"q": text})
        assert cls.tier == REVERSIBLE
        assert cls.requires_approval is False
        assert cls.rule == "reversible_tool"

    def test_a_generic_executor_never_gets_the_exemption(self):
        """The smuggling hole this must not open: for a shell the input IS the command."""
        policy = load_policy(refresh=True)
        policy = dict(policy)
        tools = {k: list(v) for k, v in (policy.get("tools") or {}).items()}
        tools.setdefault(REVERSIBLE, []).append("run_command")   # incoherent claim
        policy["tools"] = tools
        cls = classify("run_command", {"command": "git push --force"}, policy=policy)
        assert cls.tier == IRREVERSIBLE, "a shell listed reversible must still escalate"
        assert cls.requires_approval is True

    def test_an_irreversible_tool_is_unaffected(self):
        cls = classify("git_push", {"note": "read only, honest"})
        assert cls.tier == IRREVERSIBLE
        assert cls.requires_approval is True

    def test_an_unenumerated_tool_still_defaults_to_unknown(self):
        """The exemption is an allowlist, not a fallback — rule 4 is untouched."""
        cls = classify("some_new_tool", {"q": "hello"})
        assert cls.tier == UNKNOWN
        assert cls.requires_approval is True

    def test_a_recoverable_tool_is_still_escalated(self):
        """Only `reversible` is exempt. A tool that mutates can be made worse by input."""
        policy = load_policy(refresh=True)
        rec = (policy.get("tools") or {}).get(RECOVERABLE) or []
        if not rec:
            pytest.skip("no recoverable tools enumerated")
        cls = classify(rec[0], {"command": "git push --force"})
        assert cls.tier == IRREVERSIBLE

    def test_exemption_disappears_with_the_policy(self):
        """It is granted by the operator's file, so an empty policy grants nothing."""
        import tools.agent_runtime.approval_gate as ag

        cls = classify("read_file", {"q": "git push"}, policy=dict(ag._FALLBACK_POLICY))
        assert cls.tier == UNKNOWN
        assert cls.requires_approval is True

# ---------------------------------------------------------------------------
# 2. The gate halts
# ---------------------------------------------------------------------------
class TestGateHalts:
    def test_irreversible_call_is_offered_to_the_approver(self):
        approve = _approver(True, "release approved by change board")
        hook = _gate(approver=approve)
        assert hook("git_push", {"remote": "origin"}) is None
        assert len(approve.seen) == 1
        assert approve.seen[0].classification.tier == IRREVERSIBLE

    def test_denied_irreversible_call_is_blocked_with_a_reason(self):
        hook = _gate(approver=_approver(False, "not during a freeze"))
        msg = hook("run_command", {"command": "git push --force origin main"})
        assert msg is not None
        assert "BLOCKED" in msg
        assert IRREVERSIBLE in msg
        assert "not during a freeze" in msg

    def test_unknown_tool_is_blocked_without_an_approver(self):
        hook = _gate(approver=None)  # -> deny_all is not the default; console is
        # A non-interactive run has no stdin: console_approver denies on EOF.
        msg = hook("frobnicate_the_widget", {"x": 1})
        assert msg is not None and "BLOCKED" in msg

    def test_reversible_call_is_never_offered_to_the_approver(self):
        approve = _approver(False)
        hook = _gate(approver=approve)
        assert hook("read_file", {"path": "a"}) is None
        assert approve.seen == []

    def test_a_broken_approver_denies(self):
        def explode(_request):
            raise RuntimeError("approver is down")

        msg = _gate(approver=explode)("git_push", {})
        assert msg is not None and "BLOCKED" in msg

    def test_approver_may_return_a_bare_bool(self):
        assert _gate(approver=lambda r: True)("git_push", {}) is None
        assert _gate(approver=lambda r: False)("git_push", {}) is not None

    def test_dry_run_allows_but_still_decides(self):
        approve = _approver(False)
        hook = _gate(approver=approve, mode=MODE_DRY_RUN)
        assert hook("git_push", {}) is None
        assert approve.seen == []  # nobody was asked

    def test_off_is_an_explicit_escape_hatch(self):
        assert _gate(mode=MODE_OFF)("git_push", {}) is None

    def test_mode_resolution(self, monkeypatch):
        monkeypatch.delenv("ICDEV_AGENT_APPROVAL_MODE", raising=False)
        assert resolve_mode() == MODE_ENFORCE          # default is enforce
        assert resolve_mode("nonsense") == MODE_ENFORCE  # unparseable is enforce
        monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", "off")
        assert resolve_mode() == MODE_OFF


# ---------------------------------------------------------------------------
# 3. Every decision is recorded, append-only, with actor and reason
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    path = REPO_ROOT / "tools" / "db" / "migrations" / "20260803002224_agent_approval_log" / "up.py"
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module object ``approval_gate`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. ``record_decision`` imports the shim, so
    patching the canonical module (which is what monkeypatch's string form
    resolves to) silently patches nothing and the test asserts its own no-op.
    """
    import sys

    return sys.modules["tools.db.storage"]


class _TranslatingConn:
    """sqlite3 wrapper that applies storage's %s -> ? translation.

    Handing raw sqlite3 a PG-dialect statement makes the test assert its own
    no-op, so the INSERT under test goes through the same translation the
    production connection uses.
    """

    def __init__(self, raw: sqlite3.Connection) -> None:
        self.raw = raw

    def execute(self, sql, params=()):
        from icdev.tools.db.storage import _translate_pg_to_sqlite

        return self.raw.execute(_translate_pg_to_sqlite(sql), params)

    def commit(self):
        self.raw.commit()

    def close(self):
        pass  # the fixture owns the lifetime


@pytest.fixture
def audit_db(monkeypatch, tmp_path):
    """A real table built from the migration's own DDL."""
    raw = sqlite3.connect(str(tmp_path / "approvals.db"))
    raw.executescript(_migration_ddl())
    conn = _TranslatingConn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    # Undo the module-wide stub so the real writer runs. `record_decision` was
    # imported at module scope, before the stub was installed, so this name is
    # the genuine function.
    monkeypatch.setattr(
        "tools.agent_runtime.approval_gate.record_decision", record_decision
    )
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = raw.execute("SELECT * FROM agent_approval_log ORDER BY id")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class TestAudit:
    def test_insert_matches_the_migration_schema(self, audit_db):
        ok = record_decision(
            tool_name="git_push",
            tool_input={"remote": "origin", "branch": "main"},
            classification=classify("git_push", {}),
            decision=ApprovalDecision(True, "hotfix authorised", "alice"),
            mode=MODE_ENFORCE,
            session_id="s-1",
        )
        assert ok is True
        rows = _rows(audit_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["actor"] == "alice"
        assert row["reason"] == "hotfix authorised"
        assert row["decision"] == "approved"
        assert row["tier"] == IRREVERSIBLE
        assert row["tool_name"] == "git_push"
        assert row["session_id"] == "s-1"
        assert row["decided_at"]

    def test_argument_values_are_never_persisted(self, audit_db):
        record_decision(
            tool_name="send_email",
            tool_input={"to": "cui-recipient@agency.test", "body": "SECRET PAYLOAD"},
            classification=classify("send_email", {}),
            decision=ApprovalDecision(False, "no", "bob"),
            mode=MODE_ENFORCE,
        )
        blob = str(_rows(audit_db)[0])
        assert "SECRET PAYLOAD" not in blob
        assert "cui-recipient@agency.test" not in blob
        assert _rows(audit_db)[0]["arg_keys"] == "body,to"   # keys only
        assert len(_rows(audit_db)[0]["input_sha256"]) == 64

    def test_both_approvals_and_denials_are_recorded(self, audit_db):
        gate = build_approval_hook(
            approver=_approver(True, "approved for release"),
            mode=MODE_ENFORCE,
            actor="carol",
            consult_pre_tool_check=False,
        )
        gate("git_push", {"remote": "origin"})
        deny = build_approval_hook(
            approver=_approver(False, "outside the change window"),
            mode=MODE_ENFORCE,
            actor="carol",
            consult_pre_tool_check=False,
        )
        deny("frobnicate_the_widget", {"x": 1})

        rows = _rows(audit_db)
        assert [r["decision"] for r in rows] == ["approved", "denied"]
        assert [r["tier"] for r in rows] == [IRREVERSIBLE, UNKNOWN]
        assert all(r["actor"] == "test-operator" for r in rows)
        assert "outside the change window" in rows[1]["reason"]

    def test_auto_allowed_calls_do_not_fill_the_trail(self, audit_db):
        gate = build_approval_hook(
            approver=_approver(True), mode=MODE_ENFORCE, consult_pre_tool_check=False
        )
        for _ in range(5):
            gate("read_file", {"path": "a"})
        assert _rows(audit_db) == []

    def test_dry_run_and_off_still_record(self, audit_db):
        for mode in (MODE_DRY_RUN, MODE_OFF):
            build_approval_hook(
                approver=_approver(True), mode=mode, consult_pre_tool_check=False
            )("git_push", {})
        rows = _rows(audit_db)
        assert [r["mode"] for r in rows] == [MODE_DRY_RUN, MODE_OFF]
        assert all(r["decision"] == "approved" for r in rows)

    def test_table_is_registered_append_only(self):
        hook = (REPO_ROOT / "tools" / "hooks" / "shared_checks.py").read_text(
            encoding="utf-8"
        )
        assert '"agent_approval_log"' in hook

    def test_falls_back_to_hook_events_when_the_table_is_absent(self, monkeypatch):
        """A DB predating migration 342 must still leave a record."""
        monkeypatch.setattr(
            "tools.agent_runtime.approval_gate.record_decision", record_decision
        )

        def _boom(*a, **k):
            raise RuntimeError("no such table")

        monkeypatch.setattr(_storage_module(), "get_connection", _boom)
        captured: list[tuple] = []
        import tools.airgap.hook_compat  # noqa: F401 — populate sys.modules

        monkeypatch.setattr(
            sys.modules["tools.airgap.hook_compat"],
            "store_event",
            lambda *a, **k: captured.append((a, k)) or 1,
        )
        ok = record_decision(
            tool_name="git_push",
            tool_input={},
            classification=classify("git_push", {}),
            decision=ApprovalDecision(False, "denied", "dave"),
            mode=MODE_ENFORCE,
        )
        assert ok is True
        assert captured and captured[0][0][1] == "agent_approval"

    def test_actor_resolution(self, monkeypatch):
        monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "erin")
        assert resolve_actor() == "erin"
        assert resolve_actor("explicit") == "explicit"
        for key in ("ICDEV_APPROVAL_ACTOR", "ICDEV_USER", "USER", "USERNAME"):
            monkeypatch.delenv(key, raising=False)
        assert resolve_actor() == "unknown"


# ---------------------------------------------------------------------------
# 4. Wiring into the agent loop
# ---------------------------------------------------------------------------
class TestAgentLoopWiring:
    def test_gate_blocks_the_handler_from_ever_running(self):
        """The handler must not execute — the point is the side effect, not the text."""
        ran: list[dict] = []

        def push(inp, stop):
            ran.append(inp)
            return "pushed"

        router = ScriptedRouter([
            [{"id": "c1", "name": "git_push", "input": {"remote": "origin"}}],
            "gave up",
        ])
        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("git_push")],
            tool_handlers={"git_push": push},
            approval_gate=_gate(approver=_approver(False, "denied by policy")),
        )
        assert ran == []
        assert result.final_content == "gave up"

    def test_approved_call_reaches_the_handler(self):
        ran: list[dict] = []
        router = ScriptedRouter([
            [{"id": "c1", "name": "git_push", "input": {"remote": "origin"}}],
            "done",
        ])
        run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("git_push")],
            tool_handlers={"git_push": lambda i, s: ran.append(i) or "pushed"},
            approval_gate=_gate(approver=_approver(True, "ok")),
        )
        assert len(ran) == 1

    def test_gate_wins_over_a_permissive_caller_hook(self):
        hook = _compose_pre_tool_hooks(
            _gate(approver=_approver(False)), lambda n, i: None
        )
        assert hook("git_push", {}) is not None

    def test_caller_hook_still_runs_when_the_gate_allows(self):
        hook = _compose_pre_tool_hooks(
            _gate(approver=_approver(True)), lambda n, i: "caller says no"
        )
        assert hook("git_push", {}) == "caller says no"

    def test_resolution_of_the_approval_gate_argument(self, monkeypatch):
        monkeypatch.delenv("ICDEV_AGENT_APPROVAL_MODE", raising=False)
        assert _resolve_approval_gate(None) is None      # off by default
        assert _resolve_approval_gate(False) is None
        sentinel = lambda n, i: None  # noqa: E731
        assert _resolve_approval_gate(sentinel) is sentinel
        assert callable(_resolve_approval_gate(True))
        monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", "enforce")
        assert callable(_resolve_approval_gate(None))    # env switches it on
        monkeypatch.setenv("ICDEV_AGENT_APPROVAL_MODE", "off")
        assert _resolve_approval_gate(None) is None

    def test_a_gate_that_cannot_be_built_denies_everything(self, monkeypatch):
        """Fail closed: an operator who asked for a gate did not ask for none."""
        import builtins

        real_import = builtins.__import__

        def _fail(name, *args, **kwargs):
            if name == "tools.agent_runtime.approval_gate":
                raise ImportError("gate module is broken")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail)
        hook = _resolve_approval_gate(True)
        monkeypatch.undo()
        assert hook is not None
        assert "BLOCKED" in hook("anything_at_all", {})


# ---------------------------------------------------------------------------
# 5. The pre-existing SAG risk heuristic no longer fails open
# ---------------------------------------------------------------------------
class TestSagRiskHeuristicClosed:
    def test_unenumerated_tool_is_no_longer_low_risk(self):
        from tools.agent_runtime.safety import assess_risk

        # Before ars-appr-01 this returned "low", so `smart` mode auto-approved
        # every tool nobody had enumerated.
        assert assess_risk("frobnicate_the_widget", {"x": 1}) == "high"
        assert assess_risk("read_file", {"path": "a"}) == "low"
        assert assess_risk("git_push", {}) == "high"


def test_classification_is_a_frozen_value():
    cls = classify("git_push", {})
    assert isinstance(cls, Classification)
    with pytest.raises(Exception):
        cls.tier = REVERSIBLE  # type: ignore[misc]
