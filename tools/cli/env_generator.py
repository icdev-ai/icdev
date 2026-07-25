#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate a complete, commented .env component section from the registry.

The reported install bug: a user could not find /techwriter or /docdrift
because nothing anywhere connected those URLs to ICDEV_DIC_ENABLED, and the
canvas was default-OFF with no signal it even existed. The static .env template
only listed a hand-curated subset of flags.

This module renders EVERY component env flag declared in
``args/component_registry.yaml`` — enabled ones live, the rest present and
commented with their display name, description, impact level and URL prefix —
grouped by kind (canvas / feature / core_extension / child_app). `icdev init`
composes the generated section onto the non-component keys (API keys, DB
settings, ports) from the shipped ``.env.template`` so the user's .env is
complete and self-documenting.

Pure stdlib + PyYAML (already a dependency). No network. Air-gap safe.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from tools.config.component_registry import ComponentRegistry

# Order + human labels for the grouped section.
KIND_ORDER: list[str] = ["canvas", "feature", "core_extension", "child_app"]
KIND_LABELS: dict[str, str] = {
    "canvas": "CANVASES",
    "feature": "FEATURES",
    "core_extension": "CORE EXTENSIONS",
    "child_app": "CHILD APPS",
}

_BANNER = "COMPONENT ENABLEMENT — generated from args/component_registry.yaml"
_SECTION_MARK = "# === ICDEV COMPONENTS (generated) ==="

_ENV_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")


def component_env_flags(registry: "ComponentRegistry") -> set[str]:
    """Every env flag any component owns (primary + legacy alias flags)."""
    flags: set[str] = set()
    for c in registry.list_all():
        if c.env_flag:
            flags.add(c.env_flag)
        flags.update(c.extra_env_flags)
    return flags


def _wrap_comment(text: str, width: int = 74) -> list[str]:
    """Wrap a description into `# ` comment lines (no external deps)."""
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = "#   "
    for w in words:
        if len(cur) + len(w) + 1 > width and cur.strip() != "#":
            lines.append(cur.rstrip())
            cur = "#   " + w
        else:
            cur += (" " if cur != "#   " else "") + w
    if cur.strip() != "#":
        lines.append(cur.rstrip())
    return lines


def render_component_section(registry: "ComponentRegistry") -> str:
    """Return the grouped, commented component-flag block for a .env file."""
    out: list[str] = [
        "# " + "=" * 74,
        f"# {_BANNER}",
        "# " + "=" * 74,
        "# All component flags below are derived from the registry — the single",
        "# source of truth. Toggle with `icdev enable/disable <name>`, the",
        "# `icdev setup` TUI, or by editing the value here. Default-OFF flags are",
        "# listed so nothing is hidden: if a page/canvas is missing from the menu",
        "# it is simply default-OFF — flip its flag to true.",
        _SECTION_MARK,
    ]

    for kind in KIND_ORDER:
        comps = sorted(
            (c for c in registry.list_all() if c.kind == kind and c.env_flag),
            key=lambda c: c.key,
        )
        if not comps:
            continue
        enabled = sum(1 for c in comps if c.default_enabled)
        out.append("")
        out.append("# " + "-" * 74)
        out.append(f"# {KIND_LABELS.get(kind, kind.upper())}"
                   f"  ({enabled} enabled / {len(comps)} total by default)")
        out.append("# " + "-" * 74)
        for c in comps:
            out.append("")
            headline = f"# {c.display_name or c.key}"
            meta: list[str] = []
            if c.url_prefix:
                meta.append(f"url: {c.url_prefix}")
            if c.min_il:
                meta.append(f"min_il: {c.min_il}")
            if meta:
                headline += "  [" + ", ".join(meta) + "]"
            out.append(headline)
            out.extend(_wrap_comment(c.description))
            if c.extra_env_flags:
                out.append("#   (also flips: " + ", ".join(c.extra_env_flags) + ")")
            val = "true" if c.default_enabled else "false"
            out.append(f"{c.env_flag}={val}")
            for extra in c.extra_env_flags:
                out.append(f"{extra}={val}")

    return "\n".join(out)


def compose_env(template_text: str, registry: "ComponentRegistry") -> str:
    """Compose a complete .env: template's non-component keys + generated block.

    Component flag assignments already present in the template are dropped so
    the generated section is the single authority for them (no duplicate keys).
    Non-component keys (API keys, DB settings, ports, comments) are preserved
    verbatim.
    """
    comp_flags = component_env_flags(registry)
    kept: list[str] = []
    for line in template_text.splitlines():
        m = _ENV_LINE.match(line.strip())
        if m and m.group(1) in comp_flags:
            continue  # generated section owns this flag
        kept.append(line)

    body = "\n".join(kept).rstrip()
    section = render_component_section(registry)
    return body + "\n\n" + section + "\n"
