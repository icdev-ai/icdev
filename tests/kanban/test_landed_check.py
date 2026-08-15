#!/usr/bin/env python3
"""task -> main, the question the board never asked (trust-disc-05). CUI // SP-CTI

The board tracks task -> PR. Two of five cards sitting in `pr_opened` on
2026-08-15 had their work ALREADY merged under a different PR number --
ctx-perf-02 landed as #1641 and ctx-trust-02 as #1638 -- while #1646 and #1651
stayed open against them. Both conflicted, because both re-apply changes already
present against files that have since moved on: #1651's diff against main was
-38/+26 on rest_v1.py, so merging it would have DELETED 38 lines main has.

These tests build a REAL git repository per case rather than injecting git's
answers. The whole defect class is "what git actually says vs what the board
believes", so a test that stubs git out would be testing the belief.

The two halves that must not regress:

  * a body-only mention NEVER blocks. Commit a758250c0 on main says "that is
    exactly the defect ctx-trust-02 removed" while implementing a different task
    -- a gate that read that as a landing would be confidently wrong about which
    commit did the work.
  * matching is on a NAME BOUNDARY. ctx-perf-02 must not match ctx-perf-021, and
    a parent id must not match its decomposed children's commits, because a
    false "already landed" stops real work while a miss changes nothing.

No network, no board, no gh: the PR half is injected.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.kanban import landed_check as lc  # noqa: E402


# --------------------------------------------------------------------------- #
# a real repository, because the subject of the test is git
# --------------------------------------------------------------------------- #

def _git(repo: pathlib.Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert out.returncode == 0, f"git {args[:2]} failed: {out.stderr}"
    return out.stdout


def _commit(repo: pathlib.Path, message: str, filename: str = "f.txt") -> str:
    path = repo / filename
    path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + message + "\n",
                    encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path) -> pathlib.Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, "chore: root commit")
    return r


def _check(repo_root, task_id, ref="HEAD"):
    return lc.check_landed(task_id, repo_root=repo_root, ref=ref)


# --------------------------------------------------------------------------- #
# THE regression: a task id already on main
# --------------------------------------------------------------------------- #

def test_task_id_in_the_subject_is_landed(repo):
    """ctx-perf-02's real landing: the id sits in the subject of #1641."""
    _commit(repo, "perf(cortex): stop repaying config resolution (#ctx-perf-01, #ctx-perf-02) (#1641)")
    rep = _check(repo, "ctx-perf-02")
    assert rep["checked"] is True
    assert rep["landed"] is True
    assert rep["confidence"] == lc.CONFIDENCE_SUBJECT
    assert len(rep["commits"]) == 1


def test_a_merge_commit_naming_the_branch_is_the_strongest_evidence(repo):
    """ctx-enf-01's real landing: `Merge pull request #1647 from .../kanban/ctx-enf-01`."""
    _git(repo, "checkout", "-q", "-b", "kanban/ctx-enf-01")
    _commit(repo, "fix(coherence): make vendored drift detectable")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-m",
         "Merge pull request #1647 from icdev-ai/kanban/ctx-enf-01", "kanban/ctx-enf-01")

    rep = _check(repo, "ctx-enf-01")
    assert rep["landed"] is True
    assert rep["confidence"] == lc.CONFIDENCE_MERGE_REF


def test_a_task_that_never_landed_is_clean(repo):
    _commit(repo, "feat(x): something else entirely (#other-task-09)")
    rep = _check(repo, "trust-disc-05")
    assert rep["checked"] is True, "a clean answer must be a CHECKED answer"
    assert rep["landed"] is False
    assert rep["referenced"] is False
    assert rep["commits"] == []


# --------------------------------------------------------------------------- #
# the false-positive half — what must NOT fire
# --------------------------------------------------------------------------- #

def test_a_body_only_mention_is_reported_but_never_blocks(repo):
    """The a758250c0 case: a commit that CITES a task while doing another one."""
    _commit(repo, "feat(cortex): decide govern()/agent() reach (#ctx-reach-03)")
    _git(repo, "commit", "--allow-empty", "--amend", "-m",
         "feat(cortex): decide govern()/agent() reach (#ctx-reach-03)\n\n"
         "That is exactly the defect ctx-trust-02 removed, so the count stays.")

    rep = _check(repo, "ctx-trust-02")
    assert rep["referenced"] is True, "a body mention is still worth surfacing"
    assert rep["landed"] is False, "but it must not be treated as the landing"
    assert rep["confidence"] == lc.CONFIDENCE_BODY
    assert lc.CONFIDENCE_BODY not in lc.BLOCKING_CONFIDENCE


def test_a_longer_id_with_the_same_prefix_does_not_match(repo):
    """ctx-perf-02 vs ctx-perf-021 — a substring match would stop real work."""
    _commit(repo, "perf(x): landed the other one (#ctx-perf-021)")
    assert _check(repo, "ctx-perf-02")["landed"] is False
    assert _check(repo, "ctx-perf-021")["landed"] is True


def test_a_parent_id_does_not_match_its_decomposed_child(repo):
    """dwo-mcp-03-d5 must not be declared landed by its child d5-d1's commit."""
    _commit(repo, "fix(dwo): child work (#dwo-mcp-03-d5-d1)")
    assert _check(repo, "dwo-mcp-03-d5")["landed"] is False
    assert _check(repo, "dwo-mcp-03-d5-d1")["landed"] is True


def test_an_id_embedded_in_a_word_does_not_match(repo):
    _commit(repo, "chore: mention xctx-perf-02x inline")
    assert _check(repo, "ctx-perf-02")["landed"] is False


# --------------------------------------------------------------------------- #
# fail-open, and never a FALSE clean
# --------------------------------------------------------------------------- #

def test_an_unresolvable_ref_reports_unchecked_not_clean(repo):
    """A repo that never fetched origin must not answer "nothing landed"."""
    rep = _check(repo, "ctx-perf-02", ref="origin/main")
    assert rep["checked"] is False
    assert rep["landed"] is False
    assert "not resolvable" in rep["reason"]


def test_an_id_that_is_not_id_shaped_is_unchecked(repo):
    rep = _check(repo, "not an id; rm -rf /")
    assert rep["checked"] is False
    assert rep["landed"] is False
    assert "id-shaped" in rep["reason"]


def test_a_broken_repo_path_fails_open(tmp_path):
    rep = lc.check_landed("ctx-perf-02", repo_root=tmp_path / "nope", ref="HEAD")
    assert rep["checked"] is False
    assert rep["landed"] is False


# --------------------------------------------------------------------------- #
# bulk is the same code path as single — the sweep cannot drift from the gate
# --------------------------------------------------------------------------- #

def test_bulk_and_single_agree_and_bulk_answers_every_id(repo):
    _commit(repo, "perf(cortex): repaying config (#ctx-perf-02) (#1641)")
    _commit(repo, "fix(cortex): TRUST chain twice (#ctx-trust-02) (#1638)")

    ids = ["ctx-perf-02", "ctx-trust-02", "trust-disc-05"]
    bulk = lc.check_landed_bulk(ids, repo_root=repo, ref="HEAD")

    assert set(bulk) == set(ids), "every id asked about must get a report"
    assert bulk["ctx-perf-02"]["landed"] is True
    assert bulk["ctx-trust-02"]["landed"] is True
    assert bulk["trust-disc-05"]["landed"] is False
    for tid in ids:
        single = _check(repo, tid)
        assert single["landed"] == bulk[tid]["landed"]
        assert single["confidence"] == bulk[tid]["confidence"]


def test_bulk_chunks_beyond_the_argv_cap(repo, monkeypatch):
    """A board-wide sweep must not be handed to git as one 32k command line."""
    monkeypatch.setattr(lc, "_MAX_IDS_PER_CALL", 2)
    _commit(repo, "feat(x): landed (#task-07)")
    ids = [f"task-{n:02d}" for n in range(1, 8)]
    bulk = lc.check_landed_bulk(ids, repo_root=repo, ref="HEAD")
    assert len(bulk) == 7
    assert bulk["task-07"]["landed"] is True
    assert all(bulk[i]["checked"] for i in ids)
    assert sum(1 for i in ids if bulk[i]["landed"]) == 1


# --------------------------------------------------------------------------- #
# the rival-branch half: only kanban/<task_id> settles the card
# --------------------------------------------------------------------------- #

def _stub_prs(monkeypatch, prs):
    from tools.genesis.reflexes import kanban as k
    monkeypatch.setattr(k, "_open_prs_for_task", lambda tid, root, **kw: prs)


def test_open_prs_none_of_which_is_canonical_do_not_settle(monkeypatch):
    """ctx-enf-01 had #1640 and #1647 open; only kanban/<id> can close the card."""
    _stub_prs(monkeypatch, [
        {"url": "u/1640", "number": 1640, "branch": "fix/ctx-enf-01-vendored"},
        {"url": "u/1647", "number": 1647, "branch": "feat/ctx-enf-01-again"},
    ])
    rep = lc.rival_prs("ctx-enf-01")
    assert rep["checked"] is True
    assert rep["settles"] is False
    assert len(rep["rivals"]) == 2
    assert rep["canonical"] == []
    assert "RIVAL PRs" in lc.format_warning({"task_id": "ctx-enf-01", "prs": rep})


def test_a_canonical_pr_settles_but_rivals_are_still_reported(monkeypatch):
    _stub_prs(monkeypatch, [
        {"url": "u/1647", "number": 1647, "branch": "kanban/ctx-enf-01"},
        {"url": "u/1640", "number": 1640, "branch": "fix/ctx-enf-01-vendored"},
    ])
    rep = lc.rival_prs("ctx-enf-01")
    assert rep["settles"] is True
    assert [p["number"] for p in rep["canonical"]] == [1647]
    assert [p["number"] for p in rep["rivals"]] == [1640]
    assert "RIVAL PRs" in lc.format_warning({"task_id": "ctx-enf-01", "prs": rep})


def test_no_open_prs_is_not_a_verdict(monkeypatch):
    _stub_prs(monkeypatch, [])
    rep = lc.rival_prs("ctx-enf-01")
    assert rep["settles"] is None, "nothing to judge is not the same as 'wrong'"


def test_an_unavailable_pr_lookup_is_unchecked(monkeypatch):
    from tools.genesis.reflexes import kanban as k

    def _boom(*a, **kw):
        raise RuntimeError("gh unavailable")

    monkeypatch.setattr(k, "_open_prs_for_task", _boom)
    rep = lc.rival_prs("ctx-enf-01")
    assert rep["checked"] is False
    assert rep["settles"] is None


# --------------------------------------------------------------------------- #
# enforcement posture — warn by default, and `body` never blocks
# --------------------------------------------------------------------------- #

def test_default_mode_is_warn(monkeypatch):
    monkeypatch.delenv(lc._MODE_ENV, raising=False)
    assert lc.mode() == "warn"


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("0", "off"), ("false", "off"),
    ("warn", "warn"), ("nonsense", "warn"),
    ("enforce", "enforce"), ("1", "enforce"), ("true", "enforce"),
])
def test_mode_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv(lc._MODE_ENV, raw)
    assert lc.mode() == expected


def test_warn_mode_reports_but_never_blocks(repo, monkeypatch):
    monkeypatch.setenv(lc._MODE_ENV, "warn")
    _commit(repo, "perf(cortex): landed (#ctx-perf-02) (#1641)")
    rep = lc.preflight("ctx-perf-02", repo_root=repo, ref="HEAD", with_prs=False)
    assert rep["landed"] is True
    assert rep["blocking"] is False, "warn must never refuse work"
    assert "ALREADY ON" in lc.format_warning(rep)


def test_enforce_mode_blocks_on_strong_evidence_only(repo, monkeypatch):
    monkeypatch.setenv(lc._MODE_ENV, "enforce")
    _commit(repo, "perf(cortex): landed (#ctx-perf-02) (#1641)")
    _git(repo, "commit", "--allow-empty", "-m",
         "chore: unrelated\n\nsee ctx-trust-02 for the rationale")

    strong = lc.preflight("ctx-perf-02", repo_root=repo, ref="HEAD", with_prs=False)
    weak = lc.preflight("ctx-trust-02", repo_root=repo, ref="HEAD", with_prs=False)
    assert strong["blocking"] is True
    assert weak["referenced"] is True
    assert weak["blocking"] is False, "a body citation must not stop a task"


def test_off_mode_short_circuits_without_touching_git(monkeypatch):
    monkeypatch.setenv(lc._MODE_ENV, "off")
    monkeypatch.setattr(lc, "_run_git",
                        lambda *a, **kw: pytest.fail("off must not call git"))
    rep = lc.preflight("ctx-perf-02")
    assert rep["blocking"] is False
    assert rep["checked"] is False
    assert rep["mode"] == "off"


def test_format_warning_is_empty_for_a_clean_task():
    assert lc.format_warning({"task_id": "x", "landed": False,
                              "referenced": False, "prs": None}) == ""
    assert lc.format_warning({}) == ""
