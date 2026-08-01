# CUI // SP-CTI
# ICDEV™ GovCon Response Drafter — Phase 59 (D365)
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
import os
import sys
import threading
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── helpers ───────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection(db_path=str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="response_drafter"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), _now(), "govcon.response_draft", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _compute_quality_score(confidence_score: float, best_coverage: float) -> float:
    """Composite quality score: 60% confidence + 40% capability coverage."""
    return float(round(confidence_score * 0.6 + best_coverage * 0.4, 4))


def unresolved_placeholders(draft_row):
    """Unresolved [PLACEHOLDER] tokens for a proposal_section_drafts row.

    Re-scans draft_content (source of truth — covers drafts stored before
    metadata.placeholder_tokens existed); falls back to the tokens recorded
    by draft_response() if the scanner is unavailable.
    """
    d = dict(draft_row)
    try:
        from tools.quality.content_grounding import find_placeholders
        return find_placeholders(d.get("draft_content") or "")
    except Exception:
        try:
            meta = json.loads(d.get("metadata") or "{}")
            return list(meta.get("placeholder_tokens") or [])
        except (ValueError, TypeError):
            return []


def citation_findings(draft_row):
    """Citation defects for a proposal_section_drafts row (trust-cite-02).

    Returns citation_gate findings — one per issue — for the draft's content
    validated against the knowledge blocks it was built from. Citations are
    REQUIRED only when the draft was LLM-generated from at least one knowledge
    block (a template/air-gap draft with no KB has no per-claim sourcing to
    enforce); a citation to an id outside the block set is ALWAYS flagged as a
    hallucinated citation. Empty list == gate passes.
    """
    d = dict(draft_row)
    try:
        from tools.quality.citation_grounding import citation_gate
    except Exception:
        return []
    try:
        kb_ids = json.loads(d.get("knowledge_block_ids") or "[]")
    except (ValueError, TypeError):
        kb_ids = []
    allowed = [str(k) for k in kb_ids if k]
    method = d.get("draft_method") or ""
    require = bool(allowed) and method != "template"
    section = {
        "item_number": d.get("shall_statement_id") or d.get("id") or "?",
        "content": d.get("draft_content") or "",
        "allowed_sources": allowed,
    }
    return citation_gate([section], require_citations=require)


def _redaction_fail_closed() -> bool:
    """True when args/redaction_config.yaml sets fail_closed (trust-mask-01).

    Defaults to False (fail-open) — including when the config can't be read —
    so behavior is unchanged unless an operator explicitly opts in.
    """
    try:
        import yaml

        cfg_path = _ROOT / "args" / "redaction_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            return bool((yaml.safe_load(f) or {}).get("fail_closed", False))
    except Exception:
        return False


def _load_config():
    try:
        import yaml

        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ── Solicitation grounding (ground-prop-02) ───────────────────────────────────

# parse_solicitation() re-reads the PDF/DOCX; cache per document path so a
# --draft-all run parses each solicitation once.
_PARSED_SOLICITATION_CACHE: dict = {}


def _load_parsed_solicitation(conn, shall_row):
    """Resolve and parse the solicitation document behind a shall statement.

    Follows proposal_opportunity_id (or the SAM.gov link) to
    proposal_opportunities.rfp_document_path and runs solicitation_parser on
    it. Returns the parsed dict, or None when no document is on file or
    parsing fails — drafting proceeds ungrounded (air-gap safe).
    """
    s = dict(shall_row)
    prop_opp_id = s.get("proposal_opportunity_id")
    sam_opp_id = s.get("sam_opportunity_id")
    try:
        row = None
        if prop_opp_id:
            row = conn.execute(
                "SELECT rfp_document_path FROM proposal_opportunities WHERE id = %s",
                (prop_opp_id,),
            ).fetchone()
        if not row and sam_opp_id:
            row = conn.execute(
                "SELECT rfp_document_path FROM proposal_opportunities "
                "WHERE sam_gov_opportunity_id = %s OR id = "
                "(SELECT proposal_opportunity_id FROM sam_gov_opportunities WHERE id = %s)",
                (sam_opp_id, sam_opp_id),
            ).fetchone()
        doc_path = dict(row).get("rfp_document_path") if row else None
        if not doc_path or not Path(doc_path).exists():
            return None
        if doc_path in _PARSED_SOLICITATION_CACHE:
            return _PARSED_SOLICITATION_CACHE[doc_path]
        from tools.govcon.solicitation_parser import parse_solicitation

        parsed = parse_solicitation(doc_path)
        _PARSED_SOLICITATION_CACHE[doc_path] = parsed
        return parsed
    except Exception as exc:
        print(f"Warning: solicitation grounding unavailable: {exc}", file=sys.stderr)
        return None


# ── LLM drafting ──────────────────────────────────────────────────────


def _build_draft_prompt(
    shall_text,
    cap_descriptions,
    kb_content,
    domain,
    product_context,
    ground_truth_block="",
    strategy_block="",
):
    """Assemble the qwen3 worker prompt.

    When a parsed-solicitation ground-truth block is available it is injected
    as a hard citation constraint (ground-prop-02): the model sees the real
    section/factor/CLIN structure instead of inventing one.

    The capture-strategy block carries the company win themes so every drafted
    section argues one message instead of being written in isolation.
    """
    grounding_section = f"{ground_truth_block}\n\n" if ground_truth_block else ""
    strategy_section = f"{strategy_block}\n\n" if strategy_block else ""
    grounding_rule = (
        "- Cite ONLY the sections, instructions, volumes, factors, and CLINs "
        "listed in the SOLICITATION GROUND TRUTH block\n"
        if ground_truth_block
        else ""
    )
    return (
        f"{grounding_section}"
        f"{strategy_section}"
        f"Draft a concise proposal response (~400 words) to this requirement:\n\n"
        f"REQUIREMENT: {shall_text}\n\n"
        f"DOMAIN: {domain}\n"
        f"{product_context}\n"
        f"OUR CAPABILITIES:\n{cap_descriptions}\n\n"
        f"SUPPORTING EVIDENCE:\n{kb_content}\n\n"
        f"INSTRUCTIONS:\n"
        f"{grounding_rule}"
        f"- Write in third person ('The Contractor shall...' or 'Our approach...')\n"
        f"- Reference specific tools and frameworks by name\n"
        f"- Include NIST 800-53 control references where applicable\n"
        f"- Be specific about HOW we implement, not just WHAT\n"
        f"- Include measurable outcomes or metrics where possible\n"
        f"- Mention automation and repeatability\n"
        f"- Keep to ~400 words, use bullet points for clarity\n"
        f"- For every factual claim, cite the supporting evidence inline using "
        f"[source: <id>] with an id shown in SUPPORTING EVIDENCE above. Do NOT "
        f"cite an id that is not listed, and omit a claim rather than invent one.\n"
    )


def _try_llm_draft(
    shall_text, capabilities, knowledge_blocks, domain, parsed_solicitation=None, opportunity_id=""
):
    """Attempt two-tier LLM draft via tools.llm.router.

    Returns (draft_text, method) or (None, None) if unavailable.

    Phase 70: Proposal content is sanitized before LLM invocation.
    proposal_drafting routes to local Ollama only (args/llm_config.yaml).
    Sanitizer runs as defense-in-depth even for local routing.
    """
    try:
        # Cortex adoption: proposal drafting routes through the GOVERNED
        # cortex.complete facade (see the LLM call below), matching the sibling
        # RFI workbench — provenance + audit + egress redaction + budget
        # attribution + the proposal domain lens instead of a raw router.invoke.
        from tools.cortex import api as cortex_api
        from tools.cortex.schemas import CortexContext

        # Build prompt for qwen3 worker
        cap_descriptions = "\n".join(f"- {c['capability_name']}: {c.get('evidence', '')}" for c in capabilities[:3])
        # Label each knowledge block with its id so the model can cite it inline
        # via [source: <id>]. The same ids are persisted as knowledge_block_ids,
        # so the citation gate can validate the draft against them (trust-cite-02).
        kb_content = "\n".join(
            f"- [source: {kb.get('id', '?')}] {kb.get('title', '')}: {(kb.get('content', '') or '')[:200]}"
            for kb in knowledge_blocks[:3]
        )

        # Solicitation ground-truth constraint (ground-prop-02)
        ground_truth_block = ""
        if parsed_solicitation:
            try:
                from tools.govcon.solicitation_grounding import build_ground_truth_block

                ground_truth_block = build_ground_truth_block(parsed_solicitation)
            except Exception:
                ground_truth_block = ""

        # Check for product-level context
        product_key = _detect_product_template(shall_text, domain)
        product_context = ""
        if product_key == "icdev_platform":
            product_context = (
                "\nPRODUCT CONTEXT: This response should describe ICDEV™ as a complete "
                "integrated platform — a system that builds systems. Emphasize that ICDEV™ is "
                "delivered on-premises to the customer as a unified platform with 15 agents, "
                "42 compliance frameworks, 500+ tools, and 6-language support. It is NOT a "
                "collection of point tools — it is an integrated autonomous development system.\n"
            )
        elif product_key == "contract_management_portal":
            product_context = (
                "\nPRODUCT CONTEXT: This response should describe the Contract Performance "
                "Management Portal (CPMP) as a complete post-award delivery tracking system. "
                "Emphasize CDRL tracking (DD Form 1423), SOW obligation monitoring, CPARS risk "
                "scoring (4-factor formula), proactive reminders, and COR visibility. The portal "
                "closes the Shipley lifecycle from proposal to contract delivery.\n"
            )

        # Capture strategy — the same win themes the RFI workbench injects, so an RFI
        # response and the proposal that follows it argue one message. resolve_strategy
        # accepts a SAM solicitation number as well as a proposal_opportunities.id.
        strategy_block = ""
        try:
            from tools.govcon.capture_strategy import MODE_FULL, build_strategy_block, resolve_strategy

            strategy_block = build_strategy_block(
                resolve_strategy(opportunity_id=opportunity_id), mode=MODE_FULL
            )
        except Exception as exc:
            print(f"Warning: capture strategy unavailable: {exc}", file=sys.stderr)

        prompt = _build_draft_prompt(
            shall_text,
            cap_descriptions,
            kb_content,
            domain,
            product_context,
            ground_truth_block,
            strategy_block,
        )

        # Phase 70: Sanitize prompt before LLM invocation (defense-in-depth)
        # proposal_drafting already routes to local Ollama only via llm_config.yaml,
        # but sanitizer catches PII as additional protection layer.
        try:
            from tools.redaction.govcon_sanitizer import GovConSanitizer

            _sanitizer = GovConSanitizer()
            prompt, _redaction_meta = _sanitizer.sanitize_for_llm(
                prompt,
                function_name="proposal_drafting",
                impact_level="IL4",
                is_local_only=True,  # Routes to Ollama — skip_for_local honored
            )
        except ImportError:
            # trust-mask-01: honor redaction.fail_closed here too. Default
            # (fail-open) proceeds unsanitized as before; fail-closed aborts.
            # The central router gate (_pre_invoke_redaction) is the primary
            # enforcement — this is defense-in-depth on the pre-LLM prompt.
            if _redaction_fail_closed():
                raise RuntimeError(
                    "Redaction module unavailable and redaction.fail_closed is "
                    "enabled — proposal draft LLM call blocked."
                )
            # Redaction module not installed — proceed unsanitized (fail-open)

        # Governed facade: the "proposal_drafting" routing function (local-only
        # per llm_config.yaml) is preserved; domain="proposal" applies the
        # capture-manager lens persona. On failure, fall through to the template
        # draft (the except below), exactly as before.
        cx = cortex_api.complete(
            prompt,
            function="proposal_drafting",
            ctx=CortexContext(
                classification="CUI", domain="proposal", agent_id="proposal-drafter"
            ),
        )
        if cx and cx.text:
            return cx.text, "two_tier_llm"

    except Exception as exc:
        print(f"Warning: LLM draft failed: {exc}", file=sys.stderr)

    return None, None


# ── template-based drafting (air-gap fallback) ────────────────────────

_RESPONSE_TEMPLATES = {
    "devsecops": (
        "The Contractor implements a comprehensive DevSecOps pipeline leveraging "
        "the ICDEV™ platform's 9-step automated testing and security validation framework. "
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
        "authorization monitoring through the ICDEV™ compliance automation platform. "
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
        "The Contractor delivers comprehensive AI/ML governance through the ICDEV™ "
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
        "through ICDEV™'s cloud-agnostic architecture supporting 6 cloud service providers. "
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
        "The Contractor implements defense-in-depth security through ICDEV™'s comprehensive "
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
        "The Contractor provides automated multi-framework compliance through ICDEV™'s "
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
        "intake and automated decomposition through the ICDEV™ RICOAS system.\n\n"
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
    "The Contractor addresses this requirement through the ICDEV™ platform's "
    "automated {domain} capabilities.\n\n"
    "Key Implementation:\n"
    "- {tools}\n"
    "- Automated, repeatable, and auditable execution\n"
    "- Full NIST 800-53 control mapping\n\n"
    "NIST Controls: {controls}\n\n"
    "Evidence: {evidence}"
)

# ── Product-level templates (whole-product descriptions) ──────────

_PRODUCT_TEMPLATES = {
    "icdev_platform": (
        "The Contractor delivers the ICDEV™ (Intelligent Certified Development) platform — "
        "a complete autonomous software development system that generates ATO-ready "
        "government applications from natural language requirements. ICDEV™ orchestrates "
        "15 specialized AI agents across its 6-layer FORGE framework to handle the full "
        "SDLC with TDD/BDD, multi-framework compliance automation, and continuous "
        "authorization monitoring.\n\n"
        "Platform Capabilities Delivered On-Premises:\n"
        "- 42 compliance frameworks with dual-hub crosswalk (NIST 800-53 + ISO 27001) — "
        "implement a control once, cascade everywhere\n"
        "- 500+ deterministic tools for reproducible, auditable execution — no probabilistic "
        "business logic\n"
        "- 6 first-class programming languages (Python, Java, Go, Rust, C#, TypeScript) with "
        "9-step testing pipeline and TDD/BDD\n"
        "- Multi-cloud IaC generation for 6 CSPs (AWS GovCloud, Azure Government, GCP Assured "
        "Workloads, OCI Government, IBM IC4G, and local/air-gapped)\n"
        "- OSCAL-native compliance output with 3-layer deep validation\n"
        "- Zero Trust Architecture with 7-pillar maturity scoring (DoD ZTA Strategy)\n"
        "- AI governance (NIST AI RMF, OMB M-25-21/M-26-04, EU AI Act, GAO accountability)\n"
        "- Complete audit trail (append-only, NIST AU compliant) for every action\n\n"
        "Customer Value:\n"
        "- Reduces ATO timeline from 12-18 months to weeks through automated artifact generation\n"
        "- Eliminates compliance drift with continuous authorization monitoring (cATO)\n"
        "- Supports air-gapped (IL6/SIPR) deployment with no internet dependency\n"
        "- One platform replaces 10+ point solutions, lowering total cost of ownership\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
    "contract_management_portal": (
        "The Contractor provides the Contract Performance Management Portal (CPMP) — "
        "a post-award delivery tracking system that provides real-time visibility into "
        "contract execution, CDRL compliance, SOW obligation fulfillment, and CPARS risk.\n\n"
        "Portal Capabilities:\n"
        "- Automatic CDRL import from Section H (DD Form 1423) with frequency-based due date "
        "calculation (Monthly, Quarterly, Semi-annual, Annual, As Required, One-time)\n"
        "- SOW obligation extraction and tracking from Sections C, F, and H "
        "(shall/must/will statements with compliance status)\n"
        "- CPARS risk scoring (0.0-1.0) using weighted formula: 35% overdue CDRLs + "
        "25% rejected CDRLs + 25% non-compliant obligations + 15% late deliveries\n"
        "- Proactive deliverable reminders at 30/14/7/1 day intervals with severity escalation\n"
        "- Contract dashboard with CPARS risk gauges, compliance bars, and upcoming timelines\n"
        "- Full traceability from original proposal to contract to individual deliverables\n\n"
        "Customer Value:\n"
        "- Eliminates manual CDRL tracking spreadsheets with automated reminders\n"
        "- Reduces CPARS risk through proactive early warning on at-risk deliverables\n"
        "- Provides government CORs with transparent, real-time contractor performance visibility\n"
        "- Closes the Shipley lifecycle — connects proposal promises to actual delivery outcomes\n"
        "- Delivery performance feeds back into future proposals for continuous improvement\n\n"
        "NIST Controls: {controls}\n\n"
        "Evidence: {evidence}"
    ),
}

# Keywords that trigger product-level responses instead of component-level
_PRODUCT_TRIGGER_KEYWORDS = {
    "icdev_platform": [
        "integrated platform",
        "complete solution",
        "end-to-end",
        "full lifecycle",
        "software factory",
        "autonomous development",
        "unified platform",
        "meta-builder",
        "system that builds",
        "development platform",
        "coding platform",
        "SDLC platform",
        "all-in-one",
        "comprehensive platform",
        "integrated development",
    ],
    "contract_management_portal": [
        "contract management",
        "CDRL",
        "contract data requirements",
        "CPARS",
        "post-award",
        "deliverable tracking",
        "contract performance",
        "obligation tracking",
        "contract administration",
        "delivery management",
        "DD Form 1423",
        "contract monitoring",
        "performance monitoring",
        "COR visibility",
    ],
}


def _detect_product_template(shall_text, domain):
    """Detect if a shall statement should use a product-level template.

    Returns product key or None.
    """
    text_lower = shall_text.lower()
    best_product = None
    best_score = 0

    for product_key, triggers in _PRODUCT_TRIGGER_KEYWORDS.items():
        score = sum(1 for t in triggers if t.lower() in text_lower)
        if score > best_score:
            best_score = score
            best_product = product_key

    # Require at least 2 keyword matches to trigger product template
    if best_score >= 2:
        return best_product

    # Also trigger for management domain + contract/deliverable keywords
    if domain == "management" and best_score >= 1:
        return best_product

    return None


def _template_draft(shall_text, capabilities, knowledge_blocks, domain):
    """Generate template-based draft (air-gap fallback).

    Checks for product-level template first, then falls back to domain template.
    """
    # Check if this requirement maps to a whole-product response
    product_key = _detect_product_template(shall_text, domain)
    if product_key and product_key in _PRODUCT_TEMPLATES:
        template = _PRODUCT_TEMPLATES[product_key]
    else:
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
    evidence_str = "; ".join(e for e in evidence_list if e)[:300] or "ICDEV™ platform automation"

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
    stmt = conn.execute("SELECT * FROM rfp_shall_statements WHERE id = %s", (shall_id,)).fetchone()

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
            capabilities.append(
                {
                    "capability_id": cap["id"],
                    "capability_name": cap["name"],
                    "score": score,
                    "grade": coverage_to_grade(score),
                    "evidence": cap.get("evidence", ""),
                    "matched_keywords": sorted(
                        set(k.lower() for k in keywords) & set(k.lower() for k in cap.get("keywords", []))
                    ),
                }
            )
    capabilities.sort(key=lambda x: x["score"], reverse=True)

    # Find knowledge blocks — include product-level blocks when applicable
    from tools.govcon.knowledge_base import search_blocks

    kb_result = search_blocks(f"{domain} {shall_text[:100]}", domain=domain, top_k=3)
    knowledge_blocks = kb_result.get("results", [])

    # Also search for product-level blocks if requirement is cross-domain
    product_key = _detect_product_template(shall_text, domain)
    if product_key:
        product_search = (
            "ICDEV™ platform" if product_key == "icdev_platform" else "contract management portal CDRL CPARS"
        )
        product_kb = search_blocks(product_search, top_k=2)
        # Prepend product blocks (higher priority)
        product_results = product_kb.get("results", [])
        existing_ids = {kb.get("id") for kb in knowledge_blocks}
        for pkb in product_results:
            if pkb.get("id") not in existing_ids:
                knowledge_blocks.insert(0, pkb)

    # Try LLM draft, fall back to template
    cfg = _load_config().get("response_drafting", {})
    cfg.get("confidence_threshold", 0.70)

    # Ground drafting against the real solicitation structure when the
    # opportunity has a parsed document on file (ground-prop-02).
    parsed_solicitation = _load_parsed_solicitation(conn, s)

    draft_text, method = _try_llm_draft(
        shall_text,
        capabilities,
        knowledge_blocks,
        domain,
        parsed_solicitation=parsed_solicitation,
        opportunity_id=opp_id,
    )
    if not draft_text:
        draft_text, method = _template_draft(shall_text, capabilities, knowledge_blocks, domain)

    # Deterministic anti-hallucination check: unresolved [PLACEHOLDER] tokens
    # in the draft are recorded so reviewers/WriteGuard can flag them before
    # the section is promoted.
    try:
        from tools.quality.content_grounding import find_placeholders
        placeholder_tokens = find_placeholders(draft_text)
    except Exception:
        placeholder_tokens = []

    # Citation validation: does the draft cite only the knowledge blocks it was
    # built from? Recorded at draft time for reviewer visibility; enforced as a
    # blocking gate on approve_draft (trust-cite-02).
    try:
        from tools.quality.citation_grounding import validate_citations
        _kb_ids_for_cite = [kb.get("id", "") for kb in knowledge_blocks[:3] if kb.get("id")]
        citation_report = validate_citations(draft_text, _kb_ids_for_cite)
    except Exception:
        citation_report = None

    # halluc-01: non-blocking confabulation assessment recorded for reviewer
    # visibility (fabricated citations / contradictions / hedging).
    try:
        from tools.security.confabulation_detector import assess as _confab_assess
        confab_report = _confab_assess(draft_text)
    except Exception:
        confab_report = None

    # Citation validation against the parsed solicitation (ground-prop-02):
    # any Section/Factor/Volume/CLIN reference that doesn't exist in the
    # document is recorded for reviewers/WriteGuard (invalid_citation).
    invalid_references = []
    citations_checked = 0
    if parsed_solicitation:
        try:
            from tools.govcon.solicitation_grounding import validate_references

            ref_check = validate_references(draft_text, parsed_solicitation)
            invalid_references = ref_check["invalid_refs"]
            citations_checked = ref_check["checked"]
        except Exception as exc:
            print(f"Warning: citation validation failed: {exc}", file=sys.stderr)

    # Compute confidence
    best_coverage = capabilities[0]["score"] if capabilities else 0
    confidence = round(best_coverage * 0.7 + (0.3 if knowledge_blocks else 0), 2)

    # Optional, off-by-default: an independent Council pressure-test via
    # idea_lab's Specialist (tools/govcon/specialist_consult.py) as a third
    # opinion before this draft is stored -- additive to, never a
    # replacement for, the two-tier qwen3/Claude drafting pipeline above.
    # Only the requirement text + domain are sent (never the drafted
    # response itself), and specialist_consult.py sanitizes that before it
    # leaves this process. Any failure/timeout/disabled config is silently
    # skipped -- this must never block the draft from being stored.
    specialist_consult_result = None
    if os.environ.get("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        try:
            from tools.govcon.specialist_consult import request_council_consult

            specialist_consult_result = request_council_consult(
                "GovCon proposal response drafting",
                f"Third-opinion pressure-test requested for this requirement: {shall_text[:300]}",
            )
        except Exception as consult_exc:
            print(f"Specialist Council consult skipped: {consult_exc}", file=sys.stderr)

    # Store draft
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO proposal_section_drafts "
        "(id, opportunity_id, shall_statement_id, capability_ids, knowledge_block_ids, "
        "draft_content, draft_method, confidence_score, domain_category, "
        "status, reviewer_notes, created_at, updated_at, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            draft_id,
            opp_id,
            shall_id,
            json.dumps([c["capability_id"] for c in capabilities[:3]]),
            json.dumps([kb.get("id", "") for kb in knowledge_blocks[:3]]),
            draft_text,
            method,
            confidence,
            domain,
            "draft",
            "",
            _now(),
            _now(),
            json.dumps(
                {
                    "capability_count": len(capabilities),
                    "kb_count": len(knowledge_blocks),
                    "best_coverage": best_coverage,
                    **({"placeholder_tokens": placeholder_tokens} if placeholder_tokens else {}),
                    **({"citation_report": citation_report} if citation_report else {}),
                    **({"confabulation": confab_report} if confab_report else {}),
                    **(
                        {"citations_checked": citations_checked, "invalid_references": invalid_references}
                        if parsed_solicitation
                        else {}
                    ),
                    **({"specialist_consult": specialist_consult_result} if specialist_consult_result else {}),
                }
            ),
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
        "placeholder_tokens": placeholder_tokens,
        "citation_report": citation_report,
        "confabulation": confab_report,
        "grounded": bool(parsed_solicitation),
        "citations_checked": citations_checked,
        "invalid_references": invalid_references,
    }


def draft_all_for_opportunity(opportunity_id, method="auto"):
    """Draft responses for all shall statements of an opportunity."""
    conn = _get_db()
    stmts = conn.execute(
        "SELECT id FROM rfp_shall_statements WHERE sam_opportunity_id = %s",
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
    avg_confidence = sum(r.get("confidence", 0) for r in results if r.get("status") == "ok") / max(drafted, 1)

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


def approve_draft(draft_id, reviewer="human", notes="", force_placeholders=False, force_citations=False):
    """Approve a draft for inclusion in proposal.

    Changes status from 'draft' to 'approved'.
    Approved drafts can be pushed to proposal_sections via the proposal API.

    Promotion is blocked while unresolved [PLACEHOLDER] tokens remain in the
    draft (gate=placeholder_guard, mirroring the RFI export gate), or while the
    draft has citation defects — missing citations on grounded content or a
    citation to a source it was not built from (gate=citation_guard,
    trust-cite-02). force_placeholders / force_citations bypass the respective
    gate after human review and write an explicit audit trail entry.
    """
    conn = _get_db()

    # Verify draft exists and is in draft status
    draft = conn.execute("SELECT * FROM proposal_section_drafts WHERE id = %s", (draft_id,)).fetchone()

    if not draft:
        conn.close()
        return {"status": "error", "message": f"Draft {draft_id} not found"}

    if draft["status"] not in ("draft", "reviewed"):
        conn.close()
        return {"status": "error", "message": f"Draft status is '{draft['status']}', expected 'draft' or 'reviewed'"}

    d = dict(draft)

    # Placeholder gate (ground-prop-03)
    placeholder_tokens = unresolved_placeholders(d)
    if placeholder_tokens and not force_placeholders:
        conn.close()
        return {
            "status": "blocked",
            "gate": "placeholder_guard",
            "message": "Unresolved [PLACEHOLDER] tokens remain — resolve or pass force_placeholders=True",
            "placeholder_tokens": placeholder_tokens,
            "draft_id": draft_id,
        }
    if placeholder_tokens:
        _audit(
            conn,
            "placeholder_guard_override",
            f"Draft {draft_id} promoted by {reviewer} despite {len(placeholder_tokens)} "
            f"unresolved placeholder token(s): {', '.join(placeholder_tokens)}",
        )

    # Citation gate (trust-cite-02) — mirrors placeholder_guard semantics.
    citation_issues = citation_findings(d)
    if citation_issues and not force_citations:
        conn.close()
        return {
            "status": "blocked",
            "gate": "citation_guard",
            "message": "Draft has citation defects — add [source: <id>] citations or "
                       "pass force_citations=True after review",
            "citation_findings": citation_issues,
            "draft_id": draft_id,
        }
    if citation_issues:
        _audit(
            conn,
            "citation_guard_override",
            f"Draft {draft_id} promoted by {reviewer} despite {len(citation_issues)} "
            f"citation defect(s): {json.dumps(citation_issues)}",
        )

    # Create new approved record (append-only: new row, not update)
    approved_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO proposal_section_drafts "
        "(id, opportunity_id, shall_statement_id, capability_ids, knowledge_block_ids, "
        "draft_content, draft_method, confidence_score, domain_category, "
        "status, reviewer_notes, created_at, updated_at, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            approved_id,
            d["opportunity_id"],
            d["shall_statement_id"],
            d["capability_ids"],
            d["knowledge_block_ids"],
            d["draft_content"],
            d["draft_method"],
            d["confidence_score"],
            d["domain_category"],
            "approved",
            notes or f"Approved by {reviewer}",
            d["created_at"],
            _now(),
            json.dumps(
                {
                    "original_draft_id": draft_id,
                    "reviewer": reviewer,
                    "approved_at": _now(),
                    **(
                        {"placeholder_guard_override": placeholder_tokens}
                        if placeholder_tokens
                        else {}
                    ),
                    **(
                        {"citation_guard_override": citation_issues}
                        if citation_issues
                        else {}
                    ),
                }
            ),
        ),
    )
    _audit(conn, "approve_draft", f"Draft {draft_id} approved by {reviewer}")
    conn.commit()
    conn.close()

    # Approved prose becomes reusable evidence for the next pursuit. Backgrounded so
    # approval stays responsive; promote_proposal_draft is idempotent, never raises,
    # and declines drafts that were force-approved past a gate.
    threading.Thread(
        target=_promote_evidence_background, args=(approved_id,), daemon=True
    ).start()

    return {
        "status": "ok",
        "approved_draft_id": approved_id,
        "original_draft_id": draft_id,
        "reviewer": reviewer,
    }


def _promote_evidence_background(approved_draft_id):
    """Index an approved proposal draft into the evidence corpus. Never raises."""
    try:
        from tools.govcon.evidence_corpus import promote_proposal_draft

        promote_proposal_draft(approved_draft_id)
    except Exception as exc:
        print(f"Warning: evidence promotion failed: {exc}", file=sys.stderr)


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovCon Response Drafter (D365)")
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
    parser.add_argument(
        "--force-placeholders",
        action="store_true",
        help="Approve despite unresolved [PLACEHOLDER] tokens (audited override)",
    )
    parser.add_argument(
        "--force-citations",
        action="store_true",
        help="Approve despite citation defects (audited override)",
    )
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
        result = approve_draft(
            args.draft_id,
            reviewer=args.reviewer,
            notes=args.notes,
            force_placeholders=args.force_placeholders,
            force_citations=args.force_citations,
        )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
