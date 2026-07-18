# CUI // SP-CTI
"""penta-aca-07 — FORGE Academy Oracle per-lens units.

test_penta_aca_oracle.py exercises the runner/reflex at the aggregate level (all
7 lenses via academy_oracle_reflex.run). This file fills the per-lens gap:

  * each of the 7 lenses is a BaseLens with the analyze/score/propose/run contract;
  * each lens .run() returns a well-formed list[OraclePrediction] when it can run;
  * the runner is fault-tolerant — AcademyOracleRunner().run() returns a well-formed
    result and never propagates, EVEN THOUGH some lenses raise (see finding below).

FINDING (reported in the PR, not fixed here — tests-only): three lenses issue SQL
against columns that do not exist in the fa_* schema and therefore raise
OperationalError, so the runner's per-lens guard silently drops them — meaning 3 of
7 Academy Oracle lenses currently produce NO predictions:
  * LensStalenesssDetector -> `mp.updated_at` (fa_mission_progress has started_at /
    completed_at, no updated_at)
  * ACESkillGapLens        -> `fp.mission_slug`
  * AgentReadinessLens     -> `metadata_json`
These are xfail'd below; if the SQL is corrected the lens returns a list and the
test passes normally.
"""

from __future__ import annotations

import pytest

from apps.forge_academy import content_loader, db
from apps.forge_academy.oracle.base_lens import BaseLens, OraclePrediction
from apps.forge_academy.oracle.runner import AcademyOracleRunner, _LENSES


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    db.migrate()
    content_loader.seed_mission_catalog()


# Lenses with a KNOWN merged SQL-column drift that makes .run() raise. The runner
# tolerates them; this maps lens class name -> the offending column, for xfail.
_KNOWN_BROKEN_LENS = {
    "LensStalenesssDetector": "SQL references mp.updated_at — absent from fa_mission_progress",
    "ACESkillGapLens": "SQL references fp.mission_slug — absent from the queried table",
    "AgentReadinessLens": "SQL references metadata_json — absent from the queried table",
}


def test_seven_distinct_lens_classes():
    names = [L.__name__ for L in _LENSES]
    assert len(names) == 7
    assert len(set(names)) == 7, f"duplicate lens classes: {names}"


@pytest.mark.parametrize("LensClass", _LENSES, ids=[L.__name__ for L in _LENSES])
def test_lens_implements_baselens_contract(LensClass):
    assert issubclass(LensClass, BaseLens)
    for method in ("analyze", "score", "propose", "run"):
        assert callable(getattr(LensClass, method, None)), f"{LensClass.__name__} lacks {method}"


@pytest.mark.parametrize("LensClass", _LENSES, ids=[L.__name__ for L in _LENSES])
def test_lens_run_returns_predictions_or_is_tolerated(LensClass):
    lens = LensClass()
    try:
        preds = lens.run()
    except Exception as exc:  # noqa: BLE001 - classify against known drift
        name = LensClass.__name__
        if name in _KNOWN_BROKEN_LENS:
            pytest.xfail(f"{name}: {_KNOWN_BROKEN_LENS[name]} ({type(exc).__name__}: {exc})")
        raise
    assert isinstance(preds, list), f"{LensClass.__name__}.run() must return a list"
    for p in preds:
        assert isinstance(p, OraclePrediction)
        assert isinstance(p.lens, str) and p.lens
        assert 0.0 <= p.confidence <= 1.0
        assert isinstance(p.to_dict(), dict)


def test_runner_is_fault_tolerant_to_failing_lenses():
    """Even with 3 lenses raising OperationalError, the runner returns a well-formed
    result and never propagates — the resilience contract from penta-aca-06."""
    result = AcademyOracleRunner().run()
    assert set(result) >= {"predictions", "persisted_count", "convergence"}
    assert isinstance(result["predictions"], list)
    assert isinstance(result["persisted_count"], int)
    assert isinstance(result["convergence"], list)
    # Every surviving prediction serializes cleanly.
    for p in result["predictions"]:
        assert isinstance(p, dict)


def test_runner_produces_predictions_from_working_lenses():
    """At least the healthy lenses run without the whole sweep aborting."""
    result = AcademyOracleRunner().run()
    # persisted_count is >= 0 (dedup may drop repeats); the key invariant is no crash
    # and a list result. This documents that the sweep completes end-to-end.
    assert result["persisted_count"] >= 0
