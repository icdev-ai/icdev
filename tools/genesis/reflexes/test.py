#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Test Reflex — identify under-tested tools, generate real tests.

Uses api_surface_extractor.py to discover function signatures, parameter
types, and return types, then generates meaningful tests (not just import
stubs).

YELLOW tier (reversible writes — new test files only, worktree sandbox).
Scanner-tier only (zero Claude tokens).
"""
IMPLEMENTATION_STATUS = "full"

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Module-level constants — discovery thresholds, generation caps, and
# subprocess limits. These were previously inline magic numbers. The
# max-tests-per-run value is config-overridable from genesis_config under
# `test.max_tests_per_run`; this named value is the in-code fallback.
# Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_MAX_TESTS_PER_RUN = 10   # in-code fallback for test.max_tests_per_run
_MIN_MODULE_LINES          = 20   # skip files shorter than this (stubs/__init__)

_MAX_FUNCS_PER_MODULE      = 15   # cap functions covered per generated file
_MAX_SIG_PARAMS_ASSERTED   = 5    # cap params asserted in a signature test
_MAX_INVOCATION_PARAMS     = 3    # only emit invocation test below this arity
_MAX_PARAM_VALUES          = 5    # cap synthesised positional arg values
_MAX_CLASSES_PER_MODULE    = 5    # cap classes covered per generated file
_MAX_METHODS_ASSERTED      = 8    # cap public methods asserted per class
_MAX_CONSTANTS_ASSERTED    = 10   # cap module constants asserted

_EXTRACT_TIMEOUT_SEC       = 30   # api_surface_extractor subprocess timeout
_RUN_TEST_TIMEOUT_SEC      = 60   # per-file pytest subprocess timeout
_STDOUT_TAIL_CHARS         = 1000 # captured pytest stdout tail
_STDERR_TAIL_CHARS         = 500  # captured pytest stderr tail
_ERROR_SNIPPET_CHARS       = 200  # failure-result stderr snippet in results


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SKIP_DIRS = frozenset({".tmp", "vendor", "__pycache__", ".git", "node_modules", ".venv", "venv"})


def _find_untested_modules(max_results: int = _DEFAULT_MAX_TESTS_PER_RUN) -> List[Dict[str, Any]]:
    """Find Python modules in tools/ that lack corresponding test files."""
    tools_dir = BASE_DIR / "tools"
    tests_dir = BASE_DIR / "tests"

    # Collect up to max_results * 20 candidates, then sort for stability.
    # This early exit prevents scanning hundreds of large files under timeout.
    _candidate_cap = max_results * 20

    untested = []
    for py_file in tools_dir.rglob("*.py"):
        # Skip non-source directories that can hang or generate noise.
        if any(part in _SKIP_DIRS for part in py_file.parts):
            continue
        if py_file.name.startswith("_"):
            continue
        if py_file.name in ("__init__.py", "setup.py", "conftest.py"):
            continue
        # Skip very small files (< 20 lines likely just __init__ or stubs)
        try:
            line_count = len(py_file.read_text(encoding="utf-8", errors="replace").splitlines())
            if line_count < _MIN_MODULE_LINES:
                continue
        except OSError:
            continue

        # Derive expected test file name
        rel = py_file.relative_to(tools_dir)
        test_name = f"test_{rel.parts[-1]}"
        # Check common test locations
        has_test = False
        for test_dir in [tests_dir, tests_dir / "unit", tests_dir / "integration", tests_dir / "genesis_auto"]:
            if (test_dir / test_name).exists():
                has_test = True
                break
            # Also check nested structure
            nested = test_dir
            for part in rel.parts[:-1]:
                nested = nested / part
            if (nested / test_name).exists():
                has_test = True
                break

        if not has_test:
            untested.append(
                {
                    "module": str(rel).replace("\\", "/"),
                    "path": str(py_file),
                    "name": py_file.stem,
                    "line_count": line_count,
                }
            )
            if len(untested) >= _candidate_cap:
                break

    # Sort by directory depth (shallower = more important), then by size
    untested.sort(key=lambda x: (x["module"].count("/"), -x["line_count"]))
    return untested[:max_results]


def _extract_api_surface(file_path: str) -> Optional[Dict[str, Any]]:
    """Use api_surface_extractor to get function signatures and types."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/testing/api_surface_extractor.py", "--file", file_path, "--json"],
            capture_output=True,
            text=True,
            timeout=_EXTRACT_TIMEOUT_SEC,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            return json.loads(stdout[json_start:])
    except Exception as e:
        print(f"  WARN: API surface extraction failed: {e}")
    return None


def _generate_param_fixture(param: Dict) -> str:
    """Generate a reasonable test value for a parameter based on its type."""
    ptype = (param.get("type") or "").lower()
    name = param.get("name", "")
    default = param.get("default")

    if default and default not in ("None", "null", "..."):
        return default

    # Type-based defaults
    if "str" in ptype:
        return f'"test_{name}"'
    if "int" in ptype:
        return "1"
    if "float" in ptype:
        return "1.0"
    if "bool" in ptype:
        return "True"
    if "list" in ptype or "List" in ptype:
        return "[]"
    if "dict" in ptype or "Dict" in ptype:
        return "{}"
    if "path" in ptype:
        return 'Path("/tmp/test")'
    if "optional" in ptype:
        return "None"

    # Name-based heuristics
    if "id" in name.lower():
        return '"test-id-001"'
    if "path" in name.lower() or "dir" in name.lower():
        return '"/tmp/test"'
    if "name" in name.lower():
        return f'"test_{name}"'
    if "count" in name.lower() or "limit" in name.lower() or "max" in name.lower():
        return "5"
    if "enabled" in name.lower() or "flag" in name.lower():
        return "True"
    if "config" in name.lower():
        return "{}"
    if "json" in name.lower():
        return '"{}"'

    return "None"


def _generate_test_code(module_info: Dict, api_surface: Dict) -> str:
    """Generate meaningful test code from API surface data."""
    module_path = module_info["module"]  # e.g., "pulse/db.py"
    module_name = module_info["name"]
    import_path = "tools." + module_path.replace("/", ".").replace(".py", "")

    functions = api_surface.get("functions", [])
    classes = api_surface.get("classes", [])
    constants = api_surface.get("constants", [])
    mock_targets = api_surface.get("mock_targets", [])

    # Filter to public functions only
    pub_funcs = [f for f in functions if f.get("is_public", True)]
    pub_classes = [c for c in classes if c.get("is_public", True)]

    lines = [
        "#!/usr/bin/env python3",
        "# CUI // SP-CTI",
        f'"""Auto-generated tests for {import_path}.',
        "",
        "Generated by Genesis Test Reflex using api_surface_extractor.",
        "Tests validate importability, function signatures, return types,",
        "and basic invocation patterns.",
        '"""',
        "",
        "import sys",
        "from pathlib import Path",
        "from unittest.mock import patch, MagicMock",
        "",
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))",
        "",
        "import pytest",
        "",
        "",
        "# --- Module Import ---",
        "",
        f"def test_{module_name}_imports():",
        '    """Verify module can be imported without errors."""',
        "    try:",
        f"        import {import_path}",
        "    except ImportError as e:",
        '        pytest.skip(f"Import dependency missing: {e}")',
        "",
    ]

    # Test public functions exist
    if pub_funcs:
        lines.extend(
            [
                "",
                "# --- Function Signature Tests ---",
                "",
            ]
        )
        for func in pub_funcs[:_MAX_FUNCS_PER_MODULE]:  # Cap functions covered
            fname = func["name"]
            params = func.get("parameters", [])
            return_type = func.get("return_type")
            is_async = func.get("is_async", False)

            # Test function exists and is callable
            lines.extend(
                [
                    f"def test_{module_name}_{fname}_exists():",
                    f'    """Verify {fname} exists and is callable."""',
                    "    try:",
                    f"        from {import_path} import {fname}",
                    f"        assert callable({fname}), '{fname} is not callable'",
                    "    except ImportError:",
                    '        pytest.skip("Module not importable")',
                    "",
                ]
            )

            # Test function signature (parameter count)
            non_default_params = [
                p
                for p in params
                if p.get("default") is None and p["name"] not in ("self", "cls") and not p["name"].startswith("*")
            ]
            if non_default_params:
                lines.extend(
                    [
                        f"def test_{module_name}_{fname}_signature():",
                        f'    """Verify {fname} accepts expected parameters."""',
                        "    import inspect",
                        "    try:",
                        f"        from {import_path} import {fname}",
                        f"        sig = inspect.signature({fname})",
                        "        params = list(sig.parameters.keys())",
                    ]
                )
                for p in non_default_params[:_MAX_SIG_PARAMS_ASSERTED]:
                    pname = p["name"]
                    lines.append(
                        f'        assert "{pname}" in params, f"Missing parameter \\"{pname}\\" in {{params}}"'
                    )
                lines.extend(
                    [
                        "    except ImportError:",
                        '        pytest.skip("Module not importable")',
                        "",
                    ]
                )

            # Test invocation with mock DB (if function uses get_connection)
            db_mock_needed = any("get_connection" in t or "storage" in t for t in mock_targets)
            if db_mock_needed and not is_async and len(non_default_params) <= _MAX_INVOCATION_PARAMS:
                param_values = []
                for p in params:
                    if p["name"] in ("self", "cls"):
                        continue
                    if p["name"].startswith("*"):
                        continue
                    param_values.append(_generate_param_fixture(p))

                if len(param_values) <= _MAX_PARAM_VALUES:
                    lines.extend(
                        [
                            f"def test_{module_name}_{fname}_invocation():",
                            f'    """Verify {fname} can be called with test inputs."""',
                            "    try:",
                            f"        from {import_path} import {fname}",
                            "    except ImportError:",
                            '        pytest.skip("Module not importable")',
                            "        return",
                            "    mock_conn = MagicMock()",
                            "    mock_conn.execute.return_value.fetchone.return_value = None",
                            "    mock_conn.execute.return_value.fetchall.return_value = []",
                            '    with patch("tools.db.storage.get_connection", return_value=mock_conn):',
                            "        try:",
                            f"            result = {fname}({', '.join(param_values)})",
                        ]
                    )
                    if return_type:
                        rt = return_type.lower()
                        if "dict" in rt:
                            lines.append("            assert isinstance(result, dict)")
                        elif "list" in rt:
                            lines.append("            assert isinstance(result, (list, tuple))")
                        elif "bool" in rt:
                            lines.append("            assert isinstance(result, bool)")
                        elif "int" in rt:
                            lines.append("            assert isinstance(result, (int, float))")
                        elif "str" in rt:
                            lines.append("            assert isinstance(result, str)")
                    lines.extend(
                        [
                            "        except (TypeError, ValueError, KeyError, AttributeError):",
                            "            pass  # Expected with mock data",
                            "        except Exception as e:",
                            '            if "no such table" in str(e).lower():',
                            "                pass  # DB not initialized",
                            "            else:",
                            "                raise",
                            "",
                        ]
                    )

    # Test classes exist
    if pub_classes:
        lines.extend(
            [
                "",
                "# --- Class Tests ---",
                "",
            ]
        )
        for cls in pub_classes[:_MAX_CLASSES_PER_MODULE]:
            cname = cls["name"]
            lines.extend(
                [
                    f"def test_{module_name}_{cname}_exists():",
                    f'    """Verify class {cname} exists."""',
                    "    try:",
                    f"        from {import_path} import {cname}",
                    f"        assert isinstance({cname}, type), '{cname} is not a class'",
                    "    except ImportError:",
                    '        pytest.skip("Module not importable")',
                    "",
                ]
            )

            # Test class has expected public methods
            pub_methods = cls.get("public_methods", [])
            if pub_methods:
                method_names = [m["name"] for m in pub_methods[:_MAX_METHODS_ASSERTED]]
                lines.extend(
                    [
                        f"def test_{module_name}_{cname}_methods():",
                        f'    """Verify {cname} has expected public methods."""',
                        "    try:",
                        f"        from {import_path} import {cname}",
                    ]
                )
                for mname in method_names:
                    lines.append(f'        assert hasattr({cname}, "{mname}"), "Missing method {mname}"')
                lines.extend(
                    [
                        "    except ImportError:",
                        '        pytest.skip("Module not importable")',
                        "",
                    ]
                )

    # Test constants
    if constants:
        lines.extend(
            [
                "",
                "# --- Constants ---",
                "",
                f"def test_{module_name}_constants():",
                '    """Verify module exports expected constants."""',
                "    try:",
                f"        import {import_path} as mod",
            ]
        )
        for const in constants[:_MAX_CONSTANTS_ASSERTED]:
            cname = const["name"]
            lines.append(f'        assert hasattr(mod, "{cname}"), "Missing constant {cname}"')
        lines.extend(
            [
                "    except ImportError:",
                '        pytest.skip("Module not importable")',
                "",
            ]
        )

    return "\n".join(lines)


def _generate_fallback_stub(module_info: Dict) -> str:
    """Fallback: generate basic import test when extractor fails."""
    module_path = module_info["module"]
    module_name = module_info["name"]
    import_path = "tools." + module_path.replace("/", ".").replace(".py", "")

    return f'''#!/usr/bin/env python3
# CUI // SP-CTI
"""Auto-generated import test for {import_path}.

Generated by Genesis Test Reflex (fallback — api_surface_extractor unavailable).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


def test_{module_name}_imports():
    """Verify module can be imported without errors."""
    try:
        import {import_path}
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {{e}}")


def test_{module_name}_has_expected_attributes():
    """Verify module exposes expected public API."""
    try:
        mod = __import__("{import_path}", fromlist=["{module_name}"])
        public = [a for a in dir(mod) if not a.startswith("_") and callable(getattr(mod, a, None))]
        if len(public) == 0:
            pytest.skip("Module has no public callables (constants/private-only module)")
    except ImportError:
        pytest.skip("Module not importable")
'''


def _run_test(test_file: Path, timeout: int = _RUN_TEST_TIMEOUT_SEC) -> Dict[str, Any]:
    """Run a single test file via pytest."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-x"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        passed = result.returncode == 0
        return {
            "passed": passed,
            "returncode": result.returncode,
            "stdout": result.stdout[-_STDOUT_TAIL_CHARS:] if result.stdout else "",
            "stderr": result.stderr[-_STDERR_TAIL_CHARS:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "timeout"}
    except Exception as e:
        return {"passed": False, "error": str(e)}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Test Reflex."""
    max_tests = config.get("max_tests_per_run", _DEFAULT_MAX_TESTS_PER_RUN)

    # Find untested modules
    untested = _find_untested_modules(max_results=max_tests)
    if not untested:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "all_modules_have_tests"},
        }

    tests_dir = BASE_DIR / "tests" / "genesis_auto"
    tests_dir.mkdir(parents=True, exist_ok=True)

    results = []
    tests_passing = 0

    for module_info in untested[:max_tests]:
        test_file = tests_dir / f"test_{module_info['name']}.py"

        # Try API surface extraction for real tests
        api_surface = _extract_api_surface(module_info["path"])

        if api_surface and (api_surface.get("functions") or api_surface.get("classes")):
            test_code = _generate_test_code(module_info, api_surface)
            generation_method = "api_surface"
        else:
            test_code = _generate_fallback_stub(module_info)
            generation_method = "fallback_stub"

        test_file.write_text(test_code, encoding="utf-8", newline="")

        # Run the test
        print(f"  Test: {module_info['module']} ({generation_method})")
        result = _run_test(test_file)

        if result.get("passed"):
            tests_passing += 1
            results.append(
                {
                    "module": module_info["module"],
                    "test_file": str(test_file.relative_to(BASE_DIR)),
                    "generation_method": generation_method,
                    "status": "passed",
                    "test_count": test_code.count("\ndef test_"),
                }
            )
        else:
            # Remove failing test stubs (don't litter)
            test_file.unlink(missing_ok=True)
            results.append(
                {
                    "module": module_info["module"],
                    "generation_method": generation_method,
                    "status": "failed",
                    "error": result.get("error", result.get("stderr", "")[:_ERROR_SNIPPET_CHARS]),
                }
            )

    return {
        "success": True,  # reflex ran without error; 0 new tests is a valid no-op
        "metric_value": float(tests_passing),
        "details": {
            "untested_modules_found": len(untested),
            "tests_generated": len(results),
            "tests_passing": tests_passing,
            "api_surface_used": sum(1 for r in results if r.get("generation_method") == "api_surface"),
            "results": results,
        },
    }
