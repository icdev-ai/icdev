# CUI // SP-CTI
"""Regression tests for ``tools/cli/scaffold.py::_register_component``.

The bug these pin: the scaffolder appended new components to top-level
``canvases:`` / ``child_apps:`` / ``core_extensions:`` keys, while
``ComponentRegistry._load_components()`` reads ``data["components"]`` and
nothing else. Every scaffolded component was therefore registered into a list
that is never loaded — ``icdev scaffold`` reported success and produced a
component the platform could not see.

The assertion that matters is NOT "a key was written" but "the registry can
load it back" — a test that only checked the YAML key would have passed against
the broken code just as happily.
"""
import importlib

import pytest

yaml = pytest.importorskip("yaml")

scaffold = importlib.import_module("tools.cli.scaffold")
component_registry = importlib.import_module("tools.config.component_registry")


HEADER = """# CUI // SP-CTI
# Registry header comment that must survive a write.

components:

  # ═══ Section banner ═══
- key: existing
  kind: canvas
  display_name: Existing
  env_flag: ICDEV_EXISTING_ENABLED
  url_prefix: /existing
  default_enabled: false
  # `module` is REQUIRED for any kind other than feature/core_extension
  # (component_registry._make_component) — an entry without it is silently
  # skipped by the loader, so a fixture missing it would not be realistic.
  module: tools.existing_canvas.blueprint
  blueprint_attr: create_existing_blueprint

# This comment block documents the NEXT key and must stay attached to it.
iqe_path_canvas:
- {path: /existing, canvas: existing}
"""


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    path = tmp_path / "component_registry.yaml"
    path.write_text(HEADER, encoding="utf-8", newline="")
    monkeypatch.setattr(scaffold, "REGISTRY_PATH", path)
    return path


def _register(kind="canvas", key="widgets"):
    return scaffold._register_component(
        kind=kind,
        key=key,
        display_name="Widgets",
        env_flag="ICDEV_WIDGETS_ENABLED",
        url_prefix="/widgets",
        default_roles=["developer"],
    )


def test_scaffolded_component_is_actually_loadable(registry, monkeypatch):
    """The whole point: the registry loader must SEE the new component."""
    result = _register()
    assert result["registered"] is True

    monkeypatch.setattr(component_registry, "_REGISTRY", None, raising=False)
    reg = component_registry.ComponentRegistry(registry_path=registry)
    keys = {c.key for c in reg.list_all()}

    assert "widgets" in keys, (
        "scaffolded component is not visible to ComponentRegistry — it was "
        "written to a key the loader never reads"
    )
    assert "existing" in keys, "pre-existing components must survive the write"


def test_entry_lands_under_components_not_a_per_kind_key(registry):
    _register()
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))

    assert {"components", "iqe_path_canvas"} == set(data), (
        f"unexpected top-level keys: {sorted(data)} — per-kind lists are dead keys"
    )
    assert [c["key"] for c in data["components"]] == ["existing", "widgets"]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("canvas", "canvas"), ("child-app", "child_app"), ("core", "core_extension")],
)
def test_kind_is_carried_on_the_entry(registry, kind, expected):
    """One list, so `kind` on the entry is what distinguishes the three types."""
    result = _register(kind=kind, key=f"w_{expected}")
    assert result["kind"] == expected

    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    entry = next(c for c in data["components"] if c["key"] == f"w_{expected}")
    assert entry["kind"] == expected


def test_comments_survive_registration(registry):
    """A yaml.dump round-trip would silently delete every comment."""
    _register()
    text = registry.read_text(encoding="utf-8")

    assert "# CUI // SP-CTI" in text
    assert "# Registry header comment that must survive a write." in text
    assert "# ═══ Section banner ═══" in text
    assert "# This comment block documents the NEXT key" in text


def test_new_entry_precedes_the_following_top_level_key(registry):
    """Insertion point must be the end of the components LIST, not EOF."""
    _register()
    text = registry.read_text(encoding="utf-8")

    assert text.index("key: widgets") < text.index("iqe_path_canvas:"), (
        "entry was appended after the next top-level key, which would silently "
        "re-parent it"
    )
    # The trailing comment block belongs to iqe_path_canvas and must stay with it.
    assert text.index("key: widgets") < text.index("# This comment block documents")


def test_duplicate_key_is_refused(registry):
    before = registry.read_text(encoding="utf-8")
    result = scaffold._register_component(
        kind="canvas",
        key="existing",
        display_name="Existing",
        env_flag="ICDEV_EXISTING_ENABLED",
        url_prefix="/existing",
        default_roles=["developer"],
    )
    assert result["registered"] is False
    assert "already in registry" in result["reason"]
    assert registry.read_text(encoding="utf-8") == before, "refusal must not write"


def test_dry_run_does_not_write(registry):
    before = registry.read_text(encoding="utf-8")
    result = _register_dry(registry)
    assert result["registered"] is False and result["dry_run"] is True
    assert result["would_add"]["key"] == "widgets"
    assert registry.read_text(encoding="utf-8") == before


def _register_dry(_registry):
    return scaffold._register_component(
        kind="canvas",
        key="widgets",
        display_name="Widgets",
        env_flag="ICDEV_WIDGETS_ENABLED",
        url_prefix="/widgets",
        default_roles=["developer"],
        dry_run=True,
    )


def test_file_without_trailing_key_appends_at_eof(tmp_path, monkeypatch):
    """components: running to EOF is legal — insertion must still work."""
    path = tmp_path / "reg.yaml"
    path.write_text(
        "components:\n- key: only\n  kind: canvas\n  display_name: Only\n",
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setattr(scaffold, "REGISTRY_PATH", path)
    assert _register()["registered"] is True

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [c["key"] for c in data["components"]] == ["only", "widgets"]
