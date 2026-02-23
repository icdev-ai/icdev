#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 semantic layer MCP tools (Feature 6 — D277).

Covers: ClaudeMdIndexer section parsing, hierarchy, keyword search,
role mapping, cache refresh, empty/missing CLAUDE.md, table of contents.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.mcp.context_indexer import ClaudeMdIndexer


SAMPLE_CLAUDE_MD = """# CLAUDE.md

This file provides guidance.

## Quick Reference

### Commands
```bash
pytest tests/ -v
```

Some commands here.

## Architecture: GOTCHA Framework

This is a 6-layer agentic system.

### The 6 Layers

| Layer | Directory | Role |
|-------|-----------|------|
| Goals | goals/ | Process definitions |

## Testing Framework

```bash
pytest tests/ -v --tb=short
```

Testing architecture section content.

## Compliance Frameworks Supported

Many frameworks here including NIST and FedRAMP.

## Security Gates (Blocking Conditions)

- Code Review Gate: stuff
- Merge Gate: stuff

## Supported Languages (6 First-Class)

Python, Java, Go, Rust, C#, TypeScript.

## ICDEV Commands

Various commands.
"""


@pytest.fixture
def indexer():
    """Create indexer from a sample CLAUDE.md."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_CLAUDE_MD)
        f.flush()
        return ClaudeMdIndexer(claude_md_path=Path(f.name))


@pytest.fixture
def empty_indexer():
    """Create indexer from non-existent path."""
    return ClaudeMdIndexer(claude_md_path=Path("/nonexistent/CLAUDE.md"))


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

class TestSectionParsing:
    def test_sections_found(self, indexer):
        assert indexer.section_count > 0

    def test_top_level_sections(self, indexer):
        names = indexer.section_names
        assert "Quick Reference" in names
        assert "Architecture: GOTCHA Framework" in names
        assert "Testing Framework" in names

    def test_subsections_found(self, indexer):
        names = indexer.section_names
        assert "Commands" in names
        assert "The 6 Layers" in names


# ---------------------------------------------------------------------------
# get_section
# ---------------------------------------------------------------------------

class TestGetSection:
    def test_exact_match(self, indexer):
        content = indexer.get_section("Testing Framework")
        assert content is not None
        assert "pytest" in content

    def test_case_insensitive(self, indexer):
        content = indexer.get_section("testing framework")
        assert content is not None

    def test_partial_match(self, indexer):
        content = indexer.get_section("GOTCHA")
        assert content is not None
        assert "6-layer" in content

    def test_not_found(self, indexer):
        content = indexer.get_section("Nonexistent Section XYZ")
        assert content is None


# ---------------------------------------------------------------------------
# search_sections
# ---------------------------------------------------------------------------

class TestSearchSections:
    def test_keyword_in_header(self, indexer):
        results = indexer.search_sections("Testing")
        assert len(results) >= 1
        assert "Testing Framework" in results

    def test_keyword_in_content(self, indexer):
        results = indexer.search_sections("pytest")
        assert len(results) >= 1

    def test_compliance_keyword(self, indexer):
        results = indexer.search_sections("compliance")
        assert len(results) >= 1

    def test_no_match(self, indexer):
        results = indexer.search_sections("zzzznonexistentkeyword")
        assert results == []


# ---------------------------------------------------------------------------
# get_toc
# ---------------------------------------------------------------------------

class TestTableOfContents:
    def test_toc_structure(self, indexer):
        toc = indexer.get_toc()
        assert len(toc) > 0
        for entry in toc:
            assert "name" in entry
            assert "level" in entry
            assert "line_number" in entry

    def test_toc_levels(self, indexer):
        toc = indexer.get_toc()
        levels = {e["level"] for e in toc}
        assert 2 in levels  # ## headers
        assert 3 in levels  # ### headers


# ---------------------------------------------------------------------------
# get_sections_for_role
# ---------------------------------------------------------------------------

class TestRoleSections:
    def test_builder_role(self, indexer):
        content = indexer.get_sections_for_role("builder")
        assert len(content) > 0
        # Should contain language or testing content
        assert "Testing" in content or "Language" in content or "pytest" in content

    def test_compliance_role(self, indexer):
        content = indexer.get_sections_for_role("compliance")
        assert len(content) > 0

    def test_unknown_role(self, indexer):
        content = indexer.get_sections_for_role("unknown-role")
        assert content == ""

    def test_security_role(self, indexer):
        content = indexer.get_sections_for_role("security")
        assert len(content) > 0


# ---------------------------------------------------------------------------
# Empty / missing CLAUDE.md
# ---------------------------------------------------------------------------

class TestEmptyClaude:
    def test_missing_file(self, empty_indexer):
        assert empty_indexer.section_count == 0
        assert empty_indexer.get_section("anything") is None
        assert empty_indexer.search_sections("anything") == []
        assert empty_indexer.get_toc() == []

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            f.flush()
            indexer = ClaudeMdIndexer(claude_md_path=Path(f.name))
            assert indexer.section_count == 0


# ---------------------------------------------------------------------------
# Cache refresh
# ---------------------------------------------------------------------------

class TestCacheRefresh:
    def test_refresh_on_mtime_change(self):
        import time
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("## Section A\nContent A\n")
            f.flush()
            indexer = ClaudeMdIndexer(claude_md_path=Path(f.name))
            assert "Section A" in indexer.section_names

        # Modify file
        time.sleep(0.1)
        with open(f.name, "w", encoding="utf-8") as f2:
            f2.write("## Section B\nContent B\n")

        # Force refresh via access
        names = indexer.section_names
        assert "Section B" in names
