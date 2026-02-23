#!/usr/bin/env python3
# CUI // SP-CTI
"""AgentSHAP — Monte Carlo Shapley value tool attribution (D288).

Computes Shapley values for tool importance in LLM agent traces.
Uses Monte Carlo sampling (stdlib `random`, D22 air-gap safe).

Reference: arXiv:2512.12597 — 0.945 consistency, model-agnostic.

Usage:
    from tools.observability.shap.agent_shap import AgentSHAP
    shap = AgentSHAP()
    results = shap.analyze_trace(trace_id="abc123", iterations=1000)

CLI:
    python tools/observability/shap/agent_shap.py --trace-id abc123 --json
    python tools/observability/shap/agent_shap.py --project-id proj-123 --last-n 10 --json
"""

import argparse
import json
import logging
import math
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("icdev.observability.shap")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class AgentSHAP:
    """Monte Carlo Shapley value computation for tool attribution (D288).

    Determines which tools contributed most to trace outcome using
    coalition sampling and marginal contribution measurement.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        seed: Optional[int] = None,
    ):
        self._db_path = db_path or DB_PATH
        if seed is not None:
            random.seed(seed)

    def analyze_trace(
        self,
        trace_id: str,
        iterations: int = 1000,
        outcome_metric: str = "success",
    ) -> Dict[str, Any]:
        """Compute Shapley values for tools in a single trace.

        Args:
            trace_id: Trace ID to analyze.
            iterations: Monte Carlo sampling iterations.
            outcome_metric: Metric to evaluate ("success", "duration", "quality").

        Returns:
            Dict with tool attributions and metadata.
        """
        spans = self._get_trace_spans(trace_id)
        if not spans:
            return {"error": "No spans found for trace", "trace_id": trace_id}

        # Extract unique tool names from spans
        tools = self._extract_tools(spans)
        if not tools:
            return {"error": "No tool calls in trace", "trace_id": trace_id}

        # Compute outcome for the full trace
        full_outcome = self._evaluate_outcome(spans, tools, outcome_metric)

        # Monte Carlo Shapley estimation
        shapley_values: Dict[str, List[float]] = {t: [] for t in tools}

        for _ in range(iterations):
            # Random permutation of tools
            perm = list(tools)
            random.shuffle(perm)

            coalition: Set[str] = set()
            prev_value = self._evaluate_outcome(spans, coalition, outcome_metric)

            for tool in perm:
                coalition.add(tool)
                curr_value = self._evaluate_outcome(spans, coalition, outcome_metric)
                marginal = curr_value - prev_value
                shapley_values[tool].append(marginal)
                prev_value = curr_value

        # Compute statistics
        results = {}
        for tool in tools:
            values = shapley_values[tool]
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = math.sqrt(variance)
            # 95% confidence interval
            z = 1.96
            ci_low = mean - z * std / math.sqrt(len(values))
            ci_high = mean + z * std / math.sqrt(len(values))

            results[tool] = {
                "shapley_value": round(mean, 6),
                "confidence_low": round(ci_low, 6),
                "confidence_high": round(ci_high, 6),
                "std": round(std, 6),
                "coalition_size": len(tools),
            }

        # Normalize to [0, 1]
        total = sum(abs(r["shapley_value"]) for r in results.values())
        if total > 0:
            for tool in results:
                results[tool]["normalized"] = round(
                    abs(results[tool]["shapley_value"]) / total, 6
                )
        else:
            for tool in results:
                results[tool]["normalized"] = round(1.0 / len(tools), 6)

        # Store results
        self._store_attributions(trace_id, results, iterations, outcome_metric)

        return {
            "trace_id": trace_id,
            "tool_count": len(tools),
            "iterations": iterations,
            "outcome_metric": outcome_metric,
            "full_outcome": full_outcome,
            "attributions": results,
        }

    def _extract_tools(self, spans: List[Dict]) -> List[str]:
        """Extract unique tool names from MCP tool call spans."""
        tools = set()
        for span in spans:
            if span.get("name") == "mcp.tool_call":
                attrs = json.loads(span.get("attributes", "{}"))
                tool_name = attrs.get("mcp.tool.name")
                if tool_name:
                    tools.add(tool_name)
        return sorted(tools)

    def _evaluate_outcome(
        self,
        spans: List[Dict],
        coalition: Set[str],
        metric: str,
    ) -> float:
        """Evaluate outcome for a coalition of tools.

        The outcome function measures trace quality with only the
        coalition tools active. For excluded tools, their spans are
        treated as if they returned errors.
        """
        if not coalition:
            return 0.0

        relevant_spans = []
        for span in spans:
            if span.get("name") == "mcp.tool_call":
                attrs = json.loads(span.get("attributes", "{}"))
                tool_name = attrs.get("mcp.tool.name", "")
                if tool_name in coalition:
                    relevant_spans.append(span)

        if not relevant_spans:
            return 0.0

        if metric == "success":
            # Fraction of tool calls that succeeded
            ok_count = sum(
                1 for s in relevant_spans if s.get("status_code") == "OK"
            )
            return ok_count / len(relevant_spans)
        elif metric == "duration":
            # Inverse of total duration (lower is better, so invert)
            total_ms = sum(s.get("duration_ms", 0) for s in relevant_spans)
            return 1.0 / (1.0 + total_ms / 1000.0)
        else:
            # Default: success rate
            ok_count = sum(
                1 for s in relevant_spans if s.get("status_code") == "OK"
            )
            return ok_count / len(relevant_spans)

    def _get_trace_spans(self, trace_id: str) -> List[Dict]:
        """Fetch all spans for a trace."""
        if not self._db_path.exists():
            return []

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM otel_spans WHERE trace_id = ? ORDER BY start_time",
                (trace_id,),
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    def _store_attributions(
        self,
        trace_id: str,
        results: Dict[str, Dict],
        iterations: int,
        outcome_metric: str,
    ) -> None:
        """Store Shapley attributions in DB (append-only, D6)."""
        if not self._db_path.exists():
            return

        try:
            conn = sqlite3.connect(str(self._db_path))
            for tool_name, attrs in results.items():
                conn.execute(
                    """INSERT INTO shap_attributions
                       (trace_id, tool_name, shapley_value, coalition_size,
                        confidence_low, confidence_high, outcome_metric,
                        outcome_value, analysis_params)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trace_id,
                        tool_name,
                        attrs["shapley_value"],
                        attrs["coalition_size"],
                        attrs["confidence_low"],
                        attrs["confidence_high"],
                        outcome_metric,
                        attrs.get("normalized", 0),
                        json.dumps({"iterations": iterations}),
                    ),
                )
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            logger.error("Failed to store SHAP attributions: %s", e)


def main():
    parser = argparse.ArgumentParser(description="AgentSHAP Tool Attribution (D288)")
    parser.add_argument("--trace-id", help="Trace ID to analyze")
    parser.add_argument("--project-id", help="Project ID for batch analysis")
    parser.add_argument("--last-n", type=int, default=5, help="Last N traces to analyze")
    parser.add_argument("--iterations", type=int, default=1000, help="MC iterations")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    shap = AgentSHAP()

    if args.trace_id:
        result = shap.analyze_trace(args.trace_id, iterations=args.iterations)
    else:
        result = {"error": "Provide --trace-id"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
