# CUI // SP-CTI
"""The three builtin policies (exa-policy-03).

The acceptance criterion for this card names the thing these tests exist to
prove, so it is worth restating: *tests assert the DENY case for each, not just
the allow case*. A policy tested only on the call it permits is a policy whose
enforcement is untested, and enforcement is the entire feature. Every one of the
three therefore has an explicit refusal test, and the git allowlist has one per
way a branch can fail to be allowed.

Four properties are pinned here, one per section:

  1. **Each policy denies when it should** — the cap at its limit, the allowlist
     on a protected branch, the risk score at its ceiling.
  2. **Nothing is hardcoded.** Every threshold is required from config with no
     Python fallback, so omitting one is a DENY naming the error and NOT a
     default limit nobody chose. This is asserted per threshold, because a
     single default slipping back in is invisible until the day it matters.
  3. **Each is independently switchable off**, without disturbing the other two.
  4. **The counters actually accrue.** A cap whose counter never increments
     reports itself enabled and enforces nothing — the defect this whole card
     exists to stop — so the accrual is driven through the real composition
     rather than by hand-feeding a state dict.

State is exercised with ``persist=False`` so the tests never touch a database;
what is under test is the accrual, not the storage, and the storage has its own
tests in ``test_agent_policy_composition.py``.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from tools.agent_runtime import policy_builtins as pb
from tools.agent_runtime import policy_composition as pc
from tools.agent_runtime import policy_engine as pe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _event(target: str = "run_command", session_id: str = "s1", **kw: Any) -> pe.PolicyEvent:
    kw.setdefault("arguments", {})
    return pe.PolicyEvent(target=target, session_id=session_id, **kw)


def _server(*entries: dict[str, Any]) -> dict[str, Any]:
    """A server-level config holding exactly the given chain entries."""
    return {"chain": list(entries), "floors": {}, "on_policy_error": "deny"}


def _compose(
    config: dict[str, Any],
    event: pe.PolicyEvent,
    state: Optional[pc.SessionState] = None,
) -> pc.Composition:
    """Run one event through a real composition with only ``config`` at server."""
    return pc.evaluate_composed(
        event,
        session={},
        agent={},
        server=config,
        state=state if state is not None else pc.SessionState(
            event.session_id, persist=False
        ),
    )


@pytest.fixture(autouse=True)
def _clear_liveness():
    pb.reset_liveness_tracking()
    yield
    pb.reset_liveness_tracking()


# ===========================================================================
# 1. max_tool_calls_per_session
# ===========================================================================
class TestMaxToolCallsPerSession:
    def test_allows_under_the_cap_and_reports_the_count(self):
        policy = pb.max_tool_calls_policy({"limit": 3})
        decision = policy(_event(session_state={"tool_calls": 1}))
        assert decision.effect == pe.ALLOW
        assert "2 of 3" in decision.reason

    def test_DENIES_at_the_cap(self):
        """The deny case. At the limit, not one call past it."""
        policy = pb.max_tool_calls_policy({"limit": 3})
        decision = policy(_event(session_state={"tool_calls": 3}))
        assert decision.effect == pe.DENY
        assert decision.rule == "max_tool_calls_per_session"
        assert "3 tool calls" in decision.reason and "cap is 3" in decision.reason

    def test_denies_beyond_the_cap_too(self):
        policy = pb.max_tool_calls_policy({"limit": 3})
        assert policy(_event(session_state={"tool_calls": 99})).effect == pe.DENY

    def test_a_denied_call_does_not_increment(self):
        """A refused call never runs, so charging the session for it is wrong."""
        policy = pb.max_tool_calls_policy({"limit": 2})
        assert policy(_event(session_state={"tool_calls": 2})).state_updates == ()

    def test_on_exceed_can_be_configured_to_ask(self):
        policy = pb.max_tool_calls_policy({"limit": 1, "on_exceed": "ask"})
        assert policy(_event(session_state={"tool_calls": 1})).effect == pe.ASK

    def test_on_exceed_allow_is_refused(self):
        """A cap configured to allow when it fires is not a cap."""
        with pytest.raises(pb.PolicyConfigError, match="not a policy"):
            pb.max_tool_calls_policy({"limit": 1, "on_exceed": "allow"})

    def test_the_counter_actually_accrues_through_a_real_composition(self):
        """The property that makes the cap real rather than declared.

        Driven through :func:`policy_composition.compose` — the runtime path —
        rather than by hand-feeding ``session_state``, because the failure this
        guards against is precisely that the increment is never applied.
        """
        config = _server({"name": "max_tool_calls_per_session", "params": {"limit": 3}})
        state = pc.SessionState("s-accrue", persist=False)

        effects = [
            _compose(config, _event(session_id="s-accrue"), state).effect
            for _ in range(4)
        ]

        assert effects == [pe.ALLOW, pe.ALLOW, pe.ALLOW, pe.DENY]
        assert state.get("tool_calls") == 3  # the denied 4th did not count

    def test_state_key_scopes_two_instances_independently(self):
        config = _server(
            {
                "name": "max_tool_calls_per_session",
                "params": {"limit": 1, "state_key": "writes"},
            },
        )
        state = pc.SessionState("s-keys", persist=False)
        _compose(config, _event(session_id="s-keys"), state)
        assert state.get("writes") == 1
        assert state.get("tool_calls") is None

    def test_no_session_id_denies_rather_than_silently_not_counting(self):
        """Without a session there is nothing to count, so the cap cannot hold."""
        policy = pb.max_tool_calls_policy({"limit": 5})
        decision = policy(_event(session_id=""))
        assert decision.effect == pe.DENY
        assert decision.rule == "no_session_id"

    def test_require_session_false_is_an_explicit_acknowledged_gap(self):
        policy = pb.max_tool_calls_policy({"limit": 5, "require_session": False})
        decision = policy(_event(session_id=""))
        assert decision.effect == pe.ALLOW
        assert decision.rule == "no_session_id"

    def test_event_types_scopes_the_instance(self):
        policy = pb.max_tool_calls_policy({"limit": 1, "event_types": ["other"]})
        assert policy(_event(session_state={"tool_calls": 99})) is None


# ===========================================================================
# 2. git_write_allowlist
# ===========================================================================
_ALLOWLIST = {
    "repos": ["*"],
    "deny_branches": ["main", "release/*"],
    "allow_branches": ["feat/*", "kanban/*"],
}


def _git(params: Optional[dict[str, Any]] = None):
    return pb.git_write_allowlist_policy({**_ALLOWLIST, **(params or {})})


class TestGitWriteAllowlist:
    def test_DENIES_a_push_to_main(self):
        """The deny case, and the rule the regex tiers structurally cannot express."""
        decision = _git()(_event(arguments={"command": "git push origin main"}))
        assert decision.effect == pe.DENY
        assert decision.rule == "denied_branch"

    def test_allows_a_push_to_an_allowlisted_branch(self):
        decision = _git()(_event(arguments={"command": "git push origin feat/x"}))
        assert decision.effect == pe.ALLOW
        assert decision.rule == "allowed_branch"

    def test_DENIES_a_branch_that_is_on_neither_list(self):
        """Allowlist semantics: matching nothing is not the same as being fine."""
        decision = _git()(_event(arguments={"command": "git push origin wip/x"}))
        assert decision.effect == pe.DENY
        assert decision.rule == "not_allowlisted"

    def test_deny_matching_is_case_insensitive(self):
        """A deny list that misses `Main` is a deny list with a hole in it."""
        decision = _git()(_event(arguments={"command": "git push origin Main"}))
        assert decision.effect == pe.DENY

    def test_allow_matching_is_case_sensitive(self):
        """And an allow list that accepts `Feat/x` for `feat/*` has one too."""
        decision = _git()(_event(arguments={"command": "git push origin Feat/x"}))
        assert decision.effect == pe.DENY
        assert decision.rule == "not_allowlisted"

    def test_a_bare_push_asks_because_the_branch_is_not_stated(self):
        """Unknowable is not the same as forbidden — but it is never `allow`."""
        decision = _git()(_event(arguments={"command": "git push"}))
        assert decision.effect == pe.ASK
        assert decision.rule == "unknown_branch"

    def test_on_unknown_can_be_configured_to_deny(self):
        decision = _git({"on_unknown": "deny"})(
            _event(arguments={"command": "git push"})
        )
        assert decision.effect == pe.DENY

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin HEAD:main",
            "git push --force origin main",
            "git push origin +main",
            "git push origin refs/heads/main",
            "git push --delete origin main",
            "git push -o ci.skip origin main",
            "git -C /some/worktree push origin main",
        ],
    )
    def test_denies_every_spelling_of_a_push_to_main(self, command: str):
        """The destination is what is written, however the refspec is spelled."""
        assert _git()(_event(arguments={"command": command})).effect == pe.DENY

    def test_a_chained_command_is_judged_on_its_worst_segment(self):
        """`git push feat/x && git push main` writes main; reading only the
        first segment would miss it."""
        command = "git push origin feat/x && git push origin main"
        assert _git()(_event(arguments={"command": command})).effect == pe.DENY

    def test_an_explicit_branch_argument_is_honoured(self):
        """A structured tool that names its branch needs no command parsing."""
        decision = _git()(_event(target="git_push", arguments={"branch": "main"}))
        assert decision.effect == pe.DENY

    def test_abstains_on_a_call_with_no_git_write(self):
        """It has no opinion about `read_file`, and an abstention is not an
        authorisation — the rest of the chain still speaks."""
        assert _git()(_event(target="read_file", arguments={"path": "a"})) is None

    def test_abstains_on_a_git_command_that_is_not_a_governed_operation(self):
        assert _git()(_event(arguments={"command": "git status"})) is None

    def test_repo_scoping_leaves_other_repos_alone(self):
        policy = _git({"repos": ["icdev-ai/*"]})
        decision = policy(
            _event(arguments={"command": "git push origin main", "repo": "other/thing"})
        )
        assert decision.effect == pe.ALLOW
        assert decision.rule == "out_of_scope"

    def test_repo_scoping_applies_within_the_named_repo(self):
        policy = _git({"repos": ["icdev-ai/*"]})
        decision = policy(
            _event(arguments={"command": "git push origin main", "repo": "icdev-ai/icdev"})
        )
        assert decision.effect == pe.DENY

    def test_a_scoped_instance_asks_when_it_cannot_tell_the_repo(self):
        policy = _git({"repos": ["icdev-ai/*"]})
        decision = policy(_event(arguments={"command": "git push origin main"}))
        assert decision.effect == pe.ASK
        assert decision.rule == "unknown_repo"

    def test_an_instance_governing_nothing_is_a_config_error(self):
        with pytest.raises(pb.PolicyConfigError, match="at least one list"):
            pb.git_write_allowlist_policy({"repos": ["*"]})

    def test_empty_operations_is_a_config_error(self):
        with pytest.raises(pb.PolicyConfigError, match="governs nothing"):
            pb.git_write_allowlist_policy({**_ALLOWLIST, "operations": []})

    def test_two_instances_are_configured_not_copied(self):
        """The factory_params claim: one policy, two differently-scoped rules."""
        strict = _git({"deny_branches": ["main"], "allow_branches": ["feat/*"]})
        loose = _git({"deny_branches": ["main"], "allow_branches": ["*"]})
        call = _event(arguments={"command": "git push origin wip/x"})
        assert strict(call).effect == pe.DENY
        assert loose(call).effect == pe.ALLOW


# ===========================================================================
# 3. risk_score
# ===========================================================================
_RISK = {
    "ask_at": 10,
    "deny_at": 20,
    "weights": {"dangerous_tool": 7},
    "default_weight": 1,
}


class TestRiskScore:
    def test_below_the_ask_threshold_it_allows(self):
        decision = pb.risk_score_policy(_RISK)(_event(session_state={"risk_score": 0}))
        assert decision.effect == pe.ALLOW
        assert decision.rule == "under_threshold"

    def test_escalates_to_ask_once_the_score_crosses(self):
        """The point of the policy: a call that is individually benign is
        escalated because of what came before it."""
        decision = pb.risk_score_policy(_RISK)(_event(session_state={"risk_score": 9}))
        assert decision.effect == pe.ASK
        assert decision.rule == "risk_ask_threshold"
        assert "worth 1" in decision.reason

    def test_DENIES_at_the_ceiling(self):
        """The deny case."""
        decision = pb.risk_score_policy(_RISK)(_event(session_state={"risk_score": 19}))
        assert decision.effect == pe.DENY
        assert decision.rule == "risk_deny_threshold"

    def test_a_denied_call_does_not_accrue(self):
        decision = pb.risk_score_policy(_RISK)(_event(session_state={"risk_score": 99}))
        assert decision.state_updates == ()

    def test_the_proposed_call_counts_toward_its_own_decision(self):
        """9 + 7 crosses 10 but not 20, so the heavy call asks on its own."""
        policy = pb.risk_score_policy(_RISK)
        decision = policy(
            _event(target="dangerous_tool", session_state={"risk_score": 4})
        )
        assert decision.effect == pe.ASK

    def test_per_tool_weight_beats_the_tier_weight(self):
        policy = pb.risk_score_policy(
            {**_RISK, "tier_weights": {"unknown": 1}}
        )
        decision = policy(_event(target="dangerous_tool", session_state={}))
        assert "weights[dangerous_tool]" in decision.detail

    def test_tier_weights_reuse_the_reversibility_tiers(self):
        """Weighting by tier is why the dangerous-tool list is not duplicated
        here — an unrecognised tool is `unknown`, which is not cheap."""
        policy = pb.risk_score_policy(
            {"ask_at": 100, "deny_at": 200, "tier_weights": {"unknown": 9}}
        )
        decision = policy(_event(target="some_tool_nobody_declared", session_state={}))
        assert "tier_weights[unknown]" in decision.detail

    def test_a_long_chain_of_benign_calls_reaches_the_ceiling(self):
        """The whole thesis, driven through the real composition: individually
        benign calls are not collectively benign."""
        config = _server(
            {
                "name": "risk_score",
                "params": {"ask_at": 3, "deny_at": 5, "default_weight": 1,
                           "tier_weights": {}},
            }
        )
        state = pc.SessionState("s-risk", persist=False)
        effects = [
            _compose(config, _event(target="read_file", session_id="s-risk"), state).effect
            for _ in range(6)
        ]
        assert effects == [
            pe.ALLOW, pe.ALLOW, pe.ASK, pe.ASK, pe.DENY, pe.DENY
        ]
        assert state.get("risk_score") == 4  # the denied calls did not accrue

    def test_deny_below_ask_is_a_config_error(self):
        with pytest.raises(pb.PolicyConfigError, match="swapped"):
            pb.risk_score_policy({"ask_at": 50, "deny_at": 10})

    def test_an_unknown_tier_is_a_config_error(self):
        """A tier_weights key that matches no tier would never be consulted."""
        with pytest.raises(pb.PolicyConfigError, match="tiers that do not exist"):
            pb.risk_score_policy({**_RISK, "tier_weights": {"catastrophic": 9}})

    def test_no_session_id_denies(self):
        decision = pb.risk_score_policy(_RISK)(_event(session_id=""))
        assert decision.effect == pe.DENY


# ===========================================================================
# 4. No threshold has a Python default
# ===========================================================================
class TestNothingIsHardcoded:
    @pytest.mark.parametrize(
        "factory, params, missing",
        [
            (pb.max_tool_calls_policy, {}, "limit"),
            (pb.risk_score_policy, {"deny_at": 5}, "ask_at"),
            (pb.risk_score_policy, {"ask_at": 5}, "deny_at"),
        ],
    )
    def test_a_missing_threshold_is_an_error_not_a_default(
        self, factory, params: dict[str, Any], missing: str
    ):
        """A default limit in Python is how a configured limit becomes a number
        nobody chose: the YAML reads authoritative and the value in force came
        from a source file."""
        with pytest.raises(pb.PolicyConfigError) as exc:
            factory(params)
        assert missing in str(exc.value)
        assert "no default" in str(exc.value)

    @pytest.mark.parametrize("bad", ["ten", None, True, [5]])
    def test_a_non_numeric_threshold_is_an_error(self, bad: Any):
        with pytest.raises(pb.PolicyConfigError):
            pb.max_tool_calls_policy({"limit": bad})

    def test_a_typo_in_a_param_name_is_an_error_not_an_ignored_line(self):
        """`dey_branches` silently ignored is a rule the operator believes is in
        force and which is not."""
        with pytest.raises(pb.PolicyConfigError, match="unknown params"):
            pb.git_write_allowlist_policy({**_ALLOWLIST, "dey_branches": ["main"]})

    def test_a_misconfigured_instance_denies_rather_than_disappearing(self):
        """Resolved through the engine: the entry becomes a DENY that names the
        error, not a dropped line and not a fallback limit."""
        chain = pe.resolve_chain(_server({"name": "max_tool_calls_per_session"}))
        assert len(chain) == 1
        decision = chain[0][1](_event())
        assert decision.effect == pe.DENY
        assert decision.rule == "policy_config_error"
        assert "limit" in decision.reason

    def test_params_on_a_policy_that_cannot_take_them_denies(self):
        """Accepted and ignored is the same failure wearing a different hat."""
        chain = pe.resolve_chain(
            _server({"name": "reversibility", "params": {"limit": 5}})
        )
        decision = chain[0][1](_event())
        assert decision.effect == pe.DENY
        assert decision.rule == "policy_config_error"

    def test_params_of_the_wrong_shape_denies(self):
        chain = pe.resolve_chain(
            _server({"name": "risk_score", "params": ["not", "a", "mapping"]})
        )
        assert chain[0][1](_event()).effect == pe.DENY


# ===========================================================================
# 5. Each is independently switchable off
# ===========================================================================
class TestIndependentlyDisableable:
    def _full_chain(self, **disabled: bool) -> dict[str, Any]:
        entries = [
            {"name": "max_tool_calls_per_session", "params": {"limit": 500}},
            {"name": "git_write_allowlist", "params": dict(_ALLOWLIST)},
            {"name": "risk_score", "params": dict(_RISK)},
        ]
        for entry in entries:
            if entry["name"] in disabled:
                entry["enabled"] = disabled[entry["name"]]
        return _server(*entries)

    def test_all_three_resolve_by_default(self):
        names = [name for name, _ in pe.resolve_chain(self._full_chain())]
        assert names == list(pb.BUILTIN_POLICIES)

    @pytest.mark.parametrize("target", pb.BUILTIN_POLICIES)
    def test_disabling_one_leaves_the_other_two(self, target: str):
        config = self._full_chain(**{target: False})
        names = [name for name, _ in pe.resolve_chain(config)]
        assert target not in names
        assert set(names) == set(pb.BUILTIN_POLICIES) - {target}

    def test_disabling_the_allowlist_stops_it_denying(self):
        """Switched off means switched off, not merely delisted."""
        config = self._full_chain(git_write_allowlist=False)
        result = _compose(
            config,
            _event(session_id="s-off", arguments={"command": "git push origin main"}),
        )
        assert result.effect != pe.DENY


# ===========================================================================
# 6. The shipped config is wired, not merely declared
# ===========================================================================
class TestShippedConfig:
    def test_the_shipped_chain_resolves_all_three_builtins(self):
        """The card's own thesis, applied to the card: a policy shipped
        `enabled: false` would be the declared-but-never-consumed defect wearing
        the uniform of the fix for it."""
        config = pe.load_config(refresh=True)
        names = [name for name, _ in pe.resolve_chain(config)]
        for builtin in pb.BUILTIN_POLICIES:
            assert builtin in names

    def test_every_shipped_instance_builds_without_error(self):
        """A misconfigured entry resolves to a DENY, which would show up here as
        a policy_config_error rather than as a working instance."""
        config = pe.load_config(refresh=True)
        for name, fn in pe.resolve_chain(config):
            if name not in pb.BUILTIN_POLICIES:
                continue
            decision = fn(_event(target="read_file", session_id="s-shipped"))
            assert getattr(decision, "rule", "") != "policy_config_error"

    def test_the_shipped_config_states_every_threshold_itself(self):
        """No threshold is inherited from Python, so each must be in the YAML."""
        config = pe.load_config(refresh=True)
        params = {
            entry["name"]: entry.get("params") or {}
            for entry in config.get("chain") or []
            if isinstance(entry, dict) and entry.get("name")
        }
        assert "limit" in params[pb.POLICY_MAX_TOOL_CALLS]
        assert "ask_at" in params[pb.POLICY_RISK_SCORE]
        assert "deny_at" in params[pb.POLICY_RISK_SCORE]
        allowlist = params[pb.POLICY_GIT_WRITE_ALLOWLIST]
        assert allowlist.get("deny_branches") and allowlist.get("allow_branches")

    def test_the_shipped_allowlist_denies_a_push_to_main(self):
        """End to end, through the composed runtime path, with the real config."""
        result = pc.evaluate_composed(
            _event(session_id="s-real", arguments={"command": "git push origin main"}),
            session={},
            agent={},
            state=pc.SessionState("s-real", persist=False),
        )
        assert result.effect == pe.DENY
        assert result.policy == pb.POLICY_GIT_WRITE_ALLOWLIST
        assert result.level == "server"


# ===========================================================================
# 7. Composition still holds: a session can only ever ADD a deny
# ===========================================================================
class TestCompositionInteraction:
    def test_a_session_may_cap_itself_more_tightly_than_the_server(self):
        state = pc.SessionState("s-tight", persist=False)
        session = {
            "chain": [
                {
                    "name": "max_tool_calls_per_session",
                    "params": {"limit": 1, "state_key": "session_calls"},
                }
            ]
        }
        server = _server(
            {"name": "max_tool_calls_per_session", "params": {"limit": 500}}
        )
        first = pc.evaluate_composed(
            _event(session_id="s-tight"), session=session, agent={},
            server=server, state=state,
        )
        second = pc.evaluate_composed(
            _event(session_id="s-tight"), session=session, agent={},
            server=server, state=state,
        )
        assert first.effect == pe.ALLOW
        assert second.effect == pe.DENY
        assert second.level == "session"

    def test_a_session_cannot_loosen_the_servers_allowlist(self):
        """A session ALLOW is indistinguishable from a session abstention."""
        session = {
            "chain": [
                {
                    "name": "git_write_allowlist",
                    "params": {"repos": ["*"], "allow_branches": ["*"]},
                }
            ]
        }
        server = _server({"name": "git_write_allowlist", "params": dict(_ALLOWLIST)})
        result = pc.evaluate_composed(
            _event(session_id="s-loose", arguments={"command": "git push origin main"}),
            session=session, agent={}, server=server,
            state=pc.SessionState("s-loose", persist=False),
        )
        assert result.effect == pe.DENY
        assert result.level == "server"
