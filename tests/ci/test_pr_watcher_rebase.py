# CUI // SP-CTI
"""kax-conflict-01: auto-rebase a DIRTY kanban PR before spending a resume.

Three behaviours are load-bearing and each is asserted here:

  (a) a behind-but-non-conflicting branch is rebased and pushed, with no resume
      injected and no human in the loop;
  (b) a genuinely conflicting branch aborts the rebase, is NOT force-pushed, and
      escalates exactly as it did before this feature existed;
  (c) the rebase path does not spend a resume — the two budgets are separate
      ledgers in `audit_trail`, verified against PostgreSQL rather than the
      SQLite fixture, because `_count_audit_actions` carries a PG-specific
      ``details::text`` branch and a multi-placeholder ``IN`` clause.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import pr_watcher as pw  # noqa: E402
from tools.kanban import rebase_recovery as rr  # noqa: E402


OLD_SHA = "1111111111111111111111111111111111111111"
NEW_SHA = "2222222222222222222222222222222222222222"


# ────────────────────────────────────────────────────────────────────────────
# Branch-ownership guard — requirement 4: never force-push a foreign branch
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "branch,task_id,expected",
    [
        ("kanban/kax-conflict-01", "kax-conflict-01", True),
        ("kanban/kax-conflict-01-r2", "kax-conflict-01", True),
        ("kanban/kax-conflict-01-r10", "kax-conflict-01", True),
        # A prefix match is NOT ownership: this is a different task's branch.
        ("kanban/kax-conflict-01-other", "kax-conflict-01", False),
        ("kanban/kax-conflict-012", "kax-conflict-01", False),
        ("main", "kax-conflict-01", False),
        ("origin/main", "kax-conflict-01", False),
        ("feat/hand-authored", "kax-conflict-01", False),
        ("kanban/some-other-task", "kax-conflict-01", False),
        ("", "kax-conflict-01", False),
    ],
)
def test_branch_ownership_guard(branch, task_id, expected):
    owned, _why = rr.branch_is_task_owned(branch, task_id)
    assert owned is expected


# ────────────────────────────────────────────────────────────────────────────
# rebase_and_push — the git mechanics, with a scripted runner
# ────────────────────────────────────────────────────────────────────────────


class _FakeGit:
    """Records every git invocation and answers from a scripted rebase result."""

    def __init__(self, *, rebase_rc=0, ahead="3", push_rc=0):
        self.calls = []
        self._rebase_rc = rebase_rc
        self._ahead = ahead
        self._push_rc = push_rc

    @staticmethod
    def _subcommand(cmd):
        """The git subcommand, skipping any leading `-c key=value` overrides."""
        i = 1
        while i < len(cmd) and cmd[i] == "-c":
            i += 2
        return cmd[i] if i < len(cmd) else ""

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        sub = self._subcommand(cmd)
        if sub == "config":
            # No identity configured — exercise the fallback path by default.
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if sub == "rebase":
            if "--abort" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(
                returncode=self._rebase_rc, stdout="",
                stderr="" if self._rebase_rc == 0
                else "CONFLICT (content): Merge conflict in tools/x.py",
            )
        if sub == "rev-parse":
            sha = OLD_SHA if any("refs/remotes/origin/" in a for a in cmd) else NEW_SHA
            return SimpleNamespace(returncode=0, stdout=sha + "\n", stderr="")
        if sub == "rev-list":
            return SimpleNamespace(returncode=0, stdout=self._ahead + "\n", stderr="")
        if sub == "push":
            return SimpleNamespace(
                returncode=self._push_rc, stdout="",
                stderr="" if self._push_rc == 0 else "stale info",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def cmds(self, sub):
        return [c for c in self.calls if self._subcommand(c) == sub]


def test_clean_rebase_force_pushes_with_lease_pinned_to_observed_sha():
    git = _FakeGit(rebase_rc=0, ahead="3")
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["pushed"] is True
    assert verdict["conflict"] is False
    assert verdict["commits_ahead"] == 3

    pushes = git.cmds("push")
    assert len(pushes) == 1
    push = pushes[0]
    # The lease pins the exact sha we read before rebasing, so a concurrent
    # push to the same kanban/<id> branch is rejected instead of clobbered.
    assert f"--force-with-lease=refs/heads/kanban/kax-conflict-01:{OLD_SHA}" in push
    assert "HEAD:refs/heads/kanban/kax-conflict-01" in push
    # The rebase ran in a scratch worktree, never in the repo root.
    add = git.cmds("worktree")[0]
    assert add[2] == "add" and "--detach" in add
    assert git.cmds("rebase"), "the rebase itself must have run"


def test_conflicting_rebase_aborts_and_never_pushes():
    git = _FakeGit(rebase_rc=1)
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["attempted"] is True
    assert verdict["pushed"] is False
    assert verdict["conflict"] is True
    assert "conflict" in verdict["reason"].lower()
    assert git.cmds("push") == []
    assert ["git", "rebase", "--abort"] in git.calls


def test_foreign_branch_is_refused_before_any_git_runs():
    git = _FakeGit()
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "main", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["pushed"] is False
    assert verdict["attempted"] is False
    assert "refused" in verdict["reason"]
    assert git.calls == []


def test_empty_result_is_not_pushed():
    """A rebase that replays to nothing would empty the PR — report, don't push."""
    git = _FakeGit(rebase_rc=0, ahead="0")
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["attempted"] is True
    assert verdict["pushed"] is False
    assert verdict["commits_ahead"] == 0
    assert git.cmds("push") == []


def test_dry_run_probes_but_never_pushes():
    git = _FakeGit(rebase_rc=0, ahead="2")
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git, dry_run=True,
    )
    assert verdict["attempted"] is True
    assert verdict["pushed"] is False
    assert git.cmds("push") == []


def test_rejected_lease_is_reported_not_swallowed():
    git = _FakeGit(rebase_rc=0, ahead="1", push_rc=1)
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["attempted"] is True
    assert verdict["pushed"] is False
    assert "rejected" in verdict["reason"]


def test_rebase_supplies_a_committer_identity_when_none_is_configured():
    """A rebase re-commits; a bare CI runner has no identity configured.

    Without this, git dies with `fatal: empty ident name`, the module reports
    it as a conflict, and a PR with NO conflict escalates to a human.
    """
    git = _FakeGit(rebase_rc=0, ahead="1")  # its `config` probe returns nothing
    verdict = rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert verdict["pushed"] is True
    rebase_cmd = git.cmds("rebase")[0]
    assert f"user.email={rr.FALLBACK_IDENTITY_EMAIL}" in rebase_cmd
    assert f"user.name={rr.FALLBACK_IDENTITY_NAME}" in rebase_cmd


def test_configured_identity_is_never_overridden():
    """A real identity must win — this tool does not rewrite who authored work."""

    class _Configured(_FakeGit):
        def __call__(self, cmd, **kwargs):
            if self._subcommand(cmd) == "config":
                self.calls.append(list(cmd))
                return SimpleNamespace(returncode=0, stdout="dev@example.com\n",
                                       stderr="")
            return super().__call__(cmd, **kwargs)

    git = _Configured(rebase_rc=0, ahead="1")
    rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    assert "-c" not in git.cmds("rebase")[0]


def test_scratch_worktree_is_always_cleaned_up():
    git = _FakeGit(rebase_rc=1)
    rr.rebase_and_push(
        "kax-conflict-01", "kanban/kax-conflict-01", base="main",
        repo_root=str(ROOT), runner=git,
    )
    removes = [c for c in git.calls if c[1:3] == ["worktree", "remove"]]
    assert removes, "the scratch worktree must be removed even on the conflict path"


# ────────────────────────────────────────────────────────────────────────────
# Watcher integration
# ────────────────────────────────────────────────────────────────────────────


class _AuditingConnection:
    """Minimal stand-in that records audit INSERTs and replays them to counts."""

    def __init__(self, tasks, audit_rows):
        self._tasks = tasks
        self._audit = audit_rows

    def execute(self, sql, params=()):
        upper = sql.upper()
        if upper.startswith("SELECT") and "KANBAN_TASKS" in upper:
            return SimpleNamespace(fetchall=lambda: self._tasks)
        if upper.startswith("SELECT") and "AUDIT_TRAIL" in upper:
            wanted = set(params[:-1])
            like = str(params[-1]).strip("%")
            hits = [
                r for r in self._audit
                if r["action"] in wanted and like in r["details"]
            ]
            return SimpleNamespace(fetchall=lambda: hits)
        if upper.startswith("INSERT INTO AUDIT_TRAIL"):
            self._audit.append({"action": params[3], "details": params[4]})
            return SimpleNamespace(fetchall=list)
        return SimpleNamespace(fetchall=list)

    def cursor(self):
        return SimpleNamespace(execute=lambda *a, **k: None, fetchone=lambda: None)

    def commit(self):
        pass

    def close(self):
        pass


def _dirty_pr_state(branch="kanban/task-dirty"):
    return {
        "state": "OPEN",
        "mergeable": "CONFLICTING",
        "number": 1300,
        "headRefName": branch,
        "baseRefName": "main",
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "Test"}],
    }


def _build(task_id, rebase_fn, *, audit_rows=None, config=None, branch=None,
           queue_log=None):
    pr_url = "https://github.com/o/r/pull/1300"
    tasks = [{
        "id": task_id, "title": "T", "description": "",
        "status": "pr_opened", "executor_url": pr_url,
    }]
    rows = audit_rows if audit_rows is not None else []
    conn = _AuditingConnection(tasks, rows)
    log = queue_log if queue_log is not None else []

    cfg = {
        "max_resume_cycles_per_task": 5,
        "auto_rebase_on_conflict": True,
        "max_rebase_attempts_per_task": 2,
        "link_prs_on_poll": False,
        "sibling_conflict_check": False,
        "auto_merge_enabled": False,
    }
    cfg.update(config or {})

    watcher = pw.PRWatcher(
        config=cfg,
        get_connection=lambda: conn,
        queue_message=lambda tid, text, sender="user": log.append(
            {"task_id": tid, "text": text}),
        fetch_state=lambda url, **kw: _dirty_pr_state(branch or f"kanban/{task_id}"),
        fetch_logs=lambda url, **kw: "",
        rebase_fn=rebase_fn,
        default_branch_resolver=lambda: "main",
        dry_run=False,
    )
    return watcher, rows, log


def _pushed(*_a, **_kw):
    return {"attempted": True, "pushed": True, "conflict": False,
            "reason": "rebased onto origin/main and force-pushed (3 commit(s))"}


def _conflicted(*_a, **_kw):
    return {"attempted": True, "pushed": False, "conflict": True,
            "reason": "rebase onto origin/main hit conflicts: CONFLICT in tools/x.py"}


def test_a_behind_branch_is_rebased_without_a_resume():
    """(a) The cheap recovery runs and no human/LLM resume is involved."""
    watcher, audit, queue = _build("task-dirty", _pushed)
    report = watcher.poll_once()

    action = report.actions[0]
    assert action.action == "rebase"
    assert action.classification == "merge_conflict"
    assert queue == [], "no resume context may be injected when a rebase recovers the PR"
    assert [r["action"] for r in audit] == ["pr_watcher.rebase"]


def test_b_conflicting_branch_escalates_and_is_not_force_pushed():
    """(b) A real conflict aborts, pushes nothing, and escalates as before."""
    pushes = []

    def _real_conflict(task_id, branch, base="main"):
        git = _FakeGit(rebase_rc=1)
        verdict = rr.rebase_and_push(
            task_id, branch, base=base, repo_root=str(ROOT), runner=git,
        )
        pushes.extend(git.cmds("push"))
        return verdict

    watcher, audit, queue = _build("task-dirty", _real_conflict)
    # Resume budget already exhausted, so the pre-existing behaviour is escalate.
    watcher._resume_cycle = lambda task_id, pr_url=None: 5  # noqa: SLF001
    report = watcher.poll_once()

    assert pushes == [], "a genuinely conflicting branch must never be force-pushed"
    action = report.actions[0]
    assert action.action == "escalate"
    assert "cap reached" in action.reason
    assert queue == []
    # The failed attempt is recorded (so the rebase cap is durable) but the
    # escalation is unchanged from pre-feature behaviour.
    assert "pr_watcher.rebase_failed" in [r["action"] for r in audit]


def test_b_conflicting_branch_still_resumes_when_budget_remains():
    """A real conflict below the cap falls through to the ordinary resume."""
    watcher, audit, queue = _build("task-dirty", _conflicted)
    report = watcher.poll_once()

    assert report.actions[0].action == "resume"
    assert len(queue) == 1
    assert "merge conflict" in queue[0]["text"].lower()


def test_c_rebase_does_not_spend_a_resume():
    """(c) A successful rebase leaves the resume budget untouched."""
    watcher, audit, queue = _build("task-dirty", _pushed)
    before = watcher._resume_cycle("task-dirty")  # noqa: SLF001
    report = watcher.poll_once()

    after = watcher._resume_cycle("task-dirty")  # noqa: SLF001
    assert before == 0 and after == 0, "a rebase must not increment the resume count"
    assert report.actions[0].resume_cycle == 0
    # …while it DOES increment its own, separate budget.
    assert watcher._rebase_attempts("task-dirty") == 1  # noqa: SLF001


def test_watcher_refuses_to_rebase_a_branch_it_does_not_own():
    called = []

    def _spy(*a, **kw):
        called.append(a)
        return _pushed()

    watcher, audit, queue = _build("task-dirty", _spy, branch="main")
    report = watcher.poll_once()

    assert called == [], "the watcher must not hand `main` to the rebase path"
    assert report.actions[0].action == "resume"
    assert [r["action"] for r in audit] == ["pr_watcher.resume"]


def test_watcher_stops_rebasing_after_the_configured_cap():
    called = []

    def _spy(*a, **kw):
        called.append(a)
        return _conflicted()

    prior = [
        {"action": "pr_watcher.rebase_failed",
         "details": json.dumps({"task_id": "task-dirty"})},
        {"action": "pr_watcher.rebase_failed",
         "details": json.dumps({"task_id": "task-dirty"})},
    ]
    watcher, audit, queue = _build(
        "task-dirty", _spy, audit_rows=prior,
        config={"max_rebase_attempts_per_task": 2},
    )
    report = watcher.poll_once()

    assert called == [], "the cap is a hard bound, not a suggestion"
    assert report.actions[0].action == "resume"


def test_auto_rebase_can_be_disabled_by_config():
    called = []
    watcher, audit, queue = _build(
        "task-dirty", lambda *a, **kw: called.append(a) or _pushed(),
        config={"auto_rebase_on_conflict": False},
    )
    report = watcher.poll_once()
    assert called == []
    assert report.actions[0].action == "resume"


def test_ci_failed_pr_never_takes_the_rebase_path():
    called = []

    def _spy(*a, **kw):
        called.append(a)
        return _pushed()

    watcher, audit, queue = _build("task-dirty", _spy)
    watcher._fetch_state = lambda url, **kw: {  # noqa: SLF001
        "state": "OPEN", "mergeable": "MERGEABLE", "number": 1,
        "headRefName": "kanban/task-dirty", "baseRefName": "main",
        "statusCheckRollup": [{"conclusion": "FAILURE", "name": "Test"}],
    }
    report = watcher.poll_once()
    assert called == []
    assert report.actions[0].classification == "ci_failed"
    assert report.actions[0].action == "resume"


# ────────────────────────────────────────────────────────────────────────────
# (a) + (b) against REAL git — a scripted runner only proves the module agrees
# with our model of git. These build a throwaway origin and let git decide.
# ────────────────────────────────────────────────────────────────────────────


def _git_real(*args, cwd, env):
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return (proc.stdout or "").strip()


@pytest.fixture
def drifted_repo(tmp_path, monkeypatch):
    """origin + clone with two branches behind main: one clean, one conflicting.

    Returns ``(work_dir, git, env, old_shas)``. Mirrors the real situation: the
    branches were cut, main moved on, and only one of them actually collides.

    The AMBIENT git identity is deliberately removed (empty global/system config
    files) so the module runs the way a bare CI runner runs it. Without that,
    a developer's `~/.gitconfig` silently supplies the committer identity a
    rebase needs, and `_identity_args` is never exercised locally — which is
    exactly how "fatal: empty ident name" reached CI. Setup commits carry their
    identity in explicit env vars, which the module does NOT inherit.
    """
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git not on PATH")

    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(leaked, raising=False)

    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git_real("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path, env=env)
    _git_real("clone", "-q", str(origin), str(work), cwd=tmp_path, env=env)

    def git(*args):
        return _git_real(*args, cwd=work, env=env)

    def write(name, text):
        (work / name).write_text(text, encoding="utf-8", newline="\n")

    write("shared.txt", "line1\nline2\nline3\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("push", "-q", "origin", "HEAD:refs/heads/main")

    # Behind main, but touches a file main never touches.
    git("checkout", "-qb", "kanban/smoke-ok")
    write("featureA.txt", "feature A\n")
    git("add", "-A")
    git("commit", "-qm", "feat A")
    git("push", "-q", "origin", "HEAD:refs/heads/kanban/smoke-ok")
    ok_old = git("rev-parse", "HEAD")

    # Behind main, and edits the very line main is about to edit.
    git("checkout", "-q", "main")
    git("checkout", "-qb", "kanban/smoke-conflict")
    write("shared.txt", "line1\nBRANCH-EDIT\nline3\n")
    git("add", "-A")
    git("commit", "-qm", "branch edit")
    git("push", "-q", "origin", "HEAD:refs/heads/kanban/smoke-conflict")
    conflict_old = git("rev-parse", "HEAD")

    git("checkout", "-q", "main")
    write("shared.txt", "line1\nMAIN-EDIT\nline3\n")
    git("add", "-A")
    git("commit", "-qm", "main moves ahead")
    git("push", "-q", "origin", "HEAD:refs/heads/main")
    git("fetch", "-q", "origin")

    return work, git, env, {"ok": ok_old, "conflict": conflict_old}


def test_a_real_git_drifted_branch_is_rebased_and_then_merges_cleanly(drifted_repo):
    """(a) The end state that matters: the PR merges with no human action."""
    work, git, env, old = drifted_repo

    verdict = rr.rebase_and_push(
        "smoke-ok", "kanban/smoke-ok", base="main", repo_root=str(work),
    )
    assert verdict["pushed"] is True, verdict["reason"]
    assert verdict["conflict"] is False
    assert verdict["old_sha"] == old["ok"], "the lease must pin the sha we observed"
    assert verdict["new_sha"] != old["ok"], "the branch should have been rewritten"

    git("fetch", "-q", "origin")
    assert git("rev-list", "--count", "origin/kanban/smoke-ok..origin/main") == "0"

    # `env` carries the fixture's own identity — the ambient one is deliberately
    # absent (see the fixture) so the module has to supply its own.
    merge = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "origin/kanban/smoke-ok"],
        cwd=str(work), capture_output=True, text=True, timeout=60, env=env,
    )
    subprocess.run(["git", "merge", "--abort"], cwd=str(work),
                   capture_output=True, timeout=60, env=env)
    assert merge.returncode == 0, (merge.stderr or merge.stdout)

    # …and the work is still there. A "clean merge" of an emptied branch would
    # also be green, which is why this assertion is not redundant.
    changed = git("log", "--name-only", "--pretty=format:",
                  "origin/main..origin/kanban/smoke-ok")
    assert "featureA.txt" in changed

    assert "icdev-rebase-" not in git("worktree", "list")


def test_b_real_git_conflicting_branch_is_aborted_and_remote_untouched(drifted_repo):
    """(b) A real conflict: aborted, nothing pushed, remote byte-identical."""
    work, git, _env, old = drifted_repo

    verdict = rr.rebase_and_push(
        "smoke-conflict", "kanban/smoke-conflict", base="main", repo_root=str(work),
    )
    assert verdict["conflict"] is True, verdict["reason"]
    assert verdict["pushed"] is False

    git("fetch", "-q", "origin")
    assert git("rev-parse", "origin/kanban/smoke-conflict") == old["conflict"]
    assert "icdev-rebase-" not in git("worktree", "list")


def test_real_git_refuses_to_touch_a_branch_it_does_not_own(drifted_repo):
    """Requirement 4 against real git: `main` is never force-pushed."""
    work, git, _env, _old = drifted_repo
    before = git("rev-parse", "origin/main")

    verdict = rr.rebase_and_push(
        "smoke-ok", "main", base="main", repo_root=str(work),
    )
    assert verdict["pushed"] is False
    assert "refused" in verdict["reason"]

    git("fetch", "-q", "origin")
    assert git("rev-parse", "origin/main") == before


# ────────────────────────────────────────────────────────────────────────────
# (c) against PostgreSQL — the ledger separation must hold on the real backend
# ────────────────────────────────────────────────────────────────────────────


class _NoCloseConn:
    """Keeps a live PG transaction open across the watcher's close() calls."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


_PG_TIER = os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


def _pg_connection():
    """The AMBIENT connection when it is really PostgreSQL, else None.

    Under the PG tier (``ICDEV_PYTEST_PG=1``, see tests/conftest.py and
    tests/pg_tier_allowlist.txt) a connection failure is raised, not skipped —
    a tier that silently skips is the false-confidence trap the tier exists to
    prevent. Outside it, the suite is pinned to SQLite and this test skips.
    """
    from tools.db.storage import get_connection

    if not _PG_TIER:
        if os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() not in (
            "postgresql", "postgres",
        ):
            return None
        conn = get_connection()
    else:
        # Re-assert the tier's own declaration. `ICDEV_STORAGE_BACKEND` is
        # process-global and several sibling test modules (e.g.
        # tests/kanban/test_des_audit_logger.py) pin it to "sqlite" at IMPORT
        # time, so merely being collected alongside them silently downgrades
        # this test to the backend it exists to NOT use. Restored after.
        prior = os.environ.get("ICDEV_STORAGE_BACKEND")
        os.environ["ICDEV_STORAGE_BACKEND"] = "postgresql"
        try:
            conn = get_connection()
        finally:
            if prior is None:
                os.environ.pop("ICDEV_STORAGE_BACKEND", None)
            else:
                os.environ["ICDEV_STORAGE_BACKEND"] = prior
    if getattr(conn, "_backend", "sqlite") != "postgresql":
        try:
            conn.close()
        except Exception:
            pass
        if _PG_TIER:
            raise AssertionError(
                "ICDEV_PYTEST_PG=1 but get_connection() returned a "
                f"{getattr(conn, '_backend', '?')} connection"
            )
        return None
    return conn


def test_c_resume_and_rebase_are_separate_ledgers_on_postgresql():
    """The two budgets must not alias each other on the PRIMARY backend.

    `_count_audit_actions` carries a PG-only ``details::text`` cast and builds a
    multi-placeholder ``IN`` clause; the SQLite fixture exercises neither, so it
    could report a green build while the query fails (and silently returns 0)
    against the backend that actually runs. Rows are inserted inside a
    transaction that is ROLLED BACK — `audit_trail` is append-only and must not
    accumulate test rows.
    """
    conn = _pg_connection()
    if conn is None:
        pytest.skip("suite pinned to SQLite — run under ICDEV_PYTEST_PG=1 for PG")

    task_id = "kax-pgtest-" + uuid.uuid4().hex[:10]
    seeded = [
        ("pr_watcher.resume", 1),
        ("pr_watcher.rebase", 1),
        ("pr_watcher.rebase_failed", 2),
    ]
    try:
        for action, n in seeded:
            for _ in range(n):
                conn.execute(
                    "INSERT INTO audit_trail "
                    "(event_type, actor, action, details) VALUES (%s, %s, %s, %s)",
                    ("hook_event_logged", "pr_watcher", action,
                     json.dumps({"task_id": task_id})),
                )

        watcher = pw.PRWatcher(
            config={"auto_rebase_on_conflict": True},
            get_connection=lambda: _NoCloseConn(conn),
            dry_run=True,
        )
        assert watcher._resume_cycle(task_id) == 1  # noqa: SLF001
        assert watcher._rebase_attempts(task_id) == 3  # noqa: SLF001
        # The decisive assertion: three rebase attempts left the resume budget
        # at one. If the rebase ledger aliased the resume ledger, this would be 4.
        assert watcher._resume_cycle(task_id) == 1  # noqa: SLF001
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
