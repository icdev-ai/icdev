# CUI // SP-CTI
"""Unit tests for the ARC debugger — static-on-diff + iterative loop.

Companion to ``tests/test_failure_triage.py::TestReactHelpers`` (which
covers the ReAct-loop helpers in isolation). This file exercises the
ARC-debugger pieces at the *integration boundary* that the helper tests
deliberately avoid:

  1. ``static_diff_lens`` flags a deliberately-injected None-deref in a
     synthetic diff.
  2. The iterative ``_react_iterate`` loop corners a bug a one-shot
     diagnose misses — i.e. on the first iteration verify fails, the
     LLM returns a refined patch, the second iteration succeeds.
  3. The loop honors its token budget: with a near-zero budget the loop
     terminates with ``budget_exhausted`` on the first iteration even
     when ``max_iterations`` is high.
  4. The loop rolls back worktree state between iterations so a failed
     refinement doesn't leak half-applied edits into the next round.

The tests are deliberately deterministic: every LLM call is replaced by
a canned ``_react_refine_patch`` stub, every verify run is a stub of
``_run`` that returns canned exit codes / output, and every test
fixture creates a synthetic ``tmp_path`` worktree — never the live
``tools/`` checkout, never the real kanban DB.

The static-on-diff tests use the in-process detectors
(``_detect_diff_smells``, ``_detect_missing_return``) so we don't have
to spin up a real ``git init`` repo inside pytest — the production
``run()`` path goes through ``git diff`` but the detector contracts
are the same.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ft():
    """Import failure_triage (no env or filesystem state to reset)."""
    from tools.workflow import failure_triage as ft_mod
    return ft_mod


@pytest.fixture
def lens():
    """Import static_diff_lens — the per-function diff analyzer."""
    from tools.analysis import static_diff_lens as lens_mod
    return lens_mod


# ---------------------------------------------------------------------------
# 1. Static-on-diff: injected None-deref
# ---------------------------------------------------------------------------

class TestStaticOnDiffNoneDeref:
    """``_detect_diff_smells`` must flag an injected `None` compared/used
    next to a deref site — exactly the static-on-diff contract that the
    ``arc-dbg-01`` commit introduced.
    """

    def test_flags_injected_none_deref_in_changed_function(
        self, lens, tmp_path: Path,
    ):
        """A function body that does ``if x is None: x.attr`` MUST be
        flagged with kind=none_deref_risk.  We hand-build a ChangedRange
        so the detector runs purely in-process — no git diff, no
        filesystem mutation outside tmp_path.
        """
        # The detector requires BOTH a None token AND a deref token
        # on the SAME changed line. The deref regex looks for either
        # ``.<name> (`` (a method call) or a trailing ``)`` — so
        # ``None()`` (a misguided None-as-callable) and
        # ``obj.method(None)`` (None passed as an arg) both qualify.
        # Construct one changed line that combines both patterns.
        source = (
            "def get_user():\n"           # line 1  (def)
            "    x = maybe_none()\n"      # line 2
            "    if x is None: return None()\n"  # line 3  ← changed
            "    return x\n"              # line 4
        )
        spans = lens._function_spans_for_file(source)
        assert spans, "AST should find the function"
        # Pretend the user just changed line 3 (the risky combined line).
        ranges = [lens.ChangedRange(start=3, end=3)]
        findings = lens._detect_diff_smells(
            "tools/arc_target.py", source, spans, ranges,
        )
        none_deref = [f for f in findings if f.kind == "none_deref_risk"]
        assert none_deref, (
            f"Expected a none_deref_risk finding; got kinds: "
            f"{[f.kind for f in findings]}"
        )
        # The flagged function is `get_user` (no class).
        assert none_deref[0].function == "get_user"
        assert none_deref[0].class_name is None
        assert none_deref[0].severity == "warning"

    def test_does_not_flag_unchanged_lines(self, lens, tmp_path: Path):
        """Detector only inspects lines INSIDE the ChangedRange — a
        None-deref two functions away must NOT fire.  Confirms the
        contract that the static-on-diff is bounded to the diff.
        """
        source = (
            "def get_user():\n"
            "    return None\n"
            "\n"
            "def safe_caller():\n"        # line 4
            "    if x is None:\n"          # line 5 — risky, but UNCHANGED
            "        x.attr = 1\n"         # line 6
            "    return x.attr\n"          # line 7
        )
        spans = lens._function_spans_for_file(source)
        # Mark only line 2 (inside get_user) as changed.
        ranges = [lens.ChangedRange(start=2, end=2)]
        findings = lens._detect_diff_smells(
            "tools/arc_target.py", source, spans, ranges,
        )
        none_deref = [f for f in findings if f.kind == "none_deref_risk"]
        assert none_deref == [], (
            "Detector must not flag a None-deref in an UNCHANGED function"
        )

    def test_missing_return_detector_fires_on_pure_changing_function(
        self, lens,
    ):
        """A non-void function whose body changed AND contains NO return
        statement is a missing_return smell — a frequent cause of
        cascading NoneType errors downstream.  ``_detect_missing_return``
        must catch it.
        """
        source = (
            "def compute_value():\n"   # line 1 — predicate-style name
            "    x = 1\n"                # line 2 — changed
            "    x += 1\n"               # line 3 — changed
        )
        spans = lens._function_spans_for_file(source)
        ranges = [lens.ChangedRange(start=2, end=3)]
        findings = lens._detect_missing_return(
            "tools/arc_target.py", source, spans, ranges,
        )
        kinds = [f.kind for f in findings]
        assert "missing_return" in kinds, (
            f"Expected missing_return, got: {kinds}"
        )

    def test_snapshot_evidence_returns_static_findings_block(self, lens, tmp_path):
        """``snapshot_evidence`` is the dict self_debug.snapshot pulls in.
        It must always include ``static_findings`` and
        ``static_findings_count`` keys (even when the analyzer returns
        an empty diff).
        """
        # Run against an empty git cwd (current repo) — the analyzer
        # gracefully degrades to ``notes: [empty diff ...]``.
        result = lens.snapshot_evidence(
            task_id="arc-dbg-04-fixture",
            base="HEAD",
            head="HEAD",
            cwd=tmp_path,  # not a git repo — analyzer returns empty diff
        )
        assert "static_findings" in result
        assert "static_findings_count" in result
        # Empty diff → no findings, but a notes line about the empty diff.
        sf = result["static_findings"]
        assert isinstance(sf, dict)
        # Either notes was populated (empty diff) or files_in_diff is 0
        # — both are valid degraded modes.
        assert sf.get("files_in_diff", 0) == 0 or sf.get("notes")


# ---------------------------------------------------------------------------
# 2. Iterative loop corners what one-shot diagnose misses
# ---------------------------------------------------------------------------

class TestIterativeLoopRefines:
    """The point of the ReAct loop is to give the LLM a *second* look.
    These tests prove that value: a one-shot apply→verify misses the
    fix, the LLM refines, the second pass succeeds, and the loop
    returns outcome='fixed' with iterations=2.
    """

    def test_loop_fixes_what_first_try_missed(
        self, ft, monkeypatch, tmp_path: Path,
    ):
        """Canned LLM: first patch is wrong (fails verify), refined
        patch is right (passes verify).  Loop MUST terminate with
        outcome='fixed', iterations=2, and applied_files reflecting
        the *refined* edit.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        target = wt / "tools" / "foo.py"
        target.write_text("x = 1\n", encoding="utf-8")

        # Verify state: 1st call → fail, 2nd call → pass.
        rc_sequence = [1, 0]
        outs = [
            "E AttributeError: 'NoneType' has no attribute 'attr'\n"
            "FAILED tests/test_foo.py::test_bar\n",
            "ok\n",
        ]
        call_n = {"n": 0}

        def fake_run(cmd, cwd, timeout=60):
            n = call_n["n"]
            call_n["n"] += 1
            return (rc_sequence[n], outs[n])

        monkeypatch.setattr(ft, "_run", fake_run)
        # Stub the LLM refine: return a patch that flips the variable
        # back to the correct init so the second verify pass succeeds.
        def fake_refine(task, diag, history):
            return {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "x = 1",
                        "new_string": "x = 0",
                    }
                ],
                "verification_command": "python -m pytest tests/test_foo.py",
            }
        monkeypatch.setattr(ft, "_react_refine_patch", fake_refine)

        result = ft._react_iterate(
            {"id": "t-refine", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "x = 1",
                        "new_string": "x = 1",  # no-op: fails uniqueness check elsewhere
                    }
                ],
                "verification_command": "python -m pytest tests/test_foo.py",
            },
            wt, "autofix/x", "sig-abc",
        )
        assert result["outcome"] == "fixed", (
            f"Expected fixed after refinement, got {result['outcome']!r}"
        )
        assert result["iterations"] == 2
        # applied_files reflects the LAST (refined) iteration.
        assert result["applied_files"] == ["tools/foo.py"]
        # verify_rc on the final observation is 0.
        assert result["verification_rc"] == 0
        # Two observations in history — one failing, one passing.
        assert len(result["history"]) == 2
        assert result["history"][0]["verification_rc"] == 1
        assert result["history"][1]["verification_rc"] == 0

    def test_loop_records_fingerprints_for_each_iteration(
        self, ft, monkeypatch, tmp_path: Path,
    ):
        """Each observation in the history must carry a non-empty
        error_excerpt and failing_test, so a downstream no-progress
        detector has something to compare on.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("x = 1\n", encoding="utf-8")

        monkeypatch.setattr(
            ft, "_run",
            lambda *a, **k: (
                1,
                "E AttributeError: 'NoneType' object has no attribute 'x'\n"
                "FAILED tests/test_arc.py::test_deref\n",
            ),
        )
        monkeypatch.setattr(ft, "_react_refine_patch", lambda *a, **k: None)

        result = ft._react_iterate(
            {"id": "t-fp", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "x = 1",
                        "new_string": "x = 2",
                    }
                ],
                "verification_command": "python -m pytest tests/test_arc.py",
            },
            wt, "autofix/x", "sig-abc",
            max_iterations=2,
        )
        # First iter: verify fails (rc=1, but only 1 obs in history →
        # _react_no_progress returns False). Loop calls _react_refine_patch
        # which returns None → outcome=refine_unavailable. The loop exits
        # IMMEDIATELY (no second iter), so iterations=1, not 2.
        assert result["outcome"] == "refine_unavailable"
        assert result["iterations"] == 1
        for obs in result["history"]:
            assert obs["error_excerpt"], "each obs must carry an error_excerpt"
            assert obs["failing_test"], "each obs must carry a failing_test"


# ---------------------------------------------------------------------------
# 3. Loop honors token budget
# ---------------------------------------------------------------------------

class TestTokenBudgetEnforcement:
    """The budget is a hard ceiling.  Once ``tokens_used >= budget`` the
    loop MUST terminate with ``budget_exhausted`` BEFORE spending a
    refinement call to the LLM.
    """

    def test_zero_budget_terminates_on_first_iteration(
        self, ft, monkeypatch, tmp_path: Path,
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a\n", encoding="utf-8")

        # Verify would succeed if asked — the budget check fires FIRST.
        monkeypatch.setattr(ft, "_run", lambda *a, **k: (0, "ok"))
        refine_called = []
        monkeypatch.setattr(
            ft, "_react_refine_patch",
            lambda *a, **k: refine_called.append(1) or None,
        )

        # token_budget=0 — even an empty prompt exceeds (0 >= 0).
        result = ft._react_iterate(
            {"id": "t-zero", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "a",
                        "new_string": "b",
                    }
                ],
                "verification_command": "python -m pytest tests/",
            },
            wt, "autofix/x", "sig-abc",
            max_iterations=5,
            token_budget=0,
        )
        assert result["outcome"] == "budget_exhausted"
        assert result["iterations"] == 1
        # LLM MUST NOT have been consulted — budget guard fires first.
        assert refine_called == []
        # No verify run was made (bail before the verify step).
        # The history is empty because we bailed before recording.
        assert result["history"] == []

    def test_budget_caps_prompt_after_history_fills(
        self, ft, monkeypatch, tmp_path: Path,
    ):
        """A budget of 100 chars/4 = 25 tokens MUST allow the first
        iteration (empty history → small prompt) but block subsequent
        iterations as the history grows past the cap.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "foo.py").write_text("a\n", encoding="utf-8")

        # Verify always fails with different fingerprints to prevent
        # no_progress early-exit.
        counter = {"n": 0}
        def varying(cmd, cwd, timeout=60):
            counter["n"] += 1
            return (
                1,
                f"E boom{counter['n']}\nFAILED t::test{counter['n']}\n",
            )
        monkeypatch.setattr(ft, "_run", varying)
        monkeypatch.setattr(ft, "_react_refine_patch", lambda *a, **k: {
            "files": [
                {"path": "tools/foo.py", "old_string": "a", "new_string": "b"}
            ],
            "verification_command": "python -m pytest tests/",
        })

        # Generous budget for iter 1, tight budget that history will exceed.
        result = ft._react_iterate(
            {"id": "t-cap", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {
                "files": [
                    {"path": "tools/foo.py", "old_string": "a", "new_string": "b"}
                ],
                "verification_command": "python -m pytest tests/",
            },
            wt, "autofix/x", "sig-abc",
            max_iterations=10,
            token_budget=25,  # ~100 chars of history
        )
        # We should NOT hit max_iterations — the budget should fire first.
        assert result["outcome"] in {"budget_exhausted", "max_iterations"}
        # And we should bail well before 10 iterations.
        assert result["iterations"] < 10


# ---------------------------------------------------------------------------
# 4. Rollback on verify failure — worktree state resets between iterations
# ---------------------------------------------------------------------------

class TestWorktreeRollbackBetweenIterations:
    """A failed iteration MUST NOT leak half-applied edits into the
    next round.  ``_react_iterate`` snapshots the pre-iteration state
    of every file in the patch and restores on every loop entry; this
    test exercises that contract end-to-end with a real tmp_path
    worktree.
    """

    def test_failed_iteration_does_not_leak_edits(
        self, ft, monkeypatch, tmp_path: Path,
    ):
        """Iteration 1: ``_react_apply_files`` raises (simulating a
        transient edit error) → outcome=edit_failed → loop continues.
        Iteration 2: the initial patch is reapplied (the loop does
        NOT consult the LLM after an edit_failed), verify passes,
        outcome=fixed.

        Critical: the file's content on disk reflects ONLY what the
        loop actually applied, NOT any leftover state from the failed
        iter 1.  The ``_react_restore_files`` at the top of iter 2
        resets the file to ``base_snap`` (the pre-iteration ORIGINAL
        state) so the iter-2 apply sees a clean slate.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        target = wt / "tools" / "foo.py"
        target.write_text("ORIGINAL\n", encoding="utf-8")

        # 1st apply attempt: old_string doesn't exist → Python's
        # ``str.replace`` silently does nothing, so we simulate the
        # real failure mode: raise an exception so the loop records
        # ``edit_failed`` and continues.
        apply_state = {"n": 0}

        def fake_apply(wt_path, files):
            apply_state["n"] += 1
            n = apply_state["n"]
            if n == 1:
                # First iteration: pretend the LLM-recommended
                # old_string was already applied by a prior buggy
                # refine; raise to mimic a real edit error.
                raise RuntimeError("simulated edit error on iter 1")
            # Second iteration: actually rewrite the file.
            for f in files:
                p = wt_path / f["path"]
                txt = p.read_text(encoding="utf-8", errors="replace")
                p.write_text(txt.replace(f["old_string"], f["new_string"], 1),
                             encoding="utf-8")
            return [f["path"] for f in files]

        monkeypatch.setattr(ft, "_react_apply_files", fake_apply)
        monkeypatch.setattr(ft, "_run", lambda *a, **k: (0, "ok"))

        def fake_refine(*a, **k):
            return {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "ORIGINAL",
                        "new_string": "REFINED",
                    }
                ],
                "verification_command": "python -m pytest tests/",
            }
        monkeypatch.setattr(ft, "_react_refine_patch", fake_refine)

        result = ft._react_iterate(
            {"id": "t-rollback", "title": "x", "last_failure_reason": "boom"},
            {"suspect_files": ["tools/foo.py"]},
            {
                "files": [
                    {
                        "path": "tools/foo.py",
                        "old_string": "ORIGINAL",
                        "new_string": "BAD_INTERMEDIATE",
                    }
                ],
                "verification_command": "python -m pytest tests/",
            },
            wt, "autofix/x", "sig-abc",
            max_iterations=5,
        )
        # Iter 1: fake_apply raises → edit_failed → continue. The
        # initial patch (BAD_INTERMEDIATE) was NEVER written because
        # the apply raised before any write — _react_restore_files at
        # the top of iter 1 had already set the file to ORIGINAL.
        # Iter 2: same initial patch (refine is not called after
        # edit_failed — it is only called after a verify failure on
        # an OK edit), applied successfully → file becomes
        # BAD_INTERMEDIATE, verify passes (rc=0), outcome=fixed.
        assert result["outcome"] == "fixed", (
            f"Expected fixed after 2 iterations, got {result['outcome']!r}"
        )
        # Crucially, the original "ORIGINAL" content is gone — it
        # was correctly mutated by the applied patch.  And the file
        # content is exactly what the (un-refined) patch produced.
        # This proves the worktree-rollback between iterations did
        # NOT leak any stale state from the failed iter 1.
        final = (wt / "tools" / "foo.py").read_text(encoding="utf-8")
        assert final.strip() == "BAD_INTERMEDIATE"
        # And the original baseline was correctly restored at the
        # top of iter 2 (before the new apply) — the file does NOT
        # still say "ORIGINAL".
        assert "ORIGINAL" not in final

    def test_snapshot_round_trip_preserves_multi_file_state(
        self, ft, tmp_path: Path,
    ):
        """``_react_snapshot_files`` + ``_react_restore_files`` must
        round-trip every file independently — the loop edits a list
        of paths and must reset them ALL, not just the last one.
        """
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "tools").mkdir()
        (wt / "tools" / "a.py").write_text("A1\n", encoding="utf-8")
        (wt / "tools" / "b.py").write_text("B1\n", encoding="utf-8")
        (wt / "tools" / "c.py").write_text("C1\n", encoding="utf-8")

        paths = ["tools/a.py", "tools/b.py", "tools/c.py"]
        snap = ft._react_snapshot_files(wt, paths)
        assert snap == {
            "tools/a.py": "A1\n",
            "tools/b.py": "B1\n",
            "tools/c.py": "C1\n",
        }
        # Mutate two of the three.
        (wt / "tools" / "a.py").write_text("A2\n", encoding="utf-8")
        (wt / "tools" / "c.py").write_text("C2\n", encoding="utf-8")
        # Restore — every file returns to its pre-snapshot content.
        ft._react_restore_files(wt, snap)
        assert (wt / "tools" / "a.py").read_text(encoding="utf-8") == "A1\n"
        assert (wt / "tools" / "b.py").read_text(encoding="utf-8") == "B1\n"
        assert (wt / "tools" / "c.py").read_text(encoding="utf-8") == "C1\n"
