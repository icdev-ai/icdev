# CUI // SP-CTI
"""Security-intelligence domain lens — the Palo Alto Cortex XSIAM analogue.

This is a *configuration profile over existing Cortex machinery*, not new
machinery. Turning the security lens on (``CortexContext.domain="security"``)
does three things, all driven by the ``search.domains.security`` block in
``args/cortex_config.yaml`` (:mod:`tools.cortex.domains` reads it; the code
constants below are the air-gap / missing-YAML fallback only):

1. **Scope** — search is restricted to threat/vuln/incident collections and
   source tables (``pvm_*`` predictive vuln mgmt, ``dsoc_*`` threat feeds,
   ``incident_*`` tables, CVE triage outputs). The strategy router already
   intersects the backend selection with ``backends``; the row-level
   ``sources`` prefix filter (:func:`tools.cortex.domains.filter_by_sources`)
   drops non-security hits so results stay on-domain.
2. **Persona** — an LLM system prompt that makes summaries read like a SOC
   analyst: severity-first, terse, operational, evidence-cited.
3. **Triage** — an XSIAM-style triage formatter that turns a set of scored
   Cortex hits into *top risks / blast radius / recommended actions*.

Adding a second lens (compliance, program-management, …) is a YAML addition
under ``search.domains`` plus, if it needs bespoke triage/persona defaults, a
sibling module here — no change to the search or analyst code.
"""
from __future__ import annotations

from typing import List

# The domain key threaded through CortexContext.domain and the config block.
SECURITY_DOMAIN = "security"

# SOC-analyst persona: severity-first, operational, evidence-cited. Injected
# as the LLM system prompt whenever the security lens summarizes prose
# (tools.cortex.domains.apply_persona). YAML overrides this via
# search.domains.security.persona; this constant is the fallback.
DEFAULT_SECURITY_PERSONA = (
    "You are a senior Security Operations Center (SOC) analyst driving an "
    "XSIAM-style security-intelligence console. Lead with severity: rank "
    "findings critical -> high -> medium -> low and state the highest-severity "
    "item first. Be terse and operational — no filler, no hedging. Cite the "
    "source table/id every finding came from. Structure any summary as: "
    "(1) Top risks (severity-ranked), (2) Blast radius / affected assets, "
    "(3) Recommended actions. Never speculate beyond the retrieved evidence; "
    "if the data does not support a conclusion, say so explicitly."
)

# Row-level source scope: a Cortex hit is on-domain when one of its source
# fields starts with one of these (case-insensitive) prefixes. Mirrors the
# collections but expressed as table/id prefixes so the filter matches the
# backing rows (pvm_predictions, dsoc_threats, incident_reports, CVE-2024-…).
DEFAULT_SECURITY_SOURCES = [
    "pvm_",
    "dsoc_",
    "incident_",
    "cve",
    "threat",
    "vuln",
    "security",
]

# Intent labels a future intent router should bias toward when the security
# lens is active (data-only hook — tools.cortex.domains.domain_intents).
DEFAULT_SECURITY_INTENTS = [
    "threat_hunt",
    "vuln_triage",
    "incident_response",
    "blast_radius",
]

# Code-side fallback for the whole profile. YAML (search.domains.security) is
# the source of truth and wins field-by-field; anything the YAML omits falls
# back here so the lens still works air-gapped or with a stripped config.
SECURITY_DEFAULTS = {
    "display_name": "Security Intelligence",
    "backends": ["rag", "graph", "kb"],
    "collections": [
        "pvm_predictions",
        "dsoc_threats",
        "incident_reports",
        "cve_triage",
    ],
    "sources": list(DEFAULT_SECURITY_SOURCES),
    "persona": DEFAULT_SECURITY_PERSONA,
    "intents": list(DEFAULT_SECURITY_INTENTS),
    "triage": True,
}


# ---------------------------------------------------------------------------
# Severity model (XSIAM-style bands over the normalized [0,1] Cortex score)
# ---------------------------------------------------------------------------
SEVERITY_BANDS = (
    (0.8, "critical"),
    (0.6, "high"),
    (0.4, "medium"),
    (0.0, "low"),
)


def severity_for_score(score: float) -> str:
    """Map a normalized [0,1] Cortex score onto an XSIAM severity band."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 0.0
    for threshold, label in SEVERITY_BANDS:
        if value >= threshold:
            return label
    return "low"


# Source-prefix -> recommended operational action. Deterministic so triage
# stays grounded-by-construction (no LLM). The security triage tools under
# tools/security/ and tools/supply_chain/ can enrich these downstream; this
# mapping is the always-available baseline.
_ACTION_BY_SOURCE_PREFIX = [
    ("pvm_", "Prioritize patching for predicted-risk vulnerabilities (PVM)."),
    ("dsoc_", "Review active threat-feed indicators; consider RTBH / flowspec mitigation (DSOC)."),
    ("incident_", "Escalate open incidents to the IR on-call and confirm containment status."),
    ("cve", "Confirm CVE patch status and map affected assets before exposure widens."),
    ("threat", "Correlate threat indicators against current telemetry for active exploitation."),
    ("vuln", "Run a targeted vulnerability re-scan on the affected surface."),
]


def _source_of(result) -> str:
    """Best available source identifier for a Cortex hit."""
    citation = getattr(result, "citation", None)
    if citation is not None:
        return citation.source_table or citation.source_type or ""
    return ""


def _recommended_actions(results) -> List[str]:
    """Derive de-duplicated operational actions from the matched sources."""
    tokens = []
    for r in results:
        citation = getattr(r, "citation", None)
        meta = getattr(r, "metadata", None) or {}
        for field in (
            _source_of(r),
            getattr(citation, "source_id", "") if citation else "",
            meta.get("source_type", ""),
            meta.get("collection_id", ""),
        ):
            if field:
                tokens.append(str(field).lower())
    actions: List[str] = []
    for prefix, action in _ACTION_BY_SOURCE_PREFIX:
        if any(tok.startswith(prefix) for tok in tokens) and action not in actions:
            actions.append(action)
    if not actions:
        actions.append(
            "No security-scoped evidence retrieved — widen the query or verify "
            "source coverage before drawing conclusions."
        )
    return actions


def _blast_radius(results) -> dict:
    """Affected-asset footprint across the retrieved hits.

    Deterministic, grounded-by-construction: distinct source tables touched
    and distinct entities named by graph-backend hits (the relationship layer
    is where blast radius actually lives).
    """
    source_tables = []
    entities = []
    for r in results:
        table = _source_of(r)
        if table and table not in source_tables:
            source_tables.append(table)
        meta = getattr(r, "metadata", None) or {}
        if r.backend == "graph":
            label = getattr(getattr(r, "citation", None), "title", "") or ""
            entity_type = meta.get("entity_type", "")
            entity = f"{label} ({entity_type})" if entity_type else label
            if entity and entity not in entities:
                entities.append(entity)
    return {
        "affected_sources": source_tables,
        "affected_entities": entities,
        "source_count": len(source_tables),
        "entity_count": len(entities),
    }


def _format_triage_text(
    query: str, risks: List[dict], blast_radius: dict, actions: List[str]
) -> str:
    """Human-readable XSIAM-style triage brief."""
    lines = [f"Security triage for: {query!r}" if query else "Security triage"]
    if not risks:
        lines.append("No security-scoped findings.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Top risks (severity-ranked):")
    for r in risks:
        lines.append(
            f"  {r['rank']}. [{r['severity'].upper()}] {r['title']} "
            f"(score {r['score']}, source {r['source']})"
        )
    entities = blast_radius.get("affected_entities") or []
    lines.append("")
    lines.append(
        "Blast radius: "
        f"{blast_radius.get('source_count', 0)} source table(s), "
        f"{blast_radius.get('entity_count', 0)} entity(ies)"
        + (f" — {', '.join(entities[:5])}" if entities else "")
    )
    lines.append("")
    lines.append("Recommended actions:")
    for i, action in enumerate(actions, 1):
        lines.append(f"  {i}. {action}")
    return "\n".join(lines)


def triage_summary(
    results,
    query: str = "",
    profile=None,
    top_n: int = 5,
) -> dict:
    """XSIAM-style triage summary over a set of scored Cortex search hits.

    Turns the normalized results into ``top_risks`` (severity-ranked),
    ``blast_radius`` (affected sources/entities), and ``recommended_actions``,
    plus a rendered ``text`` brief. Fully deterministic and grounded by
    construction — every field derives from real retrieved rows, so it carries
    no hallucination risk. ``profile`` is accepted for symmetry / future
    tuning but is not required.
    """
    ranked = sorted(results or [], key=lambda r: r.score, reverse=True)
    top = ranked[: max(0, int(top_n))]
    risks = []
    for i, r in enumerate(top, 1):
        citation = getattr(r, "citation", None)
        title = (
            (citation.title if citation else "")
            or (r.content[:80] if r.content else "")
            or "(untitled)"
        )
        risks.append(
            {
                "rank": i,
                "title": title,
                "severity": severity_for_score(r.score),
                "score": round(float(r.score), 3),
                "backend": r.backend,
                "source": _source_of(r) or r.backend,
                "source_id": citation.source_id if citation else "",
                "snippet": (r.content or "")[:200],
            }
        )
    blast_radius = _blast_radius(ranked)
    actions = _recommended_actions(ranked)
    return {
        "domain": SECURITY_DOMAIN,
        "query": query,
        "result_count": len(ranked),
        "top_risks": risks,
        "blast_radius": blast_radius,
        "recommended_actions": actions,
        "text": _format_triage_text(query, risks, blast_radius, actions),
    }


def security_persona(profile=None) -> str:
    """The SOC-analyst system prompt, from the profile or the code fallback."""
    if profile is not None and getattr(profile, "persona", ""):
        return profile.persona
    return DEFAULT_SECURITY_PERSONA


def security_intents(profile=None) -> List[str]:
    """Intent labels the security lens biases toward (profile or fallback)."""
    if profile is not None and getattr(profile, "intents", None):
        return list(profile.intents)
    return list(DEFAULT_SECURITY_INTENTS)


def default_profile_dict() -> dict:
    """A copy of the code-side security profile fallback."""
    data = dict(SECURITY_DEFAULTS)
    data["sources"] = list(data["sources"])
    data["backends"] = list(data["backends"])
    data["collections"] = list(data["collections"])
    data["intents"] = list(data["intents"])
    return data


__all__ = [
    "SECURITY_DOMAIN",
    "SECURITY_DEFAULTS",
    "DEFAULT_SECURITY_PERSONA",
    "DEFAULT_SECURITY_SOURCES",
    "DEFAULT_SECURITY_INTENTS",
    "SEVERITY_BANDS",
    "severity_for_score",
    "triage_summary",
    "security_persona",
    "security_intents",
    "default_profile_dict",
]
