# CUI // SP-CTI
"""TTX Engine — LLM-based persona generation for exercise participants."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import random
from typing import Any

log = get_logger(__name__)

# Deterministic fallback persona pools by role category
_FALLBACK_PERSONAS: dict[str, list[dict]] = {
    "military_intel": [
        {"rank": "MAJ", "branch": "Army", "mos": "35D (All Source Intelligence)", "unit": "1st MI Battalion", "command": "INSCOM", "clearance": "TS/SCI", "years_service": 12},
        {"rank": "CPT", "branch": "Air Force", "mos": "14N (Intelligence Officer)", "unit": "566th ISS", "command": "ACC", "clearance": "TS/SCI", "years_service": 7},
        {"rank": "LTC", "branch": "Marine Corps", "mos": "0202 (Intelligence Officer)", "unit": "3rd Intelligence Battalion", "command": "MARFORPAC", "clearance": "TS/SCI", "years_service": 18},
    ],
    "civilian_analyst": [
        {"agency": "DIA", "grade": "GS-13", "specialty": "All-Source Analysis", "division": "Defense Intelligence Analysis Center", "clearance": "TS/SCI", "years_gov": 9},
        {"agency": "NSA", "grade": "GS-14", "specialty": "SIGINT Analysis", "division": "Threat Operations Center", "clearance": "TS/SCI", "years_gov": 11},
        {"agency": "DHS CISA", "grade": "GS-12", "specialty": "Cybersecurity Analysis", "division": "JCDC", "clearance": "Secret", "years_gov": 5},
    ],
    "contractor_swe": [
        {"company": "Booz Allen Hamilton", "contract_vehicle": "ITES-3H", "prime_sub": "Prime", "naics": "541511", "clearance": "TS/SCI", "specialty": "AI/ML Engineering", "years_exp": 8},
        {"company": "SAIC", "contract_vehicle": "GSA STARS III", "prime_sub": "Prime", "naics": "541512", "clearance": "Secret", "specialty": "Software Engineering", "years_exp": 6},
        {"company": "Leidos", "contract_vehicle": "OASIS+", "prime_sub": "Sub", "naics": "541519", "clearance": "TS/SCI", "specialty": "Data Engineering", "years_exp": 10},
    ],
    "ciso_lead": [
        {"agency": "DoD OCIO", "grade": "SES-1", "specialty": "Zero Trust Architecture", "portfolio": "Cross-DoD Cybersecurity Policy", "clearance": "TS/SCI", "years_gov": 20},
        {"company": "Palantir Federal", "title": "VP Cybersecurity", "specialty": "Cloud Security", "clearance": "TS/SCI", "years_exp": 15},
        {"agency": "Army G-6", "grade": "GS-15", "specialty": "Network Defense", "division": "Army Cyber Command", "clearance": "TS/SCI", "years_gov": 17},
    ],
}


def generate_persona(role_id: str, player_name: str, persona_data: dict | None = None) -> dict[str, Any]:
    """Generate a persona for a participant.

    Tries LLM first; falls back to deterministic pool if LLM unavailable.
    `persona_data` is the role's persona template dict from scenario YAML.
    """
    template = persona_data or {}
    try:
        return _llm_persona(role_id, player_name, template)
    except Exception as exc:
        log.debug("LLM persona gen failed (%s), using fallback", exc)
        return _deterministic_persona(role_id, player_name, template)


def _llm_persona(role_id: str, player_name: str, template: dict) -> dict[str, Any]:
    from tools.llm.router import LLMRouter, LLMRequest
    router = LLMRouter()
    if not router.has_any_llm():
        raise RuntimeError("no_llm")

    role_label = template.get("label", role_id.replace("_", " ").title())
    attributes = template.get("attributes", [])
    attr_block = ", ".join(attributes) if attributes else "rank/grade, unit/agency, specialty, clearance level, years of experience"

    user_msg = (
        f"Generate a persona for player '{player_name}' playing the role of '{role_label}'.\n"
        f"Include these attributes: {attr_block}.\n"
        "Also include: a 2-sentence role_brief explaining their current assignment and why they're in this exercise.\n"
        "Return JSON only."
    )
    req = LLMRequest(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=(
            "You generate realistic but fictional exercise personas for government tabletop exercises. "
            "Return JSON only. All fields must be plausible for a US government/defense context."
        ),
        max_tokens=400,
    )
    resp = router.invoke("persona_gen", req)
    raw = resp.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    parsed = json.loads(raw)
    parsed["role_id"] = role_id
    parsed["player_name"] = player_name
    parsed["generated_by"] = "llm"
    return parsed


def _deterministic_persona(role_id: str, player_name: str, template: dict) -> dict[str, Any]:
    pool = _FALLBACK_PERSONAS.get(role_id, [])
    if not pool:
        pool = [{"specialty": role_id.replace("_", " ").title(), "clearance": "Secret"}]
    base = random.choice(pool).copy()  # noqa: S311 — not crypto, just exercise variety
    base["role_id"] = role_id
    base["player_name"] = player_name
    base["generated_by"] = "deterministic"
    base["role_brief"] = (
        f"{player_name} is a {base.get('rank', base.get('grade', 'Senior'))} "
        f"{base.get('specialty', 'specialist')} currently assigned to "
        f"{base.get('unit', base.get('division', base.get('portfolio', 'this exercise')))}. "
        "They bring deep operational experience to the team."
    )
    return base
