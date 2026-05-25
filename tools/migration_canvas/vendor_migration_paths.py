# CUI // SP-CTI
"""Vendor-to-vendor network device migration path library.

Loads path definitions from args/vendor_migration_paths.yaml.
No LLM required — fully deterministic.

Public functions:
    list_all_paths()                                      → list[dict]
    get_migration_path(src_vendor, src_family, tgt_vendor) → dict | None
    list_compatible_targets(src_vendor, src_family)       → list[dict]
    get_migration_checklist(path_id)                      → list[dict]
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from pathlib import Path
from typing import Any

logger = get_logger("icdev.migration_canvas.vendor_migration_paths")

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_PATHS_YAML = _ICDEV_ROOT / "args" / "vendor_migration_paths.yaml"

_CACHE: list[dict] | None = None


def _load_paths() -> list[dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    try:
        import yaml
        with open(_PATHS_YAML, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _CACHE = data.get("paths", [])
    except ImportError:
        # Fallback: minimal YAML parser for simple list of dicts
        _CACHE = _parse_paths_minimal()
    except Exception as exc:
        logger.warning("vendor_migration_paths.yaml load failed: %s", exc)
        _CACHE = []

    return _CACHE


def _parse_paths_minimal() -> list[dict]:
    """Minimal YAML list-of-dicts parser (no PyYAML dependency)."""
    entries: list[dict] = []
    current: dict[str, Any] = {}
    list_key: str | None = None
    list_buf: list = []

    try:
        with open(_PATHS_YAML, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip()
                indent = len(line) - len(line.lstrip())

                if stripped.strip().startswith("- id:"):
                    if list_key and list_buf:
                        current[list_key] = list_buf
                        list_key = None
                        list_buf = []
                    if current and current.get("id"):
                        entries.append(current)
                    current = {"id": stripped.split("id:")[-1].strip().strip('"').strip("'")}
                    continue

                if not current:
                    continue

                stripped_content = stripped.strip()
                if stripped_content.startswith("- name:") and indent >= 4:
                    # Start of a phase/sub-list item
                    if list_key:
                        list_buf.append({"name": stripped_content.split("name:")[-1].strip().strip('"')})
                    continue

                if ":" in stripped_content and not stripped_content.startswith("-"):
                    key, _, val = stripped_content.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key in ("phases", "protocol_notes", "gotchas"):
                        if list_key and list_buf:
                            current[list_key] = list_buf
                        list_key = key
                        list_buf = []
                    elif val:
                        current[key] = val

        if list_key and list_buf:
            current[list_key] = list_buf
        if current and current.get("id"):
            entries.append(current)
    except Exception as exc:
        logger.warning("Minimal YAML parse failed: %s", exc)

    return entries


def _normalize_vendor(v: str) -> str:
    return (v or "").lower().strip()


# ── Public API ────────────────────────────────────────────────────────────────

def list_all_paths() -> list[dict]:
    """Return all migration paths from the library."""
    return _load_paths()


def get_migration_path(
    source_vendor: str,
    source_family: str,
    target_vendor: str,
) -> dict | None:
    """Find a migration path matching source→target.

    Matching is case-insensitive substring on vendor and family.
    Returns the first match or None.
    """
    sv = _normalize_vendor(source_vendor)
    sf = source_family.lower().strip()
    tv = _normalize_vendor(target_vendor)

    for path in _load_paths():
        pv = _normalize_vendor(path.get("source_vendor", ""))
        pf = path.get("source_family", "").lower().strip()
        pt = _normalize_vendor(path.get("target_vendor", ""))

        sv_match = sv in pv or pv in sv
        sf_match = sf in pf or pf in sf
        tv_match = tv in pt or pt in tv

        if sv_match and sf_match and tv_match:
            return path

    return None


def list_compatible_targets(source_vendor: str, source_family: str) -> list[dict]:
    """Return all known migration targets for a given source device.

    Each result includes: target_vendor, target_family, migration_type,
    complexity, estimated_hours, path_id.
    """
    sv = _normalize_vendor(source_vendor)
    sf = source_family.lower().strip()
    results = []

    for path in _load_paths():
        pv = _normalize_vendor(path.get("source_vendor", ""))
        pf = path.get("source_family", "").lower().strip()
        if (sv in pv or pv in sv) and (sf in pf or pf in sf):
            results.append({
                "path_id": path.get("id"),
                "target_vendor": path.get("target_vendor"),
                "target_family": path.get("target_family"),
                "migration_type": path.get("migration_type"),
                "complexity": path.get("complexity"),
                "estimated_hours": path.get("estimated_hours"),
            })

    return results


def get_migration_checklist(path_id: str) -> list[dict]:
    """Return ordered phase checklist for a migration path.

    Returns list of {order, name, description} dicts.
    """
    for path in _load_paths():
        if path.get("id") == path_id:
            phases = path.get("phases", [])
            if not phases:
                return []
            return sorted(phases, key=lambda p: p.get("order", 0))
    return []


def get_gotchas(path_id: str) -> list[str]:
    """Return list of gotcha strings for a migration path."""
    for path in _load_paths():
        if path.get("id") == path_id:
            return path.get("gotchas", [])
    return []


def get_protocol_notes(path_id: str) -> list[str]:
    """Return protocol migration notes for a path."""
    for path in _load_paths():
        if path.get("id") == path_id:
            return path.get("protocol_notes", [])
    return []
