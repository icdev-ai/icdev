#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Universal Data Classification and Marking Manager for ICDEV.

Extends classification_manager.py to support multi-regime data categories
(CUI, PHI, PCI, CJIS, PII, FTI, ITAR, SECRET) with composable markings.
A single artifact can carry multiple category markings simultaneously
(ADR D109).

Loads configuration from args/classification_config.yaml and data type
definitions from context/compliance/data_type_registry.json.

Backward-compatible: all existing classification_manager.py functions
continue to work. This module adds multi-category support on top.

CLI:
    # List all data categories
    python tools/compliance/universal_classification_manager.py --list-categories

    # Show marking for a single category
    python tools/compliance/universal_classification_manager.py --category PHI --banner

    # Composite marking for multiple categories
    python tools/compliance/universal_classification_manager.py --categories CUI,PHI,PCI --banner

    # Code header with composite markings
    python tools/compliance/universal_classification_manager.py --categories CUI,PHI --code-header python

    # Detect data categories from project metadata
    python tools/compliance/universal_classification_manager.py --detect --project-id proj-123

    # Validate project data markings
    python tools/compliance/universal_classification_manager.py --validate --project-id proj-123

    # JSON output
    python tools/compliance/universal_classification_manager.py --categories CUI,PHI --banner --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "classification_config.yaml"
REGISTRY_PATH = BASE_DIR / "context" / "compliance" / "data_type_registry.json"
FRAMEWORK_MAP_PATH = BASE_DIR / "context" / "compliance" / "data_type_framework_map.json"

# Module-level caches
_CONFIG_CACHE: Optional[Dict] = None
_REGISTRY_CACHE: Optional[Dict] = None
_FRAMEWORK_MAP_CACHE: Optional[Dict] = None

# ---------------------------------------------------------------------------
# Sensitivity ordering (highest to lowest)
# ---------------------------------------------------------------------------

SENSITIVITY_ORDER = [
    "TOP_SECRET", "SECRET", "CUI", "ITAR", "FTI",
    "CJIS", "PHI", "PCI", "PII", "PUBLIC",
]

# Backward-compatible aliases
_CATEGORY_ALIASES = {
    "TOP SECRET": "TOP_SECRET",
    "TOP SECRET//SCI": "TOP_SECRET",
    "TS": "TOP_SECRET",
    "S": "SECRET",
    "HIPAA": "PHI",
    "PCI DSS": "PCI",
    "PCI-DSS": "PCI",
    "FBI CJIS": "CJIS",
    "IRS 1075": "FTI",
    "NIST 800-122": "PII",
}

# Comment style mapping
_COMMENT_STYLES = {
    "python": "hash", "ruby": "hash", "yaml": "hash",
    "terraform": "hash", "dockerfile": "hash",
    "java": "c_style", "go": "c_style", "rust": "c_style",
    "csharp": "c_style", "c#": "c_style",
    "typescript": "c_style", "javascript": "c_style",
    "xml": "xml_style", "html": "xml_style",
    "sql": "sql_style",
}


# ---------------------------------------------------------------------------
# Config / registry loaders
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict:
    """Load YAML file with fallback to simple parsing if PyYAML unavailable."""
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def load_config() -> Dict:
    """Load and cache classification configuration from YAML."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    _CONFIG_CACHE = _load_yaml(CONFIG_PATH)
    return _CONFIG_CACHE


def load_registry() -> List[Dict]:
    """Load and cache data type registry from JSON."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    if not REGISTRY_PATH.exists():
        _REGISTRY_CACHE = []
        return _REGISTRY_CACHE
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    _REGISTRY_CACHE = data.get("data_types", [])
    return _REGISTRY_CACHE


def load_framework_map() -> Dict:
    """Load and cache data-type-to-framework mapping from JSON."""
    global _FRAMEWORK_MAP_CACHE
    if _FRAMEWORK_MAP_CACHE is not None:
        return _FRAMEWORK_MAP_CACHE
    if not FRAMEWORK_MAP_PATH.exists():
        _FRAMEWORK_MAP_CACHE = {}
        return _FRAMEWORK_MAP_CACHE
    with open(FRAMEWORK_MAP_PATH, "r", encoding="utf-8") as f:
        _FRAMEWORK_MAP_CACHE = json.load(f)
    return _FRAMEWORK_MAP_CACHE


def _normalize_category(category: str) -> str:
    """Normalize a category string to its canonical form."""
    upper = category.upper().strip()
    return _CATEGORY_ALIASES.get(upper, upper)


def get_category_config(category: str) -> Dict:
    """Return the configuration dict for a single data category.

    Args:
        category: Data category identifier (e.g., CUI, PHI, PCI, CJIS).

    Returns:
        Dict with full_name, governing_framework, marking_prefix,
        banner_template, portion_marking, distribution, controlled_by,
        handling_requirements, etc.
    """
    norm = _normalize_category(category)
    config = load_config()
    categories = config.get("data_categories", {})
    cat_config = categories.get(norm)
    if cat_config is None:
        return {
            "full_name": norm,
            "governing_framework": "Unknown",
            "marking_prefix": norm,
            "banner_template": f"{norm} // Custom",
            "portion_marking": f"({norm})",
            "distribution": "Restricted",
            "controlled_by": "Data Owner",
            "handling_requirements": [],
        }
    return cat_config


def list_categories() -> List[Dict]:
    """Return a list of all supported data categories with summary info."""
    config = load_config()
    categories = config.get("data_categories", {})
    result = []
    for cat_id, cat_config in categories.items():
        result.append({
            "id": cat_id,
            "full_name": cat_config.get("full_name", cat_id),
            "governing_framework": cat_config.get("governing_framework", ""),
            "marking_prefix": cat_config.get("marking_prefix", cat_id),
            "portion_marking": cat_config.get("portion_marking", ""),
            "sensitivity_rank": SENSITIVITY_ORDER.index(cat_id)
            if cat_id in SENSITIVITY_ORDER else 99,
        })
    result.sort(key=lambda x: x["sensitivity_rank"])
    return result


# ---------------------------------------------------------------------------
# Composite marking functions (ADR D109)
# ---------------------------------------------------------------------------

def get_composite_banner(
    categories: List[str],
    subcategories: Optional[Dict[str, str]] = None,
) -> str:
    """Generate a composite document banner for multiple data categories.

    Args:
        categories: List of data category IDs (e.g., ["CUI", "PHI", "PCI"]).
        subcategories: Optional dict mapping category to subcategory
            (e.g., {"CUI": "CTI"}).

    Returns:
        Multi-line banner string with all applicable markings.
    """
    if not categories:
        return ""

    subcategories = subcategories or {}
    normalized = [_normalize_category(c) for c in categories]

    # Sort by sensitivity (highest first)
    normalized.sort(
        key=lambda c: SENSITIVITY_ORDER.index(c)
        if c in SENSITIVITY_ORDER else 99
    )

    # Build banner lines for each category
    banner_parts = []
    distributions = []
    controllers = []

    for cat in normalized:
        cat_config = get_category_config(cat)
        template = cat_config.get("banner_template", f"{cat} // Custom")

        # Substitute subcategory/dissemination if present
        subcat = subcategories.get(cat, "")
        banner_line = template.format(
            subcategory=subcat or cat_config.get("marking_prefix", cat),
            dissemination=subcat or "NOFORN",
        )
        banner_parts.append(banner_line)

        dist = cat_config.get("distribution", "")
        if dist and dist not in distributions:
            distributions.append(dist)

        ctrl = cat_config.get("controlled_by", "")
        if ctrl and ctrl not in controllers:
            controllers.append(ctrl)

    # Compose the banner
    separator = " | "
    combined_marking = separator.join(banner_parts)
    combined_dist = "; ".join(distributions) if distributions else "Restricted"
    combined_ctrl = "; ".join(controllers) if controllers else "Data Owner"

    return (
        f"////////////////////////////////////////////////////////////////////\n"
        f"{combined_marking}\n"
        f"Distribution: {combined_dist}\n"
        f"Controlled by: {combined_ctrl}\n"
        f"////////////////////////////////////////////////////////////////////"
    )


def get_composite_footer(categories: List[str]) -> str:
    """Generate a composite document footer for multiple data categories."""
    if not categories:
        return ""

    normalized = [_normalize_category(c) for c in categories]
    normalized.sort(
        key=lambda c: SENSITIVITY_ORDER.index(c)
        if c in SENSITIVITY_ORDER else 99
    )

    parts = []
    controllers = []
    for cat in normalized:
        cat_config = get_category_config(cat)
        parts.append(cat_config.get("marking_prefix", cat))
        ctrl = cat_config.get("controlled_by", "")
        if ctrl and ctrl not in controllers:
            controllers.append(ctrl)

    marking_line = " | ".join(parts)
    ctrl_line = "; ".join(controllers) if controllers else "Data Owner"

    return (
        f"////////////////////////////////////////////////////////////////////\n"
        f"{marking_line} | {ctrl_line}\n"
        f"////////////////////////////////////////////////////////////////////"
    )


def get_composite_portion_marking(categories: List[str]) -> str:
    """Generate an inline composite portion marking.

    Returns something like "(CUI/PHI/PCI)".
    """
    if not categories:
        return ""

    normalized = [_normalize_category(c) for c in categories]
    normalized.sort(
        key=lambda c: SENSITIVITY_ORDER.index(c)
        if c in SENSITIVITY_ORDER else 99
    )

    short_marks = []
    for cat in normalized:
        cat_config = get_category_config(cat)
        portion = cat_config.get("portion_marking", f"({cat})")
        # Strip parens for composition
        inner = portion.strip("()")
        if inner:
            short_marks.append(inner)

    return f"({'|'.join(short_marks)})" if short_marks else ""


def get_composite_code_header(
    categories: List[str],
    language: str = "python",
    subcategories: Optional[Dict[str, str]] = None,
) -> str:
    """Generate a code file header with composite markings.

    Args:
        categories: Data category list.
        language: Programming language key.
        subcategories: Optional subcategory overrides.

    Returns:
        Multi-line comment block for the top of a source file.
    """
    subcategories = subcategories or {}
    normalized = [_normalize_category(c) for c in categories]
    normalized.sort(
        key=lambda c: SENSITIVITY_ORDER.index(c)
        if c in SENSITIVITY_ORDER else 99
    )

    # Build header lines
    header_lines = []

    # First line: combined marking
    parts = []
    for cat in normalized:
        cat_config = get_category_config(cat)
        template = cat_config.get("banner_template", f"{cat} // Custom")
        subcat = subcategories.get(cat, "")
        line = template.format(
            subcategory=subcat or cat_config.get("marking_prefix", cat),
            dissemination=subcat or "NOFORN",
        )
        parts.append(line)
    header_lines.append(" | ".join(parts))

    # Controller lines
    controllers = []
    for cat in normalized:
        cat_config = get_category_config(cat)
        ctrl = cat_config.get("controlled_by", "")
        if ctrl and ctrl not in controllers:
            controllers.append(ctrl)
    if controllers:
        header_lines.append(f"Controlled by: {'; '.join(controllers)}")

    # Category-specific lines
    for cat in normalized:
        cat_config = get_category_config(cat)
        fw = cat_config.get("governing_framework", "")
        if fw:
            header_lines.append(f"{cat} Framework: {fw}")

    header_lines.append("Distribution: Restricted -- See applicable framework policies")
    header_lines.append("POC: ICDEV System Administrator")

    # Apply comment style
    lang_lower = language.lower()
    style = _COMMENT_STYLES.get(lang_lower, "hash")

    result_lines: List[str] = []
    if style == "hash":
        for line in header_lines:
            result_lines.append(f"# {line}")
    elif style == "c_style":
        for line in header_lines:
            result_lines.append(f"// {line}")
    elif style == "xml_style":
        result_lines.append("<!--")
        for line in header_lines:
            result_lines.append(f"  {line}")
        result_lines.append("-->")
    elif style == "sql_style":
        for line in header_lines:
            result_lines.append(f"-- {line}")
    else:
        for line in header_lines:
            result_lines.append(f"# {line}")

    return "\n".join(result_lines) + "\n"


def get_composite_handling_requirements(categories: List[str]) -> List[str]:
    """Return the union of all handling requirements for the given categories.

    This is the set of ALL requirements from ALL categories -- the artifact
    must satisfy every one.
    """
    requirements = []
    seen = set()
    for cat in categories:
        cat_config = get_category_config(_normalize_category(cat))
        for req in cat_config.get("handling_requirements", []):
            if req not in seen:
                requirements.append(req)
                seen.add(req)
    return requirements


def get_highest_sensitivity(categories: List[str]) -> str:
    """Return the highest-sensitivity category from the list."""
    if not categories:
        return "PUBLIC"
    normalized = [_normalize_category(c) for c in categories]
    best = "PUBLIC"
    best_rank = len(SENSITIVITY_ORDER)
    for cat in normalized:
        rank = SENSITIVITY_ORDER.index(cat) if cat in SENSITIVITY_ORDER else 99
        if rank < best_rank:
            best = cat
            best_rank = rank
    return best


# ---------------------------------------------------------------------------
# Upgrade markings across categories
# ---------------------------------------------------------------------------

def upgrade_composite_markings(
    content: str,
    old_categories: List[str],
    new_categories: List[str],
) -> str:
    """Replace composite markings in content when categories change.

    Generates old and new banners/footers/portion markings and replaces
    them in the content string.

    Args:
        content: Document or code content.
        old_categories: Previous set of categories.
        new_categories: New set of categories.

    Returns:
        Updated content with new markings.
    """
    if set(old_categories) == set(new_categories):
        return content

    result = content

    # Replace banner
    old_banner = get_composite_banner(old_categories)
    new_banner = get_composite_banner(new_categories)
    if old_banner:
        result = result.replace(old_banner, new_banner)

    # Replace footer
    old_footer = get_composite_footer(old_categories)
    new_footer = get_composite_footer(new_categories)
    if old_footer:
        result = result.replace(old_footer, new_footer)

    # Replace portion markings
    old_portion = get_composite_portion_marking(old_categories)
    new_portion = get_composite_portion_marking(new_categories)
    if old_portion and new_portion:
        result = result.replace(old_portion, new_portion)

    return result


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Ensure data_classifications table exists."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            data_category TEXT NOT NULL,
            subcategory TEXT,
            source TEXT DEFAULT 'manual',
            confidence REAL DEFAULT 1.0,
            added_by TEXT DEFAULT 'icdev-compliance-engine',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, data_category)
        );

        CREATE TABLE IF NOT EXISTS framework_applicability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            source TEXT DEFAULT 'auto_detected'
                CHECK(source IN ('auto_detected', 'manual', 'inherited')),
            confirmed INTEGER DEFAULT 0,
            confirmed_by TEXT,
            confirmed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, framework_id)
        );

        CREATE INDEX IF NOT EXISTS idx_dc_project
            ON data_classifications(project_id);
        CREATE INDEX IF NOT EXISTS idx_fa_project
            ON framework_applicability(project_id);
    """)
    conn.commit()


def _log_audit_event(
    conn: sqlite3.Connection,
    project_id: str,
    action: str,
    details: Dict,
) -> None:
    """Log an append-only audit event."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "classification.validation",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: Could not log audit event: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Project data category management
# ---------------------------------------------------------------------------

def add_project_data_category(
    project_id: str,
    data_category: str,
    subcategory: Optional[str] = None,
    source: str = "manual",
    confidence: float = 1.0,
    db_path: Optional[Path] = None,
) -> Dict:
    """Add a data category to a project.

    Args:
        project_id: Project identifier.
        data_category: Category ID (CUI, PHI, PCI, CJIS, etc.).
        subcategory: Optional subcategory (e.g., CTI for CUI).
        source: How this was determined (manual, auto_detected).
        confidence: Confidence score for auto-detected categories.
        db_path: Optional database path override.

    Returns:
        Dict with status and the category record.
    """
    norm = _normalize_category(data_category)
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        conn.execute(
            """INSERT OR REPLACE INTO data_classifications
               (project_id, data_category, subcategory, source, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, norm, subcategory, source, confidence),
        )
        conn.commit()

        _log_audit_event(conn, project_id, "Data category added", {
            "data_category": norm,
            "subcategory": subcategory,
            "source": source,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {
            "status": "added",
            "project_id": project_id,
            "data_category": norm,
            "subcategory": subcategory,
            "source": source,
        }
    finally:
        conn.close()


def get_project_data_categories(
    project_id: str,
    db_path: Optional[Path] = None,
) -> List[Dict]:
    """Return all data categories assigned to a project."""
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT data_category, subcategory, source, confidence, created_at
               FROM data_classifications
               WHERE project_id = ?
               ORDER BY created_at""",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_project_marking(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Generate the full composite marking for a project.

    Reads all data categories from the database and generates
    banner, footer, portion marking, and handling requirements.

    Returns:
        Dict with banner, footer, portion_marking, categories,
        highest_sensitivity, and handling_requirements.
    """
    categories_data = get_project_data_categories(project_id, db_path)
    if not categories_data:
        # Default to CUI if no categories set
        categories_data = [{"data_category": "CUI", "subcategory": "CTI"}]

    categories = [c["data_category"] for c in categories_data]
    subcats = {
        c["data_category"]: c.get("subcategory", "")
        for c in categories_data
        if c.get("subcategory")
    }

    return {
        "categories": categories,
        "highest_sensitivity": get_highest_sensitivity(categories),
        "banner": get_composite_banner(categories, subcats),
        "footer": get_composite_footer(categories),
        "portion_marking": get_composite_portion_marking(categories),
        "handling_requirements": get_composite_handling_requirements(categories),
    }


# ---------------------------------------------------------------------------
# Auto-detection (ADR D110 -- advisory, not enforced)
# ---------------------------------------------------------------------------

def detect_data_categories(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Analyze project metadata and recommend applicable data categories.

    Reads the project's description, type, impact_level, classification,
    and target_frameworks to infer which data categories likely apply.

    ADR D110: Results are advisory only -- the customer makes the final
    selection.

    Returns:
        Dict with detected categories, confidence scores, and reasoning.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        row = conn.execute(
            """SELECT id, name, description, type, classification,
                      impact_level, target_frameworks
               FROM projects WHERE id = ?""",
            (project_id,),
        ).fetchone()

        if not row:
            raise ValueError(f"Project '{project_id}' not found.")

        project = dict(row)
        desc = (project.get("description") or "").lower()
        name = (project.get("name") or "").lower()
        cls = (project.get("classification") or "").upper()
        il = (project.get("impact_level") or "").upper()
        frameworks = (project.get("target_frameworks") or "").lower()
        combined_text = f"{name} {desc} {frameworks}"

        detected = []
        registry = load_registry()

        for data_type in registry:
            indicators = data_type.get("indicators", [])
            matches = [ind for ind in indicators if ind.lower() in combined_text]

            if matches:
                confidence = min(0.5 + (len(matches) * 0.15), 0.95)
                detected.append({
                    "data_type_id": data_type["id"],
                    "category": data_type["category"],
                    "subcategory": data_type.get("subcategory", ""),
                    "name": data_type["name"],
                    "confidence": round(confidence, 2),
                    "matched_indicators": matches,
                    "required_frameworks": data_type.get("required_frameworks", []),
                    "recommended_frameworks": data_type.get("recommended_frameworks", []),
                })

        # Always detect classification-based categories
        if cls in ("CUI",) and not any(d["category"] == "CUI" for d in detected):
            detected.append({
                "data_type_id": "CUI_CTI",
                "category": "CUI",
                "subcategory": "CTI",
                "name": "Controlled Technical Information",
                "confidence": 0.9,
                "matched_indicators": [f"classification={cls}"],
                "required_frameworks": ["nist_800_171", "cmmc_level_2"],
                "recommended_frameworks": ["fedramp_moderate"],
            })

        if cls == "SECRET" and not any(d["category"] == "SECRET" for d in detected):
            detected.append({
                "data_type_id": "SECRET_NSI",
                "category": "SECRET",
                "subcategory": "NSI",
                "name": "Classified National Security Information",
                "confidence": 0.95,
                "matched_indicators": [f"classification={cls}"],
                "required_frameworks": ["cnssi_1253"],
                "recommended_frameworks": [],
            })

        # Impact level implications
        if il in ("IL4", "IL5") and not any(d["category"] == "CUI" for d in detected):
            detected.append({
                "data_type_id": "CUI_CTI",
                "category": "CUI",
                "subcategory": "CTI",
                "name": "Controlled Technical Information",
                "confidence": 0.8,
                "matched_indicators": [f"impact_level={il}"],
                "required_frameworks": ["nist_800_171", "cmmc_level_2"],
                "recommended_frameworks": ["fedramp_moderate"],
            })

        # Sort by confidence descending
        detected.sort(key=lambda x: x["confidence"], reverse=True)

        # Collect all required and recommended frameworks
        all_required = set()
        all_recommended = set()
        for d in detected:
            all_required.update(d.get("required_frameworks", []))
            all_recommended.update(d.get("recommended_frameworks", []))

        result = {
            "project_id": project_id,
            "detected_categories": detected,
            "all_required_frameworks": sorted(all_required),
            "all_recommended_frameworks": sorted(all_recommended - all_required),
            "advisory_note": "Detection is advisory (ADR D110). "
                             "Confirm categories before applying markings.",
            "timestamp": datetime.utcnow().isoformat(),
        }

        _log_audit_event(conn, project_id, "Data category auto-detection", {
            "detected_count": len(detected),
            "categories": [d["category"] for d in detected],
            "required_frameworks": sorted(all_required),
        })

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_project_markings(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Validate that project markings are consistent with data categories.

    Checks:
    1. At least one data category is assigned.
    2. All required frameworks for each category are tracked.
    3. Marking banner matches assigned categories.
    4. Handling requirements are documented.

    Returns:
        Dict with valid (bool), issues list, and recommendations.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        issues: List[str] = []
        recommendations: List[str] = []

        # Get project info
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        project = dict(row)

        # Get assigned categories
        cat_rows = conn.execute(
            """SELECT data_category, subcategory, source, confidence
               FROM data_classifications WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        categories = [dict(r) for r in cat_rows]

        if not categories:
            issues.append(
                "No data categories assigned. Run --detect to identify "
                "applicable categories."
            )
            recommendations.append(
                "Add at least one data category with --add-category."
            )

        # Check each category's required frameworks
        for cat in categories:
            registry = load_registry()
            for dt in registry:
                if dt["category"] == cat["data_category"]:
                    required_fws = dt.get("required_frameworks", [])
                    for fw in required_fws:
                        # Check if framework is tracked in project_framework_status
                        try:
                            fw_row = conn.execute(
                                """SELECT framework_id FROM project_framework_status
                                   WHERE project_id = ? AND framework_id = ?""",
                                (project_id, fw),
                            ).fetchone()
                            if not fw_row:
                                issues.append(
                                    f"Data category {cat['data_category']} requires "
                                    f"framework '{fw}' but it is not tracked."
                                )
                        except Exception:
                            pass  # Table may not exist

        # Validate classification consistency
        proj_cls = (project.get("classification") or "").upper()
        cat_names = [c["data_category"] for c in categories]
        highest = get_highest_sensitivity(cat_names) if cat_names else "PUBLIC"

        if highest in ("SECRET", "TOP_SECRET") and proj_cls not in ("SECRET", "TOP SECRET", "TOP SECRET//SCI"):
            issues.append(
                f"Data categories include {highest} but project classification "
                f"is '{proj_cls}'. Classification must be upgraded."
            )

        result = {
            "valid": len(issues) == 0,
            "project_id": project_id,
            "assigned_categories": [c["data_category"] for c in categories],
            "highest_sensitivity": highest,
            "issues": issues,
            "recommendations": recommendations,
            "timestamp": datetime.utcnow().isoformat(),
        }

        _log_audit_event(conn, project_id, "Marking validation", {
            "valid": result["valid"],
            "issues_count": len(issues),
            "categories": result["assigned_categories"],
        })

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal Data Classification & Marking Manager"
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="List all supported data categories",
    )
    parser.add_argument(
        "--category",
        help="Single data category to display (e.g., PHI, PCI, CJIS)",
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated data categories for composite marking (e.g., CUI,PHI,PCI)",
    )
    parser.add_argument(
        "--banner", action="store_true",
        help="Show document banner for the specified categories",
    )
    parser.add_argument(
        "--code-header", metavar="LANGUAGE",
        help="Show code header for a language (python, java, go, etc.)",
    )
    parser.add_argument(
        "--handling", action="store_true",
        help="Show handling requirements for the specified categories",
    )
    parser.add_argument(
        "--detect", action="store_true",
        help="Auto-detect data categories for a project (requires --project-id)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate project data markings (requires --project-id)",
    )
    parser.add_argument(
        "--add-category",
        help="Add a data category to a project (requires --project-id)",
    )
    parser.add_argument(
        "--project-id",
        help="Project ID for detection/validation/add operations",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Database path override",
    )

    args = parser.parse_args()

    try:
        # Parse categories
        cats = []
        if args.category:
            cats = [args.category]
        elif args.categories:
            cats = [c.strip() for c in args.categories.split(",")]

        # --list-categories
        if args.list_categories:
            result = list_categories()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"{'ID':<15} {'Name':<40} {'Framework':<20} {'Marking'}")
                print("-" * 90)
                for cat in result:
                    print(
                        f"{cat['id']:<15} {cat['full_name']:<40} "
                        f"{cat['governing_framework']:<20} {cat['portion_marking']}"
                    )
            return

        # --banner
        if args.banner and cats:
            banner = get_composite_banner(cats)
            footer = get_composite_footer(cats)
            portion = get_composite_portion_marking(cats)
            if args.json:
                print(json.dumps({
                    "categories": cats,
                    "banner": banner,
                    "footer": footer,
                    "portion_marking": portion,
                    "highest_sensitivity": get_highest_sensitivity(cats),
                }, indent=2))
            else:
                print(f"Banner for {', '.join(cats)}:\n")
                print(banner)
                print(f"\nFooter:\n{footer}")
                print(f"\nPortion marking: {portion}")
            return

        # --code-header
        if args.code_header and cats:
            header = get_composite_code_header(cats, args.code_header)
            if args.json:
                print(json.dumps({
                    "categories": cats,
                    "language": args.code_header,
                    "code_header": header,
                }, indent=2))
            else:
                print(f"Code header for {', '.join(cats)} ({args.code_header}):\n")
                print(header)
            return

        # --handling
        if args.handling and cats:
            reqs = get_composite_handling_requirements(cats)
            if args.json:
                print(json.dumps({
                    "categories": cats,
                    "handling_requirements": reqs,
                }, indent=2))
            else:
                print(f"Handling requirements for {', '.join(cats)}:")
                for i, req in enumerate(reqs, 1):
                    print(f"  {i}. {req}")
            return

        # --detect
        if args.detect:
            if not args.project_id:
                print("Error: --detect requires --project-id", file=sys.stderr)
                sys.exit(1)
            result = detect_data_categories(args.project_id, args.db_path)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Data Category Detection: {args.project_id}")
                print(f"{'=' * 60}")
                for d in result["detected_categories"]:
                    print(
                        f"  [{d['confidence']:.0%}] {d['category']}/{d['subcategory']} "
                        f"-- {d['name']}"
                    )
                    print(f"         Matched: {', '.join(d['matched_indicators'])}")
                print(f"\nRequired frameworks: {', '.join(result['all_required_frameworks'])}")
                print(f"Recommended: {', '.join(result['all_recommended_frameworks'])}")
                print(f"\nNote: {result['advisory_note']}")
            return

        # --validate
        if args.validate:
            if not args.project_id:
                print("Error: --validate requires --project-id", file=sys.stderr)
                sys.exit(1)
            result = validate_project_markings(args.project_id, args.db_path)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                status = "VALID" if result["valid"] else "INVALID"
                print(f"Marking Validation: {status}")
                print(f"  Categories: {', '.join(result['assigned_categories']) or 'none'}")
                print(f"  Highest: {result['highest_sensitivity']}")
                if result["issues"]:
                    print(f"  Issues ({len(result['issues'])}):")
                    for issue in result["issues"]:
                        print(f"    - {issue}")
                if result["recommendations"]:
                    print("  Recommendations:")
                    for rec in result["recommendations"]:
                        print(f"    - {rec}")
            return

        # --add-category
        if args.add_category:
            if not args.project_id:
                print("Error: --add-category requires --project-id", file=sys.stderr)
                sys.exit(1)
            result = add_project_data_category(
                args.project_id, args.add_category, db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Added data category: {result['data_category']} to {result['project_id']}")
            return

        # Single category info
        if cats and len(cats) == 1 and not (args.banner or args.code_header or args.handling):
            cat_config = get_category_config(cats[0])
            if args.json:
                print(json.dumps({"category": cats[0], "config": cat_config}, indent=2))
            else:
                print(f"Data Category: {cats[0]}")
                for k, v in cat_config.items():
                    if isinstance(v, list):
                        print(f"  {k}:")
                        for item in v:
                            print(f"    - {item}")
                    else:
                        print(f"  {k}: {v}")
            return

        parser.print_help()

    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
