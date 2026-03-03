#!/usr/bin/env python3
# CUI // SP-CTI
"""
ICDEV Phase Registry Loader
============================
Loads phase definitions from args/phase_registry.yaml and provides
filtering, summary statistics, and category metadata for the Dashboard
and SaaS Portal phase pages.

Uses PyYAML if available, falls back to a minimal YAML parser (air-gap safe).

Usage:
    from tools.dashboard.phase_loader import load_phases, get_phase_summary
    phases = load_phases()
    summary = get_phase_summary(phases)
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PHASE_REGISTRY = BASE_DIR / "args" / "phase_registry.yaml"


def _load_yaml(filepath: Path) -> dict:
    """Load a YAML file. Uses PyYAML if available, otherwise returns empty."""
    if not filepath.exists():
        return {}
    try:
        import yaml
        with open(filepath, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        # Minimal fallback — phase_registry is complex YAML, requires PyYAML
        return {}


def load_phases(registry_path: Path = None) -> list:
    """Load all phase definitions from the registry.

    Returns:
        List of phase dicts, each with keys: id, number, name, description,
        status, category, agents, goal_file, dependencies, impact_levels,
        tier_minimum.
    """
    path = registry_path or PHASE_REGISTRY
    data = _load_yaml(path)
    return data.get("phases", [])


def load_categories(registry_path: Path = None) -> dict:
    """Load category display configuration.

    Returns:
        Dict mapping category id -> {label, description, color}.
    """
    path = registry_path or PHASE_REGISTRY
    data = _load_yaml(path)
    return data.get("categories", {})


def load_statuses(registry_path: Path = None) -> dict:
    """Load status display configuration.

    Returns:
        Dict mapping status id -> {label, color, icon}.
    """
    path = registry_path or PHASE_REGISTRY
    data = _load_yaml(path)
    return data.get("statuses", {})


def get_phase_summary(phases: list) -> dict:
    """Compute summary statistics from a list of phases.

    Returns:
        Dict with keys: total, completed, active, planned, progress_pct,
        by_category (dict of category -> count).
    """
    total = len(phases)
    completed = sum(1 for p in phases if p.get("status") == "completed")
    active = sum(1 for p in phases if p.get("status") == "active")
    planned = sum(1 for p in phases if p.get("status") == "planned")
    progress_pct = round((completed / total) * 100) if total > 0 else 0

    by_category = {}
    for p in phases:
        cat = p.get("category", "unknown")
        by_category.setdefault(cat, {"total": 0, "completed": 0})
        by_category[cat]["total"] += 1
        if p.get("status") == "completed":
            by_category[cat]["completed"] += 1

    return {
        "total": total,
        "completed": completed,
        "active": active,
        "planned": planned,
        "progress_pct": progress_pct,
        "by_category": by_category,
    }


def filter_phases(phases: list, category: str = None, status: str = None,
                  impact_level: str = None, tier: str = None) -> list:
    """Filter phases by criteria.

    Args:
        phases: Full list of phase dicts.
        category: Filter by category (e.g., 'foundation', 'compliance').
        status: Filter by status ('completed', 'active', 'planned').
        impact_level: Filter by impact level (e.g., 'IL4').
        tier: Filter by minimum tier ('starter', 'professional', 'enterprise').

    Returns:
        Filtered list of phase dicts.
    """
    TIER_ORDER = {"starter": 0, "professional": 1, "enterprise": 2}
    result = phases

    if category:
        result = [p for p in result if p.get("category") == category]

    if status:
        result = [p for p in result if p.get("status") == status]

    if impact_level:
        result = [p for p in result
                  if impact_level in p.get("impact_levels", [])]

    if tier:
        tier_rank = TIER_ORDER.get(tier, 0)
        result = [p for p in result
                  if TIER_ORDER.get(p.get("tier_minimum", "starter"), 0) <= tier_rank]

    return result
