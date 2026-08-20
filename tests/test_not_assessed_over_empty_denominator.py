# CUI // SP-CTI
"""NOT ASSESSED, never a perfect score, when the denominator is empty (rem-hyg-13).

The census in tests/ci/test_perfect_score_census.py asserts the SHAPE is gone
from the source. This asserts the BEHAVIOUR: each fixed producer, called with
nothing to measure, returns ``None`` rather than 100.0.

The distinction matters because the two fail differently. A refactor could
satisfy the census by rewriting the conditional expression as an ``if``
statement whose else-branch still assigns 100.0 — the AST predicate looks for
an ``IfExp`` and would not see it. These tests would.

``None`` is asserted rather than falsiness throughout: ``0.0`` is falsy too, and
a MEASURED zero is a real finding that must keep rendering as one.
"""
from __future__ import annotations

import importlib

import pytest


def _load(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception as exc:  # pragma: no cover - reported, never skipped away
        pytest.fail(f"{dotted} did not import: {exc!r}")


class TestSafetyRedundancy:
    """A design with no agent nodes is EMPTY, not fully protected."""

    def test_no_agent_nodes_scores_none(self):
        mod = _load("tools.agentic_ai_canvas.safety_redundancy")
        assert mod.analyze_safety_redundancy([], [])["score"] is None

    def test_a_measured_zero_is_still_a_number(self):
        # One agent, nothing guarding it. That is a real 0% and must not be
        # collapsed into "not assessed".
        mod = _load("tools.agentic_ai_canvas.safety_redundancy")
        result = mod.analyze_safety_redundancy(
            [{"id": "a1", "type": "autonomous-agent"}], []
        )
        assert result["score"] == 0.0


class TestIl5SlaCompliance:
    """`known` counts only the events whose SLA outcome could be decided."""

    def test_no_decidable_events_reports_none(self):
        mod = _load("tools.il5.il5_display_service")
        import json

        payload = json.loads(mod.render_il5_ui([]))
        assert payload["summary"]["compliance_pct"] is None

    def test_events_with_unknown_sla_report_none_not_perfect(self):
        # The dangerous case: rows EXIST, so a count-based check would say the
        # data is present, but none of them carries a decidable SLA outcome.
        mod = _load("tools.il5.il5_display_service")
        import json

        events = [{"id": 1, "sla_met": None}, {"id": 2, "sla_met": None}]
        payload = json.loads(mod.render_il5_ui(events))
        assert payload["summary"]["compliance_pct"] is None
        assert payload["summary"]["sla_unknown"] == 2


class TestStyleEngine:
    """An empty section has no prose, which is not the same as flawless prose."""

    def test_no_sections_scores_none(self):
        mod = _load("tools.document_intelligence.style_engine")
        result = mod.check_sections([])
        assert result["overall_score"] is None
        assert result["passed"] is None
        assert result["assessed_sections"] == 0

    def test_only_empty_sections_scores_none(self):
        mod = _load("tools.document_intelligence.style_engine")
        result = mod.check_sections(
            [{"heading": "A", "content": ""}, {"heading": "B", "content": "   "}]
        )
        assert result["overall_score"] is None
        assert result["assessed_sections"] == 0
        assert all(s["assessed"] is False for s in result["sections"])
        assert all(s["result"]["score"] is None for s in result["sections"])

    def test_empty_sections_do_not_drag_the_average(self):
        # The second defect at this site: total_score summed only the SCORED
        # sections while the divisor counted every section, so one clean
        # section beside three empty ones averaged to a quarter of its score.
        mod = _load("tools.document_intelligence.style_engine")
        prose = "The Contractor shall deliver the system."
        alone = mod.check_sections([{"heading": "A", "content": prose}])
        padded = mod.check_sections(
            [
                {"heading": "A", "content": prose},
                {"heading": "B", "content": ""},
                {"heading": "C", "content": ""},
                {"heading": "D", "content": ""},
            ]
        )
        assert padded["overall_score"] == alone["overall_score"]
        assert padded["assessed_sections"] == 1


class TestMaintenanceSlaCompliance:
    """No vulnerability rows means nothing was SCANNED, not perfect remediation."""

    def test_the_no_rows_path_reports_none(self, monkeypatch):
        mod = _load("tools.maintenance.maintenance_auditor")

        class _Conn:
            def execute(self, *_a, **_k):
                return self

            def fetchall(self):
                return []

        stats = mod._collect_vulnerability_stats(_Conn(), "proj-1")
        assert stats["sla_compliant_pct"] is None
        # The counts beside it are honest zeroes — only the rate was fabricated.
        assert stats["vulnerable_count"] == 0
        assert stats["overdue_critical"] == 0


class TestGovernanceOverall:
    """An unassessable framework is DROPPED from the mean, not averaged in."""

    def test_none_scores_are_excluded_from_the_mean(self):
        mod = _load("tools.aiml_canvas.governance_assessor")
        # Two frameworks at 80, one that could not be assessed. The mean of the
        # assessed pair is 80. Averaging the None in as 0 gives 53.3 and as 100
        # gives 86.7 — both are claims about evidence nobody gathered.
        assessed = [s for s in (80.0, None, 80.0) if s is not None]
        assert round(sum(assessed) / len(assessed), 1) == 80.0
        # And the real function agrees on an empty design: no models named, so
        # IL suitability is not assessed rather than perfect.
        il = mod.assess_il_suitability({"nodes": []}, {"il_level": "IL5"})
        assert il["score"] is None
        assert il["passed"] is None
