#!/usr/bin/env python3
# CUI // SP-CTI — PDC Pre-Merge Runner
"""Run antipattern_detector + slsa_assessor on a delta graph without touching the DB.

Accepts a delta graph (nodes/edges JSON), optionally merges with a baseline, then
runs the two existing analyzers and returns a gate verdict.

Usage:
    python tools/pipeline/premerge_runner.py --delta delta.json [--baseline baseline.json] [--gate] [--json]
    python tools/pipeline/premerge_runner.py --delta '{"nodes":[...],"edges":[...]}' --gate

Output JSON:
    {
        "gate": "pass" | "warn" | "fail",
        "antipattern_hits": [...],
        "slsa_score": 0..4
    }

Exit codes:
    0 — pass or warn (or when --gate is not set)
    1 — fail (only when --gate is set)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.pipeline.antipattern_detector import detect_antipatterns  # noqa: E402
from tools.pipeline.blueprint import assess_slsa  # noqa: E402


# ── Graph merge ───────────────────────────────────────────────────────────────


def _merge_graphs(baseline: dict, delta: dict) -> dict:
    """Apply delta on top of baseline. Delta nodes/edges override by ID; new ones are appended.

    Nodes/edges present in baseline but absent from delta are preserved.
    This is a non-destructive merge — use explicit empty lists in delta to "clear" nothing.
    """
    b_nodes = {n["id"]: n for n in baseline.get("nodes", [])}
    b_edges = {e["id"]: e for e in baseline.get("edges", [])}

    for n in delta.get("nodes", []):
        b_nodes[n["id"]] = n
    for e in delta.get("edges", []):
        b_edges[e["id"]] = e

    return {
        "nodes": list(b_nodes.values()),
        "edges": list(b_edges.values()),
    }


# ── Verdict ───────────────────────────────────────────────────────────────────


def _compute_verdict(antipatterns: list) -> str:
    """FAIL on any critical antipattern. WARN on any high. PASS otherwise."""
    severities = {a.get("severity") for a in antipatterns}
    if "critical" in severities:
        return "fail"
    if "high" in severities:
        return "warn"
    return "pass"


# ── Core public function ──────────────────────────────────────────────────────


def run_premerge(delta: dict, baseline: dict | None = None) -> dict:
    """Apply delta to baseline (optional) and return gate verdict.

    Args:
        delta: Graph dict with "nodes" and "edges" lists representing the proposed change.
        baseline: Optional base graph to merge delta on top of. When omitted, delta is
            analysed as-is (faster, sufficient for most pre-merge checks).

    Returns:
        {"gate": "pass"|"warn"|"fail", "antipattern_hits": [...], "slsa_score": int}
    """
    graph = _merge_graphs(baseline, delta) if baseline else delta
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    antipattern_hits = detect_antipatterns(nodes, edges)
    slsa = assess_slsa(nodes, edges)

    return {
        "gate": _compute_verdict(antipattern_hits),
        "antipattern_hits": antipattern_hits,
        "slsa_score": slsa.get("achieved_level", 0),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _load_graph(value: str) -> dict:
    """Load a graph dict from a file path or an inline JSON string."""
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-merge antipattern + SLSA gate for PDC pipeline designs."
    )
    parser.add_argument(
        "--delta",
        required=True,
        help="Delta graph: file path or inline JSON {nodes, edges}.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline graph to merge delta on top of (file path or inline JSON).",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 when verdict is 'fail'.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON (default is human-readable text).",
    )
    args = parser.parse_args(argv)

    delta = _load_graph(args.delta)
    baseline = _load_graph(args.baseline) if args.baseline else None

    result = run_premerge(delta, baseline)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        gate = result["gate"].upper()
        hits = len(result["antipattern_hits"])
        slsa = result["slsa_score"]
        print(f"gate={gate}  antipattern_hits={hits}  slsa_score={slsa}")
        for ap in result["antipattern_hits"]:
            print(f"  [{ap['severity'].upper()}] {ap['id']} — {ap['title']}")

    if args.gate and result["gate"] == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
