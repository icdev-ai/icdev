# CUI // SP-CTI
"""Tests for kanban dispatch harness reliability.

Of 182 recorded task failures on this board, 103 (57%) were the harness killing
or losing its own subprocess and only 4 (2%) were the model producing bad code.
These lock down the causes:

  * A second process running the reflex reaped the real scheduler's live tasks,
    because _running is a module global and therefore per-process.
  * The reaper treated "log file is empty" as "process is dead" at a 60s
    threshold, though a file-redirected LLM dispatch prints nothing for minutes.
  * An LLM guessed the build's kill timer and could set it to 60s.
  * Diffs were computed against a stale local ref, so a task's "changed files"
    contained hundreds of files it never touched.
  * last_failure_reason was overwritten with success text.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.genesis.reflexes import kanban as k


# --------------------------------------------------------------------------
# Thresholds — the numbers themselves are the fix
# --------------------------------------------------------------------------

def test_silent_dispatch_threshold_is_not_one_minute():
    """60s reaped live work: 31 of 182 failures were this."""
    assert k._SILENT_DISPATCH_THRESHOLD >= 600


def test_dispatch_budget_clears_the_observed_overruns():
    """Recorded kills were 902s and 911s against a 900s cap."""
    assert k.MAX_EXECUTION_SECONDS > 911


def test_timeout_ladder_is_monotonic():
    """A pattern-matched tier must never SHORTEN a task's budget."""
    assert k.MAX_EXECUTION_SECONDS <= k.MAX_EXECUTION_SECONDS_SCAN
    assert k.MAX_EXECUTION_SECONDS_SCAN <= k.MAX_EXECUTION_SECONDS_PYTEST


def test_decomposition_threshold_matches_its_own_documentation():
    """The docstring and the agent-facing coaching text both say 3."""
    assert k.MAX_FAILURES_BEFORE_DECOMPOSITION == 3
    assert k.MAX_FAILURES_BEFORE_DECOMPOSITION < 5, "must precede the fc>=5 circuit breaker"


# --------------------------------------------------------------------------
# The LLM must not be able to shorten the kill timer
# --------------------------------------------------------------------------

def test_nlp_hint_cannot_lower_the_budget(monkeypatch):
    """A task died with 'TIMEOUT after 60s (max 60s)' because a model said 60."""
    monkeypatch.setattr(k, "_nlp_extract_timeout_hint", lambda desc: 60)

    class _Row(dict):
        pass

    class _Conn:
        def execute(self, *a, **kw):
            class _R:
                @staticmethod
                def fetchone():
                    return _Row(description="do a thing in 60 seconds",
                                task_type="build", max_runtime_seconds=None)
            return _R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(k, "get_connection", lambda: _Conn())
    monkeypatch.setattr(k, "_detect_execution_anomalies", lambda **kw: {})

    assert k._get_task_timeout("some-task") >= k.MAX_EXECUTION_SECONDS


def test_nlp_hint_may_still_raise_the_budget(monkeypatch):
    monkeypatch.setattr(k, "_nlp_extract_timeout_hint", lambda desc: 3000)

    class _Conn:
        def execute(self, *a, **kw):
            class _R:
                @staticmethod
                def fetchone():
                    return dict(description="allow 50 minutes", task_type="build",
                                max_runtime_seconds=None)
            return _R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(k, "get_connection", lambda: _Conn())
    monkeypatch.setattr(k, "_detect_execution_anomalies", lambda **kw: {})
    assert k._get_task_timeout("some-task") == 3000


# --------------------------------------------------------------------------
# Cross-process safety — _running is per-process
# --------------------------------------------------------------------------

def test_reaper_is_a_noop_when_another_scheduler_owns_the_runner(monkeypatch):
    """A second process sees _running == {} and would reap every live task."""
    monkeypatch.setattr(k, "_foreign_scheduler_pid", lambda: 4242)

    def _boom():
        raise AssertionError("reaper must not open a connection when not the owner")

    monkeypatch.setattr(k, "get_connection", _boom)
    k._reap_stale_in_progress()  # must return without touching the DB


def test_startup_recovery_is_a_noop_when_another_scheduler_owns_the_runner(monkeypatch):
    """This sweep resets EVERY in_progress row — the worst one to run twice."""
    monkeypatch.setattr(k, "_foreign_scheduler_pid", lambda: 4242)
    monkeypatch.setattr(k, "_startup_recovery_done", False, raising=False)

    def _boom():
        raise AssertionError("startup recovery must not run when not the owner")

    monkeypatch.setattr(k, "get_connection", _boom)
    k._startup_recover_stale_in_progress()


def test_foreign_scheduler_pid_ignores_our_own_pid(tmp_path, monkeypatch):
    import os
    lock = tmp_path / ".tmp" / "kanban_scheduler.pid"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(k, "BASE_DIR", tmp_path)
    assert k._foreign_scheduler_pid() == 0


def test_foreign_scheduler_pid_ignores_a_dead_pid(tmp_path, monkeypatch):
    lock = tmp_path / ".tmp" / "kanban_scheduler.pid"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999999", encoding="utf-8")
    monkeypatch.setattr(k, "BASE_DIR", tmp_path)
    assert k._foreign_scheduler_pid() == 0


def test_heartbeat_daemon_no_longer_runs_the_reflex_in_process():
    """A health check must not become an executor."""
    src = Path(k.__file__).resolve().parents[3] / "tools" / "monitor" / "heartbeat_daemon.py"
    text = src.read_text(encoding="utf-8")
    assert "_kanban_run({}, None)" not in text, (
        "the in-process fallback reaps the real scheduler's live tasks"
    )


# --------------------------------------------------------------------------
# Heartbeat liveness
# --------------------------------------------------------------------------

class _HeartbeatConn:
    def __init__(self, value):
        self._value = value

    def execute(self, *a, **kw):
        value = self._value
        class _R:
            @staticmethod
            def fetchone():
                return {"last_heartbeat_at": value}
        return _R()


def test_fresh_heartbeat_is_not_stale():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    assert k._heartbeat_is_stale("t", _HeartbeatConn(now), 600) is False


def test_old_heartbeat_is_stale():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    assert k._heartbeat_is_stale("t", _HeartbeatConn(old), 600) is True


def test_absent_heartbeat_is_stale():
    """Pre-heartbeat tasks fall through to the age-based threshold."""
    assert k._heartbeat_is_stale("t", _HeartbeatConn(None), 600) is True


# --------------------------------------------------------------------------
# Diff baseline
# --------------------------------------------------------------------------

def test_base_ref_prefers_the_remote_tracking_ref(monkeypatch):
    monkeypatch.setattr(k, "_default_base_ref_cache", None, raising=False)
    monkeypatch.setattr(k, "_default_branch", lambda: "main")

    class _R:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: _R())
    assert k._default_base_ref() == "origin/main"


def test_base_ref_falls_back_to_local_when_no_remote(monkeypatch):
    """Air-gapped / origin-less checkouts must still work."""
    monkeypatch.setattr(k, "_default_base_ref_cache", None, raising=False)
    monkeypatch.setattr(k, "_default_branch", lambda: "main")

    class _R:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: _R())
    assert k._default_base_ref() == "main"


def test_no_diff_site_still_uses_the_bare_local_branch():
    """All four changed-file computations must use the remote-tracking ref."""
    src = Path(k.__file__).read_text(encoding="utf-8")
    assert "_default_branch()}.." not in src, (
        "a diff against the local ref picks up every commit main is behind"
    )


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git required")
def test_stale_local_ref_inflates_the_diff(tmp_path):
    """Reproduce the real defect: local main behind, branch cut from the remote."""
    def git(*args, cwd):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=30)

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git("init", "-q", "-b", "main", cwd=upstream)
    git("config", "user.email", "t@t.t", cwd=upstream)
    git("config", "user.name", "t", cwd=upstream)
    (upstream / "base.txt").write_text("base", encoding="utf-8")
    git("add", "-A", cwd=upstream)
    git("commit", "-qm", "base", cwd=upstream)

    clone = tmp_path / "clone"
    git("clone", "-q", str(upstream), str(clone), cwd=tmp_path)
    git("config", "user.email", "t@t.t", cwd=clone)
    git("config", "user.name", "t", cwd=clone)

    # Upstream moves on by 3 commits; the clone's LOCAL main stays put.
    for i in range(3):
        (upstream / f"other{i}.txt").write_text(str(i), encoding="utf-8")
        git("add", "-A", cwd=upstream)
        git("commit", "-qm", f"other{i}", cwd=upstream)
    git("fetch", "-q", "origin", cwd=clone)

    # A task branch cut from origin/main touching exactly one file.
    git("checkout", "-q", "-b", "kanban/t1", "origin/main", cwd=clone)
    (clone / "mine.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-qm", "task work", cwd=clone)

    def changed(base):
        r = git("diff", "--name-only", f"{base}...kanban/t1", cwd=clone)
        return [x for x in r.stdout.splitlines() if x.strip()]

    stale = changed("main")
    correct = changed("origin/main")
    assert correct == ["mine.py"], f"remote base should see only the task's file, got {correct}"
    assert len(stale) > len(correct), "local base must demonstrably over-report"
    assert "other0.txt" in stale, "the stale diff picks up unrelated upstream commits"


# --------------------------------------------------------------------------
# Failure-signal integrity
# --------------------------------------------------------------------------

def test_failure_clause_is_extracted_from_a_success_prefixed_narrative():
    reason = ("Verified (git-first): 57 file(s) changed on kanban/x "
              "| ruff found 3 issues")
    clause, narrative = k._split_failure_narrative(reason)
    assert clause == "ruff found 3 issues"
    assert "Verified (git-first)" in narrative, "the whole story is still kept"


def test_auto_remediated_prefix_is_not_a_failure_clause():
    reason = "AUTO-REMEDIATED (ruff_issues): ruff auto-fixed | coherence gate failed"
    clause, _ = k._split_failure_narrative(reason)
    assert clause == "coherence gate failed"


def test_an_all_success_narrative_is_marked_unclassified():
    """This is the 41% corruption — it must be visible, not silent."""
    reason = "Verified (git-first): 12 file(s) changed | AUTO-REMEDIATED: amended"
    clause, _ = k._split_failure_narrative(reason)
    assert clause.startswith("UNCLASSIFIED")


def test_plain_failure_passes_through_unchanged():
    clause, narrative = k._split_failure_narrative("TIMEOUT after 902s (max 900s)")
    assert clause == "TIMEOUT after 902s (max 900s)"
    assert narrative == "TIMEOUT after 902s (max 900s)"


def test_empty_reason_is_handled():
    assert k._split_failure_narrative(None) == ("", "")
    assert k._split_failure_narrative("") == ("", "")


# --------------------------------------------------------------------------
# Acceptance criteria reach the agent
# --------------------------------------------------------------------------

def test_prompt_includes_acceptance_criteria(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(k, "_ensure_prompt_dir", lambda: None)
    monkeypatch.setattr(k, "_get_resume_context", lambda tid: "")
    path = k._write_prompt_file({
        "id": "t1", "title": "T", "description": "D",
        "acceptance_criteria": "- tests pass\n- route returns 200",
    })
    text = Path(path).read_text(encoding="utf-8")
    assert "## Acceptance Criteria" in text
    assert "route returns 200" in text


def test_prompt_omits_the_section_when_criteria_are_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(k, "_ensure_prompt_dir", lambda: None)
    monkeypatch.setattr(k, "_get_resume_context", lambda tid: "")
    path = k._write_prompt_file({"id": "t2", "title": "T", "description": "D"})
    assert "## Acceptance Criteria" not in Path(path).read_text(encoding="utf-8")


def test_task_factory_persists_acceptance_criteria():
    src = Path(k.__file__).resolve().parents[3] / "tools" / "kanban" / "task_factory.py"
    text = src.read_text(encoding="utf-8")
    assert "acceptance_criteria" in text, (
        "seeded tasks must be able to carry the spec they are graded against"
    )
