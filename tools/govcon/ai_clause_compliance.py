# CUI // SP-CTI
from __future__ import annotations

# ICDEV™ Proposal Genesis — GSA "American AI" Clause Compliance Engine (§3.15)
# Generates required AI compliance artifacts by wrapping existing ICDEV™ Phase 48/49 tools.

"""
AI Clause Compliance Engine — detect AI compliance requirements in RFPs and generate
required artifacts (model cards, bias evaluations, source disclosure, American AI checklist).

Wraps existing ICDEV™ Phase 48/49 tools:
    - tools.compliance.model_card_generator (model cards)
    - tools.compliance.fairness_assessor (bias/fairness evaluation)
    - tools.compliance.ai_inventory_manager (AI component inventory)
    - tools.compliance.system_card_generator (system cards)

Tables used:
    - pg_ai_clause_compliance (CRUD — artifact tracking)
    - audit_trail (write — NIST AU-2)

Gate criteria:
    - PASS: All required artifacts generated and status='reviewed' or 'attached'
    - WARN: Artifacts generated but not reviewed
    - FAIL: Required clause detected but artifacts not generated

Usage:
    python tools/govcon/ai_clause_compliance.py --opportunity-id "opp-xxx" --detect --json
    python tools/govcon/ai_clause_compliance.py --opportunity-id "opp-xxx" --generate --json
    python tools/govcon/ai_clause_compliance.py --opportunity-id "opp-xxx" --generate --clause-type gsar_552_239_7001 --json
    python tools/govcon/ai_clause_compliance.py --opportunity-id "opp-xxx" --status --json
    python tools/govcon/ai_clause_compliance.py --opportunity-id "opp-xxx" --gate --json
"""

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.common.helpers import now_isoformat  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.ai_clause_compliance")

_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

# ── Wrapped ICDEV™ tools (graceful ImportError) ───────────────────────

try:
    from tools.compliance.model_card_generator import generate_model_card
except ImportError:
    generate_model_card = None

try:
    from tools.compliance.fairness_assessor import assess_fairness
except ImportError:
    assess_fairness = None

try:
    from tools.compliance.ai_inventory_manager import register_ai_use_case, list_ai_inventory
except ImportError:
    register_ai_use_case = None
    list_ai_inventory = None

try:
    from tools.compliance.system_card_generator import generate_system_card
except ImportError:
    generate_system_card = None


# ── Detection patterns (deterministic regex, no LLM) ────────────────

AI_CLAUSE_PATTERNS = [
    (r"GSAR\s*552\.239[–\-]7001", "gsar_552_239_7001", "GSAR 552.239-7001 (American AI)"),
    (r"(?:American\s+AI|U\.?S\.?\s+origin\s+AI)", "american_ai", "American AI / US-Origin AI"),
    (
        r"(?:AI|artificial\s+intelligence)\s+(?:disclosure|transparency|bias)",
        "ai_transparency",
        "AI Disclosure / Transparency / Bias",
    ),
    (r"OMB\s+M[–\-]26[–\-]04", "omb_m26_04", "OMB M-26-04 (Unbiased AI)"),
    (r"OMB\s+M[–\-]25[–\-]21", "omb_m25_21", "OMB M-25-21 (High-Impact AI)"),
    (r"(?:model\s+card|system\s+card|AI\s+inventory)", "model_cards", "Model Cards / System Cards / AI Inventory"),
    (
        r"(?:AI\s+source|training\s+data)\s+(?:disclosure|origin)",
        "ai_source_disclosure",
        "AI Source / Training Data Disclosure",
    ),
    (r"NIST\s+AI\s+(?:RMF|600[–\-]1|Risk\s+Management)", "nist_ai_rmf", "NIST AI RMF / AI 600-1"),
    (r"(?:Executive\s+Order\s+14110|EO\s+14110)", "eo_14110", "Executive Order 14110 (Safe AI)"),
    (
        r"(?:algorithmic|AI)\s+(?:accountability|impact\s+assessment)",
        "ai_accountability",
        "AI Accountability / Impact Assessment",
    ),
]

# Supported clause types and their required artifacts
CLAUSE_ARTIFACT_REQUIREMENTS = {
    "gsar_552_239_7001": [
        "model_cards",
        "bias_evaluation",
        "source_disclosure",
        "american_ai_checklist",
    ],
    "american_ai": [
        "source_disclosure",
        "american_ai_checklist",
    ],
    "ai_transparency": [
        "model_cards",
        "bias_evaluation",
        "source_disclosure",
    ],
    "omb_m26_04": [
        "bias_evaluation",
        "model_cards",
    ],
    "omb_m25_21": [
        "model_cards",
        "bias_evaluation",
        "source_disclosure",
        "american_ai_checklist",
    ],
    "model_cards": [
        "model_cards",
    ],
    "ai_source_disclosure": [
        "source_disclosure",
    ],
    "nist_ai_rmf": [
        "model_cards",
        "bias_evaluation",
        "source_disclosure",
    ],
    "eo_14110": [
        "model_cards",
        "bias_evaluation",
        "source_disclosure",
        "american_ai_checklist",
    ],
    "ai_accountability": [
        "bias_evaluation",
        "model_cards",
    ],
}

# Artifact types with generation methods
ARTIFACT_TYPES = ("model_cards", "bias_evaluation", "source_disclosure", "american_ai_checklist")

# Artifact statuses
ARTIFACT_STATUSES = ("pending", "generated", "reviewed", "attached", "failed")


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _uuid():
    return f"aic-{uuid.uuid4().hex[:12]}"


def _audit(conn, action, details="", actor="ai_clause_compliance"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (now_isoformat(), "govcon.ai_clause", actor, action, details, "proposal_genesis"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _get_rfp_text(conn, opportunity_id):
    """Retrieve RFP text for an opportunity from available sources.

    Checks sam_gov_opportunities description and rfp_shall_statements.
    """
    texts = []

    # Try SAM.gov opportunity description
    try:
        row = conn.execute(
            "SELECT description FROM sam_gov_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if row and row["description"]:
            texts.append(row["description"])
    except Exception:
        pass

    # Try shall statements (requirement text)
    try:
        stmts = conn.execute(
            "SELECT statement_text FROM rfp_shall_statements WHERE opportunity_id = %s",
            (opportunity_id,),
        ).fetchall()
        for s in stmts:
            if s["statement_text"]:
                texts.append(s["statement_text"])
    except Exception:
        pass

    # Try proposal sections content
    try:
        sections = conn.execute(
            "SELECT content FROM proposal_sections WHERE opportunity_id = %s",
            (opportunity_id,),
        ).fetchall()
        for s in sections:
            if s["content"]:
                texts.append(s["content"])
    except Exception:
        pass

    return " ".join(texts)


# ── Detection ────────────────────────────────────────────────────────


def detect_ai_clauses(opportunity_id):
    """Detect AI compliance clauses in RFP text (deterministic regex matching).

    Scans opportunity description, shall statements, and section content for
    known AI compliance patterns.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with detected clauses and required artifacts.
    """
    conn = _get_db()
    rfp_text = _get_rfp_text(conn, opportunity_id)

    if not rfp_text:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "ai_clauses_detected": False,
            "message": "No RFP text found for scanning",
            "detected_clauses": [],
            "required_artifacts": [],
        }

    detected = []
    required_artifacts_set = set()

    for pattern, clause_type, description in AI_CLAUSE_PATTERNS:
        matches = re.findall(pattern, rfp_text, re.IGNORECASE)
        if matches:
            detected.append(
                {
                    "clause_type": clause_type,
                    "description": description,
                    "match_count": len(matches),
                    "sample_match": matches[0] if matches else "",
                }
            )
            # Add required artifacts for this clause type
            for artifact in CLAUSE_ARTIFACT_REQUIREMENTS.get(clause_type, []):
                required_artifacts_set.add(artifact)

    required_artifacts = sorted(required_artifacts_set)

    _audit(
        conn,
        "detect_ai_clauses",
        f"Scanned {opportunity_id}: {len(detected)} clauses detected, "
        f"{len(required_artifacts)} artifact types required",
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "ai_clauses_detected": len(detected) > 0,
        "detected_clauses": detected,
        "required_artifacts": required_artifacts,
        "total_clauses": len(detected),
    }


# ── Artifact Generation ──────────────────────────────────────────────


def _generate_model_cards_artifact(opportunity_id):
    """Generate model cards by wrapping existing ICDEV™ tool or producing stub."""
    if generate_model_card is not None:
        try:
            result = generate_model_card(project_id=opportunity_id)
            return {
                "artifact_type": "model_cards",
                "status": "generated",
                "content": result,
                "source": "tools.compliance.model_card_generator",
            }
        except Exception as exc:
            return {
                "artifact_type": "model_cards",
                "status": "generated",
                "content": _stub_model_card(opportunity_id),
                "source": "stub (model_card_generator raised: " + str(exc)[:100] + ")",
            }
    return {
        "artifact_type": "model_cards",
        "status": "generated",
        "content": _stub_model_card(opportunity_id),
        "source": "stub (model_card_generator not available)",
    }


def _stub_model_card(opportunity_id):
    """Deterministic stub model card when ICDEV™ tool is unavailable."""
    return {
        "model_card": {
            "project_id": opportunity_id,
            "model_details": {
                "name": "TBD — Populate with deployed AI model name",
                "version": "TBD",
                "type": "TBD (classification, generation, recommendation, etc.)",
                "framework": "TBD",
            },
            "intended_use": {
                "primary_use": "TBD — Describe primary intended use case",
                "out_of_scope": "TBD — List uses that are out of scope",
            },
            "training_data": {
                "description": "TBD — Describe training datasets",
                "source": "TBD — US-based / ally-nation / commercial / open-source",
                "preprocessing": "TBD",
            },
            "evaluation_results": {
                "metrics": "TBD — Accuracy, F1, etc.",
                "bias_testing": "TBD — Refer to bias evaluation artifact",
            },
            "ethical_considerations": "TBD — Document ethical risks and mitigations",
            "limitations": "TBD — Known limitations",
            "generated_at": now_isoformat(),
            "note": "STUB — Complete with actual model details before submission",
        },
    }


def _generate_bias_evaluation_artifact(opportunity_id):
    """Generate bias evaluation by wrapping existing ICDEV™ fairness assessor."""
    if assess_fairness is not None:
        try:
            result = assess_fairness(project_id=opportunity_id)
            return {
                "artifact_type": "bias_evaluation",
                "status": "generated",
                "content": result,
                "source": "tools.compliance.fairness_assessor",
            }
        except Exception as exc:
            return {
                "artifact_type": "bias_evaluation",
                "status": "generated",
                "content": _stub_bias_evaluation(opportunity_id),
                "source": "stub (fairness_assessor raised: " + str(exc)[:100] + ")",
            }
    return {
        "artifact_type": "bias_evaluation",
        "status": "generated",
        "content": _stub_bias_evaluation(opportunity_id),
        "source": "stub (fairness_assessor not available)",
    }


def _stub_bias_evaluation(opportunity_id):
    """Deterministic stub bias evaluation."""
    return {
        "bias_evaluation": {
            "project_id": opportunity_id,
            "assessment_date": now_isoformat(),
            "methodology": "TBD — Document bias testing methodology",
            "protected_classes_tested": [
                "TBD — race",
                "TBD — gender",
                "TBD — age",
                "TBD — disability",
                "TBD — national_origin",
            ],
            "datasets_evaluated": "TBD — List evaluation datasets",
            "metrics": {
                "demographic_parity": "TBD",
                "equalized_odds": "TBD",
                "disparate_impact_ratio": "TBD",
            },
            "findings": "TBD — Summarize bias testing results",
            "mitigations": "TBD — Document bias mitigations implemented",
            "ongoing_monitoring": "TBD — Describe ongoing bias monitoring plan",
            "note": "STUB — Complete with actual bias evaluation before submission",
        },
    }


def _generate_source_disclosure_artifact(opportunity_id):
    """Generate AI source/component disclosure (inventory)."""
    inventory = None
    if list_ai_inventory is not None:
        try:
            inventory = list_ai_inventory(project_id=opportunity_id)
        except Exception:
            pass

    if inventory and inventory.get("status") == "ok" and inventory.get("inventory"):
        components = inventory["inventory"]
    else:
        # Deterministic stub
        components = [
            {
                "component": "TBD — AI Model / Service Name",
                "provider": "TBD — Provider / Vendor",
                "origin_country": "TBD — US / Ally / Other",
                "model_type": "TBD — LLM, CV, NLP, etc.",
                "training_data_origin": "TBD — US-sourced / Open-source / Proprietary",
                "deployment_type": "TBD — Cloud / On-premise / Hybrid",
                "api_or_self_hosted": "TBD — API / Self-hosted / Edge",
            },
        ]

    return {
        "artifact_type": "source_disclosure",
        "status": "generated",
        "content": {
            "ai_source_disclosure": {
                "project_id": opportunity_id,
                "disclosure_date": now_isoformat(),
                "total_ai_components": len(components),
                "components": components,
                "us_origin_certification": "TBD — Certify US-origin or ally-nation AI",
                "foreign_ai_components": "TBD — List any foreign-origin AI, if applicable",
                "data_sovereignty": "TBD — Confirm data processing location",
            },
        },
        "source": "tools.compliance.ai_inventory_manager" if inventory else "stub",
    }


def _generate_american_ai_checklist_artifact(opportunity_id):
    """Generate American AI certification checklist (deterministic template)."""
    return {
        "artifact_type": "american_ai_checklist",
        "status": "generated",
        "content": {
            "american_ai_checklist": {
                "project_id": opportunity_id,
                "checklist_date": now_isoformat(),
                "items": [
                    {
                        "id": "AAI-01",
                        "requirement": "All AI models developed or trained in the United States or ally nations",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-02",
                        "requirement": "Training data sourced from US-approved or open-source datasets",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-03",
                        "requirement": "No foreign adversary AI components (per EO 14110 / Section 889)",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-04",
                        "requirement": "AI model cards provided for all deployed models",
                        "status": "TBD",
                        "evidence": "See model_cards artifact",
                    },
                    {
                        "id": "AAI-05",
                        "requirement": "Bias and fairness evaluation completed",
                        "status": "TBD",
                        "evidence": "See bias_evaluation artifact",
                    },
                    {
                        "id": "AAI-06",
                        "requirement": "AI source disclosure and component inventory provided",
                        "status": "TBD",
                        "evidence": "See source_disclosure artifact",
                    },
                    {
                        "id": "AAI-07",
                        "requirement": "Ongoing AI monitoring and reporting plan documented",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-08",
                        "requirement": "AI transparency requirements per OMB M-25-21 / M-26-04 addressed",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-09",
                        "requirement": "Human oversight mechanisms documented for high-impact AI",
                        "status": "TBD",
                        "evidence": "",
                    },
                    {
                        "id": "AAI-10",
                        "requirement": "AI incident response procedures established",
                        "status": "TBD",
                        "evidence": "",
                    },
                ],
                "certification_statement": (
                    "The offeror certifies that all artificial intelligence components proposed "
                    "in this contract are developed, trained, and/or sourced in compliance with "
                    "GSAR 552.239-7001, Executive Order 14110, OMB M-25-21, and OMB M-26-04."
                ),
                "signatory": "TBD — Authorized signatory name and title",
                "note": "Complete all TBD items and provide evidence references before submission.",
            },
        },
        "source": "deterministic_template",
    }


def generate_compliance_bundle(opportunity_id, clause_type="gsar_552_239_7001"):
    """Generate all required AI compliance artifacts for a detected clause type.

    Args:
        opportunity_id: Parent opportunity UUID.
        clause_type: The clause type to generate artifacts for.

    Returns:
        Dict with generated artifacts and status.
    """
    if clause_type not in CLAUSE_ARTIFACT_REQUIREMENTS:
        return {
            "status": "error",
            "message": f"Unknown clause_type '{clause_type}'. Supported: {list(CLAUSE_ARTIFACT_REQUIREMENTS.keys())}",
        }

    required = CLAUSE_ARTIFACT_REQUIREMENTS[clause_type]
    conn = _get_db()
    now = now_isoformat()

    generators = {
        "model_cards": _generate_model_cards_artifact,
        "bias_evaluation": _generate_bias_evaluation_artifact,
        "source_disclosure": _generate_source_disclosure_artifact,
        "american_ai_checklist": _generate_american_ai_checklist_artifact,
    }

    artifacts = []
    for artifact_type in required:
        gen_fn = generators.get(artifact_type)
        if not gen_fn:
            continue

        result = gen_fn(opportunity_id)

        # Store in DB
        artifact_id = _uuid()
        try:
            conn.execute(
                "INSERT INTO pg_ai_clause_compliance "
                "(id, opportunity_id, clause_type, artifact_type, artifact_status, "
                "artifact_content, source_tool, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    artifact_id,
                    opportunity_id,
                    clause_type,
                    artifact_type,
                    result.get("status", "generated"),
                    json.dumps(result.get("content", {})),
                    result.get("source", "unknown"),
                    now,
                    now,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "generate_compliance_bundle: best-effort INSERT into pg_ai_clause_compliance failed (non-blocking): %s",
                exc,
            )

        artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "status": result.get("status", "generated"),
                "source": result.get("source", "unknown"),
            }
        )

    _audit(
        conn, "generate_bundle", f"Generated {len(artifacts)} artifacts for {opportunity_id} (clause: {clause_type})"
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "clause_type": clause_type,
        "artifacts": artifacts,
        "total_generated": len(artifacts),
        "total_required": len(required),
    }


def generate_model_cards(opportunity_id):
    """Generate model cards artifact (convenience wrapper).

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with model card content.
    """
    return _generate_model_cards_artifact(opportunity_id)


def generate_bias_evaluations(opportunity_id):
    """Generate bias/fairness evaluation artifact (convenience wrapper).

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with bias evaluation content.
    """
    return _generate_bias_evaluation_artifact(opportunity_id)


def generate_source_disclosure(opportunity_id):
    """Generate AI source disclosure / component inventory (convenience wrapper).

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with source disclosure content.
    """
    return _generate_source_disclosure_artifact(opportunity_id)


def generate_american_ai_checklist(opportunity_id):
    """Generate American AI certification checklist (convenience wrapper).

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with checklist content.
    """
    return _generate_american_ai_checklist_artifact(opportunity_id)


# ── Status & Gate ────────────────────────────────────────────────────


def get_bundle_status(opportunity_id):
    """Get current status of all AI compliance artifacts for an opportunity.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with per-artifact status.
    """
    conn = _get_db()

    # Get stored compliance rows (one row per opportunity+clause_type)
    rows = conn.execute(
        "SELECT id, clause_type, model_cards_generated, bias_evaluations_generated, "
        "source_disclosure_generated, system_card_generated, american_ai_certified, "
        "ip_rights_documented, bundle_path, status, created_at, updated_at "
        "FROM pg_ai_clause_compliance "
        "WHERE opportunity_id = %s "
        "ORDER BY clause_type",
        (opportunity_id,),
    ).fetchall()

    # Detect what's required
    detection = detect_ai_clauses(opportunity_id)
    required = detection.get("required_artifacts", [])

    conn.close()

    # Map DB flag columns to artifact types
    FLAG_MAP = {
        "model_cards": "model_cards_generated",
        "bias_evaluation": "bias_evaluations_generated",
        "source_disclosure": "source_disclosure_generated",
        "system_card": "system_card_generated",
        "american_ai_checklist": "american_ai_certified",
    }

    # Build status report from flag columns
    artifact_status = []
    row = rows[0] if rows else None
    for art_type in ARTIFACT_TYPES:
        flag_col = FLAG_MAP.get(art_type)
        if row and flag_col and row[flag_col]:
            entry = {
                "artifact_type": art_type,
                "status": row["status"] or "generated",
                "required": art_type in required,
                "clause_type": row["clause_type"],
            }
        else:
            entry = {
                "artifact_type": art_type,
                "status": "missing",
                "required": art_type in required,
            }
        artifact_status.append(entry)

    generated_count = sum(1 for a in artifact_status if a["status"] in ("generated", "reviewed", "attached", "pending"))
    required_count = sum(1 for a in artifact_status if a["required"])
    reviewed_count = sum(1 for a in artifact_status if a["status"] in ("reviewed", "attached"))

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "ai_clauses_detected": detection.get("ai_clauses_detected", False),
        "detected_clauses": detection.get("detected_clauses", []),
        "artifacts": artifact_status,
        "summary": {
            "required": required_count,
            "generated": generated_count,
            "reviewed": reviewed_count,
            "missing": required_count - generated_count,
        },
    }


def gate_evaluate(opportunity_id):
    """Pass/fail gate evaluation for AI clause compliance.

    Gate criteria:
        - PASS: All required artifacts generated and status='reviewed' or 'attached'
        - WARN: Artifacts generated but not reviewed
        - FAIL: Required clause detected but artifacts not generated

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with gate result and details.
    """
    bundle_status = get_bundle_status(opportunity_id)
    if bundle_status.get("status") != "ok":
        return {"status": "error", "gate": "fail", "message": "Failed to retrieve bundle status"}

    # If no AI clauses detected, gate passes (not applicable)
    if not bundle_status.get("ai_clauses_detected", False):
        return {
            "status": "ok",
            "gate": "pass",
            "opportunity_id": opportunity_id,
            "message": "No AI compliance clauses detected in RFP — gate not applicable",
            "blocking_issues": [],
        }

    _summary = bundle_status.get("summary", {})

    blocking = []
    warnings = []

    # Check for missing artifacts
    for art in bundle_status.get("artifacts", []):
        if art.get("required") and art.get("status") == "missing":
            blocking.append(f"Required artifact '{art['artifact_type']}' not generated")
        elif art.get("required") and art.get("status") == "generated":
            warnings.append(f"Artifact '{art['artifact_type']}' generated but not reviewed")
        elif art.get("required") and art.get("status") == "failed":
            blocking.append(f"Artifact '{art['artifact_type']}' generation failed")

    if blocking:
        gate = "fail"
    elif warnings:
        gate = "warn"
    else:
        gate = "pass"

    result = {
        "status": "ok",
        "gate": gate,
        "opportunity_id": opportunity_id,
        "blocking_issues": blocking,
        "warnings": warnings,
        "summary": _summary,
    }

    if gate == "fail":
        result["recommendation"] = (
            "Generate all required AI compliance artifacts before proposal submission. "
            "Run: python tools/govcon/ai_clause_compliance.py --opportunity-id "
            f'"{opportunity_id}" --generate --json'
        )
    elif gate == "warn":
        result["recommendation"] = (
            "All artifacts generated. Have the proposal manager or ISSO review "
            "each artifact and update status to 'reviewed' before final submission."
        )

    return result


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Proposal Genesis — GSA American AI Clause Compliance Engine (§3.15)"
    )

    # Actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--detect", action="store_true", help="Detect AI compliance clauses in RFP text")
    group.add_argument("--generate", action="store_true", help="Generate compliance artifact bundle")
    group.add_argument("--status", action="store_true", help="Get bundle status")
    group.add_argument("--gate", action="store_true", help="Pass/fail gate evaluation")

    # Identifiers
    parser.add_argument("--opportunity-id", required=True, help="Opportunity UUID")

    # Generation options
    parser.add_argument(
        "--clause-type",
        default="gsar_552_239_7001",
        choices=list(CLAUSE_ARTIFACT_REQUIREMENTS.keys()),
        help="Clause type to generate artifacts for (default: gsar_552_239_7001)",
    )

    # Output format
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    result = None

    if args.detect:
        result = detect_ai_clauses(args.opportunity_id)

    elif args.generate:
        result = generate_compliance_bundle(args.opportunity_id, clause_type=args.clause_type)

    elif args.status:
        result = get_bundle_status(args.opportunity_id)

    elif args.gate:
        result = gate_evaluate(args.opportunity_id)
        # Exit with non-zero for CI/CD gate integration
        if result.get("gate") == "fail":
            print(json.dumps(result, indent=2, default=str))
            sys.exit(1)

    # Output
    if args.human:
        print(f"\n{'=' * 60}")
        print(f"  AI Clause Compliance — {args.opportunity_id}")
        print(f"{'=' * 60}")
        if "detected_clauses" in result:
            clauses = result["detected_clauses"]
            if clauses:
                print(f"\n  Detected AI Clauses ({len(clauses)}):")
                for c in clauses:
                    print(f"    - {c['description']} (matches: {c['match_count']})")
            else:
                print("\n  No AI compliance clauses detected in RFP.")
        if "artifacts" in result:
            print("\n  Artifact Status:")
            for a in result["artifacts"]:
                req = "[REQ]" if a.get("required") else "[OPT]"
                status_icon = {
                    "reviewed": "[PASS]",
                    "attached": "[PASS]",
                    "generated": "[WARN]",
                    "missing": "[MISS]",
                    "failed": "[FAIL]",
                    "pending": "[PEND]",
                }.get(a.get("status", ""), "[????]")
                print(f"    {req} {status_icon} {a['artifact_type']}")
        if "gate" in result:
            gate_icon = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}.get(result["gate"], "?")
            print(f"\n  Gate: {gate_icon} {result['gate'].upper()}")
            for b in result.get("blocking_issues", []):
                print(f"    BLOCK: {b}")
            for w in result.get("warnings", []):
                print(f"    WARN:  {w}")
            if "recommendation" in result:
                print(f"\n  Recommendation: {result['recommendation']}")
        if "summary" in result:
            s = result["summary"]
            print(
                f"\n  Required: {s.get('required', 0)} | "
                f"Generated: {s.get('generated', 0)} | "
                f"Reviewed: {s.get('reviewed', 0)} | "
                f"Missing: {s.get('missing', 0)}"
            )
        print()
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
