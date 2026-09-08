# CUI // SP-CTI
"""The Compliance Posture widget reported TWO perfect scores its own evidence
refuted (rmf-rail-02).

rem-hyg-09 routed the per-canvas scores through ``_score_or_none`` -- ask
``_has_rows`` FIRST, return None, render "not assessed". Two blocks in the same
function never adopted it. MEASURED 2026-09-07 on the live board, from
``compute_canvas_posture`` itself:

  * ZERO TRUST 100.0 over ``zig_maturity_scores`` whose latest-per-pillar slice
    is EXACTLY 1.0 for all seven pillars from one run on 2026-06-27 (a seeded
    run), while ``zig_device_compliance_scans`` -- the probe corpus a device
    posture would have to derive from -- does not exist on the backend and the
    live device posture reads ``not_evaluated``.
  * SECURITY 100.0 beside "24 open findings": the 24 was ``COUNT(*)`` of
    assessment ROWS wearing a findings label; ``100 - avg(risk_score)`` read a
    column the STRIDE engine stores as ``100 - penalty`` (so 0.0 is grade F,
    not zero risk); and the 13 latest assessments carried 501 findings, every
    one stored with posture_grade F.
  * A MEASURED 0.0 left the overall average through ``if zig_score > 0``, so
    the headline read HIGHER because a canvas scored zero.

Every assertion here drives the REAL ``compute_canvas_posture`` through stub
canvas connections, in the shape the rem-hyg-09 tests established. The claims
half pins the contract of the two standing claims registered for this card.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import claims as C  # noqa: E402
from tools.awareness.claim_verifier import (  # noqa: E402
    AGREES, DISAGREES, UNMEASURABLE, Claim, verify,
)
from tools.canvas_compliance import posture as mod  # noqa: E402

TS = "2026-06-27T19:44:59.551432+00:00"


# --------------------------------------------------------------------------- #
# Stub canvas connections, driven by the SQL text the module actually issues
# --------------------------------------------------------------------------- #
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if isinstance(self._rows, list) else self._rows

    def fetchall(self):
        return self._rows if isinstance(self._rows, list) else [self._rows]


class _Stub:
    def __init__(self, handler):
        self._handler = handler
        self.rollbacks = 0

    def execute(self, sql, *_a, **_k):
        return _Cur(self._handler(sql.lower()))

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None

    def set_security_context(self, _):
        return None


class _MainConn:
    """The main icdev connection: no GovLift table in these tests."""

    def execute(self, *_a, **_k):
        raise RuntimeError("no govlift tables in this test")


def _security_canvas(*, assessments=0, avg_risk=0.0, findings=None,
                     findings_raise=False, maturity_rows=0, maturity_avg=None,
                     caps=0, acts=0, scans="absent"):
    """One handler for the Security canvas, which serves BOTH the Security
    block and the Zero Trust block (they share the security_canvas backend).

    ``scans`` is "absent" (the table does not exist), or a row count.
    """
    def handler(sql: str):
        # -- Security block --------------------------------------------------
        if "findings_json" in sql:
            if findings_raise:
                raise RuntimeError("findings_json unreadable")
            return [(json.dumps(f),) for f in (findings or [])]
        if "avg(risk_score)" in sql:
            return (avg_risk if assessments else None,)
        if "count(*) from sc_assessments" in sql:
            return (assessments,)
        if "max(ran_at) as m" in sql:
            return {"m": TS if assessments else None}
        # -- Zero Trust block ------------------------------------------------
        if "count(*) as c from zig_device_compliance_scans" in sql:
            if scans == "absent":
                raise RuntimeError('relation "zig_device_compliance_scans" does not exist')
            return {"c": scans}
        if "count(*) as c from zig_maturity_scores" in sql:
            return {"c": maturity_rows}
        if "count(*) as c from zig_capabilities" in sql:
            return {"c": caps}
        if "count(*) as c from zig_activities" in sql:
            return {"c": acts}
        if "avg(score) from zig_maturity_scores m1" in sql:
            return (maturity_avg,)
        if "max(assessment_run_at) as m" in sql:
            return {"m": TS if maturity_rows else None}
        if "from zig_capabilities" in sql:
            return {"total": caps, "impl": None}
        if "from zig_activities za" in sql:
            return {"total": acts, "comp": None}
        raise AssertionError(f"unexpected SQL in stub: {sql}")
    return handler


def _network_canvas(passed=8, failed=2):
    def handler(sql: str):
        if "sum(passed)" in sql:
            return {"p": passed, "f": failed}
        if "max(" in sql:
            return {"m": TS}
        raise AssertionError(f"unexpected SQL in stub: {sql}")
    return handler


def _run(monkeypatch, per_canvas):
    stubs = {name: _Stub(h) for name, h in per_canvas.items()}
    monkeypatch.setattr(mod, "_open_canvas_connection", lambda name: stubs.get(name))
    rows, overall = mod.compute_canvas_posture(_MainConn())
    return {r["name"]: r for r in rows}, overall, stubs


# --------------------------------------------------------------------------- #
# 1. Zero Trust: a maturity number is not a posture without a scan corpus
# --------------------------------------------------------------------------- #
def test_zero_trust_over_an_absent_scan_corpus_is_not_assessed(monkeypatch):
    """THE defect. Seven pillars at exactly 1.0 and no scan table: the widget
    drew a full green 100 bar. Now: None, with the reason, and the declared
    number carried beside it so nothing is hidden."""
    rows, overall, stubs = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=1.0, scans="absent")})
    zt = rows["Zero Trust"]
    assert zt["score"] is None, "a seeded 1.0 over no scan corpus used to score 100.0"
    assert zt["score_basis"] == "unmeasured:no_device_scan_corpus"
    assert zt["declared_maturity"] == 100.0
    assert overall is None


def test_the_failed_probe_does_not_blank_the_timestamp(monkeypatch):
    """On PostgreSQL a failed statement aborts the transaction. Measured on the
    first live run: last_assessed went null because the scan-table probe ran
    before the timestamp read. The probe now rolls back, and the read comes
    first regardless."""
    rows, _overall, stubs = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=1.0, scans="absent")})
    assert rows["Zero Trust"]["last_assessed"] == TS
    assert stubs["Security"].rollbacks >= 1


def test_zero_trust_over_an_empty_scan_corpus_is_not_assessed(monkeypatch):
    """A table that exists and holds nothing is the same absence of evidence."""
    rows, _, _ = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=1.0, scans=0)})
    assert rows["Zero Trust"]["score"] is None
    assert rows["Zero Trust"]["score_basis"] == "unmeasured:no_device_scan_corpus"


def test_zero_trust_over_a_populated_scan_corpus_is_scored(monkeypatch):
    """The fix must not make the row permanently blank: with probe rows behind
    it the maturity number IS the posture score."""
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=21, maturity_avg=0.8, scans=12)})
    assert rows["Zero Trust"]["score"] == 80.0
    assert rows["Zero Trust"]["score_basis"] == "measured"
    assert overall == 80.0


def test_no_zig_rows_at_all_is_unmeasured_not_zero(monkeypatch):
    """`float(r[0] or 0)` coerced an empty maturity table to 0.0, and the
    capability fallback over empty tables is 0.0 again. Neither is a score."""
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        maturity_rows=0, maturity_avg=None, caps=0, acts=0, scans=12)})
    zt = rows["Zero Trust"]
    assert zt["score"] is None
    assert zt["score_basis"] == "unmeasured:no_maturity_rows"
    assert zt["declared_maturity"] is None
    assert overall is None


# --------------------------------------------------------------------------- #
# 2. A measured zero enters the average
# --------------------------------------------------------------------------- #
def test_a_measured_zero_trust_zero_enters_the_average(monkeypatch):
    """rmf-zt-01 deliberately preserves a fail-closed 0.0 for an unverifiable
    posture. `if zig_score > 0` dropped it from the denominator, so the
    headline read HIGHER because Zero Trust scored zero. With Network at 80.0
    the overall is 40.0, not 80.0."""
    rows, overall, _ = _run(monkeypatch, {
        "Security": _security_canvas(maturity_rows=7, maturity_avg=0.0, scans=12),
        "Network": _network_canvas(passed=8, failed=2),
    })
    assert rows["Zero Trust"]["score"] == 0.0
    assert rows["Zero Trust"]["score_basis"] == "measured"
    assert rows["Network"]["score"] == 80.0
    assert overall == 40.0, "a measured 0.0 used to leave the denominator"


def test_no_score_in_the_aggregation_is_gated_on_truthiness():
    """Structural: the rule is `is not None`, never `> 0`, everywhere a score
    is folded into the average. A behavioural test covers today's blocks; this
    refuses the next `if x_score > 0:` before it ships."""
    tree = ast.parse(inspect.getsource(mod))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        comp = node.comparators[0]
        if (isinstance(left, ast.Name) and left.id.endswith("score")
                and isinstance(node.ops[0], ast.Gt)
                and isinstance(comp, ast.Constant) and comp.value == 0):
            offenders.append(f"line {node.lineno}: {left.id} > 0")
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# 3. Security: a perfect score beside its own findings is contested
# --------------------------------------------------------------------------- #
_FINDING = {"rule_id": "sec-001", "severity": "CAT1", "title": "x"}


def test_a_perfect_security_score_beside_recorded_findings_is_contested(monkeypatch):
    """THE defect. 13 assessments, risk_score 0.0 (the engine's grade F), three
    findings each: the widget read 100.0 beside a count of assessment rows
    called 'open findings'. Now the findings are the assessments' OWN, and
    the composite is refused."""
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        assessments=13, avg_risk=0.0, findings=[[_FINDING] * 3] * 13)})
    sec = rows["Security"]
    assert sec["score"] is None, "100.0 beside 39 open findings used to render"
    assert sec["score_basis"] == "contested"
    assert sec["open_findings"] == 39
    assert overall is None


def test_security_findings_that_cannot_be_read_do_not_license_a_perfect_score(monkeypatch):
    """Unreadable is not zero findings. A perfect score whose findings could
    not be read is still contested, and the count says None, never 0."""
    rows, _, _ = _run(monkeypatch, {"Security": _security_canvas(
        assessments=4, avg_risk=0.0, findings_raise=True)})
    sec = rows["Security"]
    assert sec["score"] is None
    assert sec["score_basis"] == "contested"
    assert sec["open_findings"] is None


def test_security_with_measured_risk_and_no_findings_is_scored(monkeypatch):
    rows, overall, _ = _run(monkeypatch, {"Security": _security_canvas(
        assessments=4, avg_risk=20.0, findings=[[]] * 4)})
    sec = rows["Security"]
    assert sec["score"] == 80.0
    assert sec["score_basis"] == "measured"
    assert sec["open_findings"] == 0
    assert overall == 80.0


def test_an_honest_perfect_security_score_is_still_a_score(monkeypatch):
    """Zero risk AND zero findings recorded is a measured 100.0. The rail
    refuses the contradiction, not the number."""
    rows, _, _ = _run(monkeypatch, {"Security": _security_canvas(
        assessments=2, avg_risk=0.0, findings=[[], []])})
    assert rows["Security"]["score"] == 100.0
    assert rows["Security"]["score_basis"] == "measured"


def test_no_security_assessment_is_unmeasured(monkeypatch):
    """rem-hyg-09's case, now carrying its reason."""
    rows, _, _ = _run(monkeypatch, {"Security": _security_canvas(assessments=0)})
    assert rows["Security"]["score"] is None
    assert rows["Security"]["score_basis"] == "unmeasured"


def test_every_row_carries_a_score_basis(monkeypatch):
    rows, _, _ = _run(monkeypatch, {
        "Security": _security_canvas(assessments=0),
        "Network": _network_canvas(),
    })
    for name, row in rows.items():
        assert row.get("score_basis"), name
        assert (row["score"] is None) == (row["score_basis"] != "measured"), name


# --------------------------------------------------------------------------- #
# 4. The two standing claims (autonomy-lrn-01)
# --------------------------------------------------------------------------- #
TWO = ("posture_zero_trust_has_scan_corpus",
       "posture_security_agrees_with_its_assessments")


def _claim(claim_id: str) -> Claim:
    return next(c for c in C.REGISTRY if c.claim_id == claim_id)


def test_the_two_claims_are_registered_and_cite_this_card():
    for claim_id in TWO:
        claim = _claim(claim_id)
        assert claim.incident is not None
        assert claim.incident.task_ids == ["rmf-rail-02"]
        assert claim.incident.observed_on == "2026-09-07"
        assert claim.incident.fixed_by
        assert claim.tier == "propose"
        assert "rmf-rail-02" in claim.tags


def test_the_two_sides_share_no_implementation():
    for claim_id in TWO:
        claim = _claim(claim_id)
        assert claim.reported.__code__ is not claim.derived.__code__


def test_the_derived_sides_never_import_the_surface():
    """The independent side must not be a re-run of the reported side."""
    for fn in (C._derived_zt_scan_corpus, C._derived_security_assessment_verdicts):
        src = inspect.getsource(fn)
        assert "canvas_compliance" not in src, fn.__name__
        assert "compute_canvas_posture" not in src, fn.__name__


def test_a_zero_trust_number_over_an_absent_corpus_disagrees():
    agree = _claim("posture_zero_trust_has_scan_corpus").agree
    assert agree({"score": 100.0, "basis": "measured"},
                 {"corpus_state": "absent", "corpus_rows": 0, "posture_measured": False}) is False
    assert agree({"score": 100.0, "basis": "measured"},
                 {"corpus_state": "empty", "corpus_rows": 0, "posture_measured": False}) is False


def test_a_refused_zero_trust_number_agrees_and_a_backed_one_agrees():
    agree = _claim("posture_zero_trust_has_scan_corpus").agree
    assert agree({"score": None, "basis": "unmeasured:no_device_scan_corpus"},
                 {"corpus_state": "absent", "corpus_rows": 0, "posture_measured": False}) is True
    assert agree({"score": 80.0, "basis": "measured"},
                 {"corpus_state": "rows", "corpus_rows": 12, "posture_measured": True}) is True


def test_a_perfect_security_number_beside_findings_or_an_f_disagrees():
    agree = _claim("posture_security_agrees_with_its_assessments").agree
    live = {"assessments": 13, "grades": {"F": 13}, "findings": 501}
    assert agree({"score": 100.0, "basis": "measured", "open_findings": 24}, live) is False
    assert agree({"score": 100.0, "basis": "measured", "open_findings": 0},
                 {"assessments": 2, "grades": {"F": 2}, "findings": 0}) is False
    assert agree({"score": 80.0, "basis": "measured", "open_findings": 0},
                 {"assessments": 0, "grades": {}, "findings": 0}) is False


def test_a_refused_or_honest_security_number_agrees():
    agree = _claim("posture_security_agrees_with_its_assessments").agree
    assert agree({"score": None, "basis": "contested", "open_findings": 501},
                 {"assessments": 13, "grades": {"F": 13}, "findings": 501}) is True
    assert agree({"score": 100.0, "basis": "measured", "open_findings": 0},
                 {"assessments": 2, "grades": {"A": 2}, "findings": 0}) is True
    # NAMED LIMIT: the inverted-semantics defect (a grade-F engine row at 30.0
    # reads 70.0) is not this claim's, and a non-perfect number is not judged.
    assert agree({"score": 70.0, "basis": "measured", "open_findings": 9},
                 {"assessments": 1, "grades": {"F": 1}, "findings": 9}) is True


def test_zt_derivation_reads_the_survey_and_is_none_when_unreadable(monkeypatch):
    from tools.security_canvas import zt_verdict_survey as survey
    monkeypatch.setattr(survey, "read_corpus", lambda conn=None: {
        "state": "absent", "backend": "postgresql", "error": "no relation", "rows": []})
    monkeypatch.setattr(survey, "live_posture", lambda: {
        "status": "not_evaluated", "measured": False, "stub_allowed": True})
    assert C._derived_zt_scan_corpus() == {
        "corpus_state": "absent", "corpus_rows": 0, "posture_measured": False}

    monkeypatch.setattr(survey, "read_corpus", lambda conn=None: {
        "state": "unreadable", "backend": "postgresql", "error": "down", "rows": []})
    assert C._derived_zt_scan_corpus() is None


class _FakeSecurityConn:
    def __init__(self, rows=None, raises=False):
        self._rows, self._raises = rows or [], raises

    def set_security_context(self, _):
        return None

    def execute(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("relation does not exist")
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        return None


def test_security_derivation_reads_grades_and_findings_off_the_table(monkeypatch):
    from tools.security_canvas.db import init_db
    rows = [{"g": "F", "f": json.dumps([_FINDING, _FINDING])}, {"g": "a", "f": "[]"}]
    monkeypatch.setattr(init_db, "get_connection", lambda: _FakeSecurityConn(rows))
    assert C._derived_security_assessment_verdicts() == {
        "assessments": 2, "grades": {"F": 1, "A": 1}, "findings": 2}


def test_security_derivation_is_none_when_unreadable_or_unparsable(monkeypatch):
    from tools.security_canvas.db import init_db
    monkeypatch.setattr(init_db, "get_connection", lambda: _FakeSecurityConn(raises=True))
    assert C._derived_security_assessment_verdicts() is None
    monkeypatch.setattr(init_db, "get_connection",
                        lambda: _FakeSecurityConn([{"g": "F", "f": "{not json"}]))
    assert C._derived_security_assessment_verdicts() is None


def test_a_live_disagreement_carries_both_sides():
    """Through the verifier itself: the pre-fix board, replayed."""
    zt = _claim("posture_zero_trust_has_scan_corpus")
    result = verify(Claim(
        claim_id=zt.claim_id, description=zt.description,
        reported=lambda: {"score": 100.0, "basis": "measured", "open_findings": 0},
        derived=lambda: {"corpus_state": "absent", "corpus_rows": 0, "posture_measured": False},
        agree=zt.agree, tier=zt.tier))
    assert result.verdict == DISAGREES
    assert result.reported["score"] == 100.0
    assert result.derived["corpus_state"] == "absent"


def test_the_fixed_board_agrees_and_an_unreadable_side_is_unmeasurable():
    zt = _claim("posture_zero_trust_has_scan_corpus")
    fixed = verify(Claim(
        claim_id=zt.claim_id, description=zt.description,
        reported=lambda: {"score": None, "basis": "unmeasured:no_device_scan_corpus"},
        derived=lambda: {"corpus_state": "absent", "corpus_rows": 0, "posture_measured": False},
        agree=zt.agree, tier=zt.tier))
    assert fixed.verdict == AGREES
    down = verify(Claim(
        claim_id=zt.claim_id, description=zt.description,
        reported=lambda: {"score": None, "basis": "unmeasured:no_device_scan_corpus"},
        derived=lambda: None, agree=zt.agree, tier=zt.tier))
    assert down.verdict == UNMEASURABLE
