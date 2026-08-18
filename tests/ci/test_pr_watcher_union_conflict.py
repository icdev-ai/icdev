# CUI // SP-CTI
"""A union-only conflict is REAL to GitHub, and only a rebase clears it.

GitHub does not apply `.gitattributes` merge drivers when it computes PR
mergeability. `merge=union` covers `args/ci_test_files/*.txt`,
`tools/manifest/*.md`, `args/ci_skip_census.txt` and
`docs/reference/commands.md` — and CLAUDE.md requires every PR that adds a test
file to append to `args/ci_test_files/core.txt`. So nearly every kanban PR
collides there against a main that also appended, and the forge marks it
CONFLICTING.

Measured 2026-08-17: nine of ten open PRs were DIRTY, and re-running the
three-way merge WITHOUT the union driver reproduced the forge's verdict on ten
of ten, negative control included (#1730 touched `core.txt`, did not collide,
and was the one PR reported MERGEABLE).

`_conflict_is_real` used `git merge-tree`, which DOES honour union, saw a clean
merge, and called the whole class a stale forge cache. It is not stale. The
remedy it then reached for — push a new sha so the forge recomputes — happens to
be right, because a rebase materialises the union resolution into the branch.
The BUDGET was wrong: two attempts and a one-shot refund, sized for a verdict
that goes stale once, against a collision that recurs on every push to main.
Both budgets emptied, one `escalate` row was written, and pr_watcher then went
quiet forever — hcx-evt-03 at 499 escalates, kpr-dup-03 at 380, while AWAITING
MERGE never drained.
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

# A plain import, not `importorskip`: `tools.ci.pr_watcher` is first-party and
# always present, so a guard here could only ever convert a real breakage into a
# green skip. (tests/ci/test_pr_watcher_stale_conflict.py does the same.)
import tools.ci.pr_watcher as pw  # noqa: E402


GITATTRIBUTES = (
    "* text=auto eol=lf\n"
    "*.py text eol=lf\n"
    "tools/manifest/*.md merge=union\n"
    "args/ci_test_files/*.txt merge=union\n"
)


class _Git:
    """git stub keyed on argv, not on call order.

    Order-keyed stubs quietly re-answer whichever call happens to land in the
    slot, which is how the first version of this probe read `error: unknown
    option` as "merged clean".
    """

    def __init__(self, *, with_union=0, without_union=0, fetch=0,
                 attributes=GITATTRIBUTES, raises=None):
        self.with_union = with_union
        self.without_union = without_union
        self.fetch = fetch
        self.attributes = attributes
        self.raises = raises
        self.calls: list[list[str]] = []
        self.stripped_body = ""

    def __call__(self, argv, **kw):
        if self.raises:
            raise self.raises
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "fetch" in argv:
            return self._p(self.fetch)
        if "cat-file" in argv:
            if self.attributes is None:
                return self._p(128, err="path does not exist")
            return self._p(0, out=self.attributes)
        if "hash-object" in argv:
            self.stripped_body = kw.get("input") or ""
            return self._p(0, out="b10b\n")
        if "mktree" in argv:
            return self._p(0, out="7ree\n")
        if "rev-parse" in argv:
            return self._p(0, out="basesha1\n")
        if "merge-tree" in argv:
            # `git -c attr.tree=<tree> merge-tree …` is the no-union run.
            if "attr.tree=" in joined:
                return self._p(self.without_union)
            return self._p(self.with_union)
        return self._p(0)

    @staticmethod
    def _p(rc, out="", err=""):
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": err})()


def _state(head="kanban/x", base="main"):
    return {"headRefName": head, "baseRefName": base, "mergeable": "CONFLICTING"}


def _w():
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    w._default_branch = lambda: "main"  # noqa: SLF001
    return w


# ── the three kinds ─────────────────────────────────────────────────────────
def test_a_conflict_only_union_resolves_is_its_own_kind():
    """The case that deadlocked the board. git merges it (union), the forge does
    not (no union), and calling that a phantom sends it down a one-shot budget."""
    g = _Git(with_union=0, without_union=1)
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_UNION_ONLY


def test_a_merge_clean_with_AND_without_union_is_a_phantom():
    """Nothing local explains the forge's verdict — it really is a stale cache."""
    g = _Git(with_union=0, without_union=0)
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_PHANTOM


def test_a_conflict_git_reproduces_is_real():
    g = _Git(with_union=1)
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_REAL


def test_a_real_conflict_does_not_pay_for_the_second_probe():
    """git and the forge already agree; the no-union run cannot change that."""
    g = _Git(with_union=1)
    _w().classify_conflict(_state(), runner=g)
    assert not any("attr.tree=" in " ".join(c) for c in g.calls)


# ── the no-union attribute tree ─────────────────────────────────────────────
def test_only_the_union_lines_are_stripped():
    """Disabling ALL attributes would also drop `text=auto eol=lf`, and a
    normalisation difference would then read as a union conflict. The label has
    to be true: this measures the union driver's contribution and nothing else."""
    g = _Git(with_union=0, without_union=1)
    _w().classify_conflict(_state(), runner=g)
    written = [c for c in g.calls if "hash-object" in c]
    assert written, "the stripped .gitattributes must be written as a blob"
    body = g.stripped_body
    assert "merge=union" not in body
    assert "* text=auto eol=lf" in body, "line-ending attributes must survive"
    assert "*.py text eol=lf" in body


def test_a_repo_with_no_union_attributes_reports_phantom():
    """No union in play means union cannot be the explanation."""
    g = _Git(with_union=0, without_union=1, attributes="* text=auto eol=lf\n")
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_PHANTOM


def test_no_gitattributes_at_all_reports_phantom():
    g = _Git(with_union=0, without_union=1, attributes=None)
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_PHANTOM


def test_a_probe_that_could_not_RUN_is_not_a_conflict():
    """merge-tree exits 1 on a conflict and 128+ on an error. Reading 129
    (`unknown option`) as "conflict" is how a bad flag becomes a finding."""
    g = _Git(with_union=0, without_union=129)
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_PHANTOM


# ── every failure still trusts the forge ────────────────────────────────────
@pytest.mark.parametrize("g", [
    _Git(fetch=1),
    _Git(raises=OSError("no git")),
    _Git(raises=subprocess.TimeoutExpired("git", 1)),
])
def test_a_failed_verification_trusts_the_forge(g):
    assert _w().classify_conflict(_state(), runner=g) == pw.CONFLICT_REAL


def test_a_pr_with_no_head_ref_trusts_the_forge():
    assert _w().classify_conflict(
        {"baseRefName": "main"}, runner=_Git()) == pw.CONFLICT_REAL


def test_the_old_boolean_still_answers_for_its_callers():
    """`_conflict_is_real` keeps its meaning: real OR unproven."""
    w = _w()
    assert w._conflict_is_real(_state(), runner=_Git(with_union=1)) is True  # noqa: SLF001
    assert w._conflict_is_real(  # noqa: SLF001
        _state(), runner=_Git(with_union=0, without_union=1)) is False
    assert w._conflict_is_real(  # noqa: SLF001
        _state(), runner=_Git(with_union=0, without_union=0)) is False


def test_it_probes_the_prs_own_base_not_always_main():
    g = _Git(with_union=0, without_union=1)
    _w().classify_conflict(_state(base="release/2026"), runner=g)
    assert any("origin/release/2026" in a for c in g.calls for a in c)


# ── the budget: attempts belong to the base they were spent against ─────────
def _payload(base_sha=""):
    return {"task_id": "t-1", "pr_url": "u", "base_sha": base_sha}


def test_a_rebase_spent_against_an_OLDER_base_does_not_count(monkeypatch):
    """The deadlock. Two attempts is the right cap for one stale verdict and the
    wrong cap for a collision that returns every time main moves: once spent,
    the PR can never again take the ONE action that clears it."""
    w = _w()
    monkeypatch.setattr(w, "_count_audit_actions",
                        lambda tid, actions, pr_url=None:
                        2 if "pr_watcher.rebase" in actions else 0)
    monkeypatch.setattr(w, "_audit_payloads",
                        lambda tid, actions, pr_url=None:
                        [_payload("old1"), _payload("old2")])
    assert w._rebase_attempts("t-1", "u", base_sha="new") == 0  # noqa: SLF001
    assert w._rebase_attempts("t-1", "u") == 2, (  # noqa: SLF001
        "with no base named, the ledger is the whole ledger")


def test_attempts_against_THIS_base_still_count(monkeypatch):
    """Otherwise the cap is gone and a genuinely stuck PR rebases every poll."""
    w = _w()
    monkeypatch.setattr(w, "_count_audit_actions",
                        lambda tid, actions, pr_url=None:
                        2 if "pr_watcher.rebase" in actions else 0)
    monkeypatch.setattr(w, "_audit_payloads",
                        lambda tid, actions, pr_url=None:
                        [_payload("new"), _payload("new")])
    assert w._rebase_attempts("t-1", "u", base_sha="new") == 2  # noqa: SLF001


def test_an_attempt_with_no_recorded_base_is_from_another_era(monkeypatch):
    """Rows written before this shipped carry no base_sha. Counting them against
    the current base would keep every already-deadlocked PR deadlocked."""
    w = _w()
    monkeypatch.setattr(w, "_count_audit_actions",
                        lambda tid, actions, pr_url=None:
                        2 if "pr_watcher.rebase" in actions else 0)
    monkeypatch.setattr(w, "_audit_payloads",
                        lambda tid, actions, pr_url=None:
                        [_payload(), _payload()])
    assert w._rebase_attempts("t-1", "u", base_sha="new") == 0  # noqa: SLF001


def test_the_attempt_records_which_base_it_was_spent_against():
    """Without the sha on the row the rule above has nothing to read."""
    assert "base_sha" in {f.name for f in
                          __import__("dataclasses").fields(pw.WatcherAction)}


# ── an LLM resume cannot fix either kind ───────────────────────────────────
def test_a_union_only_or_phantom_conflict_never_spends_a_resume():
    """Ten resumes went into hcx-evt-03. No agent can resolve a conflict that
    does not exist in the tree it is looking at — the branch merges clean
    locally. The remedy is a rebase, and spending the resume budget only buys
    the escalation that follows it."""
    src = inspect.getsource(pw.PRWatcher.poll_once)
    i = src.index("prepare_resume_context(")
    guard = src[:i]
    assert "CONFLICT_UNION_ONLY" in guard and "CONFLICT_PHANTOM" in guard, (
        "the resume path must be skipped for a conflict git says is not there")
    j = guard.rindex("conflict_kind")
    assert j > guard.index("_maybe_rebase(task, state)"), (
        "the skip must come AFTER the rebase — the rebase is the remedy, and "
        "skipping straight past it would leave nothing acting on the PR at all")


def test_the_skip_is_not_silence():
    """The complaint that started this was 'merge doesn't appear to run': a poll
    that decides to do nothing must still SAY so, or the board looks dead."""
    src = inspect.getsource(pw.PRWatcher.poll_once)
    i = src.index("conflict_kind in (CONFLICT_UNION_ONLY")
    block = src[i:src.index("continue", i)]
    assert "report.actions.append" in block, (
        "a skipped resume must still be reported as a wait, with its reason")
    assert 'action="wait"' in block and "reason=" in block
