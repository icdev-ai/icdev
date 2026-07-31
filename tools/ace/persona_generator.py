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
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

def _roles_dir() -> Path:
    """Resolve the roles directory the same way ``soul_manager`` does.

    Two reasons this is a function rather than the old module-level
    ``Path(__file__).parent / "roles"``:

    1. **Mirror drift.** SOUL directories exist under BOTH ``tools/ace/roles/``
       and ``icdev/tools/ace/roles/`` and are byte-identical today. Resolving
       from ``__file__`` wrote to whichever copy happened to be imported, so the
       first generated persona would silently desynchronise the two trees — and
       mirror parity is a CI gate.
    2. **Test isolation.** ``soul_manager._roles_dir()`` honours
       ``ICDEV_ACE_ROLES_DIR``; resolving from ``__file__`` ignored it, so
       generated personas accumulated as debris in the committed tree during
       test runs.

    Falls back to the package-local directory if soul_manager is unavailable.
    """
    try:
        from icdev.tools.ace.soul_manager import _roles_dir as _sm_roles_dir

        return Path(_sm_roles_dir())
    except Exception:  # noqa: BLE001 — keep generation working standalone
        override = os.environ.get("ICDEV_ACE_ROLES_DIR")
        if override:
            return Path(override)
        return Path(__file__).resolve().parent / "roles"


def _generated_index_path() -> Path:
    return _roles_dir() / "_generated_personas.json"


#: Retained for backward compatibility — some callers and tests read these
#: module attributes directly. Prefer the functions above in new code.
_ROLES_DIR = Path(__file__).resolve().parent / "roles"
_GENERATED_INDEX_PATH = _ROLES_DIR / "_generated_personas.json"


def _slug(name: str) -> str:
    """Normalize a domain label to a safe role_id slug (mirrors
    profile_generator._slug)."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:48] or "custom_specialist"


def _soul_path(role_id: str) -> Path:
    return _roles_dir() / role_id / "SOUL.md"


def _mirror_roles_dirs() -> list[Path]:
    """Every roles directory a generated artifact must be written to.

    Normally just one. When the resolved directory is inside one half of the
    ``tools/`` ↔ ``icdev/tools/`` mirror and the other half also exists on disk,
    both are returned so a generated persona does not desynchronise the trees.
    Returns an empty-safe, de-duplicated list.
    """
    primary = _roles_dir().resolve()
    dirs = [primary]

    text = primary.as_posix()
    for a, b in (("/icdev/tools/", "/tools/"), ("/tools/", "/icdev/tools/")):
        if a in text:
            twin = Path(text.replace(a, b, 1))
            # Only mirror into a tree that already exists — never conjure one.
            if twin != primary and twin.parent.exists():
                dirs.append(twin)
            break

    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = d.as_posix().lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _load_index() -> dict[str, Any]:
    path = _generated_index_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(index: dict[str, Any]) -> None:
    payload = json.dumps(index, indent=2, sort_keys=True)
    for d in _mirror_roles_dirs():
        target = d / "_generated_personas.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — a mirror write must not fail generation
            logger.warning("persona_generator: index mirror write failed at %s: %s", target, exc)


def _normalize_domain_label(domain_description: str) -> str:
    """Reduce free-text into a short, stable 2-4 word canonical domain
    label so differently-worded requests for the same domain (e.g.
    "blockchain" vs "crypto and web3 tech") converge on the same persona
    instead of spawning near-duplicate ones. Falls back to the raw text on
    any failure (no provider configured, network error, empty response)."""
    try:
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.provider import LLMRequest

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
    from icdev.tools.llm.router import LLMRouter
    from icdev.tools.llm.provider import LLMRequest

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
    content = soul_text + marker
    for d in _mirror_roles_dirs():
        target = d / role_id / "SOUL.md"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("persona_generator: SOUL mirror write failed at %s: %s", target, exc)

    index[role_id] = {
        "domain_label": domain_label,
        "domain_description": domain_description,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_index(index)

    return {"role_id": role_id, "status": "generated"}
