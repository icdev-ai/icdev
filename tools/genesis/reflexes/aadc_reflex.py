#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — AADC Compliance Scoring (aadc_compliance).

Triggered by Genesis on aadc_design_events where
event_type IN ('assess', 'simulation_run').

Cycle:
  load    → fetch latest design snapshot from aadc_designs
  score   → compute NIST AI RMF / OWASP LLM / MITRE ATLAS node-coverage scores
  memory  → write insight to memory_entries if overall score changed ≥ 5 points
  suggest → create Kanban chore suggestion if any framework < 40% coverage

COOLDOWN_HOURS = 4 (enforced by Genesis daemon).
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

COOLDOWN_HOURS = 4

# ---------------------------------------------------------------------------
# Optional imports — slim environments may lack DB or canvas modules
# ---------------------------------------------------------------------------

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

try:
    from tools.agentic_ai_canvas.db.init_db import get_connection as _aadc_conn
except ImportError:
    _aadc_conn = None  # type: ignore[assignment]

try:
    from tools.agentic_ai_canvas.assessment import score_design as _ext_score
except ImportError:
    _ext_score = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Node-type coverage maps per compliance framework
# ---------------------------------------------------------------------------

# NIST AI RMF: governance, accountability, output control
_NIST_NODES: frozenset = frozenset({
    "hitl-gate",
    "audit-logger",
    "compliance-reporter",
    "confidence-threshold",
    "circuit-breaker",
    "output-validator",
})

# OWASP LLM Top 10: input/output security, rate control, PII
_OWASP_NODES: frozenset = frozenset({
    "input-sanitizer",
    "guardrail",
    "rate-limiter",
    "pii-detector",
    "output-validator",
    "data-validator",
    "token-budget",
})

# MITRE ATLAS: adversarial ML, behavioral monitoring
_ATLAS_NODES: frozenset = frozenset({
    "baseline-snapshot",
    "drift-detector",
    "alert-manager",
    "siem-forwarder",
})

_CRITICAL_THRESHOLD = 40.0  # any framework below this triggers a Kanban suggestion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _aadc_connection() -> Any:
    """Return an AADC-specific connection, falling back to main ICDEV DB."""
    if _aadc_conn is not None:
        return _aadc_conn()
    if get_connection is not None:
        return get_connection()
    raise RuntimeError("No DB connection available")


def _score_framework(node_types: List[str], expected: frozenset) -> float:
    """Return % of expected node types present in design (0–100)."""
    if not expected:
        return 0.0
    present = sum(1 for n in expected if n in node_types)
    return round(present / len(expected) * 100, 1)


def _score_design(graph_json: str) -> Dict[str, float]:
    """Compute NIST/OWASP/ATLAS node-coverage scores from graph JSON."""
    if _ext_score is not None:
        try:
            return _ext_score(graph_json)
        except Exception:
            pass  # fall through to built-in scorer

    try:
        graph = json.loads(graph_json or '{"nodes":[],"edges":[]}')
    except (json.JSONDecodeError, TypeError):
        graph = {"nodes": [], "edges": []}

    node_types = [n.get("type", "") for n in graph.get("nodes", [])]

    nist = _score_framework(node_types, _NIST_NODES)
    owasp = _score_framework(node_types, _OWASP_NODES)
    atlas = _score_framework(node_types, _ATLAS_NODES)
    overall = round((nist + owasp + atlas) / 3, 1)

    return {
        "nist_rmf_score": nist,
        "owasp_score": owasp,
        "atlas_score": atlas,
        "overall_score": overall,
    }


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def _load_latest_design(payload: dict) -> Optional[Dict[str, Any]]:
    """Load design referenced by payload, or the most recently updated one."""
    design_id = (
        payload.get("design_id")
        or (payload.get("event_data") or {}).get("design_id")
    )
    conn = None
    try:
        conn = _aadc_connection()
        if design_id:
            row = conn.execute(
                "SELECT id, name, graph_json, autonomy_max, "
                "safety_impacting, rights_impacting "
                "FROM aadc_designs WHERE id = ?",
                (design_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, name, graph_json, autonomy_max, "
                "safety_impacting, rights_impacting "
                "FROM aadc_designs ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        print(f"  [aadc_compliance] WARNING: design load failed: {exc}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _get_previous_score(design_id: str) -> Optional[float]:
    """Return the most recent assessment overall_score for this design, or None."""
    conn = None
    try:
        conn = _aadc_connection()
        row = conn.execute(
            "SELECT score FROM aadc_assessments WHERE design_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
        return float(row[0]) if row else None
    except Exception as exc:
        print(f"  [aadc_compliance] WARNING: previous score lookup failed: {exc}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _write_assessment(design_id: str, scores: Dict[str, float]) -> None:
    """Persist computed scores to aadc_assessments."""
    conn = None
    try:
        conn = _aadc_connection()
        conn.execute(
            "INSERT INTO aadc_assessments "
            "(id, design_id, score, nist_rmf_score, owasp_score, findings_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _uid(),
                design_id,
                scores["overall_score"],
                scores["nist_rmf_score"],
                scores["owasp_score"],
                json.dumps({"atlas_score": scores["atlas_score"]}),
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"  [aadc_compliance] WARNING: assessment write failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _write_memory_insight(design_name: str, scores: Dict[str, float], delta: float) -> None:
    """Write a memory insight via memory_write.write_to_db."""
    try:
        from tools.memory.memory_write import write_to_db
    except ImportError:
        print("  [aadc_compliance] WARNING: memory_write not available — skipping insight")
        return

    if delta == 0.0:
        summary = (
            f"Initial AADC compliance baseline recorded for '{design_name}'. "
        )
    else:
        direction = "improved" if delta > 0 else "declined"
        summary = (
            f"AADC compliance score for '{design_name}' {direction} by {abs(delta):.1f} points. "
        )

    content = (
        f"{summary}"
        f"Scores — NIST AI RMF: {scores['nist_rmf_score']:.0f}%, "
        f"OWASP LLM: {scores['owasp_score']:.0f}%, "
        f"MITRE ATLAS: {scores['atlas_score']:.0f}%, "
        f"Overall: {scores['overall_score']:.0f}%."
    )

    try:
        write_to_db(content, entry_type="insight", importance=6, source="auto")
    except Exception as exc:
        print(f"  [aadc_compliance] WARNING: memory write failed: {exc}")


def _create_kanban_suggestion(design_name: str, gaps: List[str]) -> Optional[str]:
    """Insert a Kanban chore suggestion for critical compliance gaps."""
    if get_connection is None:
        print("  [aadc_compliance] WARNING: get_connection unavailable — skipping Kanban suggestion")
        return None

    task_id = f"aadc-gap-{_uid()}"
    gap_str = ", ".join(gaps)
    title = f"[AADC] Fix critical compliance gaps in '{design_name}'"
    description = (
        f"Genesis aadc_compliance reflex detected critical framework gaps (< 40%) "
        f"in design '{design_name}'. "
        f"Frameworks below threshold: {gap_str}. "
        "Add the required node types to bring all frameworks to ≥ 40% coverage. "
        "Consult the aadc_assessments table for detailed scores and the AADC canvas "
        "node reference for required node types per framework."
    )
    now = _utcnow_iso()
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            " executor_type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, title, description,
                "chore", "high", "suggested",
                "claude_cli", now, now,
            ),
        )
        conn.commit()
        return task_id
    except Exception as exc:
        print(f"  [aadc_compliance] WARNING: Kanban suggestion failed: {exc}")
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(payload: dict, ctx: Any) -> Dict[str, Any]:
    """Execute one AADC compliance scoring cycle.

    Triggered by Genesis on aadc_design_events where
    event_type IN ('assess', 'simulation_run').

    Returns a summary dict consumed by the Genesis daemon success_metric gate.
    """
    run_id = f"aadc-compliance-{_uid()}"
    start_wall = time.time()

    print(f"[aadc_compliance] run start  run_id={run_id}")

    # ------------------------------------------------------------------
    # Phase 1 — load design
    # ------------------------------------------------------------------
    design = _load_latest_design(payload)
    if design is None:
        elapsed_ms = int((time.time() - start_wall) * 1000)
        print(f"  [aadc_compliance] no design found — run skipped  run_id={run_id}")
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {
                "run_id": run_id,
                "reason": "no_design_found",
                "elapsed_ms": elapsed_ms,
            },
        }

    design_id = design["id"]
    design_name = design.get("name", design_id)
    print(f"  [aadc_compliance] design={design_name!r} ({design_id})")

    # ------------------------------------------------------------------
    # Phase 2 — score compliance
    # ------------------------------------------------------------------
    scores = _score_design(design.get("graph_json", "{}"))
    print(
        f"  [aadc_compliance] scores"
        f"  nist={scores['nist_rmf_score']}%"
        f"  owasp={scores['owasp_score']}%"
        f"  atlas={scores['atlas_score']}%"
        f"  overall={scores['overall_score']}%"
    )

    # ------------------------------------------------------------------
    # Phase 3 — compare to previous; write memory insight if delta ≥ 5
    # ------------------------------------------------------------------
    prev_score = _get_previous_score(design_id)
    wrote_insight = False
    delta = 0.0

    if prev_score is not None:
        delta = scores["overall_score"] - prev_score
        if abs(delta) >= 5.0:
            print(f"  [aadc_compliance] score delta={delta:+.1f} — writing memory insight")
            _write_memory_insight(design_name, scores, delta)
            wrote_insight = True
    else:
        # First run for this design — record the baseline
        _write_memory_insight(design_name, scores, 0.0)
        wrote_insight = True

    _write_assessment(design_id, scores)

    # ------------------------------------------------------------------
    # Phase 4 — detect critical gaps and create Kanban suggestion
    # ------------------------------------------------------------------
    critical_gaps: List[str] = []
    if scores["nist_rmf_score"] < _CRITICAL_THRESHOLD:
        critical_gaps.append(f"NIST AI RMF ({scores['nist_rmf_score']:.0f}%)")
    if scores["owasp_score"] < _CRITICAL_THRESHOLD:
        critical_gaps.append(f"OWASP LLM ({scores['owasp_score']:.0f}%)")
    if scores["atlas_score"] < _CRITICAL_THRESHOLD:
        critical_gaps.append(f"MITRE ATLAS ({scores['atlas_score']:.0f}%)")

    suggestion_id: Optional[str] = None
    if critical_gaps:
        print(f"  [aadc_compliance] critical gaps detected: {critical_gaps}")
        suggestion_id = _create_kanban_suggestion(design_name, critical_gaps)
        if suggestion_id:
            print(f"  [aadc_compliance] Kanban suggestion created: {suggestion_id}")

    elapsed_ms = int((time.time() - start_wall) * 1000)
    print(
        f"[aadc_compliance] run done  run_id={run_id}"
        f"  overall={scores['overall_score']}%"
        f"  gaps={len(critical_gaps)}"
        f"  elapsed={elapsed_ms}ms"
    )

    return {
        "success": True,
        "metric_value": scores["overall_score"],
        "details": {
            "run_id": run_id,
            "design_id": design_id,
            "design_name": design_name,
            "scores": scores,
            "prev_score": prev_score,
            "delta": delta,
            "wrote_insight": wrote_insight,
            "critical_gaps": critical_gaps,
            "suggestion_id": suggestion_id,
            "elapsed_ms": elapsed_ms,
        },
    }