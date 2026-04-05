#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Pillar 1: Self-Introspection — analyze ICDEV™'s own codebase.

Delegates to the existing introspective_analyzer and adds Scout-specific
checks for test coverage, dead code, and config drift.

Usage:
    python tools/scout/pillars/introspect.py --scan --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finding(category: str, title: str, description: str,
             severity: str = "medium", action: str = "", meta: dict = None) -> dict:
    return {
        "id": f"scout-intr-{uuid.uuid4().hex[:12]}",
        "pillar": "introspect",
        "category": category,
        "title": title,
        "description": description,
        "severity": severity,
        "actionable": bool(action),
        "suggested_action": action,
        "source": "introspective_analyzer",
        "relevance_score": {"high": 0.9, "medium": 0.7, "low": 0.4}.get(severity, 0.5),
        "metadata": meta or {},
        "discovered_at": _now(),
    }


def _run_introspective_analyzer(config: dict) -> list:
    """Delegate to existing innovation engine introspective analyzer."""
    findings = []
    try:
        from tools.innovation.introspective_analyzer import analyze_all
        results = analyze_all()
        if isinstance(results, dict):
            for analysis_type, data in results.items():
                if isinstance(data, dict) and data.get("findings"):
                    for f in data["findings"]:
                        findings.append(_finding(
                            category=analysis_type,
                            title=f.get("title", analysis_type),
                            description=f.get("description", str(f)),
                            severity=f.get("severity", "medium"),
                            action=f.get("suggested_action", ""),
                            meta={"source_analysis": analysis_type, "raw": f},
                        ))
                elif isinstance(data, list):
                    for item in data[:10]:
                        findings.append(_finding(
                            category=analysis_type,
                            title=str(item.get("title", item.get("name", analysis_type))),
                            description=str(item.get("description", item.get("detail", str(item)))),
                            severity=item.get("severity", "low"),
                            action=item.get("suggested_action", ""),
                            meta={"source_analysis": analysis_type},
                        ))
    except Exception as exc:
        findings.append(_finding(
            category="analyzer_error",
            title="Introspective analyzer failed",
            description=str(exc),
            severity="low",
        ))
    return findings


def _check_test_coverage() -> list:
    """Find Python tool files without corresponding test files."""
    findings = []
    tools_dir = BASE_DIR / "tools"
    tests_dir = BASE_DIR / "tests"

    if not tools_dir.exists() or not tests_dir.exists():
        return findings

    test_files = {f.stem.replace("test_", "") for f in tests_dir.glob("test_*.py")}

    for py_file in tools_dir.rglob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "__init__.py":
            continue
        stem = py_file.stem
        rel = py_file.relative_to(BASE_DIR)
        if stem not in test_files:
            findings.append(_finding(
                category="test_coverage",
                title=f"No test file for {rel}",
                description=f"Tool '{rel}' has no corresponding test_*.py file in tests/",
                severity="low",
                action=f"Create tests/test_{stem}.py with basic validation tests",
                meta={"file": str(rel)},
            ))

    return findings


def _check_dead_code() -> list:
    """Find tools listed in manifest but not referenced by any goal."""
    findings = []
    manifest_path = BASE_DIR / "tools" / "manifest.md"
    goals_dir = BASE_DIR / "goals"

    if not manifest_path.exists() or not goals_dir.exists():
        return findings

    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
        goals_text = ""
        for gf in goals_dir.rglob("*.md"):
            goals_text += gf.read_text(encoding="utf-8") + "\n"

        # Extract tool names from manifest (lines with .py references)
        import re
        tool_refs = re.findall(r'`([a-z_]+\.py)`', manifest_text)
        for tool_name in set(tool_refs):
            if tool_name not in goals_text:
                findings.append(_finding(
                    category="dead_code",
                    title=f"Unreferenced tool: {tool_name}",
                    description=f"Tool '{tool_name}' is in manifest but not referenced by any goal",
                    severity="low",
                    action=f"Review if {tool_name} should be added to a goal or deprecated",
                    meta={"tool": tool_name},
                ))
    except Exception:
        pass

    return findings


def _check_config_drift() -> list:
    """Check for config files that may have drifted from expected structure."""
    findings = []
    args_dir = BASE_DIR / "args"

    if not args_dir.exists():
        return findings

    try:
        import yaml
        for cfg_file in args_dir.glob("*.yaml"):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data is None:
                    findings.append(_finding(
                        category="config_drift",
                        title=f"Empty config: {cfg_file.name}",
                        description=f"Config file '{cfg_file.name}' is empty or invalid YAML",
                        severity="medium",
                        action=f"Review {cfg_file.name} for missing configuration",
                        meta={"file": cfg_file.name},
                    ))
            except yaml.YAMLError as exc:
                findings.append(_finding(
                    category="config_drift",
                    title=f"Invalid YAML: {cfg_file.name}",
                    description=f"Config file '{cfg_file.name}' has YAML errors: {exc}",
                    severity="high",
                    action=f"Fix YAML syntax in {cfg_file.name}",
                    meta={"file": cfg_file.name, "error": str(exc)},
                ))
    except ImportError:
        pass

    return findings


def scan(config: dict = None) -> dict:
    """Run all introspection analyses. Returns structured results."""
    config = config or {}
    intr_cfg = config.get("introspect", {})
    result = {"pillar": "introspect", "started_at": _now(), "findings": []}

    # Delegate to existing analyzer
    result["findings"].extend(_run_introspective_analyzer(intr_cfg))

    # Scout-specific checks
    if intr_cfg.get("check_test_coverage", True):
        result["findings"].extend(_check_test_coverage())

    if intr_cfg.get("check_dead_code", True):
        result["findings"].extend(_check_dead_code())

    if intr_cfg.get("check_config_drift", True):
        result["findings"].extend(_check_config_drift())

    result["finding_count"] = len(result["findings"])
    result["completed_at"] = _now()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Pillar 1: Self-Introspection")
    parser.add_argument("--scan", action="store_true", help="Run introspection scan")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if not args.scan:
        parser.error("Specify --scan")

    try:
        import yaml
        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    result = scan(config)
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Introspection: {result['finding_count']} findings")
        for f in result["findings"][:10]:
            print(f"  [{f['severity']}] {f['title']}")


if __name__ == "__main__":
    main()
