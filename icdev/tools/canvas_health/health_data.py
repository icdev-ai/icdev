# CUI // SP-CTI
"""Canvas Health Data — computes QA status for every registered canvas.

All checks are static file-existence checks; no DB queries, no subprocesses.
Safe to call on every page load.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.canvas_health.constants import (  # noqa: E402
    ISSUE_NO_BLUEPRINT,
    ISSUE_NO_E2E,
    ISSUE_NO_IQE,
    ISSUE_RLS_BYPASS,
    RED_ISSUES,
    STATUS_AMBER,
    STATUS_GREEN,
    STATUS_RED,
)


def _blueprint_exists(key: str, module: str | None) -> bool:
    """Does this canvas have a blueprint file, wherever the registry says it lives?"""
    if module:
        try:
            from tools.config.component_registry import _blueprint_path_from_module

            return _blueprint_path_from_module(module, BASE_DIR).is_file()
        except Exception:
            pass
    return (BASE_DIR / "tools" / key / "blueprint.py").exists()


def _load_registry() -> List[Dict[str, Any]]:
    try:
        import yaml
        registry_path = BASE_DIR / "args" / "component_registry.yaml"
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        return [c for c in data.get("components", []) if c.get("kind") == "canvas"]
    except Exception:
        return []


def rls_violation_keys() -> set:
    """Return set of canvas keys that have canvas_rls_bypass violations.

    Public so other surfaces (the IDP component-facts IQE adapter) score the
    same keys this dashboard shows, rather than re-deriving the mapping.
    """
    try:
        from tools.workflow.coherence_checker import check_canvas_rls_bypass
        result = check_canvas_rls_bypass()
        keys: set = set()
        for v in (result.missing or []):
            # e.g. "tools\security_canvas\db\init_db.py"
            parts = Path(v.replace("\\", "/")).parts
            if len(parts) >= 2:
                keys.add(parts[-3] if parts[-2] == "db" else parts[-2])
        return keys
    except Exception:
        return set()


# Back-compat alias for the original private name.
_rls_violations = rls_violation_keys


def get_canvas_health() -> List[Dict[str, Any]]:
    """Return one health dict per registered canvas."""
    canvases = _load_registry()
    if not canvases:
        return []

    rls_violators = _rls_violations()
    e2e_dir = BASE_DIR / "tests" / "e2e"
    iqe_adapters_dir = BASE_DIR / "tools" / "iqe" / "adapters"

    results = []
    for c in canvases:
        key = c.get("key", "")
        if not key:
            continue

        display_name = c.get("display_name", key)
        default_enabled = c.get("default_enabled", False)
        url_prefix = c.get("url_prefix") or f"/{key}"

        # File-existence checks. Resolve the blueprint from the registry `module`
        # rather than assuming tools/<key>/blueprint.py — a canvas may be served by
        # a plain module in another package (logs -> tools/logging/blueprint.py,
        # rfi_canvas -> tools/govcon/rfi_canvas_blueprint.py), and guessing reported
        # those working canvases as having no blueprint at all.
        bp_exists = _blueprint_exists(key, c.get("module"))
        e2e_exists = bool(list(e2e_dir.glob(f"{key}*.spec.ts"))) if e2e_dir.exists() else False
        iqe_exists = (iqe_adapters_dir / f"{key}.py").exists()
        rls_ok = key not in rls_violators

        # Aggregate status
        issues = []
        if not bp_exists:
            issues.append(ISSUE_NO_BLUEPRINT)
        if not e2e_exists:
            issues.append(ISSUE_NO_E2E)
        if not iqe_exists:
            issues.append(ISSUE_NO_IQE)
        if not rls_ok:
            issues.append(ISSUE_RLS_BYPASS)

        if any(i in RED_ISSUES for i in issues):
            status = STATUS_RED
        elif issues:
            status = STATUS_AMBER
        else:
            status = STATUS_GREEN

        results.append({
            "key": key,
            "display_name": display_name,
            "enabled": default_enabled,
            "route": url_prefix,
            "blueprint_exists": bp_exists,
            "e2e_exists": e2e_exists,
            "iqe_exists": iqe_exists,
            "rls_ok": rls_ok,
            "issues": issues,
            "status": status,
        })

    results.sort(key=lambda x: (x["status"] != STATUS_RED, x["status"] != STATUS_AMBER, x["key"]))
    return results
