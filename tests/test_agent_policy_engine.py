# CUI // SP-CTI
"""Policy-function layer over the reversibility gate (exa-policy-01).

Five things have to be true, and each has a class here:

  1. A policy function returns ALLOW / DENY / ASK **plus a reason**.
  2. ``approval_gate.classify`` is one policy in the chain, and its behaviour is
     unchanged — the same tier, rule and requires_approval it always produced.
  3. **A DENY short-circuits.** The policies after it are never called.
  4. Every decision lands in the append-only ``agent_approval_log`` with
     argument KEY NAMES and a digest, and never an argument value.
  5. Broken things fail closed: a raising policy, an unregistered policy name, a
     nonsense return value, an empty chain, a missing config.

The audit tests INSERT against the DDL from the ``agent_approval_log`` migration
itself rather than a hand-written schema, so a column added to one and not the
other fails here instead of at runtime inside a swallowed exception (CLAUDE.md:
"every column in an INSERT must exist in the LIVE schema").
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tools.agent_runtime import policy_engine as pe
from tools.agent_runtime.approval_gate import (
    IRREVERSIBLE,
    MODE_DRY_RUN,
    MODE_ENFORCE,
    MODE_OFF,
    REVERSIBLE,
    UNKNOWN,
    ApprovalDecision,
    classify,
    record_decision,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _policy(effect: str, reason: str = "because", name: str = "p"):
    """A policy that records every event it was asked about."""
    seen: list[pe.PolicyEvent] = []

    def fn(event: pe.PolicyEvent) -> pe.PolicyDecision:
        seen.append(event)
        return pe.PolicyDecision(effect, reason, policy=name)

    fn.seen = seen  # type: ignore[attr-defined]
    return fn


def _event(target: str = "read_file", **kw: Any) -> pe.PolicyEvent:
    kw.setdefault("arguments", {"path": "a"})
    return pe.PolicyEvent(target=target, **kw)


def _config(**kw: Any) -> dict[str, Any]:
    """A config with no chain — tests pass their policies explicitly."""
    base = {
        "version": 1,
        "on_policy_error": pe.DENY,
        "audit": {"log_allow": True},
        "chain": [],
        "floors": {},
    }
    base.update(kw)
    return base


def _approver(approved: bool, reason: str = "because"):
    seen: list[Any] = []

    def approve(request):
        seen.append(request)
        return ApprovalDecision(approved, reason, "test-operator")

    approve.seen = seen  # type: ignore[attr-defined]
    return approve


@pytest.fixture(autouse=True)
def _no_db_writes(monkeypatch):
    """Default: swallow the audit write. The audit tests opt back in."""
    monkeypatch.setattr(
        "tools.agent_runtime.approval_gate.record_decision", lambda **kw: True
    )


def _hook(**kwargs):
    kwargs.setdefault("approver", _approver(False))
    kwargs.setdefault("mode", MODE_ENFORCE)
    kwargs.setdefault("actor", "test-operator")
    kwargs.setdefault("consult_pre_tool_check", False)
    kwargs.setdefault("config", _config())
    return pe.build_policy_hook(**kwargs)


# ---------------------------------------------------------------------------
# 1. The interface
# ---------------------------------------------------------------------------
class TestPolicyInterface:
    @pytest.mark.parametrize("effect", [pe.ALLOW, pe.ASK, pe.DENY])
    def test_a_policy_returns_an_effect_and_a_reason(self, effect):
        result = pe.evaluate(
            _event(), [("p", _policy(effect, "stated reason"))], config=_config()
        )
        assert result.effect == effect
        assert result.reason == "stated reason"
        assert result.policy == "p"

    def test_effects_are_exactly_three(self):
        assert pe.EFFECTS == (pe.ALLOW, pe.ASK, pe.DENY)

    def test_the_event_carries_the_documented_fields(self):
        seen: list[pe.PolicyEvent] = []
        pe.evaluate(
            pe.PolicyEvent(
                event_type="tool_call",
                target="git_push",
                arguments={"remote": "origin"},
                actor="alice",
                usage={"tokens": 10},
                session_state={"calls": 3},
                session_id="s-1",
            ),
            [("p", lambda e: seen.append(e) or pe.PolicyDecision(pe.ALLOW, "ok"))],
            config=_config(),
        )
        event = seen[0]
        assert event.event_type == "tool_call"
        assert event.target == "git_push"
        assert event.arguments == {"remote": "origin"}
        assert event.actor == "alice"
        assert event.usage == {"tokens": 10}
        assert event.session_state == {"calls": 3}

    def test_state_and_usage_are_passed_by_reference_to_every_policy(self):
        """A policy must be able to see counters a caller keeps across a session."""
        state = {"calls": 0}
        seen: list[int] = []

        def counting(event: pe.PolicyEvent) -> pe.PolicyDecision:
            seen.append(event.session_state["calls"])
            return pe.PolicyDecision(pe.ALLOW, "ok")

        hook = _hook(policies=[("counting", counting)], session_state=state)
        hook("read_file", {"path": "a"})
        state["calls"] = 7
        hook("read_file", {"path": "b"})
        assert seen == [0, 7]

    def test_abstaining_is_not_authorising(self):
        """None means 'no opinion' — it lets the next policy speak, nothing more."""
        result = pe.evaluate(
            _event(),
            [("quiet", lambda e: None), ("loud", _policy(pe.DENY, "no"))],
            config=_config(),
        )
        assert result.effect == pe.DENY

    def test_strictest_wins_when_nobody_denies(self):
        result = pe.evaluate(
            _event(),
            [
                ("a", _policy(pe.ALLOW, "fine", name="a")),
                ("b", _policy(pe.ASK, "check with a human", name="b")),
                ("c", _policy(pe.ALLOW, "also fine", name="c")),
            ],
            config=_config(),
        )
        assert result.effect == pe.ASK
        assert result.policy == "b"
        assert len(result.decisions) == 3   # ask does NOT short-circuit

    def test_an_event_repr_never_shows_argument_values(self):
        """A traceback or a debug log must not be the thing that leaks CUI."""
        event = pe.PolicyEvent(
            target="send_email",
            arguments={"to": "cui-recipient@agency.test", "body": "SECRET PAYLOAD"},
        )
        text = repr(event)
        assert "SECRET PAYLOAD" not in text
        assert "cui-recipient@agency.test" not in text
        assert "body" in text and "to" in text     # key names are fine
        assert event.argument_keys() == ["body", "to"]


# ---------------------------------------------------------------------------
# 2. classify() is one policy among several, unchanged
# ---------------------------------------------------------------------------
class TestReversibilityIsOnePolicy:
    def test_it_is_registered_under_its_name(self):
        assert "reversibility" in pe.list_policies()
        assert pe.get_policy("reversibility") is pe.reversibility_policy

    @pytest.mark.parametrize(
        "target,arguments,expected",
        [
            ("read_file", {"path": "a"}, pe.ALLOW),
            ("write_file", {"path": "a"}, pe.ALLOW),
            ("git_push", {"remote": "origin"}, pe.ASK),
            ("frobnicate_the_widget", {"x": 1}, pe.ASK),
            ("run_command", {"command": "git push --force"}, pe.ASK),
            ("run_command", {"command": "git add ."}, pe.ALLOW),
        ],
    )
    def test_the_verdict_is_the_gate_verdict(self, target, arguments, expected):
        decision = pe.reversibility_policy(
            pe.PolicyEvent(target=target, arguments=arguments)
        )
        assert decision.effect == expected
        # ...and it is exactly what classify() said, not a re-derivation.
        cls = classify(target, arguments)
        assert decision.tier == cls.tier
        assert decision.rule == cls.rule
        assert decision.detail == cls.detail
        assert (decision.effect == pe.ASK) is cls.requires_approval

    def test_it_carries_the_tier_onto_the_chain_result(self):
        result = pe.evaluate(
            _event("git_push", arguments={"remote": "origin"}),
            [("reversibility", pe.reversibility_policy)],
            config=_config(),
        )
        assert result.tier() == IRREVERSIBLE
        assert result.effect == pe.ASK

    def test_it_never_denies(self):
        """The gate's strongest verdict is 'ask'. This layer does not tighten it."""
        for target in ("git_push", "terraform_apply", "frobnicate", "delete_video"):
            decision = pe.reversibility_policy(pe.PolicyEvent(target=target))
            assert decision.effect in (pe.ALLOW, pe.ASK)

    def test_the_reversible_exemption_survives_the_wrapping(self):
        """read_file('...git push...') must still not halt (the gate's rule 0)."""
        decision = pe.reversibility_policy(
            pe.PolicyEvent(target="read_file", arguments={"q": "how do I git push"})
        )
        assert decision.effect == pe.ALLOW
        assert decision.tier == REVERSIBLE
        assert decision.rule == "reversible_tool"

    def test_the_shipped_config_chains_it(self):
        """It is chained, and chained FIRST.

        This asserted the shipped chain was exactly ``["reversibility"]`` until
        exa-policy-03 added three builtins to it. The claim worth keeping is not
        that it is alone — it was never going to stay alone, that is what a
        chain is for — but that it is still there and still runs before the
        policies layered on top of it, so its verdict is established before
        anything else has an opinion. The builtins have their own coverage in
        test_agent_policy_builtins.py.
        """
        config = pe.load_config(refresh=True)
        names = [name for name, _ in pe.resolve_chain(config)]
        assert names[0] == "reversibility"


# ---------------------------------------------------------------------------
# 3. DENY short-circuits
# ---------------------------------------------------------------------------
class TestDenyShortCircuits:
    def test_policies_after_a_deny_are_never_called(self):
        after = _policy(pe.ALLOW, "should never be asked", name="after")
        result = pe.evaluate(
            _event(),
            [
                ("before", _policy(pe.ALLOW, "ok", name="before")),
                ("blocker", _policy(pe.DENY, "protected branch", name="blocker")),
                ("after", after),
            ],
            config=_config(),
        )
        assert result.effect == pe.DENY
        assert result.reason == "protected branch"
        assert result.policy == "blocker"
        assert result.short_circuited is True
        assert after.seen == [], "a policy after a DENY was consulted"
        assert len(result.decisions) == 2

    def test_a_deny_beats_an_earlier_ask(self):
        result = pe.evaluate(
            _event(),
            [("a", _policy(pe.ASK, "maybe", name="a")),
             ("b", _policy(pe.DENY, "never", name="b"))],
            config=_config(),
        )
        assert result.effect == pe.DENY
        assert result.policy == "b"

    def test_a_denied_call_is_never_offered_to_the_approver(self):
        """The point of DENY: some calls should not be answerable at 3am."""
        approve = _approver(True, "sure, why not")
        hook = _hook(
            approver=approve,
            policies=[("blocker", _policy(pe.DENY, "protected branch"))],
        )
        message = hook("git_push", {"remote": "origin"})
        assert message is not None
        assert "BLOCKED" in message and "protected branch" in message
        assert approve.seen == [], "a DENY was escalated to a human"

    def test_an_allowed_call_passes_through(self):
        hook = _hook(policies=[("ok", _policy(pe.ALLOW, "fine"))])
        assert hook("read_file", {"path": "a"}) is None

    def test_an_ask_reaches_the_approver(self):
        approve = _approver(True, "authorised by the change board")
        hook = _hook(approver=approve, policies=[("q", _policy(pe.ASK, "confirm"))])
        assert hook("git_push", {"remote": "origin"}) is None
        assert len(approve.seen) == 1

    def test_a_denied_ask_is_blocked_with_the_human_reason(self):
        hook = _hook(
            approver=_approver(False, "not during a freeze"),
            policies=[("q", _policy(pe.ASK, "confirm"))],
        )
        message = hook("git_push", {})
        assert message is not None and "not during a freeze" in message

    def test_dry_run_and_off_apply_to_ask_but_not_to_deny(self):
        """An escape hatch for an escalation is not an escape hatch for a refusal."""
        for mode in (MODE_DRY_RUN, MODE_OFF):
            ask = _hook(mode=mode, policies=[("q", _policy(pe.ASK, "confirm"))])
            assert ask("git_push", {}) is None, mode
            deny = _hook(mode=mode, policies=[("b", _policy(pe.DENY, "never"))])
            assert deny("git_push", {}) is not None, mode


# ---------------------------------------------------------------------------
# 4. Fail closed
# ---------------------------------------------------------------------------
class TestFailsClosed:
    def test_a_raising_policy_denies_by_default(self):
        def explode(_event):
            raise RuntimeError("policy is down")

        result = pe.evaluate(_event(), [("boom", explode)], config=_config())
        assert result.effect == pe.DENY
        assert "policy is down" in result.reason

    def test_on_policy_error_may_be_softened_to_ask_but_not_to_allow(self):
        def explode(_event):
            raise RuntimeError("down")

        asked = pe.evaluate(
            _event(), [("boom", explode)], config=_config(on_policy_error="ask")
        )
        assert asked.effect == pe.ASK
        # `allow` is not an accepted value — a config typo cannot authorise.
        forced = pe.evaluate(
            _event(), [("boom", explode)], config=_config(on_policy_error="allow")
        )
        assert forced.effect == pe.DENY

    def test_a_nonsense_return_value_denies(self):
        for bad in (42, object(), ["allow"], pe.PolicyDecision("maybe", "?")):
            result = pe.evaluate(
                _event(), [("weird", lambda e, b=bad: b)], config=_config()
            )
            assert result.effect == pe.DENY, bad

    def test_an_empty_chain_authorises_nothing(self):
        result = pe.evaluate(_event(), [], config=_config())
        assert result.effect == pe.ASK
        assert result.decisions[0].rule == "empty_chain"

    def test_a_policy_named_in_config_but_not_registered_denies_loudly(self):
        """It must NOT be silently skipped — that is the bug this card is about."""
        config = _config(chain=[{"name": "nope_not_registered", "enabled": True}])
        chain = pe.resolve_chain(config)
        assert len(chain) == 1
        result = pe.evaluate(_event(), chain, config=config)
        assert result.effect == pe.DENY
        assert "not registered" in result.reason

    def test_a_disabled_policy_is_dropped_from_the_chain(self):
        config = _config(chain=[{"name": "reversibility", "enabled": False}])
        assert pe.resolve_chain(config) == []

    def test_a_missing_config_falls_back_to_the_reversibility_chain(self, monkeypatch):
        monkeypatch.setattr(pe, "_find_config_path", lambda: None)
        monkeypatch.setattr(pe, "_CONFIG_CACHE", None)
        config = pe.load_config(refresh=True)
        assert [name for name, _ in pe.resolve_chain(config)] == ["reversibility"]
        assert pe._on_policy_error(config) == pe.DENY
        # ...and that fallback chain is itself fail-closed.
        result = pe.evaluate(_event("frobnicate"), config=config)
        assert result.effect == pe.ASK

    def test_a_floor_can_only_raise_the_answer(self):
        config = _config(floors={"tool_call": pe.ASK})
        raised = pe.evaluate(
            _event(), [("a", _policy(pe.ALLOW, "fine"))], config=config
        )
        assert raised.effect == pe.ASK
        assert raised.floor_applied == pe.ASK
        # A floor cannot lower a DENY.
        lowered = pe.evaluate(
            _event(),
            [("a", _policy(pe.DENY, "never"))],
            config=_config(floors={"tool_call": pe.ALLOW}),
        )
        assert lowered.effect == pe.DENY

    def test_an_unparseable_floor_is_no_floor_not_a_crash(self):
        config = _config(floors={"tool_call": "sometimes"})
        assert pe.evaluate(
            _event(), [("a", _policy(pe.ALLOW, "fine"))], config=config
        ).effect == pe.ALLOW

    def test_a_broken_approver_denies(self):
        def explode(_request):
            raise RuntimeError("approver is down")

        hook = _hook(approver=explode, policies=[("q", _policy(pe.ASK, "confirm"))])
        assert hook("git_push", {}) is not None

    def test_a_hard_block_wins_before_any_policy(self, monkeypatch):
        monkeypatch.setattr(
            "tools.agent_runtime.approval_gate._hard_block",
            lambda t, i: (True, "pre_tool_use refuses this"),
        )
        approve = _approver(True)
        hook = _hook(
            approver=approve,
            policies=[("permissive", _policy(pe.ALLOW, "looks fine to me"))],
            consult_pre_tool_check=True,
        )
        message = hook("run_command", {"command": "rm -rf /"})
        assert message is not None and "pre_tool_use refuses this" in message
        assert approve.seen == []

    def test_registering_over_an_existing_policy_needs_replace(self):
        pe.register_policy("tmp_test_policy", _policy(pe.ALLOW))
        try:
            with pytest.raises(ValueError):
                pe.register_policy("tmp_test_policy", _policy(pe.DENY))
            pe.register_policy("tmp_test_policy", _policy(pe.DENY), replace=True)
        finally:
            pe._REGISTRY.pop("tmp_test_policy", None)

    def test_strictest_treats_an_unknown_effect_as_deny(self):
        assert pe.strictest(pe.ALLOW, "banana") == pe.DENY
        assert pe.strictest(pe.ALLOW, pe.ASK) == pe.ASK
        assert pe.strictest() == pe.ALLOW


# ---------------------------------------------------------------------------
# 5. Every decision is recorded, append-only, without argument values
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_policy_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module ``record_decision`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. Patching the wrong one asserts a no-op.
    """
    import sys

    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """Wrap ``raw`` in a %s -> ? translating connection, as production has.

    The INSERT under test is authored for PostgreSQL. Handing production code a
    bare ``sqlite3`` connection makes it raise ``near "%": syntax error`` inside
    ``record_decision``'s ``except``, and the test then asserts against a no-op
    it caused itself. ``tests/_sql_compat`` delegates to the same
    ``translate_sql`` the runtime uses, so this fixture cannot drift from it.
    """
    from tests._sql_compat import translating

    conn = translating(raw)
    conn.close = lambda: None  # the fixture owns the lifetime
    return conn


@pytest.fixture
def audit_db(monkeypatch, tmp_path):
    """A real table built from the migration's own DDL.

    ``record_decision`` is bound at module scope, before the ``_no_db_writes``
    stub is installed, so the name below is the genuine function rather than
    the stub reinstalling itself.
    """
    raw = sqlite3.connect(str(tmp_path / "approvals.db"))
    raw.executescript(_migration_ddl())
    storage = _storage_module()
    monkeypatch.setattr(
        storage, "get_connection", lambda *a, **k: _translating_conn(raw)
    )
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
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
    def test_a_deny_is_recorded_with_its_reason(self, audit_db):
        hook = _hook(policies=[("blocker", _policy(pe.DENY, "protected branch"))])
        hook("git_push", {"remote": "origin", "branch": "main"})
        rows = _rows(audit_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["decision"] == "denied"
        assert "protected branch" in row["reason"]
        assert row["actor"] == "test-operator"
        assert row["tool_name"] == "git_push"
        assert row["rule"].startswith("policy_chain:")
        assert row["decided_at"]

    def test_argument_values_are_never_persisted(self, audit_db):
        hook = _hook(policies=[("blocker", _policy(pe.DENY, "no egress"))])
        hook("send_email", {"to": "cui-recipient@agency.test", "body": "SECRET PAYLOAD"})
        row = _rows(audit_db)[0]
        blob = str(row)
        assert "SECRET PAYLOAD" not in blob
        assert "cui-recipient@agency.test" not in blob
        assert row["arg_keys"] == "body,to"          # key names only
        assert len(row["input_sha256"]) == 64        # ...plus a digest

    def test_a_policy_reason_is_the_only_free_text_that_reaches_the_row(self, audit_db):
        """The detail column is composed from policy summaries, not from input."""
        hook = _hook(
            policies=[("blocker", _policy(pe.DENY, "quota exhausted", name="quota"))]
        )
        hook("upload_file", {"path": "/secret/thing.bin", "token": "hunter2"})
        row = _rows(audit_db)[0]
        assert "hunter2" not in str(row)
        assert "/secret/thing.bin" not in str(row)
        assert "quota exhausted" in row["detail"]

    def test_allow_ask_and_deny_all_land(self, audit_db):
        _hook(policies=[("a", _policy(pe.ALLOW, "fine"))])("read_file", {"path": "a"})
        _hook(
            approver=_approver(True, "authorised"),
            policies=[("q", _policy(pe.ASK, "confirm"))],
        )("git_push", {})
        _hook(policies=[("b", _policy(pe.DENY, "never"))])("terraform_apply", {})

        rows = _rows(audit_db)
        assert [r["decision"] for r in rows] == ["approved", "approved", "denied"]
        assert [r["tool_name"] for r in rows] == [
            "read_file", "git_push", "terraform_apply"
        ]
        assert "allow" in rows[0]["rule"]
        assert "ask" in rows[1]["rule"]
        assert "deny" in rows[2]["rule"]

    def test_log_allow_false_still_records_ask_and_deny(self, audit_db):
        config = _config(audit={"log_allow": False})
        _hook(config=config, policies=[("a", _policy(pe.ALLOW, "fine"))])(
            "read_file", {"path": "a"}
        )
        assert _rows(audit_db) == []
        _hook(config=config, policies=[("b", _policy(pe.DENY, "never"))])(
            "git_push", {}
        )
        assert [r["decision"] for r in _rows(audit_db)] == ["denied"]

    def test_the_reversibility_tier_reaches_the_row(self, audit_db):
        _hook(policies=[("reversibility", pe.reversibility_policy)])("git_push", {})
        row = _rows(audit_db)[0]
        assert row["tier"] == IRREVERSIBLE

    def test_a_row_with_no_tier_falls_back_to_unknown_not_to_an_effect(self, audit_db):
        """`tier` keeps the reversibility vocabulary; effects live in `rule`."""
        _hook(policies=[("b", _policy(pe.DENY, "never"))])("whatever", {})
        row = _rows(audit_db)[0]
        assert row["tier"] == UNKNOWN

    def test_the_table_is_registered_append_only(self):
        hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(
            encoding="utf-8"
        )
        assert '"agent_approval_log"' in hook


# ---------------------------------------------------------------------------
# 6. The existing gate is untouched
# ---------------------------------------------------------------------------
def test_the_reversibility_gate_is_not_modified_by_importing_this_module():
    """This layer sits ABOVE the gate; it must not mutate its policy or cache."""
    from tools.agent_runtime import approval_gate

    policy = approval_gate.load_policy(refresh=True)
    assert policy["default_tier"] == UNKNOWN
    assert UNKNOWN in policy["require_approval_tiers"]
    assert classify("git_push", {}).requires_approval is True
    assert classify("read_file", {"path": "a"}).requires_approval is False


def test_the_chain_result_is_a_frozen_value():
    result = pe.evaluate(_event(), [("a", _policy(pe.ALLOW))], config=_config())
    assert isinstance(result, pe.ChainResult)
    with pytest.raises(Exception):
        result.effect = pe.DENY  # type: ignore[misc]
