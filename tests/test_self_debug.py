# CUI // SP-CTI
"""Unit tests: phantom worktree recovery in self_debug._heuristic_diagnosis.

Phantom worktree scenario: cwd_exists=False, worktree_registered=True.
The directory no longer exists on disk but git still shows the path in
`git worktree list`. Expected behaviour:
  - _heuristic_diagnosis recommends rebuild_worktree (not quarantine/patch)
  - patch_hint mentions prune (not reset/rmtree)
  - remediate_missing_worktree calls `git worktree prune`, not `git reset`
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from tools.workflow.self_debug import _heuristic_diagnosis, _traceback_diagnosis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _phantom_snap(**overrides):
    """Build a snapshot dict for the phantom worktree edge case."""
    base = {
        "cwd_exists": False,
        "worktree_registered": True,
        "is_worktree_path": True,
        "task_id": "fake-task-abc",
        "cwd": "/fake/.tmp/worktrees/fake-task-abc",
        "reason": "coherence check failed: tools/manifest.md not found",
    }
    base.update(overrides)
    return base


def _fake_proc(returncode=0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = ""
    r.stderr = ""
    return r


# ---------------------------------------------------------------------------
# _heuristic_diagnosis — phantom worktree branch
# ---------------------------------------------------------------------------

class TestPhantomWorktreeHeuristic:
    """_heuristic_diagnosis: cwd_exists=False, worktree_registered=True."""

    def test_recommendation_is_rebuild_worktree(self):
        diag = _heuristic_diagnosis(_phantom_snap())
        assert diag["recommendation"] == "rebuild_worktree", (
            f"Expected rebuild_worktree, got {diag['recommendation']!r}"
        )

    def test_patch_hint_mentions_prune_not_reset(self):
        """Hint should point at git prune, not git reset or rmtree."""
        diag = _heuristic_diagnosis(_phantom_snap())
        hint = (diag.get("patch_hint") or "").lower()
        assert "prune" in hint, (
            f"patch_hint should mention 'prune'; got: {diag.get('patch_hint')!r}"
        )
        assert "reset" not in hint, (
            f"patch_hint must NOT mention 'reset'; got: {diag.get('patch_hint')!r}"
        )

    def test_confidence_high(self):
        """Phantom worktree is a well-understood pattern — confidence >= 0.9."""
        diag = _heuristic_diagnosis(_phantom_snap())
        assert diag.get("confidence", 0) >= 0.9, (
            f"Expected confidence >= 0.9, got {diag.get('confidence')}"
        )

    def test_source_is_heuristic(self):
        diag = _heuristic_diagnosis(_phantom_snap())
        assert diag.get("_source") == "heuristic"

    def test_worktree_registered_true_does_not_suppress_rebuild(self):
        """cwd_exists=False fires rebuild_worktree regardless of registration."""
        diag_phantom = _heuristic_diagnosis(_phantom_snap(worktree_registered=True))
        diag_orphan  = _heuristic_diagnosis(
            _phantom_snap(cwd_exists=True, worktree_registered=False)
        )
        assert diag_phantom["recommendation"] == "rebuild_worktree"
        assert diag_orphan["recommendation"] == "rebuild_worktree"

    def test_cwd_exists_false_takes_priority_over_reason_text(self):
        """cwd_exists=False branch fires even when reason text looks like timeout."""
        diag = _heuristic_diagnosis(
            _phantom_snap(reason="timeout exceeded dispatch budget")
        )
        assert diag["recommendation"] == "rebuild_worktree", (
            "cwd_exists=False must override the timeout heuristic branch"
        )


# ---------------------------------------------------------------------------
# _heuristic_diagnosis — traceback localization (kills the conf-0.30 cliff)
# ---------------------------------------------------------------------------

_ASSERT_TB = (
    "Traceback (most recent call last):\n"
    '  File "tools/workflow/foo.py", line 42, in do_thing\n'
    "    assert x == y\n"
    "AssertionError: x != y\n"
)
_IMPORT_TB = (
    "Traceback (most recent call last):\n"
    '  File "tools/workflow/bar.py", line 7, in <module>\n'
    "    import nope\n"
    "ModuleNotFoundError: No module named 'nope'\n"
)
_TIMEOUT_TB = (
    "Traceback (most recent call last):\n"
    '  File "tools/net/client.py", line 88, in fetch\n'
    "    sock.recv()\n"
    "TimeoutError: timed out\n"
)
_EXTERNAL_ONLY_TB = (
    "Traceback (most recent call last):\n"
    '  File "/usr/lib/python3.11/site-packages/lib/thing.py", line 10, in run\n'
    "    do()\n"
    "ValueError: bad\n"
)


def _tb_snap(reason):
    """Snapshot that bypasses the infra branches and carries a traceback."""
    return {
        "cwd_exists": True,
        "is_worktree_path": False,
        "worktree_registered": True,
        "task_id": "tb-task",
        "reason": reason,
    }


class TestTracebackLocalization:
    """When a traceback yields an in-repo suspect, return a real diagnosis."""

    def test_assertion_error_routes_to_patch(self):
        diag = _heuristic_diagnosis(_tb_snap(_ASSERT_TB))
        assert diag["recommendation"] == "patch"
        assert diag["suspect_files"] == ["tools/workflow/foo.py:42"]
        assert "AssertionError" in diag["root_cause"]
        assert "do_thing" in diag["root_cause"]
        assert diag["_source"] == "heuristic_traceback"

    def test_confidence_in_calibrated_band(self):
        diag = _heuristic_diagnosis(_tb_snap(_ASSERT_TB))
        assert 0.55 <= diag["confidence"] <= 0.65
        # Below the 0.85 auto-apply bar → routes to a card, not applied blind.
        assert diag["confidence"] < 0.85
        # Above the old conf-0.30 floor.
        assert diag["confidence"] > 0.30

    def test_import_error_routes_to_patch(self):
        diag = _heuristic_diagnosis(_tb_snap(_IMPORT_TB))
        assert diag["recommendation"] == "patch"
        assert diag["suspect_files"] == ["tools/workflow/bar.py:7"]
        assert "ModuleNotFoundError" in diag["root_cause"]

    def test_timeout_error_routes_to_quarantine(self):
        diag = _heuristic_diagnosis(_tb_snap(_TIMEOUT_TB))
        assert diag["recommendation"] == "quarantine"
        assert diag["suspect_files"] == ["tools/net/client.py:88"]
        assert 0.55 <= diag["confidence"] <= 0.65

    def test_external_only_traceback_falls_through_to_unknown(self):
        """No first-party suspect → keep the conf-0.30 manual-review fallback."""
        diag = _heuristic_diagnosis(_tb_snap(_EXTERNAL_ONLY_TB))
        assert diag["confidence"] == 0.30
        assert diag["recommendation"] == "quarantine"
        assert diag["_source"] == "heuristic"

    def test_no_traceback_falls_through_to_unknown(self):
        diag = _heuristic_diagnosis(_tb_snap("some unstructured failure blurb"))
        assert diag["confidence"] == 0.30
        assert diag["_source"] == "heuristic"

    def test_infra_rules_take_priority_over_traceback(self):
        """A traceback in the reason must NOT override cwd_exists=False."""
        snap = _tb_snap(_ASSERT_TB)
        snap["cwd_exists"] = False
        diag = _heuristic_diagnosis(snap)
        assert diag["recommendation"] == "rebuild_worktree"
        assert diag["_source"] == "heuristic"

    def test_traceback_from_log_tail(self):
        """Traceback present only in log_tail is still parsed."""
        snap = _tb_snap("opaque reason")
        snap["log_tail"] = _ASSERT_TB.splitlines()
        diag = _traceback_diagnosis(snap)
        assert diag is not None
        assert diag["suspect_files"] == ["tools/workflow/foo.py:42"]

    def test_traceback_diagnosis_returns_none_without_exception(self):
        assert _traceback_diagnosis(_tb_snap("no traceback here")) is None


# ---------------------------------------------------------------------------
# remediate_missing_worktree — must call git worktree prune, not git reset
# ---------------------------------------------------------------------------

class TestRemediateMissingWorktreeCallsPrune:
    """remediate_missing_worktree must use git worktree prune, never git reset."""

    def test_prune_is_called(self, tmp_path):
        """subprocess.run must receive ['git', 'worktree', 'prune']."""
        wt_dir = tmp_path / ".tmp" / "worktrees" / "my-task-abc"
        wt_dir.mkdir(parents=True)

        calls_seen: list = []

        def fake_run(cmd, **kwargs):
            calls_seen.append(list(cmd))
            return _fake_proc(returncode=0)

        import tools.workflow.auto_remediate as ar
        with patch.object(ar, "subprocess") as mock_sp:
            mock_sp.run.side_effect = fake_run
            ok, msg = ar.remediate_missing_worktree(str(wt_dir), "my-task-abc")

        prune_calls = [c for c in calls_seen if "worktree" in c and "prune" in c]
        assert prune_calls, (
            f"`git worktree prune` must be called; all subprocess calls:\n{calls_seen}"
        )

    def test_reset_is_not_called(self, tmp_path):
        """git reset must NOT appear in the phantom worktree cleanup sequence."""
        wt_dir = tmp_path / ".tmp" / "worktrees" / "my-task-abc"
        wt_dir.mkdir(parents=True)

        calls_seen: list = []

        def fake_run(cmd, **kwargs):
            calls_seen.append(list(cmd))
            return _fake_proc(returncode=0)

        import tools.workflow.auto_remediate as ar
        with patch.object(ar, "subprocess") as mock_sp:
            mock_sp.run.side_effect = fake_run
            ar.remediate_missing_worktree(str(wt_dir), "my-task-abc")

        reset_calls = [c for c in calls_seen if "reset" in c]
        assert not reset_calls, (
            f"`git reset` must NOT be called for phantom worktree; got: {reset_calls}"
        )

    def test_returns_success_and_message_mentions_prune(self, tmp_path):
        """Return value: ok=True, message mentions pruning."""
        wt_dir = tmp_path / ".tmp" / "worktrees" / "my-task-abc"
        wt_dir.mkdir(parents=True)

        import tools.workflow.auto_remediate as ar
        with patch.object(ar, "subprocess") as mock_sp:
            mock_sp.run.return_value = _fake_proc(returncode=0)
            ok, msg = ar.remediate_missing_worktree(str(wt_dir), "my-task-abc")

        assert ok, f"Expected ok=True, got msg={msg!r}"
        assert "prune" in msg.lower(), (
            f"Return message should mention 'prune'; got: {msg!r}"
        )
