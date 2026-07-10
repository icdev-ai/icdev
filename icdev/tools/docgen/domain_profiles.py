# CUI // SP-CTI
"""IDR Domain Profile loader.

Reads args/docgen/profiles.yaml and provides typed access to per-domain
settings: which analyzers to run, which ACE roles to assemble, which
WriteGuard mode to apply, and whether the profile is multi-domain.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml

_PROFILES_PATH = pathlib.Path(__file__).resolve().parents[2] / "args" / "docgen" / "profiles.yaml"

VALID_DOMAINS = frozenset(
    ["network", "security", "devops", "developer", "compliance", "standard_guide"]
)

# Mtime-aware cache: re-reads the file whenever it changes on disk.
_cache: dict[str, Any] = {}
_cache_mtime: float = 0.0


def _load_raw() -> dict[str, Any]:
    global _cache, _cache_mtime
    try:
        mtime = _PROFILES_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if mtime != _cache_mtime or not _cache:
        with open(_PROFILES_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        _cache = data.get("profiles", {})
        _cache_mtime = mtime
    return _cache


def get_profile(domain: str) -> dict[str, Any]:
    """Return the profile dict for *domain*. Raises KeyError if unknown."""
    profiles = _load_raw()
    if domain not in profiles:
        raise KeyError(f"Unknown IDR domain: {domain!r}. Valid: {sorted(profiles)}")
    return profiles[domain]


def list_profiles() -> list[dict[str, Any]]:
    """Return all profiles as a list with their domain key injected."""
    profiles = _load_raw()
    out = []
    for key, prof in profiles.items():
        out.append({"domain": key, **prof})
    return out


def is_multi_domain(domain: str) -> bool:
    return bool(get_profile(domain).get("multi_domain", False))


def get_domain_members(domain: str) -> list[str]:
    """For multi-domain profiles, return the list of member domains."""
    prof = get_profile(domain)
    if not prof.get("multi_domain"):
        return [domain]
    return list(prof.get("domain_members", []))


def get_ace_roles(domain: str) -> list[str]:
    return list(get_profile(domain).get("ace_roles", []))


def get_writeguard_mode(domain: str) -> str:
    return get_profile(domain).get("writeguard_mode", "runbook")


def get_writeguard_section_modes(domain: str) -> dict[str, str]:
    return dict(get_profile(domain).get("writeguard_section_modes", {}))


def get_upload_types(domain: str) -> list[str]:
    return list(get_profile(domain).get("upload_types", ["diagram", "doc", "supplement"]))


def get_example_doc_types(domain: str) -> list[str]:
    return list(get_profile(domain).get("example_doc_types", ["runbook"]))


def get_config_reviewer(domain: str) -> tuple[str | None, str | None]:
    """Return (module_path, fn_name) for the config reviewer, or (None, None)."""
    prof = get_profile(domain)
    return prof.get("config_reviewer"), prof.get("config_reviewer_fn")


def get_iac_reviewer(domain: str) -> tuple[str | None, str | None]:
    """Return (module_path, fn_name) for the IaC reviewer, or (None, None)."""
    prof = get_profile(domain)
    return prof.get("iac_reviewer"), prof.get("iac_reviewer_fn")


ATO_DOC_TYPES: dict[str, dict] = {
    "ato_ssp": {
        "roles": ["compliance_officer", "ato_author", "technical_writer"],
        "sections": [
            "System Overview", "System Boundary", "Data Flows",
            "Control Implementation", "Continuous Monitoring",
        ],
        "description": "FedRAMP System Security Plan",
    },
    "stig_checklist": {
        "roles": ["compliance_officer", "network_engineer"],
        "sections": [
            "Executive Summary", "STIG Findings", "Open Items",
            "POAM Integration", "Remediation Roadmap",
        ],
        "description": "DISA STIG Compliance Gap Report",
    },
    "poam": {
        "roles": ["compliance_officer", "ato_author"],
        "sections": [
            "Weakness Description", "Detection Source", "Scheduled Completion",
            "Responsible Party", "Resources Required",
        ],
        "description": "Plan of Action and Milestones",
    },
    "boundary_narrative": {
        "roles": ["ato_author", "network_engineer", "compliance_officer"],
        "sections": [
            "System Name and Identifier",
            "Authorization Boundary Description",
            "Network Architecture Overview",
            "Trust Zones and Segmentation",
            "External Connections and Interfaces",
            "Data Flows and Information Types",
        ],
        "description": "ATO System Boundary Description (FedRAMP SSP Section 9)",
    },
}


def get_ato_doc_type(doc_type: str | None) -> dict | None:
    """Return the ATO doc type config for *doc_type*, or None if not an ATO type."""
    if not doc_type:
        return None
    return ATO_DOC_TYPES.get(doc_type)


# ─── Item 13: Template gallery ───────────────────────────────────────────────

TEMPLATE_GALLERY: list[dict] = [
    {
        "id": "tpl-network-runbook",
        "name": "Network Runbook",
        "description": "Standard operating procedures for network infrastructure management",
        "doc_type": "runbook",
        "domain": "network",
        "query_string": (
            "Generate a comprehensive network runbook covering device roles, maintenance "
            "procedures, escalation paths, and change management protocols."
        ),
        "ace_roles": ["network_engineer", "technical_writer"],
        "icon": "net",
    },
    {
        "id": "tpl-ir-playbook",
        "name": "Incident Response Playbook",
        "description": "Step-by-step IR procedures for network security incidents",
        "doc_type": "playbook",
        "domain": "security",
        "query_string": (
            "Generate a network incident response playbook covering detection, containment, "
            "eradication, recovery, and lessons learned phases."
        ),
        "ace_roles": ["security_analyst", "technical_writer"],
        "icon": "ir",
    },
    {
        "id": "tpl-config-baseline",
        "name": "Configuration Baseline",
        "description": "Approved configuration standards for network devices",
        "doc_type": "baseline",
        "domain": "network",
        "query_string": (
            "Generate a configuration baseline document covering hardening standards, "
            "approved configurations, deviation procedures, and compliance requirements."
        ),
        "ace_roles": ["network_engineer", "compliance_officer"],
        "icon": "cfg",
    },
    {
        "id": "tpl-ato-package",
        "name": "ATO Evidence Package",
        "description": "FedRAMP/DoD ATO package with SSP, POAM, and control narratives",
        "doc_type": "ato_ssp",
        "domain": "security",
        "query_string": (
            "Generate a FedRAMP ATO evidence package covering system boundary, "
            "control implementations, POAM items, and continuous monitoring strategy."
        ),
        "ace_roles": ["compliance_officer", "ato_author", "technical_writer"],
        "icon": "ato",
    },
    {
        "id": "tpl-change-request",
        "name": "Change Request",
        "description": "Network change request with risk assessment and rollback plan",
        "doc_type": "change_request",
        "domain": "network",
        "query_string": (
            "Generate a network change request document covering change description, "
            "risk assessment, implementation steps, rollback plan, and testing criteria."
        ),
        "ace_roles": ["network_engineer", "technical_writer"],
        "icon": "cr",
    },
]


_TEMPLATES_PATH = _PROFILES_PATH.parent / "templates.yaml"
_tpl_cache: list[dict] = []
_tpl_cache_mtime: float = -1.0


def get_template_gallery() -> list[dict]:
    """Pre-built document templates — YAML-first (args/docgen/templates.yaml,
    mtime hot-reload like profiles.yaml); the Python list is the fallback
    when the file is absent or unparseable."""
    global _tpl_cache, _tpl_cache_mtime
    try:
        mtime = _TEMPLATES_PATH.stat().st_mtime
    except OSError:
        return TEMPLATE_GALLERY
    if mtime != _tpl_cache_mtime or not _tpl_cache:
        try:
            with open(_TEMPLATES_PATH, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            templates = [t for t in (data.get("templates") or []) if t.get("id")]
            if not templates:
                return TEMPLATE_GALLERY
            _tpl_cache = templates
            _tpl_cache_mtime = mtime
        except Exception:
            return TEMPLATE_GALLERY
    return _tpl_cache


def get_template(template_id: str) -> dict | None:
    """Return a single template by id, or None if not found."""
    return next((t for t in get_template_gallery() if t["id"] == template_id), None)


def resolve_all_reviewers(domain: str) -> list[dict[str, str]]:
    """Return a flat list of all (type, module, fn) reviewers for a domain.

    For multi-domain profiles, resolves reviewers across all member domains.
    Each entry: {"type": "config"|"iac"|"diagram", "module": ..., "fn": ..., "member_domain": ...}
    """
    reviewers: list[dict[str, str]] = []

    domains = get_domain_members(domain)
    for d in domains:
        prof = get_profile(d)

        # Diagram analyzer — always present
        diag_mod = prof.get("diagram_analyzer")
        diag_fn = prof.get("diagram_analyzer_fn")
        if diag_mod and diag_fn:
            reviewers.append(
                {"type": "diagram", "module": diag_mod, "fn": diag_fn, "member_domain": d}
            )

        # Config reviewer
        cfg_mod = prof.get("config_reviewer")
        cfg_fn = prof.get("config_reviewer_fn")
        if cfg_mod and cfg_fn:
            reviewers.append(
                {"type": "config", "module": cfg_mod, "fn": cfg_fn, "member_domain": d}
            )

        # IaC reviewer
        iac_mod = prof.get("iac_reviewer")
        iac_fn = prof.get("iac_reviewer_fn")
        if iac_mod and iac_fn:
            reviewers.append(
                {"type": "iac", "module": iac_mod, "fn": iac_fn, "member_domain": d}
            )

    # Deduplicate by (module, fn) to avoid running the same analyzer twice
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for r in reviewers:
        key = (r["module"], r["fn"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
