# CUI // SP-CTI
# ICDEV™ GovCon PM Skills Bridge — phuryn/pm-skills ↔ GovCon canvas
"""
PM Skills Bridge — AI-callable functions bridging the product_manager ACE role
with GovCon canvas tools (SWOT analysis, ICP profiling, battlecard generation).
"""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

_LLM_FUNCTION = "agent_product_manager"


# ── LLM helper ────────────────────────────────────────────────────────


def _try_llm(prompt: str) -> str | None:
    """Invoke LLMRouter for agent_product_manager. Returns content string or None."""
    try:
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest
        except ImportError:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

        router = LLMRouter()
        if not router.has_any_llm():
            return None
        req = LLMRequest(
            prompt=prompt,
            llm_function=_LLM_FUNCTION,
            max_tokens=2000,
            classification="CUI",
        )
        resp = router.invoke(_LLM_FUNCTION, req)
        return resp.content if resp else None
    except Exception as exc:
        logger.warning("LLM unavailable for %s: %s", _LLM_FUNCTION, exc)
        return None


def _get_db(conn=None):
    """Return a DB connection, creating one if conn is None."""
    if conn is not None:
        return conn
    from tools.db.storage import get_connection
    return get_connection()


def _parse_json_block(text: str) -> dict:
    """Extract the first JSON object from an LLM response string."""
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


# ── Public functions ───────────────────────────────────────────────────


def analyze_swot(context: dict, conn=None) -> dict:
    """Run SWOT analysis for a SAM.gov opportunity using the pm-skills competitive landscape step."""
    opp_id = context.get("opportunity_id", "unknown")
    agency = context.get("agency", "")
    naics = context.get("naics_code", "")
    title = context.get("title", "")
    description = context.get("description", "")[:800]

    logger.info("analyze_swot: opp_id=%s agency=%s naics=%s", opp_id, agency, naics)

    prompt = (
        f"You are a GovCon product manager applying the analyze_competitive_landscape step "
        f"(pm-skills, pm-market-research plugin) to a federal opportunity.\n\n"
        f"OPPORTUNITY: {title}\n"
        f"AGENCY: {agency}\n"
        f"NAICS: {naics}\n"
        f"DESCRIPTION: {description}\n\n"
        f"Produce a SWOT analysis in JSON with this exact schema:\n"
        f'{{"strengths": [...], "weaknesses": [...], "opportunities": [...], "threats": [...], '
        f'"win_probability_delta": <float -1.0 to 1.0>, "recommendation": "<pursue|pass|monitor>"}}\n'
        f"Return only the JSON object, no markdown."
    )

    raw = _try_llm(prompt)
    if raw:
        result = _parse_json_block(raw)
        if result:
            result["opportunity_id"] = opp_id
            return result

    logger.warning("analyze_swot: LLM unavailable, returning empty SWOT for opp_id=%s", opp_id)
    return {
        "opportunity_id": opp_id,
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
        "win_probability_delta": 0.0,
        "recommendation": "monitor",
    }


def build_icp(context: dict, conn=None) -> dict:
    """Create an Ideal Customer Profile for a GovCon agency/NAICS segment."""
    agency = context.get("agency", "")
    naics = context.get("naics_code", "")
    segment_notes = context.get("segment_notes", "")

    logger.info("build_icp: agency=%s naics=%s", agency, naics)

    prompt = (
        f"You are a GovCon product manager applying the define_positioning step "
        f"(pm-skills, pm-go-to-market plugin) to define an Ideal Customer Profile.\n\n"
        f"AGENCY: {agency}\n"
        f"NAICS CODE: {naics}\n"
        f"SEGMENT NOTES: {segment_notes}\n\n"
        f"Produce an ICP in JSON with this exact schema:\n"
        f'{{"agency": "{agency}", "naics": "{naics}", '
        f'"pains": [...], "gains": [...], '
        f'"buying_triggers": [...], "evaluation_criteria": [...], '
        f'"fit_score": <float 0.0 to 1.0>, "primary_buyer_persona": "<string>", '
        f'"typical_contract_type": "<string>", "preferred_set_aside": "<string>"}}\n'
        f"Return only the JSON object, no markdown."
    )

    raw = _try_llm(prompt)
    if raw:
        result = _parse_json_block(raw)
        if result:
            return result

    logger.warning("build_icp: LLM unavailable, returning empty ICP for agency=%s naics=%s", agency, naics)
    return {
        "agency": agency,
        "naics": naics,
        "pains": [],
        "gains": [],
        "buying_triggers": [],
        "evaluation_criteria": [],
        "fit_score": 0.0,
        "primary_buyer_persona": "",
        "typical_contract_type": "",
        "preferred_set_aside": "",
    }


def generate_battlecard(context: dict, conn=None) -> dict:
    """Generate a win-theme battlecard for a competitor in a GovCon opportunity."""
    competitor = context.get("competitor_name", "")
    opp_id = context.get("opportunity_id", "unknown")
    agency = context.get("agency", "")
    naics = context.get("naics_code", "")
    our_capabilities = context.get("our_capabilities", [])
    competitor_known_strengths = context.get("competitor_known_strengths", [])

    logger.info("generate_battlecard: competitor=%s opp_id=%s", competitor, opp_id)

    cap_list = "\n".join(f"- {c}" for c in our_capabilities[:8]) or "Not specified"
    comp_strengths = "\n".join(f"- {s}" for s in competitor_known_strengths[:5]) or "Unknown"

    prompt = (
        f"You are a GovCon product manager applying the build_battlecard step "
        f"(pm-skills, pm-go-to-market plugin) for a federal proposal competition.\n\n"
        f"OPPORTUNITY ID: {opp_id}\n"
        f"AGENCY: {agency}\n"
        f"NAICS: {naics}\n"
        f"COMPETITOR: {competitor}\n"
        f"COMPETITOR KNOWN STRENGTHS:\n{comp_strengths}\n\n"
        f"OUR CAPABILITIES:\n{cap_list}\n\n"
        f"Produce a battlecard in JSON with this exact schema:\n"
        f'{{"competitor": "{competitor}", "opportunity_id": "{opp_id}", '
        f'"win_themes": [...], "differentiators": [...], '
        f'"objection_rebuttals": [{{"objection": "...", "rebuttal": "..."}}], '
        f'"ghost_strategies": [...], "trap_questions": [...], '
        f'"confidence_score": <float 0.0 to 1.0>}}\n'
        f"Return only the JSON object, no markdown."
    )

    raw = _try_llm(prompt)
    if raw:
        result = _parse_json_block(raw)
        if result:
            return result

    logger.warning(
        "generate_battlecard: LLM unavailable, returning empty battlecard for competitor=%s opp_id=%s",
        competitor,
        opp_id,
    )
    return {
        "competitor": competitor,
        "opportunity_id": opp_id,
        "win_themes": [],
        "differentiators": [],
        "objection_rebuttals": [],
        "ghost_strategies": [],
        "trap_questions": [],
        "confidence_score": 0.0,
    }
