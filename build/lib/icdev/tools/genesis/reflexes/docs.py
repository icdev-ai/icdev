#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Docs Reflex — documentation drift detection and repair.

Scans for documentation drift between code reality and docs:
  1. Stale feature docs — docs/features/*.md referencing removed/renamed tools
  2. Missing feature docs — phases with code but no corresponding doc
  3. Manifest drift — tools/manifest.md missing newly added tools
  4. CLAUDE.md drift — commands/routes documented but not implemented
  5. README staleness — version/feature count mismatches

GREEN tier (read-only analysis, no writes).
Scanner-tier only (zero Claude tokens).
"""
IMPLEMENTATION_STATUS = "full"

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Check 1: Feature docs referencing missing tools/files
# ---------------------------------------------------------------------------


def _check_stale_feature_docs() -> List[Dict[str, Any]]:
    """Scan docs/features/*.md for references to non-existent tool paths."""
    issues = []
    docs_dir = BASE_DIR / "docs" / "features"
    if not docs_dir.exists():
        return issues

    # Pattern: python tools/some/path.py or tools/some/path.py
    tool_ref_pattern = re.compile(r"(?:python\s+)?(tools/[\w/]+\.py)")

    for doc_file in docs_dir.glob("*.md"):
        try:
            content = doc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        refs = tool_ref_pattern.findall(content)
        for ref in refs:
            ref_path = BASE_DIR / ref
            if not ref_path.exists():
                issues.append(
                    {
                        "type": "stale_tool_reference",
                        "doc": str(doc_file.relative_to(BASE_DIR)),
                        "missing_tool": ref,
                        "severity": "medium",
                    }
                )

    return issues


# ---------------------------------------------------------------------------
# Check 2: Phases with code but no feature doc
# ---------------------------------------------------------------------------


def _check_missing_feature_docs() -> List[Dict[str, Any]]:
    """Detect tool directories that lack a corresponding feature doc."""
    issues = []
    docs_dir = BASE_DIR / "docs" / "features"
    existing_docs = set()
    if docs_dir.exists():
        for f in docs_dir.glob("*.md"):
            existing_docs.add(f.stem.lower())

    # Core tool directories that should have docs
    tools_dir = BASE_DIR / "tools"
    if not tools_dir.exists():
        return issues

    significant_dirs = []
    for d in tools_dir.iterdir():
        if not d.is_dir() or d.name.startswith("_") or d.name == "__pycache__":
            continue
        # Count Python files to determine significance
        py_count = sum(1 for _ in d.rglob("*.py") if _.name != "__init__.py")
        if py_count >= 3:
            significant_dirs.append(d.name)

    for dir_name in significant_dirs:
        # Check if any feature doc mentions this directory
        has_doc = any(dir_name in doc_name for doc_name in existing_docs)
        if not has_doc:
            issues.append(
                {
                    "type": "missing_feature_doc",
                    "tool_dir": f"tools/{dir_name}/",
                    "severity": "low",
                }
            )

    return issues


# ---------------------------------------------------------------------------
# Check 3: Manifest drift — tools in tools/ but not in manifest.md
# ---------------------------------------------------------------------------


def _check_manifest_drift() -> List[Dict[str, Any]]:
    """Check tools/manifest.md for missing tool entries."""
    issues = []
    manifest_path = BASE_DIR / "tools" / "manifest.md"
    if not manifest_path.exists():
        return [{"type": "manifest_missing", "severity": "high"}]

    try:
        manifest_content = manifest_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return issues

    # Manifest split into shards (2026-04-14) — include shard bodies in lookup.
    shard_dir = BASE_DIR / "tools" / "manifest"
    if shard_dir.is_dir():
        for shard in shard_dir.glob("*.md"):
            try:
                manifest_content += "\n" + shard.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                pass

    tools_dir = BASE_DIR / "tools"
    for py_file in tools_dir.rglob("*.py"):
        if py_file.name.startswith("_") or py_file.name in (
            "__init__.py",
            "conftest.py",
            "setup.py",
        ):
            continue
        # Skip very small files
        try:
            if len(py_file.read_text(encoding="utf-8", errors="replace").splitlines()) < 30:
                continue
        except OSError:
            continue

        rel = py_file.relative_to(tools_dir)
        tool_name = rel.stem
        # Check if tool name appears anywhere in manifest
        if tool_name not in manifest_content:
            issues.append(
                {
                    "type": "manifest_drift",
                    "tool": str(rel).replace("\\", "/"),
                    "severity": "low",
                }
            )

    return issues


# ---------------------------------------------------------------------------
# Check 4: CLAUDE.md route documentation drift
# ---------------------------------------------------------------------------


def _check_claude_md_route_drift() -> List[Dict[str, Any]]:
    """Check documented dashboard routes against actual Flask routes."""
    issues = []
    claude_md_path = BASE_DIR / "CLAUDE.md"
    app_path = BASE_DIR / "tools" / "dashboard" / "app.py"

    if not claude_md_path.exists() or not app_path.exists():
        return issues

    try:
        claude_content = claude_md_path.read_text(encoding="utf-8", errors="replace")
        app_content = app_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    # Extract routes documented in CLAUDE.md (pattern: /route_name — description)
    doc_route_pattern = re.compile(r"#\s+(/[\w\-<>/]+)\s+—")
    documented_routes = set()
    for m in doc_route_pattern.finditer(claude_content):
        route = m.group(1).strip()
        # Normalize: remove <param> parts
        route = re.sub(r"<[^>]+>", "", route).rstrip("/")
        if route:
            documented_routes.add(route)

    # Extract actual routes from app.py
    flask_route_pattern = re.compile(r'@\w+\.route\(["\']([^"\']+)')
    actual_routes = set()
    for m in flask_route_pattern.finditer(app_content):
        route = m.group(1).strip()
        route = re.sub(r"<[^>]+>", "", route).rstrip("/")
        if route:
            actual_routes.add(route)

    # Also check API blueprint files
    api_dir = BASE_DIR / "tools" / "dashboard" / "api"
    if api_dir.exists():
        for api_file in api_dir.glob("*.py"):
            try:
                api_content = api_file.read_text(encoding="utf-8", errors="replace")
                for m in flask_route_pattern.finditer(api_content):
                    route = m.group(1).strip()
                    route = re.sub(r"<[^>]+>", "", route).rstrip("/")
                    if route:
                        actual_routes.add(route)
            except OSError:
                pass

    # Routes in code but not in docs
    undocumented = actual_routes - documented_routes
    for route in sorted(undocumented):
        # Skip API endpoints and static routes
        if route.startswith("/api/") or route in ("/static", "/favicon.ico"):
            continue
        issues.append(
            {
                "type": "undocumented_route",
                "route": route,
                "severity": "low",
            }
        )

    return issues


# ---------------------------------------------------------------------------
# Check 5: README version/count mismatches
# ---------------------------------------------------------------------------


def _check_readme_staleness() -> List[Dict[str, Any]]:
    """Check README.md for potentially stale counts or versions."""
    issues = []
    readme_path = BASE_DIR / "README.md"
    if not readme_path.exists():
        return issues

    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return issues

    # Check pyproject.toml version vs README version
    pyproject_path = BASE_DIR / "pyproject.toml"
    if pyproject_path.exists():
        try:
            pyp_content = pyproject_path.read_text(encoding="utf-8", errors="replace")
            version_match = re.search(r'version\s*=\s*"([^"]+)"', pyp_content)
            if version_match:
                actual_version = version_match.group(1)
                if actual_version not in content:
                    issues.append(
                        {
                            "type": "readme_version_mismatch",
                            "actual_version": actual_version,
                            "severity": "medium",
                        }
                    )
        except OSError:
            pass

    return issues


# ---------------------------------------------------------------------------
# Export results as GKP
# ---------------------------------------------------------------------------


def _export_docs_report(issues: List[Dict], stats: Dict) -> Optional[str]:
    """Export documentation drift report as a GKP."""
    try:
        from tools.genesis.promoter import export_gkp

        result = export_gkp(
            reflex="docs",
            artifact_type="docs_drift_report",
            payload={
                "title": f"Docs Drift Report — {stats['total_issues']} issues found",
                "stats": stats,
                "issues": issues[:50],  # Cap at 50 issues
            },
            confidence=0.8,  # High confidence — deterministic analysis
            evidence={
                "checks_run": stats.get("checks_run", 0),
                "total_issues": stats.get("total_issues", 0),
                "by_severity": stats.get("by_severity", {}),
            },
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: GKP export failed: {e}")
    return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Docs Reflex.

    GREEN tier: read-only analysis, no file modifications.
    Detects documentation drift and reports via GKP.
    """
    all_issues = []
    checks_run = 0

    # Check 1: Stale feature doc references
    print("  Docs: checking for stale tool references in feature docs...")
    stale_refs = _check_stale_feature_docs()
    all_issues.extend(stale_refs)
    checks_run += 1

    # Check 2: Missing feature docs
    print("  Docs: checking for missing feature documentation...")
    missing_docs = _check_missing_feature_docs()
    all_issues.extend(missing_docs)
    checks_run += 1

    # Check 3: Manifest drift
    print("  Docs: checking tools/manifest.md for drift...")
    manifest_issues = _check_manifest_drift()
    all_issues.extend(manifest_issues)
    checks_run += 1

    # Check 4: Route documentation drift
    print("  Docs: checking CLAUDE.md route documentation...")
    route_issues = _check_claude_md_route_drift()
    all_issues.extend(route_issues)
    checks_run += 1

    # Check 5: README staleness
    print("  Docs: checking README.md for staleness...")
    readme_issues = _check_readme_staleness()
    all_issues.extend(readme_issues)
    checks_run += 1

    # Summarize by severity
    by_severity = {}
    for issue in all_issues:
        sev = issue.get("severity", "low")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    by_type = {}
    for issue in all_issues:
        t = issue.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    stats = {
        "checks_run": checks_run,
        "total_issues": len(all_issues),
        "by_severity": by_severity,
        "by_type": by_type,
    }

    print(f"  Docs: {len(all_issues)} issues found across {checks_run} checks")
    for sev, count in sorted(by_severity.items()):
        print(f"    {sev}: {count}")

    # Export report if issues found
    gkp_id = None
    if all_issues:
        gkp_id = _export_docs_report(all_issues, stats)

    return {
        "success": True,
        "metric_value": float(len(all_issues)),
        "details": {
            "stats": stats,
            "issues": all_issues[:20],  # Return top 20 in response
            "gkp_id": gkp_id,
        },
    }
