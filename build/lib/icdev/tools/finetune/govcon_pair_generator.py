"""GovCon-specific fine-tuning pair generator.

Produces deterministic Q&A pairs from proposal sections without LLM calls.
Compatible with rag_ft_pipeline.py pair format:
  {"system_prompt": str, "user_input": str, "expected_output": str}

Usage:
  from tools.finetune.govcon_pair_generator import generate_govcon_pairs
  pairs = generate_govcon_pairs(section)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# System prompts by section type
# ---------------------------------------------------------------------------

_SYSTEM_PROMPTS = {
    "technical_approach": (
        "You are an expert federal proposal writer specializing in technical volumes. "
        "Your responses are compliant with DFARS, FAR, and agency-specific requirements. "
        "Write clearly, concisely, and with measurable outcomes."
    ),
    "management_approach": (
        "You are an expert federal proposal writer specializing in management volumes. "
        "Your responses address program governance, risk management, staffing, and delivery methodology. "
        "Always reference relevant certifications (PMP, ITIL, SAFe) and compliance frameworks."
    ),
    "past_performance": (
        "You are an expert federal proposal writer specializing in past performance volumes. "
        "Describe prior contracts with specificity: dollar value, period of performance, agency, "
        "scope, and measurable outcomes. Reference CPARS ratings where applicable."
    ),
}

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert federal proposal writer with deep knowledge of FAR/DFARS, "
    "NIST frameworks, and DoD/civilian agency acquisition requirements."
)


# ---------------------------------------------------------------------------
# Q&A templates by section type
# ---------------------------------------------------------------------------

_TECHNICAL_TEMPLATES = [
    {
        "q_template": "Write a technical approach section for a {domain} contract with {agency}.",
        "a_key": "content",
    },
    {
        "q_template": "What cloud security controls should be addressed in a technical approach "
                      "for a {domain} proposal responding to {agency}?",
        "a_key": "security_controls",
    },
    {
        "q_template": "Describe the phased implementation methodology for {domain} services at {agency}.",
        "a_key": "phases",
    },
]

_MANAGEMENT_TEMPLATES = [
    {
        "q_template": "Draft a management approach section for a {domain} contract at {agency}.",
        "a_key": "content",
    },
    {
        "q_template": "What program management controls are essential for a {domain} contract "
                      "at {agency}? Include governance, risk management, and reporting.",
        "a_key": "governance",
    },
    {
        "q_template": "How should a contractor structure their staffing plan and retention "
                      "strategy for a {domain} program serving {agency}?",
        "a_key": "staffing",
    },
]

_PP_TEMPLATES = [
    {
        "q_template": "Describe past performance demonstrating relevant experience for "
                      "a {domain} contract at {agency}.",
        "a_key": "content",
    },
    {
        "q_template": "What metrics and outcomes should be highlighted in the past performance "
                      "volume for a {domain} proposal responding to {agency}?",
        "a_key": "metrics",
    },
    {
        "q_template": "How should a contractor present CPARS ratings and contract references "
                      "for a {domain} opportunity at {agency}?",
        "a_key": "cpars",
    },
]

_TEMPLATES_BY_TYPE = {
    "technical_approach": _TECHNICAL_TEMPLATES,
    "management_approach": _MANAGEMENT_TEMPLATES,
    "past_performance": _PP_TEMPLATES,
}


# ---------------------------------------------------------------------------
# Content extractor helpers
# ---------------------------------------------------------------------------

def _extract_phases(content: str) -> str:
    """Extract phase descriptions from proposal text."""
    phases = re.findall(r"Phase\s+\d+[^:]*:[^\n]+(?:\n[^\n]+){0,3}", content)
    if phases:
        return "\n\n".join(phases[:3])
    return content[:800]


def _extract_security_controls(content: str) -> str:
    """Extract security control references from technical content."""
    # Find NIST, STIG, CMMC, FedRAMP references
    keywords = ["NIST", "STIG", "CMMC", "FedRAMP", "Zero Trust", "ATO", "POAM",
                "encryption", "authentication", "authorization", "audit", "monitoring"]
    lines = []
    for line in content.split("\n"):
        if any(kw.lower() in line.lower() for kw in keywords):
            lines.append(line.strip())
    if lines:
        return " ".join(lines[:10])
    return content[:600]


def _extract_governance(content: str) -> str:
    """Extract governance and risk management content."""
    keywords = ["governance", "PMO", "risk", "reporting", "status", "review",
                "schedule", "milestone", "CPARS", "SLA", "KPI", "metric"]
    lines = []
    for line in content.split("\n"):
        if any(kw.lower() in line.lower() for kw in keywords):
            lines.append(line.strip())
    if lines:
        return " ".join(lines[:10])
    return content[:600]


def _extract_staffing(content: str) -> str:
    """Extract staffing and workforce content."""
    keywords = ["staff", "FTE", "clearance", "personnel", "hire", "retention",
                "certif", "training", "team", "PM", "engineer"]
    lines = []
    for line in content.split("\n"):
        if any(kw.lower() in line.lower() for kw in keywords):
            lines.append(line.strip())
    if lines:
        return " ".join(lines[:10])
    return content[:600]


def _extract_metrics(content: str) -> str:
    """Extract performance metrics from past performance text."""
    # Look for percentages, dollar amounts, time metrics
    pattern = r"[^.]*(?:\d+%|\$[\d,]+[KMB]?|\d+\s+(?:days?|months?|hours?|years?))[^.]*\."
    matches = re.findall(pattern, content)
    if matches:
        return " ".join(m.strip() for m in matches[:5])
    return content[:600]


def _extract_cpars(content: str) -> str:
    """Extract CPARS rating mentions."""
    keywords = ["CPARS", "Exceptional", "Very Good", "Satisfactory", "rating",
                "evaluation", "performance"]
    lines = []
    for line in content.split("\n"):
        if any(kw.lower() in line.lower() for kw in keywords):
            lines.append(line.strip())
    if lines:
        return " ".join(lines[:8])
    return content[:600]


_ANSWER_EXTRACTORS = {
    "content": lambda c: c[:1200],
    "phases": _extract_phases,
    "security_controls": _extract_security_controls,
    "governance": _extract_governance,
    "staffing": _extract_staffing,
    "metrics": _extract_metrics,
    "cpars": _extract_cpars,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_govcon_pairs(section: Dict[str, Any]) -> List[Dict[str, str]]:
    """Generate Q&A fine-tuning pairs from a proposal section.

    Args:
        section: Dict with keys: title, description, notes (section type tag),
                 and optionally opportunity_id metadata like agency, domain.

    Returns:
        List of {"system_prompt", "user_input", "expected_output"} dicts.
    """
    section_type = (section.get("notes") or "").strip()
    content = (section.get("description") or "").strip()
    title = (section.get("title") or "").strip()

    if not content or len(content) < 100:
        return []

    # Try to infer domain/agency from title or content
    domain = _infer_domain(title + " " + content)
    agency = _infer_agency(content)

    templates = _TEMPLATES_BY_TYPE.get(section_type, _TECHNICAL_TEMPLATES)
    system_prompt = _SYSTEM_PROMPTS.get(section_type, _DEFAULT_SYSTEM_PROMPT)

    pairs = []
    for tmpl in templates:
        question = tmpl["q_template"].format(domain=domain, agency=agency)
        answer_key = tmpl["a_key"]
        extractor = _ANSWER_EXTRACTORS.get(answer_key, _ANSWER_EXTRACTORS["content"])
        answer = extractor(content).strip()

        if len(answer) < 50:
            answer = content[:800]

        pairs.append({
            "system_prompt": system_prompt,
            "user_input": question,
            "expected_output": answer,
        })

    return pairs


def _infer_domain(text: str) -> str:
    """Infer the proposal domain from text."""
    t = text.lower()
    if any(kw in t for kw in ["cloud migration", "aws govcloud", "cloud modernization"]):
        return "cloud migration and modernization"
    if any(kw in t for kw in ["cybersecurity", "penetration test", "stig", "vulnerability"]):
        return "cybersecurity assessment and hardening"
    if any(kw in t for kw in ["devsecops", "ci/cd", "pipeline", "container"]):
        return "DevSecOps platform implementation"
    if any(kw in t for kw in ["artificial intelligence", "machine learning", "ml model", "mlops"]):
        return "AI/ML platform development"
    if any(kw in t for kw in ["help desk", "service desk", "itsm", "end user"]):
        return "IT help desk and O&M support"
    return "IT services"


def _infer_agency(text: str) -> str:
    """Infer the federal agency from text."""
    agencies = [
        "Department of Defense", "Department of Homeland Security",
        "Department of Energy", "Department of Health and Human Services",
        "Department of Veterans Affairs", "General Services Administration",
        "DoD", "DHS", "DoE", "HHS", "VA", "GSA",
    ]
    for agency in agencies:
        if agency.lower() in text.lower():
            return agency
    return "the agency"
