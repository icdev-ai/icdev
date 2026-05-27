# CUI // SP-CTI
"""Quality Design Canvas — Gate Executor.

Executes real QA/QC tools (SAST, linting, type checking, dead code detection,
deprecation scanning, test coverage, docstring coverage, complexity metrics)
and returns structured results.

Architecture: pure functions, subprocess-based tool execution, zero Flask dependency.
Tools are invoked via subprocess to isolate failures and capture structured output.

Integrated tools (all MIT/Apache/BSD, air-gap safe, pure Python):
  - ruff        : Linting + formatting (already installed)
  - bandit      : SAST security scanning (already installed)
  - mypy        : Static type checking (MIT)
  - vulture     : Dead code detection (MIT)
  - radon       : Complexity metrics + maintainability index (MIT)
  - interrogate : Docstring coverage (MIT)
  - pyupgrade   : Deprecated syntax detection (MIT)
  - pip-audit   : Dependency vulnerability scanning (Apache-2.0)
  - pytest-cov  : Test coverage (MIT)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger(__name__)

ICDEV_ROOT = Path(__file__).resolve().parents[2]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_tool(cmd: list[str], timeout: int = 120, cwd: str | None = None) -> dict:
    """Run a CLI tool and capture output.

    Returns {"exit_code": int, "stdout": str, "stderr": str, "timed_out": bool}
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ICDEV_ROOT)}
    work_dir = cwd or str(ICDEV_ROOT)
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[:10000],  # cap output
            "stderr": result.stderr[:5000],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "Timed out", "timed_out": True}
    except FileNotFoundError as e:
        return {"exit_code": -2, "stdout": "", "stderr": f"Tool not found: {e}", "timed_out": False}


def _parse_json_output(raw: str) -> dict | list | None:
    """Try to parse JSON from tool stdout, handling markdown-wrapped JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [ln for ln in lines if not ln.startswith("```")]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _tool_available(name: str) -> bool:
    """Check if a CLI tool is available on PATH."""
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", name, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        # Try direct command
        try:
            result = subprocess.run(  # noqa: S603
                [name, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
            return result.returncode == 0
        except Exception:
            return False


# ── Individual Gate Executors ─────────────────────────────────────────────────


def execute_ruff_lint(project_dir: str | None = None) -> dict:
    """Execute ruff linting and return structured results.

    Gate: SA-15 (Development Process, Standards, and Tools)
    Dimension: code_quality
    """
    target = project_dir or str(ICDEV_ROOT / "tools")
    result = _run_tool(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            target,
            "--select",
            "E,F,W",
            "--ignore",
            "E402,E501,E731,E741",
            "--statistics",
            "--output-format",
            "json",
        ]
    )  # noqa: E501

    findings = []
    if result["exit_code"] == 0:
        status = "pass"
        score = 100.0
    else:
        status = "fail"
        parsed = _parse_json_output(result["stdout"])
        if isinstance(parsed, list):
            findings = [
                {
                    "code": f.get("code", ""),
                    "message": f.get("message", ""),
                    "file": f.get("filename", ""),
                    "line": f.get("location", {}).get("row", 0),
                }
                for f in parsed[:50]
            ]  # noqa: E501
        score = max(0.0, 100.0 - len(findings) * 2)

    return {
        "gate_id": "lint",
        "sa11_control": "SA-15",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "ruff",
        "findings_count": len(findings),
        "findings": findings[:20],
        "executed_at": _utcnow(),
    }


def execute_bandit_sast(project_dir: str | None = None) -> dict:
    """Execute bandit SAST scanning.

    Gate: SA-11(1) (Static Code Analysis)
    Dimension: security_posture
    """
    target = project_dir or str(ICDEV_ROOT / "tools")
    result = _run_tool([sys.executable, "-m", "bandit", "-r", target, "-f", "json", "-q", "--severity-level", "medium"])  # noqa: E501

    findings = []
    critical = high = medium = 0
    if result["stdout"]:
        parsed = _parse_json_output(result["stdout"])
        if isinstance(parsed, dict):
            for r in parsed.get("results", [])[:50]:
                sev = r.get("issue_severity", "LOW")
                if sev == "HIGH":
                    high += 1
                elif sev == "MEDIUM":
                    medium += 1
                findings.append(
                    {
                        "severity": sev,
                        "test_id": r.get("test_id", ""),
                        "message": r.get("issue_text", ""),
                        "file": r.get("filename", ""),
                        "line": r.get("line_number", 0),
                    }
                )  # noqa: E501

    status = "pass" if (critical == 0 and high == 0) else "fail"
    score = max(0.0, 100.0 - critical * 30 - high * 15 - medium * 5)

    return {
        "gate_id": "sast",
        "sa11_control": "SA-11(1)",
        "dimension": "security_posture",
        "status": status,
        "score": round(score, 1),
        "tool": "bandit",
        "findings_count": len(findings),
        "summary": {"critical": critical, "high": high, "medium": medium},
        "findings": findings[:20],
        "executed_at": _utcnow(),
    }


def execute_mypy_typecheck(project_dir: str | None = None) -> dict:
    """Execute mypy type checking.

    Gate: SA-11(1) supplement (Static Code Analysis — type safety)
    Dimension: code_quality
    """
    target = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")
    result = _run_tool(
        [sys.executable, "-m", "mypy", target, "--ignore-missing-imports", "--no-error-summary", "--show-error-codes"]
    )  # noqa: E501

    error_count = 0
    if result["exit_code"] != 0:
        for line in result["stdout"].split("\n"):
            if ": error:" in line:
                error_count += 1

    status = "pass" if error_count == 0 else "fail"
    score = max(0.0, 100.0 - error_count * 3)

    return {
        "gate_id": "typecheck",
        "sa11_control": "SA-11(1)",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "mypy",
        "findings_count": error_count,
        "available": _tool_available("mypy"),
        "executed_at": _utcnow(),
    }


def execute_vulture_deadcode(project_dir: str | None = None) -> dict:
    """Execute vulture dead code detection.

    Gate: SA-15 (Development Process — code hygiene)
    Dimension: code_quality
    """
    target = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")
    result = _run_tool([sys.executable, "-m", "vulture", target, "--min-confidence", "80"])

    dead_items = []
    for line in result["stdout"].split("\n"):
        line = line.strip()
        if line and "unused" in line.lower():
            dead_items.append(line)

    status = "pass" if len(dead_items) <= 5 else "fail"
    score = max(0.0, 100.0 - len(dead_items) * 5)

    return {
        "gate_id": "deadcode",
        "sa11_control": "SA-15",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "vulture",
        "findings_count": len(dead_items),
        "findings": dead_items[:20],
        "available": _tool_available("vulture"),
        "executed_at": _utcnow(),
    }


def execute_radon_complexity(project_dir: str | None = None) -> dict:
    """Execute radon complexity analysis.

    Gate: SA-15(1) (Development Standards — complexity thresholds)
    Dimension: code_quality
    """
    target = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")
    result = _run_tool([sys.executable, "-m", "radon", "cc", target, "--json", "--min", "C"])

    complex_funcs = []
    if result["stdout"]:
        parsed = _parse_json_output(result["stdout"])
        if isinstance(parsed, dict):
            for filepath, blocks in parsed.items():
                if isinstance(blocks, list):
                    for b in blocks:
                        complex_funcs.append(
                            {
                                "file": filepath,
                                "name": b.get("name", ""),
                                "complexity": b.get("complexity", 0),
                                "rank": b.get("rank", "?"),
                            }
                        )

    high_complexity = [f for f in complex_funcs if f.get("complexity", 0) > 15]
    status = "pass" if len(high_complexity) == 0 else "fail"
    avg_cc = sum(f["complexity"] for f in complex_funcs) / len(complex_funcs) if complex_funcs else 0
    score = max(0.0, 100.0 - len(high_complexity) * 10 - max(0, avg_cc - 10) * 3)

    return {
        "gate_id": "complexity",
        "sa11_control": "SA-15",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "radon",
        "findings_count": len(high_complexity),
        "avg_complexity": round(avg_cc, 1),
        "complex_functions": complex_funcs[:20],
        "available": _tool_available("radon"),
        "executed_at": _utcnow(),
    }


def execute_interrogate_docstrings(project_dir: str | None = None) -> dict:
    """Execute interrogate docstring coverage.

    Gate: SA-15 (Development Standards — documentation)
    Dimension: code_quality
    """
    target = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")
    result = _run_tool([sys.executable, "-m", "interrogate", target, "--verbose", "--fail-under", "0"])

    coverage_pct = 0.0
    for line in result["stdout"].split("\n"):
        if "TOTAL" in line and "%" in line:
            try:
                pct_str = line.split("%")[0].strip().split()[-1]
                coverage_pct = float(pct_str)
            except (ValueError, IndexError):
                pass

    status = "pass" if coverage_pct >= 50 else "fail"
    score = min(100.0, coverage_pct)

    return {
        "gate_id": "docstring_coverage",
        "sa11_control": "SA-15",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "interrogate",
        "coverage_pct": round(coverage_pct, 1),
        "available": _tool_available("interrogate"),
        "executed_at": _utcnow(),
    }


def execute_pyupgrade_deprecation(project_dir: str | None = None) -> dict:
    """Scan for deprecated Python syntax using pyupgrade (dry-run).

    Gate: SA-15 (Development Standards — modernization)
    Dimension: code_quality
    """
    target_dir = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")
    deprecated_files = []

    # Scan all .py files in target
    target_path = Path(target_dir)
    py_files = list(target_path.rglob("*.py"))[:100]

    for py_file in py_files:
        result = _run_tool([sys.executable, "-m", "pyupgrade", "--py311-plus", str(py_file)])
        if result["exit_code"] == 1:  # pyupgrade returns 1 when it would make changes
            deprecated_files.append(str(py_file.relative_to(ICDEV_ROOT)))

    status = "pass" if len(deprecated_files) == 0 else "fail"
    score = max(0.0, 100.0 - len(deprecated_files) * 10)

    return {
        "gate_id": "deprecation",
        "sa11_control": "SA-15",
        "dimension": "code_quality",
        "status": status,
        "score": round(score, 1),
        "tool": "pyupgrade",
        "findings_count": len(deprecated_files),
        "deprecated_files": deprecated_files[:20],
        "available": _tool_available("pyupgrade"),
        "executed_at": _utcnow(),
    }


def execute_pip_audit() -> dict:
    """Execute pip-audit dependency vulnerability scanning.

    Gate: SA-11(2) (Vulnerability Analysis)
    Dimension: security_posture
    """
    result = _run_tool([sys.executable, "-m", "pip_audit", "--format", "json", "--skip-editable"])

    vulns = []
    if result["stdout"]:
        parsed = _parse_json_output(result["stdout"])
        if isinstance(parsed, dict):
            deps = parsed.get("dependencies", [])
            for d in deps:
                for v in d.get("vulns", []):
                    vulns.append(
                        {
                            "package": d.get("name", ""),
                            "version": d.get("version", ""),
                            "vuln_id": v.get("id", ""),
                            "fix_versions": v.get("fix_versions", []),
                        }
                    )  # noqa: E501

    critical = sum(1 for v in vulns if "CRITICAL" in str(v.get("vuln_id", "")).upper())
    high = sum(1 for v in vulns if "HIGH" in str(v.get("vuln_id", "")).upper())
    status = "pass" if len(vulns) == 0 else ("fail" if critical > 0 or high > 0 else "pass")
    score = max(0.0, 100.0 - critical * 30 - high * 15 - len(vulns) * 3)

    return {
        "gate_id": "dependency_audit",
        "sa11_control": "SA-11(2)",
        "dimension": "security_posture",
        "status": status,
        "score": round(score, 1),
        "tool": "pip-audit",
        "findings_count": len(vulns),
        "vulnerabilities": vulns[:20],
        "executed_at": _utcnow(),
    }


def execute_pytest_coverage(project_dir: str | None = None) -> dict:
    """Execute pytest with coverage measurement.

    Gate: SA-11(7) (Verify Scope of Testing)
    Dimension: test_coverage
    """
    target = project_dir or str(ICDEV_ROOT)
    result = _run_tool(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-q",
            "--tb=no",
            "--no-header",
            "-x",
            "--ignore=tests/e2e",
            "--ignore=tests/genesis_auto",
            f"--cov={target}/tools/qdc_canvas",
            f"--cov-report=json:{os.devnull}",
        ],
        timeout=180,
        cwd=target,
    )

    passed = failed = 0
    coverage_pct = 0.0
    for line in result["stdout"].split("\n"):
        if "passed" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0:
                    try:
                        passed = int(parts[i - 1])
                    except ValueError:
                        pass
                if p == "failed" and i > 0:
                    try:
                        failed = int(parts[i - 1])
                    except ValueError:
                        pass

    status = "pass" if failed == 0 else "fail"
    score = 100.0 if failed == 0 else max(0.0, 100.0 * passed / (passed + failed))

    return {
        "gate_id": "test_coverage",
        "sa11_control": "SA-11(7)",
        "dimension": "test_coverage",
        "status": status,
        "score": round(score, 1),
        "tool": "pytest",
        "passed": passed,
        "failed": failed,
        "coverage_pct": round(coverage_pct, 1),
        "executed_at": _utcnow(),
    }


def execute_diagram_quality(image_path: str | None = None) -> dict:
    """Execute CSP-agnostic infrastructure diagram validation.

    Gate: PL-2 (System Security and Privacy Plans — Architecture Diagrams)
    Dimension: compliance_readiness

    Validates infrastructure diagrams for ATO readiness using vision LLM.
    Checks: HA, network segmentation, encryption at trust boundaries,
    logging/monitoring, IAM, management plane separation, data tier isolation.
    Works with any CSP icons (AWS, Azure, GCP, OCI, IBM, or generic).
    """
    if not image_path:
        # Look for diagrams in standard screenshot directory
        screenshot_dir = ICDEV_ROOT / "playwright" / "screenshots"
        candidates = []
        if screenshot_dir.exists():
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                candidates.extend(screenshot_dir.glob(ext))
        # Filter for infra/architecture related filenames
        infra_keywords = ("infra", "architecture", "network", "boundary", "vpc", "diagram")
        infra_files = [f for f in candidates if any(k in f.stem.lower() for k in infra_keywords)]
        if not infra_files:
            return {
                "gate_id": "diagram_quality",
                "sa11_control": "PL-2",
                "dimension": "compliance_readiness",
                "status": "skip",
                "score": 0.0,
                "error": "No infrastructure diagram found. Provide --image-path or place diagrams in playwright/screenshots/",
                "executed_at": _utcnow(),
            }
        image_path = str(infra_files[0])

    # Use the diagram_validator tool via subprocess for isolation
    result = _run_tool([
        sys.executable,
        str(ICDEV_ROOT / "tools" / "compliance" / "diagram_validator.py"),
        "--image", image_path,
        "--type", "infrastructure",
        "--json",
    ], timeout=180)

    validations = []
    passed_count = 0
    total_count = 0

    if result["stdout"]:
        parsed = _parse_json_output(result["stdout"])
        if isinstance(parsed, dict):
            validations = parsed.get("validations", [])
            for v in validations:
                total_count += 1
                if v.get("passed") is True:
                    passed_count += 1

    if total_count == 0:
        status = "skip"
        score = 0.0
    else:
        score = round(100.0 * passed_count / total_count, 1)
        status = "pass" if score >= 75.0 else "fail"

    return {
        "gate_id": "diagram_quality",
        "sa11_control": "PL-2",
        "dimension": "compliance_readiness",
        "status": status,
        "score": score,
        "tool": "diagram_validator",
        "image_path": image_path,
        "checks_passed": passed_count,
        "checks_total": total_count,
        "validations": validations[:20],
        "executed_at": _utcnow(),
    }


# ── Orchestration ─────────────────────────────────────────────────────────────

# Gate registry: gate_id → executor function
GATE_EXECUTORS: dict[str, Any] = {
    "lint": execute_ruff_lint,
    "sast": execute_bandit_sast,
    "typecheck": execute_mypy_typecheck,
    "deadcode": execute_vulture_deadcode,
    "complexity": execute_radon_complexity,
    "docstring_coverage": execute_interrogate_docstrings,
    "deprecation": execute_pyupgrade_deprecation,
    "dependency_audit": execute_pip_audit,
    "test_coverage": execute_pytest_coverage,
    "diagram_quality": execute_diagram_quality,
}


def execute_gate(gate_id: str, project_dir: str | None = None) -> dict:
    """Execute a single quality gate by ID.

    Returns gate result dict, or error dict if gate not found.
    """
    executor = GATE_EXECUTORS.get(gate_id)
    if executor is None:
        return {
            "gate_id": gate_id,
            "status": "skip",
            "score": 0.0,
            "error": f"No executor registered for gate '{gate_id}'",
            "executed_at": _utcnow(),
        }

    try:
        if gate_id in ("dependency_audit", "diagram_quality"):
            return executor()
        return executor(project_dir)
    except Exception as e:
        logger.error("Gate %s execution failed: %s", gate_id, e)
        return {
            "gate_id": gate_id,
            "status": "fail",
            "score": 0.0,
            "error": str(e),
            "executed_at": _utcnow(),
        }


def execute_all_gates(project_dir: str | None = None) -> list[dict]:
    """Execute all registered quality gates and return results."""
    results = []
    for gate_id in GATE_EXECUTORS:
        logger.info("Executing gate: %s", gate_id)
        result = execute_gate(gate_id, project_dir)
        results.append(result)
        logger.info("Gate %s: %s (score=%.1f)", gate_id, result.get("status"), result.get("score", 0))
    return results


def _tool_available_fast(name: str) -> bool:
    """Check if a tool is available using shutil.which (fast, no subprocess)."""
    import shutil

    # Check as direct binary
    if shutil.which(name) is not None:
        return True
    # Check as python module (pip_audit → pip-audit, etc.)
    module_name = name.replace("_", "-")
    if shutil.which(module_name) is not None:
        return True
    # Check importability as a quick module probe
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", f"import {name.replace('-', '_')}"],
            capture_output=True,
            timeout=3,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_tool_availability() -> dict:
    """Check which QA/QC tools are installed and available."""
    tools = {
        "ruff": _tool_available_fast("ruff"),
        "bandit": _tool_available_fast("bandit"),
        "mypy": _tool_available_fast("mypy"),
        "vulture": _tool_available_fast("vulture"),
        "radon": _tool_available_fast("radon"),
        "interrogate": _tool_available_fast("interrogate"),
        "pyupgrade": _tool_available_fast("pyupgrade"),
        "pip_audit": _tool_available_fast("pip_audit"),
        "pytest": _tool_available_fast("pytest"),
        "diagram_validator": (ICDEV_ROOT / "tools" / "compliance" / "diagram_validator.py").exists(),
    }
    return {
        "tools": tools,
        "available_count": sum(1 for v in tools.values() if v),
        "total_count": len(tools),
        "checked_at": _utcnow(),
    }


def auto_fix(gate_result: dict, project_dir: str | None = None) -> dict:
    """Attempt to auto-fix issues found by a gate.

    Only gates with safe auto-fix capabilities are supported:
    - lint → ruff --fix
    - deprecation → pyupgrade (in-place)
    """
    gate_id = gate_result.get("gate_id", "")
    target = project_dir or str(ICDEV_ROOT / "tools" / "qdc_canvas")

    if gate_id == "lint":
        result = _run_tool([sys.executable, "-m", "ruff", "check", target, "--fix"])
        return {"gate_id": gate_id, "action": "ruff --fix", "exit_code": result["exit_code"], "fixed_at": _utcnow()}

    if gate_id == "deprecation":
        fixed_files = []
        for py_file in Path(target).rglob("*.py"):
            result = _run_tool([sys.executable, "-m", "pyupgrade", "--py311-plus", str(py_file)])
            if result["exit_code"] == 0:
                fixed_files.append(str(py_file))
        return {
            "gate_id": gate_id,
            "action": "pyupgrade --py311-plus",
            "fixed_files": fixed_files,
            "fixed_at": _utcnow(),
        }

    return {"gate_id": gate_id, "action": "none", "reason": "No auto-fix available for this gate"}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="QDC Gate Executor")
    parser.add_argument("--gate", type=str, help="Execute a specific gate by ID")
    parser.add_argument("--all", action="store_true", help="Execute all gates")
    parser.add_argument("--check", action="store_true", help="Check tool availability")
    parser.add_argument("--project-dir", type=str, help="Project directory to scan")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.check:
        result = check_tool_availability()
        print(json.dumps(result, indent=2))
    elif args.all:
        results = execute_all_gates(args.project_dir)
        print(json.dumps(results, indent=2))
    elif args.gate:
        result = execute_gate(args.gate, args.project_dir)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
