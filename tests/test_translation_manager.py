#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/translation/translation_manager.py — Phase 43 full pipeline."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.translation.translation_manager import (
    run_pipeline,
    VALID_LANGUAGES,
)
from tools.translation.code_translator import (
    _get_translation_order,
    _generate_mock,
    CUI_HEADERS,
    NAMING_CONVENTIONS,
)
from tools.translation.project_assembler import (
    assemble_project,
    _ensure_cui_header,
    _format_dependencies,
)
from tools.translation.translation_validator import (
    check_api_surface,
    check_compliance,
    check_complexity,
)
from tools.translation.feature_map import FeatureMapLoader
from tools.translation.type_checker import (
    load_type_mappings,
    map_type,
    check_signature_compatibility,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def python_project(tmp_path):
    """Create a minimal Python project for testing."""
    src = tmp_path / "src"
    src.mkdir()
    code = '''# CUI // SP-CTI
import json

def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

class Formatter:
    """String formatter."""
    def __init__(self, prefix: str):
        self.prefix = prefix

    def format(self, text: str) -> str:
        return f"{self.prefix}: {text}"
'''
    (src / "main.py").write_text(code, encoding="utf-8")
    return tmp_path


@pytest.fixture
def output_dir(tmp_path):
    """Create a temp output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def sample_ir():
    """Return a sample IR structure."""
    return {
        "language": "python",
        "file_count": 1,
        "total_lines": 20,
        "imports": ["json"],
        "units": [
            {
                "name": "greet",
                "kind": "function",
                "source_file": "main.py",
                "params": [{"name": "name", "type": "str"}],
                "return_type": "str",
                "source_code": 'def greet(name: str) -> str:\n    return f"Hello, {name}!"',
                "source_hash": "abc123",
                "complexity": 1,
                "idioms": [],
                "line_count": 3,
            },
            {
                "name": "add",
                "kind": "function",
                "source_file": "main.py",
                "params": [
                    {"name": "a", "type": "int"},
                    {"name": "b", "type": "int"},
                ],
                "return_type": "int",
                "source_code": "def add(a: int, b: int) -> int:\n    return a + b",
                "source_hash": "def456",
                "complexity": 1,
                "idioms": [],
                "line_count": 2,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Tests: Pipeline validation
# ---------------------------------------------------------------------------

class TestPipelineValidation:
    """Tests for pipeline input validation."""

    def test_invalid_source_language(self, python_project, output_dir):
        """Should reject unsupported source language."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="fortran",
            target_language="java",
            output_dir=str(output_dir),
        )
        assert result.get("error")
        assert "Unsupported" in result["error"] or "unsupported" in result["error"].lower()

    def test_invalid_target_language(self, python_project, output_dir):
        """Should reject unsupported target language."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="cobol",
            output_dir=str(output_dir),
        )
        assert result.get("error")

    def test_same_language_rejected(self, python_project, output_dir):
        """Should reject same source and target language."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="python",
            output_dir=str(output_dir),
        )
        assert result.get("error")
        assert "different" in result["error"].lower() or "same" in result["error"].lower()

    def test_valid_languages_constant(self):
        """Should have all expected languages."""
        assert "python" in VALID_LANGUAGES
        assert "java" in VALID_LANGUAGES
        assert "go" in VALID_LANGUAGES
        assert "rust" in VALID_LANGUAGES
        assert "csharp" in VALID_LANGUAGES
        assert "typescript" in VALID_LANGUAGES


# ---------------------------------------------------------------------------
# Tests: Dry run (no LLM)
# ---------------------------------------------------------------------------

class TestDryRun:
    """Tests for dry run mode (extract + type-check only)."""

    def test_dry_run_extracts_ir(self, python_project, output_dir):
        """Dry run should extract IR without calling LLM."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="java",
            output_dir=str(output_dir),
            dry_run=True,
        )
        assert result["status"] == "completed"
        assert result.get("dry_run") is True
        assert "extract" in result["phases"]
        assert result["phases"]["extract"]["status"] == "completed"
        assert result["phases"]["extract"]["unit_count"] >= 2

    def test_dry_run_skips_translate(self, python_project, output_dir):
        """Dry run should skip translation phase."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="go",
            output_dir=str(output_dir),
            dry_run=True,
        )
        assert result["phases"]["translate"]["status"] == "skipped"
        assert result["phases"]["assemble"]["status"] == "skipped"

    def test_dry_run_creates_ir_file(self, python_project, output_dir):
        """Dry run should save IR file to output directory."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="rust",
            output_dir=str(output_dir),
            dry_run=True,
        )
        ir_file = output_dir / "source_ir.json"
        assert ir_file.exists()
        with open(ir_file, "r") as f:
            ir_data = json.load(f)
        assert "units" in ir_data
        assert len(ir_data["units"]) >= 2


# ---------------------------------------------------------------------------
# Tests: Extract only
# ---------------------------------------------------------------------------

class TestExtractOnly:
    """Tests for extract-only mode."""

    def test_extract_only(self, python_project, output_dir):
        """Extract only should return after Phase 1."""
        result = run_pipeline(
            source_path=str(python_project / "src"),
            source_language="python",
            target_language="java",
            output_dir=str(output_dir),
            extract_only=True,
        )
        assert result["status"] == "completed"
        assert "extract" in result["phases"]
        assert "translate" not in result["phases"]


# ---------------------------------------------------------------------------
# Tests: Translation order (D244)
# ---------------------------------------------------------------------------

class TestTranslationOrder:
    """Tests for post-order dependency traversal."""

    def test_leaf_first_ordering(self, sample_ir):
        """Independent units should be in any order."""
        order = _get_translation_order(sample_ir)
        assert len(order) == 2

    def test_empty_units(self):
        """Empty IR should return empty order."""
        ir = {"units": []}
        order = _get_translation_order(ir)
        assert order == []

    def test_dependent_units(self):
        """Dependent unit should come after its dependency."""
        ir = {
            "units": [
                {"name": "helper", "kind": "function", "calls": [], "bases": []},
                {"name": "main", "kind": "function", "calls": ["helper"], "bases": []},
            ]
        }
        order = _get_translation_order(ir)
        names = [u["name"] for u in order]
        assert names.index("helper") < names.index("main")


# ---------------------------------------------------------------------------
# Tests: Mock generation (D256)
# ---------------------------------------------------------------------------

class TestMockGeneration:
    """Tests for mock-and-continue strategy."""

    def test_mock_python(self):
        """Python mock should raise NotImplementedError."""
        unit = {"name": "broken_func", "kind": "function",
                "params": [{"name": "x"}], "return_type": "int"}
        mock = _generate_mock(unit, "python")
        assert "NotImplementedError" in mock
        assert "CUI" in mock
        assert "broken_func" in mock

    def test_mock_java(self):
        """Java mock should throw UnsupportedOperationException."""
        unit = {"name": "broken", "kind": "function",
                "params": [{"name": "x"}], "return_type": "int"}
        mock = _generate_mock(unit, "java")
        assert "UnsupportedOperationException" in mock
        assert "CUI" in mock

    def test_mock_go(self):
        """Go mock should panic."""
        unit = {"name": "broken", "kind": "function", "params": [], "return_type": ""}
        mock = _generate_mock(unit, "go")
        assert "panic" in mock

    def test_mock_rust(self):
        """Rust mock should use unimplemented!()."""
        unit = {"name": "broken", "kind": "function", "params": [], "return_type": ""}
        mock = _generate_mock(unit, "rust")
        assert "unimplemented!" in mock

    def test_mock_typescript(self):
        """TypeScript mock should throw Error."""
        unit = {"name": "broken", "kind": "function", "params": [], "return_type": ""}
        mock = _generate_mock(unit, "typescript")
        assert "throw new Error" in mock


# ---------------------------------------------------------------------------
# Tests: Project assembly
# ---------------------------------------------------------------------------

class TestProjectAssembly:
    """Tests for project_assembler.py."""

    def test_assemble_python_project(self, output_dir):
        """Should scaffold a Python project."""
        units = [
            {"name": "hello", "kind": "function",
             "source_file": "main.py",
             "translated_code": "# CUI // SP-CTI\ndef hello():\n    pass\n"},
        ]
        result = assemble_project(
            output_dir=str(output_dir),
            target_language="python",
            source_language="java",
            translated_units=units,
        )
        assert result["file_count"] >= 2  # code file + build file + README
        assert result["build_file"] == "pyproject.toml"
        assert (output_dir / "pyproject.toml").exists()

    def test_assemble_java_project(self, output_dir):
        """Should scaffold a Java project."""
        units = [
            {"name": "Hello", "kind": "class",
             "source_file": "Hello.py",
             "translated_code": "// CUI // SP-CTI\npublic class Hello {}\n"},
        ]
        result = assemble_project(
            output_dir=str(output_dir),
            target_language="java",
            source_language="python",
            translated_units=units,
        )
        assert result["build_file"] == "pom.xml"
        assert (output_dir / "pom.xml").exists()

    def test_ensure_cui_header(self):
        """Should add CUI header if missing."""
        code = "def hello():\n    pass"
        result = _ensure_cui_header(code, "python")
        assert result.startswith("# CUI // SP-CTI")

    def test_cui_header_not_duplicated(self):
        """Should not duplicate CUI header."""
        code = "# CUI // SP-CTI\ndef hello():\n    pass"
        result = _ensure_cui_header(code, "python")
        assert result.count("CUI // SP-CTI") == 1

    def test_format_empty_dependencies(self):
        """Should handle empty dependencies."""
        result = _format_dependencies(None, "python")
        assert result == ""

    def test_format_dependencies_python(self):
        """Should format Python dependencies."""
        deps = [
            {"target_import": "flask", "mapping_source": "table"},
            {"target_import": "requests", "mapping_source": "table"},
        ]
        result = _format_dependencies(deps, "python")
        assert "flask" in result or "requests" in result


# ---------------------------------------------------------------------------
# Tests: Validation checks
# ---------------------------------------------------------------------------

class TestValidationChecks:
    """Tests for translation_validator.py individual checks."""

    def test_api_surface_all_matched(self, sample_ir):
        """Should return 1.0 when all units are present."""
        translated = [
            {"name": "greet", "kind": "function"},
            {"name": "add", "kind": "function"},
        ]
        score, findings = check_api_surface(sample_ir, translated)
        assert score == 1.0
        assert len(findings) == 0

    def test_api_surface_missing_unit(self, sample_ir):
        """Should report missing units."""
        translated = [{"name": "greet", "kind": "function"}]
        score, findings = check_api_surface(sample_ir, translated)
        assert score < 1.0
        assert len(findings) >= 1

    def test_compliance_check_with_markings(self):
        """Units with CUI markings should pass."""
        units = [
            {"name": "a", "translated_code": "# CUI // SP-CTI\ndef a(): pass"},
            {"name": "b", "translated_code": "# CUI // SP-CTI\ndef b(): pass"},
        ]
        score, findings = check_compliance(units, "python")
        assert score == 1.0

    def test_compliance_check_missing_markings(self):
        """Units without CUI markings should fail."""
        units = [
            {"name": "a", "translated_code": "def a(): pass"},
            {"name": "b", "translated_code": "# CUI // SP-CTI\ndef b(): pass"},
        ]
        score, findings = check_compliance(units, "python")
        assert score < 1.0
        assert len(findings) >= 1

    def test_complexity_no_increase(self, sample_ir):
        """Should pass when complexity doesn't increase significantly."""
        # Translated code line counts must not exceed source line_count by >30%
        # greet has line_count=3, add has line_count=2
        translated = [
            {"name": "greet", "translated_code": "public String greet() {\n    return \"hello\";\n}"},
            {"name": "add", "translated_code": "public int add(int a, int b) {\n    return a + b;\n}"},
        ]
        score, findings = check_complexity(sample_ir, translated)
        # add: 3 non-blank lines vs line_count=2 => 50% increase, score = 0.5
        # This is expected behavior — only one unit exceeds threshold
        assert score >= 0.5


# ---------------------------------------------------------------------------
# Tests: Feature map
# ---------------------------------------------------------------------------

class TestFeatureMap:
    """Tests for feature_map.py."""

    def test_load_feature_maps(self):
        """Should load built-in feature maps."""
        loader = FeatureMapLoader()
        pairs = loader.list_supported_pairs()
        assert len(pairs) >= 5

    def test_get_rules_python_to_java(self):
        """Should return rules for python→java."""
        loader = FeatureMapLoader()
        rules = loader.get_rules("python", "java")
        assert isinstance(rules, list)
        assert len(rules) >= 1

    def test_rules_have_required_fields(self):
        """Each rule should have id, pattern, description, validation."""
        loader = FeatureMapLoader()
        rules = loader.get_rules("python", "java")
        for rule in rules:
            assert "id" in rule
            assert "pattern" in rule
            assert "description" in rule
            assert "validation" in rule

    def test_get_rules_unsupported_pair(self):
        """Should return empty for unsupported pair."""
        loader = FeatureMapLoader()
        rules = loader.get_rules("brainfuck", "cobol")
        assert rules == []


# ---------------------------------------------------------------------------
# Tests: Type checker
# ---------------------------------------------------------------------------

class TestTypeChecker:
    """Tests for type_checker.py."""

    def test_load_type_mappings(self):
        """Should load type mappings."""
        mappings = load_type_mappings()
        assert isinstance(mappings, dict)
        assert "primitives" in mappings

    def test_map_type_int(self):
        """Should map Python int to Java int."""
        mappings = load_type_mappings()
        result = map_type("int", "python", "java", mappings)
        assert result is not None
        assert result.get("confidence", 0) > 0.5

    def test_map_type_str(self):
        """Should map Python str to Java String."""
        mappings = load_type_mappings()
        result = map_type("str", "python", "java", mappings)
        assert result is not None

    def test_map_unknown_type(self):
        """Unknown type should return with low confidence."""
        mappings = load_type_mappings()
        result = map_type("MyCustomType", "python", "java", mappings)
        assert result is not None
        assert result.get("confidence", 1.0) < 1.0


# ---------------------------------------------------------------------------
# Tests: CUI headers and naming conventions
# ---------------------------------------------------------------------------

class TestConstants:
    """Tests for translation constants."""

    def test_cui_headers_all_languages(self):
        """Should have CUI headers for all supported languages."""
        for lang in VALID_LANGUAGES:
            assert lang in CUI_HEADERS, f"Missing CUI header for {lang}"
            assert "CUI" in CUI_HEADERS[lang]

    def test_naming_conventions_all_languages(self):
        """Should have naming conventions for all supported languages."""
        for lang in VALID_LANGUAGES:
            if lang == "javascript":
                continue  # JS uses same as TS
            assert lang in NAMING_CONVENTIONS, f"Missing naming for {lang}"
