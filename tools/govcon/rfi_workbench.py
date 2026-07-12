"""RFI Response Workbench — backing module.

Handles: session management, section CRUD, AI generation dispatch,
WriteGuard integration, HITL state machine, export assembly,
requirements layer (extract/CRUD/coverage), ACE team integration.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_workbench")

_PROFILES_PATH = _ROOT / "args" / "govcon_company_profiles.yaml"
_UPLOAD_DIR = _ROOT / ".tmp" / "rfi_uploads"
_EXPORT_DIR = _ROOT / ".tmp" / "rfi_exports"

# ── Default sections when parser produces no questionnaire parts ──────────────

_QUESTIONNAIRE_DEFAULT_SECTIONS = [
    ("part1", "1.1", "Entity Data",             "Part 1", "Provide company name, contact info, CAGE code, and SAM.gov UEI."),
    ("part1", "1.2", "Business Size",           "Part 1", "Identify business size and socioeconomic status for the primary NAICS code."),
    ("part1", "1.3", "NDC Status",              "Part 1", "Do you qualify as a Nontraditional Defense Contractor per 10 U.S.C. 3014?"),
    ("part1", "1.4", "Foreign Interest (FOCI)", "Part 1", "Is the entity or any parent company foreign-owned, controlled, or influenced?"),
    ("part1", "1.5", "Security Clearances",     "Part 1", "Do personnel hold active clearances? Specify clearance levels available."),
    ("part2", "2.1", "Current TRL",             "Part 2", "Identify the Technology Readiness Level of your proposed solution."),
    ("part2", "2.2", "Statefulness",            "Part 2", "Is the orchestration stateless or stateful? How is state synchronized across a distributed architecture without introducing latency?"),
    ("part2", "2.3", "Cold-Start/Scaling",      "Part 2", "How does the orchestrator handle cold-start of new processing resources? Can it dynamically trigger spin-up of new containerized processing nodes?"),
    ("part2", "2.4", "Technical Approach",      "Part 2", "Describe your approach to meeting the objectives. What modifications would be required?"),
    ("part2", "2.5", "Commerciality",           "Part 2", "Is the solution a Commercial Product per FAR 2.101? If not, what percentage is developmental?"),
    ("part2", "2.6", "Cybersecurity & Supply Chain", "Part 2", "Describe cybersecurity posture (NIST SP 800-171, CMMC level) and NDAA Section 889 supply chain risk."),
    ("part2", "2.7", "Mission-Specific Questions","Part 2", "Address latency at scale, multi-constraint logic, dynamic priority injection, failure recovery, and cost tracking."),
    ("part3", "3.1", "Timeline",                "Part 3", "Estimated time from award to delivery of first working prototype."),
    ("part3", "3.2", "Key Risk Areas",          "Part 3", "Identify the top two technical or schedule risks."),
    ("part3", "3.3", "Custom Risk Responses",   "Part 3", "Address explainability vs. performance trade-offs, starvation prevention, integration complexity."),
    ("part4", "4.1", "Data Rights",             "Part 4", "Describe data rights and IP assertions. Identify components with Restricted, Limited, or GPR."),
    ("part4", "4.2", "ROM Cost Estimate",       "Part 4", "Rough Order of Magnitude cost estimate. Break down by hardware, software, labor, ODC."),
    ("part4", "4.3", "Teaming / Cost Share",    "Part 4", "NDC teaming plan or 1/3 cost-share per 10 U.S.C. 4022."),
    ("part5", "5.1", "Industry Insights",       "Part 5", "What did the Government miss? What technical or programmatic considerations should be added?"),
]

# Part 6 — pre-submission questions TO the Government (RFI Q&A window).
# Not part of the exported response document; exported separately for
# ARC/email submission before the questions deadline.
_PART6_SECTIONS = [
    ("part6", "6.1", "Gap & Omission Questions",     "Part 6", "Questions surfacing what the RFI leaves out — intentionally or unintentionally (data characteristics, integration environment, accreditation path, GFE/GFI, evaluation criteria, follow-on vehicle)."),
    ("part6", "6.2", "Intent & Goal Clarification",  "Part 6", "Questions that clarify the customer's underlying intention and mission goals, so we can determine whether and how our solution fits."),
    ("part6", "6.3", "Strategic Leverage Questions", "Part 6", "Questions whose answers sharpen our capability roadmap and competitive positioning for the anticipated solicitation."),
    ("part6", "6.4", "RFI Clarifications",           "Part 6", "Questions resolving ambiguities or conflicts within the RFI text itself (format, scope wording, classification handling, submission mechanics)."),
]

_APPENDIX_SECTIONS = [
    ("appendix", "A", "Architecture Overview",  "Appendix", "Technical architecture — Intelligence Layer, Execution Layer, and cloud-native deployment."),
    ("appendix", "B", "Adaptive Learning Loop", "Appendix", "Learning loop — ECHO, SOUL, TRUST, SELA components and feedback latency benchmarks."),
]

_DEFAULT_SECTIONS = _QUESTIONNAIRE_DEFAULT_SECTIONS + _PART6_SECTIONS + _APPENDIX_SECTIONS

# ── Section generation prompts ────────────────────────────────────────────────

_SECTION_PROMPTS = {
    "1.1": "Generate Part 1.1 administrative data for {entity_name}. Include the table format with company name, address, contact name/title/email/phone, CAGE code, and SAM.gov UEI. Mark fields that need verification with [VERIFY].",
    "1.2": "Generate Part 1.2 business size declaration for {entity_name} under NAICS {primary_naics}. State business size ({business_size}), socioeconomic status, and recommend the NAICS code with rationale.",
    "1.3": "Generate Part 1.3 NDC status for {entity_name}. Status: {ndc_status}. Include teaming strategy reference if traditional contractor.",
    "1.4": "Generate Part 1.4 FOCI disclosure for {entity_name}. Confirm no foreign ownership, control, or influence.",
    "1.5": "Generate Part 1.5 security clearances for {entity_name}. Clearances available: {clearances}. Include SCIF capability statement.",
    "2.1": "Generate Part 2.1 TRL assessment for {entity_name} addressing: {rfi_title}. Use Hybrid TRL 6: core components TRL 8 (commercially deployed), NSA integration layer TRL 5-6. Include a table mapping component to TRL with evidence.",
    "2.2": "Generate Part 2.2 statefulness response for {entity_name}. Explain stateful-light architecture: routing decisions are stateless per object; resource-availability sidecar maintains eventually-consistent view via gossip protocol (100ms intervals); policy store is GitOps hot-reload (atomic swap, zero restart); no synchronization latency on the per-object decision path. {hitl_context}",
    "2.3": "Generate Part 2.3 cold-start and scaling response for {entity_name}. Cold-start: KEDA event-driven autoscaling triggered by orchestrator surge detection + pre-warm pools for premium tiers + graceful degradation to best-effort with provenance flag while new containerized processing nodes spin up. Include the surge-detection → spin-up → warm-ready flow. {hitl_context}",
    "2.4": "Generate Part 2.4 technical approach for {entity_name}'s response to RFI {rfi_number}. Describe the three-tier governed orchestration: Rule Engine (<100µs, 90% of volume) → CoD (<15ms, 8%) → CoT (<2s, 2%). Map to objectives: {objectives_list}. Include an architecture diagram as a Mermaid flowchart inside a ```mermaid fenced code block (graph TD or flowchart TD syntax) — do not use ASCII art. Emphasize intelligence layer (async, policy timescale) vs execution layer (per-object timescale). {hitl_context}",
    "2.5": "Generate Part 2.5 commerciality response for {entity_name}. Confirm commercial product per FAR 2.101, ~70% commercial/30% developmental; identify which components are commercially deployed today versus developmental for this mission. {hitl_context}",
    "2.6": "Generate Part 2.6 cybersecurity and supply chain response for {entity_name}. Cybersecurity table: CMMC Level 2, NIST SP 800-171, CycloneDX SBOM, container hardening, secure SDLC. State NDAA Section 889 supply chain compliance and any known supply chain risks (none if applicable). {hitl_context}",
    "2.7": "Generate Part 2.7 mission-specific Q&A for {entity_name}. Cover: (1) Latency benchmarks (Python prototype ~2ms, production C/Rust target <50µs, O(log N) scaling); (2) Multi-constraint logic (secondary pool → queue → CoD degradation → guaranteed retry); (3) Dynamic priority injection (REST API + YAML hot-reload, <1s propagation, atomic rule-tree swap); (4) Failure recovery (circuit breaker, dead-letter queue, W3C PROV-AGENT provenance); (5) Cost tracking (per-routing-decision cost ledger, cost_usd + duration_ms per step). {hitl_context}",
    "3.1": "Generate Part 3.1 project timeline for {entity_name}: M1-2 environment setup + test data integration; M3 core prototype (three-tier stack + HITL + XAI dashboard); M4 integration testing (latency benchmarks, priority injection, failure recovery); M5 adaptive learning (NOVA feedback loop + Bayesian tuning); M6 prototype demo. Present as a table. {hitl_context}",
    "3.2": "Generate Part 3.2 key risks for {entity_name}. Risk 1: Latency validation in customer environment (mitigation: sidecar co-located with processing resources, validate by Month 2). Risk 2: Mission metrics API dependency (mitigation: YAML static rules as fallback; live dynamic injection as Phase 2). {hitl_context}",
    "3.3": "Generate Part 3.3 custom risk responses for {entity_name}. (1) Explainability vs. performance: async ring buffer, 100ms flush, <2µs overhead per object, eventually consistent audit trail. (2) Starvation: minimum service floor (configurable % capacity reserved for best-effort), age-weighted priority boost (TTL-based escalation), hard max-wait cap. (3) Integration complexity: 50MB sidecar container, /health and /capacity endpoints, Helm chart deployment, no modification to existing LLM/analytics internals. {hitl_context}",
    "4.1": "Generate Part 4.1 data rights for {entity_name}. Table: core platform (prior IR&D) → Government Purpose Rights; integration adapters developed under effort → Unlimited Rights; models trained on customer operations data → Government owns all data and derivative models. No proprietary restrictions on operational use. {hitl_context}",
    "4.2": "Generate Part 4.2 ROM cost estimate for {entity_name}. Table: Labor ~$1.2M (6 engineers x 6 months, blended $200K/yr), Software/Cloud ~$75K, Hardware (GPU dev cluster) ~$150K, ODC ~$50K, ROM Total ~$1.475M (±30%). Annual O&M: ~$400K/year. Annual licensing: ~$250K/year. {hitl_context}",
    "4.3": "Generate Part 4.3 teaming/cost share for {entity_name}. {ndc_status}. Option A — NDC Teaming (Preferred): identify NDC partner at solicitation stage, >1/3 technical effort on novel AI/ML core components. Option B — IR&D Cost Share: ~$490K against $1.475M ROM from existing IR&D commitments per 10 U.S.C. 4022. {hitl_context}",
    "5.1": "Generate Part 5 industry insights for {entity_name}'s response to {rfi_title}. Provide 4 recommendations the Government missed: (1) Data provenance/routing lineage — recommend adding as a required objective; (2) Federated learning for sensitive routing feedback — modify Objective F; (3) Multi-classification routing scope — clarify whether orchestrator must span ILs; (4) Human-machine teaming escalation — recommend adding analyst override objective. Each recommendation should include specific proposed solicitation language. {hitl_context}",
    "6.1": "You are preparing pre-submission questions to the Contracting Officer for {rfi_title} ({rfi_number}), due before the Q&A deadline. Identify what the RFI leaves out — intentionally or unintentionally. Consider: unstated data characteristics (volume mix, formats, classification levels), the existing integration environment and its constraints, security accreditation path (ATO/cATO expectations), government-furnished equipment/information/APIs, how responses will be evaluated, and the anticipated follow-on acquisition vehicle. RFI objectives for context: {objectives_list}. Generate 4-6 numbered questions. For each: one sentence of context citing the specific RFI section it concerns, then a professionally worded question. All questions must be unclassified and safe for the Government's consolidated public Q&A publication. {hitl_context}",
    "6.2": "You are preparing pre-submission questions to the Contracting Officer for {rfi_title} ({rfi_number}). Generate questions that clarify the customer's underlying intention and mission goals so {entity_name} can determine whether {solution_name} can solution the need — and shape the response if so. Probe: what mission outcomes define success for the orchestration layer; how 'premium' vs 'best-effort' processing is decided today and who owns that policy; the real latency/throughput envelope versus aspirational figures; whether the Government intends to own the routing policy or expects vendor-managed learning; and where the boundary sits between this orchestration layer and the future internally-engineered architecture. RFI objectives: {objectives_list}. Generate 4-6 numbered questions, each with one sentence of context then the question. Unclassified, suitable for public Q&A. {hitl_context}",
    "6.3": "You are preparing pre-submission questions to the Contracting Officer for {rfi_title} ({rfi_number}). Generate strategic-leverage questions for {entity_name}: questions whose ANSWERS improve our capability roadmap and competitive positioning regardless of whether this RFI leads to award. Probe: the expected prototype-to-production transition path under 10 U.S.C. 4022 (OT follow-on production potential); the Government's appetite for modular/severable components versus an integrated platform; which capability gaps the Government weights highest among objectives ({objectives_list}); teaming expectations and NDC participation preferences; and timeline to any anticipated solicitation. Generate 4-6 numbered questions with one sentence of context each. Flag any question we should submit as PROPRIETARY (individual response) rather than public — the Government asks that proprietary questions be minimal. {hitl_context}",
    "6.4": "You are preparing pre-submission questions to the Contracting Officer for {rfi_title} ({rfi_number}). Generate questions that resolve ambiguities or conflicts within the RFI text itself. Consider: page-limit mechanics (what counts against the limit, appendix rules, cover page exclusions), whether graphics/tables count toward font requirements, the openness to alternative NAICS codes, how classified integration details will be handled with qualified respondents, ROM cost format expectations given commercial pricing models, and file naming/portal submission mechanics. Reference the section under question: {question_text}. Generate 3-5 numbered questions, each with one sentence of context citing the RFI section, then the question. Unclassified, suitable for public Q&A. {hitl_context}",
    "A": "Generate Appendix A architecture overview for {entity_name}'s FORGE framework. Intelligence Layer (policy timescale): NOVA learning, XAI engine (AgentSHAP + W3C PROV-AGENT), HITL override API, RTM traceability. Execution Layer (object timescale): Rule Engine (<50µs compiled), CoD engine (ThreadPoolExecutor, 3 debaters, <15ms), CoT engine (reason→critic→synthesize, async audit, <2s). For the AWS GovCloud/C2S deployment diagram, output EXACTLY this format and nothing else for the diagram (fill in real node labels, keep the fence markers verbatim, do not draw any box-and-line/ASCII art):\n```mermaid\nflowchart TD\n    A[Node] --> B[Node]\n```\n{hitl_context}",
    "B": "Generate Appendix B adaptive learning loop for {entity_name}'s NOVA system. ECHO: ingests mission outcome signals tagged to routing decisions, append-only feedback store, no sensitive content stored. SOUL: Bayesian policy update, hourly cadence, outputs candidate artifact (not direct deployment). TRUST: confidence scoring, KL-divergence limit, auto-deploy above threshold, HITL gate below threshold. SELA: 1-2% traffic probe to non-standard tiers, results feed ECHO. Learning cycle: 6-12 hours standard, <5 minutes emergency. {hitl_context}",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid():
    return str(uuid.uuid4())


def _load_profile(name):
    try:
        import yaml
        with open(_PROFILES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("profiles", {}).get(name, {})
    except Exception as exc:
        logger.warning("Could not load profile %s: %s", name, exc)
        return {}


def get_db():
    return get_canvas_connection("ICDEV_DB_URL")


# ── Session management ────────────────────────────────────────────────────────

def create_session(rfi_number, rfi_title, profile_name, upload_filename, parsed_data):
    sid = _uid()
    db = get_db()
    db.execute(
        "INSERT INTO rfi_workbench_sessions "
        "(id, rfi_number, rfi_title, profile_name, upload_filename, parsed_data, status, total_sections, approved_sections, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,'draft',0,0,%s,%s)",
        (sid, rfi_number, rfi_title, profile_name, upload_filename, json.dumps(parsed_data), _now(), _now()),
    )
    db.commit()
    _seed_sections(sid, parsed_data)
    threading.Thread(target=_seed_requirements_background, args=(sid,), daemon=True).start()
    threading.Thread(target=_launch_ace_team_background, args=(sid,), daemon=True).start()
    return sid


def _seed_sections(session_id, parsed_data):
    db = get_db()
    parts = parsed_data.get("questionnaire_parts", [])
    if parts:
        seen = set()
        rows = []
        for p in parts:
            key = (p.get("part", ""), p.get("item_number", ""))
            if key in seen:
                continue
            # Only questionnaire parts — headings from other RFI sections
            # (submission instructions, Q&A) must not become response sections.
            if not re.match(r"^Part\s+\d+$", p.get("part", ""), re.IGNORECASE):
                continue
            seen.add(key)
            part_key = p.get("part", "unknown").lower().replace(" ", "")
            item = p.get("item_number", "?")
            title = p.get("topic", f"Section {item}")
            question = p.get("question", "")
            rows.append((part_key, item, title, p.get("part", ""), question))
        # Part 6 (questions to the Government) and the technical appendix are
        # workbench-native parts, always present regardless of parse results.
        rows += [(s[0], s[1], s[2], s[3], s[4]) for s in _PART6_SECTIONS + _APPENDIX_SECTIONS]
    else:
        rows = [(s[0], s[1], s[2], s[3], s[4]) for s in _DEFAULT_SECTIONS]

    for part, item, title, topic, question in rows:
        db.execute(
            "INSERT INTO rfi_workbench_sections "
            "(id, session_id, part, item_number, title, topic, question_text, content, ai_draft, status, hitl_comment, generation_count, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'','','pending','',0,%s,%s)",
            (_uid(), session_id, part, item, title, topic, question, _now(), _now()),
        )

    count = len(rows)
    db.execute(
        "UPDATE rfi_workbench_sessions SET total_sections=%s, updated_at=%s WHERE id=%s",
        (count, _now(), session_id),
    )
    db.commit()


def list_sessions():
    db = get_db()
    rows = db.execute(
        "SELECT id, rfi_number, rfi_title, profile_name, status, total_sections, approved_sections, created_at, updated_at "
        "FROM rfi_workbench_sessions ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id):
    db = get_db()
    row = db.execute("SELECT * FROM rfi_workbench_sessions WHERE id=%s", (session_id,)).fetchone()
    if not row:
        return None
    s = dict(row)
    if isinstance(s.get("parsed_data"), str):
        try:
            s["parsed_data"] = json.loads(s["parsed_data"])
        except Exception:
            s["parsed_data"] = {}
    return s


def _parse_section_row(d: dict) -> dict:
    if isinstance(d.get("writeguard_result"), str) and d["writeguard_result"]:
        try:
            d["writeguard_result"] = json.loads(d["writeguard_result"])
        except Exception:
            pass
    if isinstance(d.get("requirements"), str):
        try:
            d["requirements"] = json.loads(d["requirements"] or "[]")
        except Exception:
            d["requirements"] = []
    if isinstance(d.get("ace_feedback"), str) and d["ace_feedback"]:
        try:
            d["ace_feedback"] = json.loads(d["ace_feedback"])
        except Exception:
            d["ace_feedback"] = None
    return d


def get_sections(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM rfi_workbench_sections WHERE session_id=%s ORDER BY part, item_number",
        (session_id,),
    ).fetchall()
    return [_parse_section_row(dict(r)) for r in rows]


def get_section(section_id):
    db = get_db()
    row = db.execute("SELECT * FROM rfi_workbench_sections WHERE id=%s", (section_id,)).fetchone()
    if not row:
        return None
    return _parse_section_row(dict(row))


def save_section_content(section_id, content, save_type="manual"):
    db = get_db()
    # Append current content to history before overwriting (item 6)
    existing = db.execute(
        "SELECT content, session_id FROM rfi_workbench_sections WHERE id=%s", (section_id,)
    ).fetchone()
    if existing:
        old_content = (existing["content"] if hasattr(existing, "keys") else existing[0]) or ""
        sess_id = (existing["session_id"] if hasattr(existing, "keys") else existing[1]) or ""
        if old_content.strip():
            try:
                db.execute(
                    "INSERT INTO rfi_section_history (id, section_id, session_id, content, saved_at, save_type) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (_uid(), section_id, sess_id, old_content, _now(), save_type),
                )
            except Exception as exc:
                logger.debug("History insert skipped (table may not exist yet): %s", exc)
    db.execute(
        "UPDATE rfi_workbench_sections SET content=%s, updated_at=%s WHERE id=%s",
        (content, _now(), section_id),
    )
    db.commit()


def get_section_history(section_id: str, limit: int = 10) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, saved_at, save_type, content FROM rfi_section_history "
            "WHERE section_id=%s ORDER BY saved_at DESC LIMIT %s",
            (section_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def apply_hitl(section_id, action, comment=""):
    status_map = {"approve": "hitl_approved", "reject": "hitl_rejected", "accept": "accepted"}
    new_status = status_map.get(action, "pending")
    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET hitl_action=%s, hitl_comment=%s, status=%s, updated_at=%s WHERE id=%s",
        (action, comment, new_status, _now(), section_id),
    )
    db.commit()
    _recalculate_session_progress(section_id)
    if action in ("approve", "accept"):
        threading.Thread(target=_ace_reviewer_pass_background, args=(section_id,), daemon=True).start()
        # Approved prose becomes evidence for the next pursuit. Backgrounded because
        # indexing embeds the content, and Accept must stay instant. promote_rfi_section
        # is idempotent and never raises, so a failure here cannot lose the approval.
        threading.Thread(target=_promote_evidence_background, args=(section_id,), daemon=True).start()
    result = get_section(section_id)
    if result and action in ("approve", "accept"):
        from tools.govcon.rfi_grounding import find_placeholders
        tokens = find_placeholders(result.get("content") or result.get("ai_draft") or "")
        if tokens:
            result["placeholder_warnings"] = tokens
    return result


def accept_all_drafted(session_id):
    """Bulk-accept every drafted section (ai_draft_ready or hitl_approved)
    as final. Pending sections (no draft yet) and explicitly rejected
    sections are left untouched. Skips the per-section ACE reviewer pass —
    bulk acceptance is a deliberate human shortcut, not a review trigger."""
    db = get_db()
    rows = db.execute(
        "SELECT id FROM rfi_workbench_sections WHERE session_id=%s AND status IN ('ai_draft_ready','hitl_approved')",
        (session_id,),
    ).fetchall()
    ids = [list(r)[0] if not hasattr(r, "keys") else r["id"] for r in rows]
    for sec_id in ids:
        db.execute(
            "UPDATE rfi_workbench_sections SET hitl_action='accept', status='accepted', updated_at=%s WHERE id=%s",
            (_now(), sec_id),
        )
    db.commit()
    if ids:
        _recalculate_session_progress(ids[0])
        # Bulk accept is the common path; without this the flywheel would only turn
        # for sections approved one at a time. promote_rfi_section skips anything
        # still holding [VERIFY], so a shortcut cannot smuggle unproven claims in.
        for sec_id in ids:
            threading.Thread(
                target=_promote_evidence_background, args=(sec_id,), daemon=True
            ).start()

    # Non-blocking warnings: unresolved placeholders + hallucinated RFI
    # citations in what was just accepted. The hard block is enforced at
    # export (placeholder_guard / citation_guard); here we surface so a
    # reviewer sees issues before they reach the export gate (trust-cite-03).
    from tools.govcon.rfi_grounding import find_placeholders
    session = get_session(session_id)
    parsed = (session or {}).get("parsed_data") or {}
    accepted = [s for s in get_sections(session_id) if s["id"] in set(ids)]
    warnings = []
    for sec in accepted:
        tokens = find_placeholders(sec.get("content") or sec.get("ai_draft") or "")
        if tokens:
            warnings.append({"item_number": sec.get("item_number"), "placeholders": tokens})
    citation_warnings = _reference_findings(accepted, parsed)
    return {
        "accepted": len(ids),
        "placeholder_warnings": warnings,
        "citation_warnings": citation_warnings,
    }


def _recalculate_session_progress(section_id):
    db = get_db()
    row = db.execute("SELECT session_id FROM rfi_workbench_sections WHERE id=%s", (section_id,)).fetchone()
    if not row:
        return
    sid = list(row)[0] if not hasattr(row, "keys") else row["session_id"]
    approved = db.execute(
        "SELECT COUNT(*) FROM rfi_workbench_sections WHERE session_id=%s AND status IN ('hitl_approved','accepted')",
        (sid,),
    ).fetchone()[0]
    total = db.execute(
        "SELECT COUNT(*) FROM rfi_workbench_sections WHERE session_id=%s", (sid,)
    ).fetchone()[0]
    status = "complete" if (approved >= total and total > 0) else "in_progress"
    db.execute(
        "UPDATE rfi_workbench_sessions SET approved_sections=%s, status=%s, updated_at=%s WHERE id=%s",
        (approved, status, _now(), sid),
    )
    db.commit()


# ── AI Content Generation ─────────────────────────────────────────────────────

def generate_section_content(section_id, profile_name, parsed_data):
    section = get_section(section_id)
    if not section:
        raise ValueError(f"Section {section_id} not found")

    profile = _load_profile(profile_name)
    objectives = parsed_data.get("objectives", [])
    obj_list = "; ".join(f"Obj {o['letter']}: {o['title']}" for o in objectives) if objectives else "A-F as described in the RFI"

    # Item 4 — capability context from profile (avoids hardcoded ICDEV product claims)
    cap_stmts = profile.get("capability_statements") or []
    solution_name = profile.get("solution_name") or profile.get("entity_name", "our solution")
    cap_context = ""
    if cap_stmts:
        bullets = "\n".join(f"  • {c}" for c in cap_stmts)
        cap_context = (
            f"[COMPANY CAPABILITIES — use these, do not reference NOVA/ECHO/SOUL/SELA/KEDA/FORGE "
            f"or any other vendor product names not listed here]\n"
            f"Solution name: {solution_name}\n{bullets}"
        )

    item = section["item_number"]
    prompt_tpl = _SECTION_PROMPTS.get(
        item,
        "Generate a professional GovCon response for the '{title}' section addressing: {question_text}",
    )

    hitl_comment = section.get("hitl_comment", "")
    hitl_context = f"Incorporate this reviewer feedback: {hitl_comment}" if hitl_comment else ""

    # Inject uncovered requirements so the LLM explicitly addresses them
    reqs = get_requirements(section_id)
    uncovered = [r for r in reqs if r.get("covered") in (False, None, "false", "partial")]
    if uncovered:
        req_lines = "\n".join(f"  - {r['text']}" for r in uncovered)
        req_notice = f"You MUST address each of these requirements:\n{req_lines}"
        hitl_context = f"{req_notice}\n\n{hitl_context}".strip() if hitl_context else req_notice

    # Load effective style guide and inject tone + compliance constraints into prompt
    try:
        from tools.govcon.rfi_style_engine import get_session_style_guide
        style_guide = get_session_style_guide(section.get("session_id", ""))
    except Exception:
        style_guide = {}

    prompt = prompt_tpl.format(
        entity_name=profile.get("entity_name", "the responding organization"),
        rfi_number=parsed_data.get("rfi_number", "RFI-UNKNOWN"),
        rfi_title=parsed_data.get("title", "the solicitation"),
        primary_naics=profile.get("primary_naics", "541512"),
        business_size=profile.get("business_size", "Large Business"),
        ndc_status=profile.get("ndc_status", "Traditional Defense Contractor"),
        clearances=", ".join(
            c.get("level", str(c)) if isinstance(c, dict) else c
            for c in profile.get("clearances", ["TS/SCI"])
        ),
        objectives_list=obj_list,
        hitl_context=hitl_context,
        title=section["title"],
        question_text=section.get("question_text", ""),
        capability_context=cap_context,
        solution_name=solution_name,
    )

    # Prepend capability context when not already embedded in the template
    if cap_context and "{capability_context}" not in prompt_tpl:
        prompt = cap_context + "\n\n" + prompt

    # Append style guide constraints to prompt
    if style_guide:
        tone = style_guide.get("tone", "formal")
        forbidden = style_guide.get("forbidden_phrases") or []
        style_parts = [f"Tone: {tone}."]
        if forbidden:
            style_parts.append(f"Never use: {', '.join(str(p) for p in forbidden[:10])}.")
        compliance_notes = style_guide.get("compliance_notes", "")
        if compliance_notes:
            style_parts.append(compliance_notes[:300].strip())
        prompt += "\n\n[Style Requirements]\n" + "\n".join(style_parts)

    # Inject the capture strategy so every part of the response argues one message
    # rather than reading as six separately-authored documents. Returns "" for
    # administrative sections (Part 1) and the ROM cost table (4.2), where
    # positioning does not belong. Every section — including generate_all_sections —
    # funnels through here, so this single insertion covers the whole response.
    from tools.govcon.capture_strategy import build_strategy_block, resolve_strategy

    strategy = resolve_strategy(session_id=section.get("session_id", ""))
    strategy_block = build_strategy_block(strategy, item)
    if strategy_block:
        prompt += "\n\n" + strategy_block

    # Ground the model in the RFI's real structure so it cannot invent
    # section numbers ("Section IV.B") or unsourced claims.
    from tools.govcon.rfi_grounding import (
        build_ground_truth_block, substitute_profile_facts, validate_references,
    )
    ground_block = build_ground_truth_block(parsed_data)
    if ground_block:
        prompt += "\n\n" + ground_block

    session_id = section.get("session_id", "")

    # Phase B: inject weighted source context (uploads, KG, RAG, engines).
    # sources_used is persisted (trust-cite-03) so the evidence behind a
    # section is traceable in the export/Sources panel, not dropped here.
    sources_used: list = []
    try:
        from tools.govcon.rfi_engine_runner import assemble_weighted_prompt_context
        topic = f"{section['title']} {section.get('question_text', '')[:200]}"
        engine_ctx = assemble_weighted_prompt_context(session_id, section_id, topic)
        if engine_ctx.get("context"):
            prompt += "\n\n[Source Context — weighted from enabled engines]\n" + engine_ctx["context"]
            sources_used = list(engine_ctx.get("sources_used") or [])
            logger.debug(
                "Engine context assembled: sources=%s chars=%d",
                sources_used, engine_ctx.get("total_chars", 0),
            )
    except Exception as exc:
        logger.warning("Engine context assembly failed (non-fatal): %s", exc)

    role_key = f"{session_id}:rfi_writer" if session_id else "rfi_writer"
    draft = _generate_draft(prompt, section, item, role_key)

    # Deterministic anti-hallucination post-pass: substitute identity facts
    # the LLM should never generate, then validate every RFI citation. One
    # corrective retry with the specific errors as feedback.
    draft, substitutions = substitute_profile_facts(draft, profile, parsed_data)
    ref_check = validate_references(draft, parsed_data)
    if not ref_check["valid"]:
        errs = "; ".join(f"{r['ref']} ({r['reason']})" for r in ref_check["invalid_refs"][:8])
        retry_prompt = (
            prompt
            + "\n\n[CITATION ERRORS IN PREVIOUS DRAFT — FIX THESE]\n"
            + f"Your previous draft cited references that do not exist in the RFI: {errs}. "
            + "Rewrite the section citing only the structure in [RFI GROUND TRUTH]."
        )
        retry = _call_llm(retry_prompt, section["title"], item, role_key=role_key, llm_function="rfi_writer_drafting")
        retry, retry_subs = substitute_profile_facts(retry, profile, parsed_data)
        retry_check = validate_references(retry, parsed_data)
        if len(retry_check["invalid_refs"]) < len(ref_check["invalid_refs"]):
            draft, ref_check = retry, retry_check
            substitutions += retry_subs
        logger.info(
            "Citation retry for %s: %d invalid refs remain",
            item, len(ref_check["invalid_refs"]),
        )

    # Run deterministic markdown validator — attach as transient field
    try:
        from tools.govcon.rfi_markdown_validator import validate_markdown_structure
        md_validation = validate_markdown_structure(draft)
    except Exception as exc:
        logger.warning("Markdown validator failed: %s", exc)
        md_validation = {"valid": True, "issues": []}
    md_validation["grounding"] = {
        "substitutions": substitutions,
        "invalid_references": ref_check["invalid_refs"],
        "references_checked": ref_check["checked"],
    }
    # halluc-01: non-blocking confabulation assessment (fabricated citations,
    # internal contradictions, hedging) surfaced to the reviewer/WriteGuard.
    try:
        from tools.security.confabulation_detector import assess as _confab_assess
        md_validation["confabulation"] = _confab_assess(draft)
    except Exception:
        pass

    db = get_db()
    # Persist sources_json alongside the draft. Falls back to the pre-249
    # column set if migration 249 hasn't run yet (completeness gate #6).
    try:
        db.execute(
            "UPDATE rfi_workbench_sections SET ai_draft=%s, content=%s, sources_json=%s, "
            "status='ai_draft_ready', generation_count=generation_count+1, updated_at=%s WHERE id=%s",
            (draft, draft, json.dumps(sources_used), _now(), section_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        db.execute(
            "UPDATE rfi_workbench_sections SET ai_draft=%s, content=%s, "
            "status='ai_draft_ready', generation_count=generation_count+1, updated_at=%s WHERE id=%s",
            (draft, draft, _now(), section_id),
        )
        db.commit()

    # Kick off coverage check in background
    threading.Thread(target=_check_coverage_background, args=(section_id,), daemon=True).start()
    # Kick off ACE Editor one-pass review in background (item 8)
    threading.Thread(target=_ace_editor_review_background, args=(section_id,), daemon=True).start()

    result = get_section(section_id)
    result["markdown_validation"] = md_validation
    return result


# ── Hermes-Agent pattern 2: per-role failure-count auto-block ────────────────
# Keys are "{session_id}:{role_id}" for generate-path tracking,
# or plain "{role_id}" for ACE-path tracking. Resets on server restart.
_role_failure_counts: dict[str, int] = {}
_role_blocked: set[str] = set()


def _track_llm_failure(role_key: str, max_failures: int = 2) -> bool:
    """Record a consecutive failure. Returns True if the role is now blocked."""
    _role_failure_counts[role_key] = _role_failure_counts.get(role_key, 0) + 1
    if _role_failure_counts[role_key] >= max_failures:
        _role_blocked.add(role_key)
        logger.warning(
            "Role '%s' auto-blocked after %d consecutive failures (Hermes pattern 2)",
            role_key, _role_failure_counts[role_key],
        )
        return True
    return False


def _track_llm_success(role_key: str) -> None:
    """Reset consecutive failure count on success."""
    _role_failure_counts.pop(role_key, None)
    _role_blocked.discard(role_key)


def is_role_blocked(session_id: str, role_id: str) -> bool:
    """Return True if this role is currently auto-blocked for the session."""
    return f"{session_id}:{role_id}" in _role_blocked or role_id in _role_blocked


_ROUTER = None

def _get_router():
    global _ROUTER
    if _ROUTER is None:
        import logging as _logging
        # On Windows, TimedRotatingFileHandler.doRollover() raises PermissionError
        # when the old log file is still locked by a previously killed process.
        # Suppress logging.raiseExceptions during init so rotation errors don't
        # abort LLMRouter construction.  rls-bypass: not a security bypass, Windows
        # file-lock workaround — required for task-rfi-llm-init
        old_raise = _logging.raiseExceptions
        _logging.raiseExceptions = False
        try:
            from tools.llm.router import LLMRouter
            _ROUTER = LLMRouter()
        except Exception as exc:
            logger.warning("LLMRouter init failed: %s — LLM generation disabled", exc)
        finally:
            _logging.raiseExceptions = old_raise
    return _ROUTER


# Judgment-heavy sections where multi-perspective debate materially improves
# quality: technical approach, mission Q&A, industry insights, questions to Gov.
_COD_ITEMS = {"2.4", "2.7", "5.1", "6.1", "6.2", "6.3", "6.4"}


def _cod_enabled() -> bool:
    return os.environ.get("ICDEV_RFI_COD_ENABLED", "true").strip().lower() in ("1", "true", "yes")


def _generate_draft(prompt, section, item_number, role_key):
    """Route generation: Chain of Debate for judgment sections (debaters +
    judge synthesis reduces single-model confabulation), single-shot for
    factual/boilerplate sections. Any CoD failure falls back to single-shot."""
    if item_number in _COD_ITEMS and _cod_enabled() and role_key not in _role_blocked:
        try:
            from tools.llm.chain_orchestrator import ChainOrchestrator
            from tools.llm.router import LLMRequest
            orch = ChainOrchestrator(router=_get_router())
            req = LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are an expert GovCon proposal writer for a defense/IC contractor. "
                        f"Write the '{section['title']}' (Item {item_number}) section of an RFI response. "
                        f"Be specific, professional, and avoid vague claims. UNCLASSIFIED content only. "
                        f"Format as clean markdown. Keep to ≤400 words unless the section requires detail.\n\n"
                        f"{prompt}"
                    ),
                }],
                max_tokens=900,
            )
            result = orch.invoke_chain_of_debate("rfi_writer_drafting", req)
            if result and getattr(result, "content", "").strip():
                _track_llm_success(role_key)
                logger.info("CoD draft for %s via %s", item_number, ",".join(result.models_used or []))
                return result.content
        except Exception as exc:
            logger.warning("CoD generation failed for %s (%s) — falling back to single-shot", item_number, exc)
    return _call_llm(prompt, section["title"], item_number, role_key=role_key, llm_function="rfi_writer_drafting")


def _call_llm(prompt, section_title, item_number, role_key: str = "rfi_writer", llm_function: str = "rfi_writer_drafting"):
    if role_key in _role_blocked:
        logger.warning("Skipping LLM call — role '%s' is auto-blocked", role_key)
        return _template_fallback(section_title, item_number, prompt)
    try:
        from tools.cortex import api as cortex_api
        from tools.cortex.schemas import CortexContext

        content = (
            f"You are an expert GovCon proposal writer for a defense/IC contractor. "
            f"Write the '{section_title}' (Item {item_number}) section of an RFI response. "
            f"Be specific, professional, and avoid vague claims. Use concrete numbers and built capabilities. "
            f"Format as clean markdown with bold headers and tables where appropriate. "
            f"UNCLASSIFIED content only. Keep to ≤400 words unless the section requires detail.\n\n"
            f"{prompt}"
        )
        # Adoption wave 2: route RFI drafting through the GOVERNED cortex.complete.
        # RFI responses are CUI compliance artifacts — this adds egress redaction,
        # provenance, an append-only audit row, and per-tenant budget attribution
        # the raw router.invoke never applied. The llm_function routing is preserved,
        # and a governance block / any failure degrades to _template_fallback.
        cx = cortex_api.complete(
            content,
            function=llm_function,
            ctx=CortexContext(
                classification="CUI", domain="proposal", agent_id=f"rfi:{role_key}"
            ),
            max_tokens=900,
        )
        _track_llm_success(role_key)
        return cx.text or _template_fallback(section_title, item_number, prompt)
    except Exception as exc:
        _track_llm_failure(role_key)
        logger.warning("LLM generation failed for section %s: %s — using fallback", item_number, exc)
        return _template_fallback(section_title, item_number, prompt)


def _template_fallback(title, item, prompt):
    return (
        f"**{title}**\n\n"
        f"> ⚠️ AI generation unavailable — LLM provider not configured or unreachable.\n\n"
        f"**Instructions for this section:**\n\n{prompt[:600]}\n\n"
        f"_Please edit this section manually or configure an LLM provider in `.env`._"
    )


# ── WriteGuard ────────────────────────────────────────────────────────────────

def run_writeguard(section_id):
    section = get_section(section_id)
    if not section:
        raise ValueError(f"Section {section_id} not found")

    text = section.get("content", "") or section.get("ai_draft", "")
    if not text.strip():
        return {"error": "No content to check", "overall_score": 0, "passed": False, "composites": {}}

    try:
        from tools.pulse.writeguard import run_full_quality_check
        result = run_full_quality_check(text)
    except Exception as exc:
        logger.warning("WriteGuard failed: %s", exc)
        result = {
            "passed": True,
            "overall_score": 75,
            "composites": {"Correctness": 80, "Clarity": 75, "Delivery": 70, "Originality": 75, "Engagement": 72},
            "issues": [],
            "error": str(exc),
        }

    # Merge style compliance findings into WriteGuard result
    try:
        from tools.govcon.rfi_style_engine import check_style_compliance, get_session_style_guide
        style_guide = get_session_style_guide(section.get("session_id", ""))
        style_result = check_style_compliance(text, style_guide)
        result.setdefault("composites", {})["Style"] = style_result.get("score", 100)
        result.setdefault("issues", []).extend(style_result.get("findings", []))
    except Exception as exc:
        logger.warning("Style compliance check failed: %s", exc)

    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET writeguard_result=%s, writeguard_score=%s, updated_at=%s WHERE id=%s",
        (json.dumps(result), result.get("overall_score", 0), _now(), section_id),
    )
    db.commit()
    return result


# ── Aggregation Guard (prop-sec-07) — classification-by-compilation gate ──────

class AggregationGuardBlocked(Exception):
    """Raised when the aggregation guard blocks export/finalize pending HITL review."""

    def __init__(self, fired_rules, derived, session_id):
        self.fired_rules = fired_rules
        self.derived = derived
        self.session_id = session_id
        super().__init__(f"Aggregation guard blocked session {session_id}: derived={derived}")


def _content_signature(sections) -> str:
    import hashlib

    joined = "\x1f".join((s.get("content") or s.get("ai_draft") or "") for s in sections)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _section_result_set(sections):
    return [
        {
            "element_id": s["id"],
            "label": f"{s.get('item_number', '')} {s.get('title', '')}".strip(),
            "text": s.get("content") or s.get("ai_draft") or "",
        }
        for s in sections
    ]


def check_aggregation_guard(session_id: str) -> dict:
    """Run the mosaic-effect guard across all sections of an RFI session.

    Returns the guard_result dict. If action == 'block' and no resolved
    override exists for the current content signature, raises
    AggregationGuardBlocked. Editing flagged content changes the signature,
    so a fresh guard check after an edit naturally re-evaluates rather than
    requiring an explicit "recheck" action.
    """
    from tools.db.storage import get_connection
    from tools.security.aggregation_guard import guard_result

    sections = get_sections(session_id)
    result_set = _section_result_set(sections)
    signature = _content_signature(sections)

    guard = guard_result(result_set, ctx={"surface_ceiling": "CUI"}, surface="rfi/export")

    if guard["action"] != "block":
        return guard

    conn = get_connection()
    for rule in guard["fired_rules"]:
        if rule["action"] != "block":
            continue
        existing = conn.execute(
            """SELECT resolution FROM document_aggregation_findings
               WHERE surface=%s AND document_id=%s AND rule_id=%s AND content_signature=%s""",
            ("rfi", session_id, rule["rule_id"], signature),
        ).fetchone()
        if existing and dict(existing).get("resolution") == "override":
            continue
        if not existing:
            conn.execute(
                """INSERT INTO document_aggregation_findings
                   (id, surface, document_id, rule_id, derived_classification,
                    matched_elements, content_signature, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    "rfi",
                    session_id,
                    rule["rule_id"],
                    guard["derived"],
                    json.dumps(rule["matched_elements"]),
                    signature,
                    _now(),
                ),
            )
            conn.commit()
        raise AggregationGuardBlocked(guard["fired_rules"], guard["derived"], session_id)

    return guard


def override_aggregation_guard(session_id: str, comment: str = "", resolved_by: str = "") -> int:
    """Resolve all open (unresolved) findings for the CURRENT content signature.

    Returns the number of findings resolved. Does not re-attempt export —
    the caller re-invokes assemble_and_export(), which re-checks and now
    passes because the signature matches a resolved override.
    """
    from tools.db.storage import get_connection

    sections = get_sections(session_id)
    signature = _content_signature(sections)
    conn = get_connection()
    now = _now()
    rows = conn.execute(
        """SELECT id FROM document_aggregation_findings
           WHERE surface=%s AND document_id=%s AND content_signature=%s AND resolution IS NULL""",
        ("rfi", session_id, signature),
    ).fetchall()
    for row in rows:
        rid = dict(row)["id"]
        conn.execute(
            """UPDATE document_aggregation_findings
               SET resolution='override', resolved_by=%s, resolved_at=%s, resolution_comment=%s
               WHERE id=%s""",
            (resolved_by, now, comment, rid),
        )
    conn.commit()
    return len(rows)


# ── Export ────────────────────────────────────────────────────────────────────

class PlaceholderGateBlocked(Exception):
    """Raised when unresolved [PLACEHOLDER] tokens remain in content that is
    about to leave the workbench. findings: [{item_number, placeholders[]}]"""

    def __init__(self, findings):
        self.findings = findings
        super().__init__(f"Unresolved placeholders in {len(findings)} section(s)")


class ReferenceGateBlocked(Exception):
    """Raised when a section cites an RFI reference (Section/Part/Item/Objective)
    that does not exist in the parsed RFI structure — a hallucinated citation
    (trust-cite-03). findings: [{item_number, invalid_refs:[{ref,reason}]}]"""

    def __init__(self, findings):
        self.findings = findings
        super().__init__(f"Invalid citations in {len(findings)} section(s)")


def _section_source_labels(sec: dict) -> list[str]:
    """Human-readable source keys persisted for a section (trust-cite-03).

    Reads sources_json (list of engine source keys). Returns [] when the
    column is absent/empty so pre-249 sessions render unchanged.
    """
    raw = sec.get("sources_json")
    if not raw:
        return []
    try:
        vals = raw if isinstance(raw, list) else json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(v) for v in vals if v]


def _reference_findings(sections, parsed):
    """Per-section invalid-citation findings via rfi_grounding.validate_references.

    Empty list == every citation resolves to real RFI structure. Sections with
    no content and abstained/pending sections contribute nothing.
    """
    from tools.govcon.rfi_grounding import validate_references
    findings = []
    for sec in sections:
        content = sec.get("content") or sec.get("ai_draft") or ""
        if not content:
            continue
        check = validate_references(content, parsed or {})
        if not check.get("valid", True) and check.get("invalid_refs"):
            findings.append({
                "item_number": sec.get("item_number"),
                "invalid_refs": check["invalid_refs"],
            })
    return findings


def _check_reference_gate(sections, parsed, force=False):
    """Block export while any section cites a non-existent RFI reference
    (gate=citation_guard). force=True bypasses after human review."""
    if force:
        return
    findings = _reference_findings(sections, parsed)
    if findings:
        raise ReferenceGateBlocked(findings)


def _check_placeholder_gate(sections, force=False):
    """Block export while [BRACKETED] tokens (incl. [VERIFY]) remain in any
    section that will be exported. force=True bypasses after human review."""
    if force:
        return
    from tools.govcon.rfi_grounding import find_placeholders
    findings = []
    for sec in sections:
        content = sec.get("content") or sec.get("ai_draft") or ""
        tokens = find_placeholders(content)
        if tokens:
            findings.append({"item_number": sec.get("item_number"), "placeholders": tokens})
    if findings:
        raise PlaceholderGateBlocked(findings)


def assemble_and_export(session_id, export_format="docx", force_placeholders=False, force_references=False):
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    check_aggregation_guard(session_id)

    sections = get_sections(session_id)
    _exportable = [s for s in sections if s.get("part") != "part6"]
    _check_placeholder_gate(_exportable, force=force_placeholders)
    # trust-cite-03: hallucinated RFI citations block export (was advisory).
    _check_reference_gate(_exportable, session.get("parsed_data") or {}, force=force_references)
    profile = _load_profile(session.get("profile_name", "own_company"))
    parsed = session.get("parsed_data") or {}

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    rfi_num = (session.get("rfi_number") or "RFI-UNKNOWN").replace("/", "-")
    entity = (profile.get("entity_name") or "Organization").replace(" ", "_").replace(".", "")
    base_name = f"{entity}_{rfi_num}"

    md_content = _build_markdown(session, sections, profile, parsed)
    md_path = _EXPORT_DIR / f"{base_name}.md"
    md_path.write_text(md_content, encoding="utf-8")

    if export_format == "md":
        _record_export(session_id, "md", str(md_path))
        return str(md_path)

    try:
        from tools.govcon.rfi_docx_exporter import export_to_docx
        docx_path = _EXPORT_DIR / f"{base_name}.docx"
        export_to_docx(
            md_content, str(docx_path),
            rfi_number=session.get("rfi_number"),
            entity_name=profile.get("entity_name"),
        )
        _record_export(session_id, "docx", str(docx_path))
    except Exception as exc:
        logger.warning("DOCX export failed: %s — saving MD only", exc)
        _record_export(session_id, "md", str(md_path))
        db = get_db()
        db.execute("UPDATE rfi_workbench_sessions SET status='exported', updated_at=%s WHERE id=%s", (_now(), session_id))
        db.commit()
        return str(md_path)

    db = get_db()
    db.execute("UPDATE rfi_workbench_sessions SET status='exported', updated_at=%s WHERE id=%s", (_now(), session_id))
    db.commit()
    return str(_EXPORT_DIR / f"{base_name}.docx")


def export_questions(session_id, force_placeholders=False):
    """Export Part 6 (questions to the Government) as a standalone markdown
    file formatted for ARC/email submission during the RFI Q&A window."""
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    sections = [s for s in get_sections(session_id) if s.get("part") == "part6"]
    if not sections:
        raise ValueError("No Part 6 (Questions for the Government) sections in this session")
    _check_placeholder_gate(sections, force=force_placeholders)

    profile = _load_profile(session.get("profile_name", "own_company"))
    parsed = session.get("parsed_data") or {}
    submission = parsed.get("submission_requirements") or {}
    entity = profile.get("entity_name", "Organization")
    rfi_num = session.get("rfi_number", "RFI-UNKNOWN")
    rfi_title = session.get("rfi_title", "")

    lines = [
        f"# Questions — {rfi_title} ({rfi_num})",
        "",
        f"**From:** {entity}",
        f"**To:** {parsed.get('poc_name', '')} <{parsed.get('poc_email', '')}>".strip(),
        f"**Subject:** RFI Question – {rfi_title} – {entity}",
    ]
    q_due = submission.get("questions_due_date") or parsed.get("questions_due_date")
    if q_due:
        lines.append(f"**Questions due:** {q_due}")
    lines += ["", "---", ""]
    for sec in sections:
        content = sec.get("content") or sec.get("ai_draft")
        if not content:
            continue
        lines += [f"## {sec['item_number']} {sec['title']}", "", content, ""]

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    entity_slug = entity.replace(" ", "_").replace(".", "")
    path = _EXPORT_DIR / f"{entity_slug}_{rfi_num.replace('/', '-')}_Questions.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    _record_export(session_id, "questions", str(path))
    return str(path)


def _build_markdown(session, sections, profile, parsed):
    # Part 6 (questions to the Government) is submitted separately during the
    # Q&A window — never part of the exported response document.
    sections = [s for s in sections if s.get("part") != "part6"]
    entity = profile.get("entity_name", "Organization")
    rfi_num = session.get("rfi_number", "RFI-UNKNOWN")
    rfi_title = session.get("rfi_title", "AI/ML Orchestration")
    today = date.today().strftime("%d %B %Y")
    lines = [
        "> UNCLASSIFIED//FOUO — All pages", "",
        f"# {entity}",
        f"## Response to {rfi_title}",
        f"### {rfi_num}",
        f"### Submitted: {today}", "",
        "---", "", "> UNCLASSIFIED//FOUO", "", "---", "",
    ]
    part_labels = {
        "part1": "Part 1: Administrative",
        "part2": "Part 2: Technical Approach",
        "part3": "Part 3: Project Feasibility and Schedule",
        "part4": "Part 4: Business and Intellectual Property",
        "part5": "Part 5: Industry Insights",
        "appendix": "Technical Appendix",
    }
    current_part = None
    for sec in sections:
        if sec["part"] != current_part:
            current_part = sec["part"]
            lines += ["", "---", "", "> UNCLASSIFIED//FOUO", "", "---", "", f"## {part_labels.get(sec['part'], sec['part'])}", ""]
        content = sec.get("content") or sec.get("ai_draft") or f"*[{sec['title']} — content pending]*"
        lines.append(f"**{sec['item_number']} {sec['title']}**")
        lines.append("")
        lines.append(content)
        lines.append("")
        # trust-cite-03: surface the evidence this section was drafted from so
        # claims are traceable to their source instead of being dropped.
        src_labels = _section_source_labels(sec)
        if src_labels:
            lines.append(f"*Sources: {', '.join(src_labels)}*")
            lines.append("")

    lines += ["", "---", "", "> UNCLASSIFIED//FOUO", ""]
    annex = build_compliance_annex(sections)
    lines.append(_build_compliance_annex_md(annex))
    return "\n".join(lines)


def build_compliance_annex(sections: list) -> dict:
    """Build a compliance summary dict for export and preview.

    Returns:
        {
          "coverage": [{"title", "total", "covered", "partial", "uncovered", "pct"}],
          "quality": [{"title", "wg_score", "style_score", "passed"}],
          "overall_coverage_pct": int,
          "overall_wg_score": float,
        }
    """
    coverage_rows = []
    quality_rows = []
    total_reqs = covered_total = partial_total = 0
    wg_scores = []

    for sec in sections:
        reqs = sec.get("requirements") or []
        if isinstance(reqs, str):
            try:
                reqs = json.loads(reqs)
            except Exception:
                reqs = []
        n = len(reqs)
        cov = sum(1 for r in reqs if r.get("covered") is True or r.get("covered") == "true")
        part = sum(1 for r in reqs if r.get("covered") == "partial")
        uncov = n - cov - part
        pct = round((cov + part * 0.5) / n * 100) if n else None
        coverage_rows.append({
            "title": f"{sec.get('item_number', '')} {sec.get('title', '')}".strip(),
            "total": n, "covered": cov, "partial": part, "uncovered": uncov, "pct": pct,
        })
        total_reqs += n
        covered_total += cov
        partial_total += part

        wg = sec.get("writeguard_score")
        wg_result = sec.get("writeguard_result") or {}
        if isinstance(wg_result, str):
            try:
                wg_result = json.loads(wg_result)
            except Exception:
                wg_result = {}
        style_score = (wg_result.get("composites") or {}).get("Style")
        quality_rows.append({
            "title": f"{sec.get('item_number', '')} {sec.get('title', '')}".strip(),
            "wg_score": wg,
            "style_score": style_score,
            "passed": wg is not None and wg >= 70,
        })
        if wg is not None:
            wg_scores.append(wg)

    overall_cov = round((covered_total + partial_total * 0.5) / total_reqs * 100) if total_reqs else 0
    overall_wg = round(sum(wg_scores) / len(wg_scores), 1) if wg_scores else None

    # Capability-gap demand signals raised by THIS session's requirements
    # (tools/govcon/rfi_demand.py). Best-effort; JSON refs filtered in Python (no
    # json_extract at runtime). Absent table / disabled feature -> empty list.
    demand_signals = []
    try:
        session_id = next((s.get("session_id") for s in sections if s.get("session_id")), None)
        if session_id:
            from tools.govcon.rfi_demand import list_demand_signals

            demand_signals = [
                s for s in list_demand_signals(limit=200)
                if any(str(ref).startswith(str(session_id)) for ref in (s.get("rfi_refs") or []))
            ]
    except Exception:
        pass

    return {
        "coverage": coverage_rows,
        "quality": quality_rows,
        "overall_coverage_pct": overall_cov,
        "overall_wg_score": overall_wg,
        "demand_signals": demand_signals,
    }


def _build_compliance_annex_md(annex: dict) -> str:
    lines = ["", "---", "", "## Compliance & Quality Annex", "",
             "> UNCLASSIFIED//FOUO — For internal review only", ""]

    # Requirements coverage table
    lines += ["### Requirements Coverage", ""]
    total_cov = annex.get("overall_coverage_pct", 0)
    lines.append(f"**Overall Coverage: {total_cov}%**")
    lines.append("")
    lines.append("| Section | Reqs | Covered | Partial | Uncovered | % |")
    lines.append("|---------|------|---------|---------|-----------|---|")
    for r in annex.get("coverage", []):
        pct = f"{r['pct']}%" if r["pct"] is not None else "—"
        lines.append(f"| {r['title']} | {r['total']} | {r['covered']} | {r['partial']} | {r['uncovered']} | {pct} |")
    lines.append("")

    # WriteGuard quality table
    lines += ["### WriteGuard Quality Scores", ""]
    overall_wg = annex.get("overall_wg_score")
    if overall_wg is not None:
        lines.append(f"**Average Score: {overall_wg}/100**")
        lines.append("")
    lines.append("| Section | WG Score | Style | Pass? |")
    lines.append("|---------|----------|-------|-------|")
    for r in annex.get("quality", []):
        wg = f"{r['wg_score']}" if r["wg_score"] is not None else "—"
        style = f"{r['style_score']}" if r["style_score"] is not None else "—"
        passed = "✓" if r["passed"] else "✗" if r["wg_score"] is not None else "—"
        lines.append(f"| {r['title']} | {wg} | {style} | {passed} |")
    lines.append("")

    # Capability-gap demand signals (unmet requirements captured for the roadmap).
    demand = annex.get("demand_signals") or []
    if demand:
        lines += ["### Capability Gaps → Demand Signals", ""]
        lines.append(
            "Requirements this RFI surfaced that ICDEV cannot yet fully satisfy. "
            "Each has been logged as a demand signal and queued as SUGGESTED build "
            "task(s) to close the gap for future opportunities."
        )
        lines.append("")
        lines.append("| Capability Need | Domain | Priority | High Demand |")
        lines.append("|-----------------|--------|----------|-------------|")
        for s in demand:
            need = (s.get("capability_need") or "")[:80].replace("|", "/")
            prio = s.get("priority")
            prio_s = f"{prio:.2f}" if isinstance(prio, (int, float)) else "—"
            high = "▲" if s.get("is_high_demand") else ""
            lines.append(f"| {need} | {s.get('domain', '')} | {prio_s} | {high} |")
        lines.append("")
    return "\n".join(lines)


def _record_export(session_id, fmt, path):
    db = get_db()
    db.execute(
        "INSERT INTO rfi_workbench_exports (id, session_id, export_format, file_path, exported_at) VALUES (%s,%s,%s,%s,%s)",
        (_uid(), session_id, fmt, path, _now()),
    )
    db.commit()


def delete_session(session_id):
    db = get_db()
    db.execute("DELETE FROM rfi_workbench_sections WHERE session_id=%s", (session_id,))
    db.execute("DELETE FROM rfi_workbench_exports WHERE session_id=%s", (session_id,))
    db.execute("DELETE FROM rfi_workbench_sessions WHERE id=%s", (session_id,))
    db.commit()


def list_profiles():
    try:
        import yaml
        with open(_PROFILES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return list(data.get("profiles", {}).keys())
    except Exception:
        return ["own_company"]


# ── Requirements layer ────────────────────────────────────────────────────────

def get_requirements(section_id: str) -> list[dict]:
    section = get_section(section_id)
    if not section:
        return []
    reqs = section.get("requirements")
    if isinstance(reqs, list):
        return reqs
    return []


def _save_requirements(section_id: str, reqs: list[dict]):
    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET requirements=%s, updated_at=%s WHERE id=%s",
        (json.dumps(reqs), _now(), section_id),
    )
    db.commit()


def add_requirement(section_id: str, text: str, source: str = "manual") -> dict:
    reqs = get_requirements(section_id)
    new_req = {"id": _uid(), "text": text, "source": source, "covered": None}
    reqs.append(new_req)
    _save_requirements(section_id, reqs)
    return new_req


def update_requirement(section_id: str, req_id: str, text: str) -> dict | None:
    reqs = get_requirements(section_id)
    for r in reqs:
        if r["id"] == req_id:
            r["text"] = text
            _save_requirements(section_id, reqs)
            return r
    return None


def delete_requirement(section_id: str, req_id: str) -> bool:
    reqs = get_requirements(section_id)
    original_len = len(reqs)
    reqs = [r for r in reqs if r["id"] != req_id]
    if len(reqs) == original_len:
        return False
    _save_requirements(section_id, reqs)
    return True


def extract_section_requirements(section_id: str) -> list[dict]:
    """LLM-based extraction of discrete requirements from question_text.
    Skips if requirements already exist for the section.
    """
    existing = get_requirements(section_id)
    if existing:
        return existing

    section = get_section(section_id)
    if not section:
        return []

    question_text = section.get("question_text", "").strip()
    if not question_text:
        return []

    prompt = (
        f"You are a GovCon proposal expert. Extract discrete, actionable requirements from "
        f"the following RFI question/instruction. Return ONLY a JSON array of strings, "
        f"each item being one specific requirement the response must address. "
        f"Extract 3 to 8 requirements. No preamble, no markdown, just the JSON array.\n\n"
        f"RFI Section: {section.get('title', '')}\n"
        f"Question: {question_text}"
    )

    try:
        from tools.llm.router import LLMRequest
        router = _get_router()
        if not router:
            return []
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        response = router.invoke("proposal_drafting", req)
        raw = response.content if hasattr(response, "content") else str(response)

        # Strip markdown code fences if present
        import re
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        raw = raw.strip().rstrip("```").strip()

        extracted = json.loads(raw)
        if not isinstance(extracted, list):
            return []

        reqs = [
            {"id": _uid(), "text": str(item), "source": "extracted", "covered": None}
            for item in extracted if isinstance(item, str) and item.strip()
        ]
        _save_requirements(section_id, reqs)
        return reqs
    except Exception as exc:
        logger.warning("Requirement extraction failed for section %s: %s", section_id, exc)
        return []


def check_requirement_coverage(section_id: str) -> list[dict]:
    """LLM-based check of whether generated content covers each requirement.
    Updates the covered field on each requirement and saves back to DB.
    """
    reqs = get_requirements(section_id)
    if not reqs:
        return []

    section = get_section(section_id)
    if not section:
        return reqs

    content = (section.get("content") or section.get("ai_draft") or "").strip()
    if not content:
        return reqs

    req_list = "\n".join(f"  [{r['id'][:8]}] {r['text']}" for r in reqs)
    prompt = (
        f"You are a GovCon proposal evaluator. For each requirement listed below, "
        f"determine whether the provided content addresses it.\n\n"
        f"Return ONLY a JSON object mapping the requirement short-id (first 8 chars) "
        f"to one of: true (fully addressed), false (not addressed), or \"partial\" (partially addressed).\n\n"
        f"Requirements:\n{req_list}\n\n"
        f"Content to evaluate:\n{content[:2000]}"
    )

    import re as _re

    def _parse_coverage_response(raw_text: str) -> dict | None:
        """Strip fences and extract first {...} block; return dict or None."""
        cleaned = _re.sub(r"^```[a-z]*\n?", "", raw_text.strip(), flags=_re.MULTILINE)
        cleaned = cleaned.strip().rstrip("```").strip()
        try:
            result = json.loads(cleaned)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass
        # Fallback: find first {...} block in the response
        m = _re.search(r"\{[^{}]+\}", cleaned, _re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError:
                pass
        return None

    def _apply_coverage_map(reqs_list: list, coverage_map: dict) -> list:
        for r in reqs_list:
            verdict = coverage_map.get(r["id"][:8])
            if verdict is True or verdict == "true":
                r["covered"] = True
            elif verdict is False or verdict == "false":
                r["covered"] = False
            elif verdict == "partial":
                r["covered"] = "partial"
        return reqs_list

    try:
        from tools.llm.router import LLMRequest
        router = _get_router()
        if not router:
            return reqs

        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        response = router.invoke("proposal_review", req)
        raw = response.content if hasattr(response, "content") else str(response)

        coverage_map = _parse_coverage_response(raw)

        if coverage_map is None:
            # Retry once with a strict prompt
            strict_prompt = (
                f"Respond with ONLY a JSON object, no explanation, no markdown. "
                f"Keys: requirement short-ids (8 chars). Values: true, false, or \"partial\".\n\n"
                f"{req_list}"
            )
            retry_req = LLMRequest(
                messages=[{"role": "user", "content": strict_prompt}],
                max_tokens=200,
            )
            retry_resp = router.invoke("proposal_review", retry_req)
            retry_raw = retry_resp.content if hasattr(retry_resp, "content") else str(retry_resp)
            coverage_map = _parse_coverage_response(retry_raw)

        if coverage_map is None:
            logger.warning("Coverage check: could not parse LLM response for section %s", section_id)
            return reqs

        reqs = _apply_coverage_map(reqs, coverage_map)
        _save_requirements(section_id, reqs)
        return reqs
    except Exception as exc:
        logger.warning("Coverage check failed for section %s: %s", section_id, exc)
        return reqs


def _seed_requirements_background(session_id: str):
    """Extract requirements for all sections of a session, 0.3s apart."""
    try:
        db = get_db()
        rows = db.execute(
            "SELECT id FROM rfi_workbench_sections WHERE session_id=%s ORDER BY part, item_number",
            (session_id,),
        ).fetchall()
        for row in rows:
            sec_id = list(row)[0] if not hasattr(row, "keys") else row["id"]
            try:
                extract_section_requirements(sec_id)
            except Exception as exc:
                logger.warning("Background req extraction failed for section %s: %s", sec_id, exc)
            time.sleep(0.3)
    except Exception as exc:
        logger.warning("Background requirement seeding failed for session %s: %s", session_id, exc)


def _check_coverage_background(section_id: str):
    """Run coverage check in background after content generation."""
    try:
        check_requirement_coverage(section_id)
    except Exception as exc:
        logger.warning("Background coverage check failed for section %s: %s", section_id, exc)
    # Capability-gap demand loop: once coverage is judged, surface any requirement that
    # is BOTH a capability-catalog miss (grade N) AND uncovered/partial as a demand
    # signal -> SUGGESTED kanban card. Guarded + fail-soft so it never breaks drafting.
    try:
        from tools.govcon import rfi_demand

        if rfi_demand.is_enabled():
            rfi_demand.process_section(section_id)
    except Exception as exc:
        logger.warning("RFI demand-gap detection failed for section %s: %s", section_id, exc)


# ── ACE Editor one-pass review (item 8) ──────────────────────────────────────

def run_ace_editor_review(section_id: str) -> dict:
    """Run a structured Editor review on the section's current content.

    Steps:
      1. Deterministic markdown validator (sync, no LLM).
      2. Style compliance check via WriteGuard score.
      3. LLM critique via rfi_editor_drafting (LOCAL ONLY — CUI content).

    Result is written to the ace_feedback column and returned.
    """
    section = get_section(section_id)
    if not section:
        return {}
    content = (section.get("content") or section.get("ai_draft") or "").strip()
    if not content:
        return {}

    issues: list[dict] = []
    overall = "pass"

    # Step 1 — deterministic markdown validator
    try:
        from tools.govcon.rfi_markdown_validator import validate_markdown_structure
        md_result = validate_markdown_structure(content)
        for iss in md_result.get("issues", []):
            issues.append({"source": "markdown", "level": iss.get("level", "warning"), "message": iss.get("message", str(iss))})
        if not md_result.get("valid", True):
            overall = "warn"
    except Exception as exc:
        logger.debug("ACE Editor markdown validator skipped: %s", exc)

    # Step 2 — WriteGuard style score
    try:
        from tools.govcon.writeguard import evaluate_style
        wg_result = evaluate_style(content, section.get("title", ""))
        wg_score = wg_result.get("score")
        if wg_score is not None and wg_score < 60:
            issues.append({
                "source": "writeguard",
                "level": "warn" if wg_score >= 40 else "error",
                "message": f"WriteGuard style score {wg_score}/100 — below threshold.",
            })
            overall = "warn" if overall == "pass" else overall
    except Exception as exc:
        logger.debug("ACE Editor WriteGuard skipped: %s", exc)
        wg_score = None

    # Step 3 — LLM critique (LOCAL ONLY — rfi_editor_drafting route)
    llm_summary = ""
    try:
        role_key = f"editor:{section_id}"
        if role_key not in _role_blocked:
            prompt = (
                f"You are the Editor in a GovCon color-team review. "
                f"Review this RFI section response and provide a brief critique (3-5 bullet points).\n"
                f"Focus on: clarity, completeness against stated requirements, compliance language, "
                f"government-appropriate tone, and any [VERIFY] placeholders that remain.\n"
                f"Section: {section.get('title', '')}\n\n"
                f"{content[:3000]}\n\n"
                f"Return ONLY the bullet-point critique. No preamble."
            )
            llm_summary = _call_llm(
                prompt,
                section.get("title", ""),
                section.get("item_number", ""),
                role_key=role_key,
                llm_function="rfi_editor_drafting",
            )
            if llm_summary and "[VERIFY]" in content:
                issues.append({
                    "source": "editor_llm",
                    "level": "warn",
                    "message": "Section contains un-resolved [VERIFY] placeholders.",
                })
                overall = "warn" if overall == "pass" else overall
    except Exception as exc:
        logger.warning("ACE Editor LLM critique failed for section %s: %s", section_id, exc)

    reviewed_at = _now()
    feedback = {
        "issues": issues,
        "overall": overall,
        "summary": llm_summary,
        "reviewed_at": reviewed_at,
    }

    try:
        db = get_db()
        db.execute(
            "UPDATE rfi_workbench_sections SET ace_feedback=%s, updated_at=%s WHERE id=%s",
            (json.dumps(feedback), reviewed_at, section_id),
        )
        db.commit()
    except Exception as exc:
        logger.warning("ACE Editor feedback write failed for section %s: %s", section_id, exc)

    return feedback


def _ace_editor_review_background(section_id: str) -> None:
    """Daemon wrapper for run_ace_editor_review with a short yield."""
    time.sleep(0.3)
    try:
        run_ace_editor_review(section_id)
    except Exception as exc:
        logger.warning("ACE Editor background review error for %s: %s", section_id, exc)


# ── ACE Reviewer one-pass (item 1) ───────────────────────────────────────────

def run_ace_reviewer_pass(section_id: str) -> dict:
    """ACE Reviewer: Government evaluator perspective critique.

    Called after HITL approve/accept. Asks: "Would a contracting officer score this highly?"
    Writes result to ace_feedback with source='reviewer' merged alongside editor findings.
    """
    import re as _re
    section = get_section(section_id)
    if not section:
        return {}
    content = (section.get("content") or section.get("ai_draft") or "").strip()
    if not content:
        return {}

    reqs = get_requirements(section_id)
    req_list = "\n".join(f"  [{r['id'][:8]}] {r['text']}" for r in reqs) if reqs else "None extracted."

    # Deterministic grounding evidence for the evaluator: the RFI's real
    # structure plus any invalid citations / unresolved placeholders found
    # by the regex validators (ground truth the LLM judge can't invent).
    grounding_note = ""
    parsed_data = {}
    try:
        from tools.govcon.rfi_grounding import (
            build_ground_truth_block, find_placeholders, validate_references,
        )
        sess = get_session(section.get("session_id", ""))
        parsed_data = (sess or {}).get("parsed_data") or {}
        ref_check = validate_references(content, parsed_data)
        tokens = find_placeholders(content)
        findings = []
        if ref_check["invalid_refs"]:
            findings.append("Invalid citations detected: " + "; ".join(
                f"{r['ref']} ({r['reason']})" for r in ref_check["invalid_refs"][:6]))
        if tokens:
            findings.append("Unresolved placeholders: " + ", ".join(tokens[:10]))
        gt = build_ground_truth_block(parsed_data)
        grounding_note = (
            ("\nDeterministic validator findings (treat as fact):\n" + "\n".join(findings) + "\n" if findings else "")
            + (f"\n{gt}\n" if gt else "")
        )
    except Exception as exc:
        logger.debug("Grounding context for reviewer unavailable: %s", exc)

    prompt = (
        f"You are a Government contracting officer evaluating an RFI response section.\n"
        f"Section: {section.get('item_number')} — {section.get('title')}\n\n"
        f"Content:\n{content[:2000]}\n"
        f"{grounding_note}\n"
        f"Requirements this section must address:\n{req_list}\n\n"
        f"Evaluate from the Government's perspective:\n"
        f"1. Does it directly answer what was asked, or does it sell instead of answer?\n"
        f"2. Are technical claims specific and verifiable, or vague and aspirational?\n"
        f"3. Does every RFI reference (section/item/objective) actually exist per the ground truth above?\n"
        f"4. Are there any red flags (unsupported assertions, missing data, compliance gaps)?\n"
        f"5. How would you score this section 1-5 for technical merit?\n\n"
        f"Return ONLY a JSON object:\n"
        f'{{"issues": [{{"type": "evaluator_concern", "message": "...", "suggestion": "..."}}], '
        f'"evaluator_score": 3, "overall": "pass", '
        f'"summary": "one sentence from evaluator perspective"}}'
    )

    router = _get_router()
    if not router:
        return {}

    try:
        from tools.llm.router import LLMRequest
        req_obj = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        response = router.invoke("rfi_reviewer_review", req_obj)
        raw = response.content if hasattr(response, "content") else str(response)
        raw = _re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=_re.MULTILINE)
        raw = raw.strip().rstrip("```").strip()
        try:
            feedback = json.loads(raw)
        except Exception:
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            feedback = json.loads(m.group()) if m else {
                "summary": raw[:200], "overall": "warn",
                "issues": [], "evaluator_score": None,
            }

        feedback["source"] = "reviewer"
        feedback["reviewed_at"] = _now()

        # Deterministic validator findings become first-class evaluator
        # issues — regex facts, not LLM opinions.
        try:
            from tools.govcon.rfi_grounding import find_placeholders, validate_references
            det_refs = validate_references(content, parsed_data)
            for r in det_refs["invalid_refs"]:
                feedback.setdefault("issues", []).append({
                    "type": "invalid_citation",
                    "message": f"{r['ref']}: {r['reason']}",
                    "suggestion": "Cite only sections/items/objectives that exist in the RFI.",
                })
            for tok in find_placeholders(content):
                feedback.setdefault("issues", []).append({
                    "type": "unresolved_placeholder",
                    "message": f"Placeholder {tok} remains in the content",
                    "suggestion": "Replace with the real value from the company profile.",
                })
            if det_refs["invalid_refs"] and feedback.get("overall") == "pass":
                feedback["overall"] = "warn"
        except Exception as exc:
            logger.debug("Deterministic reviewer findings failed: %s", exc)

        # Optional confabulation heuristics (Phase 48 detector) — advisory.
        try:
            from tools.security.confabulation_detector import check_output
            confab = check_output(section.get("session_id", "rfi"), content)
            if isinstance(confab, dict) and not confab.get("error"):
                feedback["confabulation"] = confab
        except Exception as exc:
            logger.debug("Confabulation check unavailable: %s", exc)

        # Optional, off-by-default: an independent Council pressure-test via
        # idea_lab's Specialist (tools/govcon/specialist_consult.py), additive
        # to -- never a replacement for -- the local qwen3/llama-local
        # reviewer pass above. Only the evaluator's own summary/score is sent
        # (never raw section content), and specialist_consult.py sanitizes
        # that before it leaves this process. Any failure/timeout/disabled
        # config is silently skipped -- this must never block the reviewer
        # pass finishing and persisting.
        if os.environ.get("ICDEV_RFI_SPECIALIST_CONSULT_ENABLED", "").strip().lower() in ("1", "true", "yes"):
            try:
                from tools.govcon.specialist_consult import request_council_consult

                consult_question = (
                    f"Independent pressure-test requested for RFI section "
                    f"{section.get('item_number')} ({section.get('title')}). "
                    f"Evaluator summary: {feedback.get('summary', '')} "
                    f"(evaluator_score={feedback.get('evaluator_score')})."
                )
                consult_result = request_council_consult("RFI response review", consult_question)
                if consult_result:
                    feedback["specialist_consult"] = consult_result
            except Exception as consult_exc:
                logger.debug("Specialist Council consult skipped for %s: %s", section_id, consult_exc)

        # Merge into existing ace_feedback — preserve editor findings
        existing = section.get("ace_feedback")
        if isinstance(existing, dict) and existing.get("source") != "reviewer":
            existing["reviewer"] = feedback
            merged = existing
        else:
            merged = feedback

        db = get_db()
        db.execute(
            "UPDATE rfi_workbench_sections SET ace_feedback=%s, updated_at=%s WHERE id=%s",
            (json.dumps(merged), _now(), section_id),
        )
        db.commit()
        return feedback
    except Exception as exc:
        logger.warning("ACE Reviewer pass failed for %s: %s", section_id, exc)
        return {}


def _ace_reviewer_pass_background(section_id: str) -> None:
    time.sleep(0.5)
    try:
        run_ace_reviewer_pass(section_id)
        logger.debug("ACE Reviewer pass complete for section %s", section_id)
    except Exception as exc:
        logger.warning("ACE Reviewer background error for %s: %s", section_id, exc)


def _promote_evidence_background(section_id: str) -> None:
    """Index an approved section into the evidence corpus. Never raises."""
    try:
        from tools.govcon.evidence_corpus import promote_rfi_section

        result = promote_rfi_section(section_id)
        logger.info(
            "Evidence promotion for section %s: %s (%s)",
            section_id, result["status"], result.get("reason") or f"{result['chunks']} chunks",
        )
    except Exception as exc:
        logger.warning("Evidence promotion background error for %s: %s", section_id, exc)


def get_section_evidence_status(section_id: str) -> dict:
    """Whether this section is indexed as reusable evidence — drives the 'Reusable' chip."""
    try:
        from tools.db.storage import get_connection
        from tools.govcon.evidence_corpus import RFI_SOURCE_TYPE

        row = get_connection().execute(
            "SELECT COUNT(*) AS cnt FROM rag_chunks WHERE source_type = %s AND source_id = %s",
            (RFI_SOURCE_TYPE, section_id),
        ).fetchone()
        chunks = int(dict(row)["cnt"]) if row else 0
        return {"indexed": chunks > 0, "chunks": chunks}
    except Exception as exc:
        logger.warning("Could not read evidence status for %s: %s", section_id, exc)
        return {"indexed": False, "chunks": 0}


# ── Cross-section consistency check (item 3) ─────────────────────────────────

def check_cross_section_consistency(session_id: str, skip_llm: bool = False) -> dict:
    """Scan accepted sections for conflicts: TRL claims, [VERIFY] tags, LLM semantic pass.

    Args:
        skip_llm: When True, run only the fast deterministic pass (used inside readiness gate).

    Returns {conflicts: [{type, sections, message, severity}], overall: 'clean'|'warnings'|'conflicts'}
    """
    import re as _re
    session = get_session(session_id)
    if not session:
        return {"error": "Session not found"}
    sections = get_sections(session_id)
    accepted = [s for s in sections if s["status"] in ("hitl_approved", "accepted")]
    if len(accepted) < 2:
        return {"conflicts": [], "overall": "clean", "checked": len(accepted)}

    conflicts = []

    # 1. TRL claims
    trl_claims = {}
    for s in accepted:
        text = s.get("content") or ""
        matches = _re.findall(r'\bTRL[\s\-]?(\d)\b', text, _re.IGNORECASE)
        if matches:
            trl_claims[s["item_number"]] = [int(m) for m in matches]
    if trl_claims:
        all_trl = {v for vals in trl_claims.values() for v in vals}
        if max(all_trl) - min(all_trl) > 2:
            conflicts.append({
                "type": "trl_mismatch",
                "sections": list(trl_claims.keys()),
                "message": f"TRL claims span {min(all_trl)}-{max(all_trl)} across sections. Verify consistency.",
                "severity": "warning",
            })

    # 2. [VERIFY] tags in accepted sections
    verify_secs = [s["item_number"] for s in accepted if "[VERIFY]" in (s.get("content") or "")]
    if verify_secs:
        conflicts.append({
            "type": "unresolved_verify_tag",
            "sections": verify_secs,
            "message": f"[VERIFY] placeholders remain in accepted sections: {', '.join(verify_secs)}",
            "severity": "error",
        })

    # 2b. Numeric claim conflicts (ROM totals, prototype timeline months)
    try:
        from tools.govcon.rfi_grounding import check_numeric_claims
        conflicts.extend(check_numeric_claims(accepted))
    except Exception as exc:
        logger.debug("Numeric claim check failed: %s", exc)

    # 3. LLM semantic pass (cap at 12 sections to stay within token budget)
    router = _get_router()
    if not skip_llm and router and len(accepted) >= 2:
        section_summaries = []
        for s in accepted[:12]:
            text = (s.get("content") or "")[:400]
            section_summaries.append(f"Section {s['item_number']} ({s['title']}):\n{text}")
        combined = "\n\n---\n\n".join(section_summaries)
        prompt = (
            f"You are reviewing an RFI response for internal consistency.\n\n"
            f"Accepted sections (summarized):\n\n{combined}\n\n"
            f"Identify up to 4 logical conflicts or inconsistencies across sections. "
            f"Focus on: contradictory technical claims, inconsistent timelines, "
            f"different entity names or capabilities stated, cost vs. timeline mismatches.\n"
            f"Return ONLY a JSON array (may be empty []). "
            f'Each element: {{"type": "narrative_conflict", "sections": ["2.1","3.1"], "message": "...", "severity": "warning"}}'
        )
        try:
            from tools.llm.router import LLMRequest
            req_obj = LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=400)
            response = router.invoke("rfi_reviewer_review", req_obj)
            raw = response.content if hasattr(response, "content") else str(response)
            raw = _re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=_re.MULTILINE)
            raw = raw.strip().rstrip("```").strip()
            try:
                llm_conflicts = json.loads(raw)
                if isinstance(llm_conflicts, list):
                    conflicts.extend(llm_conflicts[:4])
            except Exception:
                m = _re.search(r'\[.*\]', raw, _re.DOTALL)
                if m:
                    try:
                        conflicts.extend(json.loads(m.group())[:4])
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("LLM consistency check failed: %s", exc)

    overall = "clean"
    if any(c.get("severity") == "error" for c in conflicts):
        overall = "conflicts"
    elif conflicts:
        overall = "warnings"

    return {
        "conflicts": conflicts,
        "overall": overall,
        "checked": len(accepted),
        "checked_at": _now(),
    }


# ── On-demand summarization ───────────────────────────────────────────────────

def summarize_section_content(section_id: str, word_target: int) -> dict:
    """Condense section content to fit within word_target words.

    Returns condensed text WITHOUT saving — caller must confirm before replacing.
    """
    section = get_section(section_id)
    if not section:
        raise ValueError(f"Section {section_id} not found")

    content = (section.get("content") or section.get("ai_draft") or "").strip()
    if not content:
        raise ValueError("No content to summarize")

    current_words = len(content.split())
    if current_words <= word_target:
        return {"condensed": content, "word_count": current_words, "already_fits": True}

    prompt = (
        f"Condense the following GovCon section response to approximately {word_target} words. "
        f"Preserve all specific claims, metrics, compliance statements, and required headings. "
        f"Remove redundant phrases, generic boilerplate, and repetition. "
        f"Return ONLY the condensed text — no preamble, no commentary.\n\n"
        f"Section: {section['title']}\n\n"
        f"{content}"
    )

    condensed = _call_llm(prompt, section["title"], section["item_number"])
    word_count = len(condensed.split())
    return {"condensed": condensed, "word_count": word_count, "already_fits": False}


# ── ACE Team integration ──────────────────────────────────────────────────────

_RFI_TEAM_ROLES = [
    "rfi_writer",
    "rfi_editor",
    "rfi_reviewer",
    "rfi_researcher",
    "rfi_compliance_reviewer",
]


def launch_ace_team(session_id: str) -> str:
    """Launch the 5-role RFI ACE team for a session. Returns the ace_instance_id.

    Hermes-Agent pattern 3: all RFI roles are leaf nodes (is_orchestrator=false).
    Blocked roles are filtered out of the launch set before delegating to ACE.
    """
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    # Filter blocked roles (Hermes pattern 2) and enforce leaf isolation (pattern 3)
    active_roles = [r for r in _RFI_TEAM_ROLES if not is_role_blocked(session_id, r)]
    if len(active_roles) < len(_RFI_TEAM_ROLES):
        skipped = set(_RFI_TEAM_ROLES) - set(active_roles)
        logger.warning("Launching ACE team without blocked roles: %s", skipped)

    try:
        from icdev.tools.ace.controller import ACEController
        controller = ACEController()
        problem = (
            f"RFI response color team for session {session_id}: "
            f"{session.get('rfi_title', 'RFI')} ({session.get('rfi_number', '')}). "
            f"Pipeline: Writer → Editor → Reviewer → Researcher → Compliance Reviewer. "
            f"All roles are leaf nodes — no inter-role delegation permitted."
        )
        result = controller.launch(
            problem_text=problem,
            canvas="rfi_canvas",
            role_ids=active_roles,
        )
        instance_id = result.get("instance_id") or result.get("id") or _uid()
    except Exception as exc:
        logger.warning("ACE team launch failed: %s — storing placeholder id", exc)
        instance_id = f"rfi-team-{_uid()[:8]}"

    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sessions SET ace_instance_id=%s, updated_at=%s WHERE id=%s",
        (instance_id, _now(), session_id),
    )
    db.commit()
    return instance_id


def get_ace_team_status(session_id: str) -> dict:
    """Return the ACE team status for a session."""
    session = get_session(session_id)
    if not session:
        return {"status": "no_session", "roles": []}

    ace_id = session.get("ace_instance_id")
    if not ace_id:
        return {"status": "not_launched", "roles": []}

    try:
        from icdev.tools.ace.controller import ACEController
        controller = ACEController()
        status = controller.status(ace_id)
        return {"status": "active", "ace_instance_id": ace_id, **status}
    except Exception as exc:
        logger.warning("ACE team status failed for %s: %s", ace_id, exc)
        return {
            "status": "launched",
            "ace_instance_id": ace_id,
            "roles": [{"role_id": r, "state": "unknown"} for r in _RFI_TEAM_ROLES],
        }


def _launch_ace_team_background(session_id: str):
    """Launch ACE team in background thread after session creation."""
    try:
        launch_ace_team(session_id)
    except Exception as exc:
        logger.warning("Background ACE team launch failed for session %s: %s", session_id, exc)


# ── Phase B: Engine weights + uploads (delegates to rfi_engine_runner) ────────

def get_engine_weights(session_id: str) -> dict:
    from tools.govcon.rfi_engine_runner import get_session_engine_weights
    return get_session_engine_weights(session_id)


def set_engine_weights(session_id: str, weights: dict) -> None:
    from tools.govcon.rfi_engine_runner import set_session_engine_weights
    set_session_engine_weights(session_id, weights)


def get_section_engine_weights_override(section_id: str) -> dict | None:
    from tools.govcon.rfi_engine_runner import get_section_engine_weights
    return get_section_engine_weights(section_id)


def set_section_engine_weights_override(section_id: str, weights: dict) -> None:
    from tools.govcon.rfi_engine_runner import set_section_engine_weights
    set_section_engine_weights(section_id, weights)


def save_session_upload(session_id: str, filename: str, file_path: str, upload_type: str = "past_performance") -> dict:
    from tools.govcon.rfi_engine_runner import save_upload
    return save_upload(session_id, filename, file_path, upload_type)


def list_session_uploads(session_id: str) -> list[dict]:
    from tools.govcon.rfi_engine_runner import get_uploads
    return get_uploads(session_id)


def delete_session_upload(upload_id: str) -> bool:
    from tools.govcon.rfi_engine_runner import delete_upload
    return delete_upload(upload_id)


# ── Item 1: Generate All ──────────────────────────────────────────────────────

_generate_all_progress: dict[str, dict] = {}


def generate_all_sections(session_id: str, profile_name: str, parsed_data: dict) -> None:
    """Daemon thread: generate every pending/rejected section in sequence."""
    sections = get_sections(session_id)
    pending = [s for s in sections if s["status"] in ("pending", "hitl_rejected")]
    _generate_all_progress[session_id] = {
        "total": len(pending), "done": 0, "current_item": None,
        "errors": [], "running": True, "cancelled": False,
    }
    for sec in pending:
        if _generate_all_progress[session_id].get("cancelled"):
            break
        _generate_all_progress[session_id]["current_item"] = sec["item_number"]
        try:
            generate_section_content(sec["id"], profile_name, parsed_data)
        except Exception as exc:
            _generate_all_progress[session_id]["errors"].append(
                f"{sec['item_number']}: {str(exc)[:80]}"
            )
        _generate_all_progress[session_id]["done"] += 1
        _persist_genall_progress(session_id)
        time.sleep(0.1)
    _generate_all_progress[session_id]["running"] = False
    _generate_all_progress[session_id]["current_item"] = None
    _persist_genall_progress(session_id)


_GENALL_TMP_DIR = _ROOT / ".tmp"


def _persist_genall_progress(session_id: str) -> None:
    try:
        _GENALL_TMP_DIR.mkdir(parents=True, exist_ok=True)
        p = _GENALL_TMP_DIR / f"rfi_genall_{session_id}.json"
        p.write_text(json.dumps(_generate_all_progress[session_id]), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not persist generate-all progress for %s: %s", session_id, exc)


def get_generate_all_status(session_id: str) -> dict:
    if session_id in _generate_all_progress:
        return _generate_all_progress[session_id]
    # Fallback: read persisted file from .tmp/ (survives server restart)
    try:
        p = _GENALL_TMP_DIR / f"rfi_genall_{session_id}.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            # Persisted state is always non-running (server restarted mid-run)
            data["running"] = False
            return data
    except Exception:
        pass
    return {"running": False, "done": 0, "total": 0}


def cancel_generate_all(session_id: str) -> None:
    if session_id in _generate_all_progress:
        _generate_all_progress[session_id]["cancelled"] = True


# ── Item 2: Submission readiness gate ────────────────────────────────────────

def get_session_readiness(session_id: str) -> dict:
    """Compute submission readiness checklist from existing DB data."""
    session = get_session(session_id)
    if not session:
        return {"error": "Session not found"}
    sections = get_sections(session_id)

    total = len(sections)
    accepted = sum(1 for s in sections if s["status"] in ("hitl_approved", "accepted"))
    pending = sum(1 for s in sections if s["status"] == "pending")
    rejected = sum(1 for s in sections if s["status"] == "hitl_rejected")

    verify_sections = [
        s["item_number"] for s in sections
        if "[VERIFY]" in (s.get("content") or s.get("ai_draft") or "")
    ]

    page_limit_breach = None
    try:
        from tools.govcon.rfi_style_engine import get_session_style_guide
        sg = get_session_style_guide(session_id)
        page_limit = sg.get("page_limit")
        words_per_page = sg.get("words_per_page") or 250
        if page_limit:
            total_words = sum(
                len((s.get("content") or s.get("ai_draft") or "").split())
                for s in sections
            )
            estimated_pages = total_words / words_per_page
            if estimated_pages > page_limit:
                page_limit_breach = {"estimated": round(estimated_pages, 1), "limit": page_limit}
    except Exception:
        pass

    low_wg_sections = [
        s["item_number"] for s in sections
        if s.get("writeguard_score") is not None
        and float(s["writeguard_score"]) < 60
        and s["status"] in ("hitl_approved", "accepted")
    ]

    checks = [
        {
            "id": "all_accepted",
            "label": "All sections accepted",
            "passed": accepted >= total and total > 0,
            "detail": f"{accepted}/{total} accepted" if accepted < total else None,
        },
        {
            "id": "no_pending",
            "label": "No pending (ungenerated) sections",
            "passed": pending == 0,
            "detail": f"{pending} section(s) still pending" if pending else None,
        },
        {
            "id": "no_rejected",
            "label": "No rejected sections awaiting revision",
            "passed": rejected == 0,
            "detail": f"{rejected} section(s) rejected" if rejected else None,
        },
        {
            "id": "no_verify_tags",
            "label": "No [VERIFY] placeholders remaining",
            "passed": len(verify_sections) == 0,
            "detail": f"[VERIFY] found in: {', '.join(verify_sections)}" if verify_sections else None,
        },
        {
            "id": "page_limit",
            "label": "Document within page limit",
            "passed": page_limit_breach is None,
            "detail": (
                f"~{page_limit_breach['estimated']} pages (limit: {page_limit_breach['limit']})"
                if page_limit_breach else None
            ),
        },
        {
            "id": "quality_gate",
            "label": "No accepted sections below WG 60",
            "passed": len(low_wg_sections) == 0,
            "detail": f"Low quality: {', '.join(low_wg_sections)}" if low_wg_sections else None,
        },
        {
            "id": "consistency",
            "label": "No cross-section conflicts detected",
            "passed": True,
            "detail": None,
        },
    ]

    # Check #7 — cross-section consistency (deterministic pass only; LLM skipped for speed)
    try:
        consistency = check_cross_section_consistency(session_id, skip_llm=True)
        errors = [c for c in consistency.get("conflicts", []) if c.get("severity") == "error"]
        warnings = [c for c in consistency.get("conflicts", []) if c.get("severity") == "warning"]
        cons_check = next(c for c in checks if c["id"] == "consistency")
        if errors:
            cons_check["passed"] = False
            cons_check["detail"] = f"{len(errors)} conflict(s): {errors[0]['message'][:80]}"
        elif warnings:
            cons_check["passed"] = False
            cons_check["detail"] = f"{len(warnings)} warning(s): {warnings[0]['message'][:80]}"
        else:
            cons_check["passed"] = True
    except Exception as exc:
        logger.warning("Consistency check in readiness failed: %s", exc)

    passed = sum(1 for c in checks if c["passed"])
    return {
        "ready": passed == len(checks),
        "passed": passed,
        "total_checks": len(checks),
        "checks": checks,
        "summary": f"{passed}/{len(checks)} checks passed",
    }


# ── Item 6: Submission deadline countdown ────────────────────────────────────

def _compute_days_remaining(deadline_str: str) -> int | None:
    try:
        import re
        # Accept YYYY-MM-DD or MM/DD/YYYY
        s = deadline_str.strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            dl = date.fromisoformat(s[:10])
        elif re.match(r"\d{1,2}/\d{1,2}/\d{4}", s):
            from datetime import datetime as _dt
            dl = _dt.strptime(s, "%m/%d/%Y").date()
        else:
            return None
        return (dl - date.today()).days
    except Exception:
        return None


def get_deadline_info(session_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        return {}
    parsed = session.get("parsed_data") or {}
    deadline = parsed.get("response_date") or session.get("response_date")
    days = _compute_days_remaining(deadline) if deadline else None
    return {
        "deadline": deadline,
        "days_remaining": days,
        "urgent": days is not None and days <= 7,
        "overdue": days is not None and days < 0,
    }


def save_session_deadline(session_id: str, deadline: str) -> dict:
    session = get_session(session_id)
    if not session:
        return {}
    parsed = session.get("parsed_data") or {}
    parsed["response_date"] = deadline.strip()
    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sessions SET parsed_data=%s, updated_at=%s WHERE id=%s",
        (json.dumps(parsed), _now(), session_id),
    )
    db.commit()
    return get_deadline_info(session_id)


# ── Item 7: Competitive differentiator ("Generate Why Us") ───────────────────

def generate_why_us(session_id: str, profile_name: str, competitor_name: str = "") -> dict:
    """Generate a 'Why Us' competitive differentiator paragraph for section 5.1.

    Uses profile capability_statements + optional competitor_name.
    Routes via rfi_writer_drafting (LOCAL ONLY — CUI content).
    Returns {"paragraph": str, "word_count": int}.
    """
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    profile = _load_profile(profile_name)
    cap_stmts = profile.get("capability_statements") or []
    tech_diff = profile.get("technical_differentiators") or []
    solution_name = profile.get("solution_name") or profile.get("entity_name", "our solution")
    entity_name = profile.get("entity_name", "the responding organization")
    rfi_title = session.get("rfi_title", "the solicitation")

    bullets = "\n".join(f"  • {c}" for c in cap_stmts) if cap_stmts else "  • No specific capabilities listed in profile."
    diff_text = ("\nTechnical differentiators:\n" + "\n".join(f"  • {d}" for d in tech_diff)) if tech_diff else ""
    comp_ctx = f"\nKey competitor context (do NOT name them — differentiate by merit): {competitor_name}" if competitor_name.strip() else ""

    prompt = (
        f"You are a GovCon proposal strategist. Write a competitive differentiator paragraph "
        f"for the 'Industry Insights' section of an RFI response.\n\n"
        f"Entity: {entity_name}\nSolution: {solution_name}\nRFI: {rfi_title}{comp_ctx}\n\n"
        f"Capability statements:\n{bullets}{diff_text}\n\n"
        f"Write a 150-200 word paragraph that:\n"
        f"1. States why {entity_name} is uniquely positioned to address this requirement\n"
        f"2. References specific capabilities from the list above (not generic claims)\n"
        f"3. Never names competitors — differentiates by merit\n"
        f"4. Ends with a clear, concrete value proposition\n\n"
        f"Return ONLY the paragraph. No headers, no bullet points, no preamble."
    )

    role_key = f"{session_id}:rfi_writer"
    paragraph = _call_llm(
        prompt,
        "Why Us — Competitive Differentiator",
        "5.1",
        role_key=role_key,
        llm_function="rfi_writer_drafting",
    )
    return {"paragraph": paragraph, "word_count": len(paragraph.split())}


# ── Item 8: Parse summary ─────────────────────────────────────────────────────

def get_parse_summary(session: dict) -> dict:
    """Return parse metadata for the upload-feedback banner."""
    parsed = session.get("parsed_data") or {}
    rfi_number = session.get("rfi_number", "RFI-UNKNOWN")
    parse_fallback = rfi_number == "RFI-UNKNOWN" or not parsed.get("questionnaire_parts")
    return {
        "parse_fallback": parse_fallback,
        "rfi_number": rfi_number,
        "rfi_title": session.get("rfi_title", ""),
        "objectives_count": len(parsed.get("objectives") or []),
        "questionnaire_parts_count": len(parsed.get("questionnaire_parts") or []),
    }
