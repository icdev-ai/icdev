# CUI // SP-CTI
#!/usr/bin/env python3
"""AI-ify Canvas — PRD AI-Readiness Assessor (EQO-PRD-01).

Pre-build readiness check: given an intake/requirements session, score how
*AI-ready* the PRD is and infer which AI-augmentation opportunities the
requirements text implies — BEFORE a single line is built.

This is the "shift-left" sibling of the post-hoc code scanner
(``tools/aiify/pattern_classifier.py`` + ``opportunity_scorer.py``): instead of
detecting AI-augmentable *patterns in code*, it detects AI-augmentable *intent
in prose* and maps each phrase to an AI-ify paradigm, then reuses the exact same
deterministic opportunity scorer so PRD-stage and code-stage opportunities are
comparable.

Public API:
    assess_prd_for_ai_readiness(session_id, conn, use_llm=False) -> dict
        {
          "session_id", "project_id", "impact_level",
          "score",                # overall PRD AI-readiness [0.0, 1.0]
          "components",           # 6 weighted components, each {score, weight, detail}
          "gaps",                 # list of {component, message, remediation}
          "opportunities",        # inferred + scored via opportunity_scorer.score_and_assess
          "overall_ai_readiness", # ai_ready | partial | not_suitable
          "recommendations",      # actionable next steps
          "use_llm", "classification"
        }

The deterministic path is ALWAYS the fallback (air-gap safe). ``use_llm=True``
only *augments* the deterministic result with extra recommendations/opportunities
via the LLM Router; any failure degrades silently to the deterministic output.

Six deterministic components (weights):
    ai_mention_density                 0.20  — does the PRD even ask for AI?
    data_requirement_specificity       0.20  — is training/grounding data described?
    integration_clarity                0.15  — are the systems/APIs the AI plugs into named?
    acceptance_criteria_quantifiability 0.20  — are success metrics measurable?
    governance_declarations            0.15  — HITL / audit / CUI / bias declared?
    il_model_appropriateness           0.10  — is an IL-appropriate model reachable?

Usage:
    python tools/aiify/prd_readiness_assessor.py --session-id sess-abc --json
    python tools/aiify/prd_readiness_assessor.py --session-id sess-abc --use-llm

Classification: CUI // SP-CTI
Compliance:     NIST 800-53 Rev 5 / RMF; NIST AI RMF
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.aiify.constants import PATTERN_TO_PARADIGM
from tools.aiify.opportunity_scorer import roll_up_scan_verdict, score_and_assess

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Component weights (sum to 1.0) ────────────────────────────────────────────
# Kept in-module (mirrors opportunity_scorer's inline fallback weights) so the
# assessor is self-contained and air-gap safe.
COMPONENT_WEIGHTS: dict[str, float] = {
    "ai_mention_density": 0.20,
    "data_requirement_specificity": 0.20,
    "integration_clarity": 0.15,
    "acceptance_criteria_quantifiability": 0.20,
    "governance_declarations": 0.15,
    "il_model_appropriateness": 0.10,
}

# ── Lexicons ──────────────────────────────────────────────────────────────────
_AI_KEYWORDS: tuple[str, ...] = (
    "ai", "artificial intelligence", "machine learning", "ml", "deep learning",
    "neural network", "llm", "large language model", "generative ai", "genai",
    "model inference", "algorithmic", "predictive", "classification model",
    "recommendation engine", "natural language", "nlp", "computer vision",
    "embedding", "semantic search", "anomaly detection", "intelligent",
    "auto-classif", "auto classif", "smart ", "agentic", "rag",
)

_DATA_TERMS: tuple[str, ...] = (
    "dataset", "data set", "training data", "historical data", "labeled",
    "labelled", "records", "corpus", "sample", "examples", "schema", "fields",
    "data source", "data volume", "log", "logs", "database", "ground truth",
    "annotation", "feature", "data format", "csv", "json", "data pipeline",
)

_INTEGRATION_TERMS: tuple[str, ...] = (
    "api", "rest", "endpoint", "integrate", "integration", "interface",
    "webhook", "connector", "sync", "import", "export", "third-party",
    "third party", "service", "microservice", "soap", "graphql", "queue",
    "message bus", "system of record", " servicenow", "jira", "salesforce",
    "sharepoint", "sipr", "niprnet", "gateway", "ingest",
)

_GOVERNANCE_TERMS: tuple[str, ...] = (
    "human-in-the-loop", "human in the loop", "hitl", "oversight", "audit",
    "auditable", "explainab", "explainability", "bias", "fairness",
    "accountab", "model card", "nist ai rmf", "iso 42001", "iso42001",
    "transparency", "classification", "cui", "review and approve",
    "human review", "guardrail", "rollback", "reversible", "ethics",
    "governance", "data sovereignty", "impact assessment",
)

# Quantifiable acceptance-criteria signals (numbers paired with units/metrics,
# or explicit comparison operators).
_QUANT_RE = re.compile(
    r"(\b\d+(?:\.\d+)?\s*(?:%|percent|ms|millisecond|second|sec|minute|hour|day|"
    r"user|request|record|document|gb|mb|tps|rps|qps|accuracy|recall|precision|"
    r"f1|sla|uptime|availability)s?\b)"
    r"|([<>]=?\s*\d)"
    r"|(\bwithin\s+\d)"
    r"|(\bat least\s+\d)"
    r"|(\bno more than\s+\d)",
    re.IGNORECASE,
)

# IL-appropriate model declarations (local / on-prem models for high IL).
_LOCAL_MODEL_RE = re.compile(
    r"\b(local model|on-?prem(?:ise)? model|ollama|air-?gap(?:ped)? model|"
    r"self-?hosted model|llama|mistral|sovereign model|fine-?tun)\b",
    re.IGNORECASE,
)

# ── Phrase → AI-ify pattern_type inference rules ──────────────────────────────
# Each rule maps a prose phrase to one of constants.PATTERN_TYPES; the pattern
# then flows through PATTERN_TO_PARADIGM and the shared opportunity scorer.
_PHRASE_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(extract|capture)\b[^.]{0,30}\b(field|entit|metadata|value|"
                r"vendor|invoice|amount|date|attribute)", re.IGNORECASE),
     "metadata_extraction", "extract structured fields"),
    (re.compile(r"\b(extract|parse|pull|identif(?:y|ies)|read)\b[^.]{0,40}\bfrom\b",
                re.IGNORECASE),
     "regex_user_input", "extract information from input"),
    (re.compile(r"\b(generate|draft|compose|produce|create|write|author)\b"
                r"[^.]{0,40}\b(report|e-?mail|letter|summary|notification|memo|"
                r"narrative|response|message|correspondence)", re.IGNORECASE),
     "string_template_rendering", "generate written content"),
    (re.compile(r"\b(notify|send (an? )?(e-?mail|alert|message|notification))\b"
                r"[^.]{0,50}\bwhen\b", re.IGNORECASE),
     "db_render_notify_chain", "query-render-notify pipeline"),
    (re.compile(r"\b(threshold|exceed|exceeds|breach|over (the )?limit|"
                r"alert when|flag when|greater than|less than|spike)\b",
                re.IGNORECASE),
     "hardcoded_threshold", "threshold / alerting"),
    (re.compile(r"\b(anomal|outlier|unusual (pattern|activity|behaviou?r)|"
                r"fraud detection)\b", re.IGNORECASE),
     "hardcoded_threshold", "anomaly detection"),
    (re.compile(r"\b(full[- ]?text search|semantic search|search (the )?"
                r"(documents?|corpus|knowledge|content))", re.IGNORECASE),
     "fulltext_search_engine", "document search"),
    (re.compile(r"\b(search|find|look ?up|query)\b[^.]{0,30}"
                r"\b(keyword|term|phrase|by name|by text)", re.IGNORECASE),
     "keyword_list_search", "keyword search"),
    (re.compile(r"\b(classif\w*|categori[sz]\w*|auto-?tag\w*|triage)\b",
                re.IGNORECASE),
     "manual_classification_ui", "classification / tagging"),
    (re.compile(r"\b(rout\w*|dispatch\w*|re-?assign\w*|forward\w*|redirect\w*)\b"
                r"[^.]{0,30}\b(document|ticket|case|request|e-?mail|workflow|"
                r"queue|team|reviewer)", re.IGNORECASE),
     "document_routing", "routing / dispatch"),
    (re.compile(r"\b(ocr|scan(?:ned)? document|optical character|digiti[sz]e)\b",
                re.IGNORECASE),
     "ocr_extraction_pipeline", "OCR extraction"),
    (re.compile(r"\b(ingest|upload|intake)\b[^.]{0,30}"
                r"\b(document|file|attachment|batch)", re.IGNORECASE),
     "document_ingestion_pipeline", "document ingestion"),
    (re.compile(r"\b(schedul|nightly|daily|hourly|periodic|recurring|cron|"
                r"every (day|hour|week|night|morning)|batch job)\b", re.IGNORECASE),
     "scheduled_cron", "scheduled / recurring job"),
    (re.compile(r"\b(recommend\w*|suggest\w*|predict\w*|forecast\w*|"
                r"prioriti[sz]\w*|eligib\w*|rank\w*|scoring rule|"
                r"decision (matrix|table|tree))\b", re.IGNORECASE),
     "large_rule_table", "decision / recommendation rules"),
]

_MAX_OPPORTUNITIES = 30


# ============================================================================
# DB access helpers (backend-agnostic: SQLite tuples or PG dict rows)
# ============================================================================


def _fetch_dicts(conn, sql: str, params: tuple) -> list[dict]:
    """Run a query and return rows as plain dicts regardless of row factory."""
    cur = conn.execute(sql, params)
    cols = [d[0] for d in (cur.description or [])]
    out: list[dict] = []
    for row in cur.fetchall():
        if isinstance(row, dict):
            out.append(dict(row))
        elif isinstance(row, (tuple, list)):
            out.append(dict(zip(cols, row)))
        else:  # sqlite3.Row or other mapping-by-key
            try:
                out.append({c: row[c] for c in cols})
            except Exception:
                out.append(dict(zip(cols, row)))
    return out


def _load_prd(session_id: str, conn) -> dict:
    """Read PRD text + structured requirements for a session.

    Returns a dict: {session, requirements, text}. ``text`` is the concatenated
    corpus used for lexical scoring; ``requirements`` is the per-requirement list
    used for opportunity inference and acceptance-criteria scoring.
    """
    sessions = _fetch_dicts(
        conn,
        "SELECT id, project_id, customer_org, impact_level, context_summary, "
        "source_documents FROM intake_sessions WHERE id = ?",
        (session_id,),
    )
    session = sessions[0] if sessions else {}

    requirements = _fetch_dicts(
        conn,
        "SELECT id, raw_text, refined_text, requirement_type, priority, "
        "acceptance_criteria, compliance_impact FROM intake_requirements "
        "WHERE session_id = ? ORDER BY source_turn",
        (session_id,),
    )

    parts: list[str] = []
    ctx = session.get("context_summary")
    if ctx:
        try:
            parsed = json.loads(ctx)
            if isinstance(parsed, dict):
                parts.append(" ".join(str(v) for v in parsed.values() if v))
            else:
                parts.append(str(ctx))
        except (ValueError, TypeError):
            parts.append(str(ctx))
    if session.get("source_documents"):
        parts.append(str(session["source_documents"]))
    for req in requirements:
        parts.append(req.get("refined_text") or "")
        parts.append(req.get("raw_text") or "")
        parts.append(req.get("acceptance_criteria") or "")

    return {
        "session": session,
        "requirements": requirements,
        "text": "\n".join(p for p in parts if p),
    }


# ============================================================================
# Lexical helpers
# ============================================================================


def _count_hits(text: str, terms: tuple[str, ...]) -> tuple[int, list[str]]:
    """Count how many distinct terms occur in text; return (count, matched)."""
    low = text.lower()
    matched = [t.strip() for t in terms if t in low]
    return len(matched), matched


def _il_to_level(impact_level) -> str:
    """Map an intake impact_level (IL2/IL4/IL5/IL6) to a scorer il_level."""
    il = str(impact_level or "IL4").lower().replace("-", "").strip()
    if il in ("il5",):
        return "il5"
    if il in ("il6",):
        return "il6"
    return "il4"


# ============================================================================
# Component scorers
# ============================================================================


def _score_ai_mention_density(text: str, n_reqs: int) -> tuple[float, dict]:
    low = text.lower()
    hits = sum(low.count(kw) for kw in _AI_KEYWORDS)
    distinct, matched = _count_hits(text, _AI_KEYWORDS)
    # Density relative to the number of requirements: a PRD that mentions AI
    # concepts across several requirements scores higher than one passing nod.
    denom = max(1, n_reqs)
    density = hits / denom
    score = min(1.0, 0.5 * min(1.0, density / 1.5) + 0.5 * min(1.0, distinct / 4.0))
    return round(score, 4), {"hits": hits, "distinct_terms": distinct,
                             "matched": matched[:8], "per_requirement": round(density, 3)}


def _score_data_specificity(text: str) -> tuple[float, dict]:
    distinct, matched = _count_hits(text, _DATA_TERMS)
    score = min(1.0, distinct / 5.0)
    return round(score, 4), {"distinct_terms": distinct, "matched": matched[:8]}


def _score_integration_clarity(text: str) -> tuple[float, dict]:
    distinct, matched = _count_hits(text, _INTEGRATION_TERMS)
    score = min(1.0, distinct / 4.0)
    return round(score, 4), {"distinct_terms": distinct, "matched": matched[:8]}


def _score_acceptance_quantifiability(requirements: list[dict], text: str) -> tuple[float, dict]:
    if requirements:
        total = len(requirements)
        quantifiable = 0
        with_ac = 0
        for req in requirements:
            ac = req.get("acceptance_criteria") or ""
            body = " ".join(filter(None, [req.get("refined_text"), req.get("raw_text"), ac]))
            if ac.strip():
                with_ac += 1
            if _QUANT_RE.search(body):
                quantifiable += 1
        score = quantifiable / total
        return round(score, 4), {"total_requirements": total,
                                 "with_acceptance_criteria": with_ac,
                                 "quantifiable": quantifiable}
    # No structured requirements — fall back to corpus-level quantifiability.
    matches = len(_QUANT_RE.findall(text))
    return round(min(1.0, matches / 5.0), 4), {"total_requirements": 0,
                                               "corpus_quant_matches": matches}


def _score_governance(text: str, project_id, conn) -> tuple[float, dict, list[dict]]:
    """Governance component — text declarations blended with the DB sub-scorer.

    Reuses tools/requirements/ai_governance_scorer for the artifact-backed
    governance score + gap list whenever it is applicable; otherwise relies on
    the deterministic text scan (air-gap safe).
    """
    distinct, matched = _count_hits(text, _GOVERNANCE_TERMS)
    text_score = min(1.0, distinct / 5.0)
    detail: dict = {"distinct_terms": distinct, "matched": matched[:8]}
    gaps: list[dict] = []

    if project_id and conn is not None:
        try:
            from tools.requirements.ai_governance_scorer import (
                score_ai_governance_readiness,
            )
            db = score_ai_governance_readiness(project_id, conn=conn)
            if db and not db.get("note"):  # applicable + real components
                detail["db_governance_score"] = db.get("score")
                gaps.extend(db.get("gaps", []))
                score = round(0.5 * text_score + 0.5 * float(db.get("score", 0.0)), 4)
                return score, detail, gaps
            if db and db.get("note"):
                detail["db_governance_note"] = db["note"]
        except Exception as exc:  # pragma: no cover - defensive
            detail["db_governance_error"] = str(exc)

    return round(text_score, 4), detail, gaps


def _score_il_appropriateness(text: str, il_level: str) -> tuple[float, dict]:
    """Is an IL-appropriate model reachable for the requested impact level?

    IL4 → cloud-authorised models exist (1.0). IL5 → slightly tighter (0.85).
    IL6 → SIPR/air-gap; needs an explicitly declared local/on-prem model, else
    the AI ambitions outrun the available model envelope.
    """
    has_local = bool(_LOCAL_MODEL_RE.search(text))
    if il_level == "il6":
        score = 1.0 if has_local else 0.55
    elif il_level == "il5":
        score = 1.0 if has_local else 0.85
    else:
        score = 1.0
    return round(score, 4), {"il_level": il_level, "local_model_declared": has_local}


# ============================================================================
# Opportunity inference
# ============================================================================


def _infer_opportunities(prd: dict, scan_context: dict) -> list[dict]:
    """Map requirement prose → AI-ify patterns → scored opportunities.

    Each matched phrase becomes a synthetic "pattern" dict (same shape the code
    scanner emits) and flows through the shared ``score_and_assess`` so PRD-stage
    opportunities are scored identically to code-stage ones.
    """
    requirements = prd["requirements"]
    # If there are no structured requirements, treat the whole corpus as one.
    units: list[tuple[str, str]] = (
        [(r.get("id") or f"req-{i}",
          " ".join(filter(None, [r.get("refined_text"), r.get("raw_text")])))
         for i, r in enumerate(requirements)]
        if requirements else [("corpus", prd["text"])]
    )

    opportunities: list[dict] = []
    for req_id, body in units:
        if not body.strip():
            continue
        seen: set[str] = set()
        for rule_re, ptype, label in _PHRASE_RULES:
            if ptype in seen:
                continue
            m = rule_re.search(body)
            if not m:
                continue
            seen.add(ptype)
            func_loc = max(6, min(400, len(body) // 8))
            pattern = {
                "pattern_type": ptype,
                "ai_paradigm": PATTERN_TO_PARADIGM.get(ptype, "llm_generation"),
                "module_path": f"prd://{prd['session'].get('id', 'session')}/{req_id}",
                "line_start": 1,
                "line_end": 1,
                "pattern_detail": {"func_loc": func_loc, "source": "prd_inference"},
            }
            scored = score_and_assess(pattern, scan_context)
            scored["requirement_id"] = req_id
            scored["pattern_type"] = ptype
            scored["matched_phrase"] = label
            scored["evidence"] = body[max(0, m.start() - 20):m.end() + 30].strip()
            opportunities.append(scored)
            if len(opportunities) >= _MAX_OPPORTUNITIES:
                return opportunities
    return opportunities


# ============================================================================
# Recommendations
# ============================================================================


def _build_recommendations(components: dict, opportunities: list[dict], gaps: list[dict]) -> list[str]:
    recs: list[str] = []
    c = {k: v["score"] for k, v in components.items()}
    if c["ai_mention_density"] < 0.4 and opportunities:
        recs.append("Requirements imply AI value but rarely name it — make the AI "
                    "augmentation explicit per requirement so it is planned, not assumed.")
    if c["data_requirement_specificity"] < 0.5:
        recs.append("Specify the data each AI feature needs (source, volume, labels, "
                    "format) — under-specified data is the #1 cause of AI feasibility failure.")
    if c["integration_clarity"] < 0.5:
        recs.append("Name the systems/APIs the AI components plug into so integration "
                    "complexity can be estimated before build.")
    if c["acceptance_criteria_quantifiability"] < 0.6:
        recs.append("Add quantifiable acceptance criteria (latency, accuracy, throughput, "
                    "user counts) — AI features without measurable targets cannot be validated.")
    if c["governance_declarations"] < 0.5:
        recs.append("Declare AI governance up front: human-in-the-loop points, audit "
                    "logging, CUI marking, and bias/explainability expectations.")
    if c["il_model_appropriateness"] < 0.8:
        recs.append("Confirm an IL-appropriate model is reachable (declare a local/on-prem "
                    "model for IL6/air-gap) before committing to AI features.")
    ready = [o for o in opportunities if o.get("ai_readiness") == "ai_ready"]
    if ready:
        lead = max(ready, key=lambda o: o.get("composite_score", 0))
        recs.append(f"Strongest AI candidate: '{lead.get('matched_phrase')}' "
                    f"({lead.get('category')}, {lead.get('composite_score', 0):.0%}) — "
                    f"prioritise this for the first build increment.")
    if not opportunities:
        recs.append("No AI-augmentation opportunities inferred from the requirements — "
                    "this PRD reads as conventional CRUD/workflow; do not add AI for its own sake.")
    for g in gaps[:3]:
        rem = g.get("remediation")
        if rem and rem not in recs:
            recs.append(rem)
    return recs


# ============================================================================
# Optional LLM enrichment (deterministic path is always the fallback)
# ============================================================================


def _llm_enrich(text: str, result: dict) -> dict:
    """Augment the deterministic result with LLM-suggested recommendations.

    Mirrors the LLMRouter usage in pattern_classifier.py. Returns {} on any
    failure so the deterministic result stands unchanged (air-gap safe).
    """
    try:
        from tools.llm.provider import LLMRequest  # lazy, optional dep
        from tools.llm.router import LLMRouter
    except ImportError:
        return {}

    snippet = text[:4000]
    prompt = (
        "You are assessing whether a software PRD is ready to have AI capabilities "
        "built into it. Given the requirements text below, list up to 3 concrete, "
        "high-value AI-augmentation opportunities the requirements imply, and up to "
        "3 readiness gaps to fix before build.\n\n"
        f"Deterministic score so far: {result.get('score')} "
        f"({result.get('overall_ai_readiness')}).\n\n"
        f"Requirements:\n{snippet}\n\n"
        'Respond with JSON only: {"opportunities": ["..."], "gaps": ["..."]}'
    )
    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a requirements analyst. Reply only with the JSON object.",
            agent_id="prd-readiness-assessor",
            classification="CUI",
            max_tokens=512,
            effort="low",
        )
        response = router.invoke("code_generation", request)
        raw = re.sub(r"```(?:json)?|```", "", response.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        return {}

    out: dict = {}
    if isinstance(data.get("opportunities"), list):
        out["llm_opportunities"] = [str(x) for x in data["opportunities"]][:3]
    if isinstance(data.get("gaps"), list):
        out["llm_gaps"] = [str(x) for x in data["gaps"]][:3]
    return out


# ============================================================================
# Public API
# ============================================================================


def assess_prd_for_ai_readiness(session_id: str, conn=None, use_llm: bool = False) -> dict:
    """Score a PRD's AI-readiness and infer AI-augmentation opportunities.

    Args:
        session_id: intake_sessions.id to assess.
        conn:       open DB connection (SQLite or PostgreSQL). If None, one is
                    opened via tools.db.storage.get_connection().
        use_llm:    When True, augment the deterministic result with LLM
                    suggestions; failures degrade silently to deterministic.

    Returns:
        dict — see module docstring.
    """
    close_conn = False
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            conn.set_security_context(None)  # rls-bypass: internal scorer, no request context
        except Exception:
            pass
        close_conn = True

    try:
        prd = _load_prd(session_id, conn)
        session = prd["session"]
        text = prd["text"]
        requirements = prd["requirements"]
        project_id = session.get("project_id")
        impact_level = session.get("impact_level", "IL4")
        il_level = _il_to_level(impact_level)

        # ── Score the 6 components ────────────────────────────────────────────
        gaps: list[dict] = []
        components: dict[str, dict] = {}

        ai_score, ai_det = _score_ai_mention_density(text, len(requirements))
        data_score, data_det = _score_data_specificity(text)
        integ_score, integ_det = _score_integration_clarity(text)
        ac_score, ac_det = _score_acceptance_quantifiability(requirements, text)
        gov_score, gov_det, gov_gaps = _score_governance(text, project_id, conn)
        il_score, il_det = _score_il_appropriateness(text, il_level)
        gaps.extend(gov_gaps)

        raw_components = {
            "ai_mention_density": (ai_score, ai_det),
            "data_requirement_specificity": (data_score, data_det),
            "integration_clarity": (integ_score, integ_det),
            "acceptance_criteria_quantifiability": (ac_score, ac_det),
            "governance_declarations": (gov_score, gov_det),
            "il_model_appropriateness": (il_score, il_det),
        }
        for name, (score, det) in raw_components.items():
            weight = COMPONENT_WEIGHTS[name]
            components[name] = {"score": score, "weight": weight, "detail": det}
            if score < 0.5:
                gaps.append({
                    "component": name,
                    "message": f"{name.replace('_', ' ')} is weak ({score:.0%})",
                    "remediation": f"Strengthen {name.replace('_', ' ')} in the PRD before build.",
                })

        overall = round(
            sum(c["score"] * c["weight"] for c in components.values()), 4
        )

        # ── Infer + score AI-augmentation opportunities ───────────────────────
        scan_context = {"il_level": il_level}
        if il_level == "il6" and not il_det["local_model_declared"]:
            scan_context["airgap"] = True
        opportunities = _infer_opportunities(prd, scan_context)
        rollup = roll_up_scan_verdict(opportunities)

        recommendations = _build_recommendations(components, opportunities, gaps)

        result = {
            "session_id": session_id,
            "project_id": project_id,
            "impact_level": impact_level,
            "score": overall,
            "components": components,
            "gaps": gaps,
            "opportunities": opportunities,
            "opportunity_count": len(opportunities),
            "overall_ai_readiness": rollup["overall_ai_readiness"],
            "overall_verdict": rollup["overall_verdict"],
            "overall_rationale": rollup["overall_rationale"],
            "recommendations": recommendations,
            "use_llm": bool(use_llm),
            "classification": "CUI",
        }

        if use_llm:
            enrich = _llm_enrich(text, result)
            if enrich:
                result["llm_enrichment"] = enrich
                for extra in enrich.get("llm_opportunities", []):
                    result["recommendations"].append(f"[AI-suggested] {extra}")

        return result
    finally:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass


# ============================================================================
# CLI
# ============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assess a PRD/intake session for AI-readiness and infer "
                    "AI-augmentation opportunities.",
        epilog="CUI // SP-CTI",
    )
    parser.add_argument("--session-id", required=True, help="intake_sessions.id to assess")
    parser.add_argument("--use-llm", action="store_true", help="Augment with LLM suggestions")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON output")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = assess_prd_for_ai_readiness(args.session_id, conn=None, use_llm=args.use_llm)

    if args.output_json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 60)
    print("CUI // SP-CTI")
    print("=" * 60)
    print(f"PRD AI-Readiness — session {result['session_id']}")
    print(f"  Overall score:    {result['score']:.4f}")
    print(f"  Verdict:          {result['overall_verdict']}")
    print(f"  Opportunities:    {result['opportunity_count']}")
    print()
    print("Components:")
    for name, c in result["components"].items():
        print(f"    {name:<38} {c['score']:.4f}  (w={c['weight']})")
    if result["opportunities"]:
        print()
        print("Inferred opportunities:")
        for o in result["opportunities"]:
            print(f"    [{o['ai_readiness']:<13}] {o['matched_phrase']:<32} "
                  f"{o['composite_score']:.0%}  ({o['category']})")
    print()
    print("Recommendations:")
    for r in result["recommendations"]:
        print(f"  - {r}")
    print("=" * 60)
    print("CUI // SP-CTI")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
# CUI // SP-CTI
