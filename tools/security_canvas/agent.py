
from tools.logging.icdev_logger import get_logger
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
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = get_logger("icdev.security_canvas.agent")


# ── IaC Security Scan Rules ──────────────────────────────────────────────────
_IAC_CHECKS = [
    ("0.0.0.0/0", "IaC-001", "Overly permissive CIDR (0.0.0.0/0) detected", "CAT1"),
    ("public-read", "IaC-002", "Public read access enabled on storage", "CAT1"),
    ("encrypted = false", "IaC-003", "Encryption explicitly disabled", "CAT1"),
    ("encrypted  = false", "IaC-003", "Encryption explicitly disabled", "CAT1"),
    ("skip_final_snapshot = true", "IaC-004", "Database final snapshot disabled", "CAT2"),
    ("enable_logging = false", "IaC-005", "Logging explicitly disabled", "CAT2"),
    ("password", "IaC-006", "Potential hardcoded password detected", "CAT2"),
    ("secret_key", "IaC-007", "Potential hardcoded secret key", "CAT1"),
    ("multi_az = false", "IaC-008", "Multi-AZ disabled — single point of failure", "CAT3"),
    ("versioning { enabled = false", "IaC-009", "S3 versioning disabled — no rollback", "CAT2"),
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
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return {"status": "skipped", "reason": "No graph data"}

            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
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
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    design_id,
                    "auto_stride",
                    "ndc_save",
                    topology_id,
                    assessment.get("total_threats", 0),
                    assessment.get("total_controls", 0),
                    assessment.get("risk_score", 0),
                    assessment.get("posture_grade", "F"),
                    json.dumps(assessment.get("findings", [])),
                    json.dumps(assessment.get("recommendations", [])),
                    now,
                ),
            )

        logger.info(
            "Security agent: NDC %s → SDC %s assessed (score=%s, grade=%s)",
            topology_id,
            design_id,
            assessment.get("risk_score"),
            assessment.get("posture_grade"),
        )
        findings = assessment.get("findings", [])
        cat1_count = sum(1 for f in findings if f.get("severity") == "CAT1")

        # Close the NDC->SDC loop: notify the Network Design Canvas that this
        # topology has been assessed so it can record the posture on its side.
        try:
            from tools.canvas.event_bus import publish as _eb_publish

            _eb_publish(
                "sdc",
                "sdc.assessment.completed",
                {
                    "topology_id": topology_id,
                    "design_id": design_id,
                    "assessment_id": assess_id,
                    "risk_score": assessment.get("risk_score"),
                    "posture_grade": assessment.get("posture_grade"),
                    "cat1_count": cat1_count,
                },
            )
        except Exception as _emit_exc:  # noqa: BLE001 — bus failure must not break assessment
            logger.debug("Security agent: assessment.completed emit skipped: %s", _emit_exc)

        return {
            "status": "assessed",
            "design_id": design_id,
            "assessment_id": assess_id,
            "risk_score": assessment.get("risk_score"),
            "posture_grade": assessment.get("posture_grade"),
            "cat1_count": cat1_count,
            "total_findings": len(findings),
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
                findings.append(
                    {
                        "rule_id": rule_id,
                        "title": title,
                        "severity": severity,
                        "line": i,
                        "content": line.strip()[:120],
                        "iac_type": iac_type,
                    }
                )

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
    stage_names = [s if isinstance(s, str) else s.get("name", "") for s in stages]
    has_secret_scan = any("secret" in s.lower() or "detect-secrets" in s.lower() for s in stage_names)
    if not has_secret_scan:
        findings.append(
            {
                "rule_id": "PIPE-001",
                "title": "No secret scanning stage detected",
                "severity": "CAT2",
                "category": "supply_chain",
            }
        )

    # Check: no SAST stage
    has_sast = any("sast" in s.lower() or "security" in s.lower() for s in stage_names)
    if not has_sast:
        findings.append(
            {
                "rule_id": "PIPE-002",
                "title": "No SAST/security scanning stage",
                "severity": "CAT2",
                "category": "supply_chain",
            }
        )

    # Check: no container scanning
    has_container_scan = any("container" in s.lower() and "scan" in s.lower() for s in stage_names)
    if not has_container_scan and any("docker" in str(jobs).lower() or "container" in str(jobs).lower() for _ in [1]):
        findings.append(
            {
                "rule_id": "PIPE-003",
                "title": "Container builds without container scanning",
                "severity": "CAT2",
                "category": "supply_chain",
            }
        )

    # Check individual jobs for issues
    for job_name, job_cfg in jobs.items():
        if isinstance(job_cfg, dict):
            # Overly permissive permissions
            if job_cfg.get("allow_failure") is True:
                findings.append(
                    {
                        "rule_id": "PIPE-004",
                        "title": f"Job '{job_name}' allows failure — may mask security issues",
                        "severity": "CAT3",
                        "category": "misconfiguration",
                    }
                )
            # Privileged mode
            if job_cfg.get("privileged") is True:
                findings.append(
                    {
                        "rule_id": "PIPE-005",
                        "title": f"Job '{job_name}' runs in privileged mode",
                        "severity": "CAT1",
                        "category": "elevation_of_privilege",
                    }
                )

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
        risk_score = float(scan.get("cat1_count", 0) * 5 + scan.get("cat2_count", 0) * 2 + scan.get("cat3_count", 0))
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
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    None,
                    "pipeline_scan",
                    "pdc_save",
                    pipeline_id,
                    scan.get("cat1_count", 0) + scan.get("cat2_count", 0) + scan.get("cat3_count", 0),
                    0,
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: PDC %s assessed (cat1=%s, score=%s, grade=%s)",
            pipeline_id,
            scan.get("cat1_count", 0),
            risk_score,
            grade,
        )
        scan["assessment_id"] = assess_id
        scan["risk_score"] = risk_score
        scan["posture_grade"] = grade
    except Exception as exc:
        logger.warning("Security agent error on PDC save: %s", exc)
        scan["error"] = str(exc)

    return scan


def on_idc_design_saved(design_id: str) -> dict:
    """Called after an IDC infrastructure design is saved.

    Auto-assesses the infra design for security gaps and creates (or updates)
    a corresponding SDC design derived from the IDC component graph.
    Persists results to sc_assessments with trigger_source='idc_save'.
    """
    try:
        from tools.infra_canvas.db.init_db import get_connection as get_idc_conn
        from tools.security_canvas.db.init_db import get_connection as get_sdc_conn

        # Load IDC graph
        with get_idc_conn() as idc_conn:
            row = idc_conn.execute(
                "SELECT name, graph_json FROM infra_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "IDC design not found"}

        design_name = row[0]
        graph = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Map IDC node types to SDC component types
        _IDC_TO_SDC = {
            "ec2": "server",
            "lambda": "service",
            "rds": "database",
            "s3": "storage",
            "alb": "load_balancer",
            "api_gateway": "api",
            "vpc": "network",
            "subnet": "network",
            "security_group": "firewall",
            "iam": "auth_service",
            "kms": "key_management",
            "waf": "waf",
        }
        sdc_nodes = [
            {
                "id": n.get("id"),
                "label": n.get("label", n.get("id", "?")),
                "type": _IDC_TO_SDC.get(n.get("type", "").lower(), "server"),
                "data": n.get("data", {}),
            }
            for n in nodes
        ]
        sdc_edges = [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "protocol": e.get("protocol", "HTTPS"),
                "encrypted": e.get("encrypted", True),
                "authenticated": e.get("authenticated", False),
            }
            for e in edges
        ]
        sdc_graph = {"nodes": sdc_nodes, "edges": sdc_edges, "boundaries": []}

        # Security gap checks on infra components
        findings = []
        for n in nodes:
            ntype = n.get("type", "").lower()
            nlabel = n.get("label", n.get("id", "?"))
            if ntype == "s3" and not n.get("data", {}).get("encrypted"):
                findings.append(
                    {"rule_id": "IDC-001", "title": f"S3 bucket '{nlabel}' missing encryption", "severity": "CAT1"}
                )
            if ntype == "rds" and not n.get("data", {}).get("multi_az"):
                findings.append({"rule_id": "IDC-002", "title": f"RDS '{nlabel}' missing Multi-AZ", "severity": "CAT3"})
            if ntype == "ec2" and n.get("data", {}).get("public_ip"):
                findings.append(
                    {
                        "rule_id": "IDC-003",
                        "title": f"EC2 '{nlabel}' has public IP — review exposure",
                        "severity": "CAT2",
                    }
                )
            if ntype == "security_group":
                rules = n.get("data", {}).get("rules", [])
                if any("0.0.0.0/0" in str(r) for r in rules):
                    findings.append(
                        {
                            "rule_id": "IDC-004",
                            "title": f"Security group '{nlabel}' allows 0.0.0.0/0",
                            "severity": "CAT1",
                        }
                    )

        cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
        cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
        cat3 = sum(1 for f in findings if f["severity"] == "CAT3")
        risk_score = float(cat1 * 10 + cat2 * 4 + cat3)
        grade = (
            "F"
            if risk_score >= 30
            else ("D" if risk_score >= 20 else ("C" if risk_score >= 10 else ("B" if risk_score >= 4 else "A")))
        )

        now = datetime.now(timezone.utc).isoformat()
        assess_id = str(uuid.uuid4())

        with get_sdc_conn() as sdc_conn:
            # Upsert derived SDC design
            existing = sdc_conn.execute(
                "SELECT id FROM security_designs WHERE name=%s",
                (f"[IDC] {design_name}",),
            ).fetchone()
            if existing:
                sdc_design_id = existing[0]
                sdc_conn.execute(
                    "UPDATE security_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                    (json.dumps(sdc_graph), now, sdc_design_id),
                )
            else:
                sdc_design_id = f"sdc-{uuid.uuid4().hex[:10]}"
                sdc_conn.execute(
                    "INSERT INTO security_designs "
                    "(id, name, description, classification, graph_json, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        sdc_design_id,
                        f"[IDC] {design_name}",
                        f"Auto-derived from IDC design {design_id}",
                        "CUI",
                        json.dumps(sdc_graph),
                        now,
                        now,
                    ),
                )

            # Persist assessment
            sdc_conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    sdc_design_id,
                    "idc_gap_scan",
                    "idc_save",
                    design_id,
                    len(findings),
                    0,
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: IDC %s → SDC %s assessed (cat1=%s, score=%s, grade=%s)",
            design_id,
            sdc_design_id,
            cat1,
            risk_score,
            grade,
        )
        return {
            "status": "assessed",
            "sdc_design_id": sdc_design_id,
            "assessment_id": assess_id,
            "cat1": cat1,
            "cat2": cat2,
            "cat3": cat3,
            "risk_score": risk_score,
            "posture_grade": grade,
        }
    except Exception as exc:
        logger.warning("Security agent error on IDC save: %s", exc)
        return {"status": "error", "error": str(exc)}


def on_ddc_design_saved(design_id: str) -> dict:
    """Called after a DDC data design is saved.

    Auto-classifies data flows and detects CUI/PII exposure threats.
    Persists findings to sc_assessments with trigger_source='ddc_save'.
    """
    _CUI_KEYWORDS = frozenset(
        {
            "ssn",
            "social_security",
            "pii",
            "cui",
            "phi",
            "pci",
            "credit_card",
            "password",
            "secret",
            "private_key",
            "dod_id",
            "edipi",
            "npi",
        }
    )
    _UNPROTECTED_TYPES = frozenset({"ftp", "http", "smtp", "telnet", "unencrypted"})

    try:
        from tools.data_canvas.db.init_db import get_connection as get_ddc_conn
        from tools.security_canvas.db.init_db import get_connection as get_sdc_conn

        with get_ddc_conn() as ddc_conn:
            row = ddc_conn.execute(
                "SELECT name, graph_json FROM data_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "DDC design not found"}

        design_name = row[0]
        graph = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        findings = []
        for n in nodes:
            label_lower = n.get("label", "").lower()
            node_type = n.get("type", "").lower()
            # CUI/PII data store detection
            if any(kw in label_lower for kw in _CUI_KEYWORDS):
                findings.append(
                    {
                        "rule_id": "DDC-001",
                        "title": f"Potential CUI/PII data store: '{n.get('label', '?')}'",
                        "severity": "CAT1",
                        "affected": n.get("id"),
                    }
                )
                # Check if this node has unencrypted connections
                node_edges = [e for e in edges if e.get("source") == n.get("id") or e.get("target") == n.get("id")]
                for e in node_edges:
                    if not e.get("encrypted", True):
                        findings.append(
                            {
                                "rule_id": "DDC-002",
                                "title": f"CUI data node '{n.get('label', '?')}' on unencrypted flow",
                                "severity": "CAT1",
                                "affected": n.get("id"),
                            }
                        )
            # Unprotected external data store
            if node_type in ("external", "api", "external_service"):
                unprotected = [
                    e
                    for e in edges
                    if (e.get("source") == n.get("id") or e.get("target") == n.get("id"))
                    and e.get("protocol", "").lower() in _UNPROTECTED_TYPES
                ]
                if unprotected:
                    findings.append(
                        {
                            "rule_id": "DDC-003",
                            "title": f"External node '{n.get('label', '?')}' uses insecure protocol",
                            "severity": "CAT2",
                            "affected": n.get("id"),
                        }
                    )

        cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
        cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
        risk_score = float(cat1 * 10 + cat2 * 4)
        grade = "F" if risk_score >= 20 else ("D" if risk_score >= 10 else ("C" if risk_score >= 4 else "A"))
        now = datetime.now(timezone.utc).isoformat()
        assess_id = str(uuid.uuid4())

        with get_sdc_conn() as sdc_conn:
            sdc_conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    None,
                    "ddc_cui_scan",
                    "ddc_save",
                    design_id,
                    len(findings),
                    0,
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: DDC %s assessed (cat1=%s, score=%s, grade=%s)",
            design_id,
            cat1,
            risk_score,
            grade,
        )
        return {
            "status": "assessed",
            "assessment_id": assess_id,
            "design_name": design_name,
            "cat1": cat1,
            "cat2": cat2,
            "risk_score": risk_score,
            "posture_grade": grade,
        }
    except Exception as exc:
        logger.warning("Security agent error on DDC save: %s", exc)
        return {"status": "error", "error": str(exc)}


def on_bdc_design_saved(design_id: str) -> dict:
    """Called after a BDC boundary design is saved.

    Validates the boundary against NDC enclaves and checks ISA consistency.
    Persists findings to sc_assessments with trigger_source='bdc_save'.
    """
    try:
        from tools.boundary_canvas.db.init_db import get_connection as get_bdc_conn
        from tools.security_canvas.db.init_db import get_connection as get_sdc_conn

        with get_bdc_conn() as bdc_conn:
            row = bdc_conn.execute(
                "SELECT name, graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "BDC design not found"}

        design_name = row[0]
        graph = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        edges = graph.get("edges", [])
        boundaries = graph.get("boundaries", [])

        findings = []

        # Check boundary IL level consistency
        il_levels = {b.get("il_level") for b in boundaries if b.get("il_level")}
        if len(il_levels) > 1:
            findings.append(
                {
                    "rule_id": "BDC-001",
                    "title": "Mixed IL levels in same boundary design — review enclave separation",
                    "severity": "CAT2",
                }
            )

        # Check for cross-boundary flows without ISA
        for e in edges:
            src_boundary = next((b.get("id") for b in boundaries if e.get("source") in b.get("members", [])), None)
            tgt_boundary = next((b.get("id") for b in boundaries if e.get("target") in b.get("members", [])), None)
            if src_boundary and tgt_boundary and src_boundary != tgt_boundary:
                if not e.get("isa_id"):
                    findings.append(
                        {
                            "rule_id": "BDC-002",
                            "title": f"Cross-boundary flow missing ISA agreement "
                            f"(src_boundary={src_boundary}, tgt_boundary={tgt_boundary})",
                            "severity": "CAT1",
                        }
                    )

        # Validate NDC enclaves: check if known NDC topologies match BDC boundaries
        try:
            from tools.network.db.init_db import get_connection as get_ndc_conn

            with get_ndc_conn() as ndc_conn:
                ndc_enclaves = ndc_conn.execute("SELECT id, name FROM topologies LIMIT 50").fetchall()
            ndc_ids = {row[0] for row in ndc_enclaves}
            for b in boundaries:
                source_topo = b.get("source_topology_id")
                if source_topo and source_topo not in ndc_ids:
                    findings.append(
                        {
                            "rule_id": "BDC-003",
                            "title": f"Boundary '{b.get('label', '?')}' references unknown NDC topology",
                            "severity": "CAT2",
                        }
                    )
        except Exception:
            pass  # NDC not available — skip cross-canvas check

        cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
        cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
        risk_score = float(cat1 * 10 + cat2 * 4)
        grade = "F" if risk_score >= 20 else ("D" if risk_score >= 10 else ("C" if risk_score >= 4 else "A"))
        now = datetime.now(timezone.utc).isoformat()
        assess_id = str(uuid.uuid4())

        with get_sdc_conn() as sdc_conn:
            sdc_conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    None,
                    "bdc_boundary_scan",
                    "bdc_save",
                    design_id,
                    len(findings),
                    0,
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: BDC %s assessed (cat1=%s, score=%s, grade=%s)",
            design_id,
            cat1,
            risk_score,
            grade,
        )
        return {
            "status": "assessed",
            "assessment_id": assess_id,
            "design_name": design_name,
            "cat1": cat1,
            "cat2": cat2,
            "risk_score": risk_score,
            "posture_grade": grade,
        }
    except Exception as exc:
        logger.warning("Security agent error on BDC save: %s", exc)
        return {"status": "error", "error": str(exc)}


def on_odc_design_saved(design_id: str) -> dict:
    """Called after an ODC observability design is saved.

    Verifies that SIEM/logging controls in the ODC design match the
    monitoring controls required by SDC assessment requirements.
    Persists findings to sc_assessments with trigger_source='odc_save'.
    """
    _REQUIRED_CONTROLS = {
        "siem": "AU-12",
        "log_aggregation": "AU-3",
        "intrusion_detection": "SI-4",
        "vulnerability_scanner": "RA-5",
        "audit_trail": "AU-2",
    }

    try:
        from tools.observability_canvas.db.init_db import get_connection as get_odc_conn
        from tools.security_canvas.db.init_db import get_connection as get_sdc_conn

        with get_odc_conn() as odc_conn:
            row = odc_conn.execute(
                "SELECT name, graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "ODC design not found"}

        design_name = row[0]
        graph = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        nodes = graph.get("nodes", [])

        # Detect which monitoring types are present
        node_types_present = {n.get("type", "").lower() for n in nodes}
        node_labels_lower = " ".join(n.get("label", "").lower() for n in nodes)

        findings = []
        for control_type, nist_control in _REQUIRED_CONTROLS.items():
            has_control = (
                control_type in node_types_present
                or control_type.replace("_", " ") in node_labels_lower
                or control_type.replace("_", "") in node_labels_lower
            )
            if not has_control:
                findings.append(
                    {
                        "rule_id": f"ODC-{list(_REQUIRED_CONTROLS).index(control_type) + 1:03d}",
                        "title": f"Missing monitoring control: {control_type.replace('_', ' ').title()} "
                        f"(NIST {nist_control})",
                        "severity": "CAT2" if control_type in ("siem", "audit_trail") else "CAT3",
                        "nist_control": nist_control,
                    }
                )

        # Check if any SDC designs have unmatched logging requirements
        try:
            with get_sdc_conn() as sdc_conn:
                sdc_findings_rows = sdc_conn.execute(
                    "SELECT findings_json FROM sc_assessments ORDER BY ran_at DESC LIMIT 5"
                ).fetchall()
            for frow in sdc_findings_rows:
                sdc_finds = json.loads(frow[0] or "[]")
                for sf in sdc_finds:
                    if "logging" in sf.get("title", "").lower() and "log" not in node_labels_lower:
                        findings.append(
                            {
                                "rule_id": "ODC-LOG-001",
                                "title": "SDC audit finding references logging — not covered in ODC",
                                "severity": "CAT2",
                            }
                        )
                        break
        except Exception:
            pass

        cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
        cat3 = sum(1 for f in findings if f["severity"] == "CAT3")
        risk_score = float(cat2 * 4 + cat3)
        grade = "F" if risk_score >= 20 else ("D" if risk_score >= 12 else ("C" if risk_score >= 4 else "A"))
        now = datetime.now(timezone.utc).isoformat()
        assess_id = str(uuid.uuid4())

        with get_sdc_conn() as sdc_conn:
            sdc_conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    None,
                    "odc_logging_scan",
                    "odc_save",
                    design_id,
                    len(findings),
                    len(_REQUIRED_CONTROLS),
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: ODC %s assessed (cat2=%s, score=%s, grade=%s)",
            design_id,
            cat2,
            risk_score,
            grade,
        )
        return {
            "status": "assessed",
            "assessment_id": assess_id,
            "design_name": design_name,
            "cat2": cat2,
            "cat3": cat3,
            "risk_score": risk_score,
            "posture_grade": grade,
        }
    except Exception as exc:
        logger.warning("Security agent error on ODC save: %s", exc)
        return {"status": "error", "error": str(exc)}


def on_mdc_design_saved(design_id: str) -> dict:
    """Called after an MDC migration design is saved.

    Assesses migration designs for security risks: unpatched source systems,
    missing ATO gates on GovCloud targets, absent compliance bridges, and
    missing rollback controls.
    Persists findings to sc_assessments with trigger_source='mdc_save'.
    """
    _MIGRATION_CHECKS = {
        "ctl-ato-gate": ("MC-SEC-001", "Migration target missing ATO gate control", "CAT1"),
        "ctl-compliance-bridge": ("MC-SEC-002", "Missing compliance bridge for target environment", "CAT2"),
        "ctl-rollback": ("MC-SEC-003", "No rollback control defined for migration", "CAT2"),
        "ctl-security-scan": ("MC-SEC-004", "Missing security scan gate in migration path", "CAT2"),
        "ctl-test-gate": ("MC-SEC-005", "No test gate before migration cutover", "CAT3"),
    }

    try:
        from tools.migration_canvas.db.init_db import get_connection as get_mdc_conn
        from tools.security_canvas.db.init_db import get_connection as get_sdc_conn

        with get_mdc_conn() as mdc_conn:
            row = mdc_conn.execute(
                "SELECT name, graph_json FROM migration_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "MDC design not found"}

        design_name = row[0]
        graph = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        node_ids = {n.get("id", "") for n in nodes}
        node_types = {n.get("id", ""): n.get("type", "") for n in nodes}

        findings = []

        # Check for required control nodes
        for prefix, (rule_id, title, severity) in _MIGRATION_CHECKS.items():
            has_control = any(
                nid.startswith(prefix) or node_types.get(nid, "").startswith(prefix)
                for nid in node_ids
            )
            if not has_control:
                findings.append(
                    {"rule_id": rule_id, "title": title, "severity": severity}
                )

        # Check GovCloud targets without ATO gate connected
        govcloud_targets = [
            n for n in nodes
            if n.get("id", "").startswith("tgt-") and "govcloud" in n.get("id", "").lower()
        ]
        ato_gates = {
            n.get("id") for n in nodes if n.get("id", "").startswith("ctl-ato")
        }
        for tgt in govcloud_targets:
            tgt_id = tgt.get("id", "")
            connected_to_ato = any(
                (e.get("source") in ato_gates and e.get("target") == tgt_id)
                or (e.get("target") in ato_gates and e.get("source") == tgt_id)
                for e in edges
            )
            if not connected_to_ato:
                findings.append(
                    {
                        "rule_id": "MC-SEC-006",
                        "title": f"GovCloud target '{tgt.get('label', tgt_id)}' has no connected ATO gate",
                        "severity": "CAT1",
                    }
                )

        # Check source systems without security scan in path
        sources = [n for n in nodes if n.get("id", "").startswith("src-")]
        scan_nodes = {
            n.get("id") for n in nodes if "security" in n.get("id", "").lower() or "scan" in n.get("id", "").lower()
        }
        for src in sources:
            src_id = src.get("id", "")
            has_scan_in_path = any(
                (e.get("source") == src_id and e.get("target") in scan_nodes)
                or (e.get("source") in scan_nodes and e.get("target") == src_id)
                for e in edges
            )
            if not has_scan_in_path and scan_nodes:
                findings.append(
                    {
                        "rule_id": "MC-SEC-007",
                        "title": f"Source '{src.get('label', src_id)}' not connected to security scan",
                        "severity": "CAT3",
                    }
                )

        cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
        cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
        cat3 = sum(1 for f in findings if f["severity"] == "CAT3")
        risk_score = float(cat1 * 10 + cat2 * 4 + cat3)
        grade = (
            "F" if risk_score >= 20
            else ("D" if risk_score >= 12
                  else ("C" if risk_score >= 4 else ("B" if risk_score >= 1 else "A")))
        )
        now = datetime.now(timezone.utc).isoformat()
        assess_id = str(uuid.uuid4())

        with get_sdc_conn() as sdc_conn:
            sdc_conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "source_entity_id, total_threats, total_controls, "
                "risk_score, posture_grade, findings_json, "
                "recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    None,
                    "mdc_migration_scan",
                    "mdc_save",
                    design_id,
                    len(findings),
                    len(_MIGRATION_CHECKS),
                    risk_score,
                    grade,
                    json.dumps(findings),
                    json.dumps([]),
                    now,
                ),
            )

        logger.info(
            "Security agent: MDC %s assessed (cat1=%s, cat2=%s, score=%s, grade=%s)",
            design_id,
            cat1,
            cat2,
            risk_score,
            grade,
        )
        return {
            "status": "assessed",
            "assessment_id": assess_id,
            "design_name": design_name,
            "cat1": cat1,
            "cat2": cat2,
            "cat3": cat3,
            "risk_score": risk_score,
            "posture_grade": grade,
        }
    except Exception as exc:
        logger.warning("Security agent error on MDC save: %s", exc)
        return {"status": "error", "error": str(exc)}


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
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return {"status": "skipped", "reason": "Design not found"}

            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
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
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    design_id,
                    "auto",
                    trigger_source,
                    assessment.get("total_threats", 0),
                    assessment.get("total_controls", 0),
                    assessment.get("risk_score", 0),
                    assessment.get("posture_grade", "F"),
                    json.dumps(assessment.get("findings", [])),
                    json.dumps(assessment.get("recommendations", [])),
                    now,
                ),
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
    """Identify security threats using the governed LLM with STRIDE framework.

    Builds a text description of the design from *graph_data*, sends it
    through the Security Canvas LLM adapter (``tools.security_canvas.llm_adapter``,
    which routes via ``LLMRouter`` under the ``security_canvas`` function) for
    STRIDE analysis, and returns structured threat results.  Falls back to
    deterministic :func:`run_stride_analysis` if the LLM is unavailable.

    Args:
        graph_data: Design graph dict with nodes, edges, boundaries.

    Returns:
        Dict with threats list, source indicator, model name, count,
        and optional error message.
    """
    import re

    from tools.security_canvas import llm_adapter

    # ── Build text description of the design ──────────────────────────────
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])

    lines = []
    if nodes:
        lines.append("Components:")
        for n in nodes:
            lines.append(f"  - {n.get('label', n.get('id', '?'))} (type: {n.get('type', 'unknown')})")
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

    # ── Try governed LLM via the Security Canvas adapter ──────────────────
    # Routing, provider selection, model IDs, governance, and budget caps are
    # resolved from args/llm_config.yaml under the ``security_canvas`` function
    # (see tools/security_canvas/llm_adapter.py). The adapter returns None on
    # ANY failure, which drops us into the deterministic STRIDE fallback below.
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

        content = llm_adapter.generate(
            prompt,
            purpose="security_canvas",
            max_tokens=1024,
            temperature=0.3,
        )
        if not content:
            raise RuntimeError("LLM adapter returned no text")

        # Strip thinking tags if present (e.g. qwen thinking mode)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Try to extract JSON from the response (may be wrapped in markdown)
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            threats = json.loads(json_match.group()).get("threats", [])
        else:
            threats = json.loads(content).get("threats", [])

        return {
            "threats": threats,
            "source": "llm",
            "model": "security_canvas",
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
