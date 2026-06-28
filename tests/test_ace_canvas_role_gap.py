# CUI // SP-CTI
"""Tests for ACE Phase 5a — canvas role gap detection."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers — fake registry / role data
# ---------------------------------------------------------------------------

_REGISTRY_TWO_CANVASES = """
components:
  - key: dic
    kind: canvas
    display_name: Document Intelligence Canvas
    enabled: true
  - key: ndc
    kind: canvas
    display_name: Network Design Canvas
    enabled: true
  - key: byok
    kind: feature
    display_name: BYOK (feature, should be excluded)
"""

_REGISTRY_ONE_CANVAS = """
components:
  - key: dic
    kind: canvas
    display_name: Document Intelligence Canvas
    enabled: true
"""

_ROLE_WITH_DIC = """
role_id: dic_analyst
canvas: dic
trust_tier: yellow
"""

_ROLE_WITH_NDC = """
role_id: ndc_auditor
canvas: ndc
trust_tier: green
"""

_ROLE_NO_CANVAS = """
role_id: generic_developer
trust_tier: yellow
"""


# ---------------------------------------------------------------------------
# _load_canvases
# ---------------------------------------------------------------------------

class TestLoadCanvases:
    def _patch(self, registry_text: str):
        return patch(
            "icdev.tools.ace.canvas_role_gap._REGISTRY_PATH",
            new_callable=lambda: property(lambda self: None),
        )

    def _call(self, yaml_text: str):

        # Patch the path object so read_text returns our fixture
        from icdev.tools.ace import canvas_role_gap as m
        fake_path = Path("/fake/component_registry.yaml")

        original = m._REGISTRY_PATH
        m._REGISTRY_PATH = fake_path
        try:
            with patch.object(Path, "read_text", return_value=yaml_text):
                return m._load_canvases()
        finally:
            m._REGISTRY_PATH = original

    def test_returns_only_canvas_and_child_app(self):
        result = self._call(_REGISTRY_TWO_CANVASES)
        keys = {c["key"] for c in result}
        assert "dic" in keys
        assert "ndc" in keys
        assert "byok" not in keys  # feature, excluded

    def test_empty_registry_returns_empty(self):
        result = self._call("components: []")
        assert result == []

    def test_missing_registry_returns_empty(self):
        from icdev.tools.ace import canvas_role_gap as m
        fake_path = Path("/nonexistent/registry.yaml")
        original = m._REGISTRY_PATH
        m._REGISTRY_PATH = fake_path
        try:
            result = m._load_canvases()
        finally:
            m._REGISTRY_PATH = original
        assert result == []


# ---------------------------------------------------------------------------
# _build_role_canvas_map
# ---------------------------------------------------------------------------

class TestBuildRoleCanvasMap:
    def _call_with_roles(self, roles: dict[str, str]):
        """roles = {filename: yaml_content}"""
        from icdev.tools.ace import canvas_role_gap as m
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            for fname, content in roles.items():
                (td / fname).write_text(content, encoding="utf-8")
            original = m._ROLES_DIR
            m._ROLES_DIR = td
            try:
                return m._build_role_canvas_map()
            finally:
                m._ROLES_DIR = original

    def test_role_with_canvas_appears_in_map(self):
        result = self._call_with_roles({"dic_analyst.yaml": _ROLE_WITH_DIC})
        assert "dic" in result
        assert result["dic"]["role_id"] == "dic_analyst"

    def test_role_without_canvas_not_in_map(self):
        result = self._call_with_roles({"generic_dev.yaml": _ROLE_NO_CANVAS})
        assert result == {}

    def test_trust_tier_propagated(self):
        result = self._call_with_roles({"ndc.yaml": _ROLE_WITH_NDC})
        assert result["ndc"]["trust_tier"] == "green"

    def test_multiple_roles_both_appear(self):
        result = self._call_with_roles({
            "dic.yaml": _ROLE_WITH_DIC,
            "ndc.yaml": _ROLE_WITH_NDC,
        })
        assert "dic" in result and "ndc" in result

    def test_malformed_yaml_skipped_gracefully(self):
        result = self._call_with_roles({
            "good.yaml": _ROLE_WITH_DIC,
            "bad.yaml": ":\n  - invalid: [yaml\n",
        })
        # Should still return the good role; bad file skipped
        assert "dic" in result

    def test_empty_roles_dir_returns_empty(self):
        from icdev.tools.ace import canvas_role_gap as m
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            original = m._ROLES_DIR
            m._ROLES_DIR = Path(tmpdir)
            try:
                result = m._build_role_canvas_map()
            finally:
                m._ROLES_DIR = original
        assert result == {}


# ---------------------------------------------------------------------------
# detect_gaps — full integration (mocked)
# ---------------------------------------------------------------------------

class TestDetectGaps:
    def _run(self, canvases, role_canvas_map):
        from icdev.tools.ace import canvas_role_gap as m
        with patch.object(m, "_load_canvases", return_value=canvases), \
             patch.object(m, "_build_role_canvas_map", return_value=role_canvas_map):
            return m.detect_gaps()

    def test_all_gaps_when_no_roles(self):
        canvases = [
            {"key": "dic", "display_name": "DIC", "enabled": True},
            {"key": "ndc", "display_name": "NDC", "enabled": True},
        ]
        result = self._run(canvases, {})
        assert result["gap_count"] == 2
        assert result["covered"] == 0
        gap_keys = {g["canvas_key"] for g in result["gaps"]}
        assert "dic" in gap_keys
        assert "ndc" in gap_keys

    def test_covered_when_role_matches(self):
        canvases = [
            {"key": "dic", "display_name": "DIC", "enabled": True},
            {"key": "ndc", "display_name": "NDC", "enabled": True},
        ]
        role_map = {"dic": {"role_id": "dic_analyst", "trust_tier": "yellow"}}
        result = self._run(canvases, role_map)
        assert result["gap_count"] == 1
        assert result["covered"] == 1
        assert result["covered_by"][0]["canvas_key"] == "dic"
        assert result["covered_by"][0]["role_id"] == "dic_analyst"

    def test_full_coverage_no_gaps(self):
        canvases = [{"key": "dic", "display_name": "DIC", "enabled": True}]
        role_map = {"dic": {"role_id": "dic_analyst", "trust_tier": "yellow"}}
        result = self._run(canvases, role_map)
        assert result["gap_count"] == 0
        assert result["gaps"] == []

    def test_total_counts(self):
        canvases = [
            {"key": "a", "display_name": "A", "enabled": True},
            {"key": "b", "display_name": "B", "enabled": True},
            {"key": "c", "display_name": "C", "enabled": False},
        ]
        result = self._run(canvases, {"a": {"role_id": "r_a", "trust_tier": "green"}})
        assert result["total_canvases"] == 3
        assert result["covered"] == 1
        assert result["gap_count"] == 2

    def test_display_name_propagated_to_gap(self):
        canvases = [{"key": "foundry", "display_name": "ACF Foundry", "enabled": True}]
        result = self._run(canvases, {})
        assert result["gaps"][0]["display_name"] == "ACF Foundry"

    def test_enabled_flag_propagated_to_gap(self):
        canvases = [{"key": "x", "display_name": "X Canvas", "enabled": False}]
        result = self._run(canvases, {})
        assert result["gaps"][0]["enabled"] is False
