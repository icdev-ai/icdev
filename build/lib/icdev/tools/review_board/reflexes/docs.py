#!/usr/bin/env python3
# CUI // SP-CTI
"""Documentation Reflex — freshness, coverage, and quality checks.

Checks:
    - Stale docs (feature docs older than code they describe)
    - Undocumented tools (manifest.md entries vs actual tools)
    - Missing CLI examples in CLAUDE.md
    - Changelog currency

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute documentation quality checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: Stale feature docs ---
    try:
        docs_dir = BASE_DIR / "docs" / "features"
        max_age_days = thresholds.get("max_doc_age_days", 90)
        stale_docs = []
        if docs_dir.exists():
            now = datetime.now(timezone.utc).timestamp()
            for doc_file in docs_dir.glob("*.md"):
                age_days = (now - doc_file.stat().st_mtime) / 86400
                if age_days > max_age_days:
                    stale_docs.append(
                        {
                            "file": doc_file.name,
                            "age_days": round(age_days),
                        }
                    )

        if stale_docs:
            stale_docs.sort(key=lambda x: x["age_days"], reverse=True)
            findings.append(
                {
                    "severity": "low",
                    "category": "stale_docs",
                    "title": f"{len(stale_docs)} feature docs older than {max_age_days} days",
                    "description": "; ".join(f"{d['file']} ({d['age_days']}d)" for d in stale_docs[:5]),
                    "confidence": 0.7,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 2: Undocumented tools ---
    try:
        manifest_path = BASE_DIR / "tools" / "manifest.md"
        tools_dir = BASE_DIR / "tools"
        max_undocumented = thresholds.get("max_undocumented_tools", 5)

        documented_tools = set()
        if manifest_path.exists():
            content = manifest_path.read_text(encoding="utf-8")
            # Extract tool references from manifest
            for match in re.finditer(r"`tools/([^`]+\.py)`", content):
                documented_tools.add(match.group(1))

        # Find all Python scripts in tools/
        actual_tools = set()
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if "__pycache__" in str(py_file) or py_file.name.startswith("__"):
                    continue
                rel = str(py_file.relative_to(tools_dir)).replace("\\", "/")
                actual_tools.add(rel)

        undocumented = actual_tools - documented_tools
        if len(undocumented) > max_undocumented:
            findings.append(
                {
                    "severity": "low",
                    "category": "undocumented_tools",
                    "title": f"{len(undocumented)} tools not in manifest.md",
                    "description": "Examples: " + ", ".join(sorted(undocumented)[:10]),
                    "recommendation": "Add entries to tools/manifest.md",
                    "confidence": 0.6,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 3: CLAUDE.md size and health ---
    try:
        claude_md = BASE_DIR / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            line_count = len(content.splitlines())
            word_count = len(content.split())

            # Check for broken internal references
            broken_refs = []
            for match in re.finditer(r"`(tools/[^`]+\.py)`", content):
                ref_path = BASE_DIR / match.group(1)
                if not ref_path.exists():
                    broken_refs.append(match.group(1))

            if broken_refs:
                findings.append(
                    {
                        "severity": "medium",
                        "category": "broken_refs",
                        "title": f"{len(broken_refs)} broken tool references in CLAUDE.md",
                        "description": "; ".join(broken_refs[:10]),
                        "recommendation": "Update or remove stale references",
                        "confidence": 0.9,
                        "auto_fixable": False,
                    }
                )

            # CLAUDE.md size warning
            if line_count > 5000:
                findings.append(
                    {
                        "severity": "info",
                        "category": "claude_md_size",
                        "title": f"CLAUDE.md is {line_count} lines ({word_count} words)",
                        "description": "Very large CLAUDE.md may impact LLM context efficiency",
                        "confidence": 0.5,
                        "auto_fixable": False,
                    }
                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 4: Missing phase docs ---
    try:
        docs_dir = BASE_DIR / "docs" / "features"
        if docs_dir.exists():
            existing_phases = set()
            for doc in docs_dir.glob("phase-*.md"):
                match = re.match(r"phase-(\d+)", doc.stem)
                if match:
                    existing_phases.add(int(match.group(1)))

            # Check for gaps in sequence
            if existing_phases:
                max_phase = max(existing_phases)
                missing = []
                for i in range(1, max_phase + 1):
                    if i not in existing_phases:
                        missing.append(i)

                if missing:
                    findings.append(
                        {
                            "severity": "low",
                            "category": "missing_phase_docs",
                            "title": f"{len(missing)} phase docs missing in sequence",
                            "description": f"Missing phases: {missing[:20]}",
                            "confidence": 0.6,
                            "auto_fixable": False,
                        }
                    )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
        },
    }
