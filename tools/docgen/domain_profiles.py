# CUI // SP-CTI
"""IDR Domain Profile loader.

Reads args/docgen/profiles.yaml and provides typed access to per-domain
settings: which analyzers to run, which ACE roles to assemble, which
WriteGuard mode to apply, and whether the profile is multi-domain.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

import yaml

_PROFILES_PATH = pathlib.Path(__file__).resolve().parents[2] / "args" / "docgen" / "profiles.yaml"

VALID_DOMAINS = frozenset(
    ["network", "security", "devops", "developer", "compliance", "standard_guide"]
)


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    with open(_PROFILES_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("profiles", {})


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
