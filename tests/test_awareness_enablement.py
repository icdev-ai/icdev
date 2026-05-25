# CUI // SP-CTI
"""Phase 1c unit tests for tools/awareness/enablement.py.

Coverage:
  * load_enablement_flags — .env parsing, caching, missing file, malformed
  * enabled_flags_signature — stability + change detection
  * load_enablement_map — YAML parsing, missing file, malformed, cache
  * is_component_enabled — rule matching, default-on, flag resolution
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import enablement as en  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear module-level caches between tests."""
    en._reset_caches_for_test()
    yield
    en._reset_caches_for_test()


class TestLoadEnablementFlags:
    """load_enablement_flags()"""

    def _write_env(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")

    def test_parses_simple_flags(self, tmp_path):
        env = tmp_path / ".env"
        self._write_env(
            env,
            """
            FOO_ENABLED=true
            BAR_ENABLED=false
            BAZ_ENABLED=1
            """,
        )
        flags = en.load_enablement_flags(env_file=env)
        assert flags == {"FOO_ENABLED": True, "BAR_ENABLED": False, "BAZ_ENABLED": True}

    def test_strips_quotes_and_whitespace(self, tmp_path):
        env = tmp_path / ".env"
        self._write_env(
            env,
            """
            A_ENABLED = "true"
            B_ENABLED = '  false  '
            C_ENABLED=on
            D_ENABLED=OFF
            """,
        )
        flags = en.load_enablement_flags(env_file=env)
        assert flags == {
            "A_ENABLED": True,
            "B_ENABLED": False,
            "C_ENABLED": True,
            "D_ENABLED": False,
        }

    def test_ignores_comments_and_non_enabled_vars(self, tmp_path):
        env = tmp_path / ".env"
        self._write_env(
            env,
            """
            # Top-of-file comment
            API_KEY=sk-abc
            FOO_ENABLED=true
            # Another comment
            NOT_A_FLAG=yes
            BAR_ENABLED=false
            """,
        )
        flags = en.load_enablement_flags(env_file=env)
        assert set(flags.keys()) == {"FOO_ENABLED", "BAR_ENABLED"}

    def test_missing_file_returns_empty(self, tmp_path):
        flags = en.load_enablement_flags(env_file=tmp_path / "nonexistent.env")
        assert flags == {}

    def test_unknown_value_defaults_to_false(self, tmp_path):
        env = tmp_path / ".env"
        self._write_env(
            env,
            """
            WEIRD_ENABLED=maybe
            GOOD_ENABLED=true
            """,
        )
        flags = en.load_enablement_flags(env_file=env)
        assert flags["GOOD_ENABLED"] is True
        assert flags["WEIRD_ENABLED"] is False  # conservative fallback

    def test_env_example_fallback_merges_with_env(self, tmp_path):
        """Real-world case: .env.example defines RAG_ENABLED=true as
        a default, the operator's .env overrides ICDEV_NDC_ENABLED=true
        without mentioning RAG_ENABLED. Expected: RAG_ENABLED remains
        True from the example fallback."""
        example = tmp_path / ".env.example"
        self._write_env(
            example,
            """
            RAG_ENABLED=true
            FINETUNE_ENABLED=true
            ICDEV_NDC_ENABLED=false
            """,
        )
        env = tmp_path / ".env"
        self._write_env(
            env,
            """
            ICDEV_NDC_ENABLED=true
            ICDEV_NETWORK_ENABLED=true
            """,
        )
        flags = en.load_enablement_flags(env_file=env, env_example_file=example)
        # From .env.example (not overridden in .env)
        assert flags["RAG_ENABLED"] is True
        assert flags["FINETUNE_ENABLED"] is True
        # Overridden by .env
        assert flags["ICDEV_NDC_ENABLED"] is True
        # Only in .env
        assert flags["ICDEV_NETWORK_ENABLED"] is True

    def test_example_fallback_disabled_when_env_override_only(self, tmp_path):
        """When a test passes env_file only, the fallback should NOT
        silently pull in the real .env.example from the project root
        (test isolation)."""
        env = tmp_path / ".env"
        self._write_env(env, "ONLY_ENABLED=true\n")
        flags = en.load_enablement_flags(env_file=env)
        assert flags == {"ONLY_ENABLED": True}
        assert "RAG_ENABLED" not in flags  # not contaminated by real .env.example


class TestSignature:
    """enabled_flags_signature()"""

    def test_stable_for_same_input(self):
        f1 = {"A_ENABLED": True, "B_ENABLED": False}
        f2 = {"B_ENABLED": False, "A_ENABLED": True}  # different insertion order
        assert en.enabled_flags_signature(f1) == en.enabled_flags_signature(f2)

    def test_changes_when_flag_flips(self):
        f1 = {"A_ENABLED": True}
        f2 = {"A_ENABLED": False}
        assert en.enabled_flags_signature(f1) != en.enabled_flags_signature(f2)

    def test_changes_when_flag_added(self):
        f1 = {"A_ENABLED": True}
        f2 = {"A_ENABLED": True, "B_ENABLED": False}
        assert en.enabled_flags_signature(f1) != en.enabled_flags_signature(f2)

    def test_signature_is_hex_sha256(self):
        sig = en.enabled_flags_signature({"FOO_ENABLED": True})
        assert len(sig) == 64
        int(sig, 16)  # must be valid hex


class TestLoadEnablementMap:
    """load_enablement_map()"""

    def _write_map(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")

    def test_parses_valid_yaml(self, tmp_path):
        m = tmp_path / "map.yaml"
        self._write_map(
            m,
            """
            mappings:
              - entity_type: canvas_module
                path_glob: tools/network_canvas/**
                requires: [ICDEV_NETWORK_ENABLED, ICDEV_NDC_ENABLED]
              - entity_type: tool
                path_glob: tools/govcon/**
                requires: [ICDEV_GOVCON_ENABLED]
            """,
        )
        mapping = en.load_enablement_map(map_file=m)
        assert len(mapping) == 2
        assert mapping[0]["entity_type"] == "canvas_module"
        assert "ICDEV_NDC_ENABLED" in mapping[0]["requires"]
        assert mapping[1]["entity_type"] == "tool"

    def test_missing_file_returns_empty(self, tmp_path):
        assert en.load_enablement_map(map_file=tmp_path / "nope.yaml") == []

    def test_malformed_yaml_returns_empty(self, tmp_path):
        m = tmp_path / "bad.yaml"
        m.write_text("this is: : : not: valid", encoding="utf-8")
        assert en.load_enablement_map(map_file=m) == []

    def test_skips_entries_missing_required_fields(self, tmp_path):
        m = tmp_path / "partial.yaml"
        self._write_map(
            m,
            """
            mappings:
              - entity_type: canvas_module
                path_glob: tools/network_canvas/**
                requires: [A_ENABLED]
              - entity_type: tool
                # missing path_glob
                requires: [B_ENABLED]
              - # missing entity_type
                path_glob: tools/bar/**
                requires: [C_ENABLED]
            """,
        )
        mapping = en.load_enablement_map(map_file=m)
        assert len(mapping) == 1
        assert mapping[0]["requires"] == ["A_ENABLED"]


class TestIsComponentEnabled:
    """is_component_enabled()"""

    def test_no_mapping_matches_defaults_to_enabled(self):
        result = en.is_component_enabled(
            "component-1",
            {"entity_type": "skill", "file_path": ".agents/skills/foo/SKILL.md"},
            flags={"ICDEV_NDC_ENABLED": False},
            mapping=[
                {
                    "entity_type": "canvas_module",
                    "path_glob": "tools/network_canvas/**",
                    "requires": ["ICDEV_NDC_ENABLED"],
                }
            ],
        )
        # skill is not in the mapping rule for canvas_module → default on
        assert result is True

    def test_matched_rule_with_all_flags_on_is_enabled(self):
        result = en.is_component_enabled(
            "c1",
            {"entity_type": "canvas_module", "file_path": "tools/network_canvas/foo.py"},
            flags={"ICDEV_NETWORK_ENABLED": True, "ICDEV_NDC_ENABLED": True},
            mapping=[
                {
                    "entity_type": "canvas_module",
                    "path_glob": "tools/network_canvas/**",
                    "requires": ["ICDEV_NETWORK_ENABLED", "ICDEV_NDC_ENABLED"],
                }
            ],
        )
        assert result is True

    def test_matched_rule_with_one_flag_off_is_disabled(self):
        result = en.is_component_enabled(
            "c2",
            {"entity_type": "canvas_module", "file_path": "tools/network_canvas/foo.py"},
            flags={"ICDEV_NETWORK_ENABLED": True, "ICDEV_NDC_ENABLED": False},
            mapping=[
                {
                    "entity_type": "canvas_module",
                    "path_glob": "tools/network_canvas/**",
                    "requires": ["ICDEV_NETWORK_ENABLED", "ICDEV_NDC_ENABLED"],
                }
            ],
        )
        assert result is False

    def test_missing_flag_treated_as_disabled(self):
        result = en.is_component_enabled(
            "c3",
            {"entity_type": "tool", "file_path": "tools/rag/retriever.py"},
            flags={},  # RAG_ENABLED is missing entirely
            mapping=[
                {
                    "entity_type": "tool",
                    "path_glob": "tools/rag/**",
                    "requires": ["RAG_ENABLED"],
                }
            ],
        )
        assert result is False

    def test_multiple_matching_rules_union_required_flags(self):
        mapping = [
            {
                "entity_type": "tool",
                "path_glob": "tools/rag/**",
                "requires": ["RAG_ENABLED"],
            },
            {
                "entity_type": "tool",
                "path_glob": "tools/rag/codebase_indexer.py",
                "requires": ["CODEBASE_INDEXER_ENABLED"],
            },
        ]
        # Both rules match → needs both flags
        all_on = en.is_component_enabled(
            "c4",
            {"entity_type": "tool", "file_path": "tools/rag/codebase_indexer.py"},
            flags={"RAG_ENABLED": True, "CODEBASE_INDEXER_ENABLED": True},
            mapping=mapping,
        )
        assert all_on is True

        one_off = en.is_component_enabled(
            "c4",
            {"entity_type": "tool", "file_path": "tools/rag/codebase_indexer.py"},
            flags={"RAG_ENABLED": True, "CODEBASE_INDEXER_ENABLED": False},
            mapping=mapping,
        )
        assert one_off is False

    def test_windows_backslash_paths_normalized(self):
        result = en.is_component_enabled(
            "c5",
            {
                "entity_type": "canvas_module",
                "file_path": "tools\\network_canvas\\foo.py",
            },
            flags={"ICDEV_NETWORK_ENABLED": True, "ICDEV_NDC_ENABLED": True},
            mapping=[
                {
                    "entity_type": "canvas_module",
                    "path_glob": "tools/network_canvas/**",
                    "requires": ["ICDEV_NETWORK_ENABLED", "ICDEV_NDC_ENABLED"],
                }
            ],
        )
        assert result is True


class TestIntegrationWithRealMap:
    """End-to-end test using the real args/awareness_enablement_map.yaml
    file shipped with Phase 1c. Verifies the seed mapping loads and
    resolves example components correctly."""

    def test_real_map_loads(self):
        mapping = en.load_enablement_map()
        # The seed map has at least the design canvases + RAG/GovCon
        assert len(mapping) >= 10
        entity_types = {m["entity_type"] for m in mapping}
        assert "canvas_module" in entity_types
        assert "tool" in entity_types
        assert "dashboard_route" in entity_types

    def test_real_map_gates_ndc_canvas_correctly(self):
        # Network canvas should be enabled when both flags are true
        result = en.is_component_enabled(
            "ndc-module",
            {
                "entity_type": "canvas_module",
                "file_path": "tools/network_canvas/agent.py",
            },
            flags={"ICDEV_NETWORK_ENABLED": True, "ICDEV_NDC_ENABLED": True},
        )
        assert result is True

    def test_real_map_skill_defaults_to_enabled(self):
        # Skills have no rule → always enabled
        result = en.is_component_enabled(
            "skill-foo",
            {"entity_type": "skill", "file_path": ".agents/skills/foo/SKILL.md"},
            flags={},
        )
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
