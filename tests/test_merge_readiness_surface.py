# CUI // SP-CTI
"""kpr-watch-03 — the AWAITING MERGE surface: age, grouping, and the two verdicts.

kpr-watch-01 built the decision table and gave it a CLI. A report nobody opens
is not observability, so this card put the same classification on the dashboard
and on the kanban CLI. These tests pin the three things that could quietly go
wrong in the move:

  1. AGE IS A LOWER BOUND, AND IT IS NOT ``updatedAt``. Nothing persists a state
     transition, so the age is measured from the NEWEST observable event on the
     PR. ``updatedAt`` does not bump when a check completes — measured on this
     repo 2026-08-19, PR #1817 reported ``updatedAt=01:10:24Z`` while one of its
     own checks completed 45s LATER — so keying on it alone overstates the age.
     And ``gh`` renders an absent check timestamp as the Go zero value, which
     parses to a real year-1 datetime rather than to nothing.

  2. THE GROUPING USES THE DIAGNOSIS, NOT THE MERGE VERDICT. ``state``
     short-circuits at the ``linked`` rung for every kanban PR — the exact
     population this panel exists for — so grouping on it would collapse the
     board into one bucket labelled "a task owns it". ``pipeline_state`` is the
     SAME table asked a second question, and for an UNLINKED PR the two must be
     identical: that equality is what proves it is not a second copy of the
     ladder.

  3. THE PANEL IS READ ONLY. No merge, no push, no un-draft — enforced by
     asserting the route is GET-only and that a report never reaches a mutating
     helper.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from tools.ci import merge_readiness as mr

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 8, 19, 12, 0, 0, tzinfo=dt.timezone.utc)


def _function_code(path: Path, name: str) -> str:
    """One function's EXECUTABLE code — docstring and comments stripped.

    A read-only claim checked with ``"pr merge" not in source`` is a test that
    fails on the comment EXPLAINING that nothing here merges, which teaches the
    next reader to delete the comment. ``ast.unparse`` drops comments, and the
    docstring node is removed explicitly, so what is scanned is what RUNS.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError("%s does not define %s()" % (path, name))


def _pr(number=1, **over):
    base = {
        "number": number,
        "url": "https://github.com/o/r/pull/%d" % number,
        "title": "t%d" % number,
        "headRefName": "feat/x%d" % number,
        "baseRefName": "main",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "labels": [],
        "statusCheckRollup": [
            {"name": "Test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "reviews": [],
        "state": "OPEN",
        "updatedAt": "2026-08-19T11:00:00Z",
    }
    base.update(over)
    return base


# ── 1. age ──────────────────────────────────────────────────────────────────

def test_zero_value_check_timestamp_is_not_a_datetime():
    """`gh` prints 0001-01-01T00:00:00Z for a check that has not finished.

    Parsed naively that is a real datetime, and it would be reported as the
    BASIS for an age of ~2025 years — a number a reader stops trusting the
    whole panel over.
    """
    assert mr._parse_iso("0001-01-01T00:00:00Z") is None
    assert mr._parse_iso("") is None
    assert mr._parse_iso(None) is None
    assert mr._parse_iso("not a date") is None
    assert mr._parse_iso("2026-08-19T11:00:00Z") == dt.datetime(
        2026, 8, 19, 11, 0, tzinfo=dt.timezone.utc)


def test_age_takes_the_newest_event_not_updated_at():
    """THE REGRESSION THIS FILE EXISTS FOR.

    A check that completes AFTER `updatedAt` is the observed reality on this
    repo, not a hypothetical. If the age keyed on `updatedAt` alone it would
    claim 60 minutes here where the true lower bound is 45.
    """
    pr = _pr(updatedAt="2026-08-19T11:00:00Z", statusCheckRollup=[
        {"name": "Lint", "status": "COMPLETED", "conclusion": "SUCCESS",
         "startedAt": "2026-08-19T11:00:10Z",
         "completedAt": "2026-08-19T11:15:00Z"},
        {"name": "Test", "status": "IN_PROGRESS", "conclusion": "",
         "startedAt": "2026-08-19T11:00:12Z",
         "completedAt": "0001-01-01T00:00:00Z"},
    ])
    stamp, basis = mr.last_activity(pr)
    assert stamp == dt.datetime(2026, 8, 19, 11, 15, tzinfo=dt.timezone.utc)
    assert basis == "check_completed"
    assert mr.state_age_seconds(pr, now=NOW) == 45 * 60


def test_unmeasurable_age_is_none_never_zero():
    """An unmeasured age and an age of zero are different facts, and only one
    of them means "it just changed". `format_age(None)` is "?"."""
    pr = _pr(updatedAt=None, statusCheckRollup=[])
    pr.pop("updatedAt", None)
    assert mr.state_age_seconds(pr, now=NOW) is None
    assert mr.last_activity(pr)[1] == "unmeasured"
    assert mr.format_age(None) == "?"
    assert mr.format_age(0) == "0s"


def test_forge_clock_ahead_of_ours_never_prints_a_negative_age():
    pr = _pr(updatedAt="2026-08-19T12:00:30Z", statusCheckRollup=[])
    assert mr.state_age_seconds(pr, now=NOW) == 0


@pytest.mark.parametrize("seconds,text", [
    (0, "0s"), (59, "59s"), (60, "1m"), (3599, "59m"),
    (3600, "1h00m"), (11700, "3h15m"), (86400, "1d00h"), (554400, "6d10h"),
])
def test_format_age_shapes(seconds, text):
    assert mr.format_age(seconds) == text


def test_python_and_javascript_age_formatters_agree():
    """The panel formats ages client-side (one render, no round-trip per row),
    so the formatter exists twice. The two must not drift: this reads the JS
    out of the template and checks it produces the same shapes the table does.
    """
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates"
           / "_autonomy_status.html").read_text(encoding="utf-8")
    assert "function mrFormatAge(seconds)" in tpl
    # The four branch boundaries the Python side uses must all be present in
    # the JS, in the same order: <60 -> s, <60m -> m, <24h -> h+m, else d+h.
    body = tpl.split("function mrFormatAge(seconds)", 1)[1].split("\n  }", 1)[0]
    assert "seconds < 60" in body and "+ 's'" in body
    assert "minutes < 60" in body and "+ 'm'" in body
    assert "hours < 24" in body and "'h'" in body
    assert "'d'" in body


# ── 2. the two verdicts, from one table ─────────────────────────────────────

def test_unlinked_pr_has_identical_state_and_pipeline_state():
    """The equality that proves `pipeline_state` is not a second ladder."""
    prs = [
        _pr(1),                                        # ready
        _pr(2, isDraft=True),                          # draft
        _pr(3, mergeable="CONFLICTING"),               # conflicting
        _pr(4, statusCheckRollup=[]),                  # no_checks
        _pr(5, baseRefName="develop"),                 # wrong_base
        _pr(6, statusCheckRollup=[{"name": "Test", "status": "COMPLETED",
                                   "conclusion": "FAILURE"}]),
    ]
    report = mr.build_report(prs, default_branch="main", now=NOW)
    assert len(report["prs"]) == 6
    for row in report["prs"]:
        assert row["pipeline_state"] == row["state"], row["number"]
        assert row["pipeline_reason"] == row["reason"], row["number"]
    assert report["counts"] == report["pipeline_counts"]


def test_linked_pr_keeps_the_merge_verdict_and_gains_a_diagnosis():
    """`state` stays `linked` — the merger's own answer, unchanged and
    authoritative — while `pipeline_state` says why it is not landing."""
    pr = _pr(7, statusCheckRollup=[{"name": "Test", "status": "IN_PROGRESS",
                                    "conclusion": ""}])
    report = mr.build_report(
        [pr], default_branch="main", linked_urls=[pr["url"]],
        linked_tasks={pr["url"]: {"task_id": "kpr-watch-03",
                                  "task_status": "pr_opened"}},
        now=NOW)
    row = report["prs"][0]
    assert row["state"] == mr.LINKED
    assert row["reason"].startswith("a kanban task points at this PR")
    assert row["pipeline_state"] == mr.AWAITING_CI
    assert "checks still running" in row["pipeline_reason"]
    assert row["linked"] is True
    assert row["task_id"] == "kpr-watch-03"
    assert row["task_status"] == "pr_opened"


def test_task_linkage_never_changes_a_verdict():
    """`linked_tasks` is a COLUMN. Passing it must not move any state."""
    pr = _pr(8)
    without = mr.build_report([pr], default_branch="main", now=NOW)["prs"][0]
    with_task = mr.build_report(
        [pr], default_branch="main",
        linked_tasks={pr["url"]: {"task_id": "x-01", "task_status": "done"}},
        now=NOW)["prs"][0]
    assert without["state"] == with_task["state"] == mr.READY
    assert without["reason"] == with_task["reason"]
    # ...and it is still reported, so the column is not silently dropped either.
    assert with_task["task_id"] == "x-01"
    assert without["task_id"] is None


# ── 3. grouping ─────────────────────────────────────────────────────────────

def test_groups_are_in_attention_order_not_ladder_order():
    """`ready` (= awaiting merge) and `behind_main` come FIRST, so the two
    states this card names are visible without scrolling. That is the opposite
    end of the ladder, where `ready` sits second-to-last."""
    prs = [
        _pr(1, isDraft=True),
        _pr(2, mergeStateStatus="BEHIND"),
        _pr(3),
        _pr(4, statusCheckRollup=[{"name": "Test", "status": "IN_PROGRESS",
                                   "conclusion": ""}]),
    ]
    report = mr.build_report(prs, default_branch="main", now=NOW)
    states = [g["state"] for g in mr.group_by_state(report)]
    assert states[0] == mr.READY
    assert states[1] == mr.BEHIND_MAIN
    assert states.index(mr.AWAITING_CI) < states.index(mr.DRAFT)
    # The ladder is unchanged and still puts `ready` late.
    assert mr.MERGE_STATES.index(mr.READY) > mr.MERGE_STATES.index(mr.DRAFT)


def test_every_pr_lands_in_exactly_one_group():
    prs = [_pr(1), _pr(2, isDraft=True), _pr(3, mergeable="CONFLICTING")]
    report = mr.build_report(prs, default_branch="main", now=NOW)
    groups = mr.group_by_state(report)
    assert sum(g["count"] for g in groups) == len(prs)
    numbers = sorted(r["number"] for g in groups for r in g["prs"])
    assert numbers == [1, 2, 3]


def test_a_state_outside_the_attention_order_is_appended_not_dropped():
    """The vocabulary can grow. A group the presentation list has never heard
    of must still be shown — silently dropping PRs is the failure mode."""
    report = mr.build_report([_pr(1)], default_branch="main", now=NOW)
    report["prs"][0]["pipeline_state"] = "some_new_state"
    groups = mr.group_by_state(report)
    assert [g["state"] for g in groups] == ["some_new_state"]
    assert groups[0]["count"] == 1


def test_groups_flag_what_is_waiting_on_automation():
    prs = [_pr(1, mergeStateStatus="BEHIND"), _pr(2, isDraft=True)]
    report = mr.build_report(prs, default_branch="main", now=NOW)
    flags = {g["state"]: g["blocked_on_automation"]
             for g in mr.group_by_state(report)}
    assert flags[mr.BEHIND_MAIN] is True
    assert flags[mr.DRAFT] is False


def test_render_grouped_is_ascii_and_shows_every_required_column():
    """number, branch, whether a task points at it, the state, the reason,
    and the age — all six, and no character a cp1252 console cannot print."""
    pr = _pr(42, headRefName="kanban/kpr-watch-03")
    report = mr.build_report(
        [pr], default_branch="main", linked_urls=[pr["url"]],
        linked_tasks={pr["url"]: {"task_id": "kpr-watch-03",
                                  "task_status": "pr_opened"}},
        now=NOW)
    text = mr.render_grouped(report)
    text.encode("ascii")                      # raises if a dash slipped in
    assert "#42" in text                      # number
    assert "kanban/kpr-watch-03" in text      # branch AND task id
    assert "READY" in text                    # state (the group header)
    assert "1h00m" in text                    # age
    assert "LOWER BOUND" in text              # and it says what the age IS
    assert "green, mergeable" in text         # reason


def test_render_grouped_says_when_the_board_was_unreadable():
    report = mr.build_report([_pr(1)], default_branch="main",
                             linked_lookup_ok=False, now=NOW)
    assert "WARNING" in mr.render_grouped(report)


# ── 4. read-only ────────────────────────────────────────────────────────────

def test_the_dashboard_route_is_get_only_and_never_merges():
    """No merge button, no un-draft action. The route is GET (Flask's default,
    and no `methods=` widening it), and nothing in its body calls a mutating
    helper."""
    app_path = REPO_ROOT / "tools" / "dashboard" / "app.py"
    app_src = app_path.read_text(encoding="utf-8")
    assert '@app.route("/api/merge-readiness")' in app_src
    # No POST/PUT/DELETE sibling on the same path, anywhere in the module.
    assert not re.search(
        r'@app\.route\(\s*"/api/merge-readiness[^"]*"\s*,\s*methods', app_src)
    body = _function_code(app_path, "api_merge_readiness")
    for forbidden in ("pr merge", "merge_pr", "gh_merge", "--merge",
                      "ready-for-review", "push", "INSERT", "UPDATE"):
        assert forbidden not in body, forbidden


def test_the_panel_has_no_action_control():
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates"
           / "_autonomy_status.html").read_text(encoding="utf-8")
    panel = tpl.split('id="merge-readiness"', 1)[1]
    for forbidden in ("<button", "<form", "method: 'POST'", 'method: "POST"',
                      "onclick="):
        assert forbidden not in panel, forbidden
    # The only outbound links are to the PRs themselves.
    assert 'href="' + "' + escapeHTML(r.url" in panel


def test_the_panel_escapes_every_field_it_renders():
    """A branch name, a task id and a reason are DATA off the forge and the
    board. The panel builds HTML by concatenation, so each one has to be
    escaped at its own interpolation site."""
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates"
           / "_autonomy_status.html").read_text(encoding="utf-8")
    row = tpl.split("function mrRowHTML(r) {", 1)[1].split("\n  }", 1)[0]
    for field in ("r.task_id", "r.url", "r.head"):
        assert "escapeHTML(" + field in row or field + " ||" in row, field
    assert row.count("escapeHTML(") >= 5


def test_the_panel_stays_visible_when_the_report_breaks():
    """A panel that disappears when it breaks is indistinguishable from a clean
    board — the exact ambiguity this card exists to remove."""
    app_src = (REPO_ROOT / "tools" / "dashboard" / "app.py").read_text(
        encoding="utf-8")
    body = app_src.split('def api_merge_readiness():', 1)[1].split(
        "\n    @app.route", 1)[0]
    assert 'payload["visible"] = bool(payload["total"]) or error is not None' in body


# ── 5. the CLI surface ──────────────────────────────────────────────────────

def test_kanban_cli_exposes_awaiting_merge_read_only():
    cli_path = REPO_ROOT / "tools" / "kanban" / "cli.py"
    src = cli_path.read_text(encoding="utf-8")
    assert "def cmd_awaiting_merge(" in src
    assert '"--awaiting-merge"' in src
    body = _function_code(cli_path, "cmd_awaiting_merge")
    # It reads the SHARED gatherer and nothing else — no second `gh pr list`.
    assert "mr.collect_report(" in body
    assert "subprocess" not in body
    for forbidden in ("pr merge", "--merge", "set_status", "UPDATE ",
                      "INSERT ", "push"):
        assert forbidden not in body, forbidden


def test_awaiting_merge_returns_two_when_the_report_cannot_be_produced(monkeypatch):
    """Exit 2, never 0-with-an-empty-table. A report that listed nothing must
    not read like a repo with nothing open."""
    from tools.kanban import cli

    def boom(**_kw):
        raise RuntimeError("gh: command not found")

    monkeypatch.setattr(mr, "collect_report", boom)
    assert cli.cmd_awaiting_merge(json_out=True) == 2


def test_awaiting_merge_json_carries_the_groups(monkeypatch, capsys):
    from tools.kanban import cli

    pr = _pr(9)
    report = mr.build_report([pr], default_branch="main", now=NOW)
    monkeypatch.setattr(mr, "collect_report", lambda **_kw: report)
    assert cli.cmd_awaiting_merge(json_out=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["groups"][0]["state"] == mr.READY
    assert payload["prs"][0]["number"] == 9


def test_collect_report_degrades_when_the_board_is_unreadable(monkeypatch, tmp_path):
    """FAIL-OPEN and never silent: no board means nothing can be identified as
    task-linked, and the report has to SAY which fact it is missing and why."""
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps([_pr(1)]), encoding="utf-8")

    def boom():
        raise RuntimeError("no such table: kanban_tasks")

    monkeypatch.setattr(mr, "linked_pr_tasks", boom)
    report = mr.collect_report(from_json=str(fixture), default_branch="main",
                               measure_behind=False)
    assert report["linked_lookup_ok"] is False
    assert "kanban_tasks" in report["linked_lookup_error"]
    assert report["prs"][0]["state"] == mr.READY      # classified on its merits
    assert report["prs"][0]["linked"] is False
