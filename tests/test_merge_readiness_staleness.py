# CUI // SP-CTI
"""rem-hyg-12 — staleness is measured for EVERY open PR in the report.

THE DEFECT. ``collect_report`` measured ``behind_by`` only for the PRs the
ladder had ALREADY classified ``ready``::

    ready_urls = {r["url"] for r in report["prs"] if r["state"] == READY}

That is right for the MERGER — a non-ready PR will not merge, so the count
cannot change its verdict, and the ``/compare`` is the one rung that costs a
forge round-trip. It is wrong for the HUMAN REPORT, which is what somebody
reads before deciding to un-draft or merge something.

MEASURED 2026-08-20. #1850 sat in AWAITING MERGE as ``draft``. It was
``MERGEABLE``, ``mergeStateStatus=CLEAN``, and 13 commits behind main (11 at
the time the card was written); its diff against main was +97/-1691, deleting
``posture.py`` (-164, the rem-hyg-09 Not-Assessed fix), ``index.html`` (-98),
``cortex/metrics.py`` (-72, ctx-obs-03) and ``kanban_project_sync.py`` (-47,
rem-hyg-08). One un-draft away from silently reverting a day of fixes, and its
staleness had never once been measured. #1845 was ``linked`` and 16 behind,
which is why its red-first proof compared against an ancient merge base.

Both short-circuited the ladder BEFORE the staleness rung — deliberately, and
the ladder is NOT reordered here. What changes is that the REPORT measures the
count for every row and states it, as a fact that sits BESIDE the verdict
rather than inside it.

WHAT THESE TESTS PIN
  1. Every open PR gets a ``/compare``, not only the ready ones.
  2. Measuring more PRs cannot change any ``state`` — the merger's verdict and
     its ladder order are untouched. That equivalence is what proves the cost
     optimisation was removed from the REPORT and not from the MERGER.
  3. ``stale`` is a THIRD axis, independent of the ladder: a ``draft`` PR far
     behind main says so, even though its state can never be ``behind_main``.
  4. ``stale`` is None — NEVER False — when the count was not measured. An
     unmeasured branch and a branch measured level with main are different
     facts, and only one of them is evidence.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.ci import merge_readiness as mr


def _pr(number=1, **over):
    base = {
        "number": number,
        "url": "https://github.com/o/r/pull/%d" % number,
        "title": "t%d" % number,
        "headRefName": "feat/x%d" % number,
        "headRefOid": "sha%d" % number,
        "baseRefName": "main",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "labels": [],
        "statusCheckRollup": [
            {"name": "Test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "reviews": [],
        "state": "OPEN",
        "updatedAt": "2026-08-20T11:00:00Z",
    }
    base.update(over)
    return base


def _no_board():
    raise RuntimeError("no board")


@pytest.fixture()
def offline(monkeypatch):
    """No board, no forge. ``collect_report`` then reads only what it is given."""
    monkeypatch.setattr(mr, "linked_pr_tasks", _no_board)


def _fixture(tmp_path, prs):
    path = tmp_path / "prs.json"
    path.write_text(json.dumps(prs), encoding="utf-8")
    return str(path)


def _recording_map(seen):
    """A ``measure_behind_map`` stand-in that records which urls it was asked
    about and answers a fixed count for each."""

    def _map(prs, **_kw):
        urls = [(p.get("url") or "").strip() for p in prs]
        seen.extend(urls)
        return {u: 13 for u in urls if u}

    return _map


# ── 1. every open PR is measured, not only the ready ones ───────────────────

def test_a_draft_pr_gets_its_staleness_measured(tmp_path, monkeypatch, offline):
    """#1850. A draft short-circuits the ladder at the DRAFT rung, so it was
    never in ``ready_urls`` and never measured. It was 13 commits behind."""
    seen: list = []
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map(seen))
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1850, isDraft=True)]),
        default_branch="main")

    assert seen == ["https://github.com/o/r/pull/1850"], (
        "the draft PR was never handed to the /compare call")
    row = report["prs"][0]
    assert row["state"] == mr.DRAFT            # the merger's verdict is unchanged
    assert row["behind_by"] == 13
    assert row["behind_measured"] is True


def test_a_linked_pr_gets_its_staleness_measured(tmp_path, monkeypatch, offline):
    """#1845. A task-linked PR short-circuits at the LINKED rung. It was 16
    commits behind, which is why its red-first proof used an ancient base."""
    seen: list = []
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map(seen))
    monkeypatch.setattr(
        mr, "linked_pr_tasks",
        lambda: {"https://github.com/o/r/pull/1845":
                 {"task_id": "t-1", "task_status": "pr_opened"}})
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1845)]), default_branch="main")

    assert seen == ["https://github.com/o/r/pull/1845"]
    row = report["prs"][0]
    assert row["state"] == mr.LINKED
    assert row["behind_by"] == 13
    # The DIAGNOSIS is the ladder asked "setting aside who owns it" — with a
    # count in hand it can now reach the staleness rung the merge verdict
    # short-circuits before.
    assert row["pipeline_state"] == mr.BEHIND_MAIN


def test_every_open_pr_is_measured_not_a_subset(tmp_path, monkeypatch, offline):
    seen: list = []
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map(seen))
    prs = [_pr(1, isDraft=True), _pr(2, mergeable="CONFLICTING"),
           _pr(3, baseRefName="release/1.0"), _pr(4),
           _pr(5, statusCheckRollup=[
               {"name": "Test", "status": "COMPLETED", "conclusion": "FAILURE"}])]
    report = mr.collect_report(from_json=_fixture(tmp_path, prs),
                               default_branch="main")
    assert len(seen) == 5, seen
    assert all(r["behind_measured"] for r in report["prs"])


# ── 2. the merger's verdict and the ladder order are untouched ──────────────

def test_measuring_more_prs_changes_no_merge_verdict(tmp_path, monkeypatch,
                                                     offline):
    """The whole point of the cost optimisation was that a non-ready PR's
    verdict cannot change — every rung above the staleness one short-circuits.
    So removing it from the REPORT must be verdict-preserving, and that is what
    proves the MERGER's ladder was not touched."""
    prs = [_pr(1, isDraft=True), _pr(2, mergeable="CONFLICTING"),
           _pr(3, baseRefName="release/1.0"),
           _pr(4, labels=[{"name": "hold"}]),
           _pr(5, statusCheckRollup=[]),
           _pr(6, statusCheckRollup=[
               {"name": "Test", "status": "COMPLETED", "conclusion": "FAILURE"}]),
           _pr(7, statusCheckRollup=[{"name": "Test", "status": "IN_PROGRESS"}]),
           _pr(8, reviews=[{"state": "CHANGES_REQUESTED"}])]
    fixture = _fixture(tmp_path, prs)

    unmeasured = mr.collect_report(from_json=fixture, default_branch="main",
                                   measure_behind=False)
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map([]))
    measured = mr.collect_report(from_json=fixture, default_branch="main")

    before = {r["number"]: r["state"] for r in unmeasured["prs"]}
    after = {r["number"]: r["state"] for r in measured["prs"]}
    assert before == after, "measuring staleness changed a merge verdict"


def test_the_ladder_itself_is_not_reordered():
    """A stale DRAFT still reports ``draft``, and a stale RED PR still reports
    ``ci_failed``. The report describes the merger's OWN first refusal."""
    assert mr.classify_merge_readiness(
        _pr(1, isDraft=True), default_branch="main",
        behind_by=999).state == mr.DRAFT
    assert mr.classify_merge_readiness(
        _pr(2, statusCheckRollup=[
            {"name": "Test", "status": "COMPLETED", "conclusion": "FAILURE"}]),
        default_branch="main", behind_by=999).state == mr.CI_FAILED


# ── 3. `stale` is a third axis, beside the verdict and never inside it ──────

def test_a_stale_draft_says_so(tmp_path, monkeypatch, offline):
    """THE CARD'S SENTENCE: "A PR that is draft/linked AND far behind should
    say so." Its STATE can never be ``behind_main`` — the ladder refuses it
    earlier, correctly — so the fact has to live on its own axis."""
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map([]))
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1850, isDraft=True)]),
        default_branch="main", max_behind_commits=10)
    row = report["prs"][0]
    assert row["state"] == mr.DRAFT
    assert row["stale"] is True
    assert "13" in row["stale_reason"] and "main" in row["stale_reason"]
    assert report["stale_count"] == 1


def test_a_fresh_pr_is_not_stale(tmp_path, monkeypatch, offline):
    monkeypatch.setattr(
        mr, "measure_behind_map",
        lambda prs, **_kw: {(p.get("url") or ""): 2 for p in prs})
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1, isDraft=True)]),
        default_branch="main", max_behind_commits=10)
    assert report["prs"][0]["stale"] is False
    assert report["stale_count"] == 0


def test_unmeasured_staleness_is_none_never_false(tmp_path, offline):
    """The same posture ``behind_by`` already takes. ``False`` would read as
    "measured, and it is fine" for a branch nobody looked at."""
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1, isDraft=True)]),
        default_branch="main", measure_behind=False)
    row = report["prs"][0]
    assert row["behind_by"] is None
    assert row["stale"] is None
    assert row["stale_reason"] == ""
    # A count of PRs known to be stale must not absorb the unmeasured ones.
    assert report["stale_count"] == 0
    assert report["stale_unmeasured_count"] == 1


def test_stale_is_computed_for_a_ready_pr_too(tmp_path, monkeypatch, offline):
    """A ready PR over the limit is refused as ``behind_main`` by the ladder.
    The axis must AGREE with the rung rather than contradict it."""
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map([]))
    report = mr.collect_report(from_json=_fixture(tmp_path, [_pr(1)]),
                               default_branch="main", max_behind_commits=10)
    row = report["prs"][0]
    assert row["state"] == mr.BEHIND_MAIN
    assert row["stale"] is True


# ── 4. the surfaces say it ─────────────────────────────────────────────────

def test_render_grouped_shows_staleness_for_a_draft(tmp_path, monkeypatch,
                                                    offline):
    """The grouped view has no BEHIND column, and it is the view the kanban CLI
    and the dashboard read. A stale draft that renders identically to a fresh
    one is the defect, one surface over."""
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map([]))
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1850, isDraft=True)]),
        default_branch="main", max_behind_commits=10)
    text = mr.render_grouped(report)
    assert text.isascii()
    assert "13" in text
    assert "behind" in text.lower()


def test_render_table_prints_the_count_for_a_draft(tmp_path, monkeypatch,
                                                   offline):
    monkeypatch.setattr(mr, "measure_behind_map", _recording_map([]))
    report = mr.collect_report(
        from_json=_fixture(tmp_path, [_pr(1850, isDraft=True)]),
        default_branch="main", max_behind_commits=10)
    lines = [ln for ln in mr.render_table(report).splitlines()
             if ln.startswith("#1850")]
    assert lines and "13" in lines[0], mr.render_table(report)


def _panel_source() -> str:
    return (pathlib.Path(mr.REPO_ROOT) / "tools" / "dashboard" / "templates"
            / "_autonomy_status.html").read_text(encoding="utf-8")


def test_the_panel_renders_staleness():
    """The dashboard panel is the surface a human actually reads. It renders
    `pipeline_reason`, which for a draft says "mark it ready for review" and
    nothing about the 13 commits — so the flag has to be rendered explicitly."""
    panel = _panel_source()
    assert "r.stale" in panel, "the panel never reads the staleness flag"
    assert "mr-stale" in panel


def test_the_panel_gates_the_badge_on_true_not_on_truthiness():
    """``r.stale`` is a THREE-valued field and null means "nobody compared this
    branch". A truthiness test (``r.stale ?``) renders nothing for null, which
    happens to look right — but ``!r.stale`` or an ``if (r.stale)`` inverted
    later would silently fold unmeasured into "fine". The identity comparison
    is what makes the three-valued contract survive an edit."""
    row = _panel_source().split("function mrRowHTML(r) {", 1)[1].split(
        "\n  }", 1)[0]
    assert "r.stale === true" in row, row


def test_the_panel_escapes_the_staleness_fields():
    """``stale_reason`` and ``behind_by`` are DATA -- they reach the row from a
    report built out of forge json. Escaped at their own interpolation sites,
    like every other field in this row."""
    row = _panel_source().split("function mrRowHTML(r) {", 1)[1].split(
        "\n  }", 1)[0]
    assert "escapeHTML(r.stale_reason" in row
    assert "r.behind_by" in row and "escapeHTML(" in row


def test_the_panel_counts_stale_and_unmeasured_separately():
    """Two numbers in the summary line, never one. "2 stale" over a board where
    five PRs were never compared is a different claim from "2 stale" over a
    board where every one was."""
    panel = _panel_source()
    assert "payload.stale_count" in panel
    assert "payload.stale_unmeasured_count" in panel
