#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the federated data mesh ETL/ML core (issue #19).

Covers the deterministic, no-network logic: ETL normalization, escalation
pattern detection (TF-IDF keyword clustering over unstructured descriptions),
and conflict-escalation scoring. The modules live under the canonical
``icdev.tools`` namespace (no root-``tools`` shim copy).
"""
import sqlite3

from icdev.tools.strategos.federated_mesh import (
    _ESCALATION_PATTERNS,
    _normalize,
    detect_patterns,
)
from icdev.tools.strategos.intel_report_engine import _compute_escalation_score


class TestNormalize:
    """ETL standardization to the mesh schema."""

    def test_standardizes_schema(self):
        out = _normalize({
            "provider": "acled",
            "source_id": 42,
            "event_date": "2026-01-15T08:30:00Z",
            "theater_id": "ukr",
            "event_type": "Battle",
            "description": "x" * 5000,
            "fatalities": "7",
        })
        assert out["provider"] == "acled"
        assert out["source_id"] == "42"          # coerced to str
        assert out["event_date"] == "2026-01-15"  # truncated to date
        assert out["event_type"] == "battle"      # lowercased
        assert out["fatalities"] == 7             # coerced to int
        assert len(out["description"]) == 2000    # truncated
        assert out["id"]                          # uuid assigned

    def test_handles_missing_fields(self):
        out = _normalize({})
        assert out["provider"] == "unknown"
        assert out["event_date"] is None
        assert out["event_type"] == "unknown"
        assert out["fatalities"] == 0


def _seed(conn, descriptions):
    conn.execute("CREATE TABLE sg_mesh_signals (description TEXT, theater_id TEXT, ingested_at TEXT)")
    conn.execute(
        "CREATE TABLE sg_mesh_patterns (id TEXT PRIMARY KEY, pattern_type TEXT, "
        "keywords TEXT, event_count INTEGER, confidence REAL, detected_at TEXT)"
    )
    for d in descriptions:
        conn.execute("INSERT INTO sg_mesh_signals VALUES (?,?,?)", (d, "global", "2026-01-01"))
    conn.commit()


class TestDetectPatterns:
    """TF-IDF escalation-pattern detection over unstructured signal text."""

    def test_detects_force_buildup_and_offensive(self):
        conn = sqlite3.connect(":memory:")
        _seed(conn, [
            "Large mobilization and troop buildup with convoy massing near the border",
            "Major offensive assault and breakthrough; enemy advance reported",
        ])
        patterns = detect_patterns(conn, "global")
        types = {p["pattern_type"] for p in patterns}
        assert "force_buildup" in types
        assert "offensive_action" in types
        for p in patterns:
            assert 0.0 < p["confidence"] <= 1.0

    def test_empty_signals_no_patterns(self):
        conn = sqlite3.connect(":memory:")
        _seed(conn, [])
        assert detect_patterns(conn, "global") == []

    def test_benign_text_yields_no_escalation(self):
        conn = sqlite3.connect(":memory:")
        _seed(conn, ["Routine cultural exchange and trade discussion held in the capital"])
        types = {p["pattern_type"] for p in detect_patterns(conn, "global")}
        assert "force_buildup" not in types
        assert "offensive_action" not in types


class TestEscalationScore:
    """Weighted conflict-escalation scoring."""

    def test_empty_is_zero(self):
        assert _compute_escalation_score([]) == 0.0

    def test_monotonic_in_confidence(self):
        low = _compute_escalation_score([{"pattern_type": "offensive_action", "confidence": 0.2}])
        high = _compute_escalation_score([{"pattern_type": "offensive_action", "confidence": 0.9}])
        assert high > low

    def test_capped_at_one(self):
        saturated = [{"pattern_type": t, "confidence": 1.0} for t in _ESCALATION_PATTERNS]
        assert _compute_escalation_score(saturated) <= 1.0
