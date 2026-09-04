# CUI // SP-CTI
"""A guard park whose branch EXISTS and is EMPTY, and whose worktree is CLEAN,
is removed through git and requeued -- and every other shape refuses
(kpr-stale-06).

Measured 2026-09-03: task-det-e9a2e3ea16 sat in ``validating``, parked by
``worktree-isolation-guard``, with a ``kanban/<id>`` branch ZERO commits ahead of
origin/main and a ``.tmp/worktrees/<id>`` directory. ``stranded_audit`` counted
it CLEAN, kpr-stale-05's orphan proof correctly REFUSED it (``branch_exists``,
``worktree_exists``), and nothing on any runtime path consumed it. Surveyed
over all 65 guard parks lifetime, 53 had their branch BEFORE the park -- the
shape is the rule, not the exception.

Hermetic: a sqlite board behind the same ``translate_sql`` the runtime uses,
plus a REAL temporary git repository (an origin and a clone, so ``origin/main``
exists and a push is possible) for the emptiness/cleanliness proofs. Nothing
here touches the real board or the real repo.

Each test pins one rule the card states:

  * an empty, clean, registered checkout is removed through ``git worktree
    remove`` (never rmtree), its LOCAL branch deleted, the row requeued -- and
    the intent is AUDITED before the first destructive step;
  * ONE uncommitted file refuses (``worktree_dirty``);
  * a branch ONE commit ahead refuses (``branch_ahead``);
  * an UNREGISTERED directory refuses -- both the never-registered kind and the
    partial-delete kind (a worktree whose ``.git`` file is gone, the live case);
  * an origin branch is NEVER deleted;
  * no audit row, no act (``unaudited_refused``);
  * the cap is SHARED with the orphan act;
  * ``no_branch`` refuses here, because that row is the orphan proof's.
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import translating  # noqa: E402
from tools.kanban import orphan_requeue as oq  # noqa: E402

GUARD = "worktree-isolation-guard"
PARK_REASON = (
    "worktree creation failed; refusing to build in the shared checkout "
    "(see the git worktree add failure logged above)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── the fake board ─────────────────────────────────────────────────────────


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            task_type TEXT,
            priority TEXT,
            status TEXT,
            updated_at TEXT,
            scheduled_at TEXT,
            branch_name TEXT,
            failure_count INTEGER DEFAULT 0,
            last_failure_at TEXT,
            last_failure_reason TEXT,
            last_heartbeat_at TEXT
        );
        CREATE TABLE kanban_status_transitions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            from_status TEXT,
            to_status TEXT,
            actor TEXT,
            reason TEXT,
            recorded_at TEXT
        );
        """
    )


@pytest.fixture()
def raw(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "board.db"))
    conn.row_factory = sqlite3.Row
    _schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def get_conn(raw):
    return lambda: translating(raw, unclosable=True)


def _task(raw, tid, *, status="validating"):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, updated_at, failure_count, last_failure_reason, branch_name) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, f"title {tid}", f"desc {tid}", "build", "high", status,
         _now().isoformat(), 2, "an earlier failure", f"kanban/{tid}"),
    )
    raw.commit()


_seq = [0]


def _park(raw, tid, *, actor=GUARD, reason=PARK_REASON, ago=timedelta(hours=1)):
    _seq[0] += 1
    raw.execute(
        "INSERT INTO kanban_status_transitions (id, task_id, from_status, "
        "to_status, actor, reason, recorded_at) VALUES (?,?,?,?,?,?,?)",
        (f"kst-{tid}-{_seq[0]}", tid, "scheduled", "validating", actor, reason,
         (_now() - ago).isoformat()),
    )
    raw.commit()


def _row(raw, tid) -> dict:
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


def _transitions(raw, tid) -> list:
    return [dict(r) for r in raw.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ? ORDER BY recorded_at",
        (tid,)).fetchall()]


def _findings(*ids) -> dict:
    """What ``stranded_audit.audit_stranded_tasks`` hands the reflex."""
    return {
        "default_branch": "main",
        "total": len(ids),
        "stranded": [],
        "orphan_validating": [],
        "validating_with_branch": [
            {"id": i, "status": "validating", "title": f"title {i}", "unmerged_commits": 0}
            for i in ids
        ],
        "clean_count": len(ids),
    }


# ── the real temporary git repository ──────────────────────────────────────


def _git(*args, cwd) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True)
    return proc.stdout


def _git_rc(*args, cwd) -> int:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True).returncode


@pytest.fixture()
def repo(tmp_path):
    """An origin (bare) and a clone of it. The clone has ``origin/main``, which
    is what ``_create_worktree`` bases every task branch on."""
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.invalid", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=seed)
    _git("commit", "-q", "-m", "seed", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-q", "-u", "origin", "main", cwd=seed)

    clone = tmp_path / "repo"
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    _git("config", "user.email", "t@example.invalid", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)
    return {"root": clone, "origin": origin}


@pytest.fixture()
def ctx(repo):
    root = repo["root"]
    return oq.GitContext(
        repo_root=root, default_branch="main",
        worktree_path_for=lambda tid: root / ".tmp" / "worktrees" / tid,
    )


def _add_worktree(ctx, tid) -> Path:
    """Exactly what ``_create_worktree`` runs, minus the 30s budget."""
    path = ctx.path_for(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", f"kanban/{tid}", str(path), "origin/main",
         cwd=ctx.repo_root)
    return path


def _branch_exists(ctx, ref) -> bool:
    return _git_rc("rev-parse", "--verify", "--quiet", ref, cwd=ctx.repo_root) == 0


def _registered(ctx, path) -> bool:
    listing = _git("worktree", "list", "--porcelain", cwd=ctx.repo_root)
    return any(oq._norm_path(e["path"]) == oq._norm_path(path)
               for e in oq.parse_worktree_list(listing) if "path" in e)


def _recorder():
    """An audit that records phases, and what the world looked like at INTENT."""
    rows = []

    def audit(phase, details):
        rows.append((phase, dict(details)))
        return len(rows)

    return rows, audit


def _act(findings, get_conn, ctx, **kw):
    rows, audit = _recorder()
    kw.setdefault("audit", audit)
    kw.setdefault("lease_state", lambda tid: "free")
    kw.setdefault("file_card", lambda spec: spec["id"])
    result = oq.act_on_empty_checkouts(findings, {"max_requeues_per_run": 10},
                                       get_conn=get_conn, ctx=ctx, **kw)
    return result, rows


# ── the act ────────────────────────────────────────────────────────────────


def test_empty_clean_checkout_is_removed_through_git_and_requeued(raw, get_conn, ctx):
    tid = "t-empty"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)
    assert path.exists() and _registered(ctx, path)

    existed_at_intent = {}

    rows, audit = _recorder()

    def audit_seeing_world(phase, details):
        if phase == "intent":
            existed_at_intent["worktree"] = path.exists()
            existed_at_intent["branch"] = _branch_exists(ctx, f"kanban/{tid}")
        return audit(phase, details)

    result, _ = _act(_findings(tid), get_conn, ctx, audit=audit_seeing_world)

    assert result["state"] == "acted"
    assert result["requeued"] == [tid]
    assert result["refused"] == []
    assert result["acts"] == [{"task_id": tid, "removed_worktree": str(path),
                               "deleted_branches": [f"kanban/{tid}"]}]

    # The world: worktree gone and unregistered, local branch gone.
    assert not path.exists()
    assert not _registered(ctx, path)
    assert not _branch_exists(ctx, f"kanban/{tid}")

    # The board: requeued through requeue_task, with the park quoted.
    row = _row(raw, tid)
    assert row["status"] == "scheduled"
    assert row["branch_name"] is None
    assert row["last_failure_reason"] is None
    assert row["failure_count"] == 2          # preserved, never reset
    last = _transitions(raw, tid)[-1]
    assert last["actor"] == "kanban_stranded_reflex"
    assert last["to_status"] == "scheduled"
    assert "empty_checkout" in last["reason"]
    assert "kpr-stale-06" in last["reason"]
    # requeue_task keeps 200 chars of a reason: the PARK is quoted first, so
    # the requeue stays attributable to it; the full park rides on the audit.
    assert GUARD in last["reason"]
    assert PARK_REASON[:60] in last["reason"]
    assert len(last["reason"]) <= 200
    assert rows[1][1]["park"]["reason"] == PARK_REASON

    # The audit: INTENT was written while both still existed, then APPLIED.
    assert [p for p, _ in rows] == ["intent", "applied"]
    assert existed_at_intent == {"worktree": True, "branch": True}
    assert rows[0][1]["task_id"] == tid
    assert rows[0][1]["worktree"] == str(path)
    assert rows[1][1]["deleted_branches"] == [f"kanban/{tid}"]


def test_one_uncommitted_file_refuses(raw, get_conn, ctx):
    tid = "t-dirty"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)
    (path / "live-edit.txt").write_text("a session is working here\n", encoding="utf-8")

    result, rows = _act(_findings(tid), get_conn, ctx)

    assert result["requeued"] == []
    assert result["acts"] == []
    [refusal] = result["refused"]
    assert refusal["task_id"] == tid
    assert any(r.startswith("worktree_dirty:") for r in refusal["reasons"]), refusal
    assert path.exists() and (path / "live-edit.txt").exists()
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []                          # nothing audited: nothing was going to happen


def test_one_commit_ahead_refuses(raw, get_conn, ctx):
    tid = "t-ahead"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)
    (path / "work.py").write_text("print('built')\n", encoding="utf-8")
    _git("add", "work.py", cwd=path)
    _git("commit", "-q", "-m", "real work", cwd=path)

    result, rows = _act(_findings(tid), get_conn, ctx)

    [refusal] = result["refused"]
    assert f"branch_ahead:kanban/{tid}:1" in refusal["reasons"], refusal
    assert path.exists()
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []


def test_never_registered_directory_refuses_and_is_not_read_as_dirty(raw, get_conn, ctx):
    """A plain directory under the repo that git never registered. ``git
    status`` inside it walks UP and describes the enclosing clone -- which is
    dirty (the untracked ``.tmp/``) -- so a naive probe would refuse for the
    WRONG reason and a naive cleanup would rmtree a directory it knows nothing
    about (kpr-dup-10)."""
    tid = "t-unreg"
    _task(raw, tid)
    _park(raw, tid)
    _git("branch", f"kanban/{tid}", "origin/main", cwd=ctx.repo_root)
    path = ctx.path_for(tid)
    path.mkdir(parents=True)
    (path / "AGENTS.md").write_text("left behind\n", encoding="utf-8")

    result, rows = _act(_findings(tid), get_conn, ctx)

    [refusal] = result["refused"]
    assert "worktree_unregistered" in refusal["reasons"], refusal
    assert not any(r.startswith("worktree_dirty") for r in refusal["reasons"])
    assert path.exists() and (path / "AGENTS.md").exists()
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []


def test_partial_delete_worktree_without_its_git_file_refuses(raw, get_conn, ctx):
    """The live case (task-det-e9a2e3ea16): a full checkout whose ``.git`` file
    is gone. Registered in ``.git/worktrees`` or not, it is not provable."""
    tid = "t-partial"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)
    (path / ".git").unlink()

    result, rows = _act(_findings(tid), get_conn, ctx)

    [refusal] = result["refused"]
    assert any(r.startswith("worktree_") for r in refusal["reasons"]), refusal
    assert not any(r.startswith("worktree_dirty") for r in refusal["reasons"])
    assert path.exists() and (path / "README.md").exists()
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []


def test_an_origin_branch_is_never_deleted(raw, get_conn, ctx, repo):
    tid = "t-pushed"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)
    _git("push", "-q", "origin", f"kanban/{tid}", cwd=ctx.repo_root)
    assert _branch_exists(ctx, f"origin/kanban/{tid}")

    result, rows = _act(_findings(tid), get_conn, ctx)

    assert result["requeued"] == [tid]
    assert result["acts"][0]["deleted_branches"] == [f"kanban/{tid}"]   # local only
    assert not _branch_exists(ctx, f"kanban/{tid}")
    assert _branch_exists(ctx, f"origin/kanban/{tid}")
    assert _git_rc("rev-parse", "--verify", "--quiet", f"refs/heads/kanban/{tid}",
                   cwd=repo["origin"]) == 0
    assert not path.exists()


def test_no_audit_row_no_act(raw, get_conn, ctx):
    tid = "t-unaudited"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)

    def audit_refuses(phase, details):
        raise RuntimeError("audit_trail unreachable")

    result, _ = _act(_findings(tid), get_conn, ctx, audit=audit_refuses)

    [refusal] = result["refused"]
    assert refusal["reasons"][0].startswith(oq.UNAUDITED_REFUSED + ":")
    assert result["requeued"] == [] and result["acts"] == []
    assert path.exists() and _registered(ctx, path)
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"


def test_an_audit_that_returns_no_row_id_refuses_too(raw, get_conn, ctx):
    tid = "t-noid"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)

    result, _ = _act(_findings(tid), get_conn, ctx, audit=lambda phase, details: None)

    [refusal] = result["refused"]
    assert refusal["reasons"][0].startswith(oq.UNAUDITED_REFUSED + ":")
    assert path.exists()


def test_cap_is_shared_with_the_orphan_act(raw, get_conn, ctx):
    tid = "t-capped"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)

    rows, audit = _recorder()
    result = oq.act_on_empty_checkouts(
        _findings(tid), {"max_requeues_per_run": 3}, already_requeued=3,
        get_conn=get_conn, ctx=ctx, audit=audit, lease_state=lambda t: "free",
    )

    assert result["deferred"] == [tid]
    assert result["requeued"] == [] and result["acts"] == []
    assert result["already_requeued"] == 3
    assert path.exists() and _branch_exists(ctx, f"kanban/{tid}")
    assert rows == []


def test_no_branch_is_the_orphan_proofs_case_not_this_one(raw, get_conn, ctx):
    """Disjoint by construction: the row with NO branch refuses here so the two
    proofs can never both claim it."""
    tid = "t-nobranch"
    _task(raw, tid)
    _park(raw, tid)

    result, rows = _act(_findings(tid), get_conn, ctx)

    assert result["refused"] == [{"task_id": tid, "reasons": ["no_branch"]}]
    assert rows == []
    # ...and the orphan proof takes it.
    proof = oq.prove(tid, translating(raw, unclosable=True),
                     branch_exists=lambda t: False, worktree_exists=lambda t: False,
                     lease_state=lambda t: "free")
    assert proof["proven"] is True


def test_empty_branch_with_no_worktree_directory_is_requeued(raw, get_conn, ctx):
    """The branch outlived its checkout (a pruned worktree). Nothing to remove;
    the empty local branch goes and the row is requeued."""
    tid = "t-branchonly"
    _task(raw, tid)
    _park(raw, tid)
    _git("branch", f"kanban/{tid}", "origin/main", cwd=ctx.repo_root)

    result, rows = _act(_findings(tid), get_conn, ctx)

    assert result["requeued"] == [tid]
    assert result["acts"] == [{"task_id": tid, "removed_worktree": None,
                               "deleted_branches": [f"kanban/{tid}"]}]
    assert not _branch_exists(ctx, f"kanban/{tid}")
    assert [p for p, _ in rows] == ["intent", "applied"]
    assert _row(raw, tid)["status"] == "scheduled"


def test_twice_parked_within_24h_is_carded_not_acted(raw, get_conn, ctx):
    tid = "t-repark"
    _task(raw, tid)
    _park(raw, tid, ago=timedelta(hours=5))
    _park(raw, tid, ago=timedelta(hours=1))
    path = _add_worktree(ctx, tid)
    specs = []

    result, rows = _act(_findings(tid), get_conn, ctx,
                        file_card=lambda spec: specs.append(spec) or spec["id"])

    assert result["carded"] == [tid]
    assert result["cards"] == [f"kph-repark-{tid}"]
    assert result["requeued"] == [] and result["acts"] == []
    assert path.exists() and _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []
    assert specs[0]["status"] == "suggested"


def test_dry_run_proves_everything_and_acts_on_nothing(raw, get_conn, ctx):
    tid = "t-dry"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)

    result, rows = _act(_findings(tid), get_conn, ctx, dry_run=True)

    assert result["dry_run"] is True
    assert result["would_act"] == [tid]
    assert result["requeued"] == [] and result["acts"] == []
    assert path.exists() and _registered(ctx, path)
    assert _branch_exists(ctx, f"kanban/{tid}")
    assert _row(raw, tid)["status"] == "validating"
    assert rows == []


def test_a_human_park_is_not_a_guard_park(raw, get_conn, ctx):
    tid = "t-human"
    _task(raw, tid)
    _park(raw, tid, actor="manual", reason="tools/kanban/cli.py --set-status")
    path = _add_worktree(ctx, tid)

    result, _ = _act(_findings(tid), get_conn, ctx)

    assert result["refused"] == [{"task_id": tid, "reasons": ["not_guard_park"]}]
    assert path.exists()


@pytest.mark.parametrize("state", ["live", "working", None])
def test_live_or_unknown_lease_refuses(raw, get_conn, ctx, state):
    tid = "t-lease"
    _task(raw, tid)
    _park(raw, tid)
    path = _add_worktree(ctx, tid)

    result, _ = _act(_findings(tid), get_conn, ctx, lease_state=lambda t: state)

    [refusal] = result["refused"]
    assert refusal["reasons"] == (["lease_unknown"] if state is None else ["lease_live"])
    assert path.exists()


def test_unreadable_board_is_unmeasurable_never_clean():
    def boom():
        raise RuntimeError("connection refused")

    result = oq.act_on_empty_checkouts(_findings("t-x"), {}, get_conn=boom,
                                       ctx=oq.GitContext(repo_root=Path(".")))
    assert result["state"] == "unmeasurable"
    assert result["candidates"] is None
    assert "connection refused" in result["error"]


def test_no_candidates_is_clean(get_conn):
    result = oq.act_on_empty_checkouts({"default_branch": "main"}, {}, get_conn=get_conn)
    assert result["state"] == "clean" and result["candidates"] == 0


# ── the probes, on their own ───────────────────────────────────────────────


def test_parse_worktree_list_reads_every_entry_shape():
    listing = (
        "worktree C:/AI/ICDev\nHEAD 0123456789abcdef\nbranch refs/heads/main\n\n"
        "worktree C:/AI/ICDev/.tmp/worktrees/t-a\nHEAD fedcba9876543210\n"
        "branch refs/heads/kanban/t-a\n\n"
        "worktree /tmp/x\nHEAD 1111111111111111\ndetached\n\n"
        "worktree /tmp/gone\nHEAD 2222222222222222\nbranch refs/heads/kanban/gone\n"
        "prunable gitdir file points to non-existent location\n\n"
    )
    entries = oq.parse_worktree_list(listing)
    assert [e["path"] for e in entries] == [
        "C:/AI/ICDev", "C:/AI/ICDev/.tmp/worktrees/t-a", "/tmp/x", "/tmp/gone"]
    assert entries[1]["branch"] == "refs/heads/kanban/t-a"
    assert entries[2]["detached"] is True
    assert entries[3]["prunable"].startswith("gitdir file")


def test_probe_worktree_refuses_a_checkout_on_another_tasks_branch(ctx):
    """Registered at the task's path but on someone else's branch: not ours."""
    other = _add_worktree(ctx, "t-other")
    _git("checkout", "-q", "-b", "kanban/t-stranger", cwd=other)
    ctx2 = oq.GitContext(repo_root=ctx.repo_root, default_branch="main",
                         worktree_path_for=lambda tid: other)
    state = oq.probe_worktree("t-stranger-2", ctx2)
    assert state["state"] == "other_branch"
    assert state["reasons"] == ["worktree_on_other_branch:refs/heads/kanban/t-stranger"]


def test_probe_branch_reports_both_measurements_per_ref(ctx):
    tid = "t-measured"
    path = _add_worktree(ctx, tid)
    state = oq.probe_branch(tid, ctx)
    assert state["empty"] is True
    assert state["per_ref"] == {f"kanban/{tid}": {"ahead": 0, "ancestor": True}}

    (path / "w.txt").write_text("x\n", encoding="utf-8")
    _git("add", "w.txt", cwd=path)
    _git("commit", "-q", "-m", "one", cwd=path)
    state = oq.probe_branch(tid, ctx)
    assert state["empty"] is False
    assert state["per_ref"][f"kanban/{tid}"] == {"ahead": 1, "ancestor": False}


# ── the audit feeds it, the reflex wires it ────────────────────────────────


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=()):
        rows = self._rows

        class _R:
            @staticmethod
            def fetchall():
                return rows

        return _R()

    def close(self):
        pass


def test_stranded_audit_names_the_validating_row_with_an_empty_branch():
    from tools.kanban import stranded_audit as sa

    conn = _Conn([{"id": "t-v", "status": "validating", "title": "x"},
                  {"id": "t-d", "status": "done", "title": "y"}])
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: (True, 0), fetch=False)
    assert r["orphan_validating"] == []
    assert r["stranded"] == []
    assert r["clean_count"] == 2                   # unchanged: nothing is stranded
    assert r["validating_with_branch"] == [
        {"id": "t-v", "status": "validating", "title": "x", "unmerged_commits": 0}]


def test_reflex_runs_the_second_act_under_the_shared_cap(monkeypatch):
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import stranded_audit as sa

    findings = {**_findings("t-b"), "orphan_validating": [{"id": "t-a", "status": "validating",
                                                          "title": "a"}], "cards_filed": []}
    monkeypatch.setattr(sa, "run", lambda config, state: {
        "success": True, "metric_value": 1.0, "details": dict(findings)})
    monkeypatch.setattr(oq, "act_on_orphans", lambda f, c, **kw: {
        "state": "acted", "requeued": ["t-a", "t-a2"], "carded": [], "deferred": [],
        "refused": [], "candidates": 2, "max_requeues_per_run": 5})
    seen = {}

    def _fake_empty(f, c, *, already_requeued=0, **kw):
        seen["ids"] = [x["id"] for x in f["validating_with_branch"]]
        seen["already"] = already_requeued
        seen["config"] = c
        return {"state": "acted", "requeued": ["t-b"], "acts": [], "carded": [],
                "deferred": [], "refused": []}

    monkeypatch.setattr(oq, "act_on_empty_checkouts", _fake_empty)

    result = reflex.run({"max_requeues_per_run": 5}, None)

    assert seen == {"ids": ["t-b"], "already": 2, "config": {"max_requeues_per_run": 5}}
    assert result["details"]["empty_checkout_requeue"]["requeued"] == ["t-b"]
    assert result["details"]["orphan_requeue"]["requeued"] == ["t-a", "t-a2"]


def test_reflex_marks_both_acts_unmeasurable_when_the_audit_could_not_read(monkeypatch):
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import stranded_audit as sa

    monkeypatch.setattr(sa, "run", lambda config, state: {
        "success": True, "metric_value": 0.0,
        "details": {"default_branch": "main", "total": 0, "stranded": [],
                    "orphan_validating": [], "validating_with_branch": [],
                    "clean_count": 0, "error": "connection refused", "cards_filed": []}})

    result = reflex.run({}, None)

    assert result["success"] is True
    assert result["details"]["empty_checkout_requeue"]["state"] == "unmeasurable"
    assert result["details"]["orphan_requeue"]["state"] == "unmeasurable"


def test_reflex_survives_a_second_act_that_raises(monkeypatch):
    from tools.genesis.reflexes import kanban_stranded_reflex as reflex
    from tools.kanban import stranded_audit as sa

    monkeypatch.setattr(sa, "run", lambda config, state: {
        "success": True, "metric_value": 1.0, "details": {**_findings("t-b"), "cards_filed": []}})
    monkeypatch.setattr(oq, "act_on_orphans", lambda f, c, **kw: {
        "state": "clean", "requeued": [], "carded": [], "deferred": [], "refused": []})

    def _boom(*a, **k):
        raise RuntimeError("second act exploded")

    monkeypatch.setattr(oq, "act_on_empty_checkouts", _boom)

    result = reflex.run({}, None)

    assert result["success"] is True
    assert result["details"]["empty_checkout_requeue"]["state"] == "unmeasurable"
    assert "second act exploded" in result["details"]["empty_checkout_requeue"]["error"]
    assert result["details"]["validating_with_branch"][0]["id"] == "t-b"


# ── structural: git's own doors, never around them ─────────────────────────


def test_act_module_never_rmtrees_never_forces_never_pushes():
    src = (ROOT / "tools" / "kanban" / "orphan_requeue.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "rmtree" not in names
    assert "unlink" not in names and "rmdir" not in names
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != "--force", "git worktree remove must never be forced"
            assert node.value not in ("push", "--delete"), "an origin branch is never touched"
    assert "worktree_cleaned" in src
    assert "raise_on_error=True" in src


def test_orphan_proof_is_not_loosened():
    """kpr-stale-05's proof still refuses a branch or a worktree; the second
    shape is a SEPARATE proof, not a relaxed one."""
    src = (ROOT / "tools" / "kanban" / "orphan_requeue.py").read_text(encoding="utf-8")
    assert 'reasons.append("branch_exists")' in src
    assert 'reasons.append("worktree_exists")' in src
