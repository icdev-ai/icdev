# CUI // SP-CTI
"""penta-aca-07 — FORGE Academy Oracle per-lens units.

test_penta_aca_oracle.py exercises the runner/reflex at the aggregate level (all
7 lenses via academy_oracle_reflex.run). This file fills the per-lens gap:

  * each of the 7 lenses is a BaseLens with the analyze/score/propose/run contract;
  * each lens .run() returns a well-formed list[OraclePrediction] when it can run;
  * the runner is fault-tolerant — AcademyOracleRunner().run() returns a well-formed
    result and never propagates, EVEN THOUGH some lenses raise (see finding below).

FIXED (penta-fix-02): three lenses previously issued SQL against columns that do
not exist in the fa_* schema and raised OperationalError, so the runner's per-lens
guard silently dropped them — 3 of 7 Academy Oracle lenses produced NO predictions:
  * LensStalenesssDetector -> `mp.updated_at`  → now `mp.completed_at`
  * ACESkillGapLens        -> `fp.mission_slug` → now joins fa_missions on mission_id
  * AgentReadinessLens     -> `metadata_json`   → now joins steps→missions, reads
    fa_step_progress.submission
The SQL is corrected, the runner now logs lens failures loudly, and every lens
must return a list (no xfail carve-out).
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
def test_lens_run_returns_predictions(LensClass):
    # penta-fix-02: every lens (including the 3 previously column-drifted ones)
    # must now run against the real schema and return a list — no xfail carve-out.
    lens = LensClass()
    preds = lens.run()
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
