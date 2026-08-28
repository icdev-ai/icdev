# CUI // SP-CTI
"""The home Cortex tile must not report a fault for a switched-off canvas.

cch-obs-08 REMOVED that tile from Home. The registry-level guarantees below still
hold and still run; the two tile-specific ones are conditional on the tile existing,
so they return the moment it does. See the module comment above them.

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
    """Precondition for the bug: the component ships off.

    "Ships off" is a claim about the registry's ``default_enabled``, so ask it
    with an EMPTY environment. The original assertion read ``os.environ`` and
    so measured whoever ran the suite: on a host exporting
    ``ICDEV_CORTEX_ENABLED=true`` it failed on every observation from the day
    it landed (born_red_survey finding 87e60dc7d52e4104, task-det-87e60dc7d5)
    while saying nothing about the registry.
    """
    from tools.config.component_registry import ComponentRegistry

    assert ComponentRegistry(env={}).is_enabled("cortex") is False


def test_cortex_flag_is_what_switches_it_on():
    """The same registry answers True only when the flag says so."""
    from tools.config.component_registry import ComponentRegistry

    assert ComponentRegistry(env={"ICDEV_CORTEX_ENABLED": "true"}).is_enabled("cortex") is True
    assert ComponentRegistry(env={"ICDEV_CORTEX_ENABLED": "false"}).is_enabled("cortex") is False


def test_context_processor_exposes_the_helper():
    src = APP.read_text(encoding="utf-8")
    assert '"component_enabled": _REGISTRY.is_enabled' in src, (
        "templates cannot gate on component enablement without this helper"
    )


# The two assertions below were UNCONDITIONAL until cch-obs-08 removed the Cortex tile
# from Home, at which point they failed on `html.index(...)` raising ValueError.
#
# They are now CONDITIONAL rather than deleted, and the difference matters. Deleting them
# would discard a guarantee because its subject happened to go away this week -- and the
# regression they encode (a switched-off component reporting a FAULT) is a property of any
# tile that fetches a component-gated endpoint, not of this one. Written this way they are
# silent while the tile is absent and fire again the moment somebody re-adds it without
# the gate, which is exactly when the guarantee is needed.
#
# That the tile is currently absent is asserted positively, per token and per tree, by
# tests/dashboard/test_home_tile_removal.py -- so "no tile" cannot be reached by accident
# and then sit unnoticed behind a skipped assertion here.


def test_tile_is_gated_on_component_enablement(html):
    if 'id="tile-cortex"' not in html:
        return   # removed by cch-obs-08; see test_home_tile_removal.py
    gate = html.index("component_enabled('cortex')")
    tile = html.index('id="tile-cortex"')
    endif = html.index("{% endif %}", tile)
    assert gate < tile < endif, "tile-cortex is not enclosed by the enablement gate"


def test_404_is_reported_as_not_enabled_not_as_a_fault(html):
    if "/cortex/api/metrics/tile" not in html:
        return   # nothing on Home fetches it any more; see test_home_tile_removal.py
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
    # The mirror is a tracked file (the companion sync writes it); a tree
    # without it is a broken tree, not a reason to skip -- a gated test that
    # skips is an unmeasured one (skip_census).
    assert mirror.exists(), "icdev/ mirror of index.html is missing"
    assert mirror.read_text(encoding="utf-8") == INDEX.read_text(encoding="utf-8")
