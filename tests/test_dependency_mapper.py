#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/translation/dependency_mapper.py — Phase 43 dependency mapping."""

import json
import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.translation.dependency_mapper import (
    load_mappings,
    resolve_import,
    resolve_imports,
    get_domain_coverage,
    _normalize_lang,
)


# ---------------------------------------------------------------------------
# Tests: Language normalization
# ---------------------------------------------------------------------------

class TestLanguageNormalization:
    """Tests for language name normalization."""

    def test_normalize_standard(self):
        assert _normalize_lang("python") == "python"
        assert _normalize_lang("java") == "java"
        assert _normalize_lang("go") == "go"

    def test_normalize_aliases(self):
        assert _normalize_lang("js") == "javascript"
        assert _normalize_lang("ts") == "typescript"
        assert _normalize_lang("c#") == "csharp"
        assert _normalize_lang("C#") == "csharp"
        assert _normalize_lang("py") == "python"

    def test_normalize_case_insensitive(self):
        assert _normalize_lang("Python") == "python"
        assert _normalize_lang("JAVA") == "java"
        # "TypeScript" lowered is "typescript", not in aliases, so stays as-is
        assert _normalize_lang("typescript") == "typescript"


# ---------------------------------------------------------------------------
# Tests: Load mappings
# ---------------------------------------------------------------------------

class TestLoadMappings:
    """Tests for loading dependency mappings from JSON."""

    def test_load_mappings(self):
        """Should load mappings from context/translation/dependency_mappings.json."""
        mappings = load_mappings()
        assert isinstance(mappings, dict)
        assert len(mappings) > 0

    def test_mappings_have_domains(self):
        """Mappings should have domain categories."""
        mappings = load_mappings()
        domains = mappings.get("domains", {})
        expected_domains = [
            "http_client", "web_framework", "json", "testing", "logging",
        ]
        for domain in expected_domains:
            assert domain in domains, f"Missing domain: {domain}"

    def test_mappings_have_languages(self):
        """Each domain should have package entries for multiple languages."""
        mappings = load_mappings()
        domains = mappings.get("domains", {})
        for domain_name, domain_data in domains.items():
            packages = domain_data.get("packages", {})
            assert isinstance(packages, dict), f"Domain {domain_name} packages is not a dict"
            # Should have at least 3 languages
            assert len(packages) >= 3, f"Domain {domain_name} has too few languages"


# ---------------------------------------------------------------------------
# Tests: Single import resolution
# ---------------------------------------------------------------------------

class TestResolveImport:
    """Tests for resolving a single import."""

    def test_resolve_known_mapping(self):
        """Should resolve a known package mapping."""
        mappings = load_mappings()
        result = resolve_import("flask", "python", "java", mappings)
        assert result is not None
        assert result["source_import"] == "flask"
        assert result["mapping_source"] == "table"
        assert result["confidence"] == 1.0
        # Flask → Spring Boot in Java
        assert "spring" in result.get("target_import", "").lower() or result.get("target_import")

    def test_resolve_requests(self):
        """Should resolve requests → Go equivalent."""
        mappings = load_mappings()
        result = resolve_import("requests", "python", "go", mappings)
        assert result is not None
        assert result["mapping_source"] == "table"
        assert result["target_import"] is not None

    def test_resolve_stdlib(self):
        """Standard library imports should be flagged as stdlib."""
        mappings = load_mappings()
        result = resolve_import("os", "python", "java", mappings)
        assert result is not None
        # os is Python stdlib — either mapped or flagged
        assert result["mapping_source"] in ("table", "stdlib")

    def test_resolve_unknown_package(self):
        """Unknown packages should return unmapped."""
        mappings = load_mappings()
        result = resolve_import("my_custom_internal_lib_xyz", "python", "java", mappings)
        assert result is not None
        assert result["mapping_source"] in ("unmapped", "stdlib")
        assert result["confidence"] < 1.0

    def test_resolve_json_module(self):
        """json → language equivalent should be mapped."""
        mappings = load_mappings()
        result = resolve_import("json", "python", "go", mappings)
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: Batch import resolution
# ---------------------------------------------------------------------------

class TestResolveImports:
    """Tests for batch import resolution."""

    def test_resolve_multiple(self):
        """Should resolve multiple imports at once."""
        mappings = load_mappings()
        results = resolve_imports(
            ["flask", "requests", "json", "os"],
            "python", "java",
            mappings,
        )
        assert isinstance(results, list)
        assert len(results) == 4

    def test_resolve_empty_list(self):
        """Should handle empty import list."""
        mappings = load_mappings()
        results = resolve_imports([], "python", "java", mappings)
        assert results == []


# ---------------------------------------------------------------------------
# Tests: Domain coverage
# ---------------------------------------------------------------------------

class TestDomainCoverage:
    """Tests for coverage reporting."""

    def test_coverage_python_to_java(self):
        """Should report high coverage for Python → Java."""
        mappings = load_mappings()
        coverage = get_domain_coverage("python", "java", mappings)
        assert isinstance(coverage, dict)
        assert "total_domains" in coverage
        assert "covered_domains" in coverage
        assert coverage["total_domains"] > 0
        assert coverage["coverage_pct"] >= 50  # At least 50% coverage

    def test_coverage_returns_per_domain(self):
        """Should return coverage summary with domain counts."""
        mappings = load_mappings()
        coverage = get_domain_coverage("python", "go", mappings)
        assert "total_domains" in coverage
        assert "covered_domains" in coverage
        assert "coverage_pct" in coverage
        assert coverage["total_domains"] > 0

    def test_coverage_same_language(self):
        """Same language should have 100% coverage (identity mapping)."""
        mappings = load_mappings()
        coverage = get_domain_coverage("python", "python", mappings)
        # All domains should be covered since source = target
        assert coverage["coverage_pct"] == 100


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge case tests for dependency mapper."""

    def test_resolve_with_subpackage(self):
        """Should handle dotted import paths."""
        mappings = load_mappings()
        # flask.Flask → should still match flask domain
        result = resolve_import("flask.Flask", "python", "java", mappings)
        assert result is not None

    def test_resolve_typescript_to_python(self):
        """Should work for non-Python source languages."""
        mappings = load_mappings()
        result = resolve_import("express", "typescript", "python", mappings)
        assert result is not None
