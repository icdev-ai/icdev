#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/translation/source_extractor.py — Phase 43 IR extraction."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.translation.source_extractor import (
    extract_source,
    build_dependency_graph,
    _extract_python,
    _extract_regex,
    _detect_python_idioms,
    _count_branches,
    SUPPORTED_LANGUAGES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def python_source(tmp_path):
    """Create a temporary Python source file for extraction."""
    code = '''# CUI // SP-CTI
"""Module docstring."""
import os
import json
from pathlib import Path

def hello(name: str) -> str:
    """Greet someone."""
    if name:
        return f"Hello, {name}!"
    return "Hello, World!"

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

class Calculator:
    """Simple calculator."""

    def __init__(self):
        self.history = []

    def multiply(self, x: float, y: float) -> float:
        result = x * y
        self.history.append(result)
        return result

    def divide(self, x: float, y: float) -> float:
        if y == 0:
            raise ValueError("Division by zero")
        return x / y
'''
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text(code, encoding="utf-8")
    return src_dir


@pytest.fixture
def java_source(tmp_path):
    """Create a temporary Java source file for regex extraction."""
    code = '''// CUI // SP-CTI
package com.icdev.calc;

import java.util.List;
import java.util.ArrayList;

public class Calculator {
    private List<Double> history = new ArrayList<>();

    public double add(double a, double b) {
        return a + b;
    }

    public double multiply(double a, double b) {
        double result = a * b;
        history.add(result);
        return result;
    }
}
'''
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "Calculator.java").write_text(code, encoding="utf-8")
    return src_dir


@pytest.fixture
def go_source(tmp_path):
    """Create a temporary Go source file."""
    code = '''// CUI // SP-CTI
package calc

import "fmt"

func Hello(name string) string {
    if name == "" {
        return "Hello, World!"
    }
    return fmt.Sprintf("Hello, %s!", name)
}

func Add(a int, b int) int {
    return a + b
}
'''
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calc.go").write_text(code, encoding="utf-8")
    return src_dir


# ---------------------------------------------------------------------------
# Tests: Python extraction (AST-based)
# ---------------------------------------------------------------------------

class TestPythonExtraction:
    """Tests for Python AST-based extraction."""

    def test_extract_python_functions(self, python_source):
        """Should extract functions from Python source."""
        ir = extract_source(str(python_source), "python")
        assert ir is not None
        assert "units" in ir
        assert len(ir["units"]) >= 2  # hello, add, Calculator

    def test_extract_python_class(self, python_source):
        """Should extract class with methods."""
        ir = extract_source(str(python_source), "python")
        class_units = [u for u in ir["units"] if u["kind"] == "class"]
        assert len(class_units) >= 1
        calc = class_units[0]
        assert calc["name"] == "Calculator"
        assert "methods" in calc
        assert len(calc["methods"]) >= 2  # multiply, divide (+ __init__)

    def test_extract_python_imports(self, python_source):
        """Should extract imports from Python source."""
        ir = extract_source(str(python_source), "python")
        assert "imports" in ir
        imports = ir["imports"]
        assert "os" in imports
        assert "json" in imports

    def test_extract_python_function_params(self, python_source):
        """Should extract function parameters with types."""
        ir = extract_source(str(python_source), "python")
        hello = next((u for u in ir["units"] if u["name"] == "hello"), None)
        assert hello is not None
        assert hello["kind"] == "function"
        assert len(hello["parameters"]) == 1
        assert hello["parameters"][0]["name"] == "name"
        assert hello["parameters"][0]["type"] == "str"

    def test_extract_python_return_type(self, python_source):
        """Should extract return type annotations."""
        ir = extract_source(str(python_source), "python")
        add = next((u for u in ir["units"] if u["name"] == "add"), None)
        assert add is not None
        assert add["return_type"] == "int"

    def test_extract_python_source_hash(self, python_source):
        """Should compute truncated SHA-256 hash of source code."""
        ir = extract_source(str(python_source), "python")
        for unit in ir["units"]:
            assert "source_hash" in unit
            assert len(unit["source_hash"]) == 16  # SHA-256 hex truncated to 16 chars

    def test_extract_python_complexity(self, python_source):
        """Should estimate cyclomatic complexity."""
        ir = extract_source(str(python_source), "python")
        hello = next((u for u in ir["units"] if u["name"] == "hello"), None)
        assert hello is not None
        assert hello.get("complexity", 0) >= 1  # has if branch

    def test_extract_python_empty_dir(self, tmp_path):
        """Should handle empty directory gracefully."""
        ir = extract_source(str(tmp_path), "python")
        assert ir is not None
        assert ir.get("units", []) == []


# ---------------------------------------------------------------------------
# Tests: Regex-based extraction (Java, Go)
# ---------------------------------------------------------------------------

class TestRegexExtraction:
    """Tests for regex-based extraction (non-Python languages)."""

    def test_extract_java(self, java_source):
        """Should extract Java classes and methods via regex."""
        ir = extract_source(str(java_source), "java")
        assert ir is not None
        units = ir.get("units", [])
        assert len(units) >= 1

    def test_extract_go(self, go_source):
        """Should extract Go functions via regex."""
        ir = extract_source(str(go_source), "go")
        assert ir is not None
        units = ir.get("units", [])
        assert len(units) >= 2  # Hello, Add

    def test_extract_unsupported_language(self, tmp_path):
        """Should handle unsupported language gracefully."""
        ir = extract_source(str(tmp_path), "brainfuck")
        assert ir is not None
        assert ir.get("units", []) == []


# ---------------------------------------------------------------------------
# Tests: Dependency graph
# ---------------------------------------------------------------------------

class TestDependencyGraph:
    """Tests for post-order dependency traversal (D244)."""

    def test_build_dependency_graph(self, python_source):
        """Should build dependency graph with leaf-first order."""
        ir = extract_source(str(python_source), "python")
        order = build_dependency_graph(ir["units"])
        assert isinstance(order, list)
        # All units should be in the order
        assert len(order) == len(ir["units"])

    def test_empty_ir_graph(self):
        """Should handle empty IR gracefully."""
        order = build_dependency_graph([])
        assert order == []


# ---------------------------------------------------------------------------
# Tests: Idiom detection
# ---------------------------------------------------------------------------

class TestIdiomDetection:
    """Tests for Python idiom detection."""

    def test_detect_list_comprehension(self):
        """Should detect list comprehension idiom."""
        code = "result = [x*2 for x in range(10)]"
        idioms = _detect_python_idioms(code)
        assert "list_comprehension" in idioms

    def test_detect_context_manager(self):
        """Should detect context manager idiom."""
        code = "with open('file') as f:\n    data = f.read()"
        idioms = _detect_python_idioms(code)
        assert "context_manager" in idioms

    def test_detect_decorator(self):
        """Should detect decorator idiom."""
        code = "@property\ndef name(self):\n    return self._name"
        idioms = _detect_python_idioms(code)
        assert "decorator" in idioms

    def test_detect_generator(self):
        """Should detect generator idiom."""
        code = "def gen():\n    yield 1\n    yield 2"
        idioms = _detect_python_idioms(code)
        assert "generator" in idioms

    def test_detect_async(self):
        """Should detect async idiom."""
        code = "async def fetch():\n    await response()"
        idioms = _detect_python_idioms(code)
        assert "async_function" in idioms


# ---------------------------------------------------------------------------
# Tests: Complexity counting
# ---------------------------------------------------------------------------

class TestComplexityCounting:
    """Tests for branch complexity estimation."""

    def test_count_simple(self):
        """Simple function should have low complexity."""
        code = "def add(a, b):\n    return a + b"
        assert _count_branches(code) >= 1

    def test_count_branches(self):
        """Function with branches should have higher complexity."""
        code = "def check(x):\n    if x > 0:\n        return 'pos'\n    elif x < 0:\n        return 'neg'\n    else:\n        return 'zero'"
        assert _count_branches(code) >= 3


# ---------------------------------------------------------------------------
# Tests: Supported languages
# ---------------------------------------------------------------------------

class TestSupportedLanguages:
    """Tests for language support constants."""

    def test_supported_languages(self):
        """Should have all 7 supported languages."""
        assert "python" in SUPPORTED_LANGUAGES
        assert "java" in SUPPORTED_LANGUAGES
        assert "go" in SUPPORTED_LANGUAGES
        assert "rust" in SUPPORTED_LANGUAGES
        assert "csharp" in SUPPORTED_LANGUAGES
        assert "typescript" in SUPPORTED_LANGUAGES

    def test_language_extensions(self):
        """Each language should have file extensions defined."""
        for lang, exts in SUPPORTED_LANGUAGES.items():
            assert isinstance(exts, (list, tuple))
            assert len(exts) >= 1
