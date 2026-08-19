# CUI // SP-CTI
"""kpr-watch-02: alarm on eligible-but-unmerged.

The load-bearing claim is not "a stall is detected" — it is "a stall is
detected AND a hold that is working as designed is not reported as one". The
survey in args/merge_stall.yaml measured what happens if that distinction is
dropped: the same 20-minute threshold goes from 0.00% to 4.67% of routine
merges, which is three times the rate CLAUDE.md already calls grounds for
standing a check down. So most of what is asserted here is the SHAPE of the
attribution, not just the arithmetic of the age.
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from tools.ci import merge_stall as ms

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _ready_pr(number=1, url="https://github.com/o/r/pull/1", **over):
    """A PR the merge-eligibility ladder classifies `ready`."""
    pr = {
        "number": number, "url": url, "title": "t",
        "headRefName": "feat/x", "headRefOid": "sha-%d" % number,
        "baseRefName": "main", "isDraft": False, "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN", "labels": [], "reviews": [],
        "state": "OPEN",
        # NOT omitted. `protected_hits` fails CLOSED, so a PR whose changed-file
        # set is unavailable is `protected_path`, never `ready` -- a fixture
        # without this key silently tests the wrong rung.
        "files": [{"path": "README.md"}],
        "statusCheckRollup": [
            {"name": "Test", "conclusion": "SUCCESS",
             "completedAt": "2026-08-19T11:00:00Z"}],
    }
    pr.update(over)
    return pr


# ────────────────────────────────────────────────────────────────────────────
# The decision table — pure, so every rung is reachable without a forge
# ────────────────────────────────────────────────────────────────────────────


def test_an_ineligible_pr_is_never_a_stall():
    """The ladder already explains a refusal. This module must not double-report
    it — every rung `merge_readiness` refuses on is somebody else's finding."""
    v = ms.classify_stall(eligible=False, age_minutes=9999.0,
                          ineligible_reason="checks still running: Test")
    assert v.severity == ms.SEV_OK
    assert v.cause == ms.CAUSE_NOT_ELIGIBLE
    assert not v.alarming
    assert "checks still running" in v.detail


def test_eligible_and_aged_out_with_no_cause_is_the_alarm():
    """The state the card exists for."""
    v = ms.classify_stall(eligible=True, age_minutes=25.0,
                          stall_after_minutes=20.0)
    assert (v.severity, v.cause) == (ms.SEV_ALARM, ms.CAUSE_UNATTRIBUTED)
    assert v.alarming


def test_eligible_and_young_is_not_an_alarm():
    v = ms.classify_stall(eligible=True, age_minutes=19.9,
                          stall_after_minutes=20.0)
    assert (v.severity, v.cause) == (ms.SEV_OK, ms.CAUSE_WITHIN_THRESHOLD)
    assert not v.alarming


@pytest.mark.parametrize("cause", sorted(ms.BY_DESIGN_CAUSES))
def test_a_hold_working_as_designed_is_not_reported_as_stuck(cause):
    """THE POINT OF THE WHOLE MODULE. Every by-design cause, past the
    unattributed threshold, must still not alarm — that is the 4.67% -> 0.00%
    the survey measured, and it is why the report is worth reading."""
    v = ms.classify_stall(eligible=True, age_minutes=120.0,
                          stall_after_minutes=20.0,
                          by_design_after_minutes=180.0, hold_cause=cause)
    assert v.severity == ms.SEV_BY_DESIGN, cause
    assert v.cause == cause
    assert not v.alarming
    assert "working as designed" in v.detail


@pytest.mark.parametrize("cause", sorted(ms.BY_DESIGN_CAUSES))
def test_a_by_design_hold_that_never_ends_DOES_escalate(cause):
    """"The sibling-conflict hold serialising a merge indefinitely" is one of the
    four causes the card names. A category that can never escalate is a category
    people stop reading, so the hold gets its own ceiling — and keeps its own
    cause, so the alarm still says where to look."""
    v = ms.classify_stall(eligible=True, age_minutes=181.0,
                          stall_after_minutes=20.0,
                          by_design_after_minutes=180.0, hold_cause=cause)
    assert v.severity == ms.SEV_ALARM, cause
    assert v.cause == cause, "escalating must not erase which hold it was"
    assert v.alarming


def test_a_dead_daemon_outranks_every_per_pr_rung():
    """One outage must not print N identical per-PR alarms. It is also reported
    with NO threshold: "the merger is down" does not become truer with time."""
    v = ms.classify_stall(eligible=True, age_minutes=0.1, watcher_alive=False,
                          hold_cause=ms.CAUSE_SIBLING_HOLD)
    assert (v.severity, v.cause) == (ms.SEV_OUTAGE, ms.CAUSE_DAEMON_DOWN)
    assert v.alarming


def test_a_refused_credential_is_an_outage_not_a_stall():
    """"An auth failure looks exactly like 'nothing to merge'" — the card's
    words. It must not be reported as the PRs being stuck."""
    v = ms.classify_stall(eligible=True, age_minutes=0.1, forge_ok=False)
    assert (v.severity, v.cause) == (ms.SEV_OUTAGE, ms.CAUSE_FORGE_AUTH)


@pytest.mark.parametrize("watcher,forge", [(None, None), (True, None),
                                           (None, True), (True, True)])
def test_an_unmeasured_probe_never_manufactures_an_outage(watcher, forge):
    """TRISTATE. `None` is "could not measure", and a probe that cannot run must
    not invent a finding — only an explicit False does."""
    v = ms.classify_stall(eligible=True, age_minutes=1.0,
                          watcher_alive=watcher, forge_ok=forge)
    assert v.severity != ms.SEV_OUTAGE


def test_an_unmeasured_age_can_never_raise_an_alarm():
    """The same posture `merge_readiness` takes for `behind_by`: an unmeasured
    quantity is not a zero and is not evidence. It also is not `ok` — that would
    hide a stall — so it gets its own severity."""
    v = ms.classify_stall(eligible=True, age_minutes=None)
    assert (v.severity, v.cause) == (ms.SEV_UNMEASURED, ms.CAUSE_UNMEASURED)
    assert not v.alarming


def test_auto_merge_switched_off_is_by_design_not_a_stall():
    v = ms.classify_stall(eligible=True, age_minutes=9999.0, merger_enabled=False)
    assert (v.severity, v.cause) == (ms.SEV_BY_DESIGN, ms.CAUSE_MERGER_DISABLED)


def test_a_per_pr_forge_failure_is_an_outage_not_a_by_design_hold():
    v = ms.classify_stall(eligible=True, age_minutes=30.0,
                          hold_cause=ms.CAUSE_FORGE_UNREACHABLE)
    assert v.severity == ms.SEV_OUTAGE


def test_severity_and_cause_vocabularies_do_not_overlap():
    """`by_design` and `outage` must stay disjoint or a cause would classify two
    ways depending on which frozenset was consulted first."""
    assert not (ms.BY_DESIGN_CAUSES & ms.OUTAGE_CAUSES)
    assert ms.SEV_ALARM in ms.SEVERITIES and ms.SEV_OK in ms.SEVERITIES


# ────────────────────────────────────────────────────────────────────────────
# Attribution — every pattern was taken from a live row, and must still match one
# ────────────────────────────────────────────────────────────────────────────

#: Reason strings copied VERBATIM from `audit_trail` rows written by pr_watcher
#: (actor='pr_watcher'), with the counts observed in the 7 days to 2026-08-19.
#: A pattern that matches nothing is indistinguishable from a cause that never
#: happens, which is how an attribution table quietly stops attributing — so the
#: fixtures are real text, not text shaped like the patterns.
LIVE_REASONS = [
    ("held: sibling file conflict with 3 open PR(s); a lower-numbered sibling "
     "goes first", ms.CAUSE_SIBLING_HOLD),
    ("enforced gate: awaiting ICDEV done-verification", ms.CAUSE_DONE_GATE),
    ("enforced gate: ICDEV verification result=failed (e.g. conformance)",
     ms.CAUSE_DONE_GATE),
    ("fetch failed: gh pr view failed: exit=1 stderr=HTTP 503: No server is "
     "currently available to service your request.", ms.CAUSE_FORGE_UNREACHABLE),
    ("fetch failed: gh pr view failed: exit=1 stderr=error connecting to "
     "api.github.com", ms.CAUSE_FORGE_UNREACHABLE),
    ("CI still running", ms.CAUSE_CI_RUNNING),
    ("approval required or merge blocked", ms.CAUSE_APPROVAL_REQUIRED),
    ("touches protected path(s) tools/ci/pr_watcher.py -- a human must merge "
     "this", ms.CAUSE_PROTECTED_PATH),
]


@pytest.mark.parametrize("reason,expected", LIVE_REASONS)
def test_live_watcher_reasons_attribute_to_their_cause(reason, expected):
    assert ms.attribute_reason(reason) == expected


@pytest.mark.parametrize("reason,expected", LIVE_REASONS)
def test_the_shipped_config_attributes_the_same_way_as_the_fallback(reason, expected):
    """args/merge_stall.yaml and DEFAULT_HOLD_PATTERNS are two copies of one
    table. If they disagree, an unreadable config silently changes the verdict."""
    assert ms.attribute_reason(reason, ms.load_config()["hold_patterns"]) == expected


def test_an_unrecognised_reason_stays_unattributed():
    """`None` is the honest answer and it is the one that becomes the alarm. A
    catch-all pattern here would empty the unattributed bucket and the module
    would report a healthy pipeline forever."""
    assert ms.attribute_reason("something nobody has seen before") is None
    assert ms.attribute_reason("") is None


def test_forge_patterns_are_ordered_after_the_specific_ones():
    """First match wins, so a refusal that merely MENTIONS an HTTP error must
    still classify on its own terms."""
    assert ms.attribute_reason(
        "held: sibling file conflict with 2 open PR(s) (HTTP 503 earlier)"
    ) == ms.CAUSE_SIBLING_HOLD


# ────────────────────────────────────────────────────────────────────────────
# Age — two sources, never merged
# ────────────────────────────────────────────────────────────────────────────


def _report(rows, **kw):
    cfg = {"stall_after_minutes": 20.0, "by_design_stall_after_minutes": 180.0}
    return ms.build_stall_report(rows, now=NOW, config=cfg, **kw)


def _elig_row(url="u1", head_sha="sha1", **over):
    row = {"number": 1, "url": url, "title": "", "head": "feat/x",
           "head_sha": head_sha, "state": ms.READY, "reason": "green",
           "eligible": True, "door": ms.DOOR_UNLINKED, "ci_green_at": None}
    row.update(over)
    return row


def test_a_recorded_first_seen_ready_beats_the_ci_estimate():
    rep = _report(
        [_elig_row(ci_green_at=(NOW - timedelta(hours=5)).isoformat())],
        observations={"u1": {"head_sha": "sha1", "eligible": 1,
                             "observed_at": (NOW - timedelta(minutes=3)).isoformat()}})
    row = rep["prs"][0]
    assert row["ready_since_source"] == "recorded"
    assert row["age_minutes"] == pytest.approx(3.0, abs=0.1)
    # The estimate is still reported. "recorded 3 min but green 5 hours ago" has
    # to stay legible rather than being collapsed into one number.
    assert row["ci_green_age_minutes"] == pytest.approx(300.0, abs=0.1)
    assert row["severity"] == ms.SEV_OK


def test_a_recorded_row_for_a_DIFFERENT_head_sha_is_not_reused():
    """A force-push is a NEW merge opportunity. Carrying the old clock forward
    would date the PR to a commit that no longer exists — and, because the old
    row is older, it would do so in the direction that raises a false alarm."""
    rep = _report(
        [_elig_row(head_sha="sha2",
                   ci_green_at=(NOW - timedelta(minutes=2)).isoformat())],
        observations={"u1": {"head_sha": "sha1", "eligible": 1,
                             "observed_at": (NOW - timedelta(hours=9)).isoformat()}})
    row = rep["prs"][0]
    assert row["ready_since_source"] == "ci_estimate"
    assert row["age_minutes"] == pytest.approx(2.0, abs=0.1)
    assert row["severity"] == ms.SEV_OK


def test_a_recorded_row_that_was_INELIGIBLE_does_not_start_the_clock():
    """The newest row saying `awaiting_ci` means the recorder last saw it not
    ready. Its timestamp is not a first-seen-READY and must not be read as one."""
    rep = _report(
        [_elig_row(ci_green_at=(NOW - timedelta(minutes=1)).isoformat())],
        observations={"u1": {"head_sha": "sha1", "eligible": 0,
                             "observed_at": (NOW - timedelta(hours=9)).isoformat()}})
    assert rep["prs"][0]["ready_since_source"] == "ci_estimate"


def test_no_record_and_no_rollup_is_unmeasured_never_zero():
    rep = _report([_elig_row(ci_green_at=None)])
    row = rep["prs"][0]
    assert row["age_minutes"] is None
    assert row["ready_since_source"] == "unmeasured"
    assert row["severity"] == ms.SEV_UNMEASURED
    assert rep["alarms"] == 0
    # "?" and not "0" -- an unmeasured age and a zero age are different facts.
    assert " ? " in ms.render_table(rep) or "?" in ms.render_table(rep)


def test_ci_green_at_ignores_the_epoch_placeholder():
    """`gh` emits 0001-01-01T00:00:00Z for a check run that exists but has not
    started. Treating it as a completion dates the PR to the first century, and
    every such PR would alarm forever."""
    pr = _ready_pr(statusCheckRollup=[
        {"name": "E2E", "completedAt": "0001-01-01T00:00:00Z"},
        {"name": "Test", "conclusion": "SUCCESS",
         "completedAt": "2026-08-19T11:00:00Z"}])
    assert ms.ci_green_at(pr) == datetime(2026, 8, 19, 11, 0, tzinfo=timezone.utc)
    assert ms.ci_green_at({"statusCheckRollup": [
        {"name": "E2E", "completedAt": "0001-01-01T00:00:00Z"}]}) is None
    assert ms.ci_green_at({"statusCheckRollup": []}) is None


def test_the_alarm_row_sorts_above_the_healthy_ones():
    rep = _report([
        _elig_row(url="ok", head_sha="a",
                  ci_green_at=(NOW - timedelta(minutes=1)).isoformat()),
        _elig_row(url="bad", number=2, head_sha="b",
                  ci_green_at=(NOW - timedelta(minutes=90)).isoformat()),
    ])
    assert rep["prs"][0]["url"] == "bad"
    assert rep["prs"][0]["severity"] == ms.SEV_ALARM
    assert rep["alarms"] == 1


# ────────────────────────────────────────────────────────────────────────────
# Eligibility is asked with the `linked` rung SKIPPED
# ────────────────────────────────────────────────────────────────────────────


def test_a_task_linked_pr_is_judged_on_its_merits_and_keeps_its_door():
    """`linked` says WHICH ACTOR merges, not whether the PR is finished — and
    the task path is where three of the four named causes live. Collapsing the
    two is what made every stall on that path invisible."""
    pr = _ready_pr(url="https://x/pull/7", number=7)
    rows = ms.eligibility_rows([pr], default_branch="main",
                               linked_urls={"https://x/pull/7"})
    assert rows[0]["state"] == ms.READY, "the linked rung must not short-circuit"
    assert rows[0]["eligible"] is True
    assert rows[0]["door"] == ms.DOOR_LINKED


def test_an_unlinked_pr_gets_the_unlinked_door():
    rows = ms.eligibility_rows([_ready_pr(url="https://x/pull/8", number=8)],
                               default_branch="main", linked_urls=set())
    assert rows[0]["door"] == ms.DOOR_UNLINKED


def test_a_protected_path_pr_is_not_eligible_at_all():
    """A human must merge it, so it can never be a stall. Note this is the ONE
    place where `files` being absent must NOT read as clean — the ladder fails
    closed and this module inherits that."""
    pr = _ready_pr(url="https://x/pull/9", number=9,
                   files=[{"path": "tools/ci/pr_watcher.py"}])
    rows = ms.eligibility_rows([pr], default_branch="main",
                               protected_paths=["tools/ci/pr_watcher.py"])
    assert rows[0]["eligible"] is False
    assert rows[0]["state"] == "protected_path"


# ────────────────────────────────────────────────────────────────────────────
# Recording — append-only, and only on a transition
# ────────────────────────────────────────────────────────────────────────────


class _FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return self

    def commit(self):
        pass

    def close(self):
        pass


def test_a_row_is_written_only_when_the_state_changes():
    conn = _FakeConn()
    rows = [_elig_row(url="u1", head_sha="s1")]
    res = ms.record_transitions(
        rows, previous={"u1": {"state": ms.READY, "head_sha": "s1"}},
        now=NOW, get_connection=lambda: conn)
    assert res["written"] == 0 and res["unchanged"] == 1
    assert conn.sql == [], "an unchanged PR must not cost a write every poll"

    res = ms.record_transitions(
        rows, previous={"u1": {"state": "awaiting_ci", "head_sha": "s1"}},
        now=NOW, get_connection=lambda: conn)
    assert res["written"] == 1


def test_a_new_head_sha_is_a_transition_even_at_the_same_state():
    """A force-push to a `ready` PR is a new merge opportunity whose clock must
    restart, and the state string alone cannot see it."""
    conn = _FakeConn()
    res = ms.record_transitions(
        [_elig_row(url="u1", head_sha="s2")],
        previous={"u1": {"state": ms.READY, "head_sha": "s1"}},
        now=NOW, get_connection=lambda: conn)
    assert res["written"] == 1


def test_recording_only_ever_inserts():
    """Append-only: `pr_merge_eligibility_events` is registered in
    APPEND_ONLY_TABLES. An observation does not stop being true later."""
    conn = _FakeConn()
    ms.record_transitions([_elig_row()], previous={}, now=NOW,
                          get_connection=lambda: conn)
    assert conn.sql, "expected an insert"
    for sql, _ in conn.sql:
        upper = sql.upper()
        assert upper.lstrip().startswith("INSERT INTO")
        for verb in ("UPDATE ", "DELETE ", "DROP ", "TRUNCATE"):
            assert verb not in upper


def test_the_recorded_classification_is_a_label_not_a_banner():
    """`classification` feeds the RLS predicate. 'CUI // SP-CTI' matches no label
    at any clearance, so the row would be written, retained and invisible."""
    conn = _FakeConn()
    ms.record_transitions([_elig_row()], previous={}, now=NOW,
                          get_connection=lambda: conn)
    params = conn.sql[0][1]
    assert "CUI" in params and not any(
        isinstance(p, str) and "//" in p for p in params)


def test_a_recording_failure_never_raises():
    """The caller is a report that must still print, or a watch loop that must
    never stop. Failure is returned, not thrown — and never silently."""
    class _Boom(_FakeConn):
        def execute(self, sql, params=None):
            raise RuntimeError("no such table")

    res = ms.record_transitions([_elig_row()], previous={}, now=NOW,
                                get_connection=lambda: _Boom())
    assert res["ok"] is False
    assert "no such table" in res["error"]


def test_a_pr_with_no_url_is_never_recorded():
    conn = _FakeConn()
    res = ms.record_transitions([_elig_row(url="")], previous={}, now=NOW,
                                get_connection=lambda: conn)
    assert res["written"] == 0 and conn.sql == []


# ────────────────────────────────────────────────────────────────────────────
# Survey — the arithmetic the shipped threshold rests on
# ────────────────────────────────────────────────────────────────────────────


def _merged(number, green_minutes_before_merge, merged="2026-08-19T12:00:00Z",
            extra_checks=()):
    merged_at = ms.parse_ts(merged)
    green = merged_at - timedelta(minutes=green_minutes_before_merge)
    checks = [{"name": "Test", "conclusion": "SUCCESS",
               "completedAt": green.isoformat()}]
    checks.extend(extra_checks)
    return {"number": number, "url": "https://x/pull/%d" % number,
            "mergedAt": merged, "statusCheckRollup": checks}


def test_survey_ignores_checks_that_completed_after_the_merge():
    """`E2E (Playwright)` and `Two-Tier LLM Build` are `needs:`-gated jobs whose
    check runs do not exist yet when the merger polls. Counting them dated 57 of
    150 real PRs as merged BEFORE they were green, i.e. discarded 38% of the
    sample the threshold is derived from."""
    late = {"name": "E2E (Playwright)", "conclusion": "SUCCESS",
            "completedAt": "2026-08-19T12:30:00Z"}
    res = ms.survey_merged([_merged(1, 5.0, extra_checks=[late])])
    assert res["measured"] == 1
    assert res["skipped"] == {}
    assert res["raw"]["max"] == pytest.approx(5.0, abs=0.01)


def test_survey_splits_attributed_from_unattributed():
    prs = [_merged(1, 2.0), _merged(2, 90.0)]
    res = ms.survey_merged(
        prs, holds_for=lambda url, since, until:
        ms.CAUSE_DONE_GATE if url.endswith("/2") else None)
    assert res["attributed"]["n"] == 1 and res["attributed"]["max"] == pytest.approx(90.0, abs=0.1)
    assert res["unattributed"]["n"] == 1 and res["unattributed"]["max"] == pytest.approx(2.0, abs=0.1)
    assert res["causes"] == {ms.CAUSE_DONE_GATE: 1}
    # The whole finding: the same threshold, with and without attribution.
    assert res["fire_rate_raw"]["20"] == 50.0
    assert res["fire_rate_attributed"]["20"] == 0.0


def test_survey_fire_rate_denominator_is_the_whole_population():
    """A routine merge that was correctly attributed still happened, so it stays
    in the denominator — otherwise attribution flatters itself by shrinking the
    thing it is measured against."""
    res = ms.survey_merged(
        [_merged(i, 90.0) for i in range(4)],
        holds_for=lambda u, s, e: ms.CAUSE_SIBLING_HOLD if u.endswith("/0") else None)
    assert res["measured"] == 4
    assert res["fire_rate_attributed"]["20"] == 75.0


def test_survey_reports_no_data_as_none_not_zero():
    p = ms._percentiles([])
    assert p["n"] == 0 and p["p50"] is None and p["max"] is None


def test_survey_skips_are_counted_never_silently_dropped():
    res = ms.survey_merged([{"number": 1, "url": "u", "mergedAt": None},
                            {"number": 2, "url": "u2",
                             "mergedAt": "2026-08-19T12:00:00Z",
                             "statusCheckRollup": []}])
    assert res["measured"] == 0
    assert res["skipped"] == {"no_mergedAt": 1, "no_check_before_merge": 1}


def test_watcher_holds_bounds_the_window_above():
    """THE REGRESSION. `watcher_holds` scans newest-first; without an upper
    bound a row written AFTER the moment being explained wins and masks the
    earlier row that actually answered. Live, "after now" is empty so it never
    bit — retrospectively it attributed 28 of 150 merged PRs where the bounded
    version attributes 30, and moved the unattributed maximum from 10.65 to
    19.98 min, i.e. it argued the threshold out of its own evidence."""
    seen = {}

    class _Conn(_FakeConn):
        def execute(self, sql, params=None):
            seen["sql"], seen["params"] = sql, params
            return self

        def fetchall(self):
            return []

    until = NOW - timedelta(hours=2)
    ms.watcher_holds(["u1"], since=NOW - timedelta(hours=6), until=until,
                     get_connection=lambda: _Conn())
    assert "created_at <= " in seen["sql"], seen["sql"]
    assert until.isoformat() in seen["params"]


def test_watcher_holds_only_attributes_a_row_whose_SUBJECT_is_this_pr():
    """`details` is a serialized WatcherAction and the SQL match is a LIKE over
    the whole blob, so a url mentioned in another PR's reason would otherwise
    attribute that PR's hold to this one — precisely what a sibling-conflict
    reason does, since it names the sibling's url."""
    other = json.dumps({"pr_url": "https://x/pull/2", "action": "wait",
                        "reason": "held: sibling file conflict with "
                                  "https://x/pull/1"})
    mine = json.dumps({"pr_url": "https://x/pull/1", "action": "wait",
                       "reason": "enforced gate: awaiting ICDEV done-verification"})

    class _Conn(_FakeConn):
        def execute(self, sql, params=None):
            return self

        def fetchall(self):
            return [{"created_at": "2026-08-19T11:00:00Z", "details": other},
                    {"created_at": "2026-08-19T10:00:00Z", "details": mine}]

    got = ms.watcher_holds(["https://x/pull/1"], get_connection=lambda: _Conn())
    assert got["https://x/pull/1"]["cause"] == ms.CAUSE_DONE_GATE


def test_watcher_holds_fails_open_to_unattributed():
    """Missing evidence must not excuse a PR. Excusing on absent evidence is how
    an alarm goes quiet — the opposite posture from the protected-path gate,
    where missing evidence must refuse."""
    def _boom():
        raise RuntimeError("db down")

    assert ms.watcher_holds(["u1"], get_connection=_boom) == {}


# ────────────────────────────────────────────────────────────────────────────
# Liveness probes
# ────────────────────────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_forge_probe_reports_a_401_as_refused():
    ok, detail = ms.probe_forge_auth(
        runner=lambda *a, **k: _Proc(1, err="HTTP 401: Bad credentials"))
    assert ok is False and "401" in detail


def test_forge_probe_reports_a_healthy_credential():
    ok, _ = ms.probe_forge_auth(runner=lambda *a, **k: _Proc(0, out="Logged in"))
    assert ok is True


def test_forge_probe_is_unmeasured_when_it_cannot_run():
    """A missing `gh` is not evidence of an auth failure."""
    def _raise(*a, **k):
        raise FileNotFoundError("gh")

    ok, detail = ms.probe_forge_auth(runner=_raise)
    assert ok is None and "gh" in detail


def test_never_polled_is_unmeasured_not_stale():
    """A fresh database has never measured the daemon. That needs one STARTED;
    a stale heartbeat needs one RESTARTED, and an alarm that cannot tell them
    apart sends people to the wrong place."""
    import tools.kanban.metrics as metrics

    original = metrics.watcher_heartbeat
    try:
        metrics.watcher_heartbeat = lambda **kw: {"state": "never_polled"}
        assert ms.watcher_liveness(15.0)[0] is None
        metrics.watcher_heartbeat = lambda **kw: {"state": "stale"}
        assert ms.watcher_liveness(15.0)[0] is False
        metrics.watcher_heartbeat = lambda **kw: {"state": "polling"}
        assert ms.watcher_liveness(15.0)[0] is True
    finally:
        metrics.watcher_heartbeat = original


# ────────────────────────────────────────────────────────────────────────────
# Read-only against the forge
# ────────────────────────────────────────────────────────────────────────────


def test_the_cli_never_becomes_a_second_actor():
    """This module exists because the merger was unobservable. An observer that
    can merge is just a second merger with a survey attached.

    AST, not grep: the prose above legitimately says "gh pr merge" while
    explaining why the report is not one, and a text scan cannot tell that
    sentence from an argv.
    """
    tree = ast.parse(pathlib.Path(ms.__file__).read_text(encoding="utf-8"))
    shells = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
              and n.attr in ("run", "Popen", "call", "check_call", "check_output")]
    assert len(shells) == 2, (
        "expected exactly two subprocess references (list_prs, probe_forge_auth), "
        "got %s" % [ast.unparse(n) for n in shells])

    argvs = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and n.args
             and isinstance(n.args[0], ast.List)]
    literals = [[e.value for e in a.args[0].elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                for a in argvs]
    assert ["pr", "list", "--state", "--limit", "--json"] in literals, literals
    assert ["auth", "status"] in literals, literals
    for words in literals:
        for verb in ("merge", "push", "close", "edit", "delete", "comment",
                     "create", "squash", "checkout", "commit", "ready", "login",
                     "logout", "refresh"):
            assert verb not in words, (
                "a read-only argv grew a write verb: %s" % verb)


def test_the_module_writes_exactly_one_table():
    """The observation table is the only thing this module may write. A second
    writer arriving unnoticed is how a report becomes an actor."""
    source = pathlib.Path(ms.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    writes = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and any(v in n.value.upper()
                      for v in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP "))]
    assert len(writes) == 1, writes
    assert "INSERT INTO " in writes[0]
    assert ms.EVENTS_TABLE not in writes[0], (
        "the table name is concatenated, not interpolated -- keep it that way")


# ────────────────────────────────────────────────────────────────────────────
# The shipped configuration is the surveyed one
# ────────────────────────────────────────────────────────────────────────────


def test_the_shipped_thresholds_are_the_ones_the_survey_supports():
    """args/merge_stall.yaml records a measured unattributed maximum of 13.98 min
    and an attributed maximum of 116.37. Both thresholds must sit ABOVE their own
    population's observed maximum, or the check fires on routine work — the exact
    failure CLAUDE.md requires a survey to rule out."""
    cfg = ms.load_config()
    assert cfg["stall_after_minutes"] > 13.98
    assert cfg["by_design_stall_after_minutes"] > 116.37
    # ...and still "minutes, not hours" for the unattributed case, which is what
    # a 30s poll interval makes reasonable.
    assert cfg["stall_after_minutes"] <= 60


def test_an_unreadable_config_degrades_to_the_surveyed_defaults():
    """Never to "no threshold": a config typo must not disarm the alarm."""
    cfg = ms.load_config(pathlib.Path("does/not/exist.yaml"))
    assert cfg["stall_after_minutes"] == ms.DEFAULT_STALL_AFTER_MINUTES
    assert cfg["hold_patterns"] == ms.DEFAULT_HOLD_PATTERNS


def test_gate_exits_1_only_on_an_alarm(tmp_path, monkeypatch):
    """Outages and by-design holds are REPORTED, not gated: a `|| true` gets
    added to a gate that fails for reasons the person running it cannot fix."""
    saved = tmp_path / "prs.json"
    # A completion far in the PAST, because `main()` reads the real clock. The
    # fixture's own 2026-08-19T11:00Z is in the future for most of that day, and
    # a negative age can never cross a threshold -- which would make this test
    # pass for the wrong reason before noon and fail after it.
    old_pr = _ready_pr(statusCheckRollup=[
        {"name": "Test", "conclusion": "SUCCESS",
         "completedAt": "2020-01-01T00:00:00Z"}])
    saved.write_text(json.dumps([old_pr]), encoding="utf-8")
    monkeypatch.setattr(ms, "record_transitions",
                        lambda *a, **k: {"ok": True, "written": 0})
    monkeypatch.setattr(ms, "latest_observations", lambda *a, **k: {})
    monkeypatch.setattr(ms, "watcher_holds", lambda *a, **k: {})
    monkeypatch.setattr(ms, "watcher_liveness", lambda *a, **k: (True, {}))
    monkeypatch.setattr(ms, "probe_forge_auth", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ms, "linked_pr_urls", lambda *a, **k: frozenset(),
                        raising=False)
    rc = ms.main(["--from-json", str(saved), "--default-branch", "main",
                  "--gate", "--no-record", "--stall-after", "1e12", "--json"])
    assert rc == 0
    rc = ms.main(["--from-json", str(saved), "--default-branch", "main",
                  "--gate", "--no-record", "--stall-after", "0", "--json"])
    assert rc == 1


def test_a_report_that_could_not_run_is_exit_2_not_an_empty_table():
    """"Nothing is stuck" and "I could not look" must not print the same thing.
    That confusion is the entire defect this module was written for."""
    assert ms.main(["--from-json", "no/such/file.json", "--json"]) == 2
