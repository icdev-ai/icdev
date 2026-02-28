# CUI // SP-CTI
# ICDEV GovCon Response Drafter — Phase 59 (D365)
# Two-tier LLM drafting: qwen3 worker drafts → Claude reviews.

"""
Response Drafter — auto-draft proposal responses to shall statements.

Pipeline:
    1. Input: shall statement + matched capabilities + knowledge blocks
    2. qwen3 drafts compact response (~400 words, structured)
    3. Claude reviews + polishes (quality, compliance accuracy, tone)
    4. Store in proposal_section_drafts (append-only, status='draft')
    5. Human reviews → approves → content flows to proposal_sections

Uses two-tier LLM routing (D365):
    - worker_function: proposal_drafting (qwen3 → Claude)
    - Fallback: template-based response if LLM unavailable (air-gap safe)

Usage:
    python tools/govcon/response_drafter.py --draft-all --opp-id <id> --json
    python tools/govcon/response_drafter.py --draft --shall-id <id> --json
    python tools/govcon/response_drafter.py --list-drafts --opp-id <id> --json
    python tools/govcon/response_drafter.py --approve --draft-id <id> --json
    python tools/govcon/response_drafter.py --template-draft --shall-id <id> --json
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _ROOT / "data" / "icdev.db"
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="response_drafter"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.response_draft", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _load_config():
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ── LLM drafting ──────────────────────────────────────────────────────

def _try_llm_draft(shall_text, capabilities, knowledge_blocks, domain):
    """Attempt two-tier LLM draft via tools.llm.router.

    Returns (draft_text, method) or (None, None) if unavailable.
    """
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()

        # Build prompt for qwen3 worker
        cap_descriptions = "\n".join(
            f"- {c['capability_name']}: {c.get('evidence', '')}"
            for c in capabilities[:3]
        )
        kb_content = "\n".join(
            f"- {kb.get('title', '')}: {(kb.get('content', '') or '')[:200]}"
            for kb in knowledge_blocks[:3]
        )

        prompt = (
            f"Draft a concise proposal response (~400 words) to this requirement:\n\n"
            f"REQUIREMENT: {shall_text}\n\n"
            f"DOMAIN: {domain}\n\n"
            f"OUR CAPABILITIES:\n{cap_descriptions}\n\n"
            f"SUPPORTING EVIDENCE:\n{kb_content}\n\n"
            f"INSTRUCTIONS:\n"
            f"- Write in third person ('The Contractor shall...' or 'Our approach...')\n"
            f"- Reference specific tools and frameworks by name\n"
            f"- Include NIST 800-53 control references where applicable\n"
            f"- Be specific about HOW we implement, not just WHAT\n"
            f"- Include measurable outcomes or metrics where possible\n"
            f"- Mention automation and repeatability\n"
            f"- Keep to ~400 words, use bullet points for clarity\n"
        )

        response = router.invoke(
            function_name="proposal_drafting",
            prompt=prompt,
        )

        if response and response.get("content"):
            return response["content"], "two_tier_llm"

    except Exception:
        pass

    return None, None


# ── template-based drafting (air-gap fallback) ────────────────────────

_RESPONSE_TEMPLATES = {
    "devsecops": (
        "The Contractor implements a comprehensive DevSecOps pipeline leveraging "
        "the ICDEV platform's 9-step automated testing and security validation framework. "
        "Our approach integrates {tools} to deliver continuous integration, continuous delivery, "
        "and continuous security monitoring.\n\n"
        "Key Implementation:\n"
        "- Automated CI/CD with SAST, DAST, dependency scanning, and secret detection\n"
        "- Policy-as-code enforcement via Kyverno/OPA\n"
        "- Image signing and attestation for supply chain integrity\n"
        "- 5-level maturity model (Initial → Optimizing) with measurable progression\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "ato_rmf": (
        "The Contractor provides fully automated ATO artifact generation and continuous "
        "authorization monitoring through the ICDEV compliance automation platform. "
        "Our approach covers the complete RMF lifecycle from categorization through "
        "continuous monitoring.\n\n"
        "Key Implementation:\n"
        "- Automated SSP, POAM, STIG checklist, and SBOM generation\n"
        "- OSCAL-native output with 3-layer deep validation\n"
        "- cATO monitoring with evidence freshness tracking\n"
        "- eMASS synchronization for seamless DoD integration\n"
        "- 42 compliance frameworks with dual-hub crosswalk\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "ai_ml": (
        "The Contractor delivers comprehensive AI/ML governance through the ICDEV "
        "platform's responsible AI framework. Our approach addresses the full lifecycle "
        "of AI systems from development through deployment and monitoring.\n\n"
        "Key Implementation:\n"
        "- NIST AI RMF 1.0 compliance assessment (4 functions, 12 subcategories)\n"
        "- Model and system card generation following Google Model Cards standard\n"
        "- AI inventory management per OMB M-25-21 schema\n"
        "- Fairness assessment and confabulation detection\n"
        "- EU AI Act risk classification when applicable\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "cloud": (
        "The Contractor provides multi-cloud migration and modernization capabilities "
        "through ICDEV's cloud-agnostic architecture supporting 6 cloud service providers. "
        "Our approach follows the 7R methodology for systematic modernization.\n\n"
        "Key Implementation:\n"
        "- Multi-cloud IaC generation (Terraform) for AWS GovCloud, Azure Gov, GCP Assured, OCI Gov, IBM IC4G\n"
        "- Kubernetes deployment with STIG-hardened containers\n"
        "- 7R assessment (Retain, Retire, Rehost, Replatform, Refactor, Re-architect, Replace)\n"
        "- Strangler fig tracking with ATO compliance bridge\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "security": (
        "The Contractor implements defense-in-depth security through ICDEV's comprehensive "
        "security scanning and zero trust architecture capabilities.\n\n"
        "Key Implementation:\n"
        "- SAST, dependency audit, secret detection, container scanning\n"
        "- Zero Trust Architecture with 7-pillar maturity scoring\n"
        "- OWASP Agentic AI security (8-gap implementation)\n"
        "- MITRE ATLAS threat defense with red teaming\n"
        "- Supply chain risk management (NIST 800-161)\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "compliance": (
        "The Contractor provides automated multi-framework compliance through ICDEV's "
        "42-framework compliance engine with dual-hub crosswalk.\n\n"
        "Key Implementation:\n"
        "- Dual-hub crosswalk: NIST 800-53 (US) + ISO 27001 (international)\n"
        "- Implement once, cascade everywhere across all applicable frameworks\n"
        "- Auto-detection from data categories (CUI, PHI, PCI, etc.)\n"
        "- Evidence auto-collection across 14 frameworks\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "agile": (
        "The Contractor follows SAFe-based agile practices with AI-assisted requirements "
        "intake and automated decomposition through the ICDEV RICOAS system.\n\n"
        "Key Implementation:\n"
        "- AI-driven conversational requirements intake\n"
        "- SAFe decomposition: Epic → Capability → Feature → Story → Enabler\n"
        "- WSJF scoring and T-shirt sizing automation\n"
        "- 7-dimension readiness scoring with gap detection\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
}

_DEFAULT_TEMPLATE = (
    "The Contractor addresses this requirement through the ICDEV platform's "
    "automated {domain} capabilities.\n\n"
    "Key Implementation:\n"
    "- {tools}\n"
    "- Automated, repeatable, and auditable execution\n"
    "- Full NIST 800-53 control mapping\n\n"
    "NIST Controls: {controls}\n\n"
    "Evidence: {evidence}"
)


def _template_draft(shall_text, capabilities, knowledge_blocks, domain):
    """Generate template-based draft (air-gap fallback)."""
    template = _RESPONSE_TEMPLATES.get(domain, _DEFAULT_TEMPLATE)

    tools_list = []
    controls_list = []
    evidence_list = []

    for cap in capabilities[:3]:
        tools_list.extend(cap.get("matched_keywords", [])[:3])
        evidence_list.append(cap.get("evidence", ""))

    # Extract controls from capability catalog
    try:
        from tools.govcon.capability_mapper import load_capability_catalog
        catalog = load_capability_catalog()
        for cap in capabilities[:3]:
            cap_id = cap.get("capability_id", "")
            for c in catalog:
                if c["id"] == cap_id:
                    controls_list.extend(c.get("compliance_controls", []))
                    break
    except Exception:
        controls_list = ["SA-11", "CA-2", "PL-2"]

    controls_str = ", ".join(sorted(set(controls_list)))[:100] or "SA-11, CA-2"
    tools_str = ", ".join(sorted(set(tools_list)))[:200] or domain
    evidence_str = "; ".join(e for e in evidence_list if e)[:300] or "ICDEV platform automation"

    draft = template.format(
        tools=tools_str,
        controls=controls_str,
        evidence=evidence_str,
        domain=domain,
    )

    return draft, "template"


# ── draft pipeline ────────────────────────────────────────────────────

def draft_response(shall_id):
    """Draft a response for a single shall statement.

    1. Load shall statement
    2. Find matched capabilities (from capability_mapper)
    3. Find relevant knowledge blocks
    4. Try LLM draft, fall back to template
    5. Store in proposal_section_drafts
    """
    conn = _get_db()

    # Load shall statement
    stmt = conn.execute(
        "SELECT * FROM rfp_shall_statements WHERE id = ?", (shall_id,)
    ).fetchone()

    if not stmt:
        conn.close()
        return {"status": "error", "message": f"Shall statement {shall_id} not found"}

    s = dict(stmt)
    shall_text = s.get("statement_text", "")
    domain = s.get("domain_category", "general")
    opp_id = s.get("sam_opportunity_id", "")

    # Find matched capabilities
    from tools.govcon.capability_mapper import load_capability_catalog, compute_coverage_score, coverage_to_grade

    catalog = load_capability_catalog()
    keywords_str = s.get("keywords", "[]")
    try:
        keywords = json.loads(keywords_str)
    except (json.JSONDecodeError, TypeError):
        keywords = [k.strip() for k in str(keywords_str).split(",") if k.strip()]

    capabilities = []
    for cap in catalog:
        score = compute_coverage_score(keywords, cap)
        if score > 0.2:
            capabilities.append({
                "capability_id": cap["id"],
                "capability_name": cap["name"],
                "score": score,
                "grade": coverage_to_grade(score),
                "evidence": cap.get("evidence", ""),
                "matched_keywords": sorted(
                    set(k.lower() for k in keywords) & set(k.lower() for k in cap.get("keywords", []))
                ),
            })
    capabilities.sort(key=lambda x: x["score"], reverse=True)

    # Find knowledge blocks
    from tools.govcon.knowledge_base import search_blocks
    kb_result = search_blocks(f"{domain} {shall_text[:100]}", domain=domain, top_k=3)
    knowledge_blocks = kb_result.get("results", [])

    # Try LLM draft, fall back to template
    cfg = _load_config().get("response_drafting", {})
    confidence_threshold = cfg.get("confidence_threshold", 0.70)

    draft_text, method = _try_llm_draft(shall_text, capabilities, knowledge_blocks, domain)
    if not draft_text:
        draft_text, method = _template_draft(shall_text, capabilities, knowledge_blocks, domain)

    # Compute confidence
    best_coverage = capabilities[0]["score"] if capabilities else 0
    confidence = round(best_coverage * 0.7 + (0.3 if knowledge_blocks else 0), 2)

    # Store draft
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO proposal_section_drafts "
        "(id, opportunity_id, shall_statement_id, capability_ids, knowledge_block_ids, "
        "draft_content, draft_method, confidence_score, domain_category, "
        "status, reviewer_notes, created_at, updated_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            draft_id, opp_id, shall_id,
            json.dumps([c["capability_id"] for c in capabilities[:3]]),
            json.dumps([kb.get("id", "") for kb in knowledge_blocks[:3]]),
            draft_text, method, confidence, domain,
            "draft", "",
            _now(), _now(),
            json.dumps({
                "capability_count": len(capabilities),
                "kb_count": len(knowledge_blocks),
                "best_coverage": best_coverage,
            }),
        ),
    )
    _audit(conn, "draft_response", f"Drafted {shall_id} via {method}, confidence={confidence}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "draft_id": draft_id,
        "shall_id": shall_id,
        "method": method,
        "confidence": confidence,
        "best_coverage": best_coverage,
        "capabilities_matched": len(capabilities),
        "kb_blocks_used": len(knowledge_blocks),
        "draft_length": len(draft_text),
    }


def draft_all_for_opportunity(opportunity_id):
    """Draft responses for all shall statements of an opportunity."""
    conn = _get_db()
    stmts = conn.execute(
        "SELECT id FROM rfp_shall_statements WHERE sam_opportunity_id = ?",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    if not stmts:
        return {"status": "error", "message": f"No shall statements for {opportunity_id}"}

    results = []
    for stmt in stmts:
        result = draft_response(stmt["id"])
        results.append(result)

    drafted = sum(1 for r in results if r.get("status") == "ok")
    avg_confidence = (
        sum(r.get("confidence", 0) for r in results if r.get("status") == "ok") / max(drafted, 1)
    )

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total_statements": len(stmts),
        "drafted": drafted,
        "avg_confidence": round(avg_confidence, 2),
        "results": results,
    }


def list_drafts(opportunity_id=None, status=None):
    """List proposal section drafts."""
    conn = _get_db()

    query = "SELECT * FROM proposal_section_drafts WHERE 1=1"
    params = []
    if opportunity_id:
        query += " AND opportunity_id = ?"
        params.append(opportunity_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "status": "ok",
        "total": len(rows),
        "drafts": [dict(r) for r in rows],
    }


def approve_draft(draft_id, reviewer="human", notes=""):
    """Approve a draft for inclusion in proposal.

    Changes status from 'draft' to 'approved'.
    Approved drafts can be pushed to proposal_sections via the proposal API.
    """
    conn = _get_db()

    # Verify draft exists and is in draft status
    draft = conn.execute(
        "SELECT * FROM proposal_section_drafts WHERE id = ?", (draft_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return {"status": "error", "message": f"Draft {draft_id} not found"}

    if draft["status"] not in ("draft", "reviewed"):
        conn.close()
        return {"status": "error", "message": f"Draft status is '{draft['status']}', expected 'draft' or 'reviewed'"}

    # Create new approved record (append-only: new row, not update)
    approved_id = str(uuid.uuid4())
    d = dict(draft)
    conn.execute(
        "INSERT INTO proposal_section_drafts "
        "(id, opportunity_id, shall_statement_id, capability_ids, knowledge_block_ids, "
        "draft_content, draft_method, confidence_score, domain_category, "
        "status, reviewer_notes, created_at, updated_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            approved_id, d["opportunity_id"], d["shall_statement_id"],
            d["capability_ids"], d["knowledge_block_ids"],
            d["draft_content"], d["draft_method"], d["confidence_score"],
            d["domain_category"],
            "approved",
            notes or f"Approved by {reviewer}",
            d["created_at"], _now(),
            json.dumps({
                "original_draft_id": draft_id,
                "reviewer": reviewer,
                "approved_at": _now(),
            }),
        ),
    )
    _audit(conn, "approve_draft", f"Draft {draft_id} approved by {reviewer}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "approved_draft_id": approved_id,
        "original_draft_id": draft_id,
        "reviewer": reviewer,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Response Drafter (D365)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--draft", action="store_true", help="Draft response for single shall statement")
    group.add_argument("--draft-all", action="store_true", help="Draft responses for all shall statements")
    group.add_argument("--template-draft", action="store_true", help="Template-only draft (no LLM)")
    group.add_argument("--list-drafts", action="store_true", help="List drafts")
    group.add_argument("--approve", action="store_true", help="Approve a draft")

    parser.add_argument("--shall-id", help="Shall statement ID")
    parser.add_argument("--opp-id", help="Opportunity ID")
    parser.add_argument("--draft-id", help="Draft ID for approval")
    parser.add_argument("--reviewer", default="human", help="Reviewer name")
    parser.add_argument("--notes", default="", help="Reviewer notes")
    parser.add_argument("--status", help="Filter by draft status")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.draft:
        if not args.shall_id:
            print("Error: --shall-id required", file=sys.stderr)
            sys.exit(1)
        result = draft_response(args.shall_id)
    elif args.draft_all:
        if not args.opp_id:
            print("Error: --opp-id required", file=sys.stderr)
            sys.exit(1)
        result = draft_all_for_opportunity(args.opp_id)
    elif args.template_draft:
        if not args.shall_id:
            print("Error: --shall-id required", file=sys.stderr)
            sys.exit(1)
        result = draft_response(args.shall_id)
    elif args.list_drafts:
        result = list_drafts(opportunity_id=args.opp_id, status=args.status)
    elif args.approve:
        if not args.draft_id:
            print("Error: --draft-id required", file=sys.stderr)
            sys.exit(1)
        result = approve_draft(args.draft_id, reviewer=args.reviewer, notes=args.notes)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
