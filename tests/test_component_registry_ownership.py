# CUI // SP-CTI
"""Tests for component-registry ownership fields (idp-cat-01).

`owner` / `owner_contact` / `on_call` answer the first question any Internal
Developer Portal has to answer: *who owns this?* The invariants under test:

  - the fields are OPTIONAL — every existing registry entry still loads;
  - `Component.owner` is readable for any registered component;
  - a component with no owner is reported as UNOWNED, never defaulted to some
    fallback team, and placeholder text ("TBD", "unassigned") does not count
    as an owner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.config.component_registry import (
    UNOWNED_SENTINELS,
    Component,
    ComponentRegistry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_REGISTRY = REPO_ROOT / "args" / "component_registry.yaml"


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    """The real shipped registry, loaded with an empty environment."""
    return ComponentRegistry(registry_path=REAL_REGISTRY, env={})


def _write_registry(tmp_path: Path, body: str) -> ComponentRegistry:
    """Build a registry from an inline YAML fragment."""
    path = tmp_path / "component_registry.yaml"
    path.write_text(body, encoding="utf-8")
    return ComponentRegistry(registry_path=path, env={})


# ---------------------------------------------------------------------------
# Optional-ness: the real registry must keep loading
# ---------------------------------------------------------------------------


def test_all_existing_entries_still_load(registry):
    """Adding optional ownership fields breaks no existing entry.

    A required field would fail the WHOLE registry load, taking every canvas
    and child app down with it.
    """
    import yaml

    declared = yaml.safe_load(REAL_REGISTRY.read_text(encoding="utf-8"))["components"]
    assert len(registry) == len(declared), (
        f"{len(declared)} components declared but only {len(registry)} loaded — "
        "an entry was rejected"
    )
    assert len(registry) >= 66


def test_owner_is_readable_for_every_registered_component(registry):
    """`Component.owner` resolves for any component, owned or not."""
    assert len(registry) > 0
    for comp in registry:
        assert comp.owner is None or isinstance(comp.owner, str)
        assert comp.owner_contact is None or isinstance(comp.owner_contact, str)
        assert comp.on_call is None or isinstance(comp.on_call, str)
        assert comp.is_owned is (comp.owner is not None)
        assert registry.get_owner(comp.key) == comp.owner


def test_ownership_map_covers_every_component(registry):
    """The ownership map has one record per registered component."""
    mapping = registry.get_ownership_map()
    assert set(mapping) == {c.key for c in registry}
    for key, record in mapping.items():
        assert record["key"] == key
        assert set(record) == {
            "key",
            "kind",
            "display_name",
            "owned",
            "owner",
            "owner_contact",
            "on_call",
        }


def test_owner_is_not_default_roles(registry):
    """`default_roles` is RBAC access, not accountability — never conflated."""
    for comp in registry:
        if comp.default_roles:
            assert comp.owner not in comp.default_roles


# ---------------------------------------------------------------------------
# Unowned is reported, not defaulted
# ---------------------------------------------------------------------------


def test_component_without_owner_is_reported_unowned(tmp_path):
    """No owner declared → unowned, and no fallback owner is invented."""
    reg = _write_registry(
        tmp_path,
        """
components:
- key: orphan
  kind: canvas
  display_name: Orphan Canvas
  module: tools.network.blueprint
  blueprint_attr: create_network_blueprint
""",
    )
    comp = reg.get("orphan")
    assert comp.owner is None
    assert comp.owner_contact is None
    assert comp.on_call is None
    assert comp.is_owned is False
    assert comp.ownership()["owned"] is False
    assert [c.key for c in reg.list_unowned()] == ["orphan"]
    assert reg.list_owned() == []


def test_declared_owner_is_parsed(tmp_path):
    """A real owner round-trips through the loader."""
    reg = _write_registry(
        tmp_path,
        """
components:
- key: owned
  kind: canvas
  display_name: Owned Canvas
  module: tools.network.blueprint
  blueprint_attr: create_network_blueprint
  owner: '  Platform Engineering  '
  owner_contact: platform-eng@example.mil
  on_call: platform-primary
""",
    )
    comp = reg.get("owned")
    assert comp.owner == "Platform Engineering"  # whitespace stripped
    assert comp.owner_contact == "platform-eng@example.mil"
    assert comp.on_call == "platform-primary"
    assert comp.is_owned is True
    assert reg.get_owner("owned") == "Platform Engineering"
    assert reg.list_unowned() == []
    assert [c.key for c in reg.list_owned()] == ["owned"]


@pytest.mark.parametrize("placeholder", sorted(UNOWNED_SENTINELS - {""}))
def test_placeholder_owner_counts_as_unowned(tmp_path, placeholder):
    """"TBD" is not an owner. It routes an incident to nobody."""
    reg = _write_registry(
        tmp_path,
        f"""
components:
- key: placeholder
  kind: feature
  display_name: Placeholder
  owner: '{placeholder.upper()}'
""",
    )
    comp = reg.get("placeholder")
    assert comp.owner is None
    assert comp.is_owned is False
    assert [c.key for c in reg.list_unowned()] == ["placeholder"]


def test_contact_without_owner_is_still_unowned(tmp_path):
    """A contact address is not accountability."""
    reg = _write_registry(
        tmp_path,
        """
components:
- key: contactonly
  kind: feature
  display_name: Contact Only
  owner_contact: someone@example.mil
  on_call: rotation-a
""",
    )
    comp = reg.get("contactonly")
    assert comp.owner_contact == "someone@example.mil"
    assert comp.on_call == "rotation-a"
    assert comp.is_owned is False
    assert [c.key for c in reg.list_unowned()] == ["contactonly"]


def test_malformed_owner_does_not_break_load(tmp_path):
    """A non-scalar owner degrades to unowned instead of failing the registry."""
    reg = _write_registry(
        tmp_path,
        """
components:
- key: broken
  kind: feature
  display_name: Broken
  owner:
    team: nested
- key: fine
  kind: feature
  display_name: Fine
  owner: Ops
""",
    )
    assert len(reg) == 2
    assert reg.get("broken").owner is None
    assert reg.get("broken").is_owned is False
    assert reg.get("fine").owner == "Ops"


def test_get_owner_for_unknown_key_is_none(registry):
    """An unregistered key has no owner and does not raise."""
    assert registry.get_owner("definitely-not-a-component") is None


# ---------------------------------------------------------------------------
# Summary / scorecard input
# ---------------------------------------------------------------------------


def test_ownership_summary_counts(tmp_path):
    """Summary arithmetic feeds the IDP scorecard rule."""
    reg = _write_registry(
        tmp_path,
        """
components:
- key: a
  kind: feature
  display_name: A
  owner: Team A
  owner_contact: a@example.mil
  on_call: rota-a
- key: b
  kind: feature
  display_name: B
- key: c
  kind: feature
  display_name: C
  owner: TBD
- key: d
  kind: canvas
  display_name: D
  module: tools.network.blueprint
  blueprint_attr: create_network_blueprint
  owner: Team D
""",
    )
    summary = reg.get_ownership_summary()
    assert summary["total"] == 4
    assert summary["owned"] == 2
    assert summary["unowned"] == 2
    assert summary["unowned_keys"] == ["b", "c"]
    assert summary["coverage_pct"] == 50.0
    assert summary["with_contact"] == 1
    assert summary["with_on_call"] == 1

    scoped = reg.get_ownership_summary(kind="canvas")
    assert scoped == {
        "kind": "canvas",
        "total": 1,
        "owned": 1,
        "unowned": 0,
        "unowned_keys": [],
        "coverage_pct": 100.0,
        "with_contact": 0,
        "with_on_call": 0,
    }


def test_ownership_summary_on_empty_registry(tmp_path):
    """No components → 0% coverage, no ZeroDivisionError."""
    reg = _write_registry(tmp_path, "components: []\n")
    summary = reg.get_ownership_summary()
    assert summary["total"] == 0
    assert summary["coverage_pct"] == 0.0
    assert summary["unowned_keys"] == []


def test_real_registry_summary_is_internally_consistent(registry):
    """owned + unowned == total, and the keys match `list_unowned()`."""
    summary = registry.get_ownership_summary()
    assert summary["owned"] + summary["unowned"] == summary["total"] == len(registry)
    assert summary["unowned_keys"] == sorted(c.key for c in registry.list_unowned())


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_component_constructible_without_ownership_kwargs():
    """Ownership fields default to None so existing construction still works."""
    comp = Component(
        key="legacy",
        kind="canvas",
        cli_name="legacy",
        display_name="Legacy",
        description="",
        env_flag="ICDEV_LEGACY_ENABLED",
        extra_env_flags=[],
        default_enabled=False,
        module="tools.network.blueprint",
        blueprint_attr="create_network_blueprint",
        url_prefix="/legacy",
        min_il="IL2",
        min_tier="community",
        default_roles=[],
        nav={},
        iqe={},
        completeness={},
        raw={},
    )
    assert comp.owner is None
    assert comp.is_owned is False


def test_registry_yaml_and_loader_are_mirrored():
    """The icdev/ package copies must not drift — the parity gate blocks all
    branches when they do."""
    pairs = [
        (
            REPO_ROOT / "tools" / "config" / "component_registry.py",
            REPO_ROOT / "icdev" / "tools" / "config" / "component_registry.py",
        ),
        (
            REAL_REGISTRY,
            REPO_ROOT / "icdev" / "data" / "args" / "component_registry.yaml",
        ),
    ]
    for source, mirror in pairs:
        if not mirror.exists():
            pytest.skip(f"mirror not present in this checkout: {mirror}")
        assert source.read_bytes() == mirror.read_bytes(), (
            f"{mirror.relative_to(REPO_ROOT)} has drifted from "
            f"{source.relative_to(REPO_ROOT)}"
        )
