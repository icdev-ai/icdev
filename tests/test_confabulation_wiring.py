# CUI // SP-CTI
"""Tests: confabulation detector wired into drafting (halluc-01, closes C-gap-1).

Covers:
    - confabulation_detector.assess() pure risk scoring (no DB)
    - check_output() still returns the assessment (refactor to reuse assess)
"""

import importlib

cd = importlib.import_module("tools.security.confabulation_detector")

_CLEAN = "The Contractor implements zero-trust networking per NIST 800-53 controls."
_HEDGE = "As an AI, I think this might probably work but I could be wrong."


class TestAssess:
    def test_shape(self):
        a = cd.assess(_CLEAN)
        for key in ("risk_score", "risk_level", "findings", "findings_count", "checks_performed"):
            assert key in a
        assert isinstance(a["findings"], list)
        assert a["risk_level"] in ("low", "medium", "high")

    def test_clean_text_low_risk(self):
        a = cd.assess(_CLEAN)
        assert a["findings_count"] == 0
        assert a["risk_level"] == "low"

    def test_hedging_flagged(self):
        a = cd.assess(_HEDGE)
        assert a["findings_count"] >= 1
        assert any(f.get("type") == "hedging_language" for f in a["findings"])

    def test_risk_score_bounded(self):
        assert 0.0 <= cd.assess(_HEDGE)["risk_score"] <= 1.0
        assert cd.assess("")["risk_score"] == 0.0


class TestCheckOutputReusesAssess:
    def test_check_output_returns_assessment(self, monkeypatch):
        class _FakeConn:
            def execute(self, *a, **k):
                return self

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(cd, "_get_connection", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(cd, "_ensure_table", lambda conn: None)
        result = cd.check_output("proj-1", _HEDGE)
        assert result["project_id"] == "proj-1"
        assert result["findings_count"] >= 1
        assert result["risk_level"] in ("low", "medium", "high")
        assert "input_hash" in result
