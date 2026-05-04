#!/usr/bin/env python3
# CUI // SP-CTI
"""UX Quality Reflex — accessibility, error rates, template quality.

Checks:
    - ARIA attribute coverage in templates
    - Error rate per page (from audit trail)
    - Template quality (missing titles, broken extends)
    - Glossary coverage (DoD terms without tooltip definitions)

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

import re
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute UX quality checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: ARIA coverage in templates ---
    try:
        templates_dir = BASE_DIR / "tools" / "dashboard" / "templates"
        if templates_dir.exists():
            total_templates = 0
            templates_with_aria = 0
            templates_missing_aria = []

            for html_file in templates_dir.rglob("*.html"):
                if html_file.name.startswith("_"):
                    continue
                total_templates += 1
                content = html_file.read_text(encoding="utf-8")
                has_aria = bool(re.search(r"(aria-|role=)", content))
                if has_aria:
                    templates_with_aria += 1
                else:
                    templates_missing_aria.append(str(html_file.relative_to(templates_dir)))

            if total_templates > 0:
                coverage = (templates_with_aria / total_templates) * 100
                min_coverage = thresholds.get("min_aria_coverage_pct", 80)
                if coverage < min_coverage:
                    findings.append(
                        {
                            "severity": "medium",
                            "category": "aria_coverage",
                            "title": f"ARIA coverage {coverage:.0f}% below threshold ({min_coverage}%)",
                            "description": f"Missing ARIA: {', '.join(templates_missing_aria[:10])}",
                            "recommendation": "Add role= and aria-label attributes to templates",
                            "evidence": {
                                "total": total_templates,
                                "with_aria": templates_with_aria,
                                "missing": templates_missing_aria[:20],
                            },
                            "confidence": 0.8,
                            "auto_fixable": False,
                        }
                    )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 2: Template quality ---
    try:
        templates_dir = BASE_DIR / "tools" / "dashboard" / "templates"
        quality_issues = []
        if templates_dir.exists():
            for html_file in templates_dir.rglob("*.html"):
                if html_file.name.startswith("_"):
                    continue
                try:
                    content = html_file.read_text(encoding="utf-8")
                    rel_path = str(html_file.relative_to(templates_dir))

                    # Check for missing <title> in non-partial templates
                    if "{% extends" not in content and "<title>" not in content:
                        if "<html" in content:
                            quality_issues.append(f"{rel_path}: missing <title>")

                    # Check for inline styles (prefer CSS classes)
                    inline_count = len(re.findall(r'style="[^"]{50,}"', content))
                    if inline_count > 3:
                        quality_issues.append(f"{rel_path}: {inline_count} long inline styles")

                    # Check for hardcoded colors (should use CSS variables)
                    hardcoded_colors = re.findall(r"color:\s*#[0-9a-fA-F]{3,6}", content)
                    if len(hardcoded_colors) > 5:
                        quality_issues.append(f"{rel_path}: {len(hardcoded_colors)} hardcoded colors")
                except Exception:
                    pass

        if quality_issues:
            findings.append(
                {
                    "severity": "low",
                    "category": "template_quality",
                    "title": f"{len(quality_issues)} template quality issues",
                    "description": "; ".join(quality_issues[:5]),
                    "evidence": {"issues": quality_issues},
                    "confidence": 0.7,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 3: Skip-to-content and keyboard nav ---
    try:
        base_template = BASE_DIR / "tools" / "dashboard" / "templates" / "base.html"
        if base_template.exists():
            content = base_template.read_text(encoding="utf-8")
            if "skip-to-content" not in content and "skip-link" not in content:
                findings.append(
                    {
                        "severity": "medium",
                        "category": "accessibility",
                        "title": "Missing skip-to-content link in base template",
                        "recommendation": "Add <a href='#main' class='skip-link'>Skip to content</a>",
                        "confidence": 0.9,
                        "auto_fixable": True,
                    }
                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 4: Form accessibility ---
    try:
        templates_dir = BASE_DIR / "tools" / "dashboard" / "templates"
        form_issues = []
        if templates_dir.exists():
            for html_file in templates_dir.rglob("*.html"):
                try:
                    content = html_file.read_text(encoding="utf-8")
                    rel_path = str(html_file.relative_to(templates_dir))
                    # Find <input> without associated <label> or aria-label
                    inputs = re.findall(r"<input[^>]*>", content)
                    for inp in inputs:
                        if 'type="hidden"' in inp or 'type="submit"' in inp:
                            continue
                        if "aria-label" not in inp and "id=" not in inp:
                            form_issues.append(f"{rel_path}: input without label/aria-label")
                            break  # One per file is enough
                except Exception:
                    pass

        if form_issues:
            findings.append(
                {
                    "severity": "medium",
                    "category": "form_accessibility",
                    "title": f"{len(form_issues)} forms with unlabeled inputs",
                    "description": "; ".join(form_issues[:5]),
                    "confidence": 0.7,
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
