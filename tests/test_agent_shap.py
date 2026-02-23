#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AgentSHAP tool attribution (D288)."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.observability.shap.agent_shap import AgentSHAP


def _create_test_db(db_path: Path) -> None:
    """Create minimal DB with otel_spans and shap_attributions tables."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shap_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            shapley_value REAL NOT NULL,
            coalition_size INTEGER,
            confidence_low REAL,
            confidence_high REAL,
            outcome_metric TEXT DEFAULT 'success',
            outcome_value REAL,
            analysis_params TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def _insert_spans(db_path: Path, trace_id: str, tools: list) -> None:
    """Insert synthetic MCP tool call spans."""
    conn = sqlite3.connect(str(db_path))
    for i, (tool_name, status) in enumerate(tools):
        attrs = json.dumps({"mcp.tool.name": tool_name})
        conn.execute(
            """INSERT INTO otel_spans
               (id, trace_id, name, start_time, end_time, duration_ms,
                status_code, attributes, project_id)
               VALUES (?, ?, 'mcp.tool_call', ?, ?, ?, ?, ?, 'proj-test')""",
            (
                f"span-{i}", trace_id, f"2025-01-01T00:0{i}:00Z",
                f"2025-01-01T00:0{i}:01Z", 1000,
                status, attrs,
            ),
        )
    conn.commit()
    conn.close()


class TestAgentSHAPInit(unittest.TestCase):
    """Test AgentSHAP initialization."""

    def test_default_init(self):
        shap = AgentSHAP()
        self.assertIsNotNone(shap)

    def test_custom_db_path(self):
        shap = AgentSHAP(db_path=Path("/tmp/test.db"))
        self.assertEqual(shap._db_path, Path("/tmp/test.db"))

    def test_seed_for_reproducibility(self):
        shap = AgentSHAP(seed=42)
        self.assertIsNotNone(shap)


class TestAnalyzeTrace(unittest.TestCase):
    """Test Shapley value computation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.shap = AgentSHAP(db_path=self.db_path, seed=42)

    def test_no_spans_returns_error(self):
        result = self.shap.analyze_trace("nonexistent-trace")
        self.assertIn("error", result)

    def test_no_tool_calls_returns_error(self):
        """Spans exist but none are mcp.tool_call."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT INTO otel_spans
               (id, trace_id, name, start_time, status_code, attributes)
               VALUES ('s1', 'trace-1', 'other.span', '2025-01-01', 'OK', '{}')""",
        )
        conn.commit()
        conn.close()
        result = self.shap.analyze_trace("trace-1")
        self.assertIn("error", result)

    def test_single_tool_full_attribution(self):
        """Single tool gets 100% attribution."""
        _insert_spans(self.db_path, "trace-single", [("scaffold", "OK")])
        result = self.shap.analyze_trace("trace-single", iterations=100)
        self.assertIn("attributions", result)
        self.assertIn("scaffold", result["attributions"])
        # Single tool should get normalized value of 1.0
        self.assertAlmostEqual(
            result["attributions"]["scaffold"]["normalized"], 1.0, places=2
        )

    def test_two_tools_attribution(self):
        """Two tools split attribution."""
        _insert_spans(self.db_path, "trace-two", [
            ("scaffold", "OK"),
            ("generate_code", "OK"),
        ])
        result = self.shap.analyze_trace("trace-two", iterations=500)
        self.assertIn("attributions", result)
        self.assertEqual(result["tool_count"], 2)
        # Both tools should have normalized values summing to ~1.0
        total = sum(
            r["normalized"] for r in result["attributions"].values()
        )
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_three_tools_with_mixed_status(self):
        """Three tools with mixed success/failure."""
        _insert_spans(self.db_path, "trace-mixed", [
            ("scaffold", "OK"),
            ("generate_code", "OK"),
            ("lint", "ERROR"),
        ])
        result = self.shap.analyze_trace("trace-mixed", iterations=500)
        self.assertEqual(result["tool_count"], 3)
        # Successful tools should have positive attribution
        for tool in ["scaffold", "generate_code"]:
            self.assertGreaterEqual(
                result["attributions"][tool]["shapley_value"], 0.0
            )

    def test_deterministic_with_seed(self):
        """Same seed produces same results when run sequentially."""
        import random
        _insert_spans(self.db_path, "trace-det", [
            ("tool_a", "OK"), ("tool_b", "OK"), ("tool_c", "OK"),
        ])
        random.seed(999)
        shap1 = AgentSHAP(db_path=self.db_path)
        r1 = shap1.analyze_trace("trace-det", iterations=200)
        random.seed(999)
        shap2 = AgentSHAP(db_path=self.db_path)
        r2 = shap2.analyze_trace("trace-det", iterations=200)
        for tool in ["tool_a", "tool_b", "tool_c"]:
            self.assertAlmostEqual(
                r1["attributions"][tool]["shapley_value"],
                r2["attributions"][tool]["shapley_value"],
                places=6,
            )

    def test_confidence_intervals(self):
        """Results include confidence intervals."""
        _insert_spans(self.db_path, "trace-ci", [
            ("scaffold", "OK"), ("test", "OK"),
        ])
        result = self.shap.analyze_trace("trace-ci", iterations=200)
        for tool_result in result["attributions"].values():
            self.assertIn("confidence_low", tool_result)
            self.assertIn("confidence_high", tool_result)
            self.assertLessEqual(
                tool_result["confidence_low"],
                tool_result["confidence_high"],
            )

    def test_stores_results_in_db(self):
        """Results are persisted to shap_attributions table."""
        _insert_spans(self.db_path, "trace-store", [("scaffold", "OK")])
        self.shap.analyze_trace("trace-store", iterations=100)
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM shap_attributions WHERE trace_id = 'trace-store'"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_outcome_metric_success(self):
        """Success metric computes fraction of OK spans."""
        _insert_spans(self.db_path, "trace-suc", [
            ("a", "OK"), ("b", "ERROR"),
        ])
        result = self.shap.analyze_trace(
            "trace-suc", iterations=100, outcome_metric="success"
        )
        self.assertEqual(result["outcome_metric"], "success")

    def test_outcome_metric_duration(self):
        """Duration metric produces valid results."""
        _insert_spans(self.db_path, "trace-dur", [
            ("a", "OK"), ("b", "OK"),
        ])
        result = self.shap.analyze_trace(
            "trace-dur", iterations=100, outcome_metric="duration"
        )
        self.assertEqual(result["outcome_metric"], "duration")
        self.assertIn("full_outcome", result)

    def test_result_structure(self):
        """Verify complete result structure."""
        _insert_spans(self.db_path, "trace-struct", [("scaffold", "OK")])
        result = self.shap.analyze_trace("trace-struct", iterations=50)
        self.assertIn("trace_id", result)
        self.assertIn("tool_count", result)
        self.assertIn("iterations", result)
        self.assertIn("outcome_metric", result)
        self.assertIn("full_outcome", result)
        self.assertIn("attributions", result)

    def test_coalition_size_in_results(self):
        """Each tool result includes coalition_size."""
        _insert_spans(self.db_path, "trace-coal", [
            ("a", "OK"), ("b", "OK"), ("c", "OK"),
        ])
        result = self.shap.analyze_trace("trace-coal", iterations=50)
        for tool_result in result["attributions"].values():
            self.assertEqual(tool_result["coalition_size"], 3)


class TestEvaluateOutcome(unittest.TestCase):
    """Test outcome evaluation functions."""

    def setUp(self):
        self.shap = AgentSHAP(seed=42)

    def test_empty_coalition_returns_zero(self):
        spans = [{"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "a"}', "status_code": "OK"}]
        result = self.shap._evaluate_outcome(spans, set(), "success")
        self.assertEqual(result, 0.0)

    def test_all_ok_success(self):
        spans = [
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "a"}', "status_code": "OK"},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "b"}', "status_code": "OK"},
        ]
        result = self.shap._evaluate_outcome(spans, {"a", "b"}, "success")
        self.assertEqual(result, 1.0)

    def test_partial_success(self):
        spans = [
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "a"}', "status_code": "OK"},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "b"}', "status_code": "ERROR"},
        ]
        result = self.shap._evaluate_outcome(spans, {"a", "b"}, "success")
        self.assertAlmostEqual(result, 0.5)

    def test_duration_metric(self):
        spans = [
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "a"}', "status_code": "OK", "duration_ms": 1000},
        ]
        result = self.shap._evaluate_outcome(spans, {"a"}, "duration")
        self.assertGreater(result, 0.0)
        self.assertLess(result, 1.0)


class TestExtractTools(unittest.TestCase):
    """Test tool extraction from spans."""

    def setUp(self):
        self.shap = AgentSHAP(seed=42)

    def test_extract_unique_tools(self):
        spans = [
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "scaffold"}'},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "generate_code"}'},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "scaffold"}'},
        ]
        tools = self.shap._extract_tools(spans)
        self.assertEqual(len(tools), 2)
        self.assertIn("scaffold", tools)
        self.assertIn("generate_code", tools)

    def test_extract_ignores_non_tool_spans(self):
        spans = [
            {"name": "other.span", "attributes": '{"mcp.tool.name": "test"}'},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "scaffold"}'},
        ]
        tools = self.shap._extract_tools(spans)
        self.assertEqual(len(tools), 1)

    def test_extract_sorted(self):
        spans = [
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "z_tool"}'},
            {"name": "mcp.tool_call", "attributes": '{"mcp.tool.name": "a_tool"}'},
        ]
        tools = self.shap._extract_tools(spans)
        self.assertEqual(tools, ["a_tool", "z_tool"])


if __name__ == "__main__":
    unittest.main()
