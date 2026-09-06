# CUI // SP-CTI
"""A REPARK id extends a task id at the FRONT, and the matcher must not bind it
(mfx-own-05).

``_branches_for_task``'s old boundary ``(^|[/_-])<id>`` admitted a PREFIX
extension, so ``mfx-ci-04`` was gated on ``kanban/kph-repark-kph-repark-mfx-ci-04``
-- a THIRD card's branch, which built PR #2146 -- and ``kanban_requeue_reflex``
refused it every cycle with ``branch_not_ancestor`` on a branch that was never
its own (2026-09-06, 12:49 to ~18:00). The narrowed rule requires the id to
START a path segment. The documented child case (a SUFFIX extension) is kept.
"""
from __future__ import annotations

import importlib
import subprocess

import pytest

kb = importlib.import_module("tools.genesis.reflexes.kanban")
bms = importlib.import_module("tools.kanban.branch_match_survey")


class _Fake:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


LIVE_REFS = [
    # the measured incident, verbatim from `git for-each-ref` on 2026-09-06
    "kanban/mfx-ci-04",
    "kanban/kph-repark-mfx-ci-04",
    "kanban/kph-repark-kph-repark-mfx-ci-04",
    "origin/kanban/kph-repark-mfx-mrg-06",
    # the documented child case
    "kanban/dwo-mcp-03-d5",
    "kanban/dwo-mcp-03-d5-d1",
    # non-kanban prefixes the 2026-07-28 run produced, still bound
    "origin/test/dwo-vv-03-d3-trigger-link",
    "docs/dwo-mcp-03-d5-d1-mcp-readme",
]


def _refs(monkeypatch, refs):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Fake(returncode=0, stdout="\n".join(refs) + "\n"))


# ── the finding ──────────────────────────────────────────────────────────────

def test_repark_branch_is_not_the_original_cards(monkeypatch, tmp_path):
    """mfx-ci-04 owns kanban/mfx-ci-04 and NOTHING with kph-repark- in front."""
    _refs(monkeypatch, LIVE_REFS)
    assert kb._branches_for_task("mfx-ci-04", tmp_path) == ["kanban/mfx-ci-04"]


def test_repark_of_a_repark_is_not_the_first_reparks(monkeypatch, tmp_path):
    """The rule is structural, so it holds at every depth of stacking."""
    _refs(monkeypatch, LIVE_REFS)
    assert kb._branches_for_task("kph-repark-mfx-ci-04", tmp_path) == [
        "kanban/kph-repark-mfx-ci-04"]
    assert kb._branches_for_task("kph-repark-kph-repark-mfx-ci-04", tmp_path) == [
        "kanban/kph-repark-kph-repark-mfx-ci-04"]


def test_a_task_whose_only_ref_is_a_repark_has_no_branch(monkeypatch, tmp_path):
    """mfx-mrg-06 has only its repark's branch on origin: that is NOT its branch."""
    _refs(monkeypatch, LIVE_REFS)
    assert kb._branches_for_task("mfx-mrg-06", tmp_path) == []


@pytest.mark.parametrize("joiner", ["-", "_"])
def test_prefix_extension_is_refused_whatever_joins_it(monkeypatch, tmp_path, joiner):
    _refs(monkeypatch, [f"kanban/other{joiner}t-1"])
    assert kb._branches_for_task("t-1", tmp_path) == []


# ── what the narrowing must KEEP ─────────────────────────────────────────────

def test_child_suffix_extension_still_binds_the_parent(monkeypatch, tmp_path):
    _refs(monkeypatch, LIVE_REFS)
    got = kb._branches_for_task("dwo-mcp-03-d5", tmp_path)
    assert got[0] == "kanban/dwo-mcp-03-d5"          # canonical first
    assert set(got[1:]) == {"kanban/dwo-mcp-03-d5-d1", "docs/dwo-mcp-03-d5-d1-mcp-readme"}


def test_non_kanban_path_prefixes_still_bind(monkeypatch, tmp_path):
    _refs(monkeypatch, LIVE_REFS)
    assert kb._branches_for_task("dwo-vv-03-d3", tmp_path) == [
        "origin/test/dwo-vv-03-d3-trigger-link"]


def test_canonical_ref_still_sorts_first(monkeypatch, tmp_path):
    _refs(monkeypatch, ["kanban/t-1-suffix", "origin/kanban/t-1", "kanban/t-1"])
    assert kb._branches_for_task("t-1", tmp_path)[:2] == ["kanban/t-1", "origin/kanban/t-1"]


def test_fail_open_on_git_error_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fake(returncode=128))
    assert kb._branches_for_task("mfx-ci-04", tmp_path) == []


# ── the survey reads the SHIPPED predicate, and never fabricates a clean zero ─

def test_survey_names_every_drop_and_classifies_the_repark_shape():
    report = bms.survey(LIVE_REFS, ["mfx-ci-04", "kph-repark-mfx-ci-04", "mfx-mrg-06",
                                    "dwo-mcp-03-d5"])
    assert report["measured"] is True
    assert report["added"] == []
    dropped = {(d["task_id"], d["ref"]): d["kind"] for d in report["dropped"]}
    assert dropped == {
        ("mfx-ci-04", "kanban/kph-repark-mfx-ci-04"): "repark",
        ("mfx-ci-04", "kanban/kph-repark-kph-repark-mfx-ci-04"): "repark",
        ("kph-repark-mfx-ci-04", "kanban/kph-repark-kph-repark-mfx-ci-04"): "repark",
        ("mfx-mrg-06", "origin/kanban/kph-repark-mfx-mrg-06"): "repark",
    }
    assert report["dropped_by_kind"] == {"repark": 4, "other": 0}
    assert report["tasks_bound"] == {"legacy": 4, "current": 3}


def test_survey_names_a_non_repark_drop_as_other():
    """`icdev-<id>` — a hand-named branch joined with '-' — is dropped AND named."""
    report = bms.survey(["icdev-prop-cap-11", "kanban/prop-cap-11"], ["prop-cap-11"])
    assert report["dropped"] == [
        {"task_id": "prop-cap-11", "ref": "icdev-prop-cap-11", "kind": "other"}]


def test_survey_today_side_is_the_shipped_resolver_not_a_copy():
    calls = []

    def resolver(tid, root, refs=None):
        calls.append(tid)
        return []

    report = bms.survey(LIVE_REFS, ["mfx-ci-04"], resolver=resolver)
    assert calls == ["mfx-ci-04"]
    # with a resolver answering nothing, every legacy pair reads as dropped
    assert report["pairs"] == {"legacy": 3, "current": 0}


@pytest.mark.parametrize("refs,ids,reason", [
    ([], ["t-1"], "no_refs"),
    (["kanban/t-1"], [], "no_tasks"),
])
def test_survey_is_unmeasurable_never_zero_on_empty_input(refs, ids, reason):
    report = bms.survey(refs, ids)
    assert report["measured"] is False
    assert report["unmeasurable_reason"] == reason
    assert "dropped" not in report
