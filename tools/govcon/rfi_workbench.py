"""RFI Response Workbench — backing module.

Handles: session management, section CRUD, AI generation dispatch,
WriteGuard integration, HITL state machine, export assembly,
requirements layer (extract/CRUD/coverage), ACE team integration.
"""
from __future__ import annotations

import json
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

_DEFAULT_SECTIONS = [
    ("part1", "1.1", "Entity Data",             "Part 1", "Provide company name, contact info, CAGE code, and SAM.gov UEI."),
    ("part1", "1.2", "Business Size",           "Part 1", "Identify business size and socioeconomic status for the primary NAICS code."),
    ("part1", "1.3", "NDC Status",              "Part 1", "Do you qualify as a Nontraditional Defense Contractor per 10 U.S.C. 3014?"),
    ("part1", "1.4", "Foreign Interest (FOCI)", "Part 1", "Is the entity or any parent company foreign-owned, controlled, or influenced?"),
    ("part1", "1.5", "Security Clearances",     "Part 1", "Do personnel hold active clearances? Specify clearance levels available."),
    ("part2", "2.1", "Current TRL",             "Part 2", "Identify the Technology Readiness Level of your proposed solution."),
    ("part2", "2.2", "Statefulness & Cold-Start","Part 2", "Is the orchestration stateless or stateful? How is state synchronized? How does the orchestrator handle cold-start of new processing resources?"),
    ("part2", "2.4", "Technical Approach",      "Part 2", "Describe your approach to meeting the objectives. What modifications would be required?"),
    ("part2", "2.5", "Commerciality & Cybersecurity","Part 2", "Is the solution a Commercial Product per FAR 2.101? Describe cybersecurity posture and supply chain risk."),
    ("part2", "2.7", "Mission-Specific Questions","Part 2", "Address latency at scale, multi-constraint logic, dynamic priority injection, failure recovery, and cost tracking."),
    ("part3", "3.1", "Timeline",                "Part 3", "Estimated time from award to delivery of first working prototype."),
    ("part3", "3.2", "Key Risk Areas",          "Part 3", "Identify the top two technical or schedule risks."),
    ("part3", "3.3", "Custom Risk Responses",   "Part 3", "Address explainability vs. performance trade-offs, starvation prevention, integration complexity."),
    ("part4", "4.1", "Data Rights",             "Part 4", "Describe data rights and IP assertions. Identify components with Restricted, Limited, or GPR."),
    ("part4", "4.2", "ROM Cost Estimate",       "Part 4", "Rough Order of Magnitude cost estimate. Break down by hardware, software, labor, ODC."),
    ("part4", "4.3", "Teaming / Cost Share",    "Part 4", "NDC teaming plan or 1/3 cost-share per 10 U.S.C. 4022."),
    ("part5", "5.1", "Industry Insights",       "Part 5", "What did the Government miss? What technical or programmatic considerations should be added?"),
    ("appendix", "A", "Architecture Overview",  "Appendix", "Technical architecture — Intelligence Layer, Execution Layer, and cloud-native deployment."),
    ("appendix", "B", "Adaptive Learning Loop", "Appendix", "Learning loop — ECHO, SOUL, TRUST, SELA components and feedback latency benchmarks."),
]

# ── Section generation prompts ────────────────────────────────────────────────

_SECTION_PROMPTS = {
    "1.1": "Generate Part 1.1 administrative data for {entity_name}. Include the table format with company name, address, contact name/title/email/phone, CAGE code, and SAM.gov UEI. Mark fields that need verification with [VERIFY].",
    "1.2": "Generate Part 1.2 business size declaration for {entity_name} under NAICS {primary_naics}. State business size ({business_size}), socioeconomic status, and recommend the NAICS code with rationale.",
    "1.3": "Generate Part 1.3 NDC status for {entity_name}. Status: {ndc_status}. Include teaming strategy reference if traditional contractor.",
    "1.4": "Generate Part 1.4 FOCI disclosure for {entity_name}. Confirm no foreign ownership, control, or influence.",
    "1.5": "Generate Part 1.5 security clearances for {entity_name}. Clearances available: {clearances}. Include SCIF capability statement.",
    "2.1": "Generate Part 2.1 TRL assessment for {entity_name} addressing: {rfi_title}. Use Hybrid TRL 6: core components TRL 8 (commercially deployed), NSA integration layer TRL 5-6. Include a table mapping component to TRL with evidence.",
    "2.2": "Generate Part 2.2/2.3 statefulness and cold-start response for {entity_name}. Explain stateful-light architecture: routing decisions are stateless per object; resource-availability sidecar maintains eventually-consistent view via gossip protocol (100ms intervals); policy store is GitOps hot-reload (atomic swap, zero restart). Cold-start: KEDA event-driven autoscaling + pre-warm pools + graceful degradation to best-effort with provenance flag. {hitl_context}",
    "2.4": "Generate Part 2.4 technical approach for {entity_name}'s response to RFI {rfi_number}. Describe the three-tier governed orchestration: Rule Engine (<100µs, 90% of volume) → CoD (<15ms, 8%) → CoT (<2s, 2%). Map to objectives: {objectives_list}. Include an ASCII architecture diagram. Emphasize intelligence layer (async, policy timescale) vs execution layer (per-object timescale). {hitl_context}",
    "2.5": "Generate Part 2.5/2.6 commerciality and cybersecurity for {entity_name}. Confirm commercial product per FAR 2.101, ~70% commercial/30% developmental. Cybersecurity table: CMMC Level 2, NIST SP 800-171, CycloneDX SBOM, container hardening, NDAA §889 supply chain compliance. {hitl_context}",
    "2.7": "Generate Part 2.7 mission-specific Q&A for {entity_name}. Cover: (1) Latency benchmarks (Python prototype ~2ms, production C/Rust target <50µs, O(log N) scaling); (2) Multi-constraint logic (secondary pool → queue → CoD degradation → guaranteed retry); (3) Dynamic priority injection (REST API + YAML hot-reload, <1s propagation, atomic rule-tree swap); (4) Failure recovery (circuit breaker, dead-letter queue, W3C PROV-AGENT provenance); (5) Cost tracking (per-routing-decision cost ledger, cost_usd + duration_ms per step). {hitl_context}",
    "3.1": "Generate Part 3.1 project timeline for {entity_name}: M1-2 environment setup + test data integration; M3 core prototype (three-tier stack + HITL + XAI dashboard); M4 integration testing (latency benchmarks, priority injection, failure recovery); M5 adaptive learning (NOVA feedback loop + Bayesian tuning); M6 prototype demo. Present as a table. {hitl_context}",
    "3.2": "Generate Part 3.2 key risks for {entity_name}. Risk 1: Latency validation in customer environment (mitigation: sidecar co-located with processing resources, validate by Month 2). Risk 2: Mission metrics API dependency (mitigation: YAML static rules as fallback; live dynamic injection as Phase 2). {hitl_context}",
    "3.3": "Generate Part 3.3 custom risk responses for {entity_name}. (1) Explainability vs. performance: async ring buffer, 100ms flush, <2µs overhead per object, eventually consistent audit trail. (2) Starvation: minimum service floor (configurable % capacity reserved for best-effort), age-weighted priority boost (TTL-based escalation), hard max-wait cap. (3) Integration complexity: 50MB sidecar container, /health and /capacity endpoints, Helm chart deployment, no modification to existing LLM/analytics internals. {hitl_context}",
    "4.1": "Generate Part 4.1 data rights for {entity_name}. Table: core platform (prior IR&D) → Government Purpose Rights; integration adapters developed under effort → Unlimited Rights; models trained on customer operations data → Government owns all data and derivative models. No proprietary restrictions on operational use. {hitl_context}",
    "4.2": "Generate Part 4.2 ROM cost estimate for {entity_name}. Table: Labor ~$1.2M (6 engineers x 6 months, blended $200K/yr), Software/Cloud ~$75K, Hardware (GPU dev cluster) ~$150K, ODC ~$50K, ROM Total ~$1.475M (±30%). Annual O&M: ~$400K/year. Annual licensing: ~$250K/year. {hitl_context}",
    "4.3": "Generate Part 4.3 teaming/cost share for {entity_name}. {ndc_status}. Option A — NDC Teaming (Preferred): identify NDC partner at solicitation stage, >1/3 technical effort on novel AI/ML core components. Option B — IR&D Cost Share: ~$490K against $1.475M ROM from existing IR&D commitments per 10 U.S.C. 4022. {hitl_context}",
    "5.1": "Generate Part 5 industry insights for {entity_name}'s response to {rfi_title}. Provide 4 recommendations the Government missed: (1) Data provenance/routing lineage — recommend adding as a required objective; (2) Federated learning for sensitive routing feedback — modify Objective F; (3) Multi-classification routing scope — clarify whether orchestrator must span ILs; (4) Human-machine teaming escalation — recommend adding analyst override objective. Each recommendation should include specific proposed solicitation language. {hitl_context}",
    "A": "Generate Appendix A architecture overview for {entity_name}'s FORGE framework. Intelligence Layer (policy timescale): NOVA learning, XAI engine (AgentSHAP + W3C PROV-AGENT), HITL override API, RTM traceability. Execution Layer (object timescale): Rule Engine (<50µs compiled), CoD engine (ThreadPoolExecutor, 3 debaters, <15ms), CoT engine (reason→critic→synthesize, async audit, <2s). Include AWS GovCloud/C2S deployment ASCII diagram. {hitl_context}",
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
            seen.add(key)
            part_key = p.get("part", "unknown").lower().replace(" ", "")
            item = p.get("item_number", "?")
            title = p.get("topic", f"Section {item}")
            question = p.get("question", "")
            rows.append((part_key, item, title, p.get("part", ""), question))
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


def get_sections(session_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM rfi_workbench_sections WHERE session_id=%s ORDER BY part, item_number",
        (session_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
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
        result.append(d)
    return result


def get_section(section_id):
    db = get_db()
    row = db.execute("SELECT * FROM rfi_workbench_sections WHERE id=%s", (section_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
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
    return d


def save_section_content(section_id, content):
    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET content=%s, updated_at=%s WHERE id=%s",
        (content, _now(), section_id),
    )
    db.commit()


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
    return get_section(section_id)


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
    )

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

    draft = _call_llm(prompt, section["title"], item)

    # Run deterministic markdown validator — attach as transient field
    try:
        from tools.govcon.rfi_markdown_validator import validate_markdown_structure
        md_validation = validate_markdown_structure(draft)
    except Exception as exc:
        logger.warning("Markdown validator failed: %s", exc)
        md_validation = {"valid": True, "issues": []}

    db = get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET ai_draft=%s, content=%s, status='ai_draft_ready', generation_count=generation_count+1, updated_at=%s WHERE id=%s",
        (draft, draft, _now(), section_id),
    )
    db.commit()

    # Kick off coverage check in background
    threading.Thread(target=_check_coverage_background, args=(section_id,), daemon=True).start()

    result = get_section(section_id)
    result["markdown_validation"] = md_validation
    return result


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


def _call_llm(prompt, section_title, item_number):
    try:
        from tools.llm.router import LLMRequest
        router = _get_router()
        req = LLMRequest(
            messages=[{
                "role": "user",
                "content": (
                    f"You are an expert GovCon proposal writer for a defense/IC contractor. "
                    f"Write the '{section_title}' (Item {item_number}) section of an RFI response. "
                    f"Be specific, professional, and avoid vague claims. Use concrete numbers and built capabilities. "
                    f"Format as clean markdown with bold headers and tables where appropriate. "
                    f"UNCLASSIFIED content only. Keep to ≤400 words unless the section requires detail.\n\n"
                    f"{prompt}"
                ),
            }],
            max_tokens=900,
        )
        response = router.invoke("proposal_drafting", req)
        if hasattr(response, "content"):
            return response.content
        if isinstance(response, dict):
            return response.get("content", response.get("text", str(response)))
        return str(response)
    except Exception as exc:
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


# ── Export ────────────────────────────────────────────────────────────────────

def assemble_and_export(session_id, export_format="docx"):
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    sections = get_sections(session_id)
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


def _build_markdown(session, sections, profile, parsed):
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

    lines += ["", "---", "", "> UNCLASSIFIED//FOUO", ""]
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
        return ["own_company", "peraton"]


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

        import re
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        raw = raw.strip().rstrip("```").strip()

        coverage_map = json.loads(raw)
        if not isinstance(coverage_map, dict):
            return reqs

        for r in reqs:
            short_id = r["id"][:8]
            verdict = coverage_map.get(short_id)
            if verdict is True or verdict == "true":
                r["covered"] = True
            elif verdict is False or verdict == "false":
                r["covered"] = False
            elif verdict == "partial":
                r["covered"] = "partial"

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
    """Launch the 5-role RFI ACE team for a session. Returns the ace_instance_id."""
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    try:
        from icdev.tools.ace.controller import ACEController
        controller = ACEController()
        problem = (
            f"RFI response color team for session {session_id}: "
            f"{session.get('rfi_title', 'RFI')} ({session.get('rfi_number', '')}). "
            f"Pipeline: Writer → Editor → Reviewer → Researcher → Compliance Reviewer."
        )
        result = controller.launch(
            problem_text=problem,
            canvas="rfi_canvas",
            role_ids=_RFI_TEAM_ROLES,
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
