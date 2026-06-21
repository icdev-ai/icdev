# CUI // SP-CTI
"""Tests for the configuration pillar's anomaly-detection threshold system."""
from __future__ import annotations

import json
import pathlib
import textwrap


from tools.aiify.agent_readiness.pillars.configuration import (
    PILLAR,
    _apply_env_overrides,
    _check_cd_deployment,
    _check_ci_pipeline,
    _check_iac_present,
    _check_makefile_or_taskfile,
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
            "  configuration:\n"
            "    task_runner:\n"
            "      min_makefile_targets: 7\n"
            "      min_npm_scripts: 5\n"
            "    ci_pipeline:\n"
            "      min_workflows: 2\n"
            "    iac:\n"
            "      min_files: 3\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_makefile_targets"] == 7
        assert result["min_npm_scripts"] == 5
        assert result["min_ci_workflows"] == 2
        assert result["min_iac_files"] == 3
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_makefile_targets"] == 3
        assert result["min_npm_scripts"] == 3
        assert result["min_ci_workflows"] == 1
        assert result["min_iac_files"] == 1
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  configuration:\n    ci_pipeline:\n      min_workflows: 4\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_ci_workflows"] == 4
        assert result["min_makefile_targets"] == 3   # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_makefile_targets"] == 3
        mod._load_thresholds.cache_clear()

    def test_env_vars_override_yaml_values(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  configuration:\n"
            "    ci_pipeline:\n"
            "      min_workflows: 2\n"
            "    iac:\n"
            "      min_files: 2\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "5")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_IAC_FILES", "4")
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_ci_workflows"] == 5   # env var wins over YAML=2
        assert result["min_iac_files"] == 4       # env var wins over YAML=2
        mod._load_thresholds.cache_clear()

    def test_env_vars_override_defaults_when_yaml_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "10")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_NPM_SCRIPTS", "8")
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_makefile_targets"] == 10  # env var wins over default=3
        assert result["min_npm_scripts"] == 8         # env var wins over default=3
        mod._load_thresholds.cache_clear()

    def test_malformed_env_var_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "not-a-number")
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_ci_workflows"] == 1  # falls back to default
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _apply_env_overrides — unit tests for the env override helper
# ---------------------------------------------------------------------------

class TestApplyEnvOverrides:
    def test_no_env_vars_returns_input_unchanged(self, monkeypatch):
        for key in ("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "ICDEV_CONFIG_MIN_IAC_FILES",
                    "ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "ICDEV_CONFIG_MIN_NPM_SCRIPTS"):
            monkeypatch.delenv(key, raising=False)
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result == base

    def test_single_env_var_overrides_one_key(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "7")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result["min_ci_workflows"] == 7
        assert result["min_iac_files"] == 1       # unchanged

    def test_all_env_vars_override_all_keys(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "9")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_IAC_FILES", "6")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "12")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_NPM_SCRIPTS", "5")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result == {"min_ci_workflows": 9, "min_iac_files": 6, "min_makefile_targets": 12, "min_npm_scripts": 5}

    def test_malformed_env_var_leaves_value_unchanged(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_IAC_FILES", "two")
        base = {"min_iac_files": 3}
        result = _apply_env_overrides(base)
        assert result["min_iac_files"] == 3  # malformed → original preserved

    def test_does_not_mutate_input_dict(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "99")
        base = {"min_ci_workflows": 1}
        _apply_env_overrides(base)
        assert base["min_ci_workflows"] == 1  # original dict untouched


# ---------------------------------------------------------------------------
# _apply_env_overrides — env var priority layer
# ---------------------------------------------------------------------------

class TestApplyEnvOverridesEnvPriority:
    def test_env_var_overrides_yaml_value(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "5")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result["min_ci_workflows"] == 5

    def test_env_var_overrides_all_int_keys(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "2")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_IAC_FILES", "4")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "6")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_NPM_SCRIPTS", "8")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result["min_ci_workflows"] == 2
        assert result["min_iac_files"] == 4
        assert result["min_makefile_targets"] == 6
        assert result["min_npm_scripts"] == 8

    def test_malformed_env_var_is_ignored(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "not-a-number")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = _apply_env_overrides(base)
        assert result["min_ci_workflows"] == 1  # unchanged

    def test_unset_env_vars_leave_base_unchanged(self, monkeypatch):
        for var in ("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "ICDEV_CONFIG_MIN_IAC_FILES",
                    "ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "ICDEV_CONFIG_MIN_NPM_SCRIPTS"):
            monkeypatch.delenv(var, raising=False)
        base = {"min_ci_workflows": 7, "min_iac_files": 2, "min_makefile_targets": 5, "min_npm_scripts": 4}
        result = _apply_env_overrides(base)
        assert result == base

    def test_does_not_mutate_input(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "9")
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        original = dict(base)
        _apply_env_overrides(base)
        assert base == original  # input must not be mutated

    def test_env_var_override_flows_through_load_thresholds(self, tmp_path, monkeypatch):
        """End-to-end: env var beats the YAML value at load time."""
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "99")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_ci_workflows"] == 99
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_makefile_or_taskfile — anomaly detection for task runner
# ---------------------------------------------------------------------------

class TestCheckMakefileOrTaskfile:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"min_makefile_targets": 3, "min_npm_scripts": 3, "min_ci_workflows": 1, "min_iac_files": 1}
        defaults.update(overrides)
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_makefile_meets_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_makefile_targets=3)
        _write(tmp_path, "Makefile", "build:\n\techo\ntest:\n\tpytest\ndeploy:\n\t./deploy.sh\n")
        result = _check_makefile_or_taskfile(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_makefile_below_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_makefile_targets=3)
        _write(tmp_path, "Makefile", "build:\n\techo\n")  # only 1 target
        result = _check_makefile_or_taskfile(tmp_path)
        assert not result.passed
        assert "1 target" in result.message
        assert "min 3" in result.message

    def test_falls_through_to_taskfile_when_makefile_undersized(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_makefile_targets=3)
        _write(tmp_path, "Makefile", "build:\n\techo\n")
        _write(tmp_path, "Taskfile.yml", "version: '3'\ntasks:\n  build:\n    cmd: echo hi\n")
        result = _check_makefile_or_taskfile(tmp_path)
        assert result.passed
        assert "Taskfile" in result.message

    def test_passes_with_taskfile(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "Taskfile.yml", "version: '3'\ntasks: {}\n")
        result = _check_makefile_or_taskfile(tmp_path)
        assert result.passed

    def test_passes_with_enough_npm_scripts(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_npm_scripts=2)
        pkg = {"scripts": {"build": "tsc", "test": "jest", "lint": "eslint ."}}
        _write(tmp_path, "package.json", json.dumps(pkg))
        result = _check_makefile_or_taskfile(tmp_path)
        assert result.passed

    def test_fails_when_npm_scripts_below_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_npm_scripts=3)
        pkg = {"scripts": {"build": "tsc"}}  # only 1 script
        _write(tmp_path, "package.json", json.dumps(pkg))
        result = _check_makefile_or_taskfile(tmp_path)
        assert not result.passed

    def test_fails_with_no_task_runner(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_makefile_or_taskfile(tmp_path)
        assert not result.passed
        assert "No task runner" in result.message


# ---------------------------------------------------------------------------
# _check_ci_pipeline — anomaly detection for CI workflow count
# ---------------------------------------------------------------------------

class TestCheckCiPipeline:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"min_makefile_targets": 3, "min_npm_scripts": 3, "min_ci_workflows": 1, "min_iac_files": 1}
        defaults.update(overrides)
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_with_sufficient_workflows(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_ci_workflows=1)
        _write(tmp_path, ".github/workflows/ci.yml", "name: CI\non: push\njobs: {}\n")
        result = _check_ci_pipeline(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_below_min_workflows(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_ci_workflows=2)
        _write(tmp_path, ".github/workflows/ci.yml", "name: CI\non: push\njobs: {}\n")
        result = _check_ci_pipeline(tmp_path)
        assert not result.passed
        assert "1" in result.message
        assert "min 2" in result.message

    def test_passes_with_gitlab_ci(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, ".gitlab-ci.yml", "stages: [build]\n")
        result = _check_ci_pipeline(tmp_path)
        assert result.passed

    def test_fails_with_no_ci(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_ci_pipeline(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_iac_present — anomaly detection for IaC file count
# ---------------------------------------------------------------------------

class TestCheckIacPresent:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"min_makefile_targets": 3, "min_npm_scripts": 3, "min_ci_workflows": 1, "min_iac_files": 1}
        defaults.update(overrides)
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_with_sufficient_tf_files(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_iac_files=1)
        _write(tmp_path, "main.tf", 'resource "aws_s3_bucket" "b" {}\n')
        result = _check_iac_present(tmp_path)
        assert result.passed

    def test_reports_anomaly_when_tf_below_min(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_iac_files=2)
        _write(tmp_path, "main.tf", 'resource "aws_s3_bucket" "b" {}\n')
        result = _check_iac_present(tmp_path)
        assert not result.passed
        assert "1" in result.message or "Only" in result.message

    def test_passes_with_helm_chart(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_iac_files=1)
        _write(tmp_path, "charts/myapp/Chart.yaml", "apiVersion: v2\nname: myapp\nversion: 0.1.0\n")
        result = _check_iac_present(tmp_path)
        assert result.passed

    def test_fails_with_no_iac(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_iac_present(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_cd_deployment — anomaly detection for CD deployment step
# ---------------------------------------------------------------------------

class TestCheckCdDeployment:
    _DEFAULT_THRESHOLDS = {
        "min_makefile_targets": 3,
        "min_npm_scripts": 3,
        "min_ci_workflows": 1,
        "min_iac_files": 1,
        "deploy_keywords": ["deploy", "release", "publish", r"push.*image", r"helm\s+upgrade", r"kubectl\s+apply"],
    }

    def _patch_thresholds(self, monkeypatch, **overrides):
        thresholds = {**self._DEFAULT_THRESHOLDS, **overrides}
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: thresholds)

    def test_passes_when_deploy_keyword_in_github_workflow(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, ".github/workflows/cd.yml",
               "name: CD\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: kubectl apply -f k8s/\n")
        result = _check_cd_deployment(tmp_path)
        assert result.passed
        assert "cd.yml" in result.message

    def test_passes_with_custom_deploy_keyword_from_yaml(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, deploy_keywords=["ship", "publish"])
        _write(tmp_path, ".github/workflows/release.yml",
               "name: Release\non: push\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: ship now\n")
        result = _check_cd_deployment(tmp_path)
        assert result.passed

    def test_fails_when_no_deploy_keyword_in_ci(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, ".github/workflows/ci.yml",
               "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n")
        result = _check_cd_deployment(tmp_path)
        assert not result.passed
        assert "No CD deployment" in result.message

    def test_passes_when_deploy_target_in_makefile(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "Makefile", "build:\n\techo build\ndeploy:\n\t./deploy.sh\n")
        result = _check_cd_deployment(tmp_path)
        assert result.passed
        assert "Makefile" in result.message

    def test_fails_when_no_ci_and_no_makefile_deploy(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_cd_deployment(tmp_path)
        assert not result.passed

    def test_keyword_list_from_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        # Only "custom-release" is a keyword — default "deploy" should NOT trigger a pass.
        self._patch_thresholds(monkeypatch, deploy_keywords=["custom-release"])
        _write(tmp_path, ".github/workflows/ci.yml",
               "name: CI\non: push\njobs:\n  build:\n    steps:\n      - run: deploy\n")
        result = _check_cd_deployment(tmp_path)
        assert not result.passed

    def test_passes_when_gitlab_ci_has_deploy_step(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, ".gitlab-ci.yml",
               "stages:\n  - deploy\ndeploy-prod:\n  stage: deploy\n  script:\n    - helm upgrade myapp ./chart\n")
        result = _check_cd_deployment(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# _apply_env_overrides — env var priority layer
# ---------------------------------------------------------------------------

class TestApplyEnvOverridesModuleLevel:
    def test_env_var_overrides_min_ci_workflows(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "5")
        result = mod._apply_env_overrides({"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3})
        assert result["min_ci_workflows"] == 5

    def test_env_var_overrides_min_iac_files(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setenv("ICDEV_CONFIG_MIN_IAC_FILES", "4")
        result = mod._apply_env_overrides({"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3})
        assert result["min_iac_files"] == 4

    def test_env_var_overrides_min_makefile_targets(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setenv("ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", "10")
        result = mod._apply_env_overrides({"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3})
        assert result["min_makefile_targets"] == 10

    def test_env_var_overrides_min_npm_scripts(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setenv("ICDEV_CONFIG_MIN_NPM_SCRIPTS", "7")
        result = mod._apply_env_overrides({"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3})
        assert result["min_npm_scripts"] == 7

    def test_malformed_env_var_is_ignored(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "not-a-number")
        result = mod._apply_env_overrides({"min_ci_workflows": 2, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3})
        assert result["min_ci_workflows"] == 2  # original value preserved

    def test_unset_env_vars_leave_values_unchanged(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.delenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", raising=False)
        monkeypatch.delenv("ICDEV_CONFIG_MIN_IAC_FILES", raising=False)
        monkeypatch.delenv("ICDEV_CONFIG_MIN_MAKEFILE_TARGETS", raising=False)
        monkeypatch.delenv("ICDEV_CONFIG_MIN_NPM_SCRIPTS", raising=False)
        base = {"min_ci_workflows": 1, "min_iac_files": 1, "min_makefile_targets": 3, "min_npm_scripts": 3}
        result = mod._apply_env_overrides(base)
        assert result == base

    def test_env_var_takes_priority_over_yaml(self, tmp_path, monkeypatch):
        """Env var must win over both YAML and _DEFAULTS."""
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  configuration:\n    ci_pipeline:\n      min_workflows: 2\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        monkeypatch.setenv("ICDEV_CONFIG_MIN_CI_WORKFLOWS", "9")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_ci_workflows"] == 9
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# PILLAR integration — run all criteria
# ---------------------------------------------------------------------------

class TestConfigurationPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"ci-pipeline", "cd-deployment", "iac-present", "editorconfig", "task-runner"}

    def test_score_all_pass(self, tmp_path, monkeypatch):
        import tools.aiify.agent_readiness.pillars.configuration as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "min_makefile_targets": 1, "min_npm_scripts": 1,
            "min_ci_workflows": 1, "min_iac_files": 1,
        })
        _write(tmp_path, ".github/workflows/ci.yml",
               "name: CI\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: kubectl apply -f .\n")
        _write(tmp_path, "main.tf", 'resource "null_resource" "x" {}\n')
        _write(tmp_path, ".editorconfig", "[*]\nindent_size = 4\n")
        _write(tmp_path, "Makefile", "deploy:\n\techo hi\n")
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]
