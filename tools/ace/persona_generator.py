# CUI // SP-CTI
"""On-the-fly domain-expert persona generation for ace_persona_query.

Learns from `tools.ace.profile_generator`'s pattern (LLM-assisted spec
generation from a name + description, written for review, never silently
overwriting a built-in) but produces a different artifact: a SOUL.md
identity (read by `tools.ace.soul_manager.build_identity_preamble` for the
`ace_persona_query` one-shot Q&A path), not a coworker execution role YAML
(`args/ace/roles/*.yaml`, read by the async multi-role team launcher).
Different consumers, same LLM-assisted-generation-with-review-trail spirit.

Cross-repo callers (e.g. idea_lab) hit this whenever a domain doesn't match
any static, hand-authored persona -- rather than getting no consultation at
all, a persona is generated for that domain on first use and cached/reused
for every subsequent question in the same domain (so two ideas in the same
field converge on consistent advice instead of drifting).

Generated personas are used immediately (this is advisory-only -- a bad
answer, not a destructive action) but are clearly marked as auto-generated
and indexed in `_generated_personas.json` so a human can browse, review, or
hand-polish them later, mirroring `profile_generator`'s candidates-for-
review safety net without blocking on it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROLES_DIR = Path(__file__).parent / "roles"
_GENERATED_INDEX_PATH = _ROLES_DIR / "_generated_personas.json"


def _slug(name: str) -> str:
    """Normalize a domain label to a safe role_id slug (mirrors
    profile_generator._slug)."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:48] or "custom_specialist"


def _soul_path(role_id: str) -> Path:
    return _ROLES_DIR / role_id / "SOUL.md"


def _load_index() -> dict[str, Any]:
    if not _GENERATED_INDEX_PATH.exists():
        return {}
    try:
        return json.loads(_GENERATED_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(index: dict[str, Any]) -> None:
    _GENERATED_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GENERATED_INDEX_PATH.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_domain_label(domain_description: str) -> str:
    """Reduce free-text into a short, stable 2-4 word canonical domain
    label so differently-worded requests for the same domain (e.g.
    "blockchain" vs "crypto and web3 tech") converge on the same persona
    instead of spawning near-duplicate ones. Falls back to the raw text on
    any failure (no provider configured, network error, empty response)."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[{
                "role": "user",
                "content": (
                    "Reduce this idea/product domain description to a short, "
                    "canonical 2-4 word domain label (lowercase, no punctuation, "
                    "specific enough to distinguish this domain from adjacent "
                    "ones -- e.g. 'blockchain', 'legal tech', 'biotech "
                    "diagnostics', 'artisanal food subscription'. Never answer "
                    "with a vague word like 'general' or 'business' -- always "
                    "name the actual domain):\n\n"
                    f"{domain_description}\n\n"
                    "Return ONLY the label, nothing else."
                ),
            }],
            max_tokens=30,
            # Low, not the 1.0 default: this label is a cache key (see
            # get_or_generate_persona) -- found live it must converge on the
            # same label for similar inputs to make "cache and reuse"
            # reliable. At the default temperature the SAME domain
            # ("artisanal cheese subscription box") normalized to a
            # genuinely different, overly-generic label ("general") across
            # two separate calls, which would have spawned a low-quality
            # duplicate persona instead of reusing the good one.
            temperature=0.1,
        )
        response = router.invoke("task_decomposition", req)
        label = (getattr(response, "content", "") or "").strip().strip('."\'').lower()
        return label or domain_description
    except Exception:
        return domain_description


def _generate_soul_text(domain_label: str, domain_description: str) -> str:
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest

    router = LLMRouter()
    req = LLMRequest(
        messages=[{
            "role": "user",
            "content": (
                f"Write a SOUL.md identity file for a domain-expert advisor "
                f"persona specializing in: {domain_label} ({domain_description}).\n\n"
                "This persona answers ONE-SHOT questions from someone validating "
                "a business/product idea in this domain -- it is an advisory "
                "consultant, not a coworker that writes code.\n\n"
                "Match this exact structure and tone (Markdown, no preamble, no "
                "code fences):\n\n"
                "# <Display Name> — Identity & Values\n\n"
                "## Core Values\n"
                "- 4-6 bullets: genuine, domain-specific values an expert in this "
                "field actually holds (not generic business-advice platitudes).\n\n"
                "## Working Style\n"
                "- 3-5 bullets: how this advisor approaches a question in this domain.\n\n"
                "## Decision Heuristics\n"
                "- 4-6 bullets: concrete 'if X, ask/check Y' heuristics specific "
                "to this domain.\n\n"
                "## Communication Norms\n"
                "- 2-4 bullets: how this advisor communicates (specificity, "
                "confidence calibration, pushing back on hype).\n\n"
                "## RULES\n"
                "Anti-patterns this role must never exhibit:\n"
                "- 4-6 bullets: specific things a bad/lazy advisor in this domain "
                "would wrongly do or say.\n\n"
                "Ground every bullet in real domain substance -- nothing generic "
                "enough to paste into any other field's persona unchanged."
            ),
        }],
        max_tokens=1200,
        temperature=0.6,
    )
    response = router.invoke("task_decomposition", req)
    text = (getattr(response, "content", "") or "").strip()
    if not text:
        raise ValueError("empty SOUL.md generation response")
    return text


def get_or_generate_persona(domain_description: str) -> dict[str, Any]:
    """Return `{"role_id": str, "status": "cached"|"generated"}` for a
    domain-expert persona matching `domain_description`. Reuses an existing
    generated persona for the same normalized domain label if the SOUL.md
    still exists; otherwise generates, writes, and indexes a new one.

    Raises `ValueError` if `domain_description` is empty, or propagates any
    LLM/write failure -- the caller (`handle_ace_persona_query`) already
    wraps every call in try/except and degrades to an error response."""
    domain_description = (domain_description or "").strip()
    if not domain_description:
        raise ValueError("domain_description is required")

    domain_label = _normalize_domain_label(domain_description)
    role_id = _slug(domain_label)

    index = _load_index()
    if role_id in index and _soul_path(role_id).exists():
        return {"role_id": role_id, "status": "cached"}

    soul_text = _generate_soul_text(domain_label, domain_description)
    marker = (
        f"\n\n---\n_Auto-generated by tools.ace.persona_generator on "
        f"{datetime.now(timezone.utc).isoformat()} from domain description: "
        f"\"{domain_description}\". Not yet reviewed by a human -- verify before "
        f"relying on this for high-stakes decisions._\n"
    )
    path = _soul_path(role_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(soul_text + marker, encoding="utf-8")

    index[role_id] = {
        "domain_label": domain_label,
        "domain_description": domain_description,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_index(index)

    return {"role_id": role_id, "status": "generated"}
