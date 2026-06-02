#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — AADC Compliance Scoring (aadc_compliance).

Triggered by Genesis on aadc_design_events where
event_type IN ('assess', 'simulation_run').

Cycle:
  load      → fetch latest design snapshot from aadc_designs
  score     → compute NIST AI RMF / OWASP LLM / MITRE ATLAS node-coverage scores
  threshold → compute adaptive anomaly thresholds from historical assessment data
              (z-score based, parameterised by genesis_config.yaml anomaly_detection stanza)
  memory    → write insight if overall score changed beyond the adaptive delta threshold
  suggest   → create Kanban chore suggestion if any framework falls below adaptive threshold

COOLDOWN_HOURS = 4 (enforced by Genesis daemon).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import os
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
# Config loader — thresholds sourced from genesis_config.yaml (anomaly_detection)
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "critical_threshold": 40.0,
    "memory_delta_threshold": 5.0,
    "anomaly_detection": {
        "enabled": True,
        "min_samples": 5,
        "z_score": 2.0,
        "fallback_to_static": True,
        "adaptive_bounds": {
            "critical_threshold_floor": 20.0,
            "critical_threshold_ceiling": 60.0,
            "memory_delta_floor": 2.0,
            "memory_delta_ceiling": 15.0,
        },
    },
}


def _load_reflex_config() -> dict:
    """Return the aadc_compliance stanza from genesis_config.yaml (or defaults)."""
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    if not config_path.exists():
        return _CONFIG_DEFAULTS.copy()
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            root = yaml.safe_load(f) or {}
        cfg = root.get("reflexes", {}).get("aadc_compliance", {})
        return cfg if cfg else _CONFIG_DEFAULTS.copy()
    except Exception:
        return _CONFIG_DEFAULTS.copy()


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

# Config-loaded thresholds — sourced from genesis_config.yaml::reflexes.aadc_compliance.
# Python constants below are last-resort fallbacks only (config unavailable at import time).
_reflex_cfg = _load_reflex_config()
_CRITICAL_THRESHOLD: float = float(_reflex_cfg.get("critical_threshold", 40.0))
_DELTA_THRESHOLD: float = float(_reflex_cfg.get("memory_delta_threshold", 5.0))
_anomaly_cfg: dict = _reflex_cfg.get("anomaly_detection") or {}
_ANOMALY_ENABLED: bool = bool(_anomaly_cfg.get("enabled", True))
_FALLBACK_TO_STATIC: bool = bool(_anomaly_cfg.get("fallback_to_static", True))
_MIN_HISTORY_SAMPLES: int = int(_anomaly_cfg.get("min_samples", 5))
_Z_SCORE: float = float(_anomaly_cfg.get("z_score", 2.0))
_ADAPTIVE_BOUNDS: dict = _anomaly_cfg.get("adaptive_bounds") or {}
_CRITICAL_FLOOR: float = float(_ADAPTIVE_BOUNDS.get("critical_threshold_floor", 20.0))
_CRITICAL_CEILING: float = float(_ADAPTIVE_BOUNDS.get("critical_threshold_ceiling", 60.0))
_DELTA_FLOOR: float = float(_ADAPTIVE_BOUNDS.get("memory_delta_floor", 2.0))
_DELTA_CEILING: float = float(_ADAPTIVE_BOUNDS.get("memory_delta_ceiling", 15.0))


# ---------------------------------------------------------------------------
# Anomaly detection — adaptive threshold computation
# ---------------------------------------------------------------------------

def _adaptive_low_threshold(values: List[float], fallback: float) -> float:
    """Z-score-based lower anomaly threshold for framework coverage scores.

    Threshold = mean - z_score * std, clamped to [critical_threshold_floor, critical_threshold_ceiling].
    All parameters sourced from genesis_config.yaml::reflexes.aadc_compliance.anomaly_detection.
    Returns `fallback` when anomaly_detection is disabled, insufficient samples, or fallback_to_static is set.
    """
    if not _ANOMALY_ENABLED or len(values) < _MIN_HISTORY_SAMPLES:
        return fallback
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    adaptive = mean - _Z_SCORE * std
    if _FALLBACK_TO_STATIC and adaptive >= fallback:
        return fallback
    return round(max(_CRITICAL_FLOOR, min(_CRITICAL_CEILING, adaptive)), 1)


def _adaptive_delta_threshold(abs_deltas: List[float], fallback: float) -> float:
    """Z-score-based upper bound on historical absolute score deltas.

    Threshold = mean + z_score * std, clamped to [memory_delta_floor, memory_delta_ceiling].
    All parameters sourced from genesis_config.yaml::reflexes.aadc_compliance.anomaly_detection.
    Returns `fallback` when anomaly_detection is disabled or sample size is too small.
    """
    if not _ANOMALY_ENABLED or len(abs_deltas) < _MIN_HISTORY_SAMPLES:
        return fallback
    mean = sum(abs_deltas) / len(abs_deltas)
    variance = sum((d - mean) ** 2 for d in abs_deltas) / len(abs_deltas)
    std = variance ** 0.5
    adaptive = mean + _Z_SCORE * std
    return round(max(_DELTA_FLOOR, min(_DELTA_CEILING, adaptive)), 1)


def _load_threshold_history() -> tuple:
    """Fetch recent per-framework scores and consecutive deltas for anomaly detection.

    Returns (nist_scores, owasp_scores, abs_deltas) — all lists of floats.
    Any DB failure returns three empty lists (triggering fallback thresholds).
    """
    nist_scores: List[float] = []
    owasp_scores: List[float] = []
    abs_deltas: List[float] = []
    try:
        conn = _aadc_connection()
        rows = conn.execute(
            "SELECT nist_rmf_score, owasp_score, score "
            "FROM aadc_assessments ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        try:
            conn.close()
        except Exception:
            pass
        overall_scores = [float(r[2]) for r in rows if r[2] is not None]
        # Consecutive absolute deltas (newest-first ordering means i vs i+1 is adjacent pair)
        abs_deltas = [
            abs(overall_scores[i] - overall_scores[i + 1])
            for i in range(len(overall_scores) - 1)
        ]
        nist_scores = [float(r[0]) for r in rows if r[0] is not None]
        owasp_scores = [float(r[1]) for r in rows if r[1] is not None]
    except Exception:
        pass
    return nist_scores, owasp_scores, abs_deltas


# ---------------------------------------------------------------------------
# LLM-based anomaly threshold inference (sparse-data fallback)
# ---------------------------------------------------------------------------

_AADC_LLM_MODEL: str = os.environ.get("AADC_LLM_MODEL", "claude-haiku-4-5-20251001")

_LLM_ANOMALY_SYSTEM_PROMPT = (
    "You are an AI compliance analyst. Given AADC design compliance scores and bounds, "
    "infer appropriate anomaly detection thresholds. "
    "Return ONLY valid JSON with exactly two keys: "
    "'critical_threshold' (float 0-100, minimum acceptable framework coverage %) "
    "and 'delta_threshold' (float 0-20, minimum score change to record a memory insight). "
    "Be conservative: err toward flagging gaps rather than missing them."
)


def _llm_infer_thresholds(
    scores: Dict[str, float], design_name: str
) -> Optional[Dict[str, float]]:
    """Use Claude Haiku to infer anomaly thresholds when historical data is sparse.

    Returns {'critical_threshold': float, 'delta_threshold': float} with bounds applied,
    or None on any failure (caller keeps the static fallback).
    """
    if not _ANOMALY_ENABLED:
        return None
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore

        user_content = json.dumps({
            "design_name": design_name,
            "current_scores": scores,
            "static_critical_threshold": _CRITICAL_THRESHOLD,
            "static_delta_threshold": _DELTA_THRESHOLD,
            "bounds": {
                "critical_floor": _CRITICAL_FLOOR,
                "critical_ceiling": _CRITICAL_CEILING,
                "delta_floor": _DELTA_FLOOR,
                "delta_ceiling": _DELTA_CEILING,
            },
        }, indent=2)

        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_LLM_ANOMALY_SYSTEM_PROMPT,
            model=_AADC_LLM_MODEL,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        response = LLMRouter().invoke("aadc_anomaly_detection", request)
        if not (response and response.content):
            return None
        data = json.loads(response.content.strip())
        ct = float(data["critical_threshold"])
        dt = float(data["delta_threshold"])
        return {
            "critical_threshold": round(max(_CRITICAL_FLOOR, min(_CRITICAL_CEILING, ct)), 1),
            "delta_threshold": round(max(_DELTA_FLOOR, min(_DELTA_CEILING, dt)), 1),
        }
    except Exception as exc:
        print(f"  [aadc_compliance] LLM threshold inference failed, using static fallback: {exc}")
        return None


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


def _create_kanban_suggestion(
    design_name: str, gaps: List[str], threshold: float
) -> Optional[str]:
    """Insert a Kanban chore suggestion for critical compliance gaps."""
    if get_connection is None:
        print("  [aadc_compliance] WARNING: get_connection unavailable — skipping Kanban suggestion")
        return None

    task_id = f"aadc-gap-{_uid()}"
    gap_str = ", ".join(gaps)
    title = f"[AADC] Fix critical compliance gaps in '{design_name}'"
    description = (
        f"Genesis aadc_compliance reflex detected critical framework gaps "
        f"(below adaptive threshold {threshold:.0f}%) "
        f"in design '{design_name}'. "
        f"Frameworks below threshold: {gap_str}. "
        f"Add the required node types to bring all frameworks to ≥ {threshold:.0f}% coverage. "
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
    # Phase 2b — derive adaptive anomaly thresholds from history
    # ------------------------------------------------------------------
    nist_history, owasp_history, delta_history = _load_threshold_history()
    combined_fw = nist_history + owasp_history
    critical_threshold = _adaptive_low_threshold(combined_fw, _CRITICAL_THRESHOLD)
    delta_threshold = _adaptive_delta_threshold(delta_history, _DELTA_THRESHOLD)
    threshold_source = "z-score"

    # When insufficient history forces a static fallback, try LLM inference instead.
    sparse_fw = len(combined_fw) < _MIN_HISTORY_SAMPLES
    sparse_delta = len(delta_history) < _MIN_HISTORY_SAMPLES
    if sparse_fw or sparse_delta:
        llm_thresholds = _llm_infer_thresholds(scores, design_name)
        if llm_thresholds:
            if sparse_fw:
                critical_threshold = llm_thresholds["critical_threshold"]
            if sparse_delta:
                delta_threshold = llm_thresholds["delta_threshold"]
            threshold_source = "llm"

    print(
        f"  [aadc_compliance] adaptive thresholds [{threshold_source}]"
        f"  critical={critical_threshold}%"
        f"  delta={delta_threshold} pts"
        f"  (n_fw={len(combined_fw)}, n_delta={len(delta_history)})"
    )

    # ------------------------------------------------------------------
    # Phase 3 — compare to previous; write memory insight if delta exceeds adaptive threshold
    # ------------------------------------------------------------------
    prev_score = _get_previous_score(design_id)
    wrote_insight = False
    delta = 0.0

    if prev_score is not None:
        delta = scores["overall_score"] - prev_score
        if abs(delta) >= delta_threshold:
            print(f"  [aadc_compliance] score delta={delta:+.1f} (threshold={delta_threshold}) — writing memory insight")
            _write_memory_insight(design_name, scores, delta)
            wrote_insight = True
    else:
        # First run for this design — record the baseline
        _write_memory_insight(design_name, scores, 0.0)
        wrote_insight = True

    _write_assessment(design_id, scores)

    # ------------------------------------------------------------------
    # Phase 4 — detect critical gaps using adaptive threshold; create Kanban suggestion
    # ------------------------------------------------------------------
    critical_gaps: List[str] = []
    if scores["nist_rmf_score"] < critical_threshold:
        critical_gaps.append(f"NIST AI RMF ({scores['nist_rmf_score']:.0f}%)")
    if scores["owasp_score"] < critical_threshold:
        critical_gaps.append(f"OWASP LLM ({scores['owasp_score']:.0f}%)")
    if scores["atlas_score"] < critical_threshold:
        critical_gaps.append(f"MITRE ATLAS ({scores['atlas_score']:.0f}%)")

    suggestion_id: Optional[str] = None
    if critical_gaps:
        print(f"  [aadc_compliance] critical gaps detected (threshold={critical_threshold}%): {critical_gaps}")
        suggestion_id = _create_kanban_suggestion(design_name, critical_gaps, critical_threshold)
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
            "adaptive_thresholds": {
                "critical": critical_threshold,
                "delta": delta_threshold,
                "n_fw_samples": len(combined_fw),
                "n_delta_samples": len(delta_history),
                "source": threshold_source,
            },
            "elapsed_ms": elapsed_ms,
        },
    }
