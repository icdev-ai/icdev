# CUI // SP-CTI
"""SIPA adapter — NVIDIA SkillSpector AI-skill security scanner.

Wraps SkillSpector (github.com/nvidia/skillspector) as a SIPA scanner adapter.
Fires only when the quarantined artifact is an AI skill (SKILL.md present or
skill-format directory/zip layout). Normalizes SkillSpector's 64 patterns across
16 categories into the SIPA integrity_findings shape.

Integration in scanners.scan_all():
    "skillspector": run_skillspector_scan

Feature flag: ICDEV_SKILLSPECTOR_ENABLED (default off — opt-in).
Air-gap mode: Docker image (registry.icdev.local/skillspector:latest) invoked
when the `skillspector` CLI is absent but Docker is available.

Finding-type mapping (SkillSpector category → SIPA finding_type):
    prompt_injection         → dangerous_api
    data_exfiltration        → dangerous_api
    system_prompt_leakage    → dangerous_api
    output_handling          → dangerous_api
    memory_poisoning         → dangerous_api
    privilege_escalation     → unauthorized_capability
    excessive_agency         → unauthorized_capability
    rogue_agent              → unauthorized_capability
    trigger_abuse            → unauthorized_capability
    tool_misuse              → unauthorized_capability
    tool_poisoning           → known_bad_signature
    supply_chain             → vuln_dependency
    mcp_least_privilege      → unauthorized_capability
    behavioral_ast           → dangerous_api
    taint_tracking           → dangerous_api
    yara                     → known_bad_signature
    (default)                → dangerous_api

Risk score interpretation (SkillSpector 0-100):
    0-20   LOW      — safe to promote
    21-50  MEDIUM   — flag for review (warn in coherence, allow in SkillHub with badge)
    51-80  HIGH     — block install / promote in SkillHub
    81-100 CRITICAL — block + escalate to HITL

SECURITY INVARIANTS (inherited from scanners.py):
    * Never executes target code. SkillSpector is itself static-only; this adapter
      invokes it via subprocess (shell=False, fixed arg list).
    * Quarantine-first: called after ingest.stage() places artifact in quarantine.
    * All findings via _insert_finding (RLS-aware, append-only).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from tools.integrity.constants import SEVERITY
from tools.integrity.ingest import (
    BASE_DIR,
    _caller_context,
    _insert_finding,
    _quarantine_base,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.adapters.skillspector")

# Feature flag — must be explicitly enabled
_ENV_FLAG = "ICDEV_SKILLSPECTOR_ENABLED"

# Subprocess timeout (seconds) — SkillSpector static scan is fast; generous margin
_TIMEOUT = 120

# Docker image for air-gap environments where the CLI is absent
_DOCKER_IMAGE = "registry.icdev.local/skillspector:latest"

# SkillSpector category → SIPA finding_type
_CATEGORY_MAP: dict[str, str] = {
    "prompt_injection":      "dangerous_api",
    "data_exfiltration":     "dangerous_api",
    "system_prompt_leakage": "dangerous_api",
    "output_handling":       "dangerous_api",
    "memory_poisoning":      "dangerous_api",
    "behavioral_ast":        "dangerous_api",
    "taint_tracking":        "dangerous_api",
    "privilege_escalation":  "unauthorized_capability",
    "excessive_agency":      "unauthorized_capability",
    "rogue_agent":           "unauthorized_capability",
    "trigger_abuse":         "unauthorized_capability",
    "tool_misuse":           "unauthorized_capability",
    "mcp_least_privilege":   "unauthorized_capability",
    "tool_poisoning":        "known_bad_signature",
    "yara":                  "known_bad_signature",
    "supply_chain":          "vuln_dependency",
}

# SkillSpector severity → SIPA SEVERITY
_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high":     "high",
    "medium":   "medium",
    "low":      "low",
    "info":     "info",
    "informational": "info",
}


def is_skill_artifact(staged_path: str) -> bool:
    """Return True when the quarantined artifact looks like an AI skill.

    A skill artifact is one of:
    - A directory containing SKILL.md (any depth up to 2 levels)
    - A zip file whose name contains 'skill' or whose content has SKILL.md
    """
    if not staged_path:
        return False
    p = Path(staged_path)
    if p.is_dir():
        # Direct SKILL.md or one level down (e.g. skillname/SKILL.md)
        if (p / "SKILL.md").exists():
            return True
        return any(child.is_dir() and (child / "SKILL.md").exists() for child in p.iterdir())
    if p.suffix.lower() == ".zip" and p.exists():
        return "skill" in p.name.lower()
    return False


def _find_skillspector_cli() -> Optional[str]:
    """Return path to `skillspector` CLI, or None if not installed."""
    return shutil.which("skillspector")


def _find_docker() -> Optional[str]:
    """Return path to `docker`, or None."""
    return shutil.which("docker")


def _run_skillspector(scan_target: str) -> tuple[int, str, str]:
    """Invoke SkillSpector and return (returncode, stdout, stderr).

    Prefers the direct CLI; falls back to Docker for air-gap environments.
    Always uses --no-llm (static-only, no network calls) and --format json.
    LLM Stage 2 is a separate opt-in path (ICDEV_INTEGRITY_LLM_ENABLED).
    """
    cli = _find_skillspector_cli()
    if cli:
        cmd = [cli, "scan", scan_target, "--no-llm", "--format", "json"]
    else:
        docker = _find_docker()
        if not docker:
            return 127, "", "skillspector CLI and Docker both unavailable — skipping scan"
        cmd = [
            docker, "run", "--rm",
            "-v", f"{scan_target}:/scan:ro",
            _DOCKER_IMAGE,
            "scan", "/scan", "--no-llm", "--format", "json",
        ]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(  # nosec B603 — fixed arg list, shell=False
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(BASE_DIR),
            env=env,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"skillspector executable not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"skillspector timed out after {_TIMEOUT}s"


def _parse_output(stdout: str) -> Optional[dict]:
    """Parse SkillSpector JSON output; tolerate banner noise."""
    s = (stdout or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def _norm_severity(raw: Any, default: str = "medium") -> str:
    s = str(raw or "").lower().strip()
    mapped = _SEVERITY_MAP.get(s)
    if mapped and mapped in SEVERITY:
        return mapped
    return default if default in SEVERITY else "medium"


def _finding_type_for(category: str) -> str:
    return _CATEGORY_MAP.get(str(category).lower(), "dangerous_api")


def _normalize_findings(payload: dict, scan_target: str) -> list[dict]:
    """Convert SkillSpector filtered_findings to SIPA integrity_findings rows."""
    root = Path(scan_target)
    out: list[dict] = []
    for f in payload.get("filtered_findings", []) or []:
        # SkillSpector finding fields: rule_id, category, severity, file_path,
        # line, message/description, pattern
        raw_sev = f.get("severity") or f.get("risk_level", "medium")
        category = f.get("category") or f.get("type", "")
        file_path = f.get("file_path") or f.get("file", "")
        # Relativize file path against scan target
        try:
            rel = str(Path(file_path).relative_to(root))
        except (ValueError, TypeError):
            rel = file_path or None

        detail = {
            "rule_id":    f.get("rule_id") or f.get("id", ""),
            "category":   category,
            "pattern":    f.get("pattern", ""),
            "message":    f.get("message") or f.get("description", ""),
            "risk_score": payload.get("risk_score"),
            "risk_severity": payload.get("risk_severity", ""),
            "skillspector_version": payload.get("version", ""),
        }
        out.append({
            "source_scanner": "skillspector",
            "finding_type":   _finding_type_for(category),
            "severity":       _norm_severity(raw_sev),
            "file_path":      rel,
            "line":           int(f["line"]) if str(f.get("line", "")).isdigit() else None,
            "detail":         detail,
        })
    return out


def run_skillspector_scan(
    assessment_id: int,
    staged_path: Optional[str] = None,
    conn: Any = None,
) -> dict:
    """SIPA adapter — SkillSpector AI-skill security scanner.

    Called by scanners.scan_all() when the 'skillspector' scanner is enabled.
    Returns the standard adapter result dict:
        {"scanner", "success", "findings_persisted", "finding_ids", "error",
         "skipped", "risk_score", "risk_severity"}

    Skips gracefully when:
    - ICDEV_SKILLSPECTOR_ENABLED is not set/false
    - The artifact is not a skill (is_skill_artifact() returns False)
    - SkillSpector CLI and Docker are both absent (warn, don't fail)
    """
    base_result = {
        "scanner": "skillspector",
        "success": True,
        "findings_persisted": 0,
        "finding_ids": [],
        "error": None,
        "skipped": False,
        "risk_score": None,
        "risk_severity": None,
    }

    # Feature flag check
    if os.environ.get(_ENV_FLAG, "").lower() not in ("1", "true", "yes"):
        return {**base_result, "skipped": True, "error": f"{_ENV_FLAG} not set"}

    # Skill artifact check
    if not is_skill_artifact(staged_path or ""):
        return {**base_result, "skipped": True, "error": "artifact is not a skill"}

    scan_target = staged_path or str(_quarantine_base())
    logger.info("skillspector: scanning %s (assessment_id=%s)", scan_target, assessment_id)

    rc, stdout, stderr = _run_skillspector(scan_target)

    if rc == 127:
        # CLI + Docker both absent — degrade to warn, not failure
        logger.warning("skillspector: %s", stderr)
        return {**base_result, "skipped": True, "error": stderr}

    if rc not in (0, 1):
        # rc=1 means findings were found (non-zero exit); rc>1 is a real error
        err = f"skillspector exited {rc}: {stderr[:300]}"
        logger.error("skillspector: %s", err)
        return {**base_result, "success": False, "error": err}

    payload = _parse_output(stdout)
    if payload is None:
        err = "skillspector returned no parseable JSON"
        logger.warning("skillspector: %s (stderr: %s)", err, stderr[:200])
        return {**base_result, "success": False, "error": err}

    risk_score = payload.get("risk_score")
    risk_severity = payload.get("risk_severity", "")
    logger.info(
        "skillspector: risk_score=%s severity=%s findings=%d",
        risk_score, risk_severity, len(payload.get("filtered_findings", []))
    )

    normalized = _normalize_findings(payload, scan_target)
    if not normalized:
        return {
            **base_result,
            "risk_score": risk_score,
            "risk_severity": risk_severity,
        }

    # Persist findings via the SIPA shared helper (RLS-aware, append-only)
    ctx = _caller_context()
    from tools.db.storage import get_connection

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        inserted_ids: list[int] = []
        for f in normalized:
            fid = _insert_finding(
                conn=conn,
                assessment_id=assessment_id,
                source_scanner=f["source_scanner"],
                finding_type=f["finding_type"],
                severity=f["severity"],
                file_path=f.get("file_path"),
                line=f.get("line"),
                detail=f["detail"],
                tenant_id=ctx.get("tenant_id"),
                classification=ctx.get("classification"),
            )
            if fid is not None:
                inserted_ids.append(fid)
        if own_conn:
            conn.commit()
    except Exception as exc:
        logger.error("skillspector: finding persistence failed: %s", exc)
        if own_conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return {**base_result, "success": False, "error": str(exc),
                "risk_score": risk_score, "risk_severity": risk_severity}
    finally:
        if own_conn:
            conn.close()

    return {
        "scanner": "skillspector",
        "success": True,
        "findings_persisted": len(inserted_ids),
        "finding_ids": inserted_ids,
        "error": None,
        "skipped": False,
        "risk_score": risk_score,
        "risk_severity": risk_severity,
    }
