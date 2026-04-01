# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Intelligent Security Agent.

Auto-triggers security assessments when:
1. NDC topology saved → import → STRIDE → score posture
2. IaC generated → scan for misconfigurations
3. Pipeline saved → assess CI/CD security
4. SDC design saved → auto-assess

All operations are deterministic (no LLM). Pure Python with SQLite.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("icdev.security_canvas.agent")


# ── IaC Security Scan Rules ──────────────────────────────────────────────────
_IAC_CHECKS = [
    ("0.0.0.0/0", "IaC-001",
     "Overly permissive CIDR (0.0.0.0/0) detected", "CAT1"),
    ("public-read", "IaC-002",
     "Public read access enabled on storage", "CAT1"),
    ("encrypted = false", "IaC-003",
     "Encryption explicitly disabled", "CAT1"),
    ("encrypted  = false", "IaC-003",
     "Encryption explicitly disabled", "CAT1"),
    ("skip_final_snapshot = true", "IaC-004",
     "Database final snapshot disabled", "CAT2"),
    ("enable_logging = false", "IaC-005",
     "Logging explicitly disabled", "CAT2"),
    ("password", "IaC-006",
     "Potential hardcoded password detected", "CAT2"),
    ("secret_key", "IaC-007",
     "Potential hardcoded secret key", "CAT1"),
    ("multi_az = false", "IaC-008",
     "Multi-AZ disabled — single point of failure", "CAT3"),
    ("versioning { enabled = false", "IaC-009",
     "S3 versioning disabled — no rollback", "CAT2"),
]


def on_ndc_topology_saved(topology_id: str) -> dict:
    """Called after an NDC topology is saved.

    Imports the topology into the Security Design Canvas as a security
    design, then runs an automatic STRIDE assessment and persists results.
    """
    try:
        from tools.security_canvas.bridge import import_ndc_topology
        from tools.security_canvas.db.init_db import get_connection
        from tools.security_canvas.security_engine import run_security_assessment

        # Import or update SDC design from NDC
        result = import_ndc_topology(topology_id)
        design_id = result.get("design_id")
        if not design_id:
            return {"status": "skipped", "reason": "No design created"}

        # Run assessment
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=?",
                (design_id,),
            ).fetchone()
            if not row:
                return {"status": "skipped", "reason": "No graph data"}

            graph = (
                json.loads(row[0]) if isinstance(row[0], str) else row[0]
            )
            assessment = run_security_assessment(design_id, graph)

            # Persist assessment
            assess_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (assess_id, design_id, "auto_stride", "ndc_save",
                 topology_id,
                 assessment.get("total_threats", 0),
                 assessment.get("total_controls", 0),
                 assessment.get("risk_score", 0),
                 assessment.get("posture_grade", "F"),
                 json.dumps(assessment.get("findings", [])),
                 json.dumps(assessment.get("recommendations", [])),
                 now),
            )

        logger.info(
            "Security agent: NDC %s → SDC %s assessed (score=%s, grade=%s)",
            topology_id, design_id,
            assessment.get("risk_score"),
            assessment.get("posture_grade"),
        )
        return {
            "status": "assessed",
            "design_id": design_id,
            "assessment_id": assess_id,
            "risk_score": assessment.get("risk_score"),
            "posture_grade": assessment.get("posture_grade"),
        }
    except Exception as exc:
        logger.warning("Security agent error on NDC save: %s", exc)
        return {"status": "error", "error": str(exc)}


def on_iac_generated(
    topology_id: str,
    iac_content: str,
    iac_type: str = "terraform",
) -> dict:
    """Called after IaC is generated. Scans for security misconfigurations.

    Performs deterministic line-by-line pattern matching against known
    insecure configuration patterns. Returns structured findings with
    DISA STIG severity categories (CAT1/CAT2/CAT3).
    """
    findings = []
    lines = iac_content.split("\n") if isinstance(iac_content, str) else []

    for i, line in enumerate(lines, 1):
        lower = line.lower().strip()
        # Skip comments
        if lower.startswith("#"):
            continue
        for pattern, rule_id, title, severity in _IAC_CHECKS:
            if pattern in lower:
                findings.append({
                    "rule_id": rule_id,
                    "title": title,
                    "severity": severity,
                    "line": i,
                    "content": line.strip()[:120],
                    "iac_type": iac_type,
                })

    return {
        "status": "scanned",
        "topology_id": topology_id,
        "iac_type": iac_type,
        "total_findings": len(findings),
        "findings": findings,
        "cat1_count": sum(1 for f in findings if f["severity"] == "CAT1"),
        "cat2_count": sum(1 for f in findings if f["severity"] == "CAT2"),
        "cat3_count": sum(1 for f in findings if f["severity"] == "CAT3"),
    }


def on_pipeline_saved(
    pipeline_id: str,
    pipeline_config: dict,
) -> dict:
    """Called after a CI/CD pipeline is saved. Assesses pipeline security.

    Checks for common CI/CD security issues: missing secret scanning,
    unsigned artifacts, overly permissive permissions, etc.
    """
    findings = []
    stages = pipeline_config.get("stages", [])
    jobs = pipeline_config.get("jobs", {})

    # Check: no secret scanning stage
    stage_names = [s if isinstance(s, str) else s.get("name", "")
                   for s in stages]
    has_secret_scan = any(
        "secret" in s.lower() or "detect-secrets" in s.lower()
        for s in stage_names
    )
    if not has_secret_scan:
        findings.append({
            "rule_id": "PIPE-001",
            "title": "No secret scanning stage detected",
            "severity": "CAT2",
            "category": "supply_chain",
        })

    # Check: no SAST stage
    has_sast = any(
        "sast" in s.lower() or "security" in s.lower()
        for s in stage_names
    )
    if not has_sast:
        findings.append({
            "rule_id": "PIPE-002",
            "title": "No SAST/security scanning stage",
            "severity": "CAT2",
            "category": "supply_chain",
        })

    # Check: no container scanning
    has_container_scan = any(
        "container" in s.lower() and "scan" in s.lower()
        for s in stage_names
    )
    if not has_container_scan and any(
        "docker" in str(jobs).lower() or "container" in str(jobs).lower()
        for _ in [1]
    ):
        findings.append({
            "rule_id": "PIPE-003",
            "title": "Container builds without container scanning",
            "severity": "CAT2",
            "category": "supply_chain",
        })

    # Check individual jobs for issues
    for job_name, job_cfg in jobs.items():
        if isinstance(job_cfg, dict):
            # Overly permissive permissions
            if job_cfg.get("allow_failure") is True:
                findings.append({
                    "rule_id": "PIPE-004",
                    "title": f"Job '{job_name}' allows failure — may mask "
                             f"security issues",
                    "severity": "CAT3",
                    "category": "misconfiguration",
                })
            # Privileged mode
            if job_cfg.get("privileged") is True:
                findings.append({
                    "rule_id": "PIPE-005",
                    "title": f"Job '{job_name}' runs in privileged mode",
                    "severity": "CAT1",
                    "category": "elevation_of_privilege",
                })

    return {
        "status": "scanned",
        "pipeline_id": pipeline_id,
        "total_findings": len(findings),
        "findings": findings,
        "cat1_count": sum(1 for f in findings if f["severity"] == "CAT1"),
        "cat2_count": sum(1 for f in findings if f["severity"] == "CAT2"),
        "cat3_count": sum(1 for f in findings if f["severity"] == "CAT3"),
    }


def on_pdc_pipeline_saved(
    pipeline_id: str,
    pipeline_config: dict,
) -> dict:
    """Called after a PDC pipeline is saved. Runs security checks and persists
    findings to sc_assessments (design_id=NULL — pipeline-level assessment).

    Checks for CI/CD security issues then stores a record so the posture view
    can surface CAT1 findings from pipeline saves alongside SDC design grades.
    """
    # Run pipeline security checks (reuse existing logic)
    scan = on_pipeline_saved(pipeline_id, pipeline_config)
    findings = scan.get("findings", [])

    try:
        from tools.security_canvas.db.init_db import get_connection

        assess_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        risk_score = float(scan.get("cat1_count", 0) * 5 +
                          scan.get("cat2_count", 0) * 2 +
                          scan.get("cat3_count", 0))
        if risk_score >= 20:
            grade = "F"
        elif risk_score >= 15:
            grade = "D"
        elif risk_score >= 10:
            grade = "C"
        elif risk_score >= 5:
            grade = "B"
        else:
            grade = "A"

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (assess_id, None, "pipeline_scan", "pdc_save",
                 pipeline_id,
                 scan.get("cat1_count", 0) + scan.get("cat2_count", 0) + scan.get("cat3_count", 0),
                 0,
                 risk_score,
                 grade,
                 json.dumps(findings),
                 json.dumps([]),
                 now),
            )

        logger.info(
            "Security agent: PDC %s assessed (cat1=%s, score=%s, grade=%s)",
            pipeline_id, scan.get("cat1_count", 0), risk_score, grade,
        )
        scan["assessment_id"] = assess_id
        scan["risk_score"] = risk_score
        scan["posture_grade"] = grade
    except Exception as exc:
        logger.warning("Security agent error on PDC save: %s", exc)
        scan["error"] = str(exc)

    return scan


def auto_assess(design_id: str, trigger_source: str = "auto") -> dict:
    """Core assessment orchestrator. Called by blueprint on design save.

    Skips assessment if the design has fewer than 2 nodes (not enough
    topology to meaningfully assess).
    """
    try:
        from tools.security_canvas.db.init_db import get_connection
        from tools.security_canvas.security_engine import run_security_assessment

        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=?",
                (design_id,),
            ).fetchone()
            if not row:
                return {"status": "skipped", "reason": "Design not found"}

            graph = (
                json.loads(row[0]) if isinstance(row[0], str) else row[0]
            )
            nodes = graph.get("nodes", [])
            if len(nodes) < 2:
                return {
                    "status": "skipped",
                    "reason": "Design has fewer than 2 nodes",
                }

            assessment = run_security_assessment(design_id, graph)
            assess_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "total_threats, total_controls, risk_score, posture_grade, "
                "findings_json, recommendations_json, ran_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (assess_id, design_id, "auto", trigger_source,
                 assessment.get("total_threats", 0),
                 assessment.get("total_controls", 0),
                 assessment.get("risk_score", 0),
                 assessment.get("posture_grade", "F"),
                 json.dumps(assessment.get("findings", [])),
                 json.dumps(assessment.get("recommendations", [])),
                 now),
            )

        return {
            "status": "assessed",
            "assessment_id": assess_id,
            "risk_score": assessment.get("risk_score"),
            "posture_grade": assessment.get("posture_grade"),
        }
    except Exception as exc:
        logger.warning("Auto-assess error: %s", exc)
        return {"status": "error", "error": str(exc)}


# ── IaC File / Directory Scanning ──────────────────────────────────────────

_IAC_EXT_MAP = {
    ".tf": "terraform",
    ".yaml": "cloudformation",
    ".yml": "cloudformation",
    ".json": "cloudformation",
}


def scan_iac_file(file_path: str) -> dict:
    """Scan a single IaC file for security misconfigurations.

    Reads file content, detects IaC type from extension, runs the
    deterministic IaC scanner, and returns enhanced results.
    """
    fp = Path(file_path)
    if not fp.is_file():
        return {"status": "error", "error": f"File not found: {file_path}"}

    ext = fp.suffix.lower()
    iac_type = _IAC_EXT_MAP.get(ext)
    if not iac_type:
        return {
            "status": "skipped",
            "reason": f"Unsupported extension: {ext}",
            "file_path": file_path,
        }

    content = fp.read_text(encoding="utf-8", errors="replace")
    result = on_iac_generated("file-scan", content, iac_type)

    result["file_path"] = file_path
    result["file_size"] = fp.stat().st_size
    result["scanned_at"] = datetime.now(timezone.utc).isoformat()
    return result


def scan_iac_directory(directory_path: str) -> dict:
    """Walk a directory for IaC files and scan each one.

    Aggregates findings across all files and returns a summary with
    per-file results and severity breakdown.
    """
    dp = Path(directory_path)
    if not dp.is_dir():
        return {"status": "error", "error": f"Directory not found: {directory_path}"}

    supported_exts = set(_IAC_EXT_MAP.keys())
    file_results = []
    total_findings = 0
    findings_by_severity = {"CAT1": 0, "CAT2": 0, "CAT3": 0}

    for root, _dirs, files in os.walk(dp):
        for fname in sorted(files):
            ext = Path(fname).suffix.lower()
            if ext not in supported_exts:
                continue
            fpath = os.path.join(root, fname)
            result = scan_iac_file(fpath)
            file_results.append(result)
            total_findings += result.get("total_findings", 0)
            findings_by_severity["CAT1"] += result.get("cat1_count", 0)
            findings_by_severity["CAT2"] += result.get("cat2_count", 0)
            findings_by_severity["CAT3"] += result.get("cat3_count", 0)

    return {
        "directory": directory_path,
        "files_scanned": len(file_results),
        "total_findings": total_findings,
        "findings_by_severity": findings_by_severity,
        "file_results": file_results,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


# ── LLM-Assisted Threat Identification ──────────────────────────────────────


def llm_identify_threats(graph_data: dict) -> dict:
    """Identify security threats using Ollama LLM with STRIDE framework.

    Builds a text description of the design from *graph_data*, sends it
    to the local Ollama instance (qwen3:1.7b) for STRIDE analysis, and
    returns structured threat results.  Falls back to deterministic
    :func:`run_stride_analysis` if Ollama is unavailable.

    Args:
        graph_data: Design graph dict with nodes, edges, boundaries.

    Returns:
        Dict with threats list, source indicator, model name, count,
        and optional error message.
    """
    import re
    import urllib.request

    # ── Build text description of the design ──────────────────────────────
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])

    lines = []
    if nodes:
        lines.append("Components:")
        for n in nodes:
            lines.append(
                f"  - {n.get('label', n.get('id', '?'))} "
                f"(type: {n.get('type', 'unknown')})"
            )
    if edges:
        label_map = {n.get("id", ""): n.get("label", n.get("id", "")) for n in nodes}
        lines.append("Data flows:")
        for e in edges:
            src = label_map.get(e.get("source", ""), e.get("source", ""))
            tgt = label_map.get(e.get("target", ""), e.get("target", ""))
            proto = e.get("protocol", "")
            enc = "encrypted" if e.get("encrypted") else "unencrypted"
            auth = "authenticated" if e.get("authenticated") else "unauthenticated"
            lines.append(f"  - {src} -> {tgt} ({proto}, {enc}, {auth})")
    if boundaries:
        lines.append("Trust boundaries:")
        for b in boundaries:
            lines.append(
                f"  - {b.get('label', b.get('id', '?'))} "
                f"(type: {b.get('boundary_type', 'network')}, "
                f"IL: {b.get('il_level', '?')})"
            )

    description = "\n".join(lines) if lines else "Empty design with no components."

    # ── Try Ollama LLM ────────────────────────────────────────────────────
    model = "qwen3:1.7b"
    try:
        prompt = (
            "You are a security architect. Analyze this system design and "
            "identify potential security threats using the STRIDE framework.\n\n"
            f"Design:\n{description}\n\n"
            "For each threat, provide:\n"
            "- STRIDE category (S/T/R/I/D/E)\n"
            "- Title (brief)\n"
            "- Description (1-2 sentences)\n"
            "- Affected components\n"
            "- Recommended NIST 800-53 control\n\n"
            'Respond in JSON format: {"threats": [{"category": "S", "title": "...", '
            '"description": "...", "affected": "...", "nist_control": "..."}]}'
        )

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": 1024, "temperature": 0.3},
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — localhost-only Ollama
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            # Strip thinking tags if present (qwen3 thinking mode)
            content = re.sub(
                r"<think>.*?</think>", "", content, flags=re.DOTALL
            ).strip()
            # Try to extract JSON from the response (may be wrapped in markdown)
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                threats = json.loads(json_match.group()).get("threats", [])
            else:
                threats = json.loads(content).get("threats", [])

        return {
            "threats": threats,
            "source": "ollama",
            "model": model,
            "total_threats": len(threats),
            "error": None,
        }

    except Exception as exc:
        # ── Fallback to deterministic STRIDE analysis ─────────────────
        logger.info(
            "LLM threat identification unavailable (%s), using deterministic fallback",
            exc,
        )
        try:
            from tools.security_canvas.security_engine import run_stride_analysis

            stride_result = run_stride_analysis(graph_data)
            threats = stride_result.get("threats", [])
            return {
                "threats": threats,
                "source": "deterministic",
                "model": None,
                "total_threats": len(threats),
                "error": f"LLM unavailable ({type(exc).__name__}), used deterministic STRIDE analysis",
            }
        except Exception as fallback_exc:
            logger.warning("Deterministic fallback also failed: %s", fallback_exc)
            return {
                "threats": [],
                "source": "deterministic",
                "model": None,
                "total_threats": 0,
                "error": f"Both LLM and deterministic analysis failed: {fallback_exc}",
            }
