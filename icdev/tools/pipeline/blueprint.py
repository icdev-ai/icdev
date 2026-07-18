
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV™ Pipeline Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /devops/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate pipeline_canvas.db, and feature flag
ICDEV_PIPELINE_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.pipeline.blueprint import create_pipeline_blueprint
    bp = create_pipeline_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/devops")
"""

import json
import os
import re
import uuid as _uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    g,
)

logger = get_logger("icdev.pipeline")

_PIPELINE_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _PIPELINE_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

# ── Import pipeline constants ─────────────────────────────────────────────────
from tools.pipeline.constants import (  # noqa: E402
    PIPELINE_STAGES,
    PIPELINE_OBJECTS,
    CSP_SERVICE_EQUIVALENCE,
    PIPELINE_COMPLIANCE_FRAMEWORKS,
    PIPELINE_COMPLIANCE_RULES,
    compute_owasp_coverage,
    estimate_pipeline_cost,
    estimate_execution_time,
    stage_from_type,
)
from tools.common.helpers import row_to_dict, now_isoformat  # noqa: E402
from tools.pipeline.db.init_db import get_connection, init_db  # noqa: E402
from tools.pipeline.runbooks import (  # noqa: E402
    get_all_runbooks as _pdc_get_all_runbooks,
    get_runbook_by_id as _pdc_get_runbook_by_id,
)
from tools.pipeline.sops import (  # noqa: E402
    get_all_sops as _pdc_get_all_sops,
    get_sop_by_id as _pdc_get_sop_by_id,
    create_sop as _pdc_create_sop,
    update_sop as _pdc_update_sop,
    delete_sop as _pdc_delete_sop,
    submit_for_review as _pdc_submit_for_review,
    approve_sop as _pdc_approve_sop,
    reject_sop as _pdc_reject_sop,
    seed_sops as _pdc_seed_sops,
)

from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: E402

# ── Optional imports from existing ICDEV modules ─────────────────────────────
try:
    from tools.compliance.slsa_attestation_generator import SLSA_LEVEL_REQUIREMENTS
except ImportError:
    SLSA_LEVEL_REQUIREMENTS = {}

try:
    import yaml

    _config_path = _ICDEV_ROOT / "args" / "pipeline_canvas_config.yaml"
    if _config_path.exists():
        with open(_config_path, encoding="utf-8") as f:
            PC_CONFIG = yaml.safe_load(f) or {}
    else:
        PC_CONFIG = {}
except Exception:
    PC_CONFIG = {}


# ── Helpers ───────────────────────────────────────────────────────────────────


class AuditUnavailable(Exception):
    """Raised by ``_audit_strict`` when an audit row cannot be persisted.

    Destructive routes catch this and fail closed (HTTP 500, no mutation) so a
    delete/approve/reject can never happen without an audit record (NIST AU).
    """


def _audit(action, entity_type, entity_id, details="", user_id=None):
    """Write an audit log entry (best-effort).

    Failures are logged at ERROR and swallowed — appropriate for non-destructive
    writes where losing the mutation would be worse than a missing audit row.
    Destructive routes must use ``_audit_strict`` instead (fail-closed).
    """
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_audit (action, entity_type, entity_id, details, user_id, ts) VALUES (%s, %s, %s, %s, %s, %s)",
            (action, entity_type, entity_id, details, user_id or session.get("user_id", "system"), now_isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Audit write failed: %s", exc)


def _audit_strict(action, entity_type, entity_id, details="", user_id=None, conn=None):
    """Write an audit row, RAISING ``AuditUnavailable`` on failure (fail-closed).

    Two modes:
      * ``conn`` provided — the INSERT runs on the caller's connection and is NOT
        committed here, so the caller can commit audit + mutation atomically. If
        the INSERT raises, the exception propagates as ``AuditUnavailable`` and
        the caller aborts (rolls back) the whole transaction — no un-audited
        mutation can commit.
      * ``conn`` is None — a dedicated connection is opened and committed here.
        Any failure raises ``AuditUnavailable`` so a route can fail closed BEFORE
        invoking a mutation that runs on a separate connection (e.g. SOP
        delete/approve/reject in tools/pipeline/sops.py).
    """
    owning = conn is None
    try:
        if owning:
            conn = get_connection()
        conn.execute(
            "INSERT INTO pc_audit (action, entity_type, entity_id, details, user_id, ts) VALUES (%s, %s, %s, %s, %s, %s)",
            (action, entity_type, entity_id, details, user_id or session.get("user_id", "system"), now_isoformat()),
        )
        if owning:
            conn.commit()
            conn.close()
    except Exception as exc:
        if owning and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        logger.error("Audit write failed (fail-closed): %s", exc)
        raise AuditUnavailable(str(exc)) from exc


# ── Module-level analysis helpers (also used by twin.py) ─────────────────────


def assess_slsa(nodes, edges):
    """Assess SLSA level from pipeline graph nodes/edges."""
    node_types = set(n.get("type", "") for n in nodes)
    evidence = {
        "build_process_documented": any(t.startswith("cicd-") or t.startswith("build-") for t in node_types),
        "version_controlled_source": any(t.startswith("scm-") for t in node_types),
        "build_service_authenticated": any(
            t in ("cicd-tekton", "gcp-cloudbuild", "cicd-gitlab") for t in node_types
        ),
        "build_as_code": any(t in ("cicd-tekton", "cicd-gitlab", "cicd-github-actions") for t in node_types),
        "ephemeral_environment": any(t in ("cicd-tekton", "gcp-cloudbuild") for t in node_types),
        "isolated_builds": any(t in ("build-kaniko", "build-buildah", "cicd-tekton") for t in node_types),
        "hermetic_builds": any(t == "build-bazel" for t in node_types),
        "reproducible_builds": any(t == "build-bazel" for t in node_types),
    }
    has_provenance = any(t in ("attest-slsa-gen", "attest-in-toto") for t in node_types)
    has_signing = any(t.startswith("sign-") for t in node_types)

    achieved = 0
    for level in range(4, -1, -1):
        reqs = SLSA_LEVEL_REQUIREMENTS.get(level, {}).get("requirements", [])
        if all(evidence.get(r, False) for r in reqs):
            achieved = level
            break

    if achieved >= 2 and not has_signing:
        achieved = min(achieved, 1)
    if achieved >= 1 and not has_provenance:
        achieved = min(achieved, 0)

    return {
        "achieved_level": achieved,
        "evidence": evidence,
        "has_provenance": has_provenance,
        "has_signing": has_signing,
    }


def run_compliance_check(nodes, edges):
    """Run pipeline compliance rules against graph nodes/edges."""
    node_types = set(n.get("type", "") for n in nodes)
    findings = []
    passed = 0
    failed = 0

    has_category = {}
    for cat, items in PIPELINE_OBJECTS.items():
        for item in items:
            if item["type"] in node_types:
                has_category.setdefault(cat, set()).add(item["type"])

    checks = {
        "branch_protection": any(t in ("branch-policy", "commit-signing") for t in node_types),
        "code_review_required": "branch-policy" in node_types,
        "hermetic_build": any(t in ("build-bazel", "build-kaniko") for t in node_types),
        "sbom_generated": any(t.startswith("sbom-") for t in node_types),
        "provenance_attestation": any(t in ("attest-slsa-gen", "attest-in-toto") for t in node_types),
        "sast_present": any(
            "sast" in t
            or t
            in ("scan-sonarqube", "scan-semgrep", "scan-codeql", "scan-bandit", "scan-spotbugs", "aws-codeguru")
            for t in node_types
        ),
        "sca_present": any(
            t in ("scan-sca", "scan-trivy", "scan-grype", "scan-snyk", "scan-dep-check") for t in node_types
        ),
        "container_scan_before_push": any(
            t
            in (
                "scan-container",
                "scan-trivy",
                "scan-anchore",
                "scan-neuvector",
                "aws-inspector",
                "az-defender",
                "gcp-artifact-analysis",
                "ibm-vuln-advisor",
            )
            for t in node_types
        ),
        "secret_detection_present": any(
            t in ("scan-secret", "scan-gitleaks", "scan-trufflehog", "scan-detect-secrets") for t in node_types
        ),
        "iac_scan_present": any(t in ("scan-iac", "scan-checkov", "scan-tfsec", "scan-kics") for t in node_types),
        "dast_present": any(t in ("scan-dast", "scan-zap", "scan-nuclei", "scan-burp") for t in node_types),
        "image_signing": any(t.startswith("sign-") for t in node_types),
        "vuln_threshold_gate": any(t in ("gate-vuln-threshold", "gate-automated") for t in node_types),
        "admission_controller": any(
            t
            in (
                "policy-opa",
                "policy-kyverno",
                "policy-gatekeeper",
                "policy-kubewarden",
                "gcp-binary-auth",
                "ibm-portieris",
            )
            for t in node_types
        ),
        "prod_approval_gate": any(t in ("gate-manual", "gate-deploy-window") for t in node_types),
        "progressive_delivery": any(
            t in ("deploy-canary", "deploy-bluegreen", "deploy-feature-flag") for t in node_types
        ),
        "cds_for_cross_domain": not any(t.startswith("boundary-") for t in node_types)
        or any(t.startswith("cds-") for t in node_types),
        "runtime_monitoring": any(
            t.startswith("mon-")
            or t in ("aws-cloudwatch", "az-monitor", "gcp-monitoring", "aws-guardduty", "gcp-scc")
            for t in node_types
        ),
        "evidence_collection": any(t in ("comp-evidence", "comp-oscal") for t in node_types),
        "audit_logging": True,
        "airgap_vuln_mirror": not any(
            t.startswith("pipeline-sipr") or t.startswith("pipeline-jwics") for t in node_types
        )
        or "vuln-db-mirror" in node_types,
        "airgap_package_mirror": not any(
            t.startswith("pipeline-sipr") or t.startswith("pipeline-jwics") for t in node_types
        )
        or "package-mirror" in node_types,
        "slo_defined": any(
            t.startswith("sre-slo")
            or t in ("sre-openslo", "sre-sloth", "sre-pyrra", "aws-cw-slo", "gcp-service-mon")
            for t in node_types
        ),
        "incident_mgmt_present": any(
            t.startswith("sre-incident")
            or t in ("sre-pagerduty", "sre-grafana-oncall", "sre-opsgenie", "aws-incident-mgr")
            for t in node_types
        ),
        "runbooks_present": any(t in ("sre-runbook", "sre-self-heal") for t in node_types),
        "chaos_present": any(
            t in ("sre-chaos", "sre-chaos-litmus", "aws-fis", "az-chaos-studio") for t in node_types
        ),
        "dora_tracked": any(t.startswith("sre-dora") for t in node_types),
    }

    for rule in PIPELINE_COMPLIANCE_RULES:
        check_key = rule["check"]
        if checks.get(check_key, False):
            passed += 1
        else:
            failed += 1
            findings.append(
                {
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "frameworks": rule["frameworks"],
                }
            )

    return {"passed": passed, "failed": failed, "findings": findings, "total": passed + failed}


# ── graph_json validation / parsing (stored-XSS + corruption defense) ─────────


def validate_graph_json_payload(raw):
    """Validate + canonicalize an incoming graph_json payload at the write boundary.

    Accepts a JSON string or a dict. Requires a JSON object containing ``nodes``
    and ``edges`` lists. Returns the canonical ``json.dumps()`` string so only a
    well-formed graph is ever persisted (defeats stored-XSS payloads smuggled in
    as an arbitrary string). Raises ``ValueError`` with a human-readable message
    on any violation; callers translate that into HTTP 422.
    """
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            raise ValueError("graph_json is not valid JSON")
    elif isinstance(raw, dict):
        obj = raw
    else:
        raise ValueError("graph_json must be a JSON object")
    if not isinstance(obj, dict):
        raise ValueError("graph_json must be a JSON object")
    if not isinstance(obj.get("nodes"), list) or not isinstance(obj.get("edges"), list):
        raise ValueError("graph_json must contain nodes[] and edges[]")
    return json.dumps(obj)


def _graph_json_sha256(raw):
    """Stable sha256 over a graph_json value, insensitive to key ordering.

    Used by the PUT save-path (pdx-perf-01) to decide whether the graph actually
    changed. When it did not, ALL post-save side-effects (KG reindex, auto-
    snapshot, Security-Canvas assessment, KG rebuild, provenance) are skipped —
    they are amplified by the client's auto-save timer and are pure no-ops when
    the graph is identical. Falls back to hashing the raw string for non-JSON.
    """
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        canon = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True, default=str)
    import hashlib as _hashlib
    return _hashlib.sha256(canon.encode("utf-8")).hexdigest()


def parse_graph_json(raw):
    """Defensively parse a stored graph_json value into a graph dict.

    Raises ``ValueError`` if the stored blob is not valid JSON or not an object.
    Request handlers translate that into HTTP 422 ('corrupt graph') rather than
    surfacing an unhandled 500.
    """
    try:
        graph = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (ValueError, TypeError):
        raise ValueError("corrupt graph")
    if not isinstance(graph, dict):
        raise ValueError("corrupt graph")
    return graph


# ── RBAC — role sets + session-derived identity (pdx-sec-03) ──────────────────
#
# Every route below runs inside the ICDEV dashboard, whose auth layer
# (tools/dashboard/auth.py::_auth_before_request) populates ``g.current_user``
# with a ``role`` and sets ``session['user_id']``. Before this fix the pipeline
# canvas only checked ``session['user_id']`` (pc_login_required), so ANY
# authenticated user of ANY role could delete pipelines, approve/reject/delete
# SOPs, and generate deploy bundles. These sets mirror the established dashboard
# RBAC_MATRIX:
#   * PC_WRITE_ROLES  == RBAC_MATRIX["cicd"] — the pipeline canvas IS the CI/CD
#     design surface; developer/pm/isso/admin may mutate designs.
#   * PC_ELEVATED_ROLES == RBAC_MATRIX["gateway"] — the most-restricted
#     operational surface; only platform admin + the ISSO security authority may
#     perform destructive / governance actions (pipeline DELETE, SOP
#     approve/reject/delete).
# Roles are always sourced from server-side auth state (g.current_user, then the
# signed session cookie) — NEVER from the request body. Missing/unknown role
# fails closed (403).
PC_WRITE_ROLES = ("admin", "isso", "pm", "developer")
PC_ELEVATED_ROLES = ("admin", "isso")

# ── Input-validation enums (pdx-fix-03) ───────────────────────────────────────
#
# The pipeline canvas is an IL4 surface (min_il: IL4 in the component registry,
# "CUI // SP-CTI" banner). constants.py exposes no classification/CSP enum, so the
# allowed sets are defined here from the values actually used across the schema
# (tools/pipeline/db/init_db.py) and the UI:
#   * classification — pipelines.classification defaults to 'public'; snippets carry
#     'public' | 'CUI' | 'SECRET'; the SOP/boundary banner uses 'CUI // SP-CTI'.
#   * target_csp — deploy_generator recognizes aws/azure/gcp/oci/ibm/on_prem plus
#     the 'auto' detector; pipelines.target_csp defaults to 'generic'.
# Default CHOICE (documented, pdx-fix-03): we KEEP the existing create defaults of
# classification='public' and target_csp='generic' rather than forcing CUI. This
# preserves backward compatibility (existing rows, the PUT-as-PATCH partial-update
# contract, and the put_partial regression tests that round-trip non-enum csp
# labels like 'aws-il5'/'onprem-dod' on UPDATE). Enum validation is enforced at
# CREATE only; PUT keeps free-form PATCH semantics so it never rejects a value an
# older client legitimately stored. 'public'/'generic' are members of the allowed
# sets, so the defaults always pass.
PC_ALLOWED_CLASSIFICATIONS = frozenset(
    {"public", "CUI", "CUI // SP-CTI", "SECRET"}
)
PC_ALLOWED_TARGET_CSPS = frozenset(
    {"generic", "auto", "aws", "azure", "gcp", "oci", "ibm", "on_prem"}
)

# ── Child-row delete order for pipeline DELETE (pdx-data-02) ───────────────────
#
# The pipeline FK children in tools/pipeline/db/init_db.py are declared WITHOUT
# ON DELETE CASCADE (only pc_collab_sessions has it). SQLite runs with
# PRAGMA foreign_keys=ON and PostgreSQL enforces FKs, so deleting a pipeline that
# still has children raises IntegrityError (HTTP 500). Because auto-snapshot fires
# on every save, every real pipeline has at least one pdc_snapshots child — so the
# DELETE always 500'd. We delete child rows explicitly (deterministic across both
# backends, and — unlike editing the DDL to CASCADE — it also works on already
# deployed DBs whose tables were created before any CASCADE clause). Order matters:
# rows are deleted children-first so intra-canvas FKs hold —
# pc_compliance_findings before pc_compliance_checks (findings REFERENCES checks),
# and pdc_simulations before pdc_snapshots (simulations REFERENCES snapshots).
# pc_stages and pc_project_pipelines also REFERENCE pipelines(id); they are
# included for completeness so no residual child can raise IntegrityError.
# pc_audit is intentionally ABSENT: it is an append-only audit trail and has NO FK
# to pipelines(id) (entity_id is a plain TEXT value), so its rows survive the
# delete and remain queryable by the deleted pipeline's id (NIST AU).
# (table_name, fk_column) — column is the pipeline reference on that table.
_PC_CHILD_DELETE_TABLES = (
    ("pc_compliance_findings", "pipeline_id"),
    ("pc_compliance_checks", "pipeline_id"),
    ("pdc_simulations", "pipeline_id"),
    ("pdc_snapshots", "pipeline_id"),
    ("pc_versions", "pipeline_id"),
    ("pc_boundaries", "pipeline_id"),
    ("pc_change_requests", "pipeline_id"),
    ("pc_stages", "pipeline_id"),
    ("pc_project_pipelines", "pipeline_id"),
    ("pc_collab_sessions", "design_id"),
)


def _pc_current_role():
    """Resolve the caller's role from server-side auth state only.

    Prefers ``g.current_user['role']`` (set by the dashboard auth layer); falls
    back to the signed-session ``role`` claim. Returns ``""`` when no role can be
    established so callers fail closed. Never reads the request body/query.
    """
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        role = user.get("role") or ""
    elif user is not None:
        try:
            role = user["role"] or ""
        except (KeyError, TypeError, IndexError):
            role = ""
    else:
        role = ""
    if not role:
        role = session.get("role", "") or ""
    return role


def _pc_identity():
    """Resolve the caller's identity from server-side auth state only.

    Used for audit attribution, SOP approver identity, and collaboration
    membership. Sourced from ``g.current_user`` (id → display_name) then the
    session cookie ``user_id`` — NEVER from the request body (defeats identity
    spoofing / self-approval bypass). Returns ``""`` when unknown.
    """
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        ident = user.get("id") or user.get("display_name") or ""
        if ident:
            return ident
    return session.get("user_id", "") or ""


def pc_role_required(*roles):
    """Fail-closed RBAC decorator for pipeline write / governance routes.

    Restricts the wrapped route to callers whose server-derived role
    (``g.current_user`` / session — never the request body) is in ``roles``.
    Missing or unknown role -> 403. Stack this *below* ``@pc_login_required`` so
    an unauthenticated caller is rejected with 401 by the auth gate first.
    """
    allowed = frozenset(roles)

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            role = _pc_current_role()
            if role not in allowed:
                _audit(
                    "RBAC_DENY",
                    "route",
                    request.path,
                    f"required={sorted(allowed)} had={role or 'none'}",
                    user_id=_pc_identity() or "anonymous",
                )
                return jsonify({"error": "Forbidden: insufficient role"}), 403
            return f(*args, **kwargs)

        return decorated

    return decorator


# ── Blueprint Factory ─────────────────────────────────────────────────────────


def create_pipeline_blueprint():
    """Create and return the Pipeline Design Canvas Blueprint.

    Feature-flag gate, aligned with the unified component registry (pdx-fix-03 /
    deferred pdx-ops-01). The registry (args/component_registry.yaml, key ``pdc``)
    declares ``env_flag: ICDEV_PDC_ENABLED`` with ``default_enabled: false`` and
    lists ``ICDEV_PIPELINE_ENABLED`` as a legacy ``extra_env_flag``. Previously
    this factory gated on ``ICDEV_PIPELINE_ENABLED`` defaulting to *true*, so the
    canvas was silently ON while the registry considered it OFF.

    New rule — the canvas activates only if a flag is EXPLICITLY enabled:
        enabled = truthy(ICDEV_PDC_ENABLED, default False)
                  OR (ICDEV_PIPELINE_ENABLED explicitly set AND truthy)
    The silent default is now registry-consistent OFF. Truth table:
        PDC unset,  PIPELINE unset  -> OFF   (registry default)
        PDC true,   PIPELINE *      -> ON
        PDC unset,  PIPELINE true   -> ON    (legacy explicit)
        PDC false,  PIPELINE true   -> ON    (legacy explicit truthy)
        PDC *,      PIPELINE false  -> OFF unless PDC true
        PDC false,  PIPELINE unset  -> OFF

    Returns None (canvas disabled) when neither flag is explicitly enabled.
    """
    def _truthy(val):
        return str(val).strip().strip('"').strip("'").lower() in ("true", "1", "yes", "on")

    pdc_raw = os.environ.get("ICDEV_PDC_ENABLED")
    legacy_raw = os.environ.get("ICDEV_PIPELINE_ENABLED")
    # Primary flag: registry-consistent default OFF (only ON when truthy).
    pdc_on = _truthy(pdc_raw) if pdc_raw is not None else False
    # Legacy flag: activates ONLY when explicitly set truthy (no silent default-on).
    legacy_on = legacy_raw is not None and _truthy(legacy_raw)
    if not (pdc_on or legacy_on):
        logger.info(
            "Pipeline Canvas disabled (ICDEV_PDC_ENABLED=%s, ICDEV_PIPELINE_ENABLED=%s)",
            pdc_raw, legacy_raw,
        )
        return None

    # Initialize DB
    try:
        init_db()
    except Exception as exc:
        logger.warning("Pipeline DB init failed: %s", exc)

    try:
        from tools.pipeline import bus_subscriber as _pdc_bus
        _pdc_bus.register()
    except Exception as exc:
        logger.warning("PDC bus subscriber registration failed: %s", exc)

    bp = Blueprint(
        "pipeline_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth decorator ────────────────────────────────────────────────────
    def pc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if request.is_json or request.path.startswith("/devops/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)

        return decorated

    # ── Context processor ─────────────────────────────────────────────────
    @bp.context_processor
    def inject_pc_context():
        user = None
        try:
            user = getattr(g, "current_user", None)
        except RuntimeError:
            pass
        return {
            "classification_banner": PC_CONFIG.get("app", {}).get("classification", ""),
            "current_user": user,
        }

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @pc_login_required
    def pc_index():
        conn = get_connection()
        pipelines = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, description, classification, target_csp, "
                "created_at, updated_at FROM pipelines ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
        ]
        templates = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM pc_templates ORDER BY category, name"
            ).fetchall()
        ]
        snippets = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM pc_snippets ORDER BY category, name"
            ).fetchall()
        ]
        conn.close()
        return render_template(
            "pipeline/index.html",
            pipelines=pipelines,
            templates=templates,
            snippets=snippets,
            stages=PIPELINE_STAGES,
        )

    @bp.route("/canvas/new")
    @pc_login_required
    def pc_new_canvas():
        return render_template(
            "pipeline/canvas.html",
            pipeline_id="new",
            pipeline_name="Untitled Pipeline",
            graph_json=json.dumps({"nodes": [], "edges": []}),
            stages=PIPELINE_STAGES,
            objects=PIPELINE_OBJECTS,
        )

    @bp.route("/canvas/<pipe_id>")
    @pc_login_required
    def pc_edit_canvas(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return redirect("/devops/canvas/new")
        pipe = row_to_dict(row)
        return render_template(
            "pipeline/canvas.html",
            pipeline_id=pipe["id"],
            pipeline_name=pipe["name"],
            graph_json=pipe["graph_json"],
            classification=pipe.get("classification", "public"),
            design=pipe,
            stages=PIPELINE_STAGES,
            objects=PIPELINE_OBJECTS,
        )

    # ══════════════════════════════════════════════════════════════════════
    # API — PIPELINE CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/health")
    def pc_health():
        return jsonify({"status": "ok", "module": "pipeline_canvas"})

    @bp.route("/api/pipelines", methods=["GET"])
    @pc_login_required
    def pc_api_list():
        # pdx-perf-01: this list was unbounded. Add limit (default 50, max 200)
        # and offset paging. Validate the ints FIRST (garbage -> 400), then clamp
        # — mirrors the pc_api_ai_trace pattern (pdx-fix-03).
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            return jsonify({"error": "limit must be an integer"}), 400
        try:
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "offset must be an integer"}), 400
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, target_csp, "
            "created_at, updated_at FROM pipelines ORDER BY updated_at DESC "
            "LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/pipelines", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_create():
        data = request.get_json(force=True, silent=True) or {}
        # Input validation
        if len(json.dumps(data)) > 5_000_000:  # 5MB max
            return jsonify({"error": "Payload too large"}), 413
        pipe_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Pipeline")[:200]  # Limit name length
        # Validate graph_json at the write boundary: reject anything that is not a
        # well-formed {nodes:[], edges:[]} object, and persist a canonical dump so
        # a stored-XSS payload can never round-trip into the canvas renderer.
        try:
            graph_json = validate_graph_json_payload(
                data.get("graph_json", '{"nodes":[],"edges":[]}')
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        # Validate classification / target_csp against the allowed enums. Unknown
        # values are rejected (422) rather than silently persisted. Defaults
        # 'public'/'generic' are members of the sets, so an omitted field passes.
        classification = data.get("classification", "public")
        target_csp = data.get("target_csp", "generic")
        if classification not in PC_ALLOWED_CLASSIFICATIONS:
            return jsonify({
                "error": f"invalid classification: {classification!r}",
                "allowed": sorted(PC_ALLOWED_CLASSIFICATIONS),
            }), 422
        if target_csp not in PC_ALLOWED_TARGET_CSPS:
            return jsonify({
                "error": f"invalid target_csp: {target_csp!r}",
                "allowed": sorted(PC_ALLOWED_TARGET_CSPS),
            }), 422
        logger.info("Creating pipeline: %s (%s)", name, pipe_id)
        conn = get_connection()
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, classification, "
            "target_csp, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                pipe_id,
                name,
                data.get("description", ""),
                graph_json,
                classification,
                target_csp,
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "pipeline", pipe_id, name)
        # Hook: refresh PDC KG so /ask reflects the new design immediately.
        # Guarded (pdx-perf-01): an ImportError / KG failure must NOT 500 a create
        # whose row already committed above.
        try:
            from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
            reindex_canvas_on_save("pdc")
        except Exception as exc:
            logger.warning("PDC KG reindex hook failed on create (%s): %s", pipe_id, exc)
        return jsonify({"id": pipe_id, "name": name}), 201

    @bp.route("/api/pipelines/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(row_to_dict(row))

    @bp.route("/api/pipelines/<pipe_id>", methods=["PUT"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_update(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        logger.info("Updating pipeline: %s", pipe_id)
        conn = get_connection()

        # Fetch the current graph_json BEFORE the UPDATE so we can (a) 404 early
        # on an unknown id and (b) decide whether the save actually changed the
        # graph. The five post-save side-effects below (KG reindex, auto-snapshot,
        # Security-Canvas assessment, KG rebuild, blockchain provenance) are heavy
        # and are hammered by the client's 3s auto-save timer — they are pure
        # no-ops when the graph is unchanged, so we skip ALL of them in that case
        # (pdx-perf-01).
        existing_row = conn.execute(
            "SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)
        ).fetchone()
        if existing_row is None:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        old_graph_json = (
            existing_row["graph_json"]
            if not isinstance(existing_row, (list, tuple))
            else existing_row[0]
        )

        # PUT-as-PATCH semantics: only update fields the client explicitly sent.
        # The auto-save timer in pipeline-canvas.js sends {name, graph_json} only;
        # a partial PUT must NOT clobber description/classification/target_csp
        # back to defaults. (Fixes bug where opening a canvas auto-reset
        # classification="public", target_csp="generic", description="".)
        sets = []
        params = []
        new_graph_json = None
        for col in ("name", "description", "graph_json", "classification", "target_csp"):
            if col in data:
                if col == "graph_json":
                    # Validate + canonicalize at the write boundary (stored-XSS defense).
                    try:
                        value = validate_graph_json_payload(data[col])
                    except ValueError as exc:
                        conn.close()
                        return jsonify({"error": str(exc)}), 422
                    new_graph_json = value
                else:
                    value = data[col]
                sets.append(f"{col}=%s")
                params.append(value)
        if not sets:
            conn.close()
            return jsonify({"error": "No updatable fields provided"}), 400
        sets.append("updated_at=%s")
        params.append(now_isoformat())
        params.append(pipe_id)
        cur = conn.execute(
            f"UPDATE pipelines SET {', '.join(sets)} WHERE id=%s",
            params,
        )
        # 404 when the target pipeline does not exist: the UPDATE matched no rows.
        # (Belt-and-suspenders with the early SELECT above — covers a concurrent
        # delete between the SELECT and the UPDATE.)
        if getattr(cur, "rowcount", -1) == 0:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        conn.commit()
        conn.close()
        _audit("UPDATE", "pipeline", pipe_id, data.get("name", ""))

        # Did the graph actually change? Only then do we run the post-save hooks.
        # A metadata-only PUT (no graph_json in the payload) never touches the
        # graph, and a graph_json that is byte-equivalent to what was stored is a
        # no-op save from the auto-save timer.
        graph_changed = new_graph_json is not None and (
            _graph_json_sha256(new_graph_json) != _graph_json_sha256(old_graph_json)
        )

        sdc_assessment = None
        if not graph_changed:
            logger.info(
                "Pipeline %s save: graph_json unchanged — skipping post-save hooks", pipe_id
            )
        else:
            # Hook: refresh PDC KG so /ask reflects the edit immediately (guarded:
            # an ImportError must not 500 a save whose row already committed).
            try:
                from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
                reindex_canvas_on_save("pdc")
            except Exception as exc:
                logger.warning("PDC KG reindex hook failed on update (%s): %s", pipe_id, exc)
            # Hook: auto-snapshot on pipeline save (PDC twin). take_snapshot itself
            # de-dups and bounds retention (pdx-perf-01).
            try:
                from tools.pipeline.twin import take_snapshot as _twin_snapshot
                _twin_snapshot(
                    pipe_id,
                    label=f"auto-save-{now_isoformat()[:10]}",
                    user_id=session.get("user_id", "system"),
                )
            except Exception as exc:
                logger.warning("PDC auto-snapshot hook failed (%s): %s", pipe_id, exc)
            # Hook: notify Security Design Canvas of pipeline change
            try:
                from tools.security_canvas.agent import on_pdc_pipeline_saved

                graph_raw = data.get("graph_json", "{}")
                graph = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
                result = on_pdc_pipeline_saved(pipe_id, graph)
                if result and result.get("status") != "error":
                    sdc_assessment = {
                        "risk_score": result.get("risk_score"),
                        "posture_grade": result.get("posture_grade"),
                        "cat1_count": result.get("cat1_count", 0),
                        "cat2_count": result.get("cat2_count", 0),
                        "cat3_count": result.get("cat3_count", 0),
                        "total_findings": result.get("total_findings", 0),
                    }
            except Exception as exc:
                logger.warning("PDC Security Canvas hook failed (%s): %s", pipe_id, exc)
            # Incremental KG update: re-extract only if graph_json changed
            try:
                from tools.canvas.kg_builder import rebuild_canvas_kg

                rebuild_canvas_kg("pdc", pipe_id)
            except Exception as exc:
                logger.warning("PDC KG rebuild hook failed (%s): %s", pipe_id, exc)
            # Blockchain provenance
            try:
                from tools.canvas.provenance import register_canvas_provenance
                register_canvas_provenance(
                    canvas_key="pdc",
                    design_id=pipe_id,
                    graph_json=data.get("graph_json", {}),
                    project_id=data.get("project_id", ""),
                )
            except Exception as exc:
                logger.warning("PDC provenance hook failed (%s): %s", pipe_id, exc)
        resp = {"updated": True}
        if sdc_assessment is not None:
            resp["sdc_assessment"] = sdc_assessment
        return jsonify(resp)

    @bp.route("/api/pipelines/<pipe_id>", methods=["DELETE"])
    @pc_login_required
    @pc_role_required(*PC_ELEVATED_ROLES)
    def pc_api_delete(pipe_id):
        logger.info("Deleting pipeline: %s", pipe_id)
        conn = get_connection()
        # Fail-closed (NIST AU): the child-row deletes, the pipeline delete and the
        # audit INSERT all share THIS transaction. If the audit raises, the whole
        # cascade is rolled back and never commits — no un-audited delete, and no
        # orphaned/partial child removal.
        try:
            # Explicit child-row deletes (see _PC_CHILD_DELETE_TABLES) — the DDL has
            # no ON DELETE CASCADE, and every real pipeline has children (auto-
            # snapshot fires on save), so without these the FKs raise IntegrityError.
            for _tbl, _col in _PC_CHILD_DELETE_TABLES:
                conn.execute(f"DELETE FROM {_tbl} WHERE {_col}=%s", (pipe_id,))
            cur = conn.execute("DELETE FROM pipelines WHERE id=%s", (pipe_id,))
            # 404 when the pipeline row did not exist: nothing was deleted. Roll back
            # the (no-op) child deletes and return without writing a delete-audit row
            # for a pipeline that never existed.
            if getattr(cur, "rowcount", -1) == 0:
                try:
                    conn.rollback()
                except Exception:
                    pass
                conn.close()
                return jsonify({"error": "Not found"}), 404
            # pc_audit is append-only and is NEVER deleted here (no FK to
            # pipelines(id)) — audit rows for this pipeline id remain after delete.
            _audit_strict("DELETE", "pipeline", pipe_id, "", user_id=_pc_identity(), conn=conn)
            conn.commit()
        except AuditUnavailable:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
            return jsonify({"error": "audit trail unavailable"}), 500
        conn.close()
        return jsonify({"deleted": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — TEMPLATES & SNIPPETS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/templates", methods=["GET"])
    @pc_login_required
    def pc_api_list_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, tags FROM pc_templates ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/templates/<tpl_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_templates WHERE id=%s", (tpl_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(d)

    @bp.route("/api/templates/<tpl_id>/load", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_load_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_templates WHERE id=%s", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        tpl = row_to_dict(row)
        pipe_id = str(_uuid.uuid4())
        # Propagate the template's classification / target_csp onto the new
        # pipeline instead of dropping them (which defaulted every template-derived
        # pipeline to public/generic even for DoD/IL5 templates). `.get()` falls
        # back to the create defaults when the source row lacks the column (the
        # current pc_templates schema has no classification/target_csp column, so
        # this is forward-compatible: it activates as soon as those columns exist
        # without re-touching this route).
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, template_id, "
            "classification, target_csp, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                pipe_id,
                f"{tpl['name']} (copy)",
                tpl.get("description", ""),
                tpl["graph_json"],
                tpl_id,
                tpl.get("classification") or "public",
                tpl.get("target_csp") or "generic",
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_TEMPLATE", "pipeline", pipe_id, tpl["name"])
        return jsonify({"id": pipe_id, "name": f"{tpl['name']} (copy)"}), 201

    @bp.route("/api/snippets", methods=["GET"])
    @pc_login_required
    def pc_api_list_snippets():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, classification_level, "
            "impact_level, slsa_level, tags FROM pc_snippets ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/snippets/<snip_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_snippet(snip_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_snippets WHERE id=%s", (snip_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(d)

    @bp.route("/api/snippets/<snip_id>/load", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_load_snippet(snip_id):
        """Create a new pipeline from a snippet (like template load)."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_snippets WHERE id=%s", (snip_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        snip = row_to_dict(row)
        pipe_id = str(_uuid.uuid4())
        # Propagate the snippet's classification (from classification_level) and
        # target_csp onto the new pipeline. Snippets carry real DoD levels
        # (public/CUI/SECRET, IL2–IL6), so dropping them defaulted IL5/SECRET
        # snippets to public. target_csp defaults to generic when the snippet row
        # has no such column (current schema).
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, classification, "
            "target_csp, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                pipe_id,
                f"{snip['name']} (copy)",
                snip.get("description", ""),
                snip["graph_json"],
                snip.get("classification_level", "CUI"),
                snip.get("target_csp") or "generic",
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_SNIPPET", "pipeline", pipe_id, snip["name"])
        return jsonify({"id": pipe_id, "name": f"{snip['name']} (copy)"}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — OBJECT LIBRARY & CSP EQUIVALENCE
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipeline-objects")
    @pc_login_required
    def pc_api_objects():
        return jsonify(PIPELINE_OBJECTS)

    @bp.route("/api/pipeline-stages")
    @pc_login_required
    def pc_api_stages():
        return jsonify(PIPELINE_STAGES)

    @bp.route("/api/csp-equivalence")
    @pc_login_required
    def pc_api_csp_equivalence():
        return jsonify(CSP_SERVICE_EQUIVALENCE)

    @bp.route("/api/csp-equivalence/<service_key>")
    @pc_login_required
    def pc_api_csp_equivalence_detail(service_key):
        eq = CSP_SERVICE_EQUIVALENCE.get(service_key)
        if not eq:
            return jsonify({"error": "Unknown service key"}), 404
        return jsonify(eq)

    @bp.route("/api/csp-equivalence/<service_key>/<target_csp>")
    @pc_login_required
    def pc_api_csp_equiv_single(service_key, target_csp):
        eq = CSP_SERVICE_EQUIVALENCE.get(service_key, {})
        csp_data = eq.get(target_csp)
        if not csp_data:
            return jsonify({"error": f"No mapping for {service_key}/{target_csp}"}), 404
        return jsonify(csp_data)

    # ══════════════════════════════════════════════════════════════════════
    # API — ANALYSIS (calls existing ICDEV tools)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipelines/<pipe_id>/analyze", methods=["POST"])
    @pc_login_required
    def pc_api_analyze(pipe_id):
        """Run analysis on a pipeline. Body: {analysis_type: "security_coverage"|"cost"|"execution_time"|"slsa"|"compliance"|"antipatterns"}."""
        logger.info("Analyzing pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404

        try:
            graph = parse_graph_json(row["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]

        data = request.get_json(force=True, silent=True) or {}
        analysis_type = data.get("analysis_type", "security_coverage")

        if analysis_type == "security_coverage":
            result = compute_owasp_coverage(node_types)
        elif analysis_type == "cost":
            runs = data.get("runs_per_month", 500)
            result = estimate_pipeline_cost(node_types, runs)
        elif analysis_type == "execution_time":
            result = estimate_execution_time(nodes, edges)
        elif analysis_type == "slsa":
            result = _assess_slsa(nodes, edges)
        elif analysis_type == "compliance":
            result = _run_compliance_check(nodes, edges)
        elif analysis_type == "antipatterns":
            try:
                from tools.pipeline.antipattern_detector import detect_antipatterns

                result = {"findings": detect_antipatterns(nodes, edges), "total": 0}
                result["total"] = len(result["findings"])
            except Exception as exc:
                result = {"findings": [], "total": 0, "error": str(exc)}
        elif analysis_type == "governance":
            result = _compute_pdc_governance({"nodes": nodes, "edges": edges})
        else:
            return jsonify({"error": f"Unknown analysis type: {analysis_type}"}), 400

        _audit("ANALYZE", "pipeline", pipe_id, analysis_type)
        _summary = str(result.get("score", result.get("total", result.get("coverage", ""))))
        record_canvas_decision(
            canvas_type="pdc",
            record_id=pipe_id,
            decision_type="compliance_finding",
            decision=f"{analysis_type}: {_summary}",
            rationale=f"Nodes analyzed: {len(nodes)}",
            model_used=None,
        )
        return jsonify({"analysis_type": analysis_type, "result": result})

    # ══════════════════════════════════════════════════════════════════════
    # API — COMPLIANCE AUDIT
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<pipe_id>/audit", methods=["POST"])
    @pc_login_required
    def pc_api_compliance_audit(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        try:
            graph = parse_graph_json(row["graph_json"])
        except ValueError:
            conn.close()
            return jsonify({"error": "corrupt graph"}), 422
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        result = _run_compliance_check(nodes, edges)

        # Persist findings
        check_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO pc_compliance_checks (id, pipeline_id, check_type, passed, failed, findings_json, ran_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                check_id,
                pipe_id,
                "full_audit",
                result["passed"],
                result["failed"],
                json.dumps(result["findings"]),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("COMPLIANCE_AUDIT", "pipeline", pipe_id, f"passed={result['passed']}, failed={result['failed']}")
        # Blockchain provenance for assessment
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="pdc",
                design_id=pipe_id,
                assessment_data=result,
                project_id="",
            )
        except Exception:
            pass
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — VERSIONS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/versions/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_versions(pipe_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, version_num, label, created_by, notes, created_at "
            "FROM pc_versions WHERE pipeline_id=%s ORDER BY version_num DESC",
            (pipe_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<pipe_id>", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_create_version(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        max_ver = conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) FROM pc_versions WHERE pipeline_id=%s", (pipe_id,)
        ).fetchone()[0]
        data = request.get_json(force=True, silent=True) or {}
        ver_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO pc_versions (id, pipeline_id, version_num, label, graph_json, created_by, notes, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                ver_id,
                pipe_id,
                max_ver + 1,
                data.get("label", f"v{max_ver + 1}"),
                row["graph_json"],
                session.get("user_id", "system"),
                data.get("notes", ""),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(
            "CREATE_VERSION", "version", ver_id,
            f"pipeline={pipe_id} v{max_ver + 1}", user_id=_pc_identity(),
        )
        return jsonify({"id": ver_id, "version_num": max_ver + 1}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — BOUNDARIES (Security Zones / Stage Fencing)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/boundaries/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_boundaries(pipe_id):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM pc_boundaries WHERE pipeline_id=%s", (pipe_id,)).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/boundaries/<pipe_id>", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_create_boundary(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        bid = str(_uuid.uuid4())
        # Validate the numeric geometry fields: a client-supplied non-numeric
        # value (e.g. pos_x="abc") returns 400 rather than a DB type error / 500.
        _num_defaults = (
            ("fill_opacity", 0.08, float),
            ("pos_x", 0, float),
            ("pos_y", 0, float),
            ("width", 400, float),
            ("height", 300, float),
        )
        nums = {}
        for field, default, caster in _num_defaults:
            raw = data.get(field, default)
            try:
                nums[field] = caster(raw)
            except (TypeError, ValueError):
                return jsonify({"error": f"{field} must be a number"}), 400
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_boundaries (id, pipeline_id, label, classification, color, "
            "fill_opacity, node_ids, boundary_type, pos_x, pos_y, width, height) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                bid,
                pipe_id,
                data.get("label", "Stage Boundary"),
                data.get("classification", "CUI"),
                data.get("color", "#e94560"),
                nums["fill_opacity"],
                json.dumps(data.get("node_ids", [])),
                data.get("boundary_type", "security_zone"),
                nums["pos_x"],
                nums["pos_y"],
                nums["width"],
                nums["height"],
            ),
        )
        conn.commit()
        conn.close()
        _audit(
            "CREATE_BOUNDARY", "boundary", bid,
            f"pipeline={pipe_id} label={data.get('label', 'Stage Boundary')}",
            user_id=_pc_identity(),
        )
        return jsonify({"id": bid}), 201

    @bp.route("/api/boundaries/<pipe_id>/<bid>", methods=["DELETE"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_delete_boundary(pipe_id, bid):
        conn = get_connection()
        conn.execute("DELETE FROM pc_boundaries WHERE id=%s AND pipeline_id=%s", (bid, pipe_id))
        conn.commit()
        conn.close()
        _audit("DELETE_BOUNDARY", "boundary", bid, f"pipeline={pipe_id}", user_id=_pc_identity())
        return jsonify({"deleted": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — EXPORT
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/export/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_export(pipe_id):
        """Export pipeline to various formats."""
        logger.info("Exporting pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        try:
            graph = parse_graph_json(pipe["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "gitlab_ci")

        try:
            from tools.pipeline.export import export_pipeline

            result = export_pipeline(graph, pipe["name"], fmt)
        except ImportError:
            # The export module is genuinely unavailable — fail with 501 rather
            # than returning HTTP 200 with a placeholder body + a real .<fmt>
            # filename (which a caller would save/download as if it were a valid
            # export). 501 Not Implemented signals the capability is absent.
            logger.error("Export module unavailable for pipeline %s (fmt=%s)", pipe_id, fmt)
            return jsonify({"error": "export module unavailable"}), 501
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        _audit("EXPORT", "pipeline", pipe_id, fmt)
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — VALIDATE IaC
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/validate/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_validate(pipe_id):
        """Validate generated IaC through the 5-layer pyramid."""
        logger.info("Validating IaC for pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        try:
            graph = parse_graph_json(pipe["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        data = request.get_json(force=True, silent=True) or {}

        # Guard the int() cast on a client-supplied query/body param: garbage
        # (e.g. "abc") returns 400, not an unhandled 500.
        try:
            max_layer = int(data.get("max_layer", 3))
        except (TypeError, ValueError):
            return jsonify({"error": "max_layer must be an integer"}), 400

        try:
            from tools.pipeline.iac_validator import validate_deploy_bundle_from_generator

            result = validate_deploy_bundle_from_generator(
                graph,
                pipe["name"],
                target_csp=data.get("target_csp", "auto"),
                max_layer=max_layer,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        _audit("VALIDATE_IAC", "pipeline", pipe_id, f"gate={result.get('validation', {}).get('gate', 'unknown')}")
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — FIX IaC WARNINGS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/validate/<pipe_id>/fix", methods=["POST"])
    @pc_login_required
    def pc_api_fix_warnings(pipe_id):
        """Apply suggested auto-fixes to IaC warnings and re-validate."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        try:
            graph = parse_graph_json(pipe["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        data = request.get_json(force=True, silent=True) or {}
        fixes = data.get("fixes", [])

        try:
            from tools.pipeline.deploy_generator import generate_deploy_bundle
            from tools.pipeline.iac_validator import validate_deploy_bundle_from_generator, _EMPTY_YAML_DEFAULTS

            bundle = generate_deploy_bundle(graph, pipe["name"], target_csp="auto", options={})
            files = bundle.get("files_content", [])

            applied = []
            for fix in fixes:
                action = fix.get("fix_action", "")
                target_file = fix.get("file")

                if action == "add_provider_block":
                    # Inject a provider stub into the target .tf file
                    for f in files:
                        if f["path"] == target_file:
                            if "provider " not in f["content"]:
                                f["content"] = 'provider "aws" {\n  region = "us-east-1"\n}\n\n' + f["content"]
                                applied.append(f"Added provider block to {target_file}")
                            break

                elif action == "populate_empty_yaml":
                    stem = Path(target_file).stem if target_file else ""
                    defaults = _EMPTY_YAML_DEFAULTS.get(stem, _EMPTY_YAML_DEFAULTS.get("values", "# configure here\n"))
                    for f in files:
                        if f["path"] == target_file:
                            if not f["content"].strip() or all(
                                ln.strip().startswith("#") or not ln.strip()
                                for ln in f["content"].splitlines()
                            ):
                                f["content"] = defaults
                                applied.append(f"Populated {target_file} with default config")
                            break

                elif action == "fix_shell_header":
                    for f in files:
                        if f["path"] == target_file:
                            if not f["content"].startswith("#!/"):
                                f["content"] = "#!/bin/bash\nset -euo pipefail\n\n" + f["content"]
                                applied.append(f"Added shebang + set -euo pipefail to {target_file}")
                            break

                elif action == "add_tags":
                    for f in files:
                        if f["path"] == (target_file or f["path"]) and f["path"].endswith(".tf"):
                            if "common_tags" not in f["content"]:
                                tag_block = '\nlocals {\n  common_tags = {\n    Environment = "production"\n    ManagedBy   = "terraform"\n  }\n}\n'
                                f["content"] = f["content"] + tag_block
                                applied.append(f"Added common_tags locals block to {f['path']}")
                            break

            # Re-validate with patched files
            re_result = validate_deploy_bundle_from_generator(
                graph, pipe["name"], target_csp="auto", max_layer=3, _override_files=files
            )
            _audit("FIX_IAC", "pipeline", pipe_id, f"applied={len(applied)}")
            return jsonify({"fixed": len(applied), "applied": applied, **re_result})

        except Exception as exc:
            logger.warning("IaC fix failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API — DEPLOY IaC BUNDLE
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/deploy/<pipe_id>", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_deploy(pipe_id):
        """Generate IaC deployment bundle."""
        logger.info("Generating deploy bundle for pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        try:
            graph = parse_graph_json(pipe["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        data = request.get_json(force=True, silent=True) or {}

        try:
            from tools.pipeline.deploy_generator import generate_deploy_bundle

            result = generate_deploy_bundle(
                graph,
                pipe["name"],
                target_csp=data.get("target_csp", "auto"),
                options=data.get("options", {}),
            )
        except Exception as exc:
            logger.warning("Deploy generation failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        _audit("DEPLOY_GENERATE", "pipeline", pipe_id, result.get("summary", ""))

        try:
            from tools.canvas.event_bus import publish as _bus_publish
            _bus_publish(
                "pdc",
                "pipeline_deployed",
                {"pipeline_id": pipe_id, "env": data.get("target_csp", "auto")},
                target_canvas="sdc",
            )
        except Exception as _exc:
            logger.warning("canvas event publish failed for pipeline %s: %s", pipe_id, _exc)

        # Check if zip download requested
        if data.get("format") == "zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in result["files"]:
                    zf.writestr(f"devops-deploy/{f['path']}", f["content"])
            buf.seek(0)
            from flask import send_file

            # Sanitize the download filename to [a-z0-9._-] — the pipeline name is
            # user-controlled and flows into the Content-Disposition header; strip
            # path separators and any other characters that could enable header
            # injection or path traversal in a downloaded filename.
            safe = re.sub(r"[^a-z0-9._-]", "", pipe["name"].replace(" ", "-").lower())[:30] or "pipeline"
            return send_file(
                buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{safe}-deploy-bundle.zip",
            )

        return jsonify(
            {
                "summary": result["summary"],
                "files": [f["path"] for f in result["files"]],
                "manifest": result["manifest"],
                "file_contents": {f["path"]: f["content"] for f in result["files"]},
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # API — HEATMAP DATA
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/heatmap/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_heatmap(pipe_id):
        """Get heatmap data. Query: ?type=execution_time|findings|compliance|freshness"""
        heatmap_type = request.args.get("type", "execution_time")
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = parse_graph_json(row["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        nodes = graph.get("nodes", [])

        # Findings are sourced from pc_compliance_findings (written by the QDC
        # gate bus subscriber), NOT from node config. They are attributed at the
        # PIPELINE level — ``affected_entity`` holds a QDC gate id, not a PDC
        # node id — so we report an honest pipeline-level count instead of the
        # old ``config.findings_count`` (which nothing ever set → permanent 0)
        # or a fabricated per-node distribution.
        if heatmap_type == "findings":
            fconn = get_connection()
            try:
                frows = fconn.execute(
                    "SELECT severity, status FROM pc_compliance_findings WHERE pipeline_id=%s",
                    (pipe_id,),
                ).fetchall()
            finally:
                fconn.close()
            total = 0
            open_count = 0
            by_severity: dict = {}
            for fr in frows:
                fd = row_to_dict(fr)
                total += 1
                sev = (fd.get("severity") or "unknown")
                status = (fd.get("status") or "open")
                by_severity[sev] = by_severity.get(sev, 0) + 1
                if status == "open":
                    open_count += 1
            return jsonify({
                "type": "findings",
                "scope": "pipeline",
                "data": {},  # findings are not node-attributed — no per-node overlay
                "total": total,
                "open": open_count,
                "by_severity": by_severity,
                "color": _findings_color(open_count),
            })

        heatmap = {}
        for node in nodes:
            nid = node.get("id", "")
            config = node.get("config") or {}
            if heatmap_type == "execution_time":
                minutes = config.get("avg_execution_min", 5)
                heatmap[nid] = {"value": minutes, "color": _time_color(minutes)}
            elif heatmap_type == "compliance":
                pct = config.get("compliance_pct", 100)
                heatmap[nid] = {"value": pct, "color": _compliance_color(pct)}
            elif heatmap_type == "freshness":
                age_days = config.get("tool_age_days", 0)
                heatmap[nid] = {"value": age_days, "color": _age_color(age_days)}

        return jsonify({"type": heatmap_type, "data": heatmap})

    @bp.route("/api/pipelines/<pipe_id>/findings", methods=["GET"])
    @pc_login_required
    def pc_api_pipeline_findings(pipe_id):
        """Raw compliance findings for a pipeline (read-only, paginated LIMIT 100).

        Reads pc_compliance_findings — the table written by the QDC gate bus
        subscriber that was previously write-only (nothing read it). Supports an
        ``?offset=`` cursor; newest first.
        """
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM pc_compliance_findings WHERE pipeline_id=%s "
                "ORDER BY created_at DESC LIMIT 100 OFFSET %s",
                (pipe_id, offset),
            ).fetchall()
        finally:
            conn.close()
        return jsonify({
            "pipeline_id": pipe_id,
            "offset": offset,
            "count": len(rows),
            "findings": [row_to_dict(r) for r in rows],
        })

    # ══════════════════════════════════════════════════════════════════════
    # API — CHANGE REQUESTS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/change-requests/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_crs(pipe_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM pc_change_requests WHERE pipeline_id=%s ORDER BY created_at DESC", (pipe_id,)
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/change-requests/<pipe_id>", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_create_cr(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        cr_id = str(_uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_change_requests (id, pipeline_id, cr_number, cr_type, status, "
            "markup_json, created_by, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                cr_id,
                pipe_id,
                data.get("cr_number", f"CR-{cr_id[:4]}"),
                data.get("cr_type", "modify"),
                "draft",
                json.dumps(data.get("markup", [])),
                session.get("user_id", "system"),
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(
            "CREATE_CR", "change_request", cr_id,
            f"pipeline={pipe_id} cr={data.get('cr_number', f'CR-{cr_id[:4]}')}",
            user_id=_pc_identity(),
        )
        return jsonify({"id": cr_id}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — DESIGN RULES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/design-rules/node/<node_type>")
    @pc_login_required
    def pc_api_design_rules_node(node_type):
        """Get design rules for a node type from pipeline_design_rules.yaml."""
        try:
            rules_path = _ICDEV_ROOT / "args" / "pipeline_design_rules.yaml"
            if yaml and rules_path.exists():
                with open(rules_path, encoding="utf-8") as f:
                    rules = yaml.safe_load(f) or {}
                node_rules = rules.get("on_node_add", {}).get(node_type, {})
                return jsonify(node_rules)
        except Exception:
            pass
        return jsonify({})

    # ══════════════════════════════════════════════════════════════════════
    # API — COMPLIANCE FRAMEWORKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance-frameworks")
    @pc_login_required
    def pc_api_frameworks():
        return jsonify(PIPELINE_COMPLIANCE_FRAMEWORKS)

    @bp.route("/api/compliance-rules")
    @pc_login_required
    def pc_api_compliance_rules():
        return jsonify(PIPELINE_COMPLIANCE_RULES)

    @bp.route("/api/slsa-levels")
    @pc_login_required
    def pc_api_slsa_levels():
        return jsonify(SLSA_LEVEL_REQUIREMENTS)

    # ══════════════════════════════════════════════════════════════════════
    # API — SCORECARD (calls existing DevSecOps profile manager)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipelines/<pipe_id>/scorecard", methods=["GET"])
    @pc_login_required
    def pc_api_scorecard(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = parse_graph_json(row["graph_json"])
        except ValueError:
            return jsonify({"error": "corrupt graph"}), 422
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]

        # Compute scorecard
        owasp = compute_owasp_coverage(node_types)
        slsa = _assess_slsa(nodes, edges)
        compliance = _run_compliance_check(nodes, edges)
        cost = estimate_pipeline_cost(node_types)
        exec_time = estimate_execution_time(nodes, edges)

        # Anti-pattern detection
        try:
            from tools.pipeline.antipattern_detector import detect_antipatterns

            antipatterns = detect_antipatterns(nodes, edges)
        except Exception:
            antipatterns = []

        # Stage coverage: the save path never persists a node ``stage`` (only
        # ``type``), so derive the stage from the type server-side. Explicit
        # stage wins when present; otherwise inferred from the type taxonomy.
        _covered = {stage_from_type(n.get("type", ""), n.get("stage")) for n in nodes}
        _covered.discard(None)

        scorecard = {
            "security_coverage": owasp,
            "slsa_level": slsa,
            "compliance": {
                "passed": compliance["passed"],
                "failed": compliance["failed"],
                "score_pct": round(compliance["passed"] / max(compliance["passed"] + compliance["failed"], 1) * 100, 1),
                "findings": compliance.get("findings", []),
            },
            "antipatterns": {
                "total": len(antipatterns),
                "critical": len([a for a in antipatterns if a["severity"] == "critical"]),
                "high": len([a for a in antipatterns if a["severity"] == "high"]),
                "medium": len([a for a in antipatterns if a["severity"] == "medium"]),
                "findings": antipatterns,
            },
            "cost_estimate": cost,
            "execution_time": exec_time,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "stages_covered": len(_covered),
            "total_stages": len(PIPELINE_STAGES),
        }
        return jsonify(scorecard)

    # ══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS — delegate to module-level functions (importable by twin.py)
    # ══════════════════════════════════════════════════════════════════════

    def _assess_slsa(nodes, edges):
        return assess_slsa(nodes, edges)

    def _run_compliance_check(nodes, edges):
        return run_compliance_check(nodes, edges)

    def _compute_pdc_governance(graph_data):
        """DevSecOps pipeline governance — SLSA / NIST SSDF / DoD DEVSECOPS aligned."""
        _nodes = graph_data.get("nodes", [])
        _edges = graph_data.get("edges", [])
        _types = [n.get("type", "").lower() for n in _nodes]
        _labels = [str(n.get("label", "")).lower() for n in _nodes]
        # Derived stages (server-side) — the save path never persists ``stage``.
        _stages_present = {stage_from_type(n.get("type", ""), n.get("stage")) for n in _nodes}
        _stages_present.discard(None)

        # Predicate helpers. ``_any`` matches type PREFIXES, ``_typ`` matches
        # exact PDC type keys (from constants.py), ``_stage`` checks a derived
        # stage is present, and ``_lbl`` is the secondary label-keyword signal.
        # The pre-fix predicates checked src-/bld-/tst-/sast-/reg-/dep- prefixes
        # that belong to a DIFFERENT canvas taxonomy and never matched PDC types
        # (scm-/build-/scan-/registry-/deploy- …), so scores survived only on
        # incidental label keywords. These are rewritten against the real keys.
        def _any(*pfx): return any(any(t.startswith(p) for p in pfx) for t in _types)
        def _typ(*keys): return any(t in frozenset(keys) for t in _types)
        def _stage(*ss): return any(s in _stages_present for s in ss)
        def _lbl(*kws): return any(kw in l for l in _labels for kw in kws)

        CHECKS = [
            ("Source Control Defined",           "Source Control",   "CAT1", _any("scm-") or _typ("aws-codecommit","az-repos","gcp-source","oci-code-repos","branch-policy","commit-signing") or _lbl("git","gitlab","github","bitbucket","svn","vcs","source control")),
            ("Automated Build Stage",            "CI/CD Pipeline",   "CAT1", _any("cicd-","build-") or _typ("aws-codepipeline","aws-codebuild","az-pipelines","gcp-cloudbuild","oci-devops","ibm-cd") or _lbl("build","compile","make","gradle","maven","npm build","docker build")),
            ("Automated Test Stage",             "CI/CD Pipeline",   "CAT1", _any("scan-") or _stage("test") or _lbl("test","pytest","jest","junit","mocha","rspec","automated test")),
            ("SAST Integration",                 "Security",         "CAT1", _typ("scan-sast","scan-sonarqube","scan-semgrep","scan-codeql","scan-bandit","scan-spotbugs","aws-codeguru") or _lbl("sast","sonar","semgrep","bandit","snyk","veracode","checkmarx")),
            ("Container Image Scanning",         "Security",         "CAT1", _typ("scan-container","scan-anchore","scan-neuvector","scan-trivy","aws-inspector","az-defender","gcp-artifact-analysis","ibm-vuln-advisor") or _lbl("trivy","snyk","aqua","anchore","image scan","clair","container scan")),
            ("SCA / Dependency Scanning",        "Security",         "CAT2", _typ("scan-sca","scan-trivy","scan-grype","scan-snyk","scan-dep-check") or _lbl("sca","dependency","sbom","cyclonedx","dependency-check","owasp dependency")),
            ("Secrets Detection",                "Security",         "CAT1", _typ("scan-secret","scan-gitleaks","scan-trufflehog","scan-detect-secrets") or _lbl("secret detect","truffleH","detect-secrets","gitleaks","credscan")),
            ("Artifact Registry Defined",        "CI/CD Pipeline",   "CAT2", _any("registry-") or _typ("aws-ecr","az-acr","gcp-gar","oci-cr","ibm-cr","sbom-store","package-repo") or _lbl("registry","nexus","artifactory","ecr","acr","gcr","harbor")),
            ("Deployment Stage",                 "CI/CD Pipeline",   "CAT1", _any("deploy-","gitops-","k8s-") or _typ("aws-eks","az-aks","gcp-gke","oci-oke","ibm-iks","openshift","rke2","k3s","aws-codedeploy","gcp-deploy") or _lbl("deploy","release","rollout","helm","argocd","flux","eks deploy")),
            ("Environment Promotion Gates",      "CI/CD Pipeline",   "CAT2", _typ("gate-manual","gate-automated","gate-deploy-window") or _stage("approval") or _lbl("staging","prod","promote","env gate","approval","manual gate","dev→staging")),
            ("SLSA L2 or Higher",                "Supply Chain",     "CAT2", _typ("attest-slsa-gen","verify-slsa","attest-in-toto","sign-cosign","sign-notation","gcp-binary-auth") or _lbl("slsa","provenance","build attestation","sigstore","cosign","rekor")),
            ("SBOM Generation",                  "Supply Chain",     "CAT2", _typ("sbom-syft","sbom-cyclonedx","sbom-spdx","sbom-store","sc-cargo-auditable") or _lbl("sbom","cyclonedx","spdx","bill of materials","bom")),
            ("IaC Scanning / Policy",            "Security",         "CAT2", _typ("scan-iac","scan-checkov","scan-tfsec","scan-kics","policy-opa","policy-kyverno","policy-gatekeeper","policy-kubewarden") or _lbl("terrascan","checkov","tflint","opa","sentinel","policy as code","iac scan")),
            ("Pipeline Execution Monitoring",    "Observability",    "CAT2", _any("mon-") or _typ("aws-cloudwatch","az-monitor","gcp-monitoring") or _stage("monitor") or _lbl("monitor","pipeline log","metrics","duration","observ","grafana","datadog pipeline")),
            ("Failure Alerting",                 "Observability",    "CAT2", _typ("mon-pagerduty","mon-soar","aws-guardduty") or _lbl("alert","notify","pagerduty","slack notify","webhook","on failure")),
            ("Rollback / Blue-Green / Canary",   "Resilience",       "CAT2", _typ("deploy-canary","deploy-bluegreen","deploy-feature-flag") or _lbl("rollback","blue-green","canary","progressive","feature flag")),
            ("Compliance Gate (FedRAMP/CMMC)",   "Compliance",       "CAT1", _any("comp-") or _typ("aws-config","az-policy","aws-securityhub","aws-audit","az-defender-cloud","ibm-scc") or _stage("compliance") or _lbl("fedramp","cmmc","stig","il4","il5","rmf","ato","compliance gate")),
            ("DoD DevSecOps Ref Arch Aligned",   "Compliance",       "CAT2", _typ("deploy-bigbang","registry-ironbank") or _lbl("devsecops","dod","enterprise devsecops","p-ato","continuous ato","c-ato")),
        ]

        PILLARS = ["Source Control", "CI/CD Pipeline", "Security", "Supply Chain", "Observability", "Resilience", "Compliance"]
        WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
        MATURITY = [
            (0,  "L1 — Initial",    "Ad-hoc pipelines with no security gates."),
            (30, "L2 — Developing", "Basic CI with some SAST/test automation."),
            (55, "L3 — Defined",    "Full CI/CD with security integrated throughout."),
            (70, "L4 — Managed",    "SLSA L2+, SBOM, compliance gates automated."),
            (85, "L5 — Optimised",  "Continuous ATO with supply chain integrity and full observability."),
        ]

        check_results, total_w, passed_w = [], 0, 0
        cats = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
        for title, pillar, sev, passed in CHECKS:
            w = WEIGHTS[sev]
            total_w += w
            status = "pass" if passed else "fail"
            if passed:
                passed_w += w
            cats.setdefault(pillar, {"passed": 0, "total": 0, "pct": 0})
            cats[pillar]["total"] += 1
            if passed:
                cats[pillar]["passed"] += 1
            check_results.append({"title": title, "pillar": pillar, "severity": sev,
                                   "status": status, "weight": w, "detail": ""})
        for c in cats.values():
            c["pct"] = round(c["passed"] / c["total"] * 100) if c["total"] else 0

        score = round(passed_w / total_w * 100) if total_w else 0
        grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
        mat_level = sum(1 for t, *_ in MATURITY if score >= t)
        mat_label, mat_desc = MATURITY[mat_level - 1][1], MATURITY[mat_level - 1][2]

        recs = [{"title": c["title"], "pillar": c["pillar"], "priority": c["severity"]}
                for c in check_results if c["status"] == "fail"]
        recs.sort(key=lambda r: {"CAT1": 0, "CAT2": 1, "CAT3": 2}[r["priority"]])
        from datetime import datetime, timezone as _tz
        return {
            "score": score, "grade": grade,
            "maturity": {"level": mat_level, "label": mat_label.split(" — ")[1], "description": mat_desc},
            "checks": check_results, "categories": cats, "recommendations": recs,
            "total_checks": len(CHECKS), "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
            "assessed_at": datetime.now(_tz.utc).isoformat(),
        }

    # Heatmap color helpers
    def _time_color(minutes):
        if minutes <= 2:
            return "#27ae60"
        if minutes <= 10:
            return "#f39c12"
        return "#e74c3c"

    def _findings_color(count):
        if count == 0:
            return "#27ae60"
        if count <= 5:
            return "#f39c12"
        return "#e74c3c"

    def _compliance_color(pct):
        if pct >= 90:
            return "#27ae60"
        if pct >= 60:
            return "#f39c12"
        return "#e74c3c"

    def _age_color(days):
        if days <= 90:
            return "#27ae60"
        if days <= 365:
            return "#f39c12"
        return "#e74c3c"

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES — RUNBOOKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/runbooks")
    @pc_login_required
    def pc_runbooks_page():
        """Browse all pipeline incident-response runbooks."""
        return render_template(
            "pipeline/runbooks.html",
            runbooks=_pdc_get_all_runbooks(),
        )

    @bp.route("/runbooks/<runbook_id>")
    @pc_login_required
    def pc_runbook_detail(runbook_id):
        """View a single pipeline runbook playbook."""
        runbook = _pdc_get_runbook_by_id(runbook_id)
        if not runbook:
            return redirect("/devops/runbooks")
        return render_template(
            "pipeline/runbook_detail.html",
            runbook=runbook,
        )

    # ══════════════════════════════════════════════════════════════════════
    # API — RUNBOOKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/runbooks", methods=["GET"])
    @pc_login_required
    def pc_api_list_runbooks():
        """Return all pipeline incident-response runbooks."""
        return jsonify(_pdc_get_all_runbooks())

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_runbook(runbook_id):
        """Return a single pipeline runbook by ID."""
        runbook = _pdc_get_runbook_by_id(runbook_id)
        if not runbook:
            return jsonify({"error": "Not found"}), 404
        return jsonify(runbook)

    # ── Remediation ────────────────────────────────────────────────────────
    @bp.route("/api/pipelines/<pipeline_id>/remediate", methods=["POST"])
    @pc_login_required
    def pc_api_remediate(pipeline_id):
        """Generate remediation plan for a pipeline's compliance findings."""
        from tools.pipeline.remediation import generate_remediation_plan

        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipeline_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404

        try:
            graph = parse_graph_json(row["graph_json"])
        except ValueError:
            conn.close()
            return jsonify({"error": "corrupt graph"}), 422
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Run compliance check to get findings
        result = _run_compliance_check(nodes, edges)
        findings = result.get("findings", [])

        if not findings:
            conn.close()
            return jsonify(
                {
                    "phases": [],
                    "total_actions": 0,
                    "auto_fixable": 0,
                    "summary": "No compliance findings — pipeline is fully compliant.",
                    "created_at": now_isoformat(),
                }
            )

        plan = generate_remediation_plan(findings, rules=PIPELINE_COMPLIANCE_RULES)

        _audit(
            "REMEDIATION_PLAN",
            "pipeline",
            pipeline_id,
            f"actions={plan['total_actions']}, auto_fixable={plan['auto_fixable']}",
        )
        conn.close()
        return jsonify(plan)

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _PDCCollabMgr

    _pdc_collab = _PDCCollabMgr("pc")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_collab_join(design_id):
        """Join a collaborative PDC editing session."""
        body = request.json or {}
        # Identity is server-derived (session / g.current_user), NEVER the body:
        # trusting body.user_id let a caller join a session as an arbitrary user.
        user_id = _pc_identity() or str(_uuid_mod.uuid4())
        user_name = body.get("user_name", "")
        result = _pdc_collab.join(design_id, user_id, user_name)
        _audit("COLLAB_JOIN", "collab", design_id, f"user={user_id}", user_id=user_id)
        return jsonify(result)

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_collab_leave(design_id):
        """Leave a PDC collaborative session."""
        # Server-derived identity only — a caller may only remove themselves.
        user_id = _pc_identity()
        _pdc_collab.leave(design_id, user_id)
        _audit("COLLAB_LEAVE", "collab", design_id, f"user={user_id}", user_id=user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_collab_push(design_id):
        """Push an operation into a PDC collaborative session."""
        body = request.json or {}
        # Attribute the op to the authenticated caller, not body.user_id
        # (identity spoofing — a caller could forge ops as another participant).
        user_id = _pc_identity()
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        # CanvasCollabManager.push(design_id, user_id, operation: dict) is the real,
        # shared interface (other canvases depend on it). Bundle op_type + payload
        # into the single operation dict it expects rather than passing 4 positional
        # args (which raised TypeError before pdx-hyg-01).
        result = _pdc_collab.push(design_id, user_id, {"op_type": op_type, "data": data})
        _audit("COLLAB_PUSH", "collab", design_id, f"user={user_id} op={op_type}", user_id=user_id)
        return jsonify(result)

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @pc_login_required
    def pc_collab_poll(design_id):
        """Poll for PDC collaborative session participants."""
        # Guard the int() cast on a client-supplied query param: garbage -> 400.
        # CanvasCollabManager.poll() is participant-oriented (no server-side op log
        # or cursor tracking), so `since` is validated for a clean 400 but does not
        # slice an operation stream.
        try:
            int(request.args.get("since", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "since must be an integer"}), 400
        # CanvasCollabManager.poll(design_id) -> {"participants": [...], "polled_at": ...}
        result = _pdc_collab.poll(design_id)
        return jsonify(
            {
                "operations": [],
                "participants": result.get("participants", []),
                "polled_at": result.get("polled_at"),
            }
        )

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @pc_login_required
    def pc_collab_participants(design_id):
        """Return current participants in a PDC collaborative session."""
        # CanvasCollabManager exposes participants() (not get_participants()).
        return jsonify({"participants": _pdc_collab.participants(design_id)})

    # ══════════════════════════════════════════════════════════════════════
    # SOPs — Standard Operating Procedures
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/sops")
    @pc_login_required
    def pc_sops_page():
        """Browse all pipeline standard operating procedures."""
        _pdc_seed_sops()
        return render_template(
            "pipeline/sops.html",
            sops=_pdc_get_all_sops(),
        )

    @bp.route("/api/sops", methods=["GET"])
    @pc_login_required
    def pc_api_list_sops():
        """Return all pipeline SOPs with optional type/status filters."""
        sop_type = request.args.get("type")
        approval_status = request.args.get("status")
        return jsonify(_pdc_get_all_sops(sop_type=sop_type, approval_status=approval_status))

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_sop(sop_id):
        """Return a single pipeline SOP by ID."""
        sop = _pdc_get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_create_sop():
        """Create a new pipeline SOP."""
        data = request.json or {}
        sop = _pdc_create_sop(data)
        _audit(
            "SOP_CREATE", "sop", (sop or {}).get("id", ""),
            (sop or {}).get("title", ""), user_id=_pc_identity(),
        )
        return jsonify(sop), 201

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_update_sop(sop_id):
        """Update an existing pipeline SOP."""
        data = request.json or {}
        sop = _pdc_update_sop(sop_id, data)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        _audit("SOP_UPDATE", "sop", sop_id, "", user_id=_pc_identity())
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @pc_login_required
    @pc_role_required(*PC_ELEVATED_ROLES)
    def pc_api_delete_sop(sop_id):
        """Delete a pipeline SOP (governance action — elevated role only).

        Fail-closed (NIST AU): the audit row is written BEFORE the delete, so if
        the audit write fails the SOP is never deleted (500, no mutation).
        """
        if not _pdc_get_sop_by_id(sop_id):
            return jsonify({"error": "Not found"}), 404
        try:
            _audit_strict("SOP_DELETE", "sop", sop_id, "", user_id=_pc_identity())
        except AuditUnavailable:
            return jsonify({"error": "audit trail unavailable"}), 500
        _pdc_delete_sop(sop_id)
        return jsonify({"ok": True})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_WRITE_ROLES)
    def pc_api_submit_sop(sop_id):
        """Submit a pipeline SOP for review (draft → pending_review)."""
        sop, err = _pdc_submit_for_review(sop_id)
        if err:
            return jsonify({"error": err}), 400
        _audit("SOP_SUBMIT", "sop", sop_id, "draft->pending_review", user_id=_pc_identity())
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_ELEVATED_ROLES)
    def pc_api_approve_sop(sop_id):
        """Approve a pending pipeline SOP.

        Separation of duties (NIST AC-5): the approver identity is taken from
        the authenticated session — NEVER the request body — and an approver may
        not approve a SOP they own/authored (self-approval -> 403).
        """
        sop = _pdc_get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        approver = _pc_identity()
        owner = (sop.get("owner") or "").strip()
        if approver and owner and owner.lower() == approver.lower():
            _audit(
                "SOP_SELF_APPROVAL_DENY",
                "sop",
                sop_id,
                f"approver={approver} owner={owner}",
                user_id=approver,
            )
            return jsonify(
                {"error": "Separation of duties: you may not approve a SOP you own"}
            ), 403
        # Fail-closed (NIST AU): record the approval BEFORE mutating state, so an
        # approval can never commit without an audit row.
        try:
            _audit_strict("SOP_APPROVE", "sop", sop_id, f"approved_by={approver}", user_id=approver)
        except AuditUnavailable:
            return jsonify({"error": "audit trail unavailable"}), 500
        sop, err = _pdc_approve_sop(sop_id, approved_by=approver)
        if err:
            return jsonify({"error": err}), 400
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/reject", methods=["POST"])
    @pc_login_required
    @pc_role_required(*PC_ELEVATED_ROLES)
    def pc_api_reject_sop(sop_id):
        """Reject a pending pipeline SOP (governance action — elevated role only)."""
        body = request.json or {}
        reason = body.get("reason", "")
        # Rejecter identity is server-derived, never the request body.
        rejected_by = _pc_identity()
        # Fail-closed (NIST AU): record the rejection BEFORE mutating state, so a
        # governance rejection can never commit without an audit row.
        try:
            _audit_strict("SOP_REJECT", "sop", sop_id, f"rejected_by={rejected_by}", user_id=rejected_by)
        except AuditUnavailable:
            return jsonify({"error": "audit trail unavailable"}), 500
        sop, err = _pdc_reject_sop(sop_id, reason=reason, rejected_by=rejected_by)
        if err:
            return jsonify({"error": err}), 400
        return jsonify(sop)

    # ══════════════════════════════════════════════════════════════════════
    # PIPELINE TWIN — pre-merge what-if simulation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/twin/<pipe_id>")
    @pc_login_required
    def pc_twin_page(pipe_id):
        """Pipeline Twin simulation UI for a specific pipeline."""
        conn = get_connection()
        row = conn.execute("SELECT id, name, description FROM pipelines WHERE id=%s", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return redirect("/devops/")
        from tools.pipeline.twin import list_snapshots
        snapshots = list_snapshots(pipe_id)
        return render_template(
            "pipeline/twin.html",
            pipeline=row_to_dict(row),
            snapshots=snapshots,
        )

    @bp.route("/api/pipelines/<pipe_id>/twin/snapshot", methods=["POST"])
    @pc_login_required
    def pc_api_twin_snapshot(pipe_id):
        """Take a DAG snapshot of the current pipeline state."""
        from tools.pipeline.twin import take_snapshot, CorruptGraphError
        data = request.get_json(force=True, silent=True) or {}
        try:
            snap = take_snapshot(pipe_id, label=data.get("label"), user_id=session.get("user_id", "system"))
        except CorruptGraphError:
            return jsonify({"error": "corrupt graph"}), 422
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        _audit("twin_snapshot", "pipeline", pipe_id, f"snap={snap['id']}", session.get("user_id"))
        return jsonify(snap), 201

    @bp.route("/api/pipelines/<pipe_id>/twin/snapshots", methods=["GET"])
    @pc_login_required
    def pc_api_twin_list_snapshots(pipe_id):
        """List all snapshots for a pipeline."""
        from tools.pipeline.twin import list_snapshots
        return jsonify(list_snapshots(pipe_id))

    @bp.route("/api/pipelines/<pipe_id>/twin/simulate", methods=["POST"])
    @pc_login_required
    def pc_api_twin_simulate(pipe_id):
        """Run a pre-merge simulation on a delta graph.

        Request body:
            delta_graph: {"nodes": [...], "edges": [...]}
            baseline_snap_id: (optional) snapshot ID to diff against
        """
        from tools.pipeline.twin import simulate_delta, CorruptGraphError
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        delta_graph = data.get("delta_graph")
        if not delta_graph or not isinstance(delta_graph.get("nodes"), list):
            return jsonify({"error": "delta_graph with nodes[] required"}), 400
        baseline_snap_id = data.get("baseline_snap_id")
        try:
            result = simulate_delta(
                pipe_id, delta_graph,
                baseline_snap_id=baseline_snap_id,
                user_id=session.get("user_id", "system"),
            )
        except CorruptGraphError:
            return jsonify({"error": "corrupt graph"}), 422
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        _audit("twin_simulate", "pipeline", pipe_id, f"sim={result['id']} verdict={result['verdict']}", session.get("user_id"))
        return jsonify(result), 201

    @bp.route("/api/twin/simulations/<sim_id>", methods=["GET"])
    @pc_login_required
    def pc_api_twin_get_simulation(sim_id):
        """Retrieve a stored simulation result."""
        from tools.pipeline.twin import get_simulation
        result = get_simulation(sim_id)
        if not result:
            return jsonify({"error": "Not found"}), 404
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES — PDC TWIN DASHBOARD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/twin")
    @pc_login_required
    def pc_twin_list():
        """PDC Twin dashboard — all pipelines with last snapshot.

        pdx-perf-01: previously this loaded every pipeline then called
        list_snapshots() once per pipeline (an N+1 query) just to use the two
        newest snapshots. Now a single windowed query fetches the two newest
        snapshots for all pipelines at once.
        """
        from tools.pipeline.twin import latest_snapshots_by_pipeline
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, updated_at "
            "FROM pipelines ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        snaps_by_pipeline = latest_snapshots_by_pipeline(per_pipeline=2)
        pipelines = []
        for row in rows:
            p = row_to_dict(row)
            snaps = snaps_by_pipeline.get(p["id"], [])
            p["last_snapshot"] = snaps[0] if snaps else None
            p["prev_snapshot"] = snaps[1] if len(snaps) > 1 else None
            pipelines.append(p)
        return render_template("pipeline/twin_list.html", pipelines=pipelines)

    @bp.route("/twin/<pipe_id>/delta")
    @pc_login_required
    def pc_twin_delta(pipe_id):
        """PDC Twin diff view — structural delta between two snapshots."""
        snap_from = request.args.get("from")
        snap_to = request.args.get("to")
        if not snap_from or not snap_to:
            return redirect(f"/devops/twin/{pipe_id}")
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name FROM pipelines WHERE id=%s", (pipe_id,)
        ).fetchone()
        conn.close()
        if not row:
            return redirect("/devops/twin")
        try:
            from tools.pipeline.delta import compute_delta
            delta = compute_delta(snap_from, snap_to)
        except ValueError as exc:
            return render_template(
                "pipeline/twin_delta.html",
                pipeline=row_to_dict(row),
                delta=None,
                error=str(exc),
                snap_from=snap_from,
                snap_to=snap_to,
                gate=None,
                removed_count=0,
                added_count=0,
                modified_count=0,
            ), 404
        removed = len(delta["nodes"]["removed"]) + len(delta["edges"]["removed"])
        added = len(delta["nodes"]["added"]) + len(delta["edges"]["added"])
        modified = len(delta["nodes"]["modified"]) + len(delta["edges"]["modified"])
        gate = "fail" if removed > 3 else ("warn" if removed > 0 else "pass")
        return render_template(
            "pipeline/twin_delta.html",
            pipeline=row_to_dict(row),
            delta=delta,
            error=None,
            snap_from=snap_from,
            snap_to=snap_to,
            gate=gate,
            removed_count=removed,
            added_count=added,
            modified_count=modified,
        )

    # ── GraphRAG /ask — shared canvas_ask pattern (DT adaptation #1) ──────
    @bp.route("/ask")
    @pc_login_required
    def pdc_ask_page():
        return render_template(
            "canvas_ask.html",
            canvas_label="Pipeline Design Canvas",
            graph_id="pdc-designs",
            profile="provenance",
            examples=["build", "deploy", "scm-gitlab", "test", "monorepo"],
            api_url="/devops/api/ask",
            home_url="/devops/",
        )

    @bp.route("/api/ask", methods=["POST"])
    @pc_login_required
    def pdc_api_ask():
        from tools.knowledge_graph.canvas_ask import handle_ask_request
        data = request.get_json(silent=True) or {}
        # Guard the int() cast on a client-supplied param: garbage -> 400.
        try:
            top_k = int(data.get("top_k", 10))
        except (TypeError, ValueError):
            return jsonify({"error": "top_k must be an integer"}), 400
        payload = handle_ask_request(
            query=data.get("query", ""),
            graph_id="pdc-designs",
            profile="provenance",
            top_k=top_k,
            narrate=bool(data.get("narrate", False)),
            canvas_label="CI/CD pipeline",
        )
        status = payload.pop("_status", 200)
        return jsonify(payload), status

    @bp.route("/api/iqe-query", methods=["POST"])
    @pc_login_required
    def pdc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against PDC pipeline data."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.pipeline  # noqa: F401 — registers pipeline.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        collections = ["pipeline.snapshots", "pipeline.nodes", "pipeline.edges"]
        translation = nl_to_iqe(question, collections)
        iqe_str = translation.get("iqe", "")
        explanation = translation.get("explanation", "")

        if not data.get("execute", True):
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

        try:
            ast = parse(iqe_str)
            rows = execute_query(ast, None)
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                            "results": rows, "row_count": len(rows)}), 200
        except IQESyntaxError as exc:
            return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
        except Exception as exc:
            logger.warning("PDC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    @bp.route("/api/ai-trace")
    @pc_login_required
    def pc_api_ai_trace():
        """Return recent AI decisions made by PDC assessment engines."""
        # Validate the int FIRST, then clamp: min() must be applied to an already
        # validated int (min("abc", 200) would compare str<int on py2 / raise on
        # py3, and int("abc") raises). Garbage -> 400.
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "limit must be an integer"}), 400
        limit = min(max(limit, 1), 200)
        record_id = request.args.get("record_id")
        try:
            from tools.db.storage import get_connection as _gc
            with _gc() as _conn:
                if record_id:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='pdc' AND record_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='pdc' "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "pdc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return bp
