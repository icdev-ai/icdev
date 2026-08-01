# CUI // SP-CTI
"""Cortex domain lenses — data-driven configuration profiles over the facade.

A *domain lens* scopes the unified Cortex layer to one intelligence domain
without adding new machinery. The canonical lens is
:mod:`tools.cortex.domains.security` (the Palo Alto Cortex XSIAM analogue);
future lenses (compliance, program-management, …) are additions to the
``search.domains`` block in ``args/cortex_config.yaml`` plus, optionally, a
sibling module here for bespoke persona/triage defaults.

Each lens is described by a :class:`DomainProfile` loaded from
``search.domains.<name>`` (YAML is the source of truth; per-domain code
defaults registered in ``_DOMAIN_DEFAULTS`` fill any field the YAML omits so
the lens still works air-gapped). Consumers use three seams:

- :func:`filter_by_sources` — row-level scope: the strategy router
  (:mod:`tools.cortex.search_service`) calls this to drop hits whose source
  table/id is outside the domain's ``sources`` prefixes.
- :func:`apply_persona` — the LLM system prompt for the domain, prepended to
  whatever base prompt a caller passes (empty when no domain is active, which
  is how "switch back to general mode" restores default behavior).
- :func:`triage_summary` — the domain's answer formatter over a set of scored
  hits (XSIAM-style top-risks / blast-radius / recommended-actions for
  security).

The config is read with a lazy import (``config.load_cortex_config``) so this
package never imports the search service at module load — the router imports
:func:`filter_by_sources` from here, and a top-level back-import would cycle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

from . import document, proposal, security
from .document import DOCUMENT_DOMAIN
from .proposal import PROPOSAL_DOMAIN
from .security import (  # noqa: F401 - re-exports
    SECURITY_DOMAIN,
    security_intents,
    security_persona,
    severity_for_score,
    triage_summary,
)

# Per-domain code-side defaults, merged UNDER the YAML block (YAML wins).
_DOMAIN_DEFAULTS = {
    SECURITY_DOMAIN: security.SECURITY_DEFAULTS,
}

# Which domains own a bespoke triage formatter. Generic domains get the raw
# search results with no triage synthesis.
_TRIAGE_FORMATTERS = {
    SECURITY_DOMAIN: security.triage_summary,
    PROPOSAL_DOMAIN: proposal.triage_summary,
    DOCUMENT_DOMAIN: document.triage_summary,
}


@dataclass
class DomainProfile:
    """One Cortex domain lens: scope + persona + triage, all data-driven."""

    name: str
    display_name: str = ""
    backends: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    persona: str = ""
    intents: list = field(default_factory=list)
    triage: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _domains_config(config: Optional[dict]) -> dict:
    """Return the ``search.domains`` mapping from a cortex config.

    ``config`` is the already-loaded cortex config (the router passes its
    ``cfg``); when omitted, load it lazily via ``config.load_cortex_config``
    to avoid a module-load cycle with the search service.
    """
    if config is None:
        try:
            from ..config import load_cortex_config

            config = load_cortex_config()
        except Exception:  # noqa: BLE001 - missing/unreadable config -> empty
            config = {}
    return ((config or {}).get("search") or {}).get("domains") or {}


def load_domain_profile(
    name: str, config: Optional[dict] = None
) -> Optional[DomainProfile]:
    """Load the :class:`DomainProfile` for ``name`` (YAML over code defaults).

    Returns ``None`` when the domain is neither in the config nor a
    code-registered default — an unknown/blank domain means "no lens", which
    every consumer treats as general (default) behavior.
    """
    if not name:
        return None
    raw = _domains_config(config).get(name)
    defaults = _DOMAIN_DEFAULTS.get(name, {})
    if raw is None and not defaults:
        return None
    merged = {**defaults, **(raw or {})}
    return DomainProfile(
        name=name,
        display_name=str(merged.get("display_name") or name.title()),
        backends=list(merged.get("backends") or []),
        collections=list(merged.get("collections") or []),
        sources=list(merged.get("sources") or []),
        persona=str(merged.get("persona") or ""),
        intents=list(merged.get("intents") or []),
        triage=bool(merged.get("triage", False)),
    )


def list_domain_names(config: Optional[dict] = None) -> List[str]:
    """All known domain lens names (config block ∪ code-registered defaults)."""
    names = set(_domains_config(config)) | set(_DOMAIN_DEFAULTS)
    return sorted(names)


def domain_intents(name: str, config: Optional[dict] = None) -> List[str]:
    """Intent labels an intent router should bias toward for ``name``.

    Data-only hook for a future intent router: a lens biases classification
    toward its own intents (e.g. security -> threat_hunt / vuln_triage). An
    unknown domain returns ``[]`` (no bias).
    """
    profile = load_domain_profile(name, config)
    return list(profile.intents) if profile else []


# ---------------------------------------------------------------------------
# Row-level source scope
# ---------------------------------------------------------------------------
def _result_source_tokens(result) -> List[str]:
    """Lowercased source identifiers a scope filter can match against."""
    citation = getattr(result, "citation", None)
    meta = getattr(result, "metadata", None) or {}
    candidates = [
        getattr(citation, "source_table", "") if citation else "",
        getattr(citation, "source_id", "") if citation else "",
        getattr(citation, "source_type", "") if citation else "",
        meta.get("source_type", ""),
        meta.get("collection_id", ""),
        meta.get("entity_type", ""),
    ]
    return [str(c).lower() for c in candidates if c]


def result_in_scope(result, prefixes: Tuple[str, ...]) -> bool:
    """True when any source field of ``result`` starts with a scope prefix.

    An empty prefix set means "no scope constraint" — every result is in
    scope (general behavior).
    """
    if not prefixes:
        return True
    for token in _result_source_tokens(result):
        if token.startswith(prefixes):
            return True
    return False


def filter_by_sources(results, sources) -> Tuple[list, list]:
    """Partition ``results`` into (in-scope, dropped) by source-prefix match.

    ``sources`` is the domain's list of source table/id prefixes
    (``["pvm_", "dsoc_", ...]``). An empty/absent list passes everything
    through unchanged — scoping is opt-in per domain.
    """
    prefixes = tuple(str(s).lower() for s in (sources or []) if str(s).strip())
    if not prefixes:
        return list(results or []), []
    kept, dropped = [], []
    for r in results or []:
        (kept if result_in_scope(r, prefixes) else dropped).append(r)
    return kept, dropped


# ---------------------------------------------------------------------------
# Persona injection
# ---------------------------------------------------------------------------
def apply_persona(ctx, base_system_prompt: str = "", config: Optional[dict] = None) -> str:
    """Prepend the active domain's persona to ``base_system_prompt``.

    Returns ``base_system_prompt`` unchanged when ``ctx`` has no domain or the
    domain defines no persona — that is exactly how switching back to general
    mode restores default (personaless) behavior.
    """
    name = getattr(ctx, "domain", "") if ctx is not None else ""
    profile = load_domain_profile(name, config) if name else None
    persona = profile.persona if profile else ""
    if not persona:
        return base_system_prompt
    if base_system_prompt:
        return f"{persona}\n\n{base_system_prompt}"
    return persona


# ---------------------------------------------------------------------------
# Domain-aware answer synthesis
# ---------------------------------------------------------------------------
def summarize(results, ctx=None, query: str = "", config: Optional[dict] = None) -> Optional[dict]:
    """Domain triage summary for ``results`` when the active lens has one.

    Returns the lens's triage dict (e.g. security's XSIAM-style
    top-risks/blast-radius/recommended-actions) when ``ctx.domain`` names a
    triage-capable lens, else ``None`` (general mode — the caller keeps the
    raw ranked results). This is the "produces a triage-style summary for a
    threat query" acceptance seam.
    """
    name = getattr(ctx, "domain", "") if ctx is not None else ""
    profile = load_domain_profile(name, config) if name else None
    if not profile or not profile.triage:
        return None
    formatter = _TRIAGE_FORMATTERS.get(name)
    if formatter is None:
        return None
    return formatter(results, query=query, profile=profile)


__all__ = [
    "DomainProfile",
    "SECURITY_DOMAIN",
    "load_domain_profile",
    "list_domain_names",
    "domain_intents",
    "filter_by_sources",
    "result_in_scope",
    "apply_persona",
    "summarize",
    "triage_summary",
    "security_persona",
    "security_intents",
    "severity_for_score",
]
