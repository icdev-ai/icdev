# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.builder.scaffolder — project scaffolding from templates.

Verifies Python backend, API, CLI, microservice scaffolding including
directory structures, CUI markings, gitignore patterns, and README content.
All tests use tmp_path only — no database required.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

try:
    from tools.builder.scaffolder import (
        scaffold_python_backend,
        scaffold_api,
        scaffold_cli,
        scaffold_microservice,
        _common_gitignore,
        _readme_content,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.builder.scaffolder not available")


# ---------------------------------------------------------------------------
# TestScaffoldPythonBackend
# ---------------------------------------------------------------------------
class TestScaffoldPythonBackend:
    """Python backend scaffolding: src/, tests/, pyproject.toml, Dockerfile, compliance/."""

    def test_creates_src_directory(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        assert (tmp_path / "myapp" / "src").is_dir()

    def test_creates_tests_directory(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        assert (tmp_path / "myapp" / "tests").is_dir()

    def test_creates_pyproject_toml(self, tmp_path):
        files = scaffold_python_backend(str(tmp_path), "myapp")
        pyproject = tmp_path / "myapp" / "pyproject.toml"
        assert pyproject.exists()
        assert str(pyproject) in files

    def test_pyproject_contains_project_name(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        content = (tmp_path / "myapp" / "pyproject.toml").read_text(encoding="utf-8")
        assert '"myapp"' in content

    def test_creates_dockerfile(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        dockerfile = tmp_path / "myapp" / "Dockerfile"
        assert dockerfile.exists()

    def test_dockerfile_has_cui_header(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        content = (tmp_path / "myapp" / "Dockerfile").read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in content

    def test_creates_compliance_directory(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        compliance = tmp_path / "myapp" / "compliance"
        assert compliance.is_dir()

    def test_compliance_has_subdirs(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        for sub in ["ssp", "poam", "stig", "sbom"]:
            assert (tmp_path / "myapp" / "compliance" / sub).is_dir()

    def test_creates_main_py(self, tmp_path):
        scaffold_python_backend(str(tmp_path), "myapp")
        main_py = tmp_path / "myapp" / "src" / "main.py"
        assert main_py.exists()

    def test_returns_file_list(self, tmp_path):
        files = scaffold_python_backend(str(tmp_path), "myapp")
        assert isinstance(files, list)
        assert len(files) > 0


# ---------------------------------------------------------------------------
# TestScaffoldApi
# ---------------------------------------------------------------------------
class TestScaffoldApi:
    """API scaffolding: Flask app structure, requirements."""

    def test_creates_app_py(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        app_py = tmp_path / "myapi" / "src" / "app.py"
        assert app_py.exists()

    def test_app_py_contains_flask(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        content = (tmp_path / "myapi" / "src" / "app.py").read_text(encoding="utf-8")
        assert "Flask" in content

    def test_app_py_has_health_endpoint(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        content = (tmp_path / "myapi" / "src" / "app.py").read_text(encoding="utf-8")
        assert "/health" in content

    def test_creates_conftest(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        conftest = tmp_path / "myapi" / "tests" / "conftest.py"
        assert conftest.exists()

    def test_pyproject_has_flask_dependency(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        content = (tmp_path / "myapi" / "pyproject.toml").read_text(encoding="utf-8")
        assert "flask" in content

    def test_creates_compliance_dir(self, tmp_path):
        scaffold_api(str(tmp_path), "myapi")
        assert (tmp_path / "myapi" / "compliance").is_dir()


# ---------------------------------------------------------------------------
# TestScaffoldCli
# ---------------------------------------------------------------------------
class TestScaffoldCli:
    """CLI scaffolding: cli.py with argparse template."""

    def test_creates_cli_py(self, tmp_path):
        scaffold_cli(str(tmp_path), "mytool")
        cli_py = tmp_path / "mytool" / "src" / "cli.py"
        assert cli_py.exists()

    def test_cli_contains_argparse(self, tmp_path):
        scaffold_cli(str(tmp_path), "mytool")
        content = (tmp_path / "mytool" / "src" / "cli.py").read_text(encoding="utf-8")
        assert "argparse" in content

    def test_cli_has_build_parser(self, tmp_path):
        scaffold_cli(str(tmp_path), "mytool")
        content = (tmp_path / "mytool" / "src" / "cli.py").read_text(encoding="utf-8")
        assert "build_parser" in content

    def test_pyproject_has_script_entry(self, tmp_path):
        scaffold_cli(str(tmp_path), "mytool")
        content = (tmp_path / "mytool" / "pyproject.toml").read_text(encoding="utf-8")
        assert "mytool" in content
        assert "src.cli:main" in content


# ---------------------------------------------------------------------------
# TestScaffoldMicroservice
# ---------------------------------------------------------------------------
class TestScaffoldMicroservice:
    """Microservice scaffolding: backend + k8s/ directory."""

    def test_creates_k8s_directory(self, tmp_path):
        scaffold_microservice(str(tmp_path), "svc")
        assert (tmp_path / "svc" / "k8s").is_dir()

    def test_creates_deployment_yaml(self, tmp_path):
        scaffold_microservice(str(tmp_path), "svc")
        deployment = tmp_path / "svc" / "k8s" / "deployment.yaml"
        assert deployment.exists()

    def test_creates_service_yaml(self, tmp_path):
        scaffold_microservice(str(tmp_path), "svc")
        service = tmp_path / "svc" / "k8s" / "service.yaml"
        assert service.exists()

    def test_deployment_has_cui_marking(self, tmp_path):
        scaffold_microservice(str(tmp_path), "svc")
        content = (tmp_path / "svc" / "k8s" / "deployment.yaml").read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in content

    def test_also_has_python_backend_files(self, tmp_path):
        files = scaffold_microservice(str(tmp_path), "svc")
        # Should include pyproject.toml from python backend scaffold
        pyproject = tmp_path / "svc" / "pyproject.toml"
        assert pyproject.exists()
        assert str(pyproject) in files


# ---------------------------------------------------------------------------
# TestReadmeContent
# ---------------------------------------------------------------------------
class TestReadmeContent:
    """README generation with project name and CUI banners."""

    def test_readme_includes_project_name(self):
        content = _readme_content("myproj", "api")
        assert "# myproj" in content

    def test_readme_includes_cui_banner(self):
        content = _readme_content("myproj", "api")
        assert "CUI // SP-CTI" in content

    def test_readme_includes_distribution(self):
        content = _readme_content("myproj", "api")
        assert "Distribution" in content

    def test_readme_includes_compliance_section(self):
        content = _readme_content("myproj", "api")
        assert "compliance/" in content


# ---------------------------------------------------------------------------
# TestCommonGitignore
# ---------------------------------------------------------------------------
class TestCommonGitignore:
    """Gitignore content for Python/JS projects."""

    def test_includes_pycache(self):
        content = _common_gitignore()
        assert "__pycache__/" in content

    def test_includes_pyc_pattern(self):
        content = _common_gitignore()
        assert "*.py[cod]" in content

    def test_includes_env_file(self):
        content = _common_gitignore()
        assert ".env" in content

    def test_includes_node_modules(self):
        content = _common_gitignore()
        assert "node_modules/" in content

    def test_includes_coverage(self):
        content = _common_gitignore()
        assert ".coverage" in content


# [TEMPLATE: CUI // SP-CTI]
