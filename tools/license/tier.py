# CUI // SP-CTI
"""ECR tier gate — community / starter / professional / enterprise."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml

TIER_ORDER: List[str] = ["community", "starter", "professional", "enterprise"]

_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "canvas_registry.yaml"


def get_active_tier() -> str:
    """Return active tier from ECR_TIER env var; defaults to community."""
    tier = os.environ.get("ECR_TIER", "community").lower().strip()
    return tier if tier in TIER_ORDER else "community"


def load_canvas_registry() -> list:
    """Return the list of canvas dicts from canvas_registry.yaml."""
    try:
        data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data.get("canvases", [])
    except Exception:
        return []


def get_canvas_min_tier(canvas_name: str) -> str:
    """Return min_tier for a canvas; defaults to community if not found."""
    for canvas in load_canvas_registry():
        if canvas.get("name") == canvas_name:
            return canvas.get("min_tier", "community")
    return "community"


def is_canvas_allowed(canvas_name: str, active_tier: str | None = None) -> bool:
    """Return True if active_tier >= the canvas min_tier."""
    if active_tier is None:
        active_tier = get_active_tier()
    active_idx = TIER_ORDER.index(active_tier) if active_tier in TIER_ORDER else 0
    min_tier = get_canvas_min_tier(canvas_name)
    min_idx = TIER_ORDER.index(min_tier) if min_tier in TIER_ORDER else 0
    return active_idx >= min_idx


def get_available_features(active_tier: str | None = None) -> List[str]:
    """Return canvas names allowed at the given tier."""
    if active_tier is None:
        active_tier = get_active_tier()
    return [
        c["name"]
        for c in load_canvas_registry()
        if is_canvas_allowed(c["name"], active_tier)
    ]


def get_locked_features(active_tier: str | None = None) -> List[str]:
    """Return canvas names locked at the given tier."""
    if active_tier is None:
        active_tier = get_active_tier()
    return [
        c["name"]
        for c in load_canvas_registry()
        if not is_canvas_allowed(c["name"], active_tier)
    ]
