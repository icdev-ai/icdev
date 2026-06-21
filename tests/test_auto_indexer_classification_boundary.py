#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for auto_indexer classification-boundary constants (AI-ify 5458/5459).

AI-ify opportunities 5458 (line ~345) and 5459 (line ~348) flagged the bare
numeric comparisons ``project_level < 2`` / ``project_level <= 1`` in
``AutoIndexer._is_above_classification`` as ``hardcoded_threshold ->
anomaly_detection``. The classification check is a deterministic NIST/CUI
security boundary (audit + classification controls must never be probabilistic),
so the resolution was to remove the magic numbers by deriving named boundary
constants from the classification hierarchy — not to introduce ML anomaly
detection. These tests pin both the named constants and the boundary behavior.
"""

from __future__ import annotations

from tools.rag.auto_indexer import (
    _CLASSIFICATION_LEVELS,
    _CUI_LEVEL,
    _DEFAULT_CLASSIFICATION_LEVEL,
    _SECRET_LEVEL,
    AutoIndexer,
)


# ---------------------------------------------------------------------------
# Named boundary constants
# ---------------------------------------------------------------------------


class TestClassificationConstants:
    def test_hierarchy_is_monotonic(self):
        assert _CLASSIFICATION_LEVELS["UNCLASSIFIED"] < _CLASSIFICATION_LEVELS["CUI"]
        assert _CLASSIFICATION_LEVELS["CUI"] < _CLASSIFICATION_LEVELS["SECRET"]
        assert _CLASSIFICATION_LEVELS["SECRET"] < _CLASSIFICATION_LEVELS["TOP SECRET"]
        assert _CLASSIFICATION_LEVELS["TS"] == _CLASSIFICATION_LEVELS["TOP SECRET"]

    def test_named_constants_derive_from_hierarchy(self):
        # The constants that replaced the magic numbers must track the hierarchy.
        assert _CUI_LEVEL == _CLASSIFICATION_LEVELS["CUI"]
        assert _SECRET_LEVEL == _CLASSIFICATION_LEVELS["SECRET"]
        assert _DEFAULT_CLASSIFICATION_LEVEL == _CLASSIFICATION_LEVELS["CUI"]

    def test_named_constants_match_former_literals(self):
        # Behavior-preserving: the old code used `< 2` (SECRET) and `<= 1` (CUI).
        assert _SECRET_LEVEL == 2
        assert _CUI_LEVEL == 1


# ---------------------------------------------------------------------------
# Boundary behavior (deterministic, no ML)
# ---------------------------------------------------------------------------


class TestIsAboveClassification:
    def _indexer(self, classification: str) -> AutoIndexer:
        return AutoIndexer(project_dir=".", classification=classification)

    def test_cui_project_blocks_secret(self, tmp_path):
        p = tmp_path / "doc.md"
        p.write_text("// SECRET\nbody", encoding="utf-8")
        assert self._indexer("CUI")._is_above_classification(p) is True

    def test_cui_project_blocks_noforn_sci_markers(self, tmp_path):
        for marker in ("//NOFORN", "//SAP", "//SCI", "//HCS", "//SI", "//TK"):
            p = tmp_path / f"doc_{marker.strip('/')}.md"
            p.write_text(f"header {marker} more", encoding="utf-8")
            assert self._indexer("CUI")._is_above_classification(p) is True, marker

    def test_cui_project_allows_plain_cui(self, tmp_path):
        p = tmp_path / "ok.md"
        p.write_text("// CUI\nordinary content", encoding="utf-8")
        assert self._indexer("CUI")._is_above_classification(p) is False

    def test_top_secret_marker_always_blocked(self, tmp_path):
        p = tmp_path / "ts.md"
        p.write_text("// TOP SECRET\nx", encoding="utf-8")
        assert self._indexer("CUI")._is_above_classification(p) is True

    def test_secret_project_does_not_block_secret(self, tmp_path):
        # Project already at SECRET tier -> nothing below TS is blocked, and the
        # CUI-tier NOFORN block must NOT fire (project_level > _CUI_LEVEL).
        p = tmp_path / "doc.md"
        p.write_text("//NOFORN\nbody", encoding="utf-8")
        assert self._indexer("SECRET")._is_above_classification(p) is False
