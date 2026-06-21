# CUI // SP-CTI
"""Tests for agent_readiness configuration pillar — anomaly-detection threshold loading."""
from __future__ import annotations

import json
import pathlib
import textwrap



# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return an empty repo directory."""
    return tmp_path


def _make_repo_with_ci(tmp_path: pathlib.Path, n_workflows: int = 2) -> pathlib.Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for i in range(n_workflows):
        (wf_dir / f"ci-{i}.yml").write_text("on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: []\n")
    return tmp_path


def _make_repo_with_makefile(tmp_path: pathlib.Path, targets: list[str]) -> pathlib.Path:
    lines = [f"{t}:\n\techo done\n" for t in targets]
    (tmp_path / "Makefile").write_text("\n".join(lines))
    return tmp_path


def _make_repo_with_tf(tmp_path: pathlib.Path, n_files: int = 2) -> pathlib.Path:
    for i in range(n_files):
        (tmp_path / f"main{i}.tf").write_text('resource "null_resource" "x" {}\n')
    return tmp_path


# ---------------------------------------------------------------------------
# _load_thresholds
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def setup_method(self):
        # Clear lru_cache between tests
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        _load_thresholds.cache_clear()

    def test_returns_dict_with_expected_keys(self):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        t = _load_thresholds()
        assert set(t) >= {"min_makefile_targets", "min_npm_scripts", "min_ci_workflows", "min_iac_files"}

    def test_values_are_positive_integers(self):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        t = _load_thresholds()
        for k, v in t.items():
            assert isinstance(v, int) and v >= 1, f"{k}={v} should be a positive int"

    def test_config_file_is_readable(self):
        """Verify _ARGS_PATH actually points to an existing file (parents[4] fix)."""
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _ARGS_PATH
        assert _ARGS_PATH.exists(), f"Config not found at {_ARGS_PATH}; parents[] index may be wrong"

    def test_fallback_on_missing_config(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.configuration as cfg_mod
        _load_thresholds = cfg_mod._load_thresholds
        _load_thresholds.cache_clear()
        monkeypatch.setattr(cfg_mod, "_ARGS_PATH", pathlib.Path("/nonexistent/path.yaml"))
        _load_thresholds.cache_clear()
        t = _load_thresholds()
        # Must fall back to _DEFAULTS, not raise
        assert t["min_makefile_targets"] == cfg_mod._DEFAULTS["min_makefile_targets"]
        _load_thresholds.cache_clear()

    def test_custom_yaml_values_loaded(self, tmp_path, monkeypatch):
        """Thresholds from a custom YAML override the defaults."""
        import tools.ai_augmentation.agent_readiness.pillars.configuration as cfg_mod
        cfg_mod._load_thresholds.cache_clear()
        custom_yaml = tmp_path / "cfg.yaml"
        custom_yaml.write_text(textwrap.dedent("""\
            pillars:
              configuration:
                ci_pipeline:
                  min_workflows: 5
                iac:
                  min_files: 7
                task_runner:
                  min_makefile_targets: 10
                  min_npm_scripts: 8
        """))
        monkeypatch.setattr(cfg_mod, "_ARGS_PATH", custom_yaml)
        cfg_mod._load_thresholds.cache_clear()
        t = cfg_mod._load_thresholds()
        assert t["min_ci_workflows"] == 5
        assert t["min_iac_files"] == 7
        assert t["min_makefile_targets"] == 10
        assert t["min_npm_scripts"] == 8
        cfg_mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_ci_pipeline
# ---------------------------------------------------------------------------

class TestCheckCiPipeline:
    def setup_method(self):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        _load_thresholds.cache_clear()

    def test_passes_when_workflows_meet_minimum(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_ci_pipeline
        repo = _make_repo_with_ci(tmp_path, n_workflows=2)
        result = _check_ci_pipeline(repo)
        assert result.passed

    def test_fails_when_no_ci_found(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_ci_pipeline
        result = _check_ci_pipeline(tmp_path)
        assert not result.passed

    def test_threshold_respected_from_config(self, tmp_path, monkeypatch):
        """If config sets min_workflows=3, a repo with 2 workflows should fail."""
        import tools.ai_augmentation.agent_readiness.pillars.configuration as cfg_mod
        cfg_mod._load_thresholds.cache_clear()
        custom_yaml = tmp_path / "cfg.yaml"
        custom_yaml.write_text(textwrap.dedent("""\
            pillars:
              configuration:
                ci_pipeline:
                  min_workflows: 3
                iac:
                  min_files: 1
                task_runner:
                  min_makefile_targets: 3
                  min_npm_scripts: 3
        """))
        monkeypatch.setattr(cfg_mod, "_ARGS_PATH", custom_yaml)
        cfg_mod._load_thresholds.cache_clear()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo_with_ci(repo, n_workflows=2)
        result = cfg_mod._check_ci_pipeline(repo)
        assert not result.passed
        assert "min 3" in result.message
        cfg_mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_makefile_or_taskfile
# ---------------------------------------------------------------------------

class TestCheckMakefileOrTaskfile:
    def setup_method(self):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        _load_thresholds.cache_clear()

    def test_passes_with_sufficient_targets(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_makefile_or_taskfile
        repo = _make_repo_with_makefile(tmp_path, ["build", "test", "lint", "deploy"])
        result = _check_makefile_or_taskfile(repo)
        assert result.passed

    def test_fails_with_no_task_runner(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_makefile_or_taskfile
        result = _check_makefile_or_taskfile(tmp_path)
        assert not result.passed

    def test_passes_with_taskfile(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_makefile_or_taskfile
        (tmp_path / "Taskfile.yml").write_text("version: '3'\ntasks:\n  build:\n    cmds:\n      - go build\n")
        result = _check_makefile_or_taskfile(tmp_path)
        assert result.passed

    def test_npm_scripts_threshold_from_config(self, tmp_path, monkeypatch):
        """Config min_npm_scripts=5; package.json with 3 scripts should fail."""
        import tools.ai_augmentation.agent_readiness.pillars.configuration as cfg_mod
        cfg_mod._load_thresholds.cache_clear()
        custom_yaml = tmp_path / "cfg.yaml"
        custom_yaml.write_text(textwrap.dedent("""\
            pillars:
              configuration:
                ci_pipeline:
                  min_workflows: 1
                iac:
                  min_files: 1
                task_runner:
                  min_makefile_targets: 3
                  min_npm_scripts: 5
        """))
        monkeypatch.setattr(cfg_mod, "_ARGS_PATH", custom_yaml)
        cfg_mod._load_thresholds.cache_clear()
        repo = tmp_path / "repo"
        repo.mkdir()
        pkg = {"scripts": {"build": "tsc", "test": "jest", "lint": "eslint ."}}
        (repo / "package.json").write_text(json.dumps(pkg))
        result = cfg_mod._check_makefile_or_taskfile(repo)
        assert not result.passed
        cfg_mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_iac_present
# ---------------------------------------------------------------------------

class TestCheckIacPresent:
    def setup_method(self):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds
        _load_thresholds.cache_clear()

    def test_passes_with_tf_files(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_iac_present
        repo = _make_repo_with_tf(tmp_path, n_files=2)
        result = _check_iac_present(repo)
        assert result.passed

    def test_fails_when_no_iac(self, tmp_path):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import _check_iac_present
        result = _check_iac_present(tmp_path)
        assert not result.passed

    def test_iac_threshold_from_config(self, tmp_path, monkeypatch):
        """Config min_files=3; a repo with 1 .tf file should fail."""
        import tools.ai_augmentation.agent_readiness.pillars.configuration as cfg_mod
        cfg_mod._load_thresholds.cache_clear()
        custom_yaml = tmp_path / "cfg.yaml"
        custom_yaml.write_text(textwrap.dedent("""\
            pillars:
              configuration:
                ci_pipeline:
                  min_workflows: 1
                iac:
                  min_files: 3
                task_runner:
                  min_makefile_targets: 3
                  min_npm_scripts: 3
        """))
        monkeypatch.setattr(cfg_mod, "_ARGS_PATH", custom_yaml)
        cfg_mod._load_thresholds.cache_clear()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_repo_with_tf(repo, n_files=1)
        result = cfg_mod._check_iac_present(repo)
        assert not result.passed
        assert "min 3" in result.message
        cfg_mod._load_thresholds.cache_clear()
