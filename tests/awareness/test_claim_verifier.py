# CUI // SP-CTI
"""A claim is verified against an INDEPENDENT fact, or not at all (rem-hyg-17).

The defects of 2026-08-20 were one defect wearing eight faces: a surface
asserting something whose supporting evidence nothing ever re-derived. The data
was almost never wrong — the REDUCTION was.

That is why a learner watching outputs cannot find them. A bad reduction
produces a beautifully stable series: `odc_gap_scores` holds 91 rows over a
month carrying ONE distinct value for ONE subject, and five `pr_watcher.resume`
rows for one task are one failure repeated. Anything gaining confidence from row
count rates both as strong evidence.

    REPETITION IS NOT CORROBORATION.

The tests below pin that, and pin the three ways this checker could quietly stop
checking: a vacuous agreement, an unmeasurable folded into clean, and a claim
whose two sides share an implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness.claim_verifier import (  # noqa: E402
    AGREES,
    DISAGREES,
    TIER,
    UNMEASURABLE,
    Claim,
    independent_observations,
    verify,
    verify_all,
)


def _claim(reported, derived, **kw):
    return Claim(
        claim_id=kw.pop("claim_id", "c"),
        description=kw.pop("description", "d"),
        reported=lambda: reported() if callable(reported) else reported,
        derived=lambda: derived() if callable(derived) else derived,
        **kw,
    )


# --------------------------------------------------------------------------- #
# 1. The core comparison
# --------------------------------------------------------------------------- #
def test_matching_sides_agree():
    assert verify(_claim(3, 3)).verdict == AGREES


def test_differing_sides_are_the_finding():
    r = verify(_claim(331, 46))
    assert r.verdict == DISAGREES
    assert r.reported == 331 and r.derived == 46, (
        "both derivations must be reported — a finding that shows only one "
        "side cannot be acted on"
    )


# --------------------------------------------------------------------------- #
# 2. The three ways this could quietly stop checking
# --------------------------------------------------------------------------- #
def test_two_empty_sides_are_unmeasurable_not_agreement():
    """THE self-inflicted bug this module nearly shipped.

    Run against an empty database both sides came back `[]`, `[] == []` was
    True, and claims reported `agrees` having compared nothing. That is "no data
    rendered as a clean bill of health" — committed by the very checker built to
    catch it.
    """
    r = verify(_claim([], []))
    assert r.verdict == UNMEASURABLE
    assert "vacuously true" in r.detail


def test_a_measured_false_is_not_treated_as_empty():
    """`False` and `0` are real answers. Only an empty COLLECTION covered
    nothing — conflating them would discard exactly the measurements that
    matter, like `unlogged=False`."""
    assert verify(_claim(False, False)).verdict == AGREES
    assert verify(_claim(0, 0)).verdict == AGREES


def test_an_unreadable_side_is_unmeasurable_not_agreement():
    def _boom():
        raise RuntimeError("table missing")

    assert verify(_claim(_boom, 1)).verdict == UNMEASURABLE
    assert verify(_claim(1, _boom)).verdict == UNMEASURABLE


def test_a_none_side_never_reads_as_agreement():
    assert verify(_claim(None, None)).verdict == UNMEASURABLE
    assert verify(_claim(5, None)).verdict == UNMEASURABLE


def test_a_broken_comparator_is_unmeasurable_not_a_failure():
    """A comparison that raises says nothing about the claim."""
    def _bad(_a, _b):
        raise ValueError("nope")

    assert verify(_claim(1, 2, agree=_bad)).verdict == UNMEASURABLE


# --------------------------------------------------------------------------- #
# 3. Repetition is not corroboration
# --------------------------------------------------------------------------- #
def test_a_stuck_writer_counts_as_one_observation():
    """The live odc_gap_scores shape: 91 rows, one subject, one value."""
    rows = [{"s": "design-1", "v": 0.85} for _ in range(91)]
    assert len(rows) == 91
    assert independent_observations(rows, "s", "v") == 1, (
        "91 rows carrying one fact is one observation — a row count would rate "
        "this as strongly corroborated"
    )


def test_a_retry_loop_counts_as_one_observation():
    """Five resume rows for one task are ONE failure, not five recoveries."""
    rows = [{"s": "task-c49fb2727d", "v": "resume"} for _ in range(5)]
    assert independent_observations(rows, "s", "v") == 1


def test_genuinely_varying_evidence_counts_separately():
    rows = [{"s": "a", "v": 1}, {"s": "a", "v": 2}, {"s": "b", "v": 1}]
    assert independent_observations(rows, "s", "v") == 3


def test_no_rows_is_zero_observations():
    assert independent_observations([], "s", "v") == 0
    assert independent_observations(None, "s", "v") == 0


# --------------------------------------------------------------------------- #
# 4. The summary must not hide an unmeasurable
# --------------------------------------------------------------------------- #
def test_unmeasurable_is_reported_separately():
    report = verify_all([
        _claim(1, 1, claim_id="ok"),
        _claim(1, 2, claim_id="bad"),
        _claim(None, None, claim_id="unknown"),
    ])
    assert report["counts"] == {AGREES: 1, DISAGREES: 1, UNMEASURABLE: 1}


def test_an_unmeasurable_alone_is_not_a_disagreement():
    """`any_disagreement` drives the human's attention. A claim nobody could
    check has not failed — but it has not passed either, which is why the count
    stays visible."""
    report = verify_all([_claim(None, None, claim_id="unknown")])
    assert report["any_disagreement"] is False
    assert report["counts"][UNMEASURABLE] == 1
    assert report["counts"][AGREES] == 0, "it must never be counted as agreement"


# --------------------------------------------------------------------------- #
# 5. Corrective action stays inside its tier
# --------------------------------------------------------------------------- #
def test_no_tier_permits_editing_the_claim():
    """A verifier that may rewrite the assertion can always make itself green —
    the same move as a test quietly weakened to match code that broke."""
    assert set(TIER) == {"report", "restore", "propose"}
    joined = " ".join(TIER.values()).lower()
    for forbidden in ("edit the claim", "adjust the threshold", "update the assertion"):
        assert forbidden not in joined


# --------------------------------------------------------------------------- #
# 6. The registry's own discipline
# --------------------------------------------------------------------------- #
def test_registered_claims_are_unique_and_described():
    from tools.awareness.claims import REGISTRY

    ids = [c.claim_id for c in REGISTRY]
    assert len(ids) == len(set(ids)), f"duplicate claim id: {ids}"
    for c in REGISTRY:
        assert len(c.description) > 40, f"{c.claim_id} states no evidence"
        assert c.tier in TIER, f"{c.claim_id} has an unknown action tier"


def test_the_two_sides_are_different_callables():
    """If the verifier calls what the surface calls, it proves the function is
    deterministic — which was never in question. Every defect survived because
    one computation was trusted twice."""
    from tools.awareness.claims import REGISTRY

    for c in REGISTRY:
        assert c.reported is not c.derived, c.claim_id
        assert getattr(c.reported, "__code__", None) is not getattr(
            c.derived, "__code__", object()), c.claim_id


@pytest.mark.parametrize("claim_id", [
    "posture_score_needs_evidence",
    "cache_unlogged_is_measured",
    "recovery_counts_outcomes_not_attempts",
    "repetition_is_not_corroboration",
])
def test_every_seeded_claim_is_a_defect_that_actually_happened(claim_id):
    """Seeding only from PROVEN defects is what stops this becoming another
    capability that reports clean because it does nothing."""
    from tools.awareness.claims import REGISTRY

    assert claim_id in {c.claim_id for c in REGISTRY}


# --------------------------------------------------------------------------- #
# 7. The guards catch the PRE-FIX states
# --------------------------------------------------------------------------- #
def test_the_posture_guard_catches_a_score_without_evidence():
    """Network/Pipeline/Migration scored 100.0 with zero rows."""
    from tools.awareness.claims import _scored_implies_evidence

    assert _scored_implies_evidence(["Infra"], ["Infra"]) is True
    assert _scored_implies_evidence(["Network", "Infra"], ["Infra"]) is False


def test_the_posture_guard_allows_evidence_without_a_score():
    """One-directional: a canvas holding evidence but scoring None is fine (it
    may be unreadable or out of scope). Only a NUMBER demands evidence."""
    from tools.awareness.claims import _scored_implies_evidence

    assert _scored_implies_evidence([], ["Infra", "Data"]) is True


def test_the_posture_guard_ignores_canvases_it_has_no_table_for():
    """GovLift and Zero Trust are scored outside the canvas loop from tables the
    map does not cover. Asserting on them would be asserting wrongly."""
    from tools.awareness.claims import _scored_implies_evidence

    assert _scored_implies_evidence(["GovLift", "Zero Trust"], []) is True


# --------------------------------------------------------------------------- #
# 8. The narrowing: identical output is only STUCK if the input moved
# --------------------------------------------------------------------------- #
class _SeriesConn:
    """Answers MIN(assessed_at) on the series and MAX(updated_at) on the input."""

    def __init__(self, series_start, input_changed_at):
        self._start, self._input = series_start, input_changed_at

    def execute(self, sql, *_a, **_k):
        self._last = "MAX(" in sql
        return self

    def fetchone(self):
        return {"t": self._input if self._last else self._start}


def test_identical_output_with_an_unchanged_input_is_not_stuck():
    """THE false positive this claim produced on its first live run.

    odc_gap_scores: 91 rows over a month, one value, one subject — and
    `observability_designs.updated_at` is 2026-06-28, unchanged since creation.
    A 6-hourly snapshot of an unchanged subject SHOULD repeat itself. Flagging
    it accuses a reflex that is working.
    """
    from tools.awareness.claims import _input_changed_since_series_start

    conn = _SeriesConn("2026-07-18T00:00:00", "2026-06-28T02:42:53")
    assert _input_changed_since_series_start(conn, "s", "i", "updated_at") is False


def test_identical_output_after_the_input_moved_is_stuck():
    """The case worth catching: the subject changed and the answer did not."""
    from tools.awareness.claims import _input_changed_since_series_start

    conn = _SeriesConn("2026-07-18T00:00:00", "2026-08-01T12:00:00")
    assert _input_changed_since_series_start(conn, "s", "i", "updated_at") is True


def test_an_unreadable_input_never_manufactures_a_finding():
    """Fail-safe to "unchanged": accusing a working reflex is how a check earns
    itself a `|| true`."""
    from tools.awareness.claims import _input_changed_since_series_start

    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("no such table")

    assert _input_changed_since_series_start(_Boom(), "s", "i", "c") is False


def test_only_a_stuck_writer_counts_as_disagreement():
    """The two sides differ by construction whenever a series repeats — the
    reported side is what a ROW COUNT concludes. Demanding equality would flag
    every legitimate snapshot series."""
    from tools.awareness.claims import _no_stuck_writer

    assert _no_stuck_writer({"t": "well_corroborated"}, {"t": "stable_input"}) is True
    assert _no_stuck_writer({"t": "well_corroborated"}, {"t": "well_corroborated"}) is True
    assert _no_stuck_writer({"t": "well_corroborated"}, {"t": "stuck_writer"}) is False


def test_a_series_naming_no_input_cannot_be_registered():
    """Every registered series must name an input, or the narrowed rule silently
    reverts to the false-positive behaviour."""
    from tools.awareness.claims import _STUCK_SERIES

    for entry in _STUCK_SERIES:
        assert len(entry) == 6, f"series {entry[:2]} names no input signal"
        assert entry[4] and entry[5], entry
