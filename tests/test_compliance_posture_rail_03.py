# CUI // SP-CTI
"""Two more latent perfect scores on the Compliance Posture widget (rmf-rail-03).

Neither fires on the live board today -- both rows read "Not assessed" -- but
each flips to a fabricated 100.0 the first time someone does the ordinary
thing on its canvas:

  * MIGRATION scored a validation-only SUM while gating PRESENCE on ANY row.
    The Assess button (``migration_canvas/blueprint.py::mc_api_assess``)
    writes ``assessment_type='full'`` with its own ``score``/``grade``;
    governance writes ``'governance'``; only ``tools/migration/validator.py``
    ever writes ``'validation'``. So: create a design, press Assess ->
    ``_has_rows`` sees the 'full' row and passes, the validation-gated SUMs
    are NULL -> 0, and ``100 - 0*20 - 0*10 - 0*5`` reads 100.0 beside whatever
    grade the Assess button actually recorded.

  * ZERO TRUST scored the declared zig maturity the moment the device-scan
    corpus held ANY row, never asking whether anything in it was MEASURED.
    ``device_compliance_scanner.scan_device`` with no probe (the Falcon
    adapter is a stub; no caller supplies probes -- rmf-zt-01) records every
    check as ``unknown``. One ordinary scanner run therefore fills the corpus
    with rows -- ``_has_rows`` reads True -- while measuring nothing at all,
    and the declared number (100.0 on this board, one seeded 2026-06-27 run)
    would have rendered as the posture.

Both fixtures read 100.0 against ``tools/canvas_compliance/posture.py`` as it
stood before this card (red-first). Drives the REAL ``compute_canvas_posture``
through stub canvas connections, reusing the rail-02 stub infrastructure.
"""

from __future__ import annotations

from tests.test_compliance_posture_rail_02 import TS, _run, _security_canvas

# --------------------------------------------------------------------------- #
# Migration: score what the canvas WRITES, not a validation-only SUM
# --------------------------------------------------------------------------- #


def _migration_canvas(*, assessments=0, score=None, cat1=0, cat2=0, cat3=0, ts=TS):
    """Stub for the Migration canvas backend (``mc_assessments``).

    One row shape only -- enough for the presence/score/findings/timestamp
    queries ``compute_canvas_posture`` issues against this table. ``score`` is
    the design's OWN stored score (what ``mc_api_assess`` / governance /
    the validator all compute and persist), never re-derived here.
    """
    def handler(sql: str):
        if "count(*) as c from mc_assessments" in sql:
            return {"c": assessments}
        if " as c1, sum(cat2_findings)" in sql:
            return {"c1": cat1, "c2": cat2, "c3": cat3}
        if " as cat1, sum(cat2_findings)" in sql:
            return {"cat1": cat1, "cat2": cat2, "cat3": cat3}
        if "avg(score) from mc_assessments a1" in sql:
            return (score,)
        if "avg(score) from mc_assessments" in sql:
            return (score,)
        if "assessment_type as t from mc_assessments" in sql:
            return {"t": "full"}
        if "max(created_at) as m from mc_assessments where" in sql:
            return {"m": ts if assessments else None}
        if "max(created_at) as m from mc_assessments" in sql:
            return {"m": ts if assessments else None}
        raise AssertionError(f"unexpected SQL in stub: {sql}")
    return handler


def test_migration_reads_its_own_assessment_score_not_a_validation_only_sum(monkeypatch):
    """THE defect. One 'full' row (the ordinary Assess-button path) with
    cat1_findings=2 and a real stored score of 60.0. The old code gated
    presence on this row via ``_has_rows`` but summed cat*_findings gated on
    ``assessment_type = 'validation'`` -- a type this row never carries -- so
    the SUM read NULL -> 0 and ``100 - 0*20 - 0*10 - 0*5`` produced 100.0
    beside a design that actually scored 60.0 with two CAT1 findings open."""
    rows, overall, _ = _run(monkeypatch, {"Migration": _migration_canvas(
        assessments=1, score=60.0, cat1=2, cat2=0, cat3=0)})
    mig = rows["Migration"]
    assert mig["score"] == 60.0, "used to read 100.0 off a validation-only SUM"
    assert mig["score_basis"] == "measured"
    assert mig["open_findings"] == 2
    assert overall == 60.0


def test_migration_with_no_assessments_is_not_assessed(monkeypatch):
    """The fix must not make the row permanently blank, and an unassessed
    design must never fall back to the old formula's 100.0."""
    rows, overall, _ = _run(monkeypatch, {"Migration": _migration_canvas(assessments=0)})
    mig = rows["Migration"]
    assert mig["score"] is None
    assert mig["score_basis"] == "unmeasured"
    assert overall is None


def test_migration_with_multiple_designs_averages_each_designs_latest_score(monkeypatch):
    """Same shape as every other canvas's ``_score_or_none``: the latest
    assessment PER DESIGN, averaged -- not a single global row."""
    rows, overall, _ = _run(monkeypatch, {"Migration": _migration_canvas(
        assessments=2, score=75.0, cat1=1, cat2=1, cat3=0)})
    mig = rows["Migration"]
    assert mig["score"] == 75.0
    assert mig["score_basis"] == "measured"
    assert overall == 75.0


# --------------------------------------------------------------------------- #
# Zero Trust: a corpus with rows is not a corpus with MEASUREMENTS
# --------------------------------------------------------------------------- #


def test_zero_trust_over_an_all_unknown_scan_corpus_is_not_assessed(monkeypatch):
    """THE defect. Seven pillars declaring 1.0 maturity and a scan corpus that
    HOLDS ROWS -- but every one of them is an unprobed ``unknown`` verdict
    (the only kind ``device_compliance_scanner.scan_device`` can record with
    no probe source wired, rmf-zt-01). The old code scored the declared
    number the instant the table held ANY row; now a corpus of only
    ``unknown`` verdicts is its own distinct unmeasured reason."""
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=1.0, scans=12, zt_measured=0)})
    zt = rows["Zero Trust"]
    assert zt["score"] is None, "a scan corpus of only unknown verdicts used to score 100.0"
    assert zt["score_basis"] == "unmeasured:no_measured_device_checks"
    assert zt["declared_maturity"] == 100.0
    assert overall is None


def test_zero_trust_over_a_corpus_with_one_measured_verdict_is_scored(monkeypatch):
    """The fix must not make the row permanently blank: once at least one
    check actually measured pass/fail, the declared maturity IS the score --
    same contract as the empty/absent corpus cases rmf-rail-02 already pins."""
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=0.8, scans=12, zt_measured=1)})
    zt = rows["Zero Trust"]
    assert zt["score"] == 80.0
    assert zt["score_basis"] == "measured"
    assert overall == 80.0
