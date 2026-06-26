# CUI // SP-CTI
"""Tests for tools/gameday/nova_hook.py — NOVA training pair persistence."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _build_training_pair
# ---------------------------------------------------------------------------

class TestBuildTrainingPair:
    def _fn(self):
        from tools.gameday.nova_hook import _build_training_pair
        return _build_training_pair

    def test_returns_prompt_and_completion(self):
        fn = self._fn()
        artifact = {
            "team_role": "defender",
            "artifact_type": "ir_playbook",
            "content": {"steps": ["detect", "contain"]},
        }
        result = fn(artifact, "ZERO-DAY-2026")
        assert "prompt" in result
        assert "completion" in result

    def test_prompt_contains_role_and_scenario(self):
        fn = self._fn()
        artifact = {"team_role": "adversary", "artifact_type": "attack_plan", "content": {}}
        result = fn(artifact, "APT-SIMULATION")
        assert "adversary" in result["prompt"]
        assert "APT-SIMULATION" in result["prompt"]

    def test_completion_truncated_to_2000(self):
        fn = self._fn()
        big_content = "x" * 5000
        artifact = {"team_role": "defender", "artifact_type": "countermeasures", "content": big_content}
        result = fn(artifact, "test")
        assert len(result["completion"]) <= 2000

    def test_handles_dict_content(self):
        fn = self._fn()
        artifact = {"team_role": "innovator", "artifact_type": "innovation_package", "content": {"key": "value"}}
        result = fn(artifact, "FORGE-CHALLENGE")
        assert isinstance(result["completion"], str)

    def test_handles_missing_content_gracefully(self):
        fn = self._fn()
        artifact = {"team_role": "compliance", "artifact_type": "nist_audit"}
        result = fn(artifact, "COMPLIANCE-OPS")
        assert isinstance(result["completion"], str)


# ---------------------------------------------------------------------------
# persist_tournament_learnings
# ---------------------------------------------------------------------------

class TestPersistTournamentLearnings:
    def _fn(self):
        from tools.gameday.nova_hook import persist_tournament_learnings
        return persist_tournament_learnings

    def test_empty_artifacts_returns_zero_pairs(self):
        fn = self._fn()
        result = fn({"artifacts": [], "scenario_name": "test"})
        assert result["pairs_submitted"] == 0
        assert result["nova_available"] is False

    def test_low_score_artifacts_are_skipped(self):
        fn = self._fn()
        result = fn({
            "artifacts": [
                {"judge_score": 50.0, "team_role": "red", "artifact_type": "attack_plan", "content": {}},
                {"judge_score": 60.0, "team_role": "blue", "artifact_type": "defense_posture", "content": {}},
            ],
            "scenario_name": "test",
        })
        assert result["pairs_submitted"] == 0
        assert result["skipped"] == 2

    def test_returns_correct_keys(self):
        fn = self._fn()
        result = fn({"artifacts": [], "scenario_name": "test"})
        assert set(result.keys()) == {"pairs_submitted", "skipped", "nova_available"}

    def test_high_score_artifacts_counted(self, monkeypatch):
        import sys
        import types
        import tools.gameday.nova_hook as nh
        submitted = []

        def fake_submit(prompt, completion, metadata, quality_score):
            submitted.append({"prompt": prompt, "completion": completion})

        monkeypatch.setattr(nh, "_SCORE_THRESHOLD", 75.0)
        fake_nova_mod = types.ModuleType("tools.nova.echo")
        fake_nova_mod.submit_training_pair = fake_submit
        if "tools.nova" not in sys.modules:
            sys.modules["tools.nova"] = types.ModuleType("tools.nova")
        sys.modules["tools.nova.echo"] = fake_nova_mod

        try:
            from tools.gameday.nova_hook import persist_tournament_learnings
            result = persist_tournament_learnings({
                "artifacts": [
                    {"judge_score": 80.0, "team_role": "gold", "artifact_type": "innovation_package", "content": {"idea": "x"}},
                    {"judge_score": 90.0, "team_role": "green", "artifact_type": "compliance_verdict", "content": {}},
                ],
                "tournament_id": "t-001",
                "scenario_name": "LIVE-FIRE",
            })
            assert result["pairs_submitted"] == 2
        finally:
            sys.modules.pop("tools.nova.echo", None)


# ---------------------------------------------------------------------------
# Module-level smoke test
# ---------------------------------------------------------------------------

def test_module_importable():
    import tools.gameday.nova_hook as m
    assert callable(m.persist_tournament_learnings)
    assert callable(m._build_training_pair)
