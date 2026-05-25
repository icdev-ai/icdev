# CUI // SP-CTI
"""ICDEV - Canvas Findings (POA&M) approval API.

Powers the /poam dashboard page. Aggregates open findings from the 7 canvas
SQLite DBs via tools.dashboard.findings_aggregator and lets a reviewer record
their decision (approved | declined | accepted_risk | remediated) which is
persisted into icdev.db / finding_approvals + audit_trail.

Endpoints:
    GET  /api/poam                              - list all findings (with filters)
    GET  /api/poam/summary                      - counts by severity / canvas / decision
    GET  /api/poam/<finding_hash>               - single finding detail
    POST /api/poam/<finding_hash>/decision      - record approval decision
    POST /api/poam/<finding_hash>/attach_plan   - attach remediation plan to finding
    POST /api/poam/<finding_hash>/file_github_issue - file GitHub issue for finding
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import subprocess
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, session

from tools.dashboard.config import DB_PATH
from tools.dashboard.findings_aggregator import aggregate_findings, compute_finding_hash, summary
from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Built-in remediation plans for known IDC rule IDs
# ---------------------------------------------------------------------------
IDC_PLANS: dict[str, dict] = {
    "IDC-IAM-001": {
        "title": "Add IAM/IdP service to infra design",
        "phase": "CAT1 (0-48h)",
        "effort_hours": 16,
        "nist_controls": ["IA-2", "IA-2(1)", "IA-2(2)", "IA-5", "AC-2", "AC-3"],
        "options_aws": "aws-iam + aws-cognito (federated)",
        "options_azure": "az-entra (Entra ID, formerly Azure AD)",
        "options_gcp": "gcp-iam + Cloud Identity",
        "options_onprem": "iac-vault, FreeIPA, Keycloak, Active Directory",
        "steps": [
            "Architect selects IAM/IdP product appropriate to chosen CSP and IL.",
            "Add IAM/IdP node to infra canvas with chosen vendor type (e.g., aws-iam, az-entra).",
            "Wire IAM node to all user-facing assets (workstations, applications, APIs).",
            "Configure SAML/OIDC federation if multi-cloud or hybrid.",
            "Enforce MFA per NIST IA-2(1)/(2) - phishing-resistant for IL5+.",
            "Re-run infra assessment to confirm IDC-IAM-001 is closed.",
        ],
    },
    "IDC-ENC-003": {
        "title": "Add KMS/Key Vault to infra design",
        "phase": "CAT1 (0-48h)",
        "effort_hours": 16,
        "nist_controls": ["SC-12", "SC-13", "SC-28", "SC-28(1)"],
        "options_aws": "aws-kms (FIPS 140-2 validated; CloudHSM for IL5+)",
        "options_azure": "az-keyvault (Premium tier with HSM-backed keys)",
        "options_gcp": "gcp-kms (Cloud HSM for FIPS 140-2 L3)",
        "options_onprem": "iac-vault (HashiCorp Vault) with HSM backend",
        "steps": [
            "Architect selects KMS product. For IL5+ require FIPS 140-2 L3 (HSM-backed).",
            "Add KMS node to infra canvas.",
            "Wire KMS to every database, storage, and secrets-manager node.",
            "Define key rotation policy (NIST SC-12) - default 90 days.",
            "Verify CMVP certificate number for chosen module (NIST SC-13).",
            "Re-run infra assessment to confirm IDC-ENC-003 is closed.",
        ],
    },
    "IDC-IAM-002": {
        "title": "Add centralized secrets manager to infra design",
        "phase": "CAT1 (0-48h)",
        "effort_hours": 16,
        "nist_controls": ["IA-5", "IA-5(1)", "SC-12", "CM-6"],
        "options_aws": "aws-secrets (Secrets Manager) or aws-ssm (Parameter Store SecureString)",
        "options_azure": "az-keyvault (secrets vault, distinct from key vault)",
        "options_gcp": "gcp-secret (Secret Manager)",
        "options_onprem": "iac-vault (HashiCorp Vault) - preferred for multi-cloud",
        "steps": [
            "Architect selects secrets product (often the same as KMS choice for cohesion).",
            "Add secrets-manager node to infra canvas.",
            "Wire to all applications/services that need credentials at runtime.",
            "Enforce rotation policy: <= 90 days per NIST IA-5(1).",
            "Eliminate hardcoded secrets - codebase scan with bandit/gitleaks/trufflehog.",
            "Re-run infra assessment to confirm IDC-IAM-002 is closed.",
        ],
    },
    "IDC-SEC-001": {
        "title": "Add CSPM (cloud security posture management) to infra design",
        "phase": "CAT2 (1-2 weeks)",
        "effort_hours": 16,
        "nist_controls": ["CA-7", "CM-2", "CM-6", "RA-5", "SI-4"],
        "options_aws": "aws-securityhub + aws-config",
        "options_azure": "az-defender (Microsoft Defender for Cloud)",
        "options_gcp": "gcp-scc (Security Command Center Premium)",
        "options_onprem": "Wiz, Prisma Cloud, Lacework - agent-based for non-cloud workloads",
        "steps": [
            "Architect selects CSPM product based on dominant CSP.",
            "Add CSPM node to infra canvas.",
            "Enable continuous compliance scanning against CIS/STIG/FedRAMP baselines.",
            "Wire CSPM findings to SIEM/SOAR for incident pipeline.",
            "Configure ticket auto-creation for HIGH/CRITICAL drift.",
            "Re-run infra assessment to confirm IDC-SEC-001 is closed.",
        ],
    },
    "IDC-IAC-001": {
        "title": "Add IaC tool (Terraform/Pulumi/Crossplane) to infra design",
        "phase": "CAT2 (1-2 weeks)",
        "effort_hours": 24,
        "nist_controls": ["CM-2", "CM-3", "CM-6", "CM-8", "SA-10"],
        "options_aws": "iac-terraform (preferred for GovCloud) or aws-cdk",
        "options_azure": "iac-terraform or iac-bicep",
        "options_gcp": "iac-terraform",
        "options_onprem": "iac-terraform or iac-crossplane (Kubernetes-native)",
        "steps": [
            "Architect selects IaC tool. Terraform is the de facto standard across clouds.",
            "Add iac-terraform (or chosen) node to infra canvas.",
            "Migrate console-managed resources into IaC modules.",
            "Store state in remote backend (S3 + DynamoDB lock, az-storage, gcs).",
            "Wire IaC pipeline to GitLab CI with mandatory plan-review before apply.",
            "Re-run infra assessment to confirm IDC-IAC-001 is closed.",
        ],
    },
}

logger = get_logger("icdev.dashboard.poam_api")

poam_api = Blueprint("poam_api", __name__, url_prefix="/api/poam")

VALID_DECISIONS = {"pending", "approved", "declined", "accepted_risk", "remediated"}


def _get_db():
    return get_connection(db_path=str(DB_PATH))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_decision(finding_hash: str, canvas: str, decision: str, reviewer: str, rationale: str) -> None:
    """Append decision change to audit_trail (best-effort, non-blocking)."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="finding_approval",
            actor=reviewer or "anonymous",
            action=f"finding.{decision}",
            details=(
                f'{{"finding_hash":"{finding_hash}","canvas":"{canvas}",'
                f'"decision":"{decision}","rationale":"{(rationale or "")[:500]}"}}'
            ),
            project_id="dashboard-poam",
        )
    except Exception as exc:  # pragma: no cover - audit must never break the API
        logger.warning("audit logging failed for %s: %s", finding_hash, exc)


@poam_api.route("", methods=["GET"])
def list_findings():
    """List canvas findings with their approval state.

    Query params:
        canvas    - filter by canvas slug (security|infra|...|pipeline)
        severity  - filter by CAT1 / CAT2 / CAT3
        decision  - filter by pending|approved|declined|accepted_risk|remediated
        include_remediated - "true" to include remediated source rows (default: true)
    """
    include_remediated = request.args.get("include_remediated", "true").lower() in ("1", "true", "yes")
    findings = aggregate_findings(get_db_conn=_get_db, include_remediated=include_remediated)

    canvas = (request.args.get("canvas") or "").strip().lower()
    severity = (request.args.get("severity") or "").strip().upper()
    decision = (request.args.get("decision") or "").strip().lower()

    if canvas:
        findings = [f for f in findings if f["canvas_source"] == canvas]
    if severity:
        findings = [f for f in findings if f["severity"] == severity]
    if decision:
        findings = [f for f in findings if f["decision"] == decision]

    return jsonify({"findings": findings, "total": len(findings)})


@poam_api.route("/summary", methods=["GET"])
def get_summary():
    """Counts grouped by severity, canvas, and decision (for dashboard charts)."""
    return jsonify(summary(get_db_conn=_get_db))


@poam_api.route("/<finding_hash>", methods=["GET"])
def get_finding(finding_hash):
    """Single finding detail (looked up by hash from the aggregator output)."""
    findings = aggregate_findings(get_db_conn=_get_db, include_remediated=True)
    for f in findings:
        if f["finding_hash"] == finding_hash:
            return jsonify(f)
    return jsonify({"error": "finding not found"}), 404


@poam_api.route("/<finding_hash>/decision", methods=["POST"])
def record_decision(finding_hash):
    """Record an approval decision for a finding.

    Body (JSON):
        decision  - one of approved|declined|accepted_risk|remediated|pending
        rationale - free-text justification (required for declined/accepted_risk)
    """
    payload = request.get_json(silent=True) or {}
    decision = (payload.get("decision") or "").strip().lower()
    rationale = (payload.get("rationale") or "").strip()

    if decision not in VALID_DECISIONS:
        return jsonify({
            "error": "invalid decision",
            "valid": sorted(VALID_DECISIONS),
        }), 400

    if decision in ("declined", "accepted_risk") and not rationale:
        return jsonify({
            "error": f"rationale required for decision={decision}",
        }), 400

    findings = aggregate_findings(get_db_conn=_get_db, include_remediated=True)
    target = next((f for f in findings if f["finding_hash"] == finding_hash), None)
    if target is None:
        return jsonify({"error": "finding not found in current canvas data"}), 404

    reviewer = (session.get("user") or request.headers.get("X-User") or "anonymous")
    now = _now_iso()

    conn = _get_db()
    try:
        # UPSERT — finding_hash is PK. The translator turns this into ON CONFLICT for PG.
        existing = conn.execute(
            "SELECT decision FROM finding_approvals WHERE finding_hash = ?",
            (finding_hash,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE finding_approvals SET decision = ?, decision_by = ?, "
                "decision_at = ?, decision_rationale = ?, updated_at = ? "
                "WHERE finding_hash = ?",
                (decision, reviewer, now, rationale, now, finding_hash),
            )
        else:
            conn.execute(
                "INSERT INTO finding_approvals "
                "(finding_hash, canvas_source, rule_id, severity, title, "
                "affected_entity, decision, decision_by, decision_at, "
                "decision_rationale, classification, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding_hash,
                    target["canvas_source"],
                    target["rule_id"],
                    target["severity"],
                    target["title"],
                    target["affected_entity"],
                    decision,
                    reviewer,
                    now,
                    rationale,
                    "CUI // SP-CTI",
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    _audit_decision(finding_hash, target["canvas_source"], decision, reviewer, rationale)

    return jsonify({
        "ok": True,
        "finding_hash": finding_hash,
        "decision": decision,
        "decision_by": reviewer,
        "decision_at": now,
    })


# ---------------------------------------------------------------------------
# Helpers for plan attachment and issue filing
# ---------------------------------------------------------------------------

def _format_plan(rule_id: str, plan: dict) -> str:
    """Render a remediation plan dict as a structured text block."""
    lines = [
        f"Title: {plan['title']}",
        f"Phase: {plan['phase']} (effort: {plan['effort_hours']}h)",
        f"NIST 800-53 controls: {', '.join(plan.get('nist_controls', []))}",
        "Vendor options:",
        f"  - AWS: {plan.get('options_aws', 'N/A')}",
        f"  - AZURE: {plan.get('options_azure', 'N/A')}",
        f"  - GCP: {plan.get('options_gcp', 'N/A')}",
        f"  - ONPREM: {plan.get('options_onprem', 'N/A')}",
        "Remediation steps:",
    ]
    for i, step in enumerate(plan.get("steps", []), 1):
        lines.append(f"  {i}. {step}")
    return "\n".join(lines)


def _upsert_approval_row(conn, target: dict, now: str) -> None:
    """Ensure a row exists in finding_approvals for the given finding (no-op if present)."""
    existing = conn.execute(
        "SELECT finding_hash FROM finding_approvals WHERE finding_hash = ?",
        (target["finding_hash"],),
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO finding_approvals "
            "(finding_hash, canvas_source, rule_id, severity, title, "
            "affected_entity, decision, classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target["finding_hash"],
                target["canvas_source"],
                target["rule_id"],
                target["severity"],
                target["title"],
                target["affected_entity"],
                "pending",
                "CUI // SP-CTI",
                now,
                now,
            ),
        )


# ---------------------------------------------------------------------------
# POST /api/poam/<finding_hash>/attach_plan
# ---------------------------------------------------------------------------

@poam_api.route("/<finding_hash>/attach_plan", methods=["POST"])
def attach_plan(finding_hash):
    """Attach a remediation plan to a finding's decision_rationale.

    Body (JSON, optional):
        plan - dict with keys: title, phase, effort_hours, nist_controls,
               options_aws, options_azure, options_gcp, options_onprem, steps.
               If omitted, the built-in IDC_PLANS entry for the finding's
               rule_id is used (required for non-IDC rules).

    The plan is prepended with a [REMEDIATION PLAN] header and written to
    decision_rationale, preserving any existing GitHub issue URL at the end.
    """
    payload = request.get_json(silent=True) or {}
    plan_data = payload.get("plan")

    # Resolve the finding
    findings = aggregate_findings(get_db_conn=_get_db, include_remediated=True)
    target = next((f for f in findings if f["finding_hash"] == finding_hash), None)
    if target is None:
        return jsonify({"error": "finding not found"}), 404

    # Resolve the plan
    if plan_data is None:
        plan_data = IDC_PLANS.get(target["rule_id"])
        if plan_data is None:
            return jsonify({
                "error": (
                    f"no built-in plan for rule_id={target['rule_id']}. "
                    "Provide a 'plan' object in the request body."
                )
            }), 400

    plan_text = (
        "[REMEDIATION PLAN - manual architect review required]\n\n"
        + _format_plan(target["rule_id"], plan_data)
    )

    now = _now_iso()
    conn = _get_db()
    try:
        _upsert_approval_row(conn, target, now)

        # Preserve any existing GitHub issue URL appended after the plan
        existing_row = conn.execute(
            "SELECT decision_rationale FROM finding_approvals WHERE finding_hash = ?",
            (finding_hash,),
        ).fetchone()
        issue_suffix = ""
        if existing_row:
            old_rationale = dict(existing_row).get("decision_rationale") or ""
            # Keep any [GITHUB ISSUE] lines already appended
            for line in old_rationale.splitlines():
                if line.startswith("[GITHUB ISSUE]"):
                    issue_suffix += f"\n\n{line}"

        conn.execute(
            "UPDATE finding_approvals SET decision_rationale = ?, updated_at = ? "
            "WHERE finding_hash = ?",
            (plan_text + issue_suffix, now, finding_hash),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="finding_approval",
            actor=(session.get("user") or request.headers.get("X-User") or "anonymous"),
            action="poam.attach_plan",
            details=json.dumps({"finding_hash": finding_hash, "rule_id": target["rule_id"]}),
            project_id="dashboard-poam",
        )
    except Exception as exc:
        logger.warning("audit logging failed for attach_plan %s: %s", finding_hash, exc)

    return jsonify({
        "ok": True,
        "finding_hash": finding_hash,
        "rule_id": target["rule_id"],
        "plan_attached": True,
    })


# ---------------------------------------------------------------------------
# POST /api/poam/<finding_hash>/file_github_issue
# ---------------------------------------------------------------------------

@poam_api.route("/<finding_hash>/file_github_issue", methods=["POST"])
def file_github_issue(finding_hash):
    """File a GitHub issue for a POA&M finding via the gh CLI.

    Creates an issue with title format:
        [POA&M] [SEVERITY] RULE_ID: Title

    The current decision_rationale (remediation plan) is embedded in the body.
    The resulting GitHub issue URL is appended to decision_rationale in the DB.

    Body (JSON, optional):
        extra_labels - list of additional label strings to apply

    Returns:
        { ok, finding_hash, rule_id, github_url }
    """
    payload = request.get_json(silent=True) or {}
    extra_labels: list[str] = payload.get("extra_labels", [])

    # Resolve the finding
    findings = aggregate_findings(get_db_conn=_get_db, include_remediated=True)
    target = next((f for f in findings if f["finding_hash"] == finding_hash), None)
    if target is None:
        return jsonify({"error": "finding not found"}), 404

    title = f"[POA&M] [{target['severity']}] {target['rule_id']}: {target['title']}"
    body_lines = [
        f"**Finding hash:** `{target['finding_hash']}`",
        f"**Canvas:** {target.get('canvas_label', target['canvas_source'])} (`{target['canvas_source']}`)",
        f"**Rule ID:** `{target['rule_id']}`",
        f"**Severity:** `{target['severity']}`",
        f"**Affected entity:** `{target.get('affected_entity') or 'design'}`",
        "",
        "## Description",
        target.get("description") or "(no description)",
        "",
        "## Remediation Plan",
        "",
        "```",
        target.get("decision_rationale") or "(no plan attached — run Attach Plan first)",
        "```",
        "",
        "## Provenance",
        f"- Discovered at: {target.get('discovered_at') or 'unknown'}",
        "- Filed by: ICDEV\u2122 POA&M dashboard (`/poam`)",
    ]
    body = "\n".join(body_lines)

    cmd = [
        "gh", "issue", "create",
        "--title", title,
        "--body", body,
        "--label", "poam",
        "--label", f"severity:{target['severity']}",
        "--label", f"canvas:{target['canvas_source']}",
    ]
    for lbl in extra_labels:
        cmd += ["--label", lbl]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", check=False
        )
        if result.returncode != 0:
            return jsonify({
                "error": "gh issue create failed",
                "stderr": result.stderr.strip(),
            }), 502
        github_url = result.stdout.strip().splitlines()[-1]
    except FileNotFoundError:
        return jsonify({"error": "gh CLI not found — install GitHub CLI"}), 501

    # Append the URL to decision_rationale without clobbering the existing plan
    now = _now_iso()
    conn = _get_db()
    try:
        _upsert_approval_row(conn, target, now)
        existing_row = conn.execute(
            "SELECT decision_rationale FROM finding_approvals WHERE finding_hash = ?",
            (finding_hash,),
        ).fetchone()
        old = ""
        if existing_row:
            old = dict(existing_row).get("decision_rationale") or ""
        new_rationale = old + f"\n\n[GITHUB ISSUE] {github_url}"
        conn.execute(
            "UPDATE finding_approvals SET decision_rationale = ?, updated_at = ? "
            "WHERE finding_hash = ?",
            (new_rationale, now, finding_hash),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="approval_granted",
            actor=(session.get("user") or request.headers.get("X-User") or "anonymous"),
            action="poam.file_github_issue",
            details=json.dumps({
                "finding_hash": finding_hash,
                "rule_id": target["rule_id"],
                "github_url": github_url,
            }),
            project_id="dashboard-poam",
        )
    except Exception as exc:
        logger.warning("audit logging failed for file_github_issue %s: %s", finding_hash, exc)

    return jsonify({
        "ok": True,
        "finding_hash": finding_hash,
        "rule_id": target["rule_id"],
        "github_url": github_url,
    })


# ---------------------------------------------------------------------------
# GET /api/poam/simulation-sessions
# POST /api/poam/import-from-simulation
# ---------------------------------------------------------------------------

_SIM_SEVERITY_MAP = {
    "critical": "CAT1", "high": "CAT1", "cat1": "CAT1",
    "medium": "CAT2", "moderate": "CAT2", "warning": "CAT2", "cat2": "CAT2",
    "low": "CAT3", "info": "CAT3", "informational": "CAT3", "cat3": "CAT3",
}
_SIM_REMEDIATION_DAYS = {"CAT1": 15, "CAT2": 90, "CAT3": 180}


def _map_sim_severity(raw: str) -> str:
    return _SIM_SEVERITY_MAP.get((raw or "").lower().strip(), "CAT3")


def _extract_sim_findings(session_row: dict, runs: list[dict]) -> list[dict]:
    """Convert simulation session/runs into normalised finding dicts."""
    session_id = session_row["id"]
    canvas_type = (session_row.get("canvas_type") or "unknown").upper()
    topology_id = session_row.get("topology_id") or ""
    affected = topology_id or session_id

    findings: list[dict] = []
    seen_hashes: set[str] = set()

    for run in runs:
        run_id = run.get("id", "")
        steps_raw = run.get("steps") or "[]"
        try:
            steps = json.loads(steps_raw) if isinstance(steps_raw, str) else steps_raw
        except (json.JSONDecodeError, TypeError):
            steps = []

        for i, step in enumerate(steps if isinstance(steps, list) else []):
            if not isinstance(step, dict):
                continue
            step_type = (step.get("type") or step.get("kind") or "").lower()
            if step_type not in ("risk", "finding", "error", "warning", "vulnerability",
                                  "compliance", "failure", "alert"):
                continue
            raw_sev = step.get("severity") or step.get("risk_level") or step.get("level") or ""
            sev = _map_sim_severity(raw_sev or step_type)
            title = (step.get("message") or step.get("description") or step.get("text") or
                     f"Simulation {step_type} #{i + 1}")[:200]
            rule_id = (step.get("rule_id") or step.get("id") or
                       f"SIM-{canvas_type}-{i + 1:03d}")
            fhash = compute_finding_hash("tfw_simulation", rule_id, title, affected)
            if fhash in seen_hashes:
                continue
            seen_hashes.add(fhash)
            findings.append({
                "finding_hash": fhash,
                "rule_id": rule_id,
                "severity": sev,
                "title": title,
                "affected_entity": affected,
                "description": title,
                "run_id": run_id,
            })

        # If no step findings, surface the run summary as a finding
        if not findings:
            summary_text = (run.get("summary") or "").strip()
            if summary_text:
                rule_id = f"SIM-{canvas_type}-RUN-SUMMARY"
                title = summary_text[:200]
                fhash = compute_finding_hash("tfw_simulation", rule_id, title, affected)
                if fhash not in seen_hashes:
                    seen_hashes.add(fhash)
                    findings.append({
                        "finding_hash": fhash,
                        "rule_id": rule_id,
                        "severity": "CAT3",
                        "title": title,
                        "affected_entity": affected,
                        "description": summary_text,
                        "run_id": run_id,
                    })

    # Session-level fallback when there are no runs or no extractable findings
    if not findings:
        rule_id = f"SIM-{canvas_type}-SESSION"
        title = f"Simulation session {canvas_type}: {session_id[:12]}"
        fhash = compute_finding_hash("tfw_simulation", rule_id, title, affected)
        findings.append({
            "finding_hash": fhash,
            "rule_id": rule_id,
            "severity": "CAT3",
            "title": title,
            "affected_entity": affected,
            "description": f"Digital twin simulation run on {canvas_type} canvas.",
            "run_id": "",
        })

    return findings


@poam_api.route("/simulation-sessions", methods=["GET"])
def list_simulation_sessions():
    """List available simulation sessions for the import modal.

    Returns up to 50 most-recent nc_simulation_sessions rows with run counts.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT s.id, s.canvas_type, s.topology_id, s.mode, s.created_at, "
            "  COUNT(r.id) AS run_count "
            "FROM nc_simulation_sessions s "
            "LEFT JOIN nc_simulation_runs r ON r.session_id = s.id "
            "GROUP BY s.id "
            "ORDER BY s.created_at DESC "
            "LIMIT 50"
        ).fetchall()
        sessions = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_simulation_sessions: query failed: %s", exc)
        sessions = []
    finally:
        conn.close()
    return jsonify({"sessions": sessions})


@poam_api.route("/import-from-simulation", methods=["POST"])
def import_from_simulation():
    """Import POA&M findings from a simulation session.

    Body (JSON):
        session_id  - ID of an nc_simulation_sessions row

    Inserts finding_approvals rows with canvas_source='tfw_simulation'.
    Skips hashes that already exist (idempotent).

    Returns:
        { ok, session_id, inserted, skipped, findings_count }
    """
    payload = request.get_json(silent=True) or {}
    session_id = (payload.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    conn = _get_db()
    try:
        session_row = conn.execute(
            "SELECT id, canvas_type, topology_id, mode, created_at, metadata "
            "FROM nc_simulation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session_row:
            return jsonify({"error": "simulation session not found"}), 404
        session_row = dict(session_row)

        run_rows = conn.execute(
            "SELECT id, run_at, steps, summary FROM nc_simulation_runs "
            "WHERE session_id = ? ORDER BY run_at DESC LIMIT 10",
            (session_id,),
        ).fetchall()
        runs = [dict(r) for r in run_rows]

        findings = _extract_sim_findings(session_row, runs)

        now = _now_iso()
        inserted = 0
        skipped = 0

        for f in findings:
            existing = conn.execute(
                "SELECT finding_hash FROM finding_approvals WHERE finding_hash = ?",
                (f["finding_hash"],),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            sev = f["severity"]
            days = _SIM_REMEDIATION_DAYS.get(sev, 90)
            milestone = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
            description = f"[SIM] {f.get('description', f['title'])}\nMilestone: {milestone}"

            conn.execute(
                "INSERT INTO finding_approvals "
                "(finding_hash, canvas_source, rule_id, severity, title, "
                "affected_entity, decision, classification, created_at, updated_at, decision_rationale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f["finding_hash"],
                    "tfw_simulation",
                    f["rule_id"],
                    sev,
                    f["title"],
                    f["affected_entity"],
                    "pending",
                    "CUI // SP-CTI",
                    now,
                    now,
                    description,
                ),
            )
            inserted += 1

        conn.commit()
    finally:
        conn.close()

    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="finding_approval",
            actor=(session.get("user") or request.headers.get("X-User") or "icdev-simulation"),
            action="poam.import_from_simulation",
            details=json.dumps({
                "session_id": session_id,
                "inserted": inserted,
                "skipped": skipped,
            }),
            project_id="dashboard-poam",
        )
    except Exception as exc:
        logger.warning("audit logging failed for import_from_simulation: %s", exc)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "inserted": inserted,
        "skipped": skipped,
        "findings_count": len(findings),
    })
