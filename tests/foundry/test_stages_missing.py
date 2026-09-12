# CUI // SP-CTI
"""The foundry names the stages it does not have (xrv-lab-01).

``run_cycle`` degrades a missing stage module to a clean no-op. That is correct
and it stays -- but for four of the eight stages the no-op has been the
PERMANENT state since the engine shipped: ``synthesizer``, ``scorer``,
``deliberator`` and ``seeder`` have no module in the tree. A cycle therefore
harvested signals, synthesized zero concepts because nothing can synthesize, and
reported a green run with ``tasks_emitted: 0`` -- which a reader takes as "the
gates rejected everything". It was never a gate verdict.

``seeder`` is singled out because it is the only writer of a kanban row: without
it the cycle CANNOT emit, so 0 is unmeasurable rather than measured.
"""
from __future__ import annotations

import pytest

from tools.foundry import engine
from tools.genesis.reflexes import foundry_cycle


# --------------------------------------------------------------------------- #
# 1. The engine's own probe
# --------------------------------------------------------------------------- #
class TestStageAvailability:
    def test_every_declared_stage_gets_a_verdict(self):
        report = engine.stage_availability()
        assert set(report["present"]) | set(report["missing"]) == set(engine.STAGE_MODULES)
        assert not set(report["present"]) & set(report["missing"])

    def test_the_four_unbuilt_stages_are_named_on_this_tree(self):
        """The measurement the card was written from, asserted against the tree."""
        missing = set(engine.stage_availability()["missing"])
        assert {"synthesizer", "scorer", "deliberator", "seeder"} <= missing, (
            "a stage module landed — update this assertion and the reflex's "
            "verdict will follow on its own"
        )

    def test_the_shipped_stages_are_present(self):
        """A positive control: a probe that reported everything missing would
        also satisfy the assertion above."""
        present = set(engine.stage_availability()["present"])
        assert {"harvester", "novelty_gate", "spec_generator", "task_graph"} <= present

    def test_emit_stage_is_declared_once(self):
        assert engine.EMIT_STAGE == "seeder"
        assert engine.EMIT_STAGE in engine.STAGE_MODULES

    def test_stage_order_is_the_pipeline_order(self):
        order = list(engine.STAGE_MODULES)
        assert order.index("harvester") < order.index("synthesizer")
        assert order.index("novelty_gate") < order.index("scorer")
        assert order.index("task_graph") < order.index(engine.EMIT_STAGE)


class TestMarkRan:
    """`stages_ran` is what was INVOKED, which is not what is importable."""

    def test_mark_ran_is_idempotent_and_tolerates_no_collector(self):
        ran: list = []
        engine._mark_ran(ran, "harvester")
        engine._mark_ran(ran, "harvester")
        assert ran == ["harvester"]
        engine._mark_ran(None, "harvester")  # must not raise


# --------------------------------------------------------------------------- #
# 2. The reflex verdict
# --------------------------------------------------------------------------- #
def _cycle(**overrides):
    base = {
        "run_id": "1",
        "harvested": 7,
        "concepts_proposed": 0,
        "concepts_approved": 0,
        "tasks_emitted": 0,
        "status": "completed",
        "stages_missing": ["synthesizer", "scorer", "deliberator", "seeder"],
        "stages_present": ["harvester", "novelty_gate", "spec_generator", "task_graph"],
        "stages_ran": ["harvester"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def _enabled(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "1")
    monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {})
    return None


def _arm_engine(monkeypatch, cycle):
    import tools.foundry.engine as eng

    monkeypatch.setattr(eng, "run_cycle", lambda **_kw: cycle)


class TestReflexReportsStagesMissing:
    def test_absent_seeder_makes_tasks_emitted_unmeasurable(self, monkeypatch, _enabled):
        _arm_engine(monkeypatch, _cycle())

        result = foundry_cycle.run({})

        assert result["metric_value"] is None, "0 emitted reported as a measured zero"
        assert result["status"] == "unmeasurable"
        assert result["success"] is True, "an unbuilt stage is a gap, not a failing cycle"
        details = result["details"]
        assert details["status"] == "unmeasurable"
        assert details["stages_missing"] == [
            "synthesizer", "scorer", "deliberator", "seeder",
        ]
        assert details["emit_stage"] == "seeder"
        assert details["emit_stage_missing"] is True
        assert "seeder" in details["reason"]

    def test_the_raw_count_is_still_carried(self, monkeypatch, _enabled):
        """Withholding the METRIC is not hiding what the cycle did."""
        _arm_engine(monkeypatch, _cycle(harvested=7))

        result = foundry_cycle.run({})
        assert result["harvested"] == 7
        assert result["tasks_emitted"] == 0
        assert result["details"]["stages_ran"] == ["harvester"]

    def test_a_present_seeder_reports_a_real_measured_zero(self, monkeypatch, _enabled):
        """Positive control: with a seeder, 0 emitted IS the gate verdict."""
        _arm_engine(monkeypatch, _cycle(
            stages_missing=[],
            stages_present=list(engine.STAGE_MODULES),
            stages_ran=["harvester", "synthesizer", "novelty_gate"],
        ))

        result = foundry_cycle.run({})
        assert result["metric_value"] == 0.0
        assert result["status"] == "ok"
        assert result["details"]["emit_stage_missing"] is False

    def test_a_present_seeder_that_emitted_reports_the_count(self, monkeypatch, _enabled):
        _arm_engine(monkeypatch, _cycle(stages_missing=[], tasks_emitted=3))

        result = foundry_cycle.run({})
        assert result["metric_value"] == 3.0
        assert result["status"] == "ok"

    def test_an_engine_error_outranks_the_missing_stage(self, monkeypatch, _enabled):
        _arm_engine(monkeypatch, _cycle(status="failed"))

        result = foundry_cycle.run({})
        assert result["status"] == "error"
        assert result["success"] is False

    def test_a_skipped_reflex_reports_no_metric(self, monkeypatch):
        """The flag is off: nothing was measured, so metric_value is None."""
        monkeypatch.delenv(foundry_cycle.FEATURE_FLAG, raising=False)

        result = foundry_cycle.run({})
        assert result["status"] == "skipped"
        assert result["metric_value"] is None
        assert result["details"]["status"] == "skipped"


class TestNoSecondSpellingOfTheEmitStage:
    """Two spellings of 'which stage writes the board' is how this pair comes to
    disagree about a pipeline neither of them changed."""

    def test_the_reflex_imports_the_engine_constant(self):
        import pathlib

        source = pathlib.Path(foundry_cycle.__file__).read_text(encoding="utf-8")
        assert "from tools.foundry.engine import EMIT_STAGE" in source
