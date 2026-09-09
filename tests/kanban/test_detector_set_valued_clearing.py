# CUI // SP-CTI
"""A set-valued finding clears when its condition goes, not when its set changes
(autonomy-dep-05).

THE DEFECT. ``migration_drift`` fingerprints on the sorted pending SET, and
``_clear_missing`` marks ``cleared`` every active finding the current run does
not report BY finding_id. For a set-valued fingerprint those two rules compose
into a FALSE CLEAR: when the pending set changes because another migration
MERGED -- not because anything was APPLIED -- the finding_id changes, the old
row falls out of ``still_active``, and it is written ``cleared`` with its
migrations still pending. ``cleared`` is what a detector card's acceptance
criterion reads, so a card was verifiable complete before the work happened.

MEASURED on the live PG board over the WHOLE recorded ``migration_drift``
population (``detector_findings`` x ``schema_migrations``, 2026-09-03..09):
six clears, of which TWO cleared a finding whose migration was applied 2h59m
and 1h11m LATER. Every row below is that live data, not a fixture invented to
make the point -- ``LIVE_CLEARS`` is replayed through the SHIPPED predicate,
and the four HONEST clears are asserted to be unaffected, because a rule that
stops clearing is not a fix.

Survey: docs/audits/autonomy-dep-05-set-valued-clearing-survey.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban import detector_findings as df  # noqa: E402
from tools.kanban import task_factory as tf  # noqa: E402


# --------------------------------------------------------------------------- #
# The live population, verbatim. `False` is the card's finding: the clear was
# written while a migration the finding NAMES was still pending.
# --------------------------------------------------------------------------- #
LIVE_CLEARS = [
    # (finding_id, its pending set, the set the run reported at the clearing
    #  instant, honest?, note)
    ("3a2f0f2aa1b33c3e", ["20260903100336"], [], True,
     "applied 2026-09-03 17:55:41, cleared 17:56:21 -- 40s later, on a CLEAN run"),
    ("12a243722f5eed9a", ["20260903194350"],
     ["20260903194350", "20260905070028", "20260905093010", "20260905104843"], False,
     "cleared 2026-09-05 12:24:18; applied 15:23:12 -- 2h58m54s LATER"),
    ("6abf31a8e7ca6804",
     ["20260903194350", "20260905070028", "20260905093010", "20260905104843"], [], True,
     "all four applied 2026-09-05 15:23:12-13, cleared 18:24:22"),
    ("7a5948ff916cff0b", ["20260908003311", "20260908003920"], ["20260908071149"], True,
     "both applied 2026-09-08 09:33:40, cleared 14:21:33 -- the set changed "
     "BECAUSE they were applied, and a successor was filed at the same instant"),
    ("a02733a6dc9aa2e6", ["20260908071149"],
     ["20260908071149", "20260908071432", "20260908071433"], False,
     "cleared 2026-09-08 20:45:26; applied 21:56:12 -- 1h10m46s LATER"),
    ("03922726ad17f1c1",
     ["20260908071149", "20260908071432", "20260908071433"], [], True,
     "all three applied 2026-09-08 21:56:12-14, cleared 2026-09-09 02:45:34"),
]

MD = df.DETECTOR_MIGRATION_DRIFT


def _drift_report(pending, *, state="pending"):
    return {
        "state": state, "ref": "origin/main", "root": "deployment",
        "on_branch_count": 413, "applied_count": 445,
        "pending_count": len(pending),
        "pending": [{"version": v, "name": f"{v}_thing"} for v in pending],
        "applied_not_on_branch": ["173", "186"],
    }


def _drift_findings(pending):
    """The REAL adapter on a REAL report shape -- never a hand-built Finding, so
    the fingerprint under test is the one the detector actually writes."""
    return df.migration_drift_findings(_drift_report(pending)) if pending else []


@pytest.fixture
def conn(icdev_db):
    c = get_connection(str(icdev_db))
    yield c
    c.close()


@pytest.fixture
def seeded(monkeypatch):
    calls: list = []

    def _fake_create_tasks(specs, **_kw):
        calls.append([dict(s) for s in specs])
        return [s["id"] for s in specs]

    monkeypatch.setattr(tf, "create_tasks", _fake_create_tasks)
    return calls


def _runner(state, findings):
    return lambda conn_, cfg: df._result(state, findings)


def _row(conn, finding_id):
    r = conn.execute(
        f"SELECT status, cleared_at FROM {df.FINDINGS_TABLE} WHERE finding_id = %s",
        (finding_id,)).fetchone()
    return dict(r) if r else None


def _consume(conn, pending, *, state=None):
    findings = _drift_findings(pending)
    run_state = state or (df.RUN_FINDINGS if findings else df.RUN_CLEAN)
    return df.consume({}, conn=conn, runners={MD: _runner(run_state, findings)})


# --------------------------------------------------------------------------- #
# 1. The two measured false clears, replayed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "before,after,note",
    [(row[1], row[2], row[4]) for row in LIVE_CLEARS if not row[3]],
    ids=["12a243722f_dic_artifacts", "a02733a6dc_dic_sme_assertions"])
def test_the_two_measured_false_clears_would_have_stayed_active(
        conn, seeded, before, after, note):
    """RED-FIRST. Both of these were written `cleared` on the live board while
    the migration they name was still pending. Replayed through the shipped
    consume(), the predecessor must stay ACTIVE and carry no cleared_at."""
    first = _consume(conn, before)
    fid = _drift_findings(before)[0]["finding_id"]
    assert _row(conn, fid)["status"] == df.FINDING_ACTIVE

    report = _consume(conn, after)
    row = _row(conn, fid)
    assert row["status"] == df.FINDING_ACTIVE, f"FALSE CLEAR reinstated: {note}"
    assert row["cleared_at"] is None
    # and it is SURFACED, not silently skipped
    assert report["findings_held_still_true"] == 1
    held = report["held_still_true"][0]
    assert held["finding_id"] == fid and held["detector"] == MD
    assert set(held["still_reported"]) == set(before) & set(after)
    assert report["detectors"][MD]["cleared"] == 0
    assert first["cards_seeded"] and report["cards_seeded"]


# --------------------------------------------------------------------------- #
# 2. The four HONEST clears still clear. A rule that stops clearing is not a fix
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "before,after,note",
    [(row[1], row[2], row[4]) for row in LIVE_CLEARS if row[3]],
    ids=["3a2f0f2aa1_clean_run", "6abf31a8e7_all_four_applied",
         "7a5948ff91_set_changed_because_applied", "03922726ad_all_three_applied"])
def test_the_four_measured_honest_clears_still_clear(conn, seeded, before, after, note):
    _consume(conn, before)
    fid = _drift_findings(before)[0]["finding_id"]
    report = _consume(conn, after)
    row = _row(conn, fid)
    assert row["status"] == df.FINDING_CLEARED, f"honest clear lost: {note}"
    assert row["cleared_at"]
    assert report["detectors"][MD]["cleared"] == 1
    assert report["findings_held_still_true"] == 0


def test_the_whole_live_population_flips_exactly_the_two_false_clears():
    """The survey's headline, re-derived through the shipped predicate rather
    than quoted: 6 recorded clears, 2 held, 4 untouched."""
    verdicts = {}
    for fid, before, after, _honest, _note in LIVE_CLEARS:
        reported = df.reported_members(MD, _drift_findings(after))
        survivors = df.survives_clearing(MD, "|".join(sorted(before)), reported)
        verdicts[fid] = "held" if survivors else "cleared"
    assert list(verdicts.values()).count("held") == 2
    assert verdicts["12a243722f5eed9a"] == "held"
    assert verdicts["a02733a6dc9aa2e6"] == "held"
    for fid, _b, _a, honest, _n in LIVE_CLEARS:
        assert (verdicts[fid] == "cleared") is honest


# --------------------------------------------------------------------------- #
# 3. A partial application clears exactly the findings whose members are gone
# --------------------------------------------------------------------------- #
def test_a_partly_applied_finding_is_not_cleared_and_a_fully_applied_one_is(conn, seeded):
    a = _drift_findings(["m1"])[0]["finding_id"]
    b = _drift_findings(["m1", "m2"])[0]["finding_id"]
    _consume(conn, ["m1"])
    _consume(conn, ["m1", "m2"])          # m2 merges: a is held, b is filed
    assert _row(conn, a)["status"] == df.FINDING_ACTIVE

    _consume(conn, ["m2"])                # m1 applied, m2 still pending
    assert _row(conn, a)["status"] == df.FINDING_CLEARED   # everything a names is gone
    assert _row(conn, b)["status"] == df.FINDING_ACTIVE    # b still names m2

    _consume(conn, [])                    # m2 applied: the honest, final clear
    assert _row(conn, b)["status"] == df.FINDING_CLEARED


def test_a_clean_run_clears_every_held_finding(conn, seeded):
    for pending in (["m1"], ["m1", "m2"], ["m1", "m2", "m3"]):
        _consume(conn, pending)
    active = conn.execute(
        f"SELECT COUNT(*) AS n FROM {df.FINDINGS_TABLE} WHERE status = %s",
        (df.FINDING_ACTIVE,)).fetchone()
    assert dict(active)["n"] == 3, "held findings accumulate while the drift persists"

    report = _consume(conn, [])
    assert report["detectors"][MD]["cleared"] == 3
    rows = conn.execute(f"SELECT status FROM {df.FINDINGS_TABLE}").fetchall()
    assert {dict(r)["status"] for r in rows} == {df.FINDING_CLEARED}


def test_an_unmeasurable_run_still_clears_nothing(conn, seeded):
    _consume(conn, ["m1"])
    fid = _drift_findings(["m1"])[0]["finding_id"]
    df.consume({}, conn=conn, runners={MD: _runner(df.RUN_UNMEASURABLE, [])})
    assert _row(conn, fid)["status"] == df.FINDING_ACTIVE


# --------------------------------------------------------------------------- #
# 4. born_red and recovery are UNCHANGED. Their fingerprints are CONSTANTS and
#    cannot exhibit the defect; the point is that the new predicate is a no-op
#    for them and for every detector not declared set-valued.
# --------------------------------------------------------------------------- #
def _plain(detector, subject, fingerprint):
    return df.Finding(detector, subject, fingerprint, title=f"{subject} finding",
                      priority="medium", task_type="fix",
                      evidence={"subject": subject}, derivation="python -m tools.x",
                      advice="do the thing")


@pytest.mark.parametrize("detector,was,now", [
    # born_red and recovery carry CONSTANT fingerprints, so the only way one of
    # their findings leaves a report is that its SUBJECT does -- which is the
    # honest clear, and must stay one.
    (df.DETECTOR_BORN_RED, ("tests/x_test.py", "born_red"),
     ("tests/y_test.py", "born_red")),
    (df.DETECTOR_RECOVERY, ("t-1", "needed_a_human"), ("t-2", "needed_a_human")),
    # status_churn's fingerprint CONTAINS the separator and is NOT a set: a
    # cycle and a contested flag. Its clearing must not change either.
    (df.DETECTOR_STATUS_CHURN, ("t-3", "a -> b -> a|contested"),
     ("t-3", "a -> b -> a|single")),
    # deployment_freshness is the same SHAPE as migration_drift and is NAMED,
    # NOT SURVEYED -- so it is deliberately not declared, and clears as before.
    (df.DETECTOR_DEPLOYMENT_FRESHNESS, ("/deploy", "union_refused|args/projects.yaml"),
     ("/deploy", "union_refused|args/projects.yaml|args/x.yaml")),
])
def test_a_detector_not_declared_set_valued_clears_exactly_as_before(
        conn, seeded, detector, was, now):
    old = _plain(detector, was[0], was[1])
    new = _plain(detector, now[0], now[1])
    df.consume({}, conn=conn, runners={detector: _runner(df.RUN_FINDINGS, [old])})
    report = df.consume({}, conn=conn,
                        runners={detector: _runner(df.RUN_FINDINGS, [new])})
    assert _row(conn, old["finding_id"])["status"] == df.FINDING_CLEARED
    assert report["detectors"][detector]["cleared"] == 1
    assert report["findings_held_still_true"] == 0


@pytest.mark.parametrize("detector", [
    df.DETECTOR_BORN_RED, df.DETECTOR_RECOVERY, df.DETECTOR_STATUS_CHURN,
    df.DETECTOR_DEPLOYMENT_FRESHNESS])
def test_the_survival_predicate_is_a_no_op_off_the_declared_set(detector):
    """Even for an identical fingerprint on both sides -- the ONE case where a
    membership rule would bite -- an undeclared detector survives nothing."""
    assert df.reported_members(detector, [{"fingerprint": "a|b"}]) == frozenset()
    assert df.survives_clearing(detector, "a|b", frozenset({"a", "b"})) == frozenset()


def test_only_migration_drift_is_declared_set_valued():
    """The declaration is the whole scope of the change. deployment_freshness
    shares the shape and is left out ON PURPOSE until it has its own survey."""
    assert df.SET_VALUED_FINGERPRINT_DETECTORS == frozenset({MD})
    assert df.DETECTOR_DEPLOYMENT_FRESHNESS not in df.SET_VALUED_FINGERPRINT_DETECTORS


# --------------------------------------------------------------------------- #
# 5. The card's own account of its lifecycle
# --------------------------------------------------------------------------- #
def test_a_migration_drift_card_states_the_set_valued_clearing_rule():
    f = _drift_findings(["m1", "m2"])[0]
    text = df.render_description(f, seen_count=1, first_seen_at=None, revision=1)
    assert "fingerprint is a SET" in text and "m1|m2" in text
    assert "reports NONE of the members it names" in text
    # unchanged for every other detector
    other = df.render_description(_plain(df.DETECTOR_BORN_RED, "t", "born_red"),
                                  seen_count=1, first_seen_at=None, revision=1)
    assert "fingerprint is a SET" not in other


def test_the_acceptance_criterion_still_reads_status_cleared():
    """An unclearable finding is worse than a false clear, so no third status
    was introduced and every already-filed card stays closable."""
    f = _drift_findings(["m1"])[0]
    spec = df.build_spec(f, seen_count=1, first_seen_at=None, revision=1,
                         seed_status="suggested")
    assert "status=cleared" in spec["acceptance_criteria"]
