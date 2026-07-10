# CUI // SP-CTI
"""Tests: the installed package can resolve ``tools.*`` and find its config.

Two defects made column masking (and db.storage, security.abac_engine,
llm.router) silently inert in the pip-installed wheel:

1. ~1,900 modules under ``icdev/tools/`` import siblings via the absolute
   ``tools.*`` namespace. A source checkout provides a top-level ``tools/``
   shim; the wheel ships only ``icdev``, so those imports raised
   ``ModuleNotFoundError``. Where that was caught by a broad ``except``
   (``storage.py``'s row masker), the control was disabled with no trace.

2. ``column_security._CONFIG_PATH`` resolved to ``<pkg>/args/...``, but the
   wheel ships the config at ``<pkg>/data/args/...`` — so zero policies loaded
   and every row came back unmasked.
"""

import importlib
import sys

import pytest

cs = importlib.import_module("tools.security.column_security")


class TestToolsNamespaceAlias:
    def test_alias_helper_exists(self):
        import icdev
        assert callable(icdev._alias_tools_namespace)

    def test_real_top_level_tools_is_not_shadowed(self):
        """A source checkout (and a scaffolded project's own tools/) must win."""
        import icdev
        before = sys.modules.get("tools")
        assert before is not None, "source checkout should already provide tools"
        icdev._alias_tools_namespace()  # must be a no-op here
        assert sys.modules["tools"] is before

    def test_alias_is_idempotent(self):
        import icdev
        icdev._alias_tools_namespace()
        icdev._alias_tools_namespace()
        assert "tools" in sys.modules


class TestConfigPathResolution:
    def test_source_layout_resolves_to_args(self):
        assert cs._resolve_config_path() == cs._CONFIG_PATH
        assert cs._CONFIG_PATH.exists()

    def test_packaged_layout_falls_back_to_data_args(self, tmp_path, monkeypatch):
        """When <pkg>/args/ is absent, use <pkg>/data/args/ (the wheel layout)."""
        missing = tmp_path / "args" / "security_config.yaml"
        packaged = tmp_path / "data" / "args" / "security_config.yaml"
        packaged.parent.mkdir(parents=True)
        packaged.write_text("column_policies: []\n", encoding="utf-8")
        monkeypatch.setattr(cs, "_CONFIG_PATH", missing)
        monkeypatch.setattr(cs, "_PACKAGED_CONFIG_PATH", packaged)
        assert cs._resolve_config_path() == packaged
        assert cs._load_config() == {"column_policies": []}

    def test_missing_everywhere_returns_empty_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cs, "_CONFIG_PATH", tmp_path / "nope.yaml")
        monkeypatch.setattr(cs, "_PACKAGED_CONFIG_PATH", tmp_path / "also-nope.yaml")
        assert cs._load_config() == {}

    def test_policies_actually_load_in_this_layout(self):
        """Guard the regression: a wrong path silently means 'no policy'."""
        assert len(cs._load_config().get("column_policies", [])) > 0
        assert cs.get_column_policies_for_role("dashboard_users", "viewer")


class TestMaskFailureIsNotSilent:
    def test_storage_warns_once_per_table_on_masking_failure(self):
        storage = importlib.import_module("tools.db.storage")
        assert hasattr(storage, "_MASK_FAILURE_WARNED")
        assert isinstance(storage._MASK_FAILURE_WARNED, set)

    @pytest.mark.parametrize("role", ["viewer", "auditor"])
    def test_known_policy_roles_resolve(self, role):
        assert cs.get_column_policies_for_role("dashboard_users", role)
