# CUI // SP-CTI
"""Task-scoped git runs in the TASK's repo, not always in ICDev (kpr-extrepo-01).

ked-core-01 made DISPATCH repo-aware and stated why in its own header:

    "For an EXTERNAL-repo task it is wrong in the most expensive way: the done-gate asks
     ICDEV whether COMPASS's work landed, the answer is always no, and the task churns
     forever."

It added `_task_repo_root(task_id)`. The VERIFICATION path never adopted it, so an external
task whose agent committed cleanly was structurally unable to pass: the commit check ran
`git log <base>..kanban/<id>` in the ICDev checkout, where that branch does not exist.

OBSERVED, not theorised — xft-vfy-01, 2026-08-27. The agent produced exactly the requested
commit on `kanban/xft-vfy-01` in `C:/ai/icdev_ft` and pushed it; the verifier reported
"No git commits found on task branch — agent produced no committed file-level output",
incremented failure_count and re-queued the task 60 seconds later.
"""
from __future__ import annotations

import ast
import pathlib
import textwrap

import pytest

from tools.genesis.reflexes import kanban as K

MODULE = pathlib.Path(K.__file__)


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------


def test_an_external_task_resolves_to_its_own_repo_root(monkeypatch):
    """The helper that already existed and was not being used."""

    class _Target:
        is_external = True
        root = pathlib.Path("C:/ai/icdev_ft")
        name = "icdev_ft"

    monkeypatch.setattr(K, "_task_repo_target", lambda tid: _Target())
    assert K._task_repo_root("xft-vfy-01") == pathlib.Path("C:/ai/icdev_ft")


def test_an_icdev_task_still_resolves_to_base_dir(monkeypatch):
    """The default must be byte-identical — an absent registry is a complete no-op."""
    monkeypatch.setattr(K, "_task_repo_target", lambda tid: None)
    assert K._task_repo_root("rem-hyg-01") == K.BASE_DIR


def test_the_commit_check_runs_git_in_the_tasks_repo(monkeypatch):
    """The defect, end to end: which directory does the commit check actually look in?"""
    seen: list[dict] = []

    class _Result:
        returncode = 0
        stdout = "README.md\n"
        stderr = ""

    def _fake_run(argv, **kwargs):
        seen.append({"argv": list(argv), "cwd": str(kwargs.get("cwd"))})
        return _Result()

    import subprocess as real_sp

    monkeypatch.setattr(K, "_task_repo_root", lambda tid: pathlib.Path("C:/ai/icdev_ft"))
    # The check only runs its branch-commit probe when a dispatch baseline was recorded.
    monkeypatch.setitem(K._dispatch_main_heads, "xft-vfy-01", "abc1234")
    # `_git_worktree_has_real_changes` does `import subprocess as _sp` inside the function,
    # so patching the real module's `run` is what it will pick up.
    monkeypatch.setattr(real_sp, "run", _fake_run)

    ok, reason = K._git_worktree_has_real_changes("xft-vfy-01")

    assert seen, "the commit check ran no git command at all"
    # THE COMMIT-CHECK CALL BY NAME, not seen[0]. `_task_base_branch` may probe the
    # local checkout with `git symbolic-ref` first, and whether it does depends on
    # whether ICDEV_KANBAN_REPO_FT is set in the AMBIENT environment -- so asserting
    # on "whichever git ran first" passed on a developer box with the var exported
    # and failed on CI without it, while the behaviour under test was identical and
    # correct in both. A test that reads os.environ measures the RUNNER, not the code.
    probe = next((c for c in seen if c["argv"][:2] == ["git", "log"]), None)
    assert probe is not None, f"no `git log` commit probe ran; got {[c['argv'] for c in seen]}"
    assert probe["cwd"].replace("\\", "/").lower().endswith("icdev_ft"), (
        f"the commit check ran in {probe['cwd']!r}. For an external task that is the ICDev "
        "checkout, where the task branch does not exist — the done-gate then asks ICDev "
        "whether the other repo's work landed, the answer is always no, and the task churns."
    )
    assert ok and "README.md" in reason


# ---------------------------------------------------------------------------
# The structural guard — this is what stops it growing back
# ---------------------------------------------------------------------------


def _task_scoped_basedir_sites(path: pathlib.Path | None = None) -> list[tuple[int, str, str]]:
    """Every `cwd=BASE_DIR` inside a per-task function that shells out to git/gh.

    Derived by the SAME predicate the fix used, so the guard and the change cannot disagree
    about what counts. Sites that are genuinely about the ICDev checkout itself — worktree
    pruning, default-branch detection, the scheduler's own repo — take no task_id and are not
    matched.
    """
    source = (path or MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    def enclosing(lineno: int):
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                    if best is None or node.lineno > best.lineno:
                        best = node
        return best

    found: list[tuple[int, str, str]] = []
    for i, ln in enumerate(lines, 1):
        if "cwd=str(BASE_DIR)" not in ln and "cwd=BASE_DIR" not in ln:
            continue
        fn = enclosing(i)
        if fn is None or not any(a.arg in ("task_id", "task") for a in fn.args.args):
            continue
        cmd = ""
        for j in range(max(0, i - 9), i):
            s = lines[j]
            if any(tok in s for tok in ('"git"', "'git'", '"gh"', "'gh'")):
                cmd = s.strip()[:70]
                break
        if cmd:
            found.append((i, fn.name, cmd))
    return found


def test_no_task_scoped_git_call_is_hardcoded_to_icdev():
    sites = _task_scoped_basedir_sites()
    rendered = "\n".join(f"    L{i} {fn}: {cmd}" for i, fn, cmd in sites)
    assert sites == [], (
        "these per-task git/gh calls run against the ICDev checkout regardless of which repo "
        "the task builds in. For an external task the answer is always 'nothing landed' and "
        "the task churns forever. Use `cwd=str(_task_repo_root(task_id))`.\n" + rendered
    )


def test_the_guard_can_actually_see_a_violation(tmp_path):
    """A guard that cannot fail is not a guard.

    The predicate is exercised against a synthetic module rather than trusted: it is the only
    thing standing between this defect and its return, and it has to discriminate — catching
    the per-task call while leaving the ICDev-scoped one alone.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        textwrap.dedent(
            """
            BASE_DIR = '/x'

            def _verify(task_id):
                run(['git', 'log'], cwd=str(BASE_DIR))

            def _prune():
                run(['git', 'worktree', 'prune'], cwd=str(BASE_DIR))
            """
        ),
        encoding="utf-8",
    )
    sites = _task_scoped_basedir_sites(sample)
    assert len(sites) == 1, f"expected exactly the per-task site, got {sites}"
    assert sites[0][1] == "_verify", "the ICDev-scoped call must NOT be flagged"


@pytest.mark.parametrize(
    "fn_name",
    [
        "_git_worktree_has_real_changes",
        "_run_verify_checks",
        "_verify_task_specific",
        "_run_post_task_validation",
        "_verify_task_completed",
        "_capture_diff_stats",
        "_post_merge_route_smoke",
        "_ensure_pr_base",
    ],
)
def test_every_repointed_function_still_exists(fn_name):
    """Named so a rename cannot quietly drop one out of the guard's reach."""
    assert callable(getattr(K, fn_name)), f"{fn_name} is gone — re-audit its git calls"
