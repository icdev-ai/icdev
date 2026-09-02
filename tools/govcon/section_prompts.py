"""RFI/RFP section prompt templates — loaded from config, never hardcoded.

WHY THIS MODULE EXISTS.
`rfi_workbench._SECTION_PROMPTS` was a hardcoded dict carrying the specifics of
ONE live solicitation: a full ROM cost breakdown, an IR&D cost-share figure and
its statutory authority, teaming options, TRL positioning, commerciality
percentages, a data-rights strategy and a risk register. This repository is
PUBLIC, and that content is competition-sensitive bid strategy.

The templates now live in `args/govcon/section_prompts.yaml`, which is
pursuit-NEUTRAL by policy: a template describes the SHAPE of a response section,
never the CONTENT of a particular bid.

THE OVERLAY IS THE POINT. Real pursuit content is loaded at runtime from a file
OUTSIDE this repository:

    ICDEV_GOVCON_PROMPTS_PATH=/path/outside/repo/section_prompts.yaml

The overlay is merged over the in-repo defaults per item key, so an operator
supplies only the items they are tailoring. Nothing about the overlay is
committed, and an absent overlay is the normal case, not a degraded one.

The loader NEVER raises. An unreadable or malformed overlay is reported through
`load_status()` and the in-repo defaults are used — a proposal drafting run must
not die because an operator's private file has a typo, and it must equally never
silently swap in prose nobody reviewed.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from icdev.core.paths import repo_root

_DEFAULT_RELPATH = Path("args") / "govcon" / "section_prompts.yaml"
_OVERLAY_ENV = "ICDEV_GOVCON_PROMPTS_PATH"

# Used when the packaged YAML is itself unreadable. Deliberately generic: a
# missing config must degrade to a correct generic prompt, never to a
# pursuit-specific one.
_HARDCODED_FALLBACK = (
    "Generate a professional GovCon response for the '{title}' section "
    "addressing: {question_text}. {hitl_context}"
)

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _read_yaml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return (data, error). Never raises."""
    try:
        if not path.is_file():
            return None, f"not_found: {path}"
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            return {}, None
        if not isinstance(data, dict):
            return None, f"not_a_mapping: {path}"
        return data, None
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _load() -> dict[str, Any]:
    """Load defaults, then merge a private overlay over them if one is set."""
    default_path = repo_root(__file__) / _DEFAULT_RELPATH
    base, base_err = _read_yaml(default_path)

    prompts: dict[str, str] = {}
    fallback = _HARDCODED_FALLBACK
    if base:
        raw = base.get("prompts") or {}
        if isinstance(raw, dict):
            prompts = {str(k): str(v) for k, v in raw.items() if v}
        if isinstance(base.get("default"), str) and base["default"].strip():
            fallback = base["default"]

    overlay_path_raw = os.environ.get(_OVERLAY_ENV, "").strip()
    overlay_err: str | None = None
    overlay_count = 0
    if overlay_path_raw:
        overlay, overlay_err = _read_yaml(Path(overlay_path_raw))
        if overlay:
            raw = overlay.get("prompts") or {}
            if isinstance(raw, dict):
                merged = {str(k): str(v) for k, v in raw.items() if v}
                overlay_count = len(merged)
                prompts.update(merged)
            if isinstance(overlay.get("default"), str) and overlay["default"].strip():
                fallback = overlay["default"]

    return {
        "prompts": prompts,
        "default": fallback,
        "default_path": str(default_path),
        "default_error": base_err,
        "overlay_path": overlay_path_raw or None,
        "overlay_error": overlay_err,
        "overlay_count": overlay_count,
    }


def _state() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return _cache


def reload() -> dict[str, Any]:
    """Drop the cache and re-read from disk. Returns the new load status."""
    global _cache
    with _lock:
        _cache = None
    return load_status()


def get_prompt(item_number: str) -> str:
    """Return the prompt template for an RFI item, or the generic default.

    An unknown item is NOT an error — the generic default is a correct response
    prompt. This mirrors the `.get(item, <generic>)` behaviour of the dict it
    replaces, so callers need no change.
    """
    st = _state()
    return st["prompts"].get(str(item_number), st["default"])


def known_items() -> list[str]:
    """Item numbers with a specific template, in file order."""
    return list(_state()["prompts"].keys())


def load_status() -> dict[str, Any]:
    """Where templates came from, and whether an overlay was applied.

    Surfaced so an operator can tell "no overlay configured" apart from "overlay
    configured and failed to load" — those justify opposite actions, and a
    drafting run that quietly used the generic templates when a private overlay
    was expected would produce a response nobody meant to send.
    """
    st = _state()
    return {
        "default_path": st["default_path"],
        "default_error": st["default_error"],
        "default_template_count": len(st["prompts"]) - st["overlay_count"]
        if st["overlay_count"] <= len(st["prompts"])
        else len(st["prompts"]),
        "overlay_path": st["overlay_path"],
        "overlay_error": st["overlay_error"],
        "overlay_applied": bool(st["overlay_path"]) and st["overlay_error"] is None,
        "overlay_template_count": st["overlay_count"],
        "total_templates": len(st["prompts"]),
    }
