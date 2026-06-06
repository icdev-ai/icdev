# CUI // SP-CTI
"""IQE Demo Runner collection adapters.

Importing this module registers three collections:
  demo_runner.runs      — showcase_demo_runs table (run history)
  demo_runner.scenarios — static list of the 5 demo scenarios
  demo_runner.results   — per-scenario result metrics extracted from run JSON
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection

_STATIC_SCENARIOS = [
    {"scenario_id": 1, "title": "AI Observatory",    "canvas": "observatory", "audience": "exec+tech", "hook": "Governance visibility — 200+ decisions, 18 confab flags"},
    {"scenario_id": 2, "title": "AADC Design",       "canvas": "aadc",        "audience": "exec+tech", "hook": "HITL + rights-impacting — 5/8 HITL, 2 OMB M-25-21 flagged"},
    {"scenario_id": 3, "title": "AIMC Models",       "canvas": "aimc",        "audience": "exec+tech", "hook": "IL5 air-gap + ROI — SIGINT NLP, FAR/DFARS 29x ROI"},
    {"scenario_id": 4, "title": "AAC Modernization", "canvas": "aac",         "audience": "exec+tech", "hook": "Legacy scan — GCSS-Army 287K LOC, SIEM composite=0.85"},
    {"scenario_id": 5, "title": "Cross-Canvas Gov",  "canvas": "cross",       "audience": "exec+tech", "hook": "OMB M-25-21 inventory — 3 rights-impacting, CAIO queue"},
]


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    return conn


def runs_adapter(conn: Any) -> list[dict]:
    """Return rows from showcase_demo_runs."""
    conn = _conn(conn)
    try:
        cur = conn.execute(
            "SELECT run_id, audience, scenarios_json, status, scenarios_passed, "
            "scenarios_total, elapsed_ms, created_at "
            "FROM showcase_demo_runs ORDER BY created_at DESC LIMIT 50"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def scenarios_adapter(conn: Any) -> list[dict]:
    """Return the static list of 5 demo scenarios."""
    return _STATIC_SCENARIOS


def results_adapter(conn: Any) -> list[dict]:
    """Return per-scenario metrics extracted from the most recent 20 runs."""
    conn = _conn(conn)
    try:
        cur = conn.execute(
            "SELECT run_id, audience, status, result_json, created_at "
            "FROM showcase_demo_runs WHERE result_json IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 20"
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []

    results = []
    for run in rows:
        try:
            result_json = json.loads(run.get("result_json") or "{}")
        except (ValueError, TypeError):
            result_json = {}
        for key, val in result_json.items():
            if not isinstance(val, dict):
                continue
            scenario_num = int(key[1]) if len(key) == 2 and key[0] == "s" else 0
            meta = _STATIC_SCENARIOS[scenario_num - 1] if 1 <= scenario_num <= 5 else {}
            results.append({
                "run_id": run["run_id"],
                "audience": run["audience"],
                "run_status": run["status"],
                "run_created_at": run["created_at"],
                "scenario": key,
                "scenario_title": meta.get("title", key),
                "scenario_status": val.get("status", "unknown"),
                **{k: v for k, v in val.items() if k != "status"},
            })
    return results


register_collection("demo_runner.runs", runs_adapter)
register_collection("demo_runner.scenarios", scenarios_adapter)
register_collection("demo_runner.results", results_adapter)
