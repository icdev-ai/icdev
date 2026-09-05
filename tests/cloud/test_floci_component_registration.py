# CUI // SP-CTI
"""`floci` is a DECLARED component, and its CLI toggle is the seam's own flag.

flx-compose-01 shipped a pinned `floci` compose profile whose commit body told
operators to run ``icdev enable floci``. That command did not exist: nothing
declared `floci` in ``args/component_registry.yaml``, and the registry is what
makes a component visible to `icdev enable` / `disable` / `status` / `list`,
the `icdev setup` TUI and the generated .env. This registers it.

THE LOAD-BEARING ASSERTION IS NOT "the entry exists" -- it is that the CLI
toggle and ``tools/cloud/emulator.py`` CANNOT DISAGREE. The registry defaults
``env_flag`` to ``ICDEV_<KEY>_ENABLED``, so an entry that simply omits the
field would have `icdev enable floci` write ``ICDEV_FLOCI_ENABLED=true`` -- a
flag the seam never reads -- and `icdev status` would then report floci enabled
on a deployment whose emulator is off. Two derivations of one fact, disagreeing.
So the entry declares ``FLOCI_ENABLED`` and this module proves the two agree
across the whole truthy vocabulary.

Every assertion asks ``ComponentRegistry(env=...)`` with an explicit mapping.
Reading ``os.environ`` here would measure the runner's environment instead of
the registry, and would pass or fail depending on whose machine ran it.
"""

from __future__ import annotations

import pytest

from tools.cloud import emulator
from tools.config.component_registry import ComponentRegistry


@pytest.fixture
def registry() -> ComponentRegistry:
    """The real registry, with an EMPTY env -- never os.environ."""
    return ComponentRegistry(env={})


def test_floci_is_a_declared_core_extension(registry):
    """`floci` is declared, and declared as a core_extension -- not a canvas.

    It has no page, no blueprint and no IQE collections, so `kind: canvas`
    would put it under the 8-point canvas completeness gate for a surface that
    does not exist.
    """
    comp = registry.get("floci")
    assert comp is not None, (
        "floci is not declared in args/component_registry.yaml -- "
        "`icdev enable floci` cannot work until it is"
    )
    assert comp.kind == "core_extension"
    assert comp.module is None
    assert comp.blueprint_attr is None
    assert comp.get_blueprint() is None
    assert not comp.nav, "floci has no page; a nav entry would advertise one"
    assert not comp.iqe, "floci registers no IQE collections"
    assert comp.url_prefix == "", "url_prefix defaults to /<key>; floci mounts nothing"


def test_floci_toggle_uses_the_seams_own_flag(registry):
    """The CLI writes FLOCI_ENABLED -- the flag tools/cloud/emulator.py reads.

    NOT the ``ICDEV_FLOCI_ENABLED`` the loader would default to.
    """
    comp = registry.get("floci")
    assert comp.env_flag == "FLOCI_ENABLED"
    assert comp.env_flag in emulator.DEPRECATED_ALIASES, (
        "FLOCI_ENABLED should be the canonical name the emulator seam reads"
    )


def test_floci_does_not_author_the_deprecated_alias(registry):
    """``LOCALSTACK_ENABLED`` is a READ-fallback, never a name the CLI writes.

    ``extra_env_flags`` flip together, so listing the alias would make
    `icdev enable floci` write a deprecated variable into .env -- and it would
    not even close the read gap, because ``Component.is_enabled`` consults the
    PRIMARY flag only.
    """
    comp = registry.get("floci")
    assert comp.extra_env_flags == []
    assert "LOCALSTACK_ENABLED" not in comp.extra_env_flags


def test_floci_is_reachable_from_the_cli(registry):
    """`icdev enable/disable/status/list floci` resolve through the registry."""
    toggles = registry.get_cli_toggles()
    assert toggles.get("floci") == ["FLOCI_ENABLED"]
    assert registry.get_cli_descriptions().get("floci")


def test_cli_module_exposes_the_floci_toggle():
    """tools/cli/enable.py derives TOGGLES from the registry -- no new list."""
    from tools.cli import enable as enable_mod

    assert "floci" in enable_mod.TOGGLES
    assert enable_mod.TOGGLES["floci"] == ["FLOCI_ENABLED"]
    assert enable_mod.DESCRIPTIONS.get("floci")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"FLOCI_ENABLED": "true"},
        {"FLOCI_ENABLED": "1"},
        {"FLOCI_ENABLED": "yes"},
        {"FLOCI_ENABLED": "on"},
        {"FLOCI_ENABLED": "false"},
        {"FLOCI_ENABLED": "0"},
        {"FLOCI_ENABLED": ""},
        {"FLOCI_ENABLED": "maybe"},
    ],
)
def test_registry_and_emulator_seam_cannot_disagree(env):
    """`icdev status` and ``emulator.enabled()`` answer identically.

    Same flag, same default, same truthy vocabulary -- asserted across the
    whole vocabulary rather than on one happy value, because the two truthy
    parsers are separate implementations that could drift on any of them.
    """
    comp = ComponentRegistry(env=env).get("floci")
    assert comp is not None
    assert comp.is_enabled(env) is emulator.enabled(env), (
        f"registry and emulator seam disagree for {env!r}"
    )


def test_floci_defaults_off_matching_the_airgap_posture(registry):
    """Default OFF -- the operator opts in explicitly, on both derivations."""
    comp = registry.get("floci")
    assert comp.default_enabled is False
    assert comp.is_enabled({}) is False
    assert emulator.enabled({}) is False


def test_deprecated_alias_alone_is_a_known_bounded_divergence(registry):
    """RECORDED, not papered over: the alias moves the seam and not the toggle.

    ``emulator._read`` falls back to ``LOCALSTACK_ENABLED`` when the canonical
    flag is unset; ``Component.is_enabled`` reads the primary flag only. So a
    deployment carrying ONLY the deprecated name has an emulator ON and a
    registry that says off. That is the one input on which the two differ, and
    it is exactly the deployment the seam already warns about. Asserted here so
    the divergence is a known bound rather than a later surprise -- and so that
    closing it (or widening it) fails this test rather than passing silently.
    """
    env = {"LOCALSTACK_ENABLED": "true"}
    emulator.reset_alias_warnings()
    assert emulator.enabled(env) is True
    assert registry.get("floci").is_enabled(env) is False
