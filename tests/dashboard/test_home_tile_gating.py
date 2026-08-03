# CUI // SP-CTI
"""The home Cortex tile must not report a fault for a switched-off canvas.

Regression for: with ICDEV_CORTEX_ENABLED unset the Cortex blueprint is never
registered, so /cortex/api/metrics/tile 404s and the tile rendered "Cortex
metrics unavailable" — telling an operator the audit trail was broken when the
component was merely disabled. The tile already distinguished ok / idle /
unavailable; it had no state for "not enabled".

These assert on source rather than by booting the app: importing
tools.dashboard.app triggers air-gap detection, which socket-probes local LLM
servers and stalls the suite.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "tools/dashboard/templates/index.html"
APP = ROOT / "tools/dashboard/app.py"


@pytest.fixture(scope="module")
def html():
    return INDEX.read_text(encoding="utf-8")


def test_registry_answers_enablement_for_any_component():
    """One registry-driven helper, not a per-component boolean."""
    from tools.config.component_registry import ComponentRegistry

    reg = ComponentRegistry()
    assert callable(reg.is_enabled)
    for key in ("cortex", "dic", "ndc"):
        assert isinstance(reg.is_enabled(key), bool)


def test_cortex_is_disabled_by_default():
    """Precondition for the bug: the component ships off."""
    from tools.config.component_registry import ComponentRegistry

    assert ComponentRegistry().is_enabled("cortex") is False


def test_context_processor_exposes_the_helper():
    src = APP.read_text(encoding="utf-8")
    assert '"component_enabled": _REGISTRY.is_enabled' in src, (
        "templates cannot gate on component enablement without this helper"
    )


def test_tile_is_gated_on_component_enablement(html):
    gate = html.index("component_enabled('cortex')")
    tile = html.index('id="tile-cortex"')
    endif = html.index("{% endif %}", tile)
    assert gate < tile < endif, "tile-cortex is not enclosed by the enablement gate"


def test_404_is_reported_as_not_enabled_not_as_a_fault(html):
    assert "r.status === 404" in html, "404 is not distinguished from other errors"
    assert "err.notMounted" in html, "no distinct not-mounted branch in the catch"

    catch_start = html.index(".catch(function (err)")
    body = html[catch_start:catch_start + 1500]
    assert body.index("Not enabled") < body.index("Metrics unavailable"), (
        "the not-enabled branch must precede the fault branch"
    )


def test_template_still_parses(html):
    from jinja2 import Environment

    Environment().parse(html)


def test_icdev_mirror_matches():
    """index.html was byte-identical to its icdev/ mirror before this change."""
    mirror = ROOT / "icdev/tools/dashboard/templates/index.html"
    if not mirror.exists():
        pytest.skip("no icdev mirror in this tree")
    assert mirror.read_text(encoding="utf-8") == INDEX.read_text(encoding="utf-8")
