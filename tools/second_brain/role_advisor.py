# CUI // SP-CTI
"""Role inference and canvas affinity resolution for the Second Brain proactive advisor.

Reads args/role_canvas_affinity.yaml — zero hardcoded role logic in Python.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_AFFINITY_PATH = Path(__file__).resolve().parents[2] / "args" / "role_canvas_affinity.yaml"


@lru_cache(maxsize=1)
def _load_affinity() -> dict[str, Any]:
    try:
        return yaml.safe_load(_AFFINITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("[role_advisor] could not load role_canvas_affinity.yaml: %s", exc)
        return {}


def infer_persona(title: str) -> dict[str, Any]:
    """Match a free-form job title to the best-fit persona entry.

    Returns the full persona dict from role_canvas_affinity.yaml, or the
    fallback_persona entry if nothing matches.
    """
    cfg = _load_affinity()
    affinities: list[dict] = cfg.get("role_affinities", [])
    fallback: dict = cfg.get("fallback_persona", {"persona": "general", "canvases": [], "digest_topics": [], "review_dimensions": {}})

    if not title:
        return fallback

    title_lower = title.lower()
    for entry in affinities:
        for kw in entry.get("title_keywords", []):
            # Word-boundary match so "engineer" doesn't match "sales engineer" persona
            if re.search(rf"\b{re.escape(kw.lower())}\b", title_lower):
                return entry

    return fallback


def get_relevant_canvases(user_id: str, tenant_id: str = "default") -> list[str]:
    """Return ordered list of canvas keys relevant to the user's role."""
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)
    return persona.get("canvases", [])


def get_digest_topics(user_id: str, tenant_id: str = "default") -> list[str]:
    """Return intelligence feed topics for the weekly digest, role-adjusted."""
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)

    # Base topics from role
    topics: list[str] = list(persona.get("digest_topics", []))

    # Augment with customer-type topics from the affinity map
    # (e.g. an architect serving CSPs gets carrier-grade topics on top)
    customer_topics = _customer_type_topics(user_id, tenant_id)
    for t in customer_topics:
        if t not in topics:
            topics.append(t)

    return topics[:12]


def get_stall_signal_canvases(user_id: str, tenant_id: str = "default") -> list[str]:
    """Return canvas keys whose inactivity signals stalled work for this role."""
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)
    return persona.get("stall_signal_canvases", [])


def get_review_dimensions(user_id: str, tenant_id: str = "default") -> dict[str, Any]:
    """Return review dimension definitions for the user's role."""
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)
    return persona.get("review_dimensions", {})


def build_role_aware_review(
    design_context: dict[str, Any],
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Run role-appropriate review dimensions against *design_context*.

    For each dimension, checks which artifact_checks are absent from
    design_context and surfaces them as findings, weighted by severity.

    Returns findings list — same schema as customer_aware_review() so callers
    can merge both result sets.
    """
    dimensions = get_review_dimensions(user_id, tenant_id)
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)
    persona_label = persona.get("display_name", "this role")

    findings: list[dict[str, Any]] = []

    for dim_key, dim in dimensions.items():
        weight = dim.get("weight", 1)
        checks = dim.get("artifact_checks", [])
        missing = [c for c in checks if not design_context.get(c)]

        if not missing:
            continue  # all checks present — dimension passes

        severity = "high" if weight >= 3 else "medium"
        findings.append({
            "dimension": dim_key,
            "description": dim.get("description", ""),
            "severity": severity,
            "finding": f"Missing {len(missing)} of {len(checks)} required fields: {', '.join(missing[:4])}{'...' if len(missing) > 4 else ''}.",
            "recommendation": (
                f"As a {persona_label}, the '{dim_key}' dimension is {'critical' if weight >= 3 else 'important'}. "
                f"Add these fields to your design artifact before submitting for review: {', '.join(missing[:5])}."
            ),
            "customer": None,    # role-level finding, not customer-specific
            "customer_type": None,
        })

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_user_title(user_id: str, tenant_id: str) -> str:
    try:
        from tools.second_brain.profile import get_profile
        profile = get_profile(user_id, tenant_id) or {}
        return profile.get("title", "") or ""
    except Exception:
        return ""


# Map customer types to extra digest topics (complements the role topics)
_CUSTOMER_TYPE_TOPIC_BOOST: dict[str, list[str]] = {
    "customer": ["carrier infrastructure", "CSP/ISP technology trends", "enterprise client SLAs"],
    "stakeholder": ["internal platform adoption", "cross-team alignment", "DevOps culture"],
    "boss": ["executive technology briefing", "strategic IT roadmap", "technology investment ROI"],
    "direct": ["team upskilling", "engineering productivity", "mentorship in tech"],
    "vendor": ["vendor evaluation", "contract compliance", "partner ecosystem"],
}


def _customer_type_topics(user_id: str, tenant_id: str) -> list[str]:
    try:
        from tools.second_brain.profile import get_relationships
        rels = get_relationships(user_id, tenant_id) or []
        topics: list[str] = []
        seen_types: set[str] = set()
        for r in rels:
            ctype = r.get("relationship_type", "")
            if ctype and ctype not in seen_types:
                seen_types.add(ctype)
                topics.extend(_CUSTOMER_TYPE_TOPIC_BOOST.get(ctype, [])[:2])
        return topics
    except Exception:
        return []
