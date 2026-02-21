# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.mosa.mosa_code_enforcer -- MOSA code violation detection."""

import pytest

from tools.mosa.mosa_code_enforcer import (
    _build_module_map,
    _check_direct_coupling,
    _check_missing_openapi,
    _extract_imports,
    scan_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_py(path: Path, content: str):
    """Write a Python file, creating parent dirs and __init__.py as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init = path.parent / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# TestDirectCouplingDetection
# ---------------------------------------------------------------------------

class TestDirectCouplingDetection:
    """_check_direct_coupling (MOSA-V001): internal module imports."""

    def test_no_violation_for_public_import(self, tmp_path):
        modules = {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}
        imports = [{"module": "beta.api", "line": 1, "names": ["handler"]}]
        result = _check_direct_coupling(tmp_path / "alpha" / "x.py", imports, modules, "alpha", False)
        assert len(result) == 0

    def test_violation_for_private_submodule(self, tmp_path):
        modules = {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}
        imports = [{"module": "beta._internal.secret", "line": 5, "names": ["helper"]}]
        result = _check_direct_coupling(tmp_path / "alpha" / "x.py", imports, modules, "alpha", False)
        assert len(result) == 1
        assert result[0]["id"] == "MOSA-V001"
        assert result[0]["severity"] == "HIGH"

    def test_same_module_ignored(self, tmp_path):
        modules = {"alpha": tmp_path / "alpha"}
        imports = [{"module": "alpha._internal", "line": 1, "names": ["x"]}]
        result = _check_direct_coupling(tmp_path / "alpha" / "y.py", imports, modules, "alpha", False)
        assert len(result) == 0

    def test_fix_suggestion_included(self, tmp_path):
        modules = {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}
        imports = [{"module": "beta._priv.core", "line": 3, "names": ["x"]}]
        result = _check_direct_coupling(tmp_path / "alpha" / "z.py", imports, modules, "alpha", True)
        assert len(result) == 1
        assert "suggestion" in result[0]
        assert "public API" in result[0]["suggestion"]


# ---------------------------------------------------------------------------
# TestInterfaceCoverage
# ---------------------------------------------------------------------------

class TestInterfaceCoverage:
    """_check_missing_openapi (MOSA-V002): API files without OpenAPI spec."""

    def test_non_api_file_no_violation(self, tmp_path):
        f = tmp_path / "utils.py"
        f.write_text("x = 1\n", encoding="utf-8")
        result = _check_missing_openapi(f, False)
        assert result is None

    def test_flask_route_without_spec(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text('@app.route("/health")\ndef health(): return "ok"\n', encoding="utf-8")
        result = _check_missing_openapi(f, False)
        assert result is not None
        assert result["id"] == "MOSA-V002"
        assert result["severity"] == "MEDIUM"

    def test_flask_route_with_spec_no_violation(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text('@app.route("/health")\ndef health(): return "ok"\n', encoding="utf-8")
        (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
        result = _check_missing_openapi(f, False)
        assert result is None


# ---------------------------------------------------------------------------
# TestFixSuggestions
# ---------------------------------------------------------------------------

class TestFixSuggestions:
    """Fix suggestions included when fix_suggestions=True."""

    def test_missing_openapi_has_suggestion(self, tmp_path):
        f = tmp_path / "api.py"
        f.write_text('@app.route("/data")\ndef data(): pass\n', encoding="utf-8")
        result = _check_missing_openapi(f, True)
        assert result is not None
        assert "suggestion" in result
        assert "openapi.yaml" in result["suggestion"]


# ---------------------------------------------------------------------------
# TestProjectScan
# ---------------------------------------------------------------------------

class TestProjectScan:
    """scan_project: full project scan orchestration."""

    def test_clean_project_passes(self, tmp_path):
        _write_py(tmp_path / "alpha" / "api.py", "x = 1\n")
        (tmp_path / "alpha" / "openapi.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
        result = scan_project(str(tmp_path))
        assert result["tool"] == "mosa_code_enforcer"
        assert result["pass"] is True
        assert result["total_violations"] == 0

    def test_scan_detects_violations(self, tmp_path):
        _write_py(tmp_path / "alpha" / "svc.py",
                   "from beta._internal import secret\n"
                   '@app.route("/x")\ndef x(): pass\n')
        _write_py(tmp_path / "beta" / "pub.py", "val = 1\n")
        (tmp_path / "beta" / "_internal").mkdir(parents=True, exist_ok=True)
        (tmp_path / "beta" / "_internal" / "__init__.py").write_text("secret = 1\n", encoding="utf-8")
        result = scan_project(str(tmp_path), fix_suggestions=True)
        assert result["total_violations"] >= 1
        ids = {v["id"] for v in result["violations"]}
        assert "MOSA-V001" in ids or "MOSA-V002" in ids or "MOSA-V003" in ids

    def test_scan_result_has_severity_counts(self, tmp_path):
        _write_py(tmp_path / "pkg" / "x.py", "y = 1\n")
        result = scan_project(str(tmp_path))
        assert "violations_by_severity" in result
        assert "HIGH" in result["violations_by_severity"]
        assert "MEDIUM" in result["violations_by_severity"]


# CUI // SP-CTI
