# CUI // SP-CTI
"""A registry-declared component must not also be hardcode-registered in app.py.

`args/component_registry.yaml` is the single source for canvas blueprint
registration; `tools/dashboard/app.py` walks it and registers each enabled
component. A leftover hand-rolled ``from <module> import <blueprint_attr>`` +
``app.register_blueprint(...)`` for the same component registers a *second*
blueprint under a name Flask already holds, so every startup logged:

    WARNING in app: Logs blueprint failed to register: The name 'logs' is
    already registered for a different blueprint.

The route still worked — the registry had already registered it — so the
duplicate was pure dead code that emitted a warning on every boot and trained
readers to ignore blueprint warnings. Worse, the hardcoded path bypasses the
registry's enablement and IL gating, so it would resurrect a component the
registry had deliberately switched off.

Codifies the CLAUDE.md guardrail: "Adding or changing a canvas / child app /
feature: update args/component_registry.yaml first. Do not add new Python lists
in tools/dashboard/app.py."
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "args" / "component_registry.yaml"

APP_FILES = [
    REPO_ROOT / "tools" / "dashboard" / "app.py",
    REPO_ROOT / "icdev" / "tools" / "dashboard" / "app.py",
]


def _components() -> list[dict]:
    raw = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    comps = raw if isinstance(raw, list) else raw.get("components", raw)
    return [c for c in comps if isinstance(c, dict)]


def _duplicate_imports(app_src: str) -> list[tuple[str, int]]:
    """(component key, 1-indexed line) for each registry module imported directly.

    Matched on the component's dotted `module` AND its `blueprint_attr` together.
    `blueprint_attr` alone is too loose to key on — several components use the
    generic name "bp", which collides with ordinary local variables.
    """
    found: list[tuple[str, int]] = []
    for comp in _components():
        module, attr = comp.get("module"), comp.get("blueprint_attr")
        if not module or not attr:
            continue
        pattern = re.compile(
            rf"^\s*from\s+{re.escape(module)}\s+import\s+.*\b{re.escape(attr)}\b",
            re.MULTILINE,
        )
        for match in pattern.finditer(app_src):
            found.append((comp.get("key", module), app_src[: match.start()].count("\n") + 1))
    return found


@pytest.mark.parametrize("app_file", APP_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_registry_component_is_hardcode_registered(app_file: Path):
    if not app_file.exists():  # mirror may be absent in a trimmed checkout
        pytest.skip(f"{app_file} not present")

    duplicates = _duplicate_imports(app_file.read_text(encoding="utf-8"))

    assert not duplicates, (
        f"{app_file.relative_to(REPO_ROOT)} hand-registers components that "
        f"args/component_registry.yaml already registers: "
        + ", ".join(f"{key} (line {line})" for key, line in duplicates)
        + ". Flask rejects the second registration under the same name, so this is "
        "dead code that warns on every boot. Delete the block; the registry loop "
        "already covers it."
    )


def test_logs_is_registry_declared():
    """Guard the premise: if `logs` ever leaves the registry, the block above is
    no longer redundant and this suite must fail loudly rather than pass vacuously."""
    keys = {c.get("key") for c in _components()}
    assert "logs" in keys, (
        "the 'logs' canvas is no longer declared in component_registry.yaml — "
        "dashboard registration for it must be re-established somewhere"
    )
