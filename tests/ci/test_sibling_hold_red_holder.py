# CUI // SP-CTI
"""kpr-watch-23: a sibling that CANNOT merge is not a queue position.

MEASURED 2026-10-10: #2400 and #2402 were green, CLEAN and within the
behind-limit, held by `sibling_hold` for 81-138 min behind #2397, whose
REQUIRED `Test` check had been red for ~5h. All three shared
tools/cli/__main__.py. `_open_pr_index` fetched only `url,files,mergeable,
isDraft`, so the tie-break could not see that the holder was red — or that a
holder touched a protected path only a human may merge. merge_stall called the
holds `by_design`, so nothing alarmed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import tools.ci.merge_stall as ms
import tools.ci.pr_watcher as pw

SHARED = "tools/cli/__main__.py"
REQUIRED = frozenset({"Lint", "Test", "Security Scan", "Helm Lint"})


def _url(n):
    return f"https://github.com/icdev-ai/ICDev/pull/{n}"


def _check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"name": name, "conclusion": conclusion, "status": status}


def _green():
    return [_check(n) for n in sorted(REQUIRED)]


def _pr(n, rollup, files=(SHARED,)):
    return {"url": _url(n), "files": [{"path": f} for f in files],
            "mergeable": "MERGEABLE", "isDraft": False,
            "statusCheckRollup": rollup}


class _Runner:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        payload = self.payload

        class P:
            returncode = 0
            stdout = json.dumps(payload)
            stderr = ""

        return P()


def _watcher(payload, protected=()):
    w = pw.PRWatcher.__new__(pw.PRWatcher)   # pure seam: no forge, no db
    w.config = {"protected_paths": list(protected), "required_checks_only": True}
    w._pr_list_runner = _Runner(payload)
    w.required_checks = lambda: REQUIRED
    return w


def _winner_among(payload, protected=()):
    """Run the same derivation poll_once does and return who wins the tie-break."""
    index = _watcher(payload, protected)._open_pr_index()
    blocked = {u for u, e in index.items() if not pw._pr_can_merge(e)}
    files = {u: e["files"] for u, e in index.items()}
    winners = []
    for url in files:
        sib = {o: fs & files[url] for o, fs in files.items()
               if o != url and fs & files[url]}
        if url not in blocked and pw._wins_sibling_tiebreak(url, sib, blocked=blocked):
            winners.append(url)
    return winners


def test_the_listing_carries_the_rollup_in_the_same_single_call():
    w = _watcher([_pr(1, _green())])
    w._open_pr_index()
    assert len(w._pr_list_runner.calls) == 1
    fields = w._pr_list_runner.calls[0][w._pr_list_runner.calls[0].index("--json") + 1]
    assert "statusCheckRollup" in fields.split(",")


def test_a_red_required_holder_no_longer_holds_the_green_sibling():
    red = _green()[:-1] + [_check("Test", "FAILURE")]
    payload = [_pr(2397, red), _pr(2400, _green())]
    index = _watcher(payload)._open_pr_index()
    assert index[_url(2397)]["ci_failed"] is True
    assert index[_url(2400)]["ci_failed"] is False
    assert _winner_among(payload) == [_url(2400)]


def test_every_failure_conclusion_counts():
    for conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
        rollup = [_check("Lint"), _check("Test", conclusion)]
        assert _winner_among([_pr(1, rollup), _pr(2, _green())]) == [_url(2)], conclusion


def test_a_pending_holder_still_holds_it():
    """A sibling mid-CI keeps its queue position — that is serialisation working."""
    pending = [_check("Lint"), {"name": "Test", "conclusion": "", "status": "IN_PROGRESS"}]
    payload = [_pr(2397, pending), _pr(2400, _green())]
    assert _watcher(payload)._open_pr_index()[_url(2397)]["ci_failed"] is False
    assert _winner_among(payload) == [_url(2397)]


def test_a_red_non_required_check_does_not_drop_the_holder():
    """`Test (Windows)` is advisory; the forge would still merge the holder."""
    rollup = _green() + [_check("Test (Windows)", "FAILURE")]
    payload = [_pr(1, rollup), _pr(2, _green())]
    assert _watcher(payload)._open_pr_index()[_url(1)]["ci_failed"] is False
    assert _winner_among(payload) == [_url(1)]


def test_a_protected_path_holder_is_skipped():
    hook = ".claude/hooks/pre_tool_use.py"
    payload = [_pr(2384, _green(), files=(SHARED, hook)), _pr(2400, _green())]
    index = _watcher(payload, protected=[hook])._open_pr_index()
    assert index[_url(2384)]["protected"] is True
    assert index[_url(2400)]["protected"] is False
    assert _winner_among(payload, protected=[hook]) == [_url(2400)]


def test_a_green_lower_sibling_still_holds_it():
    payload = [_pr(2397, _green()), _pr(2400, _green())]
    assert _winner_among(payload) == [_url(2397)]


def test_two_prs_sharing_a_file_never_both_win():
    red = [_check("Test", "FAILURE")]
    payload = [_pr(1, red), _pr(2, _green()), _pr(3, _green()), _pr(4, [])]
    assert _winner_among(payload) == [_url(2)]


def test_the_required_set_is_not_resolved_when_nothing_is_red():
    w = _watcher([_pr(1, _green()), _pr(2, [])])
    calls = []
    w.required_checks = lambda: calls.append(1) or REQUIRED
    w._open_pr_index()
    assert calls == []


# ── merge_stall: a hold behind a holder that cannot merge is an ALARM ──────

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
CONFIG = {"stall_after_minutes": 15.0, "by_design_stall_after_minutes": 180.0}


def _stall_pr(n, rollup, files=(SHARED,), age_min=90):
    green_at = (NOW - timedelta(minutes=age_min)).isoformat()
    return {"number": n, "url": _url(n), "title": "t", "headRefName": f"b{n}",
            "headRefOid": f"sha{n}", "baseRefName": "main", "isDraft": False,
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "labels": [],
            "state": "OPEN", "reviews": [],
            "statusCheckRollup": [dict(c, completedAt=green_at) for c in rollup],
            "files": [{"path": f} for f in files]}


def _stall_report(prs, protected=()):
    rows = ms.eligibility_rows(prs, default_branch="main", protected_paths=protected,
                               behind_by_url={p["url"]: 0 for p in prs},
                               required_checks=REQUIRED)
    holds = {_url(2400): {"cause": ms.CAUSE_SIBLING_HOLD,
                          "reason": "held: sibling file conflict with 1 open PR(s)"}}
    report = ms.build_stall_report(rows, now=NOW, config=CONFIG, holds=holds,
                                   watcher_alive=True, forge_ok=True)
    return {r["url"]: r for r in report["prs"]}


def test_merge_stall_alarms_on_a_hold_behind_a_red_holder():
    red = _green()[:-1] + [_check("Test", "FAILURE")]
    row = _stall_report([_stall_pr(2397, red), _stall_pr(2400, _green())])[_url(2400)]
    assert row["severity"] == ms.SEV_ALARM, row["detail"]
    assert "#2397" in row["detail"]


def test_merge_stall_alarms_on_a_hold_behind_a_protected_holder():
    hook = ".claude/hooks/pre_tool_use.py"
    prs = [_stall_pr(2384, _green(), files=(SHARED, hook)), _stall_pr(2400, _green())]
    row = _stall_report(prs, protected=[hook])[_url(2400)]
    assert row["severity"] == ms.SEV_ALARM, row["detail"]


def test_merge_stall_keeps_a_hold_behind_a_green_holder_by_design():
    row = _stall_report([_stall_pr(2397, _green()), _stall_pr(2400, _green())])[_url(2400)]
    assert row["severity"] == ms.SEV_BY_DESIGN, row["detail"]


def test_classify_stall_holder_alarm_waits_for_the_unattributed_threshold():
    young = ms.classify_stall(eligible=True, age_minutes=5, hold_cause=ms.CAUSE_SIBLING_HOLD,
                              blocked_holders=["#1"])
    old = ms.classify_stall(eligible=True, age_minutes=20, hold_cause=ms.CAUSE_SIBLING_HOLD,
                            blocked_holders=["#1"])
    assert young.severity == ms.SEV_BY_DESIGN
    assert old.severity == ms.SEV_ALARM
