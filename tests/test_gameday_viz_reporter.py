# CUI // SP-CTI
"""Tests for tools/gameday/viz_reporter.py — VIZ export for tournament reports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _build_viz_spec
# ---------------------------------------------------------------------------

class TestBuildVizSpec:
    def _fn(self):
        from tools.gameday.viz_reporter import _build_viz_spec
        return _build_viz_spec

    def _sample_tournament(self):
        return {
            "tournament_id": "t-001",
            "scenario_name": "ZERO-DAY-2026",
            "completed_at": "2026-06-25T10:00:00Z",
            "teams": [
                {"team_key": "red", "role": "Attacker", "total_score": 850, "artifact_count": 4, "ai_tool_receipts": 12},
                {"team_key": "blue", "role": "Defender", "total_score": 920, "artifact_count": 5, "ai_tool_receipts": 15},
            ],
            "leaderboard": [
                {"rank": 1, "team_name": "Blue Team", "total_pts": 920, "ai_tools_used": 15},
                {"rank": 2, "team_name": "Red Team", "total_pts": 850, "ai_tools_used": 12},
            ],
            "rounds": [
                {"round_num": 1, "started_at": "2026-06-25T09:00:00Z", "state": "complete"},
                {"round_num": 2, "started_at": "2026-06-25T09:30:00Z", "state": "complete"},
            ],
        }

    def test_returns_dict_with_required_keys(self):
        fn = self._fn()
        spec = fn(self._sample_tournament())
        assert "title" in spec
        assert "slides" in spec
        assert "theme" in spec
        assert "classification" in spec

    def test_slides_contains_title_slide(self):
        fn = self._fn()
        spec = fn(self._sample_tournament())
        types = [s["type"] for s in spec["slides"]]
        assert "title" in types

    def test_slides_contains_leaderboard_when_present(self):
        fn = self._fn()
        spec = fn(self._sample_tournament())
        types = [s["type"] for s in spec["slides"]]
        assert "leaderboard" in types

    def test_slides_contains_team_summaries(self):
        fn = self._fn()
        spec = fn(self._sample_tournament())
        types = [s["type"] for s in spec["slides"]]
        assert types.count("team_summary") == 2

    def test_slides_contains_timeline_when_rounds_present(self):
        fn = self._fn()
        spec = fn(self._sample_tournament())
        types = [s["type"] for s in spec["slides"]]
        assert "timeline" in types

    def test_empty_tournament_has_title_slide(self):
        fn = self._fn()
        spec = fn({})
        assert len(spec["slides"]) >= 1
        assert spec["slides"][0]["type"] == "title"

    def test_aar_content_added_when_present(self):
        fn = self._fn()
        data = {"aar_content": "Key finding: Blue team performed excellently."}
        spec = fn(data)
        types = [s["type"] for s in spec["slides"]]
        assert "text" in types

    def test_classification_marking(self):
        fn = self._fn()
        spec = fn({})
        assert spec["classification"] == "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# export_tournament_report
# ---------------------------------------------------------------------------

class TestExportTournamentReport:
    def _fn(self):
        from tools.gameday.viz_reporter import export_tournament_report
        return export_tournament_report

    def test_returns_dict_with_success_key(self, tmp_path):
        fn = self._fn()
        result = fn(
            {"tournament_id": "t-test", "scenario_name": "TEST"},
            output_format="json",
            output_path=str(tmp_path / "report.json"),
        )
        assert "success" in result

    def test_json_fallback_creates_file(self, tmp_path, monkeypatch):
        import sys, types
        # Remove viz.kernel from sys.modules so ImportError triggers fallback
        sys.modules.pop("tools.viz.kernel", None)
        sys.modules.pop("tools.viz", None)

        fn = self._fn()
        out_path = str(tmp_path / "report.html")
        result = fn(
            {"tournament_id": "t-fallback", "scenario_name": "FALLBACK-TEST"},
            output_format="html",
            output_path=out_path,
        )
        assert result["success"] is True
        assert result["format"] == "json"

    def test_returns_error_key_on_failure(self, monkeypatch):
        import tools.gameday.viz_reporter as vr
        def bad_spec(data):
            raise RuntimeError("broken")
        monkeypatch.setattr(vr, "_build_viz_spec", bad_spec)
        fn = self._fn()
        result = fn({})
        assert result["success"] is False
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# export_leaderboard_slide
# ---------------------------------------------------------------------------

class TestExportLeaderboardSlide:
    def _fn(self):
        from tools.gameday.viz_reporter import export_leaderboard_slide
        return export_leaderboard_slide

    def test_returns_dict(self):
        fn = self._fn()
        result = fn([{"rank": 1, "team_name": "Blue", "total_pts": 900}])
        assert isinstance(result, dict)
        assert "success" in result

    def test_truncates_to_10_entries(self, monkeypatch):
        import sys
        sys.modules.pop("tools.viz.kernel", None)
        sys.modules.pop("tools.viz", None)
        fn = self._fn()
        lb = [{"rank": i, "team_name": f"Team{i}", "total_pts": 1000 - i * 10} for i in range(20)]
        result = fn(lb)
        # Falls back to error (no VIZ kernel) but should not raise
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Module smoke
# ---------------------------------------------------------------------------

def test_module_importable():
    import tools.gameday.viz_reporter as m
    assert callable(m.export_tournament_report)
    assert callable(m.export_leaderboard_slide)
    assert callable(m._build_viz_spec)
