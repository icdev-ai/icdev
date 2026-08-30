"""reverify must look for a task's branch in the TASK's repo (kpr-rvfy-11).

`reverify` accepted `repo_root` and `base` as optional arguments and NOTHING
ever resolved them from the task, so both fell back to the ICDEV[IT] checkout
and its default base. An EXTERNAL-repo task's branch lives on a different
origin, so the lookup always missed and the verdict was always `failed`.

MEASURED 2026-08-30 on ftp-prd-13 -- an icdev_ft task whose PR was open, green,
mergeable and passing 7 of the 8 merge gates:

    branch origin/kanban/ftp-prd-13 not found on origin (deleted after merge,
    or never pushed) - cannot verify from git

The branch existed. It was on icdev_ft's origin, and this door looked at
icdev's. `repo_registry.resolve_task_repo` already returned the right answer;
nothing asked it.

THE CONSEQUENCE WAS NOT A BAD MESSAGE. An external task could never satisfy the
enforced done-gate through the sanctioned door, so it could only be completed by
`--force-done` or by one of the weaker paths kpr-rvfy-04 measured producing
phantom `done`s -- the gate pushed work toward exactly the routes it exists to
replace.
"""
from __future__ import annotations

import sys
import types

import pytest

from tools.kanban import reverify as rv


class _Target:
    def __init__(self, name="icdev_ft", root="/repos/icdev_ft",
                 base_branch="main", is_external=True):
        self.name, self.root = name, root
        self.base_branch, self.is_external = base_branch, is_external


def _registry(monkeypatch, target, *, boom=False):
    mod = types.ModuleType("tools.kanban.repo_registry")

    def resolve_task_repo(task_id, config_path=None):
        if boom:
            raise RuntimeError("no registry on this machine")
        return target

    mod.resolve_task_repo = resolve_task_repo
    monkeypatch.setitem(sys.modules, "tools.kanban.repo_registry", mod)


# --------------------------------------------------------------------------- #
# the regression
# --------------------------------------------------------------------------- #
def test_an_external_task_resolves_to_its_own_checkout(monkeypatch, tmp_path):
    root = tmp_path / "icdev_ft"
    root.mkdir()
    _registry(monkeypatch, _Target(root=str(root)))
    repo_root, base, unmeasurable = rv.resolve_repo_context(
        "ftp-prd-13", rv._UNSET, rv._UNSET
    )
    assert repo_root == str(root)
    assert unmeasurable is None


def test_the_base_keeps_the_remote_shape(monkeypatch, tmp_path):
    """THE BUG INSIDE THE FIX. `DEFAULT_BASE` is "origin/main" and the registry's
    `base_branch` is the bare "main". Taking it raw compares against a LOCAL
    branch nothing in this process fetches -- measured 2026-08-30,
    C:/ai/icdev_ft's local `main` was 14 commits behind origin, which turned
    ftp-prd-13's 4 commits into 18 and its 6 files into 52.

    The verdict stayed `passed`, which is exactly why it would have shipped: the
    ANSWER was right and the EVIDENCE attached to it was wrong -- and the
    evidence is what a human reads out of kanban_verifications afterwards.
    """
    root = tmp_path / "ft"
    root.mkdir()
    _registry(monkeypatch, _Target(root=str(root), base_branch="main"))
    _, base, _ = rv.resolve_repo_context("ftp-prd-13", rv._UNSET, rv._UNSET)
    assert base == "origin/main", f"compared against a local ref: {base!r}"


def test_a_base_branch_that_is_already_qualified_is_not_double_prefixed(
    monkeypatch, tmp_path
):
    root = tmp_path / "ft"
    root.mkdir()
    _registry(monkeypatch, _Target(root=str(root), base_branch="upstream/release"))
    _, base, _ = rv.resolve_repo_context("x-y-1", rv._UNSET, rv._UNSET)
    assert base == "upstream/release"


# --------------------------------------------------------------------------- #
# the caller's arguments must still win
# --------------------------------------------------------------------------- #
def test_an_explicit_argument_beats_the_registry(monkeypatch, tmp_path):
    root = tmp_path / "ft"
    root.mkdir()
    _registry(monkeypatch, _Target(root=str(root)))
    repo_root, base, _ = rv.resolve_repo_context("ftp-prd-13", "/elsewhere", "develop")
    assert (repo_root, base) == ("/elsewhere", "develop")


def test_an_explicitly_falsy_argument_is_still_explicit(monkeypatch, tmp_path):
    """Why the sentinel exists. A falsy check cannot tell "the caller said
    nothing" from "the caller passed None", and silently overriding the second
    makes the function ignore its own arguments."""
    root = tmp_path / "ft"
    root.mkdir()
    _registry(monkeypatch, _Target(root=str(root)))
    repo_root, base, _ = rv.resolve_repo_context("ftp-prd-13", None, "")
    assert repo_root is None
    assert base == ""


# --------------------------------------------------------------------------- #
# absent checkout is UNMEASURABLE, never `failed`
# --------------------------------------------------------------------------- #
def test_a_missing_external_checkout_is_unmeasurable_not_failed(monkeypatch, tmp_path):
    _registry(monkeypatch, _Target(root=str(tmp_path / "does-not-exist")))
    _, _, unmeasurable = rv.resolve_repo_context("ftp-prd-13", rv._UNSET, rv._UNSET)
    assert unmeasurable and "not present" in unmeasurable


def test_compute_verification_reports_unmeasurable_without_running_git(
    monkeypatch, tmp_path
):
    """`failed` is a claim about the WORK; an absent checkout is a fact about the
    HOST. Conflating them blocks a merge on the strength of which machine ran."""
    _registry(monkeypatch, _Target(root=str(tmp_path / "gone")))

    def no_git(*_a, **_kw):
        raise AssertionError("git must not run when the repo is unmeasurable")

    monkeypatch.setattr(rv, "_run", no_git)
    v = rv.compute_verification("ftp-prd-13", task_row={"branch_name": "kanban/x"})
    assert v["result"] == "unmeasurable"
    assert "not present" in v["reason"]


def test_an_unmeasurable_verdict_is_never_written(monkeypatch, tmp_path):
    """kanban_verifications is APPEND-ONLY and `_enforced_done_ok` reads only the
    LATEST row, so a row written here would stand as the task's verdict until
    something outvoted it. "I could not look" is the absence of a verdict, and
    the honest record of it is no row at all."""
    _registry(monkeypatch, _Target(root=str(tmp_path / "gone")))
    executed = []

    class _Conn:
        def execute(self, sql, params=None):
            executed.append(sql)
            if sql.strip().upper().startswith("SELECT"):
                return types.SimpleNamespace(
                    fetchone=lambda: {"id": "ftp-prd-13", "branch_name": "kanban/x"}
                )
            raise AssertionError(f"wrote a row for an unmeasurable verdict: {sql}")

        def commit(self):
            pass

        def close(self):
            pass

    v = rv.reverify("ftp-prd-13", lambda: _Conn())
    assert v["result"] == "unmeasurable"
    assert v["written"] is False
    assert not any(s.strip().upper().startswith("INSERT") for s in executed)


# --------------------------------------------------------------------------- #
# degrade, never raise
# --------------------------------------------------------------------------- #
def test_no_registry_degrades_to_the_previous_behaviour(monkeypatch):
    """This door already runs on machines with no external repos configured."""
    _registry(monkeypatch, None, boom=True)
    repo_root, base, unmeasurable = rv.resolve_repo_context(
        "any-task-1", rv._UNSET, rv._UNSET
    )
    assert repo_root is None
    assert base == rv.DEFAULT_BASE
    assert unmeasurable is None


def test_an_internal_task_still_uses_the_ambient_checkout(monkeypatch):
    _registry(monkeypatch, _Target(name="icdev", root="/repos/icdev", is_external=False))
    repo_root, _, unmeasurable = rv.resolve_repo_context(
        "kpr-rvfy-11", rv._UNSET, rv._UNSET
    )
    assert repo_root is None, "an internal task must not be pinned to a resolved path"
    assert unmeasurable is None


@pytest.mark.parametrize("bad", [None, "", 0])
def test_a_target_without_a_usable_root_degrades(monkeypatch, bad):
    _registry(monkeypatch, _Target(root=bad))
    repo_root, _, unmeasurable = rv.resolve_repo_context("x-y-1", rv._UNSET, rv._UNSET)
    assert repo_root is None or unmeasurable
