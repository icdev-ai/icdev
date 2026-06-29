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


def _load_registry() -> List[Dict[str, Any]]:
    try:
        import yaml
        registry_path = BASE_DIR / "args" / "component_registry.yaml"
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        return [c for c in data.get("components", []) if c.get("kind") == "canvas"]
    except Exception:
        return []


def _rls_violations() -> set:
    """Return set of canvas keys that have canvas_rls_bypass violations."""
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

        # File-existence checks
        bp_exists = (BASE_DIR / "tools" / key / "blueprint.py").exists()
        e2e_exists = bool(list(e2e_dir.glob(f"{key}*.spec.ts"))) if e2e_dir.exists() else False
        iqe_exists = (iqe_adapters_dir / f"{key}.py").exists()
        rls_ok = key not in rls_violators

        # Aggregate status
        issues = []
        if not bp_exists:
            issues.append("no blueprint")
        if not e2e_exists:
            issues.append("no E2E spec")
        if not iqe_exists:
            issues.append("no IQE adapter")
        if not rls_ok:
            issues.append("RLS bypass needed")

        if not rls_ok:
            status = "red"
        elif issues:
            status = "amber"
        else:
            status = "green"

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

    results.sort(key=lambda x: (x["status"] != "red", x["status"] != "amber", x["key"]))
    return results
