# CUI // SP-CTI
"""kpr-watch-01: the merge-eligibility decision table.

The acceptance test is BEHAVIOUR PARITY: `_reference_ladder` below is a literal
transcription of the `continue` ladder that lived in
`pr_watcher._sweep_unlinked_prs` before the extraction (see git history for
`tools/ci/pr_watcher.py` at ff6662fe6). Over an exhaustive product of the
signals that ladder read, `classify_merge_readiness(...).state == "ready"` must
agree with it on every single case. If someone later "improves" the table and
the merger starts merging something it did not merge before, this goes red.
"""
from __future__ import annotations

import itertools
import json
import pathlib

import pytest

from tools.ci import error_classifier as ec
from tools.ci import merge_readiness as mr


# ────────────────────────────────────────────────────────────────────────────
# The pre-extraction ladder, transcribed verbatim
# ────────────────────────────────────────────────────────────────────────────


def _reference_ladder(pr, *, default_branch, linked):
    """True iff the ORIGINAL `_sweep_unlinked_prs` would have merged this PR."""
    url = (pr.get("url") or "").strip()
    if not url or url in linked:
        return False
    if pr.get("isDraft"):
        return False
    labels = {(lbl.get("name") or "").strip().lower()
              for lbl in (pr.get("labels") or [])}
    if labels & frozenset({"hold", "do-not-merge", "do not merge", "wip",
                           "no-automerge", "blocked"}):
        return False
    if (pr.get("baseRefName") or "") != default_branch:
        return False
    if (pr.get("mergeable") or "").upper() != "MERGEABLE":
        return False
    state = dict(pr)
    if not ec.is_passing(state):
        return False
    if ec.is_changes_requested(state):
        return False
    return True


def _pr(**over):
    base = {
        "number": 1,
        "url": "https://github.com/o/r/pull/1",
        "title": "t",
        "headRefName": "feat/x",
        "baseRefName": "main",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "labels": [],
        "statusCheckRollup": [{"name": "Test", "conclusion": "SUCCESS"}],
        "reviews": [],
        "state": "OPEN",
    }
    base.update(over)
    return base


# ────────────────────────────────────────────────────────────────────────────
# Parity — the acceptance test
# ────────────────────────────────────────────────────────────────────────────

_URLS = ["https://github.com/o/r/pull/1", ""]
_DRAFTS = [True, False]
_LABELS = [[], [{"name": "hold"}], [{"name": "enhancement"}],
           [{"name": "docs"}, {"name": "WIP"}]]
_BASES = ["main", "release/1.0", ""]
_MERGEABLE = ["MERGEABLE", "CONFLICTING", "UNKNOWN", ""]
_ROLLUPS = [
    [],
    [{"name": "Test", "conclusion": "SUCCESS"}],
    [{"name": "Test", "conclusion": "FAILURE"}],
    [{"name": "Test", "conclusion": ""}, {"name": "Lint", "conclusion": "SUCCESS"}],
    [{"name": "Docker", "conclusion": "SKIPPED"}],
]
_REVIEWS = [[], [{"state": "APPROVED"}], [{"state": "CHANGES_REQUESTED"}]]


def _matrix():
    for url, draft, labels, base, mergeable, rollup, reviews in itertools.product(
            _URLS, _DRAFTS, _LABELS, _BASES, _MERGEABLE, _ROLLUPS, _REVIEWS):
        yield _pr(url=url, isDraft=draft, labels=labels, baseRefName=base,
                  mergeable=mergeable, statusCheckRollup=rollup, reviews=reviews)


@pytest.mark.parametrize("linked", [frozenset(), frozenset({_URLS[0]})])
def test_ready_exactly_when_the_old_ladder_would_have_merged(linked):
    cases = 0
    for pr in _matrix():
        cases += 1
        verdict = mr.classify_merge_readiness(
            pr, default_branch="main", linked_urls=linked)
        expected = _reference_ladder(pr, default_branch="main", linked=linked)
        assert verdict.ready is expected, (
            "drift on %r -> %s (%s); ladder said merge=%s"
            % (pr, verdict.state, verdict.reason, expected))
    # A parity sweep that swept nothing must not read as green.
    assert cases == (len(_URLS) * len(_DRAFTS) * len(_LABELS) * len(_BASES)
                     * len(_MERGEABLE) * len(_ROLLUPS) * len(_REVIEWS))
    assert cases > 1000


def test_every_state_is_reachable_and_declared():
    seen = {mr.classify_merge_readiness(pr, default_branch="main").state
            for pr in _matrix()}
    seen.add(mr.classify_merge_readiness(
        _pr(state="MERGED"), default_branch="main").state)
    seen.add(mr.classify_merge_readiness(
        _pr(), default_branch="main", linked_urls=[_URLS[0]]).state)
    assert seen == set(mr.MERGE_STATES), (
        "unreachable or undeclared states: %s"
        % (seen.symmetric_difference(mr.MERGE_STATES),))


# ────────────────────────────────────────────────────────────────────────────
# Per-state behaviour
# ────────────────────────────────────────────────────────────────────────────


def test_merged_outranks_everything():
    v = mr.classify_merge_readiness(
        _pr(state="MERGED", isDraft=True, mergeable="CONFLICTING"),
        default_branch="main")
    assert v.state == mr.MERGED


def test_linked_pr_is_the_task_paths_problem():
    v = mr.classify_merge_readiness(
        _pr(), default_branch="main", linked_urls=[_URLS[0]])
    assert v.state == mr.LINKED and not v.ready


def test_hold_label_names_the_label():
    v = mr.classify_merge_readiness(
        _pr(labels=[{"name": "Do-Not-Merge"}]), default_branch="main")
    assert v.state == mr.HELD_LABEL and "do-not-merge" in v.reason


def test_wrong_base_names_both_branches():
    v = mr.classify_merge_readiness(
        _pr(baseRefName="release/1.0"), default_branch="main")
    assert v.state == mr.WRONG_BASE
    assert "release/1.0" in v.reason and "main" in v.reason


def test_unknown_mergeability_is_not_reported_as_a_conflict():
    """UNKNOWN means GitHub is still computing. Telling someone to rebase a
    branch that has no conflict is the merged-buckets defect in miniature."""
    unknown = mr.classify_merge_readiness(
        _pr(mergeable="UNKNOWN"), default_branch="main")
    conflicting = mr.classify_merge_readiness(
        _pr(mergeable="CONFLICTING"), default_branch="main")
    assert unknown.state == conflicting.state == mr.CONFLICTING
    assert "not a conflict" in unknown.reason
    assert "rebase" in conflicting.reason and "rebase" not in unknown.reason


def test_empty_rollup_is_no_checks_not_awaiting_ci():
    """An empty rollup means nothing has reported — possibly nothing ever will.
    Pending checks mean waiting works. Different fixes, different states."""
    assert mr.classify_merge_readiness(
        _pr(statusCheckRollup=[]), default_branch="main").state == mr.NO_CHECKS
    assert mr.classify_merge_readiness(
        _pr(statusCheckRollup=[{"name": "Test", "conclusion": ""}]),
        default_branch="main").state == mr.AWAITING_CI


def test_ci_failed_names_the_failing_check():
    v = mr.classify_merge_readiness(
        _pr(statusCheckRollup=[{"name": "Lint", "conclusion": "FAILURE"},
                               {"name": "Test", "conclusion": "SUCCESS"}]),
        default_branch="main")
    assert v.state == mr.CI_FAILED and "Lint" in v.reason and "Test" not in v.reason


def test_changes_requested_blocks_a_green_pr():
    v = mr.classify_merge_readiness(
        _pr(reviews=[{"state": "CHANGES_REQUESTED"}]), default_branch="main")
    assert v.state == mr.CHANGES_REQUESTED and not v.ready


def test_ready_is_a_two_tuple():
    v = mr.classify_merge_readiness(_pr(), default_branch="main")
    state, reason = v
    assert (state, v.ready) == (mr.READY, True) and reason


def test_no_url_is_unknown_not_linked():
    v = mr.classify_merge_readiness(_pr(url=""), default_branch="main")
    assert v.state == mr.UNKNOWN


def test_classify_is_pure_and_does_not_mutate_its_input():
    pr = _pr(labels=[{"name": "hold"}])
    before = json.dumps(pr, sort_keys=True)
    mr.classify_merge_readiness(pr, default_branch="main", linked_urls=["x"])
    assert json.dumps(pr, sort_keys=True) == before


# ────────────────────────────────────────────────────────────────────────────
# The merger CONSUMES the table (one table, two consumers)
# ────────────────────────────────────────────────────────────────────────────


def _watcher(prs, merged):
    from tools.ci import pr_watcher as pw

    def _list_runner(cmd, **kw):
        class P:
            returncode = 0
            stdout = json.dumps(prs)
            stderr = ""
        return P()

    w = pw.PRWatcher(
        config={"merge_unlinked_prs": True, "auto_merge_enabled": True},
        get_connection=lambda: None,
        pr_list_runner=_list_runner,
        default_branch_resolver=lambda: "main",
    )
    w._auto_merge = lambda url: (merged.append(url) or True)  # type: ignore
    return w, pw


def test_sweep_merges_exactly_the_ready_prs(monkeypatch):
    prs = [
        _pr(number=1, url="https://github.com/o/r/pull/1"),                    # ready
        _pr(number=2, url="https://github.com/o/r/pull/2", isDraft=True),
        _pr(number=3, url="https://github.com/o/r/pull/3",
            labels=[{"name": "hold"}]),
        _pr(number=4, url="https://github.com/o/r/pull/4",
            baseRefName="release/1.0"),
        _pr(number=5, url="https://github.com/o/r/pull/5",
            mergeable="CONFLICTING"),
        _pr(number=6, url="https://github.com/o/r/pull/6",
            statusCheckRollup=[{"name": "Test", "conclusion": "FAILURE"}]),
        _pr(number=7, url="https://github.com/o/r/pull/7",
            reviews=[{"state": "CHANGES_REQUESTED"}]),
        _pr(number=8, url="https://github.com/o/r/pull/8"),                    # linked
    ]
    merged = []
    w, pw = _watcher(prs, merged)
    monkeypatch.setattr(
        pw, "list_pr_tasks",
        lambda _c: [{"pr_url": "https://github.com/o/r/pull/8"}])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert merged == ["https://github.com/o/r/pull/1"]
    assert [a.pr_url for a in report.actions] == merged


def test_watcher_and_report_cannot_hold_two_label_lists():
    from tools.ci import pr_watcher as pw
    assert pw._NO_AUTOMERGE_LABELS is mr.NO_AUTOMERGE_LABELS


# ────────────────────────────────────────────────────────────────────────────
# The report — READ ONLY
# ────────────────────────────────────────────────────────────────────────────


def test_cli_is_read_only():
    """No write verb reaches a subprocess. The report exists because the actor
    was unobservable; it must never quietly become a second actor.

    AST, not grep: this module's prose legitimately says "gh pr merge" while
    explaining why a draft cannot be merged, and a text scan cannot tell that
    sentence from an argv. Only CODE string literals are inspected -- docstrings
    are excluded, comments never reach the AST at all.
    """
    import ast

    tree = ast.parse(pathlib.Path(mr.__file__).read_text(encoding="utf-8"))

    # Exactly one thing may shell out, and only through the injectable runner.
    shells = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
              and n.attr in ("run", "Popen", "call", "check_call", "check_output")]
    assert len(shells) == 1, (
        "expected one subprocess reference, got %s"
        % [ast.unparse(n) for n in shells])

    # ...and its argv must be a read. The command literals are what execute;
    # prose in a docstring or an --help string is not.
    argvs = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and n.args
             and isinstance(n.args[0], ast.List)]
    assert len(argvs) == 1, "expected one argv list, got %d" % len(argvs)
    words = [e.value for e in argvs[0].args[0].elts
             if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    assert words == ["pr", "list", "--state", "open", "--limit", "--json"], words
    for verb in ("merge", "push", "close", "edit", "delete", "comment",
                 "create", "squash", "checkout", "commit", "ready"):
        assert verb not in words, "read-only argv grew a write verb: %s" % verb


def test_build_report_counts_and_sorts(tmp_path):
    prs = [_pr(number=9, url="https://github.com/o/r/pull/9", isDraft=True),
           _pr(number=3, url="https://github.com/o/r/pull/3")]
    report = mr.build_report(prs, default_branch="main")
    assert report["total"] == 2 and report["ready"] == 1
    assert report["counts"] == {"draft": 1, "ready": 1}
    assert report["prs"][0]["number"] == 3          # ready sorts first
    assert report["prs"][0]["reason"]


def test_build_report_says_so_when_the_board_was_unreadable():
    report = mr.build_report([], default_branch="main", linked_lookup_ok=False)
    assert report["linked_lookup_ok"] is False
    assert "WARNING" in mr.render_table(report)


def test_render_table_is_ascii_only():
    prs = [_pr(number=1, url="https://github.com/o/r/pull/1", mergeable="UNKNOWN")]
    text = mr.render_table(mr.build_report(prs, default_branch="main"))
    text.encode("ascii")  # raises if a box-drawing char sneaks in
    assert "conflicting" in text


def test_cli_from_json_reports_every_state(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prs.json"
    path.write_text(json.dumps([
        _pr(number=1, url="https://github.com/o/r/pull/1"),
        _pr(number=2, url="https://github.com/o/r/pull/2", isDraft=True),
    ]), encoding="utf-8")
    monkeypatch.setattr(mr, "linked_pr_urls", lambda *a, **k: frozenset())
    rc = mr.main(["--from-json", str(path), "--default-branch", "main", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] == 1
    assert {p["state"] for p in payload["prs"]} == {"ready", "draft"}


def test_cli_state_filter(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prs.json"
    path.write_text(json.dumps([
        _pr(number=1, url="https://github.com/o/r/pull/1"),
        _pr(number=2, url="https://github.com/o/r/pull/2", isDraft=True),
    ]), encoding="utf-8")
    monkeypatch.setattr(mr, "linked_pr_urls", lambda *a, **k: frozenset())
    rc = mr.main(["--from-json", str(path), "--default-branch", "main",
                  "--json", "--state", "draft"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in payload["prs"]] == [2]
    assert payload["ready"] == 1          # the count still describes the repo


def test_cli_exits_2_when_it_cannot_list(monkeypatch, capsys):
    """A report that could not run must not read like a repo with nothing open."""
    monkeypatch.setattr(mr, "linked_pr_urls", lambda *a, **k: frozenset())
    monkeypatch.setattr(
        mr, "list_open_prs",
        lambda **k: (_ for _ in ()).throw(RuntimeError("gh: not authenticated")))
    rc = mr.main(["--default-branch", "main", "--json"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "not authenticated" in payload["error"]


def test_cli_degrades_honestly_when_the_board_is_unreadable(
        tmp_path, monkeypatch, capsys):
    path = tmp_path / "prs.json"
    path.write_text(json.dumps([_pr()]), encoding="utf-8")
    monkeypatch.setattr(
        mr, "linked_pr_urls",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table")))
    rc = mr.main(["--from-json", str(path), "--default-branch", "main", "--json"])
    assert rc == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["linked_lookup_ok"] is False
    assert "kanban board unreadable" in out.err


def test_cli_rejects_an_unknown_state_filter(tmp_path):
    with pytest.raises(SystemExit):
        mr.main(["--state", "nearly_ready", "--default-branch", "main"])


def test_list_open_prs_raises_rather_than_returning_empty():
    class P:
        returncode = 1
        stdout = ""
        stderr = "gh: could not determine repository"

    with pytest.raises(RuntimeError, match="could not determine repository"):
        mr.list_open_prs(runner=lambda *a, **k: P())
