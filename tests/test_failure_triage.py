# CUI // SP-CTI
"""Unit tests for tools/workflow/failure_triage.py.

Covers the pure-Python decision logic: dedup markers, rate budget, the
should_auto_apply gate chain, and the LLM routing fallback.  The DB
query and actual LLM invocations are mocked.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def ft(monkeypatch, tmp_path):
    """Import failure_triage with temp dirs + env reset."""
    from tools.workflow import failure_triage as ft_mod

    # Redirect file-backed state into the pytest tmpdir
    monkeypatch.setattr(ft_mod, "TRIAGED_DIR", tmp_path / "triaged")
    monkeypatch.setattr(ft_mod, "RATE_FILE", tmp_path / "rate.json")
    monkeypatch.delenv(ft_mod.AUTOFIX_ENV, raising=False)
    return ft_mod


# ---------------------------------------------------------------------------
# dedup markers
# ---------------------------------------------------------------------------

class TestDedup:
    def test_not_triaged_by_default(self, ft):
        assert ft.already_triaged("t1", "sig1") is False

    def test_mark_then_detect(self, ft):
        ft.mark_triaged("t1", "sig1", {"outcome": "test"})
        assert ft.already_triaged("t1", "sig1") is True

    def test_different_signature_not_deduped(self, ft):
        ft.mark_triaged("t1", "sigA", {"outcome": "test"})
        assert ft.already_triaged("t1", "sigB") is False


# ---------------------------------------------------------------------------
# rate budget
# ---------------------------------------------------------------------------

class TestRateBudget:
    def test_empty_log_allows_apply(self, ft):
        ok, count = ft.within_rate_budget()
        assert ok is True
        assert count == 0

    def test_cap_blocks_after_max(self, ft):
        # Record the max applies within the last hour
        now = time.time()
        for i in range(ft.MAX_APPLIES_PER_HOUR):
            ft.record_apply(ts=now - i * 10)
        ok, count = ft.within_rate_budget()
        assert ok is False
        assert count == ft.MAX_APPLIES_PER_HOUR

    def test_old_entries_dont_count(self, ft):
        # 2 hours ago — outside the rolling window
        ft.record_apply(ts=time.time() - 7200)
        ok, count = ft.within_rate_budget()
        assert ok is True
        assert count == 0


# ---------------------------------------------------------------------------
# should_auto_apply gate chain
# ---------------------------------------------------------------------------

class TestAutoApplyGates:
    def _task(self, **overrides):
        base = {
            "id": "t-auto",
            "title": "fix a bug",
            "description": "regular build task",
            "task_type": "fix",
            "last_failure_reason": "AttributeError: typo",
        }
        base.update(overrides)
        return base

    def _diag(self, **overrides):
        base = {
            "root_cause": "missing attribute",
            "recommendation": "patch",
            "patch_hint": "rename _x to x",
            "suspect_files": ["tools/foo.py:42"],
            "confidence": 0.95,
        }
        base.update(overrides)
        return base

    def test_kill_switch_off_blocks(self, ft):
        allow, reason = ft.should_auto_apply(self._task(), self._diag())
        assert allow is False
        assert "AUTOFIX_ENABLED" in reason

    def test_kill_switch_on_and_gates_green(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(self._task(), self._diag())
        assert allow is True, reason

    def test_recommendation_quarantine_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(self._task(), self._diag(recommendation="quarantine"))
        assert allow is False
        assert "'patch'" in reason

    def test_low_confidence_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(self._task(), self._diag(confidence=0.5))
        assert allow is False
        assert "confidence" in reason

    def test_task_type_not_in_whitelist_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            self._task(task_type="deploy"), self._diag(),
        )
        assert allow is False
        assert "task_type" in reason

    def test_deny_token_in_description_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            self._task(description="apply migration 017"), self._diag(),
        )
        assert allow is False
        assert "deny-token" in reason

    def test_deny_file_prefix_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            self._task(),
            self._diag(suspect_files=["tools/db/migrations/017_foo.sql"]),
        )
        assert allow is False
        assert "deny-path" in reason

    def test_rate_limit_blocks(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        now = time.time()
        for i in range(ft.MAX_APPLIES_PER_HOUR):
            ft.record_apply(ts=now - i)
        allow, reason = ft.should_auto_apply(self._task(), self._diag())
        assert allow is False
        assert "rate limit" in reason

    def test_gates_applied_in_stable_order(self, ft, monkeypatch):
        """Kill switch is always checked first — env-off short-circuits
        even if other gates would also fail."""
        # env unset → block even though the diag is obviously bad
        allow, reason = ft.should_auto_apply(
            self._task(task_type="deploy"),
            self._diag(recommendation="quarantine", confidence=0.1),
        )
        assert allow is False
        assert "AUTOFIX_ENABLED" in reason


# ---------------------------------------------------------------------------
# Chain blockers face the SAME confidence bar as leaf tasks (kax-recover-03)
#
# should_auto_apply used to relax the bar from APPLY_CONFIDENCE (0.85) to 0.70
# when blocked_dependents_count > 0. That conflated cost with correctness:
# being a blocker says the failure is expensive, not that the diagnosis is
# right. These tests pin the removal — a blocker and a leaf must be gated
# identically, and the amplified cost is spent on escalation instead.
# ---------------------------------------------------------------------------

class TestChainBlockerSameBar:
    def _task(self, blocked, **overrides):
        base = {
            "id": "t-chain",
            "title": "fix a bug",
            "description": "regular build task",
            "task_type": "fix",
            "last_failure_reason": "AttributeError: typo",
            "blocked_dependents_count": blocked,
        }
        base.update(overrides)
        return base

    def _diag(self, confidence):
        return {
            "root_cause": "missing attribute",
            "recommendation": "patch",
            "patch_hint": "rename _x to x",
            "suspect_files": ["tools/foo.py:42"],
            "confidence": confidence,
        }

    @pytest.mark.parametrize(
        "confidence", [0.0, 0.5, 0.69, 0.70, 0.71, 0.80, 0.84, 0.85, 0.90, 1.0],
    )
    def test_blocker_and_leaf_decide_identically(self, ft, monkeypatch, confidence):
        """The whole point: blocker status must not move the bar anywhere."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        leaf_allow, _ = ft.should_auto_apply(self._task(0), self._diag(confidence))
        blocker_allow, _ = ft.should_auto_apply(self._task(3), self._diag(confidence))
        assert leaf_allow == blocker_allow, (
            f"confidence {confidence} decided differently for a chain blocker "
            f"(leaf={leaf_allow}, blocker={blocker_allow})"
        )
        assert leaf_allow is (confidence >= ft.APPLY_CONFIDENCE)

    def test_the_old_relaxed_band_now_blocks_a_blocker(self, ft, monkeypatch):
        """0.70 <= conf < 0.85 was the window the relaxation opened."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(self._task(9), self._diag(0.75))
        assert allow is False
        assert f"threshold {ft.APPLY_CONFIDENCE}" in reason

    def test_reason_string_advertises_no_lowered_threshold(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        for conf in (0.75, 0.95):
            _, reason = ft.should_auto_apply(self._task(2), self._diag(conf))
            assert "0.70" not in reason
            assert "lower" not in reason.lower()

    def test_effective_bar_is_the_same_number_for_both(self, ft, monkeypatch):
        """Sweep for the allow boundary independently on each shape."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")

        def first_allowed(blocked):
            for step in range(0, 101):
                conf = step / 100.0
                allow, _ = ft.should_auto_apply(self._task(blocked), self._diag(conf))
                if allow:
                    return conf
            return None

        leaf_bar = first_allowed(0)
        blocker_bar = first_allowed(4)
        assert leaf_bar == blocker_bar == pytest.approx(ft.APPLY_CONFIDENCE)

    def test_decision_and_rationale_are_recorded_in_the_docstring(self, ft):
        """Acceptance criterion: the call must not be a silent threshold flip."""
        doc = ft.should_auto_apply.__doc__ or ""
        assert "kax-recover-03" in doc
        assert "REMOVED" in doc
        # The distinction the removal turns on has to be stated, not implied.
        assert "correct" in doc.lower() and "cost" in doc.lower()


class TestChainBlockerEscalation:
    def _task(self, blocked):
        return {"id": "t-esc", "blocked_dependents_count": blocked}

    def test_leaf_task_gets_no_escalation(self, ft):
        assert ft.chain_blocker_escalation(self._task(0)) is None

    def test_missing_count_is_treated_as_leaf(self, ft):
        assert ft.chain_blocker_escalation({"id": "t"}) is None

    def test_blocker_escalates_to_critical_with_a_marker(self, ft):
        esc = ft.chain_blocker_escalation(self._task(3))
        assert esc is not None
        assert esc["priority"] == "critical"
        assert esc["blocked_dependents_count"] == 3
        assert "3 task(s) stalled" in esc["title_marker"]

    def test_escalation_does_not_touch_the_apply_gate(self, ft, monkeypatch):
        """Escalation is a routing decision; it must not grant an apply."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        task = {
            "id": "t-esc2", "task_type": "fix", "description": "",
            "last_failure_reason": "AttributeError: typo",
            "blocked_dependents_count": 7,
        }
        diag = {
            "recommendation": "patch", "confidence": 0.80,
            "root_cause": "x", "patch_hint": "y", "suspect_files": ["tools/foo.py"],
        }
        assert ft.chain_blocker_escalation(task) is not None
        allow, _ = ft.should_auto_apply(task, diag)
        assert allow is False


# ---------------------------------------------------------------------------
# Non-code failure classes — a TIMEOUT is a budget symptom, not a defect
# ---------------------------------------------------------------------------

# The verbatim strings the kanban scheduler writes into last_failure_reason.
# Each is paired with the writer that produces it so the pairing stays
# checkable when a writer's wording changes. MEASURED 2026-08-08 — four of
# the five tasks that entered the autofix queue carried the first one.
NON_CODE_REASONS = [
    # runtime budget / lifecycle
    ("TIMEOUT after 3430s (max 3386s) — task exceeded dispatch budget",
     "reaper timeout kill"),
    ("stale-reaper: task was in_progress for 95 min with an empty log "
     "(threshold=60 min). Automatically reset to backlog for re-dispatch.",
     "_reap_stale_in_progress"),
    ("Zombie reclaim: no heartbeat for >6h", "_reclaim_zombies"),
    ("startup-recovery: task was in_progress when the scheduler restarted "
     "— process died or scheduler crashed mid-run.", "startup sweep"),
    ("Circuit breaker: failure_count 5 >= max_retries 5", "retry cap"),
    # executor environment
    ("no executor available: internet=False, gitlab=unreachable, "
     "ollama=unreachable", "dispatch fallback chain"),
    # not a failure at all — bookkeeping parked in the column
    ("Task-specific checks passed or not applicable", "_verify_task_specific"),
    ("decay-promoted: re-queued after 48 h in suggested",
     "_promote_decayed_suggested"),
    ("auto-revive 2/3: deps satisfied + cooled down, re-queued to backlog "
     "for another attempt.", "_revive_quarantined_suggested"),
    ("dep-chain-unblock: child waiting in backlog, revived from suggested "
     "(fc was 2)", "_revive_dep_chain_blocked"),
    ("cascade: parent sbx-fmt-01 demoted from done", "_move_task rollback"),
    ("auto-closed: parent kax-obs-01 resolved", "_close_orphaned_rca_children"),
    ("UNCLASSIFIED (no failure clause): Verified (git-first): 2 files changed",
     "_split_failure_narrative fallback"),
]

# A real defect the autofixer exists to handle — same shape, same confidence.
GENUINE_CODE_FAILURE = (
    "VALIDATION FAILED: pytest — tests/test_foo.py::test_bar "
    "AttributeError: 'Router' object has no attribute 'invoke_sync'"
)


class TestNonCodeFailureClasses:
    """A failure whose cause is the dispatch budget, the scheduler lifecycle,
    or the executor environment has no code a patch could fix. Autofix must
    not spend an LLM generation on it — but a genuine code failure with the
    SAME confidence must still get through."""

    def _task(self, reason, **overrides):
        base = {
            "id": "t-nc",
            "title": "rebuild the SBOM disclosure seam",
            "description": "regular build task",
            "task_type": "build",
            "last_failure_reason": reason,
        }
        base.update(overrides)
        return base

    def _diag(self, **overrides):
        base = {
            "root_cause": "the task did not finish",
            "recommendation": "patch",
            "patch_hint": "raise the budget",
            "suspect_files": ["tools/foo.py:42"],
            "confidence": 0.95,
        }
        base.update(overrides)
        return base

    @pytest.mark.parametrize(
        "reason,writer", NON_CODE_REASONS,
        ids=[w for _, w in NON_CODE_REASONS],
    )
    def test_non_code_reason_is_denied(self, ft, reason, writer):
        hit = ft._deny_hit(self._diag(), self._task(reason))
        assert hit is not None, (
            f"{writer} writes {reason[:60]!r} into last_failure_reason; "
            f"there is no code a patch could fix, so _deny_hit must block it"
        )
        assert "non-code failure class" in hit

    def test_timeout_denied_end_to_end_through_the_gate(self, ft, monkeypatch):
        """The measured case: autofix ON, confidence 0.95, whitelisted
        task_type — and it still must not auto-apply."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            self._task("TIMEOUT after 3430s (max 3386s) — task exceeded "
                       "dispatch budget"),
            self._diag(),
        )
        assert allow is False
        assert "non-code failure class" in reason

    def test_genuine_code_failure_at_same_confidence_still_allowed(
        self, ft, monkeypatch,
    ):
        """The control. Identical confidence, identical task_type, identical
        suspect files — only last_failure_reason differs. If this ever starts
        failing, the deny list has been widened into a kill switch."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        task = self._task(GENUINE_CODE_FAILURE)
        diag = self._diag()

        assert ft._deny_hit(diag, task) is None
        allow, reason = ft.should_auto_apply(task, diag)
        assert allow is True, reason

    def test_timeout_in_description_does_not_block(self, ft, monkeypatch):
        """Scoping proof. NON_CODE_FAILURE_TOKENS is matched against
        last_failure_reason ONLY — a task legitimately *about* timeouts stays
        eligible when it fails for a real reason. This is why 'timeout' is not
        in DENY_SIGNATURE_TOKENS, which also matches description."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            self._task(
                GENUINE_CODE_FAILURE,
                description="add a socket timeout to the CSP monitor probe "
                            "so it stops hanging",
            ),
            self._diag(root_cause="the probe never sets a timeout"),
        )
        assert allow is True, reason

    def test_every_token_is_lowercase(self, ft):
        """_deny_hit lower-cases the reason before matching, so an uppercase
        token could never fire."""
        for tok in ft.NON_CODE_FAILURE_TOKENS:
            assert tok == tok.lower(), f"{tok!r} can never match"


class TestCompliancePathDeny:
    """tools/compliance/ was added to DENY_FILE_PREFIXES on 2026-08-08.
    Criterion: the independent gates cannot detect a wrong compliance
    artifact (it compiles, lints, and the tests assert structure not truth),
    and the artifact is outward-facing evidence a recipient may already hold.
    """

    def test_sbom_generator_is_denied(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        allow, reason = ft.should_auto_apply(
            {"id": "t-c", "title": "fix sbom", "description": "d",
             "task_type": "build",
             "last_failure_reason": GENUINE_CODE_FAILURE},
            {"recommendation": "patch", "confidence": 0.95,
             "root_cause": "wrong component list",
             "suspect_files": ["tools/compliance/sbom_generator.py:88"]},
        )
        assert allow is False
        assert "deny-path" in reason

    def test_apply_side_also_rejects_compliance_paths(self, ft, tmp_path,
                                                      monkeypatch):
        """Belt-and-suspenders: _validate_patch_files re-checks the prefix so
        a patch cannot reach the worktree even if the gate is bypassed."""
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        target = tmp_path / "tools" / "compliance" / "sbom_generator.py"
        target.parent.mkdir(parents=True)
        target.write_text("COMPONENTS = []\n", encoding="utf-8")
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/compliance/sbom_generator.py",
                        "old_string": "COMPONENTS", "new_string": "PARTS"}]},
            {"suspect_files": ["tools/compliance/sbom_generator.py"]},
        )
        assert ok is False
        assert "deny-path" in why


# ---------------------------------------------------------------------------
# diagnose_task — LLM routing fallback
# ---------------------------------------------------------------------------

class TestDiagnoseFallback:
    def test_llm_unavailable_falls_back_to_self_debug(self, ft, monkeypatch):
        """When the thinking-tier LLM raises LLMUnavailableError, we MUST
        fall back to the deterministic self_debug.diagnose heuristic."""
        from tools.workflow import self_debug

        # Stub self_debug.snapshot so we don't touch git.
        monkeypatch.setattr(
            self_debug, "snapshot",
            lambda task_id, cwd, reason: {"reason": reason, "task_id": task_id},
        )
        # Stub self_debug.diagnose so we can assert it was called. It must accept
        # chain_mode — diagnose_task forwards it on every fallback branch, and a
        # one-arg stub turns the fallback into a TypeError instead of a result.
        called = {}
        def fake_diag(snap, chain_mode=""):
            called["yes"] = True
            return {"root_cause": "fallback", "recommendation": "quarantine",
                    "confidence": 0.4, "_source": "heuristic"}
        monkeypatch.setattr(self_debug, "diagnose", fake_diag)

        # Stub the LLM path to raise LLMUnavailableError.
        #
        # importlib, NOT `import tools.llm.router as router_mod`: tools/llm/router.py
        # is a real module, but tools/__init__.py's _ToolsRedirect resolves the
        # attribute traversal in the `import a.b.c as x` form to
        # icdev.tools.llm.router — a DIFFERENT class object from the one
        # `from tools.llm.router import LLMRouter` inside diagnose_task binds.
        # Patching that one silently missed, and this test made a real LLM call.
        import importlib
        router_mod = importlib.import_module("tools.llm.router")
        def boom(self, function, request):
            raise router_mod.LLMUnavailableError(
                "no provider", function=function, chain=[], no_llm_mode=False,
            )
        monkeypatch.setattr(router_mod.LLMRouter, "invoke", boom)

        diag = ft.diagnose_task(
            {"id": "t1", "title": "x", "description": "",
             "task_type": "fix", "last_failure_reason": "boom"},
        )
        assert called.get("yes") is True
        assert diag["_source"] == "heuristic"
        assert diag["root_cause"] == "fallback"


# ---------------------------------------------------------------------------
# triage_once — end-to-end with mocked DB + diagnosis
# ---------------------------------------------------------------------------

class TestTriageOnce:
    def test_no_failures_returns_empty(self, ft, monkeypatch):
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [])
        summary = ft.triage_once(apply=False)
        assert summary["failures_scanned"] == 0
        assert summary["results"] == []

    def test_already_triaged_is_skipped(self, ft, monkeypatch):
        task = {
            "id": "t-skip", "title": "t", "description": "",
            "task_type": "fix", "last_failure_reason": "x",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        sig = ft._sig("x")
        ft.mark_triaged("t-skip", sig, {"prior": True})
        # diagnosis should not be called
        def boom(_t):
            raise AssertionError("should not diagnose a deduped task")
        monkeypatch.setattr(ft, "diagnose_task", boom)
        summary = ft.triage_once(apply=False)
        assert summary["results"][0]["outcome"] == "skipped_already_triaged"

    def test_low_confidence_creates_suggested_card(self, ft, monkeypatch):
        task = {
            "id": "t-low", "title": "t", "description": "",
            "task_type": "fix", "last_failure_reason": "y",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        monkeypatch.setattr(ft, "diagnose_task", lambda t: {
            "root_cause": "unclear", "recommendation": "quarantine",
            "confidence": 0.4, "_source": "heuristic",
        })
        calls = []
        monkeypatch.setattr(ft, "_create_diagnostic_card",
                            lambda t, d: calls.append(("card", t["id"])) or "diag-xyz")
        summary = ft.triage_once(apply=True)
        assert summary["results"][0]["outcome"] == "suggested_card_created"
        assert calls == [("card", "t-low")]

    def test_apply_calls_worktree_stage_when_gates_green(self, ft, monkeypatch):
        """With env on, apply=True, high confidence, and a valid patch the
        triage loop MUST call apply_patch_in_worktree (not just print)."""
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        task = {
            "id": "t-go", "title": "t", "description": "regular task",
            "task_type": "fix", "last_failure_reason": "AttributeError: _x",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        monkeypatch.setattr(ft, "diagnose_task", lambda t: {
            "root_cause": "typo", "recommendation": "patch",
            "confidence": 0.95, "suspect_files": ["tools/foo.py"],
            "_source": "llm",
        })
        monkeypatch.setattr(ft, "generate_patch", lambda t, d: {
            "files": [{"path": "tools/foo.py", "old_string": "old", "new_string": "new"}],
            "verification_command": "python -m pytest tests/test_foo.py",
        })
        seen = {}
        monkeypatch.setattr(ft, "apply_patch_in_worktree",
                            lambda t, d, p: seen.setdefault("called", (t, d, p)) and None
                            or {"applied": True, "outcome": "applied_verified_committed"})
        monkeypatch.setattr(ft, "_create_diagnostic_card_with_patch", lambda t, d, p: "diag-xyz")
        summary = ft.triage_once(apply=True)
        assert "called" in seen
        assert summary["results"][0]["outcome"] == "applied_verified_committed"

    def test_apply_without_env_stays_on_suggested_path(self, ft, monkeypatch):
        """Even with apply=True, kill-switch env off means no patch gen."""
        monkeypatch.delenv(ft.AUTOFIX_ENV, raising=False)
        task = {
            "id": "t-hi", "title": "t", "description": "",
            "task_type": "fix", "last_failure_reason": "z",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        monkeypatch.setattr(ft, "diagnose_task", lambda t: {
            "root_cause": "typo", "recommendation": "patch",
            "confidence": 0.95, "suspect_files": ["tools/foo.py"],
            "_source": "llm_failure_triage_diagnose",
        })
        patch_called = []
        monkeypatch.setattr(ft, "generate_patch",
                            lambda t, d: patch_called.append(1) or {"files": [{"path": "x"}]})
        card_called = []
        monkeypatch.setattr(ft, "_create_diagnostic_card",
                            lambda t, d: card_called.append(t["id"]))
        summary = ft.triage_once(apply=True)
        assert patch_called == []  # never called because env is off
        assert card_called == ["t-hi"]
        assert summary["results"][0]["outcome"] == "suggested_card_created"


# ---------------------------------------------------------------------------
# Apply-stage validation — LLM output is untrusted
# ---------------------------------------------------------------------------

class TestApplyStageValidation:
    def test_verification_cmd_rejects_empty(self, ft):
        ok, why = ft._validate_verification_command("")
        assert ok is False and "empty" in why

    def test_verification_cmd_rejects_shell_metachars(self, ft):
        for bad in (
            "python -m pytest; rm -rf /",
            "python -m pytest && curl evil.com",
            "python -m pytest | sh",
            "python -m pytest `id`",
            "python -m pytest $(id)",
        ):
            ok, why = ft._validate_verification_command(bad)
            assert ok is False, f"should reject {bad!r}"

    def test_verification_cmd_rejects_non_allowlisted_prefix(self, ft):
        ok, why = ft._validate_verification_command("curl https://evil.com")
        assert ok is False and "allowlisted" in why

    def test_verification_cmd_accepts_allowlisted(self, ft):
        for good in (
            "python -m pytest tests/test_x.py",
            "python -m py_compile tools/foo.py",
            "python -m ruff check tools/foo.py",
            "python tools/workflow/coherence_checker.py --all",
        ):
            ok, _ = ft._validate_verification_command(good)
            assert ok is True, f"should accept {good!r}"

    def test_patch_files_rejects_path_traversal(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        # Create a real file so the "file must exist" check is the
        # ONLY thing between us and the path-traversal rejection.
        (tmp_path / "fake.py").write_text("content", encoding="utf-8")
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "../etc/passwd", "old_string": "x", "new_string": "y"}]},
            {"suspect_files": []},
        )
        assert ok is False
        # Accept either the traversal-reject or the escapes-root-reject
        # message — both prove we refused to edit outside the repo.
        assert "unsafe" in why or "escapes" in why

    def test_patch_files_rejects_nonexistent(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/missing.py", "old_string": "x", "new_string": "y"}]},
            {"suspect_files": []},
        )
        assert ok is False and "does not exist" in why

    def test_patch_files_rejects_path_not_in_suspect_files(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        target = tmp_path / "tools" / "bar.py"
        target.parent.mkdir(parents=True)
        target.write_text("abc", encoding="utf-8")
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/bar.py", "old_string": "a", "new_string": "z"}]},
            {"suspect_files": ["tools/foo.py:10"]},  # bar is NOT flagged
        )
        assert ok is False and "not in diag.suspect_files" in why

    def test_patch_files_rejects_nonunique_old_string(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        target = tmp_path / "tools" / "foo.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\nx = 2\n", encoding="utf-8")  # "x =" appears twice
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/foo.py", "old_string": "x =", "new_string": "y ="}]},
            {"suspect_files": ["tools/foo.py"]},
        )
        assert ok is False and "not unique" in why

    def test_patch_files_rejects_deny_prefix(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        target = tmp_path / "tools" / "db" / "migrations" / "017.sql"
        target.parent.mkdir(parents=True)
        target.write_text("CREATE TABLE x;", encoding="utf-8")
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/db/migrations/017.sql",
                        "old_string": "CREATE", "new_string": "DROP"}]},
            {"suspect_files": ["tools/db/migrations/017.sql"]},
        )
        assert ok is False and "deny-path" in why

    def test_patch_files_accepts_valid_patch(self, ft, tmp_path, monkeypatch):
        monkeypatch.setattr(ft, "BASE_DIR", tmp_path)
        target = tmp_path / "tools" / "foo.py"
        target.parent.mkdir(parents=True)
        target.write_text("def foo(): return 1", encoding="utf-8")
        ok, why = ft._validate_patch_files(
            {"files": [{"path": "tools/foo.py",
                        "old_string": "return 1", "new_string": "return 2"}]},
            {"suspect_files": ["tools/foo.py:1"]},
        )
        assert ok is True, why


# ---------------------------------------------------------------------------
# Apply-stage rejection paths (no real worktree — asserts early returns)
# ---------------------------------------------------------------------------

class TestApplyPatchRejections:
    def test_rejects_on_bad_verification_cmd(self, ft, monkeypatch, tmp_path):
        monkeypatch.setattr(ft, "AUDIT_DIR", tmp_path / "audit")
        # record_apply MUST NOT be called when validation fails before apply
        counted = []
        monkeypatch.setattr(ft, "record_apply",
                            lambda ts=None: counted.append(1))
        monkeypatch.setattr(ft, "_create_autofix_worktree",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("should not create worktree on validation fail")
                            ))
        result = ft.apply_patch_in_worktree(
            {"id": "t-bad", "last_failure_reason": "x"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "rm -rf /"},
        )
        assert result["applied"] is False
        assert result["outcome"] == "rejected_bad_verification_command"
        assert counted == []  # budget not consumed

    def test_rejects_on_bad_patch_files(self, ft, monkeypatch, tmp_path):
        monkeypatch.setattr(ft, "AUDIT_DIR", tmp_path / "audit")
        counted = []
        monkeypatch.setattr(ft, "record_apply",
                            lambda ts=None: counted.append(1))
        result = ft.apply_patch_in_worktree(
            {"id": "t-bad2", "last_failure_reason": "x"},
            {"suspect_files": []},  # LLM didn't flag any file
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/"},
        )
        assert result["applied"] is False
        assert result["outcome"] == "rejected_bad_patch_files"
        assert counted == []


# ---------------------------------------------------------------------------
# Automerge is off by default — safety invariant
# ---------------------------------------------------------------------------

class TestAutomergeSwitch:
    def test_automerge_env_off_by_default(self, ft, monkeypatch):
        monkeypatch.delenv(ft.AUTOMERGE_ENV, raising=False)
        assert ft.automerge_enabled() is False

    def test_automerge_env_on(self, ft, monkeypatch):
        monkeypatch.setenv(ft.AUTOMERGE_ENV, "true")
        assert ft.automerge_enabled() is True


# ---------------------------------------------------------------------------
# Structured NDJSON recovery events (WS3.2 / arc-obs-02)
# ---------------------------------------------------------------------------

class TestStructuredEvents:
    """Each triage decision emits a structured event via get_logger that
    lands in .logs/ now and flows to centralized_logs once log_ingest lands.
    We capture the underlying logger calls instead of touching the filesystem.
    """

    @pytest.fixture
    def captured(self, ft, monkeypatch):
        """Capture every (level, event_type, payload) passed to _events.log."""
        events = []

        def fake_log(level, msg, *args, **kwargs):
            payload = (kwargs.get("extra") or {}).get("extra", {})
            events.append((level, msg, payload))

        monkeypatch.setattr(ft._events, "log", fake_log)
        return events

    def test_emit_event_drops_none_fields(self, ft, captured):
        ft._emit_event(
            "diagnosis_made", task_id="t1", signature="sig1",
            confidence=0.9, recommendation=None,
        )
        assert len(captured) == 1
        _level, msg, payload = captured[0]
        assert msg == "diagnosis_made"
        assert payload["event_type"] == "diagnosis_made"
        assert payload["task_id"] == "t1"
        assert payload["signature"] == "sig1"
        assert payload["confidence"] == 0.9
        assert "recommendation" not in payload  # None dropped

    def test_emit_event_never_raises(self, ft, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("logging exploded")
        monkeypatch.setattr(ft._events, "log", boom)
        # Must swallow — logging cannot break the triage pipeline.
        ft._emit_event("gate_decision", task_id="t", signature="s")

    def test_suggested_card_path_emits_diagnosis_gate_and_outcome(self, ft, monkeypatch, captured):
        task = {
            "id": "t-ev", "title": "t", "description": "",
            "task_type": "fix", "last_failure_reason": "boom",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        monkeypatch.setattr(ft, "diagnose_task", lambda t: {
            "root_cause": "unclear", "recommendation": "quarantine",
            "confidence": 0.4, "_source": "heuristic",
        })
        monkeypatch.setattr(ft, "_create_diagnostic_card", lambda t, d: "diag-1")

        ft.triage_once(apply=True)

        types = [p["event_type"] for (_l, _m, p) in captured]
        assert types == ["diagnosis_made", "gate_decision", "apply_outcome"]
        outcome_evt = captured[-1][2]
        assert outcome_evt["outcome"] == "suggested_card_created"
        assert outcome_evt["task_id"] == "t-ev"
        assert outcome_evt["signature"] == ft._sig("boom")

    def test_apply_path_emits_full_event_sequence(self, ft, monkeypatch, captured):
        monkeypatch.setenv(ft.AUTOFIX_ENV, "true")
        task = {
            "id": "t-go", "title": "t", "description": "regular task",
            "task_type": "fix", "last_failure_reason": "AttributeError: _x",
        }
        monkeypatch.setattr(ft, "find_recent_failures", lambda **k: [task])
        monkeypatch.setattr(ft, "diagnose_task", lambda t: {
            "root_cause": "typo", "recommendation": "patch",
            "confidence": 0.95, "suspect_files": ["tools/foo.py"], "_source": "llm",
        })
        monkeypatch.setattr(ft, "generate_patch", lambda t, d: {
            "files": [{"path": "tools/foo.py", "old_string": "old", "new_string": "new"}],
            "verification_command": "python -m pytest tests/test_foo.py",
        })
        monkeypatch.setattr(ft, "apply_patch_in_worktree",
                            lambda t, d, p: {"applied": True, "outcome": "applied_verified_committed"})
        monkeypatch.setattr(ft, "_create_diagnostic_card_with_patch", lambda t, d, p: "diag-1")

        ft.triage_once(apply=True)

        types = [p["event_type"] for (_l, _m, p) in captured]
        # apply_patch_in_worktree is mocked so verify_result fires inside it
        # only in the real path; here we assert the orchestrator-level events.
        assert types == ["diagnosis_made", "gate_decision", "patch_generated", "apply_outcome"]
        patch_evt = next(p for (_l, _m, p) in captured if p["event_type"] == "patch_generated")
        assert patch_evt["generated"] is True
        assert patch_evt["files"] == ["tools/foo.py"]

    def test_verify_result_emitted_in_worktree_on_failure(self, ft, monkeypatch, tmp_path, captured):
        """apply_patch_in_worktree emits verify_result with the rc/outcome."""
        monkeypatch.setattr(ft, "AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setattr(ft, "record_apply", lambda ts=None: None)
        monkeypatch.setattr(ft, "_create_autofix_worktree",
                            lambda tid, sig: (tmp_path / "wt", "autofix/x"))
        # Make the file edit a no-op and force verification to fail (rc=1).
        (tmp_path / "wt").mkdir(parents=True)
        monkeypatch.setattr(ft, "_run", lambda *a, **k: (1, "boom"))
        monkeypatch.setattr(ft, "_cleanup_autofix_worktree", lambda *a, **k: None)
        monkeypatch.setattr(ft, "_validate_verification_command", lambda c: (True, "ok"))
        monkeypatch.setattr(ft, "_validate_patch_files", lambda p, d: (True, "ok"))

        # Patch file edit: the loop reads/writes via Path — stub the file.
        target = tmp_path / "wt" / "tools" / "foo.py"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")

        ft.apply_patch_in_worktree(
            {"id": "t-vf", "last_failure_reason": "x", "title": "t"},
            {"suspect_files": ["tools/foo.py"], "confidence": 0.9, "recommendation": "patch"},
            {"files": [{"path": "tools/foo.py", "old_string": "old", "new_string": "new"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
        )

        verify_evts = [p for (_l, _m, p) in captured if p["event_type"] == "verify_result"]
        assert len(verify_evts) == 1
        assert verify_evts[0]["verification_rc"] == 1
        assert verify_evts[0]["passed"] is False
        assert verify_evts[0]["outcome"] == "verify_failed"


# ---------------------------------------------------------------------------
# WS4.2 — verification beyond the LLM's own command
# (originally failing test + ruff + coherence, diffed vs base)
# ---------------------------------------------------------------------------

import json as _json


def _coh_json(failing_ids, warned_ids=()):
    """Build a coherence_checker --json report with noisy import prefix."""
    checks = [{"check_id": cid, "check_name": cid, "status": "fail",
               "message": f"{cid} broke"} for cid in failing_ids]
    checks += [{"check_id": cid, "check_name": cid, "status": "warn",
                "message": f"{cid} warned"} for cid in warned_ids]
    checks += [{"check_id": "schema_code", "check_name": "schema_code",
                "status": "pass", "message": "ok"}]
    report = {
        "overall_pass": not failing_ids,
        "checks": checks,
        "total_checks": len(checks),
        "failed_checks": len(failing_ids),
        "warned_checks": len(warned_ids),
    }
    # Prepend import-side-effect noise like the real subprocess emits.
    return "[init_db] Schema created (PostgreSQL)\n[BP-REG] x\n" + _json.dumps(report, indent=2)


class TestExtractLastJson:
    def test_pulls_report_past_noise(self, ft):
        text = _coh_json(["a", "b"])
        obj = ft._extract_last_json(text)
        assert obj is not None
        assert obj["failed_checks"] == 2

    def test_returns_none_on_garbage(self, ft):
        assert ft._extract_last_json("boom no json here") is None

    def test_picks_last_object_when_multiple(self, ft):
        text = '{"check_id": "noise"}\nmid\n' + _coh_json(["z"])
        obj = ft._extract_last_json(text)
        assert "checks" in obj and obj["failed_checks"] == 1


class TestOriginalTestTargets:
    def test_extracts_node_id_for_existing_file(self, ft, tmp_path):
        t = tmp_path / "tests" / "test_foo.py"
        t.parent.mkdir(parents=True)
        t.write_text("def test_x(): pass", encoding="utf-8")
        reason = "FAILED tests/test_foo.py::test_x - AssertionError"
        assert ft._original_test_targets(reason, tmp_path) == ["tests/test_foo.py::test_x"]

    def test_skips_nonexistent_file(self, ft, tmp_path):
        reason = "FAILED tests/test_missing.py::test_y"
        assert ft._original_test_targets(reason, tmp_path) == []

    def test_no_target_for_gate_failure(self, ft, tmp_path):
        reason = "route_not_listed: /foo missing from start.md Pages line"
        assert ft._original_test_targets(reason, tmp_path) == []

    def test_caps_at_five(self, ft, tmp_path):
        (tmp_path / "tests").mkdir()
        parts = []
        for i in range(8):
            f = tmp_path / "tests" / f"test_{i}.py"
            f.write_text("def test_z(): pass", encoding="utf-8")
            parts.append(f"tests/test_{i}.py::test_z")
        reason = " ".join(parts)
        assert len(ft._original_test_targets(reason, tmp_path)) == 5


class TestRunIndependentGates:
    """_run_independent_gates dispatches three subprocesses; we stub ft._run
    by inspecting the command vector."""

    def _make_run(self, *, pytest_rc=0, ruff_rc=0, coh_fails=(), coh_text=None):
        def fake_run(cmd, cwd, timeout=60):
            joined = " ".join(cmd)
            if "pytest" in joined:
                return (pytest_rc, "pytest output")
            if "ruff" in joined:
                return (ruff_rc, "All checks passed!" if ruff_rc == 0 else "Found 1 error")
            if "coherence_checker" in joined:
                return (1 if coh_fails else 0,
                        coh_text if coh_text is not None else _coh_json(list(coh_fails)))
            return (0, "")
        return fake_run

    def test_all_green_passes(self, ft, monkeypatch, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass", encoding="utf-8")
        monkeypatch.setattr(ft, "_run", self._make_run())
        task = {"id": "t1", "last_failure_reason": "FAILED tests/test_a.py::test_a"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails=set())
        assert res["passed"] is True
        assert res["first_failure"] is None
        assert res["gates"]["original_test"]["ran"] is True

    def test_original_test_still_failing_blocks(self, ft, monkeypatch, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass", encoding="utf-8")
        monkeypatch.setattr(ft, "_run", self._make_run(pytest_rc=1))
        task = {"id": "t1", "last_failure_reason": "FAILED tests/test_a.py::test_a"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails=set())
        assert res["passed"] is False
        assert res["first_failure"] == "original_test"

    def test_ruff_failure_blocks(self, ft, monkeypatch, tmp_path):
        monkeypatch.setattr(ft, "_run", self._make_run(ruff_rc=1))
        # no test target -> original_test skipped, ruff is the blocker
        task = {"id": "t1", "last_failure_reason": "coherence: route_not_listed"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails=set())
        assert res["passed"] is False
        assert res["first_failure"] == "ruff"
        assert res["gates"]["original_test"]["ran"] is False

    def test_new_coherence_failure_blocks(self, ft, monkeypatch, tmp_path):
        # baseline had {x}; after patch it has {x, y} -> y is NEW -> block
        monkeypatch.setattr(ft, "_run", self._make_run(coh_fails=("x", "y")))
        task = {"id": "t1", "last_failure_reason": "coherence: foo"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails={"x"})
        assert res["passed"] is False
        assert res["first_failure"] == "coherence"
        assert res["gates"]["coherence"]["new_fails"] == ["y"]

    def test_inherited_coherence_debt_does_not_block(self, ft, monkeypatch, tmp_path):
        # base already failing {x, z}; after patch same set -> no NEW fail
        monkeypatch.setattr(ft, "_run", self._make_run(coh_fails=("x", "z")))
        task = {"id": "t1", "last_failure_reason": "coherence: foo"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails={"x", "z"})
        assert res["passed"] is True

    def test_unparseable_post_patch_coherence_fails_closed(self, ft, monkeypatch, tmp_path):
        monkeypatch.setattr(ft, "_run", self._make_run(coh_text="boom no json"))
        task = {"id": "t1", "last_failure_reason": "coherence: foo"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails=set())
        assert res["passed"] is False
        assert res["first_failure"] == "coherence_unparsed"

    def test_missing_baseline_skips_coherence_diff(self, ft, monkeypatch, tmp_path):
        # baseline None (could not capture) -> don't block on inherited debt
        monkeypatch.setattr(ft, "_run", self._make_run(coh_fails=("x",)))
        task = {"id": "t1", "last_failure_reason": "coherence: foo"}
        res = ft._run_independent_gates(task, tmp_path, baseline_coh_fails=None)
        assert res["passed"] is True
        assert "skipping coherence diff" in res["gates"]["coherence"]["note"]


class TestApplyPatchIndependentGate:
    """apply_patch_in_worktree must roll back when an independent gate fails,
    even though the LLM's verification_command passed."""

    def _setup(self, ft, monkeypatch, tmp_path):
        monkeypatch.setattr(ft, "AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setattr(ft, "record_apply", lambda ts=None: None)
        wt = tmp_path / "wt"
        wt.mkdir(parents=True)
        monkeypatch.setattr(ft, "_create_autofix_worktree", lambda tid, sig: (wt, "autofix/x"))
        cleaned = []
        monkeypatch.setattr(ft, "_cleanup_autofix_worktree",
                            lambda w, b, keep_branch: cleaned.append(keep_branch))
        monkeypatch.setattr(ft, "_validate_verification_command", lambda c: (True, "ok"))
        monkeypatch.setattr(ft, "_validate_patch_files", lambda p, d: (True, "ok"))
        target = wt / "tools" / "foo.py"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")
        return wt, cleaned

    def test_independent_gate_failure_rolls_back(self, ft, monkeypatch, tmp_path):
        wt, cleaned = self._setup(ft, monkeypatch, tmp_path)
        # verification_command passes (rc 0); independent gate reports failure.
        monkeypatch.setattr(ft, "_run", lambda *a, **k: (0, "ok"))
        monkeypatch.setattr(ft, "_coherence_failing_checks", lambda w: (set(), 0, "base"))
        monkeypatch.setattr(ft, "_run_independent_gates",
                            lambda t, w, b: {"passed": False, "first_failure": "ruff", "gates": {}})
        res = ft.apply_patch_in_worktree(
            {"id": "t-ig", "last_failure_reason": "x", "title": "t"},
            {"suspect_files": ["tools/foo.py"], "confidence": 0.9, "recommendation": "patch"},
            {"files": [{"path": "tools/foo.py", "old_string": "old", "new_string": "new"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
        )
        assert res["applied"] is False
        assert res["outcome"] == "independent_gate_failed"
        assert res["failed_gate"] == "ruff"
        # worktree removed WITHOUT keeping the branch (rollback)
        assert cleaned == [False]

    def test_independent_gate_pass_commits(self, ft, monkeypatch, tmp_path):
        wt, cleaned = self._setup(ft, monkeypatch, tmp_path)
        monkeypatch.setattr(ft, "_coherence_failing_checks", lambda w: (set(), 0, "base"))
        monkeypatch.setattr(ft, "_run_independent_gates",
                            lambda t, w, b: {"passed": True, "first_failure": None, "gates": {}})
        # All git subprocess calls succeed; rev-parse returns a sha.
        def fake_run(cmd, cwd, timeout=60):
            if "rev-parse" in cmd:
                return (0, "deadbeef\n")
            return (0, "ok")
        monkeypatch.setattr(ft, "_run", fake_run)
        monkeypatch.delenv(ft.AUTOMERGE_ENV, raising=False)
        res = ft.apply_patch_in_worktree(
            {"id": "t-ig2", "last_failure_reason": "x", "title": "t"},
            {"suspect_files": ["tools/foo.py"], "confidence": 0.9, "recommendation": "patch"},
            {"files": [{"path": "tools/foo.py", "old_string": "old", "new_string": "new"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
        )
        assert res["applied"] is True
        assert res["outcome"].startswith("applied_verified_committed")
        # worktree cleaned but branch KEPT
        assert cleaned == [True]
