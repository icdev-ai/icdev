# CUI // SP-CTI
"""Tests for the structure pillar's anomaly-detection threshold system."""
from __future__ import annotations

import json
import pathlib
import textwrap


from tools.ai_augmentation.agent_readiness.pillars.structure import (
    PILLAR,
    _check_docker_support,
    _check_env_template,
    _check_gitignore,
    _check_monorepo_tools,
    _check_src_layout,
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
            "  structure:\n"
            "    gitignore:\n"
            "      min_lines: 12\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_gitignore_lines"] == 12
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_gitignore_lines"] == 5
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_structure_section_absent(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  documentation:\n    readme:\n      min_content_length: 80\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_gitignore_lines"] == 5
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_gitignore_lines"] == 5
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_gitignore — anomaly detection for .gitignore line count
# ---------------------------------------------------------------------------

class TestCheckGitignore:
    def _patch_thresholds(self, monkeypatch, min_lines: int = 5):
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {"min_gitignore_lines": min_lines})

    def test_passes_when_gitignore_meets_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_lines=5)
        _write(tmp_path, ".gitignore", "__pycache__/\n*.pyc\n*.egg-info/\ndist/\nbuild/\n")
        result = _check_gitignore(tmp_path)
        assert result.passed

    def test_passes_when_gitignore_exactly_at_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_lines=3)
        _write(tmp_path, ".gitignore", "__pycache__/\n*.pyc\ndist/\n")
        result = _check_gitignore(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_gitignore_below_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_lines=5)
        _write(tmp_path, ".gitignore", "__pycache__/\n*.pyc\n")  # only 2 lines
        result = _check_gitignore(tmp_path)
        assert not result.passed
        assert "sparse" in result.message.lower() or "gitignore" in result.message.lower()

    def test_fails_when_no_gitignore_present(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_gitignore(tmp_path)
        assert not result.passed
        assert "No .gitignore" in result.message

    def test_threshold_from_yaml_respected(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_lines=10)
        _write(tmp_path, ".gitignore", "\n".join(f"pattern{i}/" for i in range(8)))
        result = _check_gitignore(tmp_path)
        assert not result.passed

    def test_passes_when_threshold_raised_and_met(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_lines=10)
        _write(tmp_path, ".gitignore", "\n".join(f"pattern{i}/" for i in range(10)))
        result = _check_gitignore(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# _check_src_layout — source directory detection
# ---------------------------------------------------------------------------

class TestCheckSrcLayout:
    def test_passes_with_python_src_layout(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed
        assert "src/" in result.message

    def test_passes_with_java_src_main_layout(self, tmp_path):
        (tmp_path / "src" / "main").mkdir(parents=True)
        result = _check_src_layout(tmp_path)
        assert result.passed

    def test_passes_with_go_cmd_layout(self, tmp_path):
        (tmp_path / "cmd").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed
        assert "Go" in result.message or "cmd" in result.message

    def test_passes_with_go_pkg_layout(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed

    def test_passes_with_nodejs_lib_layout(self, tmp_path):
        _write(tmp_path, "package.json", '{"name": "myapp"}')
        (tmp_path / "lib").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed

    def test_passes_with_nodejs_packages_layout(self, tmp_path):
        _write(tmp_path, "package.json", '{"name": "monorepo"}')
        (tmp_path / "packages").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed

    def test_passes_with_generic_app_dir(self, tmp_path):
        (tmp_path / "app").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed
        assert "app" in result.message

    def test_passes_with_generic_api_dir(self, tmp_path):
        (tmp_path / "api").mkdir()
        result = _check_src_layout(tmp_path)
        assert result.passed

    def test_fails_with_no_recognized_source_layout(self, tmp_path):
        result = _check_src_layout(tmp_path)
        assert not result.passed
        assert "No recognizable" in result.message


# ---------------------------------------------------------------------------
# _check_monorepo_tools — monorepo tooling detection
# ---------------------------------------------------------------------------

class TestCheckMonorepoTools:
    def test_passes_with_npm_workspaces(self, tmp_path):
        pkg = {"name": "monorepo", "workspaces": ["packages/*"]}
        _write(tmp_path, "package.json", json.dumps(pkg))
        result = _check_monorepo_tools(tmp_path)
        assert result.passed
        assert "workspaces" in result.message.lower()

    def test_passes_with_pnpm_workspace_yaml(self, tmp_path):
        _write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - 'packages/*'\n")
        result = _check_monorepo_tools(tmp_path)
        assert result.passed
        assert "pnpm" in result.message.lower()

    def test_passes_with_nx_json(self, tmp_path):
        _write(tmp_path, "nx.json", '{"version": 2}')
        result = _check_monorepo_tools(tmp_path)
        assert result.passed

    def test_passes_with_turbo_json(self, tmp_path):
        _write(tmp_path, "turbo.json", '{"pipeline": {}}')
        result = _check_monorepo_tools(tmp_path)
        assert result.passed

    def test_skips_gracefully_for_single_repo(self, tmp_path):
        result = _check_monorepo_tools(tmp_path)
        assert result.passed
        assert result.skipped

    def test_passes_with_gradle_multi_project(self, tmp_path):
        _write(tmp_path, "settings.gradle", 'rootProject.name = "myapp"\ninclude(":core", ":api")\n')
        result = _check_monorepo_tools(tmp_path)
        assert result.passed
        assert "Gradle" in result.message


# ---------------------------------------------------------------------------
# _check_env_template — .env template detection
# ---------------------------------------------------------------------------

class TestCheckEnvTemplate:
    def test_passes_with_env_example(self, tmp_path):
        _write(tmp_path, ".env.example", "DATABASE_URL=\nSECRET_KEY=\n")
        result = _check_env_template(tmp_path)
        assert result.passed
        assert ".env.example" in result.message

    def test_passes_with_env_template(self, tmp_path):
        _write(tmp_path, ".env.template", "DATABASE_URL=\n")
        result = _check_env_template(tmp_path)
        assert result.passed

    def test_passes_with_env_sample(self, tmp_path):
        _write(tmp_path, ".env.sample", "API_KEY=\n")
        result = _check_env_template(tmp_path)
        assert result.passed

    def test_fails_when_no_env_template_present(self, tmp_path):
        result = _check_env_template(tmp_path)
        assert not result.passed
        assert ".env.example" in result.message or "template" in result.message.lower()


# ---------------------------------------------------------------------------
# _check_docker_support — container configuration detection
# ---------------------------------------------------------------------------

class TestCheckDockerSupport:
    def test_passes_with_dockerfile(self, tmp_path):
        _write(tmp_path, "Dockerfile", "FROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n")
        result = _check_docker_support(tmp_path)
        assert result.passed
        assert "Dockerfile" in result.message

    def test_passes_with_containerfile(self, tmp_path):
        _write(tmp_path, "Containerfile", "FROM fedora:latest\n")
        result = _check_docker_support(tmp_path)
        assert result.passed

    def test_passes_with_docker_compose_yml(self, tmp_path):
        _write(tmp_path, "docker-compose.yml", "version: '3'\nservices:\n  app:\n    image: myapp\n")
        result = _check_docker_support(tmp_path)
        assert result.passed
        assert "docker-compose" in result.message.lower() or "compose" in result.message.lower()

    def test_passes_with_compose_yaml(self, tmp_path):
        _write(tmp_path, "compose.yaml", "services:\n  app:\n    build: .\n")
        result = _check_docker_support(tmp_path)
        assert result.passed

    def test_fails_when_no_container_config_present(self, tmp_path):
        result = _check_docker_support(tmp_path)
        assert not result.passed
        assert "No container" in result.message


# ---------------------------------------------------------------------------
# PILLAR integration — run all criteria
# ---------------------------------------------------------------------------

class TestStructurePillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"gitignore-present", "src-layout", "monorepo-tools", "env-template", "docker-support"}

    def test_pillar_id_and_name(self):
        assert PILLAR.id == "structure"
        assert PILLAR.name == "Structure"

    def test_score_all_pass(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {"min_gitignore_lines": 1})
        _write(tmp_path, ".gitignore", "*.pyc\n")
        (tmp_path / "src").mkdir()
        _write(tmp_path, ".env.example", "API_KEY=\n")
        _write(tmp_path, "Dockerfile", "FROM python:3.12-slim\n")
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]

    def test_score_all_fail(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.structure as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {"min_gitignore_lines": 100})
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        # monorepo-tools skips (counts as passed), others fail
        assert score["passed"] < score["total"]
