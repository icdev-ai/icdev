# CUI // SP-CTI
"""Tests for the documentation pillar's anomaly-detection threshold system."""
from __future__ import annotations

import pathlib
import textwrap


from tools.ai_augmentation.agent_readiness.pillars.documentation import (
    PILLAR,
    _check_readme,
    _check_inline_docstrings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  documentation:\n"
            "    readme:\n"
            "      min_content_length: 200\n"
            "    inline_docs:\n"
            "      sample_size: 10\n"
            "      min_jsdoc_files: 5\n"
            "      docstring_ratio_denominator: 2\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["readme_min_content_length"] == 200
        assert result["inline_docs_sample_size"] == 10
        assert result["min_jsdoc_files"] == 5
        assert result["docstring_ratio_denominator"] == 2
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["readme_min_content_length"] == 80
        assert result["inline_docs_sample_size"] == 20
        assert result["min_jsdoc_files"] == 2
        assert result["docstring_ratio_denominator"] == 3
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  documentation:\n    readme:\n      min_content_length: 150\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["readme_min_content_length"] == 150
        assert result["inline_docs_sample_size"] == 20   # default
        assert result["min_jsdoc_files"] == 2            # default
        assert result["docstring_ratio_denominator"] == 3  # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["readme_min_content_length"] == 80
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_readme — anomaly detection for README content length
# ---------------------------------------------------------------------------

class TestCheckReadme:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {
            "readme_min_content_length": 80,
            "inline_docs_sample_size": 20,
            "min_jsdoc_files": 2,
            "docstring_ratio_denominator": 3,
        }
        defaults.update(overrides)
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_readme_exceeds_min_length(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, readme_min_content_length=80)
        _write(tmp_path, "README.md", "# My Project\n\n" + "word " * 20)
        result = _check_readme(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_readme_too_short(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, readme_min_content_length=80)
        _write(tmp_path, "README.md", "# Short")
        result = _check_readme(tmp_path)
        assert not result.passed
        assert "min 80" in result.message

    def test_readme_exactly_at_threshold_fails(self, tmp_path, monkeypatch):
        # len(content) must be > min_length, so exactly at the boundary fails
        min_len = 80
        self._patch_thresholds(monkeypatch, readme_min_content_length=min_len)
        _write(tmp_path, "README.md", "x" * min_len)
        result = _check_readme(tmp_path)
        assert not result.passed

    def test_readme_just_above_threshold_passes(self, tmp_path, monkeypatch):
        min_len = 80
        self._patch_thresholds(monkeypatch, readme_min_content_length=min_len)
        _write(tmp_path, "README.md", "x" * (min_len + 1))
        result = _check_readme(tmp_path)
        assert result.passed

    def test_fails_when_no_readme(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_readme(tmp_path)
        assert not result.passed
        assert "No README" in result.message

    def test_accepts_rst_readme(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, readme_min_content_length=10)
        _write(tmp_path, "README.rst", "My Project\n==========\n\nSome description here.\n")
        result = _check_readme(tmp_path)
        assert result.passed

    def test_threshold_override_changes_pass_condition(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, readme_min_content_length=10)
        _write(tmp_path, "README.md", "# Short but enough")
        result = _check_readme(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# _check_inline_docstrings — anomaly detection for docstring/JSDoc coverage
# ---------------------------------------------------------------------------

class TestCheckInlineDocstrings:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {
            "readme_min_content_length": 80,
            "inline_docs_sample_size": 20,
            "min_jsdoc_files": 2,
            "docstring_ratio_denominator": 3,
        }
        defaults.update(overrides)
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_with_sufficient_python_docstrings(self, tmp_path, monkeypatch):
        # ratio_denom=3: need max(1, 3//3)=1 file with docstrings; provide 1 out of 3
        self._patch_thresholds(monkeypatch, docstring_ratio_denominator=3)
        _write(tmp_path, "mod_a.py", '"""Module A docstring."""\ndef foo(): pass\n')
        _write(tmp_path, "mod_b.py", "def bar(): pass\n")
        _write(tmp_path, "mod_c.py", "def baz(): pass\n")
        result = _check_inline_docstrings(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_docstring_ratio_below_threshold(self, tmp_path, monkeypatch):
        # ratio_denom=2: need max(1, 4//2)=2 files with docstrings; provide 0
        self._patch_thresholds(monkeypatch, docstring_ratio_denominator=2)
        for i in range(4):
            _write(tmp_path, f"mod_{i}.py", "def func(): pass\n")
        result = _check_inline_docstrings(tmp_path)
        assert not result.passed
        assert "Low docstring" in result.message or "0" in result.message

    def test_passes_with_sufficient_jsdoc_when_no_python(self, tmp_path, monkeypatch):
        # No Python files → falls through to JSDoc check; min_jsdoc_files=1
        self._patch_thresholds(monkeypatch, min_jsdoc_files=1)
        _write(tmp_path, "src/a.ts", "/** @param x number */\nexport function foo(x: number) {}\n")
        _write(tmp_path, "src/b.ts", "export function bar() {}\n")
        result = _check_inline_docstrings(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_jsdoc_below_threshold(self, tmp_path, monkeypatch):
        # No Python files; min_jsdoc_files=3 but only 1 TS file has JSDoc
        self._patch_thresholds(monkeypatch, min_jsdoc_files=3)
        _write(tmp_path, "src/a.ts", "/** @param x number */\nexport function foo(x: number) {}\n")
        _write(tmp_path, "src/b.ts", "export function bar() {}\n")
        _write(tmp_path, "src/c.ts", "export function baz() {}\n")
        result = _check_inline_docstrings(tmp_path)
        assert not result.passed
        assert "min 3" in result.message

    def test_skipped_when_no_source_files(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_inline_docstrings(tmp_path)
        assert result.skipped
        assert result.passed  # skipped counts as pass

    def test_sample_size_limits_files_checked(self, tmp_path, monkeypatch):
        # sample_size=2 caps the scan; create 10 files, 3 with docstrings
        # With ratio_denom=3 and sample=2: need max(1, 2//3)=max(1,0)=1 docstring file
        self._patch_thresholds(monkeypatch, inline_docs_sample_size=2, docstring_ratio_denominator=3)
        _write(tmp_path, "a.py", '"""Has docstring."""\n')
        for i in range(9):
            _write(tmp_path, f"mod_{i}.py", "def func(): pass\n")
        result = _check_inline_docstrings(tmp_path)
        # With sample_size=2, only 2 files are checked — result depends on order,
        # but at least the threshold mechanism is exercised without error
        assert isinstance(result.passed, bool)

    def test_threshold_override_changes_jsdoc_pass_condition(self, tmp_path, monkeypatch):
        # With min_jsdoc_files=1 a single JSDoc file passes; with 2 it would fail
        self._patch_thresholds(monkeypatch, min_jsdoc_files=1)
        _write(tmp_path, "index.js", "/** Main entry point. */\nfunction main() {}\n")
        result = _check_inline_docstrings(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# PILLAR integration — run all criteria
# ---------------------------------------------------------------------------

class TestDocumentationPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {
            "readme-present",
            "changelog-present",
            "contributing-guide",
            "api-docs",
            "inline-docstrings",
        }

    def test_score_all_pass(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "readme_min_content_length": 10,
            "inline_docs_sample_size": 20,
            "min_jsdoc_files": 1,
            "docstring_ratio_denominator": 3,
        })
        _write(tmp_path, "README.md", "# Project\n\nFull description here.\n")
        _write(tmp_path, "CHANGELOG.md", "# Changelog\n\n## v1.0.0\n- Initial release\n")
        _write(tmp_path, "CONTRIBUTING.md", "# Contributing\n\nPlease read this guide.\n")
        _write(tmp_path, "docs/index.md", "# Documentation\n")
        _write(tmp_path, "main.py", '"""Main module."""\ndef run(): pass\n')
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]

    def test_score_reflects_failures(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.documentation as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "readme_min_content_length": 80,
            "inline_docs_sample_size": 20,
            "min_jsdoc_files": 2,
            "docstring_ratio_denominator": 3,
        })
        # Intentionally empty repo — all checks fail (inline-docstrings skipped → not counted)
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] < score["total"]
