#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Spec quality checker -- 'unit tests for English'.

Validates spec markdown files against quality criteria: required sections,
ambiguity patterns, acceptance criteria testability, ATO coverage, task
completeness, and project constitution compliance.

Usage:
    python tools/requirements/spec_quality_checker.py --spec-file specs/feat.md --json
    python tools/requirements/spec_quality_checker.py --spec-dir specs/ --json
    python tools/requirements/spec_quality_checker.py --spec-file specs/feat.md --annotate --output annotated.md
    python tools/requirements/spec_quality_checker.py --spec-file specs/feat.md --strip-markers
    python tools/requirements/spec_quality_checker.py --spec-file specs/feat.md --count-markers
"""

import argparse
import dataclasses
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful audit import (air-gap safe)
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs):
        return -1


def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="sqc"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CheckResult:
    """Result of a single quality check."""
    check_id: str
    name: str
    status: str       # "pass", "fail", "warn"
    severity: str     # "critical", "high", "medium", "low"
    message: str
    suggestion: str = ""
    section: str = ""

    def to_dict(self):
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

def parse_spec_sections(spec_path: Path) -> dict:
    """Parse markdown by ``## Header`` into a dict.

    ``### Subheader`` content is nested within the parent ``## `` section.
    Returns ``{section_name_lower: content_string}``.
    """
    content = spec_path.read_text(encoding="utf-8")
    sections: dict = {}
    current_key = "_preamble"
    buffer = []

    for line in content.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2 and not h3:
            # Flush previous section
            sections[current_key] = "\n".join(buffer)
            current_key = h2.group(1).strip().lower()
            buffer = []
        else:
            buffer.append(line)

    # Flush final section
    sections[current_key] = "\n".join(buffer)
    return sections


# ---------------------------------------------------------------------------
# Loaders (context files with hardcoded fallbacks)
# ---------------------------------------------------------------------------

_DEFAULT_CHECKLIST = {
    "required_sections": [
        {"name": "Feature Description", "severity": "critical", "min_words": 20},
        {"name": "User Story", "severity": "critical",
         "pattern": r"(?i)as a .+ i want .+ so that .+"},
        {"name": "Solution Statement", "severity": "critical", "min_words": 30},
        {"name": "ATO Impact Assessment", "severity": "critical"},
        {"name": "Acceptance Criteria", "severity": "critical", "min_items": 3},
        {"name": "Implementation Plan", "severity": "high"},
        {"name": "Step by Step Tasks", "severity": "high", "min_items": 3},
        {"name": "Testing Strategy", "severity": "high"},
        {"name": "Validation Commands", "severity": "medium"},
        {"name": "NIST 800-53 Controls", "severity": "medium"},
    ]
}

_DEFAULT_AMBIGUITY_PATTERNS = [
    {"phrase": "as needed", "severity": "high",
     "clarification": "Define the specific conditions that trigger this action."},
    {"phrase": "appropriate", "severity": "high",
     "clarification": "Define measurable criteria for 'appropriate'."},
    {"phrase": "timely", "severity": "high",
     "clarification": "Specify an exact time threshold."},
    {"phrase": "user-friendly", "severity": "medium",
     "clarification": "Define specific usability criteria."},
    {"phrase": "fast", "severity": "high",
     "clarification": "Specify a measurable target (e.g., <2s response time)."},
    {"phrase": "secure", "severity": "critical",
     "clarification": "Specify security requirements: FIPS, CAC, STIG, NIST controls."},
    {"phrase": "scalable", "severity": "medium",
     "clarification": "Define target scale: concurrent users, data volume."},
    {"phrase": "efficient", "severity": "medium",
     "clarification": "Define efficiency metric: CPU, memory, cost."},
    {"phrase": "reasonable", "severity": "high",
     "clarification": "Define the quantitative threshold."},
    {"phrase": "adequate", "severity": "high",
     "clarification": "Define the minimum acceptable criteria."},
    {"phrase": "flexible", "severity": "medium",
     "clarification": "Define what specifically needs to be configurable."},
    {"phrase": "robust", "severity": "medium",
     "clarification": "Define failure scenarios and recovery time objectives."},
    {"phrase": "etc.", "severity": "high",
     "clarification": "Enumerate all items explicitly."},
    {"phrase": "and/or", "severity": "medium",
     "clarification": "Clarify inclusive OR vs exclusive OR."},
    {"phrase": "should", "severity": "medium",
     "clarification": "Is this MUST (mandatory) or SHOULD (recommended)? Use RFC 2119."},
]


def _load_checklist() -> dict:
    """Load spec quality checklist from context file, fallback to defaults."""
    path = BASE_DIR / "context" / "requirements" / "spec_quality_checklist.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return _DEFAULT_CHECKLIST


def _load_ambiguity_patterns() -> list:
    """Load ambiguity patterns from context file, fallback to defaults."""
    path = BASE_DIR / "context" / "requirements" / "ambiguity_patterns.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("ambiguity_patterns", _DEFAULT_AMBIGUITY_PATTERNS)
        except (json.JSONDecodeError, OSError):
            pass
    return _DEFAULT_AMBIGUITY_PATTERNS


def _load_constitutions(project_id: str = None, db_path=None) -> list:
    """Load constitution principles.

    If *project_id* is given and the DB is available, attempt to load
    project-specific constitutions.  Otherwise fall back to the default
    constitutions context file.
    """
    # Try DB first when project_id available
    if project_id:
        try:
            conn = _get_connection(db_path)
            rows = conn.execute(
                "SELECT principles FROM project_constitutions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            conn.close()
            if rows:
                return json.loads(rows["principles"])
        except Exception:
            pass

    # Fallback to context file
    path = BASE_DIR / "context" / "requirements" / "default_constitutions.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("default_principles", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _count_list_items(text: str) -> int:
    """Count markdown list items (``- `` or ``N. ``)."""
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            count += 1
    return count


def check_required_sections(sections: dict, checklist: dict) -> list:
    """Check that all required sections exist and meet criteria."""
    results = []
    for req in checklist.get("required_sections", []):
        name = req["name"]
        key = name.lower()
        severity = req.get("severity", "high")

        # Case-insensitive section lookup
        content = sections.get(key, "")
        if not content:
            # Try partial match
            for sec_key, sec_val in sections.items():
                if key in sec_key or sec_key in key:
                    content = sec_val
                    break

        if not content or not content.strip():
            results.append(CheckResult(
                check_id=_generate_id("sec"),
                name=f"Section: {name}",
                status="fail",
                severity=severity,
                message=f"Required section '{name}' is missing.",
                suggestion=f"Add a '## {name}' section to the spec.",
                section=name,
            ))
            continue

        # Min words check
        min_words = req.get("min_words")
        if min_words:
            word_count = len(content.split())
            if word_count < min_words:
                results.append(CheckResult(
                    check_id=_generate_id("sec"),
                    name=f"Section: {name} (word count)",
                    status="fail",
                    severity=severity,
                    message=f"Section '{name}' has {word_count} words, minimum is {min_words}.",
                    suggestion=f"Expand '{name}' to at least {min_words} words.",
                    section=name,
                ))
                continue

        # Pattern check
        pattern = req.get("pattern")
        if pattern:
            if not re.search(pattern, content):
                results.append(CheckResult(
                    check_id=_generate_id("sec"),
                    name=f"Section: {name} (pattern)",
                    status="fail",
                    severity=severity,
                    message=f"Section '{name}' does not match required pattern.",
                    suggestion=f"Ensure '{name}' follows the expected format.",
                    section=name,
                ))
                continue

        # Min items check
        min_items = req.get("min_items")
        if min_items:
            item_count = _count_list_items(content)
            if item_count < min_items:
                results.append(CheckResult(
                    check_id=_generate_id("sec"),
                    name=f"Section: {name} (items)",
                    status="fail",
                    severity=severity,
                    message=f"Section '{name}' has {item_count} list items, minimum is {min_items}.",
                    suggestion=f"Add at least {min_items} list items to '{name}'.",
                    section=name,
                ))
                continue

        # All checks passed for this section
        results.append(CheckResult(
            check_id=_generate_id("sec"),
            name=f"Section: {name}",
            status="pass",
            severity=severity,
            message=f"Section '{name}' present and meets criteria.",
            section=name,
        ))

    return results


def check_ambiguity(sections: dict, patterns: list) -> list:
    """Scan all sections for ambiguity patterns."""
    results = []
    all_content = "\n".join(sections.values()).lower()

    for pat in patterns:
        phrase = pat.get("phrase", "")
        if not phrase:
            continue
        # Use word boundary matching to avoid false positives inside words
        escaped = re.escape(phrase)
        regex = rf"\b{escaped}\b" if not phrase.endswith(".") else re.escape(phrase)
        matches = list(re.finditer(regex, all_content, re.IGNORECASE))
        if matches:
            # Find which section(s) contain the match
            match_sections = []
            for sec_name, sec_content in sections.items():
                if re.search(regex, sec_content, re.IGNORECASE):
                    match_sections.append(sec_name)

            results.append(CheckResult(
                check_id=_generate_id("amb"),
                name=f"Ambiguity: '{phrase}'",
                status="fail",
                severity=pat.get("severity", "medium"),
                message=(
                    f"Ambiguous phrase '{phrase}' found {len(matches)} time(s) "
                    f"in section(s): {', '.join(match_sections)}."
                ),
                suggestion=pat.get("clarification", "Replace with specific, measurable language."),
                section=", ".join(match_sections),
            ))

    return results


# Verbs that indicate testable/observable assertions in acceptance criteria
_TESTABLE_VERBS = re.compile(
    r"\b(shows?|returns?|displays?|links?\s+to|loads?\s+without|"
    r"renders?|navigates?\s+to|redirects?|creates?|updates?|deletes?|"
    r"validates?|rejects?|accepts?|sends?|receives?|stores?|"
    r"contains?|includes?|excludes?|matches?|equals?|is\s+visible|"
    r"appears?|disappears?|enables?|disables?|triggers?|"
    r"given|when|then|must|shall)\b",
    re.IGNORECASE,
)


def check_acceptance_criteria(sections: dict) -> list:
    """Validate acceptance criteria for testability."""
    results = []

    # Find acceptance criteria section
    ac_content = ""
    for key, content in sections.items():
        if "acceptance" in key and "criteria" in key:
            ac_content = content
            break
        if "acceptance criteria" in key:
            ac_content = content
            break

    if not ac_content.strip():
        results.append(CheckResult(
            check_id=_generate_id("acc"),
            name="Acceptance Criteria: presence",
            status="fail",
            severity="critical",
            message="No acceptance criteria section found.",
            suggestion="Add '## Acceptance Criteria' with at least 3 testable items.",
            section="acceptance criteria",
        ))
        return results

    # Count items
    items = []
    for line in ac_content.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            items.append(stripped)

    if len(items) < 3:
        results.append(CheckResult(
            check_id=_generate_id("acc"),
            name="Acceptance Criteria: count",
            status="fail",
            severity="critical",
            message=f"Only {len(items)} acceptance criteria found, minimum is 3.",
            suggestion="Add at least 3 specific, testable acceptance criteria.",
            section="acceptance criteria",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("acc"),
            name="Acceptance Criteria: count",
            status="pass",
            severity="critical",
            message=f"{len(items)} acceptance criteria found.",
            section="acceptance criteria",
        ))

    # Check each item for testable assertion
    untestable = []
    for item in items:
        if not _TESTABLE_VERBS.search(item):
            untestable.append(item[:80])

    if untestable:
        results.append(CheckResult(
            check_id=_generate_id("acc"),
            name="Acceptance Criteria: testability",
            status="fail",
            severity="high",
            message=(
                f"{len(untestable)} of {len(items)} criteria lack testable verbs: "
                f"{untestable[0]}..."
            ),
            suggestion=(
                "Each criterion should contain a measurable verb "
                "(shows, returns, displays, loads without, etc.)."
            ),
            section="acceptance criteria",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("acc"),
            name="Acceptance Criteria: testability",
            status="pass",
            severity="high",
            message="All acceptance criteria contain testable assertions.",
            section="acceptance criteria",
        ))

    # Check for Given/When/Then format
    has_gwt = bool(re.search(r"\b(given|when|then)\b", ac_content, re.IGNORECASE))
    results.append(CheckResult(
        check_id=_generate_id("acc"),
        name="Acceptance Criteria: BDD format",
        status="pass" if has_gwt else "warn",
        severity="low",
        message=(
            "BDD Given/When/Then format detected."
            if has_gwt else
            "No Given/When/Then BDD format found. Consider using BDD for clearer test mapping."
        ),
        suggestion="" if has_gwt else "Rewrite criteria in Given/When/Then format for BDD.",
        section="acceptance criteria",
    ))

    return results


def check_ato_coverage(sections: dict) -> list:
    """Check ATO impact assessment completeness."""
    results = []

    # Find ATO section
    ato_content = ""
    for key, content in sections.items():
        if "ato" in key and "impact" in key:
            ato_content = content
            break
        if "ato impact" in key:
            ato_content = content
            break

    if not ato_content.strip():
        results.append(CheckResult(
            check_id=_generate_id("ato"),
            name="ATO: section presence",
            status="fail",
            severity="critical",
            message="ATO Impact Assessment section is missing.",
            suggestion="Add '## ATO Impact Assessment' with boundary tier, NIST controls, SSP impact.",
            section="ato impact assessment",
        ))
        return results

    # Boundary impact tier
    tier_pattern = re.compile(r"\b(GREEN|YELLOW|ORANGE|RED)\b")
    tier_match = tier_pattern.search(ato_content)
    if tier_match:
        results.append(CheckResult(
            check_id=_generate_id("ato"),
            name="ATO: boundary tier",
            status="pass",
            severity="critical",
            message=f"Boundary impact tier specified: {tier_match.group(1)}.",
            section="ato impact assessment",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("ato"),
            name="ATO: boundary tier",
            status="fail",
            severity="critical",
            message="No boundary impact tier (GREEN/YELLOW/ORANGE/RED) found.",
            suggestion="Specify one of: GREEN (no impact), YELLOW (minor), ORANGE (significant), RED (ATO-invalidating).",
            section="ato impact assessment",
        ))

    # NIST controls
    nist_pattern = re.compile(r"\b[A-Z]{2}-\d+(?:\(\d+\))?\b")
    nist_matches = nist_pattern.findall(ato_content)
    if nist_matches:
        results.append(CheckResult(
            check_id=_generate_id("ato"),
            name="ATO: NIST controls",
            status="pass",
            severity="high",
            message=f"NIST controls referenced: {', '.join(nist_matches[:5])}.",
            section="ato impact assessment",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("ato"),
            name="ATO: NIST controls",
            status="fail",
            severity="high",
            message="No NIST 800-53 control IDs found in ATO section.",
            suggestion="Reference applicable controls (e.g., AC-2, AU-2, IA-2, SC-8).",
            section="ato impact assessment",
        ))

    # SSP impact
    ssp_mentioned = bool(re.search(r"\bSSP\b", ato_content, re.IGNORECASE))
    results.append(CheckResult(
        check_id=_generate_id("ato"),
        name="ATO: SSP impact",
        status="pass" if ssp_mentioned else "warn",
        severity="medium",
        message=(
            "SSP impact noted." if ssp_mentioned
            else "No mention of SSP impact. Consider documenting whether SSP requires update."
        ),
        suggestion="" if ssp_mentioned else "Add SSP impact statement (e.g., 'SSP addendum required').",
        section="ato impact assessment",
    ))

    return results


def check_testability(sections: dict) -> list:
    """Check that testing strategy and validation commands exist with content."""
    results = []

    # Testing strategy
    ts_content = ""
    for key, content in sections.items():
        if "testing" in key and "strategy" in key:
            ts_content = content
            break
        if "testing strategy" in key:
            ts_content = content
            break

    if ts_content.strip():
        results.append(CheckResult(
            check_id=_generate_id("tst"),
            name="Testability: testing strategy",
            status="pass",
            severity="high",
            message="Testing strategy section present with content.",
            section="testing strategy",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("tst"),
            name="Testability: testing strategy",
            status="fail",
            severity="high",
            message="Testing strategy section is missing or empty.",
            suggestion="Add '## Testing Strategy' describing unit, BDD, edge case, and E2E approaches.",
            section="testing strategy",
        ))

    # Validation commands
    vc_content = ""
    for key, content in sections.items():
        if "validation" in key and "command" in key:
            vc_content = content
            break
        if "validation commands" in key:
            vc_content = content
            break

    if vc_content.strip():
        # Check for actual command-like content (backticks or bash patterns)
        has_commands = bool(
            re.search(r"(```|python |pytest |behave |curl |bash |npm |go |cargo )", vc_content)
        )
        results.append(CheckResult(
            check_id=_generate_id("tst"),
            name="Testability: validation commands",
            status="pass" if has_commands else "warn",
            severity="medium",
            message=(
                "Validation commands section has executable commands."
                if has_commands else
                "Validation commands section exists but may lack executable commands."
            ),
            suggestion="" if has_commands else "Include runnable bash/python commands to verify implementation.",
            section="validation commands",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("tst"),
            name="Testability: validation commands",
            status="fail",
            severity="medium",
            message="Validation commands section is missing or empty.",
            suggestion="Add '## Validation Commands' with bash commands to verify the implementation.",
            section="validation commands",
        ))

    return results


def check_task_completeness(sections: dict) -> list:
    """Verify implementation plan phases are covered in step-by-step tasks."""
    results = []

    # Find implementation plan
    plan_content = ""
    for key, content in sections.items():
        if "implementation" in key and "plan" in key:
            plan_content = content
            break
        if "implementation plan" in key:
            plan_content = content
            break

    if not plan_content.strip():
        return results  # Cannot check without plan

    # Find tasks section
    tasks_content = ""
    for key, content in sections.items():
        if "step" in key and "task" in key:
            tasks_content = content
            break
        if "step by step tasks" in key:
            tasks_content = content
            break

    if not tasks_content.strip():
        return results  # Already caught by required section check

    # Extract phase names/numbers from plan
    phase_pattern = re.compile(r"###?\s*Phase\s+(\d+)[:\s]*(.+)", re.IGNORECASE)
    phases = phase_pattern.findall(plan_content)

    if not phases:
        # Try simpler pattern: numbered list with "Phase" or keywords
        numbered = re.compile(r"^\s*\d+\.\s*(.+)", re.MULTILINE)
        phase_items = numbered.findall(plan_content)
        for idx, item in enumerate(phase_items, 1):
            phases.append((str(idx), item.strip()))

    if not phases:
        results.append(CheckResult(
            check_id=_generate_id("task"),
            name="Task Completeness: phase extraction",
            status="warn",
            severity="medium",
            message="Could not extract phases from implementation plan.",
            suggestion="Use '### Phase N: Name' format in the Implementation Plan.",
            section="implementation plan",
        ))
        return results

    tasks_lower = tasks_content.lower()
    uncovered = []
    for num, name in phases:
        # Check if phase number or key words appear in tasks
        name_words = [w for w in name.lower().split() if len(w) > 3]
        phase_ref = f"phase {num}"
        found = phase_ref in tasks_lower
        if not found and name_words:
            found = any(w in tasks_lower for w in name_words[:3])
        if not found:
            uncovered.append(f"Phase {num}: {name.strip()}")

    if uncovered:
        results.append(CheckResult(
            check_id=_generate_id("task"),
            name="Task Completeness: phase coverage",
            status="fail",
            severity="high",
            message=f"{len(uncovered)} phase(s) have no corresponding tasks: {'; '.join(uncovered[:3])}.",
            suggestion="Ensure each implementation phase has detailed tasks in 'Step by Step Tasks'.",
            section="step by step tasks",
        ))
    else:
        results.append(CheckResult(
            check_id=_generate_id("task"),
            name="Task Completeness: phase coverage",
            status="pass",
            severity="high",
            message=f"All {len(phases)} implementation phases are covered in tasks.",
            section="step by step tasks",
        ))

    return results


def check_constitution_compliance(sections: dict, principles: list) -> list:
    """Check spec against project constitution principles."""
    results = []
    if not principles:
        return results

    all_content = "\n".join(sections.values()).lower()

    for principle in principles:
        p_text = principle.get("text", "")
        keywords = principle.get("keywords", [])
        priority = principle.get("priority", 3)
        category = principle.get("category", "general")

        if not keywords:
            continue

        # Check if any keyword appears in the spec
        found_keywords = [kw for kw in keywords if kw.lower() in all_content]

        if found_keywords:
            results.append(CheckResult(
                check_id=_generate_id("con"),
                name=f"Constitution: {category}",
                status="pass",
                severity="critical" if priority == 1 else "high" if priority == 2 else "medium",
                message=f"Principle addressed: '{p_text[:60]}...' (keywords: {', '.join(found_keywords[:3])}).",
                section="constitution",
            ))
        else:
            severity = "critical" if priority == 1 else "high" if priority == 2 else "medium"
            status = "fail" if priority == 1 else "warn"
            results.append(CheckResult(
                check_id=_generate_id("con"),
                name=f"Constitution: {category}",
                status=status,
                severity=severity,
                message=f"Principle not addressed: '{p_text[:80]}'.",
                suggestion=f"Ensure the spec addresses: {', '.join(keywords[:4])}.",
                section="constitution",
            ))

    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_checks(spec_path: Path, project_id: str = None, db_path=None) -> dict:
    """Run all quality checks on a spec file.

    Returns a summary dict with quality score, check results, and suggestions.
    """
    spec_path = Path(spec_path)
    if not spec_path.exists():
        return {"status": "error", "error": f"Spec file not found: {spec_path}"}

    sections = parse_spec_sections(spec_path)
    checklist = _load_checklist()
    patterns = _load_ambiguity_patterns()
    principles = _load_constitutions(project_id, db_path)

    all_checks = []
    all_checks.extend(check_required_sections(sections, checklist))
    all_checks.extend(check_ambiguity(sections, patterns))
    all_checks.extend(check_acceptance_criteria(sections))
    all_checks.extend(check_ato_coverage(sections))
    all_checks.extend(check_testability(sections))
    all_checks.extend(check_task_completeness(sections))
    all_checks.extend(check_constitution_compliance(sections, principles))

    passed = sum(1 for c in all_checks if c.status == "pass")
    failed = sum(1 for c in all_checks if c.status == "fail")
    warnings = sum(1 for c in all_checks if c.status == "warn")
    total = len(all_checks)

    # Quality score: pass_count / total * 100, cap at 50 if any critical failure
    quality_score = (passed / max(total, 1)) * 100.0
    critical_failures = [
        c.to_dict() for c in all_checks
        if c.status == "fail" and c.severity == "critical"
    ]
    if critical_failures:
        quality_score = min(quality_score, 50.0)

    quality_score = round(quality_score, 1)

    suggestions = [
        c.suggestion for c in all_checks
        if c.suggestion and c.status in ("fail", "warn")
    ]

    if _HAS_AUDIT:
        log_event(
            event_type="spec_quality_check",
            actor="icdev-requirements-analyst",
            action=f"Quality check on {spec_path.name}: {quality_score}%",
            project_id=project_id or "",
            details={
                "spec_file": str(spec_path),
                "quality_score": quality_score,
                "passed": passed,
                "failed": failed,
            },
        )

    return {
        "status": "ok",
        "spec_file": str(spec_path),
        "quality_score": quality_score,
        "total_checks": total,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "checks": [c.to_dict() for c in all_checks],
        "critical_failures": critical_failures,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Annotation helpers (inline markers)
# ---------------------------------------------------------------------------

_MARKER_PATTERN = re.compile(r"\[NEEDS CLARIFICATION:\s*[^\]]+\]")


def annotate_spec(spec_path: Path, check_results: list, max_markers: int = 3) -> str:
    """Insert ``[NEEDS CLARIFICATION: ...]`` markers inline for critical/high failures.

    Only inserts up to *max_markers* markers.  Returns the annotated content string.
    """
    content = Path(spec_path).read_text(encoding="utf-8")

    # Filter to critical/high failures only
    failures = [
        c for c in check_results
        if c.get("status") == "fail" and c.get("severity") in ("critical", "high")
    ]

    inserted = 0
    for fail in failures:
        if inserted >= max_markers:
            break

        section = fail.get("section", "").lower()
        message = fail.get("message", "")
        check_id = fail.get("check_id", "unknown")
        marker = f"[NEEDS CLARIFICATION: {check_id} -- {message[:80]}]"

        # Try to insert after the section heading
        if section:
            # Look for ## Section heading (case-insensitive)
            heading_re = re.compile(
                rf"^(##\s+.*{re.escape(section.split(',')[0].strip())}.*$)",
                re.IGNORECASE | re.MULTILINE,
            )
            match = heading_re.search(content)
            if match:
                insert_pos = match.end()
                content = content[:insert_pos] + f"\n{marker}" + content[insert_pos:]
                inserted += 1
                continue

        # Fallback: insert at end of file
        content = content.rstrip() + f"\n\n{marker}\n"
        inserted += 1

    return content


def strip_markers(spec_path: Path) -> str:
    """Remove all ``[NEEDS CLARIFICATION: ...]`` markers from spec content."""
    content = Path(spec_path).read_text(encoding="utf-8")
    cleaned = _MARKER_PATTERN.sub("", content)
    # Clean up any leftover blank lines from removed markers
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def count_markers(spec_path: Path) -> int:
    """Count existing ``[NEEDS CLARIFICATION: ...]`` markers."""
    content = Path(spec_path).read_text(encoding="utf-8")
    return len(_MARKER_PATTERN.findall(content))


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _format_human(result: dict) -> str:
    """Format check results for terminal display."""
    lines = []
    score = result.get("quality_score", 0)
    spec = result.get("spec_file", "unknown")

    # Score color indicator
    if score >= 80:
        indicator = "[PASS]"
    elif score >= 50:
        indicator = "[WARN]"
    else:
        indicator = "[FAIL]"

    lines.append(f"{'=' * 60}")
    lines.append(f"Spec Quality Report: {spec}")
    lines.append(f"{'=' * 60}")
    lines.append(f"  Score: {score:.1f}% {indicator}")
    lines.append(f"  Passed: {result.get('passed', 0)} | Failed: {result.get('failed', 0)} | Warnings: {result.get('warnings', 0)}")
    lines.append("")

    # Group by status
    for check in result.get("checks", []):
        status = check.get("status", "?").upper()
        sev = check.get("severity", "?")
        name = check.get("name", "")
        msg = check.get("message", "")
        tag = f"[{status}:{sev}]"
        lines.append(f"  {tag:20s} {name}")
        if status in ("FAIL", "WARN"):
            lines.append(f"  {'':20s}   {msg}")
            if check.get("suggestion"):
                lines.append(f"  {'':20s}   -> {check['suggestion']}")

    if result.get("critical_failures"):
        lines.append("")
        lines.append(f"CRITICAL FAILURES ({len(result['critical_failures'])}):")
        for cf in result["critical_failures"]:
            lines.append(f"  * {cf.get('name', '')}: {cf.get('message', '')}")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Spec Quality Checker -- 'unit tests for English'"
    )
    parser.add_argument("--spec-file", type=str, help="Check a single spec markdown file")
    parser.add_argument("--spec-dir", type=str, help="Check all .md files in directory (recursive)")
    parser.add_argument("--annotate", action="store_true", help="Output annotated spec with inline markers")
    parser.add_argument("--output", type=str, help="Write annotated output to file instead of stdout")
    parser.add_argument("--strip-markers", action="store_true", help="Remove markers from spec")
    parser.add_argument("--count-markers", action="store_true", help="Count markers in spec")
    parser.add_argument("--project-id", type=str, help="Project ID for constitution validation")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    args = parser.parse_args()

    try:
        # --- Strip markers mode ---
        if args.strip_markers:
            if not args.spec_file:
                raise ValueError("--strip-markers requires --spec-file")
            cleaned = strip_markers(Path(args.spec_file))
            if args.output:
                Path(args.output).write_text(cleaned, encoding="utf-8")
                result = {"status": "ok", "message": f"Markers stripped, written to {args.output}"}
            else:
                result = {"status": "ok", "content": cleaned}
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(cleaned if not args.output else result["message"])
            return

        # --- Count markers mode ---
        if args.count_markers:
            if not args.spec_file:
                raise ValueError("--count-markers requires --spec-file")
            count = count_markers(Path(args.spec_file))
            result = {"status": "ok", "spec_file": args.spec_file, "marker_count": count}
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Markers found: {count}")
            return

        # --- Single file mode ---
        if args.spec_file:
            result = run_all_checks(
                Path(args.spec_file),
                project_id=args.project_id,
            )

            # Annotate mode
            if args.annotate and result.get("status") == "ok":
                annotated = annotate_spec(Path(args.spec_file), result.get("checks", []))
                if args.output:
                    Path(args.output).write_text(annotated, encoding="utf-8")
                    result["annotated_output"] = args.output
                    result["message"] = f"Annotated spec written to {args.output}"
                else:
                    if args.json:
                        result["annotated_content"] = annotated
                    else:
                        print(annotated)
                        return

            if args.json:
                print(json.dumps(result, indent=2, default=str))
            elif args.human:
                print(_format_human(result))
            else:
                print(json.dumps(result, indent=2, default=str))
            return

        # --- Batch mode ---
        if args.spec_dir:
            spec_dir = Path(args.spec_dir)
            if not spec_dir.is_dir():
                raise ValueError(f"Not a directory: {spec_dir}")

            all_results = []
            for md_file in sorted(spec_dir.rglob("*.md")):
                r = run_all_checks(md_file, project_id=args.project_id)
                all_results.append(r)

            batch_result = {
                "status": "ok",
                "spec_dir": str(spec_dir),
                "total_specs": len(all_results),
                "average_score": round(
                    sum(r.get("quality_score", 0) for r in all_results) / max(len(all_results), 1),
                    1,
                ),
                "specs_passing": sum(1 for r in all_results if r.get("quality_score", 0) >= 70),
                "specs_failing": sum(1 for r in all_results if r.get("quality_score", 0) < 70),
                "results": all_results,
            }

            if args.json:
                print(json.dumps(batch_result, indent=2, default=str))
            elif args.human:
                print(f"Batch Quality Report: {spec_dir}")
                print(f"  Specs: {batch_result['total_specs']} | "
                      f"Avg Score: {batch_result['average_score']}% | "
                      f"Passing: {batch_result['specs_passing']} | "
                      f"Failing: {batch_result['specs_failing']}")
                print()
                for r in all_results:
                    print(_format_human(r))
                    print()
            else:
                print(json.dumps(batch_result, indent=2, default=str))
            return

        # No action specified
        parser.print_help()

    except (ValueError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
