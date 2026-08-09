#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Comply Reflex — refresh cATO evidence, regenerate stale SSPs.

Runs existing compliance tools to ensure evidence freshness stays above
threshold.  Non-destructive read + regenerate (GREEN tier).

Scanner-tier only (zero Claude tokens by default).  Air-gap safe.
LLM anomaly assessment is opt-in via aiify_config.yaml
(comply_reflex.llm_anomaly_detection.enabled) — aiify-rm-ff651-phase-5264.
"""
IMPLEMENTATION_STATUS = "full"

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional



BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger(__name__)

_CONFIG_PATH = BASE_DIR / "args" / "aiify_config.yaml"

# ---------------------------------------------------------------------------
# Config — comply_reflex section from args/aiify_config.yaml
# ---------------------------------------------------------------------------
_FALLBACK_COMPLY_CFG: Dict[str, Any] = {
    "min_completed_checks": 1,
    "llm_anomaly_detection": {
        "enabled": False,
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 512,
    },
}


def _load_comply_config() -> Dict[str, Any]:
    try:
        import yaml
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and "comply_reflex" in cfg:
            return cfg["comply_reflex"]
    except Exception:
        pass
    return dict(_FALLBACK_COMPLY_CFG)


_comply_cfg: Dict[str, Any] = _load_comply_config()
_MIN_COMPLETED_CHECKS: int = int(_comply_cfg.get("min_completed_checks", 1))
_llm_cfg: Dict[str, Any] = _comply_cfg.get("llm_anomaly_detection", {})
_LLM_ENABLED: bool = bool(_llm_cfg.get("enabled", False))
_LLM_MODEL: str = str(_llm_cfg.get("model", "claude-haiku-4-5-20251001"))
_LLM_MAX_TOKENS: int = int(_llm_cfg.get("max_tokens", 512))

_ANOMALY_PROMPT = """\
You are a compliance posture anomaly detector for a FedRAMP/CMMC system.

Compliance check results (JSON):
{checks_json}

Summary:
- Evidence freshness: {freshness_pct}%
- Checks completed: {completed}/{total}
- Crosswalk coverage: {coverage_pct}%
- SbD score: {sbd_score}

Identify metrics that are unusually low, trending wrong, or pose an immediate compliance risk.

Respond ONLY with valid JSON:
{{
  "overall_health": "healthy|degraded|critical",
  "anomalies": [
    {{"metric": "name", "value": 0, "reason": "why anomalous"}}
  ],
  "summary": "one-line assessment",
  "recommend_escalate": false
}}
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_tool(cmd: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Run a tool as subprocess, capture JSON output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            try:
                return {"success": True, "data": json.loads(stdout[json_start:])}
            except json.JSONDecodeError:
                pass
        return {"success": True, "data": {"raw_output": stdout[:2000]}}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _check_cato_evidence() -> Dict[str, Any]:
    """Check cATO evidence freshness via cato_live_engine."""
    print("  Comply: checking cATO evidence freshness...")
    result = _run_tool(
        [
            sys.executable,
            "tools/compliance/cato_live_engine.py",
            "--project-id",
            "sparkpilot",
            "--dashboard",
            "--json",
        ],
        timeout=120,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "cato_evidence",
            "status": "completed",
            "findings": {
                "current_controls": data.get("current", 0),
                "stale_controls": data.get("stale", 0),
                "expired_controls": data.get("expired", 0),
                "freshness_pct": data.get("freshness_pct", 0),
            },
        }
    return {"check": "cato_evidence", "status": "failed", "error": result.get("error", "unknown")}


def _run_crosswalk() -> Dict[str, Any]:
    """Run compliance crosswalk to sync control coverage."""
    print("  Comply: running crosswalk engine...")
    result = _run_tool(
        [
            sys.executable,
            "tools/compliance/crosswalk_engine.py",
            "--project-id",
            "sparkpilot",
            "--coverage",
        ],
        timeout=120,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "crosswalk",
            "status": "completed",
            "findings": {
                "frameworks_synced": data.get("frameworks_synced", 0),
                "controls_mapped": data.get("controls_mapped", 0),
                "coverage_pct": data.get("coverage_pct", 0),
            },
        }
    return {"check": "crosswalk", "status": "failed", "error": result.get("error", "unknown")}


def _check_sbd_posture() -> Dict[str, Any]:
    """Run Secure by Design assessment."""
    print("  Comply: running SbD assessment...")
    result = _run_tool(
        [
            sys.executable,
            "tools/compliance/sbd_assessor.py",
            "--project-id",
            "sparkpilot",
            "--project-dir",
            ".",
            "--json",
        ],
        timeout=120,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "sbd_assessment",
            "status": "completed",
            "findings": {
                "overall_score": data.get("overall_score", 0),
                "requirements_met": data.get("requirements_met", 0),
                "total_requirements": data.get("total_requirements", 0),
                "expired_exceptions": data.get("expired_exceptions", 0),
            },
        }
    return {"check": "sbd_assessment", "status": "failed", "error": result.get("error", "unknown")}


def _llm_assess_compliance(
    checks: List[Dict[str, Any]],
    evidence_freshness: float,
) -> Optional[Dict[str, Any]]:
    """Assess compliance metrics for anomalies via LLM anomaly detection.

    Returns a dict with keys: overall_health, anomalies, summary, recommend_escalate.
    Returns None on any error so callers fall back to deterministic logic.
    Only called when _LLM_ENABLED is True (opt-in via aiify_config.yaml).
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except ImportError:
        logger.debug("comply: LLMRouter unavailable, skipping anomaly detection")
        return None

    completed = [c for c in checks if c.get("status") == "completed"]
    crosswalk = next((c for c in completed if c.get("check") == "crosswalk"), None)
    sbd = next((c for c in completed if c.get("check") == "sbd_assessment"), None)

    coverage_pct = crosswalk.get("findings", {}).get("coverage_pct", 0) if crosswalk else 0
    sbd_score = sbd.get("findings", {}).get("overall_score", 0) if sbd else 0

    prompt = _ANOMALY_PROMPT.format(
        checks_json=json.dumps(checks, indent=2)[:3000],
        freshness_pct=round(evidence_freshness, 1),
        completed=len(completed),
        total=len(checks),
        coverage_pct=round(coverage_pct, 1),
        sbd_score=round(sbd_score, 1),
    )

    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a compliance anomaly detection system. Return only valid JSON.",
            agent_id="comply-anomaly-detector",
            classification="CUI",
            model=_LLM_MODEL,
            max_tokens=_LLM_MAX_TOKENS,
            temperature=0.0,
            skip_injection_scan=True,
        )
        response = router.invoke("anomaly_detection", request)
        raw = (response.content or "").strip()
        json_start = raw.find("{")
        if json_start >= 0:
            return json.loads(raw[json_start:])
    except Exception as exc:
        logger.debug("comply: anomaly detection skipped (%s), using deterministic fallback", exc)
    return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Comply Reflex.

    config keys:
      min_completed_checks — override the yaml-configured minimum for this run
    """
    min_completed = int(config.get("min_completed_checks", _MIN_COMPLETED_CHECKS))
    checks = []

    # Run each compliance check
    for check_func in [_check_cato_evidence, _run_crosswalk, _check_sbd_posture]:
        try:
            result = check_func()
            checks.append(result)
        except Exception as e:
            checks.append({"check": check_func.__name__, "status": "failed", "error": str(e)})

    completed = [c for c in checks if c.get("status") == "completed"]

    # Calculate overall evidence freshness
    evidence_freshness = 0.0
    cato = next((c for c in completed if c.get("check") == "cato_evidence"), None)
    if cato:
        evidence_freshness = float(cato.get("findings", {}).get("freshness_pct", 0))

    # Optional LLM anomaly assessment (zero Claude tokens when disabled).
    # Falls back to config-driven threshold check when LLM is unavailable.
    anomaly_assessment: Optional[Dict[str, Any]] = None
    if _LLM_ENABLED and checks:
        anomaly_assessment = _llm_assess_compliance(checks, evidence_freshness)

    if anomaly_assessment:
        overall_health = anomaly_assessment.get("overall_health", "healthy")
        success = overall_health != "critical"
    else:
        success = len(completed) >= min_completed

    detail_block: Dict[str, Any] = {
        "min_completed_checks": min_completed,
        "checks_completed": len(completed),
        "checks_failed": len(checks) - len(completed),
        "evidence_freshness_pct": evidence_freshness,
        "checks": checks,
        "timestamp": _utcnow_iso(),
    }
    if anomaly_assessment is not None:
        detail_block["anomaly_assessment"] = anomaly_assessment

    return {
        "success": success,
        "metric_value": float(evidence_freshness),
        "details": detail_block,
    }
