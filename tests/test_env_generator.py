# CUI // SP-CTI
"""Tests for the registry-driven .env generator (pkg-init-02).

The reported install bug: a canvas was default-OFF and nothing in the .env
connected its URL to its env flag, so the user never found it. The generator
guarantees EVERY registry component flag lands in the generated .env — enabled
live, the rest present and commented — while preserving the template's
non-component keys.

Run: pytest tests/test_env_generator.py -v --tb=short
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli.env_generator import (
    compose_env,
    component_env_flags,
    render_component_section,
)
from tools.config.component_registry import get_registry

_ENV = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)


def _assigned_keys(text: str) -> set[str]:
    return set(_ENV.findall(text))


def test_every_registry_flag_present_in_generated_env():
    """The core guarantee: no component flag is omitted from the .env."""
    reg = get_registry()
    section = render_component_section(reg)
    keys = _assigned_keys(section)
    missing = component_env_flags(reg) - keys
    assert not missing, f"registry flags missing from generated .env: {sorted(missing)}"


def test_default_enabled_flags_are_true():
    """A default-enabled component is written as `=true`, others `=false`."""
    reg = get_registry()
    section = render_component_section(reg)
    for c in reg.list_all():
        if not c.env_flag:
            continue
        expected = "true" if c.default_enabled else "false"
        assert f"{c.env_flag}={expected}" in section, (
            f"{c.env_flag} should be {expected}"
        )


def test_section_is_grouped_by_kind():
    reg = get_registry()
    section = render_component_section(reg)
    for label in ("CANVASES", "FEATURES", "CORE EXTENSIONS", "CHILD APPS"):
        assert label in section, f"missing group header: {label}"


def test_compose_preserves_non_component_keys_and_drops_dupes():
    """Non-component keys survive verbatim; component flags in the template are
    dropped so the generated section is their single authority (no dup keys)."""
    reg = get_registry()
    # A template with one non-component key and one component flag set stale.
    sample_flag = next(c.env_flag for c in reg.list_all() if c.env_flag)
    template = (
        "# header\n"
        "ANTHROPIC_API_KEY=\n"
        "OLLAMA_MODEL=qwen3.5:latest\n"
        f"{sample_flag}=STALE_VALUE\n"
    )
    out = compose_env(template, reg)
    keys = _assigned_keys(out)
    # Non-component keys preserved.
    assert "ANTHROPIC_API_KEY" in keys
    assert "OLLAMA_MODEL" in keys
    # The stale component assignment must not appear.
    assert "STALE_VALUE" not in out
    # The component flag appears exactly once (generated section owns it).
    assert out.count(f"\n{sample_flag}=") == 1
    # And every registry flag is present.
    assert not (component_env_flags(reg) - keys)


def test_generated_env_documents_urls_for_discoverability():
    """A canvas with a url_prefix has that URL in a comment, so an operator can
    connect the flag to the page (the fix for the reported confusion)."""
    reg = get_registry()
    section = render_component_section(reg)
    canvas = next(
        (c for c in reg.list_all()
         if c.kind == "canvas" and c.env_flag and c.url_prefix),
        None,
    )
    assert canvas is not None
    assert f"url: {canvas.url_prefix}" in section
