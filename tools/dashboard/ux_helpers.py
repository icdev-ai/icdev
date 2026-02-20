# CUI // SP-CTI
# ICDEV Dashboard UX Helpers
# Server-side utilities for user-friendly display
"""
UX Helpers for the ICDEV Dashboard
===================================
Provides server-side utilities that make the ICDEV dashboard intuitive for
non-technical government users: human-friendly timestamps, glossary tooltips,
score badges, error recovery guidance, quick-path workflows, and a getting-
started wizard.

All functions use Python stdlib only (datetime, re, html). No external
dependencies required.

Usage:
    from tools.dashboard.ux_helpers import register_ux_filters
    app = Flask(__name__)
    register_ux_filters(app)

Then in Jinja2 templates:
    {{ timestamp | friendly_time }}
    {{ timestamp | short_time }}
    {{ timestamp | time_ago }}
    {{ "ATO" | glossary }}
    {{ score_display(0.78, 0.7, "Readiness") }}
"""

import html
import re
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Glossary of acronyms (used by glossary_term filter)
# ---------------------------------------------------------------------------

GLOSSARY = {
    "ATO": "Authorization to Operate — formal approval to run a system",
    "cATO": "Continuous Authorization to Operate — ongoing compliance monitoring",
    "CAC": "Common Access Card — DoD smart card for authentication",
    "CAT-I": "Category I — critical severity (STIG finding that must be fixed immediately)",
    "CAT-II": "Category II — high severity (STIG finding that should be fixed soon)",
    "CAT-III": "Category III — medium severity (STIG finding that should be tracked)",
    "CMMC": "Cybersecurity Maturity Model Certification — DoD contractor security standard",
    "COA": "Course of Action — a proposed plan option with cost/schedule/risk tradeoffs",
    "CUI": "Controlled Unclassified Information — sensitive but not classified data",
    "CVE": "Common Vulnerabilities and Exposures — a known security vulnerability",
    "DES": "Digital Engineering Strategy — DoDI 5000.87 requirements for model-based engineering",
    "eMASS": "Enterprise Mission Assurance Support Service — DoD compliance tracking system",
    "FedRAMP": "Federal Risk and Authorization Management Program — cloud security standard",
    "FIPS": "Federal Information Processing Standards — NIST cryptographic and categorization standards",
    "FIPS 199": "Standards for Security Categorization — determines system impact level",
    "FIPS 200": "Minimum Security Requirements — 17 security areas every federal system must address",
    "IaC": "Infrastructure as Code — automated infrastructure provisioning (Terraform, Ansible)",
    "IL2": "Impact Level 2 — public, non-sensitive data",
    "IL4": "Impact Level 4 — CUI in AWS GovCloud",
    "IL5": "Impact Level 5 — CUI on dedicated GovCloud infrastructure",
    "IL6": "Impact Level 6 — SECRET / classified data on SIPR",
    "ISA": "Interconnection Security Agreement — contract for data exchange between systems",
    "ISSO": "Information System Security Officer — person responsible for system security",
    "IV&V": "Independent Verification and Validation — IEEE 1012 testing standard",
    "MBSE": "Model-Based Systems Engineering — using SysML models as source of truth",
    "NIST": "National Institute of Standards and Technology — publishes security frameworks",
    "OSCAL": "Open Security Controls Assessment Language — machine-readable compliance format",
    "PIV": "Personal Identity Verification — federal employee smart card",
    "POA&M": "Plan of Action and Milestones — documented plan to fix security gaps",
    "POAM": "Plan of Action and Milestones — documented plan to fix security gaps",
    "RICOAS": "Requirements Intake, COA & Approval System",
    "RMF": "Risk Management Framework — NIST process for managing security risk",
    "SAFe": "Scaled Agile Framework — enterprise agile methodology",
    "SAST": "Static Application Security Testing — automated code vulnerability scanning",
    "SBOM": "Software Bill of Materials — inventory of all software components",
    "SCRM": "Supply Chain Risk Management — assessing vendor and dependency risks",
    "SIPR": "Secret Internet Protocol Router Network — classified network",
    "SLA": "Service Level Agreement — promised response/resolution times",
    "SSP": "System Security Plan — primary ATO document describing security controls",
    "STIG": "Security Technical Implementation Guide — DoD security configuration checklist",
    "TDD": "Test-Driven Development — write tests first, then code to pass them",
    "WSJF": "Weighted Shortest Job First — SAFe prioritization method",
}


# ---------------------------------------------------------------------------
# 1. Jinja2 Template Filters
# ---------------------------------------------------------------------------

def _format_time_12h(dt):
    """Format a datetime to '2:30 PM' style using only stdlib."""
    hour = dt.hour % 12 or 12
    minute = dt.strftime("%M")
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{minute} {ampm}"


def format_timestamp(value):
    """Convert ISO-8601 string to human-friendly format.

    Examples:
        "2026-02-18T14:30:00Z"  -> "Feb 18, 2026 at 2:30 PM"
        "2026-02-18T14:30:00"   -> "Feb 18, 2026 at 2:30 PM"
        None or ""              -> "\u2014"
        invalid                 -> original string
    """
    if not value:
        return "\u2014"
    try:
        dt = _parse_iso(value)
        date_part = dt.strftime("%b %d, %Y")
        time_part = _format_time_12h(dt)
        return f"{date_part} at {time_part}"
    except Exception:
        return str(value)


def format_timestamp_short(value):
    """Convert ISO-8601 string to short date format.

    Examples:
        "2026-02-18T14:30:00Z"  -> "Feb 18, 2026"
        None or ""              -> "\u2014"
    """
    if not value:
        return "\u2014"
    try:
        dt = _parse_iso(value)
        return dt.strftime("%b %d, %Y")
    except Exception:
        return str(value)


def format_time_ago(value):
    """Convert ISO-8601 string to relative time description.

    Examples:
        (now - 30 seconds)  -> "just now"
        (now - 5 minutes)   -> "5 minutes ago"
        (now - 3 hours)     -> "3 hours ago"
        (now - 2 days)      -> "2 days ago"
        (now - 2 weeks)     -> "2 weeks ago"
        (now - 60 days)     -> "Feb 18, 2026"
    """
    if not value:
        return "\u2014"
    try:
        dt = _parse_iso(value)
        now = datetime.now(timezone.utc)
        # Ensure dt is timezone-aware for comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())

        if seconds < 0:
            # Future timestamp — just show the date
            return format_timestamp_short(value)
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            unit = "minute" if minutes == 1 else "minutes"
            return f"{minutes} {unit} ago"
        hours = minutes // 60
        if hours < 24:
            unit = "hour" if hours == 1 else "hours"
            return f"{hours} {unit} ago"
        days = hours // 24
        if days < 7:
            unit = "day" if days == 1 else "days"
            return f"{days} {unit} ago"
        weeks = days // 7
        if days < 30:
            unit = "week" if weeks == 1 else "weeks"
            return f"{weeks} {unit} ago"
        # Older than 30 days — show the short date
        return format_timestamp_short(value)
    except Exception:
        return str(value)


def glossary_term(text):
    """Wrap a known acronym in a glossary tooltip span.

    If the text matches a known term in the GLOSSARY dict, returns an HTML
    span with a data-glossary attribute for JavaScript tooltip activation.
    Unknown terms are returned unchanged (HTML-escaped for safety).

    Examples:
        glossary_term("ATO")  -> '<span data-glossary="ATO">ATO</span>'
        glossary_term("foo")  -> 'foo'
    """
    if not text:
        return ""
    text_str = str(text).strip()
    if text_str in GLOSSARY:
        safe_text = html.escape(text_str)
        return f'<span data-glossary="{safe_text}">{safe_text}</span>'
    return html.escape(text_str)


def score_display(value, threshold, label="Score"):
    """Return HTML for a color-coded score badge.

    The badge color depends on how the value compares to the threshold:
      - value >= threshold          -> green  (good)
      - value >= threshold * 0.85   -> yellow (warning / almost ready)
      - value < threshold * 0.85    -> red    (poor / needs work)

    Args:
        value:     Numeric score between 0.0 and 1.0.
        threshold: The passing threshold (e.g. 0.7).
        label:     Human-readable label (e.g. "Readiness").

    Returns:
        HTML string with appropriate CSS class and icon.

    Examples:
        score_display(0.78, 0.7, "Readiness")
          -> '<span class="score-display score-good">...'
        score_display(0.62, 0.7, "Readiness")
          -> '<span class="score-display score-warning">...'
        score_display(0.45, 0.7, "Readiness")
          -> '<span class="score-display score-poor">...'
    """
    try:
        val = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    pct = int(round(val * 100))

    if val >= threshold:
        css = "score-good"
        icon = "\u2713"  # checkmark
        status = "Ready"
    elif val >= threshold * 0.85:
        css = "score-warning"
        icon = "\u26a0"  # warning sign
        status = "Almost ready"
    else:
        css = "score-poor"
        icon = "\u2715"  # multiplication x
        status = "Needs work"

    safe_label = html.escape(str(label))
    return (
        f'<span class="score-display {css}" title="{safe_label}: {pct}%">'
        f'<span class="score-icon">{icon}</span> '
        f'{pct}% \u2014 {status}'
        f'</span>'
    )


# ---------------------------------------------------------------------------
# 2. ISO-8601 parsing helper (stdlib only)
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"           # date
    r"[T ](\d{2}):(\d{2}):(\d{2})"        # time
    r"(?:\.(\d+))?"                         # optional fractional seconds
    r"(Z|[+-]\d{2}:\d{2})?$"              # optional timezone
)


def _parse_iso(value):
    """Parse an ISO-8601 datetime string into a timezone-aware datetime.

    Handles:
        2026-02-18T14:30:00Z
        2026-02-18T14:30:00
        2026-02-18T14:30:00.123456Z
        2026-02-18T14:30:00+05:00
        2026-02-18 14:30:00
    """
    s = str(value).strip()
    m = _ISO_RE.match(s)
    if not m:
        raise ValueError(f"Cannot parse ISO timestamp: {s}")

    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
    # Fractional seconds (truncate to microseconds)
    frac = m.group(7)
    microsecond = int(frac[:6].ljust(6, "0")) if frac else 0
    # Timezone
    tz_str = m.group(8)
    if tz_str is None or tz_str == "":
        tz = timezone.utc  # treat naive as UTC
    elif tz_str == "Z":
        tz = timezone.utc
    else:
        sign = 1 if tz_str[0] == "+" else -1
        tz_parts = tz_str[1:].split(":")
        tz_hours = int(tz_parts[0])
        tz_mins = int(tz_parts[1]) if len(tz_parts) > 1 else 0
        from datetime import timedelta
        tz = timezone(timedelta(hours=sign * tz_hours, minutes=sign * tz_mins))

    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)


# ---------------------------------------------------------------------------
# 3. Error Recovery Dictionary
# ---------------------------------------------------------------------------

ERROR_RECOVERY = {
    "cat1_stig": {
        "what": "A Critical (CAT-I) security finding was detected",
        "why": (
            "CAT-I findings block deployment \u2014 they represent "
            "vulnerabilities that could be immediately exploited"
        ),
        "fix": (
            "1. Review the finding details in the STIG tab\n"
            "2. Fix the underlying code vulnerability\n"
            "3. Re-run security scan to verify fix\n"
            "4. Document the fix in the audit trail"
        ),
        "who": "Developer + Security team",
        "time": "4-8 hours",
    },
    "readiness_below_threshold": {
        "what": "Requirements readiness score is below the 70% threshold",
        "why": (
            "Proceeding with incomplete requirements leads to rework "
            "and cost overruns"
        ),
        "fix": (
            "1. Check which dimension is lowest (completeness, clarity, "
            "feasibility, compliance, testability)\n"
            "2. For low COMPLETENESS: Add missing requirement areas\n"
            "3. For low CLARITY: Replace vague terms ('fast', 'secure') "
            "with measurable criteria\n"
            "4. For low FEASIBILITY: Validate technical constraints with "
            "architects\n"
            "5. For low COMPLIANCE: Add NIST control mappings\n"
            "6. For low TESTABILITY: Add acceptance criteria to each "
            "requirement"
        ),
        "who": "Requirements Analyst + Customer",
        "time": "1-3 hours of additional intake",
    },
    "compliance_bridge_gap": {
        "what": "ATO compliance coverage is below 95% during migration",
        "why": (
            "Operating below 95% compliance coverage risks losing "
            "Authorization to Operate"
        ),
        "fix": (
            "1. Run crosswalk engine to identify missing controls\n"
            "2. Map existing legacy controls to new system\n"
            "3. Create POA&M items for controls that can't be immediately "
            "migrated\n"
            "4. Get ISSO approval for temporary coverage gap"
        ),
        "who": "Compliance Officer + ISSO",
        "time": "1-2 days",
    },
    "connection_validation_failed": {
        "what": "System connection validation failed during build",
        "why": (
            "Your application can't connect to required services "
            "(database, APIs, auth)"
        ),
        "fix": (
            "1. Check database connection string in environment variables\n"
            "2. Verify API keys are set and not expired\n"
            "3. Test network connectivity to external services\n"
            "4. Check firewall rules allow outbound connections\n"
            "5. Review logs for specific connection error messages"
        ),
        "who": "Developer + Infrastructure team",
        "time": "1-4 hours",
    },
    "deployment_gate_failed": {
        "what": "Deployment blocked by security gates",
        "why": "One or more required security checks did not pass",
        "fix": (
            "1. Check which gate failed (tests, coverage, STIG, "
            "vulnerabilities, SBOM)\n"
            "2. For failed TESTS: Fix failing tests, ensure 80%+ coverage\n"
            "3. For STIG findings: Remediate CAT-I findings "
            "(CAT-II/III can be POA&M'd)\n"
            "4. For VULNERABILITIES: Update dependencies with known CVEs\n"
            "5. For missing SBOM: Regenerate SBOM with latest dependencies"
        ),
        "who": "Developer + Security team",
        "time": "2-8 hours depending on findings",
    },
    "ato_boundary_red": {
        "what": "Proposed change would invalidate the current ATO",
        "why": (
            "RED-tier changes (classification upgrade, boundary expansion) "
            "require re-authorization \u2014 this is a full stop"
        ),
        "fix": (
            "1. Review the boundary impact assessment details\n"
            "2. Generate alternative COAs that stay within current ATO "
            "boundary\n"
            "3. If no alternative exists, plan for re-authorization "
            "(add 3-6 months)\n"
            "4. Brief the ISSO and Authorizing Official on the impact\n"
            "5. Consider splitting the requirement into ATO-safe and "
            "ATO-impacting parts"
        ),
        "who": "ISSO + Authorizing Official + Program Manager",
        "time": "1-2 weeks for alternative COAs, 3-6 months for re-authorization",
    },
    "database_not_initialized": {
        "what": "The ICDEV database has not been set up yet",
        "why": (
            "ICDEV needs its database to track projects, compliance, "
            "and audit trails"
        ),
        "fix": (
            "Run this command:\n"
            "  python tools/db/init_icdev_db.py\n\n"
            "This creates the database with all required tables. "
            "It only needs to be done once."
        ),
        "who": "Any team member",
        "time": "Under 1 minute",
    },
    "session_not_found": {
        "what": "The intake session was not found",
        "why": (
            "The session ID may be incorrect, or the session may have expired"
        ),
        "fix": (
            "1. Double-check the session ID\n"
            "2. List active sessions: python tools/requirements/"
            "intake_engine.py --project-id <id> --list\n"
            "3. If session expired (>30 days), create a new session\n"
            "4. Previous session data is preserved in the database"
        ),
        "who": "Requirements Analyst",
        "time": "5 minutes",
    },
    "fips199_required": {
        "what": "FIPS 199 security categorization has not been completed",
        "why": (
            "Security categorization determines your baseline controls "
            "\u2014 all compliance work depends on this"
        ),
        "fix": (
            "1. Run: python tools/compliance/fips199_categorizer.py "
            "--project-id <id> --list-catalog\n"
            "2. Select information types that match your system's data\n"
            "3. Run categorization: python tools/compliance/"
            "fips199_categorizer.py --project-id <id> --categorize\n"
            "4. Review and approve the categorization result"
        ),
        "who": "ISSO + System Owner",
        "time": "30-60 minutes",
    },
    "cve_sla_breach": {
        "what": "A vulnerability fix deadline has been missed",
        "why": (
            "Critical CVEs must be fixed within 48 hours per supply chain SLA"
        ),
        "fix": (
            "1. Review the CVE details and affected component\n"
            "2. Check if a patched version is available\n"
            "3. If patch available: Update dependency and test\n"
            "4. If no patch: Implement compensating control and document "
            "in POA&M\n"
            "5. Notify ISSO of the SLA breach"
        ),
        "who": "Developer + Security team + ISSO",
        "time": "4-24 hours depending on complexity",
    },
    "framework_selection_unclear": {
        "what": (
            "It's unclear which compliance frameworks apply to your project"
        ),
        "why": (
            "Choosing the wrong frameworks wastes effort; missing a required "
            "framework delays ATO"
        ),
        "fix": (
            "Answer these questions:\n"
            "- Is this a cloud service? \u2192 FedRAMP required\n"
            "- Is this for a DoD contractor? \u2192 CMMC Level 2+ required\n"
            "- Does it handle CUI? \u2192 NIST 800-171 required\n"
            "- What impact level? \u2192 IL4/IL5 = FedRAMP Moderate, "
            "IL6 = FedRAMP High\n"
            "- Most projects need: NIST 800-53 + FedRAMP + CMMC\n"
            "- The crosswalk engine maps controls across frameworks "
            "automatically"
        ),
        "who": "ISSO + Contracting Officer",
        "time": "15-30 minutes to decide",
    },
}


# ---------------------------------------------------------------------------
# 4. Quick Path Definitions
# ---------------------------------------------------------------------------

QUICK_PATHS = [
    {
        "id": "quick_ato",
        "title": "Quick ATO Package",
        "icon": "\U0001f6e1",
        "description": (
            "Generate the minimum required ATO artifacts for your project. "
            "Covers FIPS 199 categorization, SSP, POA&M, STIG checklist, "
            "and SBOM."
        ),
        "audience": "ISSO, Compliance Officer",
        "estimated_time": "2-4 hours",
        "steps": [
            {
                "name": "Categorize System",
                "tool": "fips199_categorizer.py --categorize",
                "desc": "Determine security impact level",
            },
            {
                "name": "Validate Requirements",
                "tool": "fips200_validator.py",
                "desc": "Check all 17 security areas",
            },
            {
                "name": "Generate SSP",
                "tool": "ssp_generator.py",
                "desc": "Create System Security Plan",
            },
            {
                "name": "Create POA&M",
                "tool": "poam_generator.py",
                "desc": "Document security gaps with deadlines",
            },
            {
                "name": "Run STIG Check",
                "tool": "stig_checker.py",
                "desc": "Evaluate security technical compliance",
            },
            {
                "name": "Generate SBOM",
                "tool": "sbom_generator.py",
                "desc": "Inventory all software components",
            },
        ],
    },
    {
        "id": "build_and_ship",
        "title": "Build & Ship",
        "icon": "\U0001f680",
        "description": (
            "Scaffold a new project, write tests, build code, run security "
            "scans, and generate deployment files \u2014 the complete "
            "development pipeline."
        ),
        "audience": "Developer, Architect",
        "estimated_time": "4-8 hours",
        "steps": [
            {
                "name": "Create Project",
                "tool": "project_create.py",
                "desc": "Initialize project with compliance scaffolding",
            },
            {
                "name": "Write Tests",
                "tool": "test_writer.py",
                "desc": "Generate test cases from requirements",
            },
            {
                "name": "Build Code",
                "tool": "code_generator.py",
                "desc": "TDD: write code to pass tests",
            },
            {
                "name": "Run Security Scans",
                "tool": "sast_runner.py + dependency_auditor.py",
                "desc": "Check for vulnerabilities",
            },
            {
                "name": "Generate IaC",
                "tool": "terraform_generator.py + k8s_generator.py",
                "desc": "Create deployment infrastructure",
            },
            {
                "name": "Deploy",
                "tool": "pipeline_generator.py",
                "desc": "Generate CI/CD pipeline",
            },
        ],
    },
    {
        "id": "intake_to_approval",
        "title": "Requirements to Approval",
        "icon": "\U0001f4cb",
        "description": (
            "Capture requirements through AI-guided conversation, analyze "
            "gaps, decompose into work items, generate COAs, and route for "
            "approval."
        ),
        "audience": "Program Manager, Requirements Analyst",
        "estimated_time": "2-6 hours",
        "steps": [
            {
                "name": "Start Intake Session",
                "tool": "intake_engine.py --new",
                "desc": "AI-guided requirements conversation",
            },
            {
                "name": "Upload Documents",
                "tool": "document_extractor.py --upload",
                "desc": "Extract requirements from SOW/CDD",
            },
            {
                "name": "Detect Gaps",
                "tool": "gap_detector.py",
                "desc": "Find missing or vague requirements",
            },
            {
                "name": "Score Readiness",
                "tool": "readiness_scorer.py",
                "desc": "Check if requirements are complete",
            },
            {
                "name": "Decompose Work",
                "tool": "decomposition_engine.py",
                "desc": "Break into epics, features, stories",
            },
            {
                "name": "Generate COAs",
                "tool": "coa_generator.py",
                "desc": "Create 3 cost/schedule options",
            },
            {
                "name": "Submit for Approval",
                "tool": "approval_manager.py",
                "desc": "Route to reviewers",
            },
        ],
    },
    {
        "id": "modernize_legacy",
        "title": "Modernize Legacy App",
        "icon": "\U0001f504",
        "description": (
            "Analyze a legacy application, assess the best migration "
            "strategy (7Rs), create a migration plan, and generate "
            "modernized code while maintaining ATO compliance."
        ),
        "audience": "Architect, Developer, ISSO",
        "estimated_time": "1-3 weeks",
        "steps": [
            {
                "name": "Register & Analyze",
                "tool": "legacy_analyzer.py",
                "desc": "Scan codebase for complexity and tech debt",
            },
            {
                "name": "Extract Architecture",
                "tool": "architecture_extractor.py",
                "desc": "Map components, dependencies, data flows",
            },
            {
                "name": "UI Analysis",
                "tool": "ui_analyzer.py",
                "desc": "Analyze UI screenshots for complexity",
            },
            {
                "name": "7R Assessment",
                "tool": "seven_r_assessor.py",
                "desc": "Score: Rehost, Replatform, Refactor, Rearchitect...",
            },
            {
                "name": "Create Migration Plan",
                "tool": "migration_code_generator.py",
                "desc": "Generate migration code and plan",
            },
            {
                "name": "Validate Compliance",
                "tool": "compliance_bridge.py",
                "desc": "Ensure ATO coverage maintained",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# 5. Wizard Logic Data
# ---------------------------------------------------------------------------

WIZARD_STEPS = [
    {
        "question": "What are you trying to do?",
        "options": [
            {
                "id": "build",
                "icon": "\U0001f528",
                "title": "Build a New Application",
                "desc": "Start a new project from scratch with compliance built in",
            },
            {
                "id": "modernize",
                "icon": "\U0001f504",
                "title": "Modernize a Legacy App",
                "desc": "Migrate an existing system to modern architecture",
            },
            {
                "id": "comply",
                "icon": "\U0001f6e1",
                "title": "Get ATO / Compliance",
                "desc": "Generate security artifacts and pass compliance gates",
            },
            {
                "id": "requirements",
                "icon": "\U0001f4cb",
                "title": "Capture Requirements",
                "desc": "Gather and structure requirements from stakeholders",
            },
        ],
    },
    {
        "question": "What is your role?",
        "options": [
            {
                "id": "pm",
                "icon": "\U0001f4ca",
                "title": "Program Manager",
                "desc": "I manage timelines, budgets, and stakeholders",
            },
            {
                "id": "developer",
                "icon": "\U0001f4bb",
                "title": "Developer / Architect",
                "desc": "I write code and design systems",
            },
            {
                "id": "isso",
                "icon": "\U0001f510",
                "title": "ISSO / Security Officer",
                "desc": "I manage security and compliance",
            },
            {
                "id": "co",
                "icon": "\U0001f4dd",
                "title": "Contracting Officer",
                "desc": "I manage contracts and vendor requirements",
            },
            {
                "id": "analyst",
                "icon": "\U0001f50d",
                "title": "Analyst",
                "desc": "I research data, requirements, and threats",
            },
            {
                "id": "solutions_architect",
                "icon": "\U0001f3d7",
                "title": "Solutions Architect",
                "desc": "I design technical solutions and architectures",
            },
            {
                "id": "sales_engineer",
                "icon": "\U0001f4e1",
                "title": "Sales Engineer",
                "desc": "I demo capabilities and support proposals",
            },
            {
                "id": "innovator",
                "icon": "\U0001f4a1",
                "title": "Innovator",
                "desc": "I explore emerging tech and prototype ideas",
            },
            {
                "id": "biz_dev",
                "icon": "\U0001f91d",
                "title": "Business Development",
                "desc": "I identify opportunities and grow partnerships",
            },
        ],
    },
    {
        "question": "What classification level?",
        "options": [
            {
                "id": "il2",
                "icon": "\U0001f310",
                "title": "IL2 \u2014 Public",
                "desc": "Non-sensitive data, commercial cloud OK",
            },
            {
                "id": "il4",
                "icon": "\U0001f512",
                "title": "IL4 \u2014 CUI (GovCloud)",
                "desc": "Controlled Unclassified Information in AWS GovCloud",
            },
            {
                "id": "il5",
                "icon": "\U0001f510",
                "title": "IL5 \u2014 CUI (Dedicated)",
                "desc": "CUI requiring dedicated GovCloud infrastructure",
            },
            {
                "id": "il6",
                "icon": "\U0001f6d1",
                "title": "IL6 \u2014 SECRET",
                "desc": "Classified data requiring SIPR network",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# 6. Path Recommendation Logic
# ---------------------------------------------------------------------------

# Lookup table: (goal, role) -> path_id
# When the combination has a special override, it is listed here.
# Otherwise the default goal-based mapping applies.
_GOAL_ROLE_OVERRIDES = {
    ("build", "pm"): "intake_to_approval",
    ("build", "sales_engineer"): "intake_to_approval",
    ("build", "biz_dev"): "intake_to_approval",
    ("comply", "developer"): "build_and_ship",
}

# Default mapping: goal -> path_id
_GOAL_DEFAULTS = {
    "build": "build_and_ship",
    "comply": "quick_ato",
    "requirements": "intake_to_approval",
    "modernize": "modernize_legacy",
}

# Classification-specific notes
_CLASSIFICATION_NOTES = {
    "il2": "IL2 (public) has the fewest compliance requirements. FedRAMP Low may apply.",
    "il4": "IL4 (CUI / GovCloud) requires FedRAMP Moderate and NIST 800-171.",
    "il5": "IL5 (CUI / Dedicated) requires FedRAMP Moderate+ on dedicated infrastructure.",
    "il6": (
        "IL6 (SECRET) requires SIPR network, NSA Type 1 encryption, FedRAMP High, "
        "and air-gapped CI/CD. Plan for additional lead time."
    ),
}


def _find_path(path_id):
    """Return the QUICK_PATHS entry matching *path_id*, or None."""
    for p in QUICK_PATHS:
        if p["id"] == path_id:
            return p
    return None


def recommend_path(goal, role, classification):
    """Given wizard answers, return a recommended quick path and first steps.

    Args:
        goal:           One of "build", "modernize", "comply", "requirements".
        role:           One of "pm", "developer", "isso", "co".
        classification: One of "il2", "il4", "il5", "il6".

    Returns:
        dict with keys:
            path_id    - id of the recommended QUICK_PATHS entry
            path_name  - human-readable title
            steps      - list of step dicts from the quick path
            first_command - suggested CLI command to start
            notes      - classification-specific advisory text
    """
    goal = str(goal).lower().strip()
    role = str(role).lower().strip()
    classification = str(classification).lower().strip()

    # Determine path_id: check overrides first, then goal defaults
    path_id = _GOAL_ROLE_OVERRIDES.get((goal, role))
    if path_id is None:
        path_id = _GOAL_DEFAULTS.get(goal, "build_and_ship")

    path = _find_path(path_id)
    if path is None:
        # Fallback to build_and_ship if somehow nothing matched
        path = _find_path("build_and_ship") or QUICK_PATHS[0]
        path_id = path["id"]

    # Build the first command suggestion
    first_step = path["steps"][0] if path["steps"] else {}
    tool_name = first_step.get("tool", "")
    first_command = f"python tools/... {tool_name}" if tool_name else ""

    # Specific first-command overrides for common workflows
    _FIRST_COMMANDS = {
        "quick_ato": (
            "python tools/compliance/fips199_categorizer.py "
            "--project-id <your-project-id> --list-catalog"
        ),
        "build_and_ship": (
            "python tools/project/project_create.py "
            "--name <app-name> --type microservice"
        ),
        "intake_to_approval": (
            "python tools/requirements/intake_engine.py "
            "--project-id <your-project-id> --customer-name <name> "
            "--customer-org <org> --impact-level "
            + classification.upper()
            + " --json"
        ),
        "modernize_legacy": (
            "python tools/modernization/legacy_analyzer.py "
            "--project-id <your-project-id> --app-id <app-id> "
            "--source-path /path/to/legacy"
        ),
    }
    first_command = _FIRST_COMMANDS.get(path_id, first_command)

    notes = _CLASSIFICATION_NOTES.get(classification, "")

    return {
        "path_id": path_id,
        "path_name": path["title"],
        "steps": path["steps"],
        "first_command": first_command,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 7. Flask Registration
# ---------------------------------------------------------------------------

def register_ux_filters(app):
    """Register all UX helpers with a Flask application.

    Adds Jinja2 template filters and global variables so templates can use:
        {{ value | friendly_time }}
        {{ value | short_time }}
        {{ value | time_ago }}
        {{ "ATO" | glossary }}
        {{ score_display(0.78, 0.7, "Readiness") }}
        {{ ERROR_RECOVERY["cat1_stig"]["fix"] }}
        {{ QUICK_PATHS[0]["title"] }}
        {{ WIZARD_STEPS[0]["question"] }}

    Args:
        app: A Flask application instance.
    """
    # Template filters (used with the pipe operator: {{ val | filter }})
    app.jinja_env.filters["friendly_time"] = format_timestamp
    app.jinja_env.filters["short_time"] = format_timestamp_short
    app.jinja_env.filters["time_ago"] = format_time_ago
    app.jinja_env.filters["glossary"] = glossary_term

    # Template globals (callable directly in templates)
    app.jinja_env.globals["score_display"] = score_display
    app.jinja_env.globals["ERROR_RECOVERY"] = ERROR_RECOVERY
    app.jinja_env.globals["QUICK_PATHS"] = QUICK_PATHS
    app.jinja_env.globals["WIZARD_STEPS"] = WIZARD_STEPS
    app.jinja_env.globals["GLOSSARY"] = GLOSSARY
    app.jinja_env.globals["recommend_path"] = recommend_path


# ---------------------------------------------------------------------------
# CUI // SP-CTI
# ---------------------------------------------------------------------------
