#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis cATO Monitor Reflex — 6-hour continuous compliance monitoring.

Discovers all compliance/* IQE seed queries, runs them via importlib-loaded
parser/executor, and calls poam_generator for any new violations.

Scanner-tier only (zero Claude tokens by default).  Air-gap safe.
LLM anomaly assessment is opt-in via aiify_config.yaml
(cato_monitor.llm_anomaly_detection.enabled) — aiify-rm-ff651-phase-5259.
"""
IMPLEMENTATION_STATUS = "full"

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

_IQE_DIR = BASE_DIR / "context" / "iqe" / "queries" / "compliance"
_CONFIG_PATH = BASE_DIR / "args" / "aiify_config.yaml"

# ---------------------------------------------------------------------------
# Config — cato_monitor section from args/aiify_config.yaml
# ---------------------------------------------------------------------------
_FALLBACK_CATO_CFG: Dict[str, Any] = {
    "violation_threshold": 1,
    "llm_anomaly_detection": {
        "enabled": False,
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 256,
    },
}


def _load_cato_config() -> Dict[str, Any]:
    try:
        import yaml
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and "cato_monitor" in cfg:
            return cfg["cato_monitor"]
    except Exception:
        pass
    return dict(_FALLBACK_CATO_CFG)


_cato_cfg: Dict[str, Any] = _load_cato_config()
_VIOLATION_THRESHOLD: int = int(_cato_cfg.get("violation_threshold", 1))
_llm_cfg: Dict[str, Any] = _cato_cfg.get("llm_anomaly_detection", {})
_LLM_ENABLED: bool = bool(_llm_cfg.get("enabled", False))
_LLM_MODEL: str = str(_llm_cfg.get("model", "claude-haiku-4-5-20251001"))
_LLM_MAX_TOKENS: int = int(_llm_cfg.get("max_tokens", 256))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_iqe_files() -> List[Path]:
    """Return all .iqe files under context/iqe/queries/compliance/."""
    if not _IQE_DIR.exists():
        return []
    return sorted(_IQE_DIR.rglob("*.iqe"))


def _run_iqe_query(query_file: Path, conn: Any) -> Dict[str, Any]:
    """Parse and execute one IQE file against the real compliance tables.

    The seed queries reference the ``compliance.*`` collections
    (``compliance.controls`` / ``.snapshots`` / ``.violations``). Those are
    registered on the module-level *default* Executor by
    ``tools.iqe.adapters.compliance`` and resolve to the real tables:
    ``project_controls`` LEFT JOIN ``compliance_controls`` (controls),
    ``pi_compliance_tracking`` (snapshots), and ``poam_items`` (violations).

    A previous version instantiated a *fresh* ``Executor()`` whose registry was
    empty and never imported the adapter, so every ``compliance.X`` fell through
    to the executor's bare-table fallback — which strips the namespace and runs
    ``SELECT * FROM controls`` (etc.), a relation that does not exist. That was
    the pre-existing "missing controls table" failure (bdt-mon-1). Using the
    default Executor (via ``execute_query``) with the adapter imported repoints
    the queries at the real tables; an empty table is a healthy 0-row no-op.

    Returns {name, rows, error}; a genuine execution failure is surfaced
    explicitly in ``error`` — never a silent success.
    """
    name = f"{query_file.parent.name}/{query_file.stem}"
    try:
        parser = importlib.import_module("tools.iqe.parser")
        executor_mod = importlib.import_module("tools.iqe.executor")
        # Registers compliance.* collections on the module-level default Executor.
        importlib.import_module("tools.iqe.adapters.compliance")
        text = query_file.read_text(encoding="utf-8")
        ast = parser.parse(text)
        rows = executor_mod.execute_query(ast, conn)
        return {"name": name, "rows": rows, "error": None}
    except Exception as exc:
        # On PostgreSQL a failed statement aborts the surrounding transaction, so
        # every subsequent seed query in this cycle would otherwise fail with
        # "current transaction is aborted". Roll back to de-poison the shared
        # connection (mirrors cato_twin._pull_framework_controls). The error is
        # reported explicitly in the returned dict, not swallowed.
        try:
            conn.rollback()
        except Exception:
            pass
        return {"name": name, "rows": [], "error": str(exc)}


def _call_poam_generator(project_id: str) -> Dict[str, Any]:
    """Invoke poam_generator.generate_poam(). Returns {success, path, error}."""
    try:
        gen = importlib.import_module("tools.compliance.poam_generator")
        path = gen.generate_poam(project_id)
        return {"success": True, "path": str(path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _llm_assess_anomaly(
    total_violations: int,
    scanned: int,
    query_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """LLM anomaly assessment for violation pattern (opt-in only).

    Returns {anomaly_score, anomaly_label, rationale}.
    anomaly_score 0.0 = nominal, 1.0 = severe anomaly.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        violation_summary = "\n".join(
            f"- {r['name']}: {len(r['rows'])} violation(s)"
            + (f" [ERR: {r['error']}]" if r["error"] else "")
            for r in query_results
        )
        prompt = (
            f"cATO compliance scan results: {total_violations} total violation(s) "
            f"across {scanned} IQE queries.\n\nPer-query breakdown:\n{violation_summary}\n\n"
            "Assess whether this pattern is anomalous. Return JSON only:\n"
            '{"anomaly_score": <0.0-1.0>, '
            '"anomaly_label": "nominal|elevated|anomalous|critical", '
            '"rationale": "<one sentence>"}'
        )
        req = LLMRequest(
            system_prompt=(
                "You are a compliance anomaly detector. "
                "Evaluate cATO scan results and classify the violation pattern. "
                "Return ONLY the JSON object requested."
            ),
            messages=[{"role": "user", "content": prompt}],
            model=_LLM_MODEL,
            max_tokens=_LLM_MAX_TOKENS,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if resp and resp.content:
            import json as _json
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = _json.loads(raw)
            return {
                "anomaly_score": float(data.get("anomaly_score", 0.0)),
                "anomaly_label": str(data.get("anomaly_label", "unknown")),
                "rationale": str(data.get("rationale", "")),
            }
    except Exception as exc:
        return {"anomaly_score": 0.0, "anomaly_label": "unknown", "rationale": f"LLM error: {exc}"}
    return {"anomaly_score": 0.0, "anomaly_label": "unknown", "rationale": "no response"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the cATO Monitor Reflex.

    Returns {success, scanned, violations, poam_created, metric_value, details}.
    config keys:
      project_id         — project slug (default: "sparkpilot")
      violation_threshold — override the yaml-configured threshold for this run
    """
    project_id = config.get("project_id", "sparkpilot")
    violation_threshold = int(config.get("violation_threshold", _VIOLATION_THRESHOLD))
    iqe_files = _collect_iqe_files()

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "scanned": 0,
            "violations": 0,
            "poam_created": 0,
            "metric_value": 0.0,
            "error": f"DB connection failed: {exc}",
        }

    query_results: List[Dict[str, Any]] = []
    total_violations = 0
    try:
        for qf in iqe_files:
            result = _run_iqe_query(qf, conn)
            query_results.append(result)
            total_violations += len(result["rows"])
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # POAM generation triggered when violations meet or exceed the configurable threshold
    poam_created = 0
    poam_detail: Dict[str, Any] = {}
    if total_violations >= violation_threshold:
        poam_detail = _call_poam_generator(project_id)
        if poam_detail.get("success"):
            poam_created = 1

    # Optional LLM anomaly assessment (zero Claude tokens when disabled)
    anomaly: Optional[Dict[str, Any]] = None
    if _LLM_ENABLED and total_violations > 0:
        anomaly = _llm_assess_anomaly(total_violations, len(iqe_files), query_results)

    detail_block: Dict[str, Any] = {
        "project_id": project_id,
        "violation_threshold": violation_threshold,
        "queries_run": len(iqe_files),
        "queries_failed": sum(1 for r in query_results if r["error"]),
        "query_results": [
            {
                "name": r["name"],
                "violations": len(r["rows"]),
                "error": r["error"],
            }
            for r in query_results
        ],
        "poam": poam_detail,
        "timestamp": _utcnow_iso(),
    }
    if anomaly is not None:
        detail_block["anomaly"] = anomaly

    return {
        "success": True,
        "scanned": len(iqe_files),
        "violations": total_violations,
        "poam_created": poam_created,
        "metric_value": float(total_violations),
        "details": detail_block,
    }
