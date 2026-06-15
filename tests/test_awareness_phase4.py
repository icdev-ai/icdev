# CUI // SP-CTI
"""Phase 4 tests — RAG/KG source registration + ask-icdev Q&A flow.

Most of Phase 4 lives in tools/dashboard/app.py (route handlers) and
is validated via live dashboard E2E rather than unit tests. These
tests cover:

  * SOURCE_REGISTRY has the 10 new icdev_* entries with correct shape
  * graph_rag SCORING_PROFILES and PROFILE_KEYWORDS include the
    internal_awareness entry
  * Each entry's filter clause is well-formed
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.knowledge_graph import graph_rag  # noqa: E402
from tools.rag.source_registry import SOURCE_REGISTRY  # noqa: E402


EXPECTED_ICDEV_SOURCES = {
    "icdev_codebase_files",
    "icdev_skills",
    "icdev_mcp_servers",
    "icdev_canvas_modules",
    "icdev_goals",
    "icdev_tools",
    "icdev_a2a_agents",
    "icdev_dashboard_routes",
    "icdev_reflexes",
    "icdev_db_tables",
    "icdev_tool_categories",
}


class TestSourceRegistryPhase4:
    def test_all_icdev_sources_present(self):
        missing = EXPECTED_ICDEV_SOURCES - set(SOURCE_REGISTRY.keys())
        assert not missing, f"missing icdev_* sources: {missing}"

    def test_icdev_sources_point_at_kg_nodes(self):
        # Everything except icdev_codebase_files reads from kg_nodes
        for name in EXPECTED_ICDEV_SOURCES - {"icdev_codebase_files"}:
            entry = SOURCE_REGISTRY[name]
            assert entry["table"] == "kg_nodes", f"{name} should read from kg_nodes"
            assert entry["db"] == "icdev"
            assert entry["pk"] == "id"
            assert "label" in entry["content_cols"]
            assert "filter" in entry, f"{name} missing filter clause"
            assert "kg-icdev-self-awareness" in entry["filter"]

    def test_icdev_sources_have_unique_entity_type_filters(self):
        # Each icdev_* source targets a distinct entity_type
        entity_types = set()
        for name in EXPECTED_ICDEV_SOURCES - {"icdev_codebase_files"}:
            entry = SOURCE_REGISTRY[name]
            filt = entry["filter"]
            # Extract entity_type from the filter string
            assert "entity_type" in filt
            # Pull the quoted value after "entity_type = "
            idx = filt.find("entity_type = '")
            if idx >= 0:
                end = filt.find("'", idx + 16)
                et = filt[idx + 16:end]
                entity_types.add(et)
        assert len(entity_types) >= 10, f"expected 10+ unique types, got {len(entity_types)}"

    def test_icdev_codebase_files_unchanged(self):
        entry = SOURCE_REGISTRY["icdev_codebase_files"]
        assert entry["table"] == "codebase_index"
        assert entry["pk"] == "id"


class TestGraphRagInternalAwarenessProfile:
    def test_profile_registered(self):
        assert "internal_awareness" in graph_rag.SCORING_PROFILES
        profile = graph_rag.SCORING_PROFILES["internal_awareness"]
        # Four weights: edge_weight, centrality, recency, ontology_weight
        assert set(profile.keys()) == {"edge_weight", "centrality", "recency", "ontology_weight"}
        total = sum(profile.values())
        assert abs(total - 1.0) < 0.01, f"weights should sum to 1.0, got {total}"

    def test_profile_keywords_registered(self):
        assert "internal_awareness" in graph_rag.PROFILE_KEYWORDS
        keywords = graph_rag.PROFILE_KEYWORDS["internal_awareness"]
        assert len(keywords) >= 20
        # Should include the obvious terms
        assert "icdev" in keywords
        assert "component" in keywords
        assert "canvas" in keywords
        assert "skill" in keywords
        assert "reflex" in keywords

    def test_keyword_auto_detection_would_fire(self):
        """Simulate a query that should auto-route to internal_awareness."""
        query = "how does the icdev network canvas component work"
        keywords = graph_rag.PROFILE_KEYWORDS["internal_awareness"]
        matches = sum(1 for kw in keywords if kw in query.lower())
        # Should hit at least a couple of the keywords
        assert matches >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
