#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 dangerous pattern detection (Feature 9 — D278).

Covers: per-language patterns with known-dangerous snippets, universal patterns,
language auto-detection, directory scanning, content scanning, gate evaluation,
clean files, supported languages.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.security.code_pattern_scanner import CodePatternScanner, EXTENSION_MAP, SKIP_DIRS


@pytest.fixture
def scanner():
    return CodePatternScanner()


# ---------------------------------------------------------------------------
# Universal patterns
# ---------------------------------------------------------------------------

class TestUniversalPatterns:
    def test_eval_detected(self, scanner):
        result = scanner.scan_content("result = eval(user_input)", "python")
        assert result["findings_count"] >= 1
        assert any(f["name"] == "eval_usage" for f in result["findings"])

    def test_exec_detected(self, scanner):
        result = scanner.scan_content("exec(code_string)", "python")
        assert any(f["name"] == "exec_usage" for f in result["findings"])

    def test_subprocess_detected(self, scanner):
        result = scanner.scan_content("subprocess.call(['ls'])", "python")
        assert any(f["name"] == "subprocess_spawn" for f in result["findings"])


# ---------------------------------------------------------------------------
# Python patterns
# ---------------------------------------------------------------------------

class TestPythonPatterns:
    def test_os_system(self, scanner):
        result = scanner.scan_content("os.system('rm -rf /')", "python")
        assert any(f["name"] == "os_system" for f in result["findings"])
        assert result["critical"] >= 1

    def test_pickle(self, scanner):
        result = scanner.scan_content("pickle.loads(data)", "python")
        assert any(f["name"] == "pickle_usage" for f in result["findings"])

    def test_dunder_import(self, scanner):
        result = scanner.scan_content("__import__('os')", "python")
        assert any(f["name"] == "dunder_import" for f in result["findings"])

    def test_os_popen(self, scanner):
        result = scanner.scan_content("os.popen('cmd')", "python")
        assert any(f["name"] == "os_popen" for f in result["findings"])

    def test_clean_python(self, scanner):
        result = scanner.scan_content("def hello():\n    return 'world'", "python")
        assert result["findings_count"] == 0
        assert result["gate_passed"] is True


# ---------------------------------------------------------------------------
# Java patterns
# ---------------------------------------------------------------------------

class TestJavaPatterns:
    def test_runtime_exec(self, scanner):
        result = scanner.scan_content("Runtime.getRuntime().exec(cmd)", "java")
        assert any(f["name"] == "runtime_exec" for f in result["findings"])

    def test_deserialization(self, scanner):
        result = scanner.scan_content("ObjectInputStream ois = new ObjectInputStream(in);", "java")
        assert any(f["name"] == "deserialization" for f in result["findings"])

    def test_process_builder(self, scanner):
        result = scanner.scan_content('new ProcessBuilder("cmd")', "java")
        assert any(f["name"] == "process_builder" for f in result["findings"])

    def test_clean_java(self, scanner):
        result = scanner.scan_content("public class App {\n  public static void main(String[] args) {}\n}", "java")
        assert result["findings_count"] == 0


# ---------------------------------------------------------------------------
# Go patterns
# ---------------------------------------------------------------------------

class TestGoPatterns:
    def test_exec_command(self, scanner):
        result = scanner.scan_content('exec.Command("ls", "-la")', "go")
        assert any(f["name"] == "exec_command" for f in result["findings"])

    def test_unsafe_usage(self, scanner):
        result = scanner.scan_content("ptr := unsafe.Pointer(&x)", "go")
        assert any(f["name"] == "unsafe_usage" for f in result["findings"])


# ---------------------------------------------------------------------------
# Rust patterns
# ---------------------------------------------------------------------------

class TestRustPatterns:
    def test_unsafe_block(self, scanner):
        result = scanner.scan_content("unsafe { ptr::read(addr) }", "rust")
        assert any(f["name"] == "unsafe_block" for f in result["findings"])

    def test_process_command(self, scanner):
        result = scanner.scan_content('use std::process::Command;', "rust")
        assert any(f["name"] == "process_command" for f in result["findings"])


# ---------------------------------------------------------------------------
# C# patterns
# ---------------------------------------------------------------------------

class TestCSharpPatterns:
    def test_process_start(self, scanner):
        result = scanner.scan_content('Process.Start("cmd.exe")', "csharp")
        assert any(f["name"] == "process_start" for f in result["findings"])

    def test_assembly_load(self, scanner):
        result = scanner.scan_content("Assembly.LoadFrom(path)", "csharp")
        assert any(f["name"] == "assembly_load" for f in result["findings"])


# ---------------------------------------------------------------------------
# TypeScript / JavaScript patterns
# ---------------------------------------------------------------------------

class TestTypeScriptPatterns:
    def test_child_process(self, scanner):
        result = scanner.scan_content("const { exec } = require('child_process');", "typescript")
        assert any(f["name"] == "child_process" for f in result["findings"])

    def test_eval_ts(self, scanner):
        result = scanner.scan_content("eval(userInput)", "typescript")
        assert any(f["name"] == "eval_usage" for f in result["findings"])

    def test_function_constructor(self, scanner):
        result = scanner.scan_content("new Function('return 1+1')", "typescript")
        assert any(f["name"] == "function_constructor" for f in result["findings"])


# ---------------------------------------------------------------------------
# Language auto-detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_python_extension(self):
        assert CodePatternScanner._detect_language(Path("app.py")) == "python"

    def test_java_extension(self):
        assert CodePatternScanner._detect_language(Path("App.java")) == "java"

    def test_go_extension(self):
        assert CodePatternScanner._detect_language(Path("main.go")) == "go"

    def test_rust_extension(self):
        assert CodePatternScanner._detect_language(Path("lib.rs")) == "rust"

    def test_csharp_extension(self):
        assert CodePatternScanner._detect_language(Path("Program.cs")) == "csharp"

    def test_typescript_extension(self):
        assert CodePatternScanner._detect_language(Path("app.ts")) == "typescript"

    def test_tsx_extension(self):
        assert CodePatternScanner._detect_language(Path("App.tsx")) == "typescript"

    def test_js_extension(self):
        assert CodePatternScanner._detect_language(Path("index.js")) == "typescript"

    def test_unknown_extension(self):
        assert CodePatternScanner._detect_language(Path("readme.txt")) == ""


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

class TestFileScan:
    def test_scan_file(self, scanner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import os\nos.system('ls')\n")
            f.flush()
            result = scanner.scan_file(f.name)
            assert result["findings_count"] >= 1
            assert result["language"] == "python"

    def test_scan_nonexistent_file(self, scanner):
        result = scanner.scan_file("/nonexistent/file.py")
        assert "error" in result

    def test_scan_unknown_language(self, scanner):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("some text")
            f.flush()
            result = scanner.scan_file(f.name)
            assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

class TestDirectoryScan:
    def test_scan_directory(self, scanner):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a python file with dangerous pattern
            py_file = Path(tmpdir) / "bad.py"
            py_file.write_text("eval(input())\n")

            # Create a clean file
            clean_file = Path(tmpdir) / "clean.py"
            clean_file.write_text("def hello(): return 42\n")

            result = scanner.scan_directory(tmpdir)
            assert result["scanned_files"] == 2
            assert result["findings_count"] >= 1

    def test_scan_nonexistent_dir(self, scanner):
        result = scanner.scan_directory("/nonexistent/dir")
        assert "error" in result

    def test_skip_dirs(self):
        assert "node_modules" in SKIP_DIRS
        assert "__pycache__" in SKIP_DIRS
        assert ".git" in SKIP_DIRS


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

class TestGateEvaluation:
    def test_gate_passed_clean(self, scanner):
        result = scanner.scan_content("def safe(): return 1", "python")
        assert result["gate_passed"] is True

    def test_gate_failed_critical(self, scanner):
        result = scanner.scan_content("os.system('cmd')", "python")
        assert result["gate_passed"] is False

    def test_evaluate_gate_structure(self, scanner):
        gate = scanner.evaluate_gate("proj-123")
        assert gate["gate"] == "code_patterns"
        assert "config" in gate


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

class TestSeverityClassification:
    def test_severity_counts(self, scanner):
        code = "os.system('cmd')\npickle.loads(data)\nimportlib.import_module('x')\n"
        result = scanner.scan_content(code, "python")
        assert result["critical"] >= 1  # os.system
        assert result["high"] >= 1  # pickle
        assert result["medium"] >= 1  # importlib


# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

class TestSupportedLanguages:
    def test_supported_languages(self, scanner):
        langs = scanner.supported_languages
        assert "python" in langs
        assert "java" in langs
        assert "go" in langs
        assert "rust" in langs
        assert "csharp" in langs
        assert "typescript" in langs
        assert "universal" not in langs  # Not a language
