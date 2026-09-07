# [TEMPLATE: CUI // SP-CTI]
"""A worktree HUSK with no .git marker is provably dead -- sweep it on a short
clock, not the 7-day one (mfx-own-04).

task-det-e9a2e3ea16 sat in `validating` behind a 534 MB directory at
.tmp/worktrees/<id> with NO .git file: unregistered, so the empty-checkout
proof refused it (`worktree_unregistered`); .git-less, so the 7-day sweep never
listed it as a candidate and `_worktree_is_disposable` refused it by design. A
live worktree ALWAYS carries `.git`, so the class is safe to sweep on a clock of
hours -- and it must NEVER be widened to a directory that carries one.

Every guard the card names is pinned here: in_progress, a board row, the
unregistered check against a SUCCESSFUL listing, the deep newest-mtime walk,
the audit row BEFORE rmtree, the per-run bound, the kill switch, and the scope
(a direct child of WORKTREE_BASE only; the sanctioned root is reported, never
acted on).
"""
from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

import pytest

from tests._sql_compat import connect, translating
from tools.kanban import worktree_husks as wh

OLD = 1_000_000_000.0   # 2001 -- older than any clock this module could be set to


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def raw(tmp_path):
    """A translating board connection (never a bare sqlite3 one -- the module's
    SQL is authored with %s, and a raw connection would raise inside its
    best-effort except and read as an unreadable board)."""
    conn = connect(str(tmp_path / "board.db"))
    conn.execute("CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT)")
    yield conn
    conn.close()


@pytest.fixture()
def get_conn(raw):
    return lambda: translating(raw._conn, unclosable=True)


@pytest.fixture()
def base(tmp_path):
    b = tmp_path / "worktrees"
    b.mkdir()
    return b


@pytest.fixture()
def repo(tmp_path):
    """A real repo, so `git worktree list` succeeds and lists nothing but itself."""
    import subprocess

    r = tmp_path / "repo"
    r.mkdir()
    for args in (["init", "-b", "main"], ["config", "user.email", "t@x"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=str(r), capture_output=True, check=True)
    (r / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(r), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)
    return r


def _task(raw, tid, status="validating"):
    raw.execute("INSERT INTO kanban_tasks (id, status) VALUES (?, ?)", (tid, status))
    raw.commit()


def _age_everything(root: Path, ts: float = OLD) -> None:
    """Set every mtime in the tree to ``ts``, bottom-up so the directory stamps
    survive the file writes."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for f in filenames:
            os.utime(os.path.join(dirpath, f), (ts, ts))
        for d in dirnames:
            os.utime(os.path.join(dirpath, d), (ts, ts))
    os.utime(root, (ts, ts))


def _husk(base: Path, tid: str, *, old: bool = True, files: int = 3) -> Path:
    d = base / tid
    (d / "tools" / "x").mkdir(parents=True)
    (d / ".logs").mkdir()
    for i in range(files):
        (d / "tools" / "x" / f"f{i}.py").write_text("print(1)\n", encoding="utf-8")
    (d / ".logs" / "mcp.base.ndjson").write_text("{}\n", encoding="utf-8")
    if old:
        _age_everything(d)
    return d


class _Audit:
    """Records every audit call in order; raises when told to."""

    def __init__(self, fail: bool = False):
        self.calls: list = []
        self.fail = fail

    def __call__(self, action, details, *, raise_on_error):
        if self.fail and raise_on_error:
            raise RuntimeError("audit_trail refused the row")
        self.calls.append((action, dict(details), raise_on_error))
        return len(self.calls)


def _cfg(**over):
    c = dict(wh.DEFAULTS)
    c.update(over)
    return c


# ── the class ───────────────────────────────────────────────────────────────


def test_a_proven_husk_is_audited_then_removed(base, repo, raw, get_conn):
    """THE act: prove -> audit -> apply -> confirm. The intent row exists
    BEFORE the directory is touched."""
    d = _husk(base, "task-det-e9a2e3ea16")
    _task(raw, "task-det-e9a2e3ea16", "validating")
    audit = _Audit()
    seen_at_rmtree: list = []

    def rmtree(p):
        seen_at_rmtree.append([a for a, _, _ in audit.calls])
        wh._rmtree(p)

    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                         audit=audit, rmtree=rmtree)
    assert out["state"] == "acted", out
    assert [a["task_id"] for a in out["applied"]] == ["task-det-e9a2e3ea16"]
    assert not d.exists(), "the husk must be gone"
    assert seen_at_rmtree == [["husk_sweep.remove.intent"]], (
        "rmtree ran before the intent row was written")
    assert audit.calls[0][2] is True, "the intent row is fail-closed (raise_on_error=True)"
    assert audit.calls[-1][0] == "husk_sweep.remove.applied"
    intent = audit.calls[0][1]
    assert intent["task_id"] == "task-det-e9a2e3ea16" and intent["task_status"] == "validating"
    assert intent["age_hours"] > 6 and intent["entries"] > 0


def test_a_young_husk_is_kept(base, repo, raw, get_conn):
    d = _husk(base, "t-young", old=False)
    _task(raw, "t-young", "done")
    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                         audit=_Audit())
    assert out["applied"] == [] and "t-young" in out["refused"]
    assert d.exists()
    v = wh.classify(d, base=base, listing=set(), statuses={"t-young": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["younger_than_6h"]


def test_a_write_deep_in_the_tree_keeps_it(base, repo):
    """The top-level mtime is not the age. A process still writing
    .logs/*.ndjson four levels down is the one sign of life a husk can show,
    and on Windows the directory stamp never moves for it."""
    d = _husk(base, "t-deep")
    (d / ".logs" / "mcp.base.ndjson").write_text("{\"alive\": true}\n", encoding="utf-8")
    os.utime(d, (OLD, OLD))
    v = wh.classify(d, base=base, listing=set(), statuses={"t-deep": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["younger_than_6h"]
    assert v.newest_path and "ndjson" in v.newest_path


def test_an_in_progress_task_is_kept_however_old(base, repo):
    d = _husk(base, "t-live")
    v = wh.classify(d, base=base, listing=set(), statuses={"t-live": "in_progress"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["task_in_progress"]


def test_a_directory_with_no_board_row_is_kept(base, repo):
    """`data` and `tools` under .tmp/worktrees are residue of a root-computing
    bug, not worktrees of any task; one was still being written to."""
    for name in ("data", "tools"):
        d = _husk(base, name)
        v = wh.classify(d, base=base, listing=set(), statuses={name: None}, cfg=_cfg(),
                        repo_root_path=repo)
        assert v.proven is False and v.reasons == ["no_board_row"], name
        assert d.exists()


def test_a_git_marker_puts_it_outside_the_class(base, repo):
    """NEVER widened to a registered worktree. A `.git` FILE (linked worktree)
    or DIRECTORY (a clone) means the 7-day path owns the question."""
    f = _husk(base, "t-file")
    (f / ".git").write_text("gitdir: /somewhere/.git/worktrees/t-file\n", encoding="utf-8")
    _age_everything(f)
    v = wh.classify(f, base=base, listing=set(), statuses={"t-file": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["has_git_marker"]

    g = _husk(base, "t-dir")
    (g / ".git").mkdir()
    _age_everything(g)
    v = wh.classify(g, base=base, listing=set(), statuses={"t-dir": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["has_git_marker"]


def test_a_registered_path_is_kept_even_without_its_marker(base, repo):
    """Registered but .git-less is a corrupt worktree, which is git's to
    reason about -- not a husk."""
    d = _husk(base, "t-reg")
    v = wh.classify(d, base=base, listing={wh._norm(d)}, statuses={"t-reg": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is False and v.reasons == ["registered"]


def test_a_failed_worktree_listing_is_unmeasurable_not_absent(base, repo):
    """Absence from a listing that FAILED proves nothing (the kpr-dup-10 inversion)."""
    d = _husk(base, "t-nolist")
    v = wh.classify(d, base=base, listing=None, statuses={"t-nolist": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.proven is None and v.reasons == ["worktree_list_unreadable"]


def test_an_unreadable_board_is_unmeasurable_and_acts_on_nothing(base, repo):
    d = _husk(base, "t-noboard")

    def boom():
        raise RuntimeError("no board")

    s = wh.survey(base=base, repo_root_path=repo, get_conn=boom, cfg=_cfg(),
                  include_out_of_scope=False)
    assert s["state"] == "unmeasurable" and s["error"] == "board_unreadable"
    assert s["in_scope"][0]["proven"] is None
    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=boom, cfg=_cfg(),
                         audit=_Audit())
    assert out["state"] == "unmeasurable" and out["applied"] == []
    assert d.exists()


def test_a_missing_base_is_unmeasurable_never_clean(tmp_path, repo, get_conn):
    s = wh.survey(base=tmp_path / "absent", repo_root_path=repo, get_conn=get_conn,
                  cfg=_cfg(), include_out_of_scope=False)
    assert s["state"] == "unmeasurable" and s["error"] == "worktree_base_missing"
    assert s["roots"] == [{"path": str(tmp_path / "absent"), "readable": False}]


def test_no_audit_row_no_act(base, repo, raw, get_conn):
    d = _husk(base, "t-unaudited")
    _task(raw, "t-unaudited", "done")
    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                         audit=_Audit(fail=True))
    assert out["applied"] == [] and "t-unaudited" in out["refused"]
    r = wh.apply(d, base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                 audit=_Audit(fail=True))
    assert r["outcome"] == "unaudited_refused" and r["verdict"]["proven"] is True
    assert d.exists(), "an unaudited automatic repair is indistinguishable from drift"


def test_dry_run_proves_and_touches_nothing(base, repo, raw, get_conn):
    d = _husk(base, "t-dry")
    _task(raw, "t-dry", "done")
    audit = _Audit()
    r = wh.apply(d, base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                 dry_run=True, audit=audit)
    assert r["outcome"] == "dry_run" and r["verdict"]["proven"] is True
    assert audit.calls == [] and d.exists()


def test_bounded_per_run_oldest_first_and_deferred_by_name(base, repo, raw, get_conn):
    ages = {"t-b1": OLD + 400, "t-b2": OLD + 100, "t-b3": OLD + 300, "t-b4": OLD + 200}
    for tid, ts in ages.items():
        d = _husk(base, tid)
        _age_everything(d, ts)
        _task(raw, tid, "done")
    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn,
                         cfg=_cfg(max_removals_per_run=2), audit=_Audit())
    assert [a["task_id"] for a in out["applied"]] == ["t-b2", "t-b4"], "oldest first"
    assert sorted(out["deferred"]) == ["t-b1", "t-b3"], "the bound is reported, never silent"
    assert (base / "t-b1").exists() and (base / "t-b3").exists()


def test_the_kill_switch_stands_the_act_down(base, repo, raw, get_conn, monkeypatch):
    d = _husk(base, "t-off")
    _task(raw, "t-off", "done")
    monkeypatch.setenv(wh.KILL_SWITCH_ENV, "0")
    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                         audit=_Audit())
    assert out["state"] == "disabled" and out["deferred"] == ["t-off"] and out["applied"] == []
    assert d.exists()
    s = wh.survey(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                  include_out_of_scope=False)
    assert s["proven"] == 1 and s["enabled"] is False, "the survey still measures"


def test_a_walk_over_budget_is_unmeasurable_not_old(base, repo):
    """A partial walk over-estimates the age -- the direction that deletes."""
    d = _husk(base, "t-big", files=10)
    v = wh.classify(d, base=base, listing=set(), statuses={"t-big": "done"},
                    cfg=_cfg(max_walk_entries=3), repo_root_path=repo)
    assert v.proven is None and v.reasons == ["age_unmeasurable:walk_exceeded_3_entries"]
    assert v.age_hours is None


def test_a_read_only_file_does_not_survive_the_rmtree(base, repo, raw, get_conn):
    """The `<id>/node_modules`-only residue measured under .tmp/worktrees is
    what an rmtree that stops at the first read-only file leaves behind."""
    d = _husk(base, "t-ro")
    ro = d / "tools" / "x" / "f0.py"
    os.chmod(ro, stat.S_IREAD)
    _age_everything(d)
    _task(raw, "t-ro", "done")
    r = wh.apply(d, base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                 audit=_Audit())
    assert r["outcome"] == "applied", r
    assert not d.exists()


def test_the_env_override_sets_the_clock(monkeypatch, tmp_path):
    monkeypatch.setenv(wh.AGE_ENV, "48")
    assert wh.load_config(tmp_path / "absent.yaml")["husk_age_hours"] == 48.0
    monkeypatch.setenv(wh.AGE_ENV, "soon")
    assert wh.load_config(tmp_path / "absent.yaml")["husk_age_hours"] == wh.DEFAULTS["husk_age_hours"]


def test_the_shipped_config_declares_hours_not_days():
    cfg = wh.load_config()
    assert 0 < cfg["husk_age_hours"] <= 24, "the whole point is a clock of hours"
    assert cfg["max_removals_per_run"] >= 1


# ── scope ───────────────────────────────────────────────────────────────────


def test_the_sanctioned_root_is_surveyed_and_never_acted_on(base, repo, raw, get_conn, tmp_path):
    """Under the nested root a directory is named for a slug, so no task id is
    a fact and the in_progress guard cannot be asked. Reported by name."""
    sanctioned = tmp_path / "icdev-worktrees"
    # the live layout: an actor level holding a live worktree AND a husk leaf
    leaf = sanctioned / "verify" / "redfirst-39b47c40ecba-2384"
    (leaf / "tools").mkdir(parents=True)
    (leaf / "tools" / "x.py").write_text("x", encoding="utf-8")
    _age_everything(leaf)
    (sanctioned / "verify" / "flx-gcp-01-baseline").mkdir()
    (sanctioned / "verify" / "flx-gcp-01-baseline" / ".git").write_text(
        "gitdir: elsewhere\n", encoding="utf-8")
    container = sanctioned / "cli" / "session-1"
    container.mkdir(parents=True)
    (container / "live-wt").mkdir()
    (container / "live-wt" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    # a session level whose every worktree is gone: husk-shaped as a whole
    dead_session = sanctioned / "cli" / "local-026f5ed1fc5c"
    (dead_session / "data").mkdir(parents=True)
    (dead_session / "data" / "x.db").write_text("x", encoding="utf-8")

    s = wh.survey(base=base, repo_root_path=repo, sanctioned_root=sanctioned,
                  get_conn=get_conn, cfg=_cfg())
    paths = {o["path"] for o in s["out_of_scope"]}
    assert str(leaf) in paths, s["out_of_scope"]
    assert str(dead_session) in paths, s["out_of_scope"]
    assert str(container) not in paths, "a container holding a live worktree is not husk-shaped"
    assert str(sanctioned / "cli") not in paths and str(sanctioned / "verify") not in paths
    assert str(container / "live-wt") not in paths

    out = wh.sweep_husks(base=base, repo_root_path=repo, get_conn=get_conn, cfg=_cfg(),
                         audit=_Audit())
    assert out["applied"] == [] and leaf.exists()
    v = wh.classify(leaf, base=base, listing=set(), statuses={}, cfg=_cfg(), repo_root_path=repo)
    assert v.in_scope is False and v.proven is False
    assert v.reasons == ["out_of_scope:not_a_direct_child_of_worktree_base"]


def test_a_nested_child_is_out_of_scope(base, repo):
    d = _husk(base, "t-outer")
    inner = d / "tools"
    v = wh.classify(inner, base=base, listing=set(), statuses={"tools": "done"}, cfg=_cfg(),
                    repo_root_path=repo)
    assert v.in_scope is False and v.proven is False


def test_the_repo_root_can_never_be_the_target(base, repo):
    d = _husk(base, "t-root")
    v = wh.classify(d, base=base, listing=set(), statuses={"t-root": "done"}, cfg=_cfg(),
                    repo_root_path=d / "tools")
    assert v.proven is False and v.reasons == ["would_remove_repo_root"]


# ── the consumer ────────────────────────────────────────────────────────────


def _isolate_the_seven_day_sweep(monkeypatch, kanban, base, repo, tmp_path):
    """`_sweep_old_worktrees` is a FORCE-remover over EVERY sanctioned root.
    Pointing it at a temp base while leaving the real root resolver in place
    walked the live %TEMP%/icdev-worktrees and removed four clean, pushed
    worktrees the first time this file ran (2026-09-06). Every seam it reads
    is pinned to the fixture here, and the remover is stubbed besides."""
    monkeypatch.setattr(kanban, "WORKTREE_BASE", base)
    monkeypatch.setattr(kanban, "BASE_DIR", repo)
    monkeypatch.setattr(kanban, "_canonical_repo_root", lambda: repo)
    monkeypatch.setattr("tools.git.worktree_paths.worktree_root",
                        lambda: tmp_path / "absent-sanctioned-root")
    monkeypatch.setattr(kanban, "get_connection",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no board")))
    monkeypatch.setattr(kanban, "_remove_worktree", lambda p: False)
    monkeypatch.setattr(kanban, "_unlock_dead_entries", lambda: 0)


def test_the_scheduler_sweep_consumes_the_husk_class(monkeypatch, base, repo, tmp_path):
    """`_sweep_old_worktrees` asks for husks under WORKTREE_BASE and counts what
    was applied. A class nobody runs is the declared-but-unconsumed defect."""
    from tools.genesis.reflexes import kanban

    _husk(base, "t-wired")
    _isolate_the_seven_day_sweep(monkeypatch, kanban, base, repo, tmp_path)
    calls: list = []

    def fake_sweep(**kw):
        calls.append(kw)
        return {"state": "acted", "applied": [{"task_id": "t-wired", "path": str(base / "t-wired")}],
                "unconfirmed": [], "deferred": [], "refused": [], "unmeasurable": [], "error": None}

    monkeypatch.setattr(wh, "sweep_husks", fake_sweep)
    removed = kanban._sweep_old_worktrees(max_age_days=0)
    assert len(calls) == 1
    assert Path(calls[0]["base"]).resolve() == base.resolve()
    assert Path(calls[0]["repo_root_path"]).resolve() == repo.resolve()
    assert "t-wired" in removed


def test_a_husk_sweep_failure_never_stops_the_sweep(monkeypatch, base, repo, tmp_path):
    from tools.genesis.reflexes import kanban

    _isolate_the_seven_day_sweep(monkeypatch, kanban, base, repo, tmp_path)

    def boom(**kw):
        raise RuntimeError("husk module broke")

    monkeypatch.setattr(wh, "sweep_husks", boom)
    assert kanban._sweep_old_worktrees(max_age_days=0) == []


# ── structural ──────────────────────────────────────────────────────────────


def _source_tree():
    return ast.parse(Path(wh.__file__).read_text(encoding="utf-8"))


def test_the_module_never_asks_git_to_remove_anything():
    """The class is defined by the ABSENCE of git metadata; `git worktree
    remove --force` is the 7-day path's tool and reaching for it here would be
    the widening this card forbids."""
    tree = _source_tree()
    names_used = set()
    keywords_used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            lits = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if lits and lits[0] == "git":
                assert "remove" not in lits and "--force" not in lits and "prune" not in lits, lits
                assert lits[1:] == ["worktree", "list", "--porcelain"], lits
        elif isinstance(node, ast.Name):
            names_used.add(node.id)
        elif isinstance(node, ast.Attribute):
            names_used.add(node.attr)
        elif isinstance(node, ast.keyword):
            keywords_used.add(node.arg)
    # Read from NAMES, not source text: the docstring is allowed to explain why
    # the 7-day predicate cannot see a husk; the code is not allowed to call it.
    assert "_worktree_is_disposable" not in names_used, "no second opinion, no widening"
    assert "ignore_errors" not in keywords_used, "a swallowed rmtree failure is the residue this fixes"


def test_the_act_is_prove_audit_apply_confirm_in_that_order():
    """`apply` calls `classify` before the audit seam, the audit seam before
    rmtree, and re-reads `exists()` after -- read from the AST."""
    fn = next(n for n in _source_tree().body
              if isinstance(n, ast.FunctionDef) and n.name == "apply")
    order = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name in ("classify", "audit", "rmtree", "exists"):
                order.append((node.lineno, name))
    names = [n for _, n in sorted(order)]
    assert names.index("classify") < names.index("audit") < names.index("rmtree") < names.index("exists"), names
