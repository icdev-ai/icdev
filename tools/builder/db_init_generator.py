#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""DB Init Generator - generates standalone database init scripts for child apps.

Decision D27: Minimal DB + migration. Core tables first, expand as capabilities activate.

Consumes a blueprint dict (from tools/builder/app_blueprint.py) and generates a
self-contained Python script that initializes the child app's SQLite database.
The generated script has zero ICDEV imports and creates only the tables needed
for the child app's enabled capabilities.

CLI:
    python tools/builder/db_init_generator.py \\
        --blueprint /path/to/blueprint.json \\
        --output-dir /path/to/output \\
        --json
"""

import argparse
import json
import logging
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger = logging.getLogger("icdev.db_init_generator")

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit event")


# ============================================================
# TABLE DEFINITIONS (used to generate child app's init script)
# ============================================================
# Each dict maps table_name -> CREATE TABLE SQL.
# The SQL is standalone and uses CREATE TABLE IF NOT EXISTS
# so re-running is idempotent.

CORE_TABLES: Dict[str, str] = {
    "projects": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT,
            status TEXT DEFAULT 'active',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "agents": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            port INTEGER,
            status TEXT DEFAULT 'inactive',
            last_health_check TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "a2a_tasks": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id TEXT PRIMARY KEY,
            source_agent TEXT,
            target_agent TEXT,
            task_type TEXT NOT NULL,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );"""),

    "audit_trail": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT NOT NULL,
            actor TEXT,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI'
        );"""),

    "knowledge_patterns": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS knowledge_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,
            pattern_signature TEXT NOT NULL,
            description TEXT,
            solution TEXT,
            confidence REAL DEFAULT 0.0,
            occurrences INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "self_healing_events": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS self_healing_events (
            id TEXT PRIMARY KEY,
            pattern_id TEXT REFERENCES knowledge_patterns(id),
            trigger_type TEXT NOT NULL,
            action_taken TEXT,
            result TEXT,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "tasks": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            assigned_agent TEXT,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );"""),

    "deployments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            environment TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            artifacts TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "metric_snapshots": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS metric_snapshots (
            id TEXT PRIMARY KEY,
            metric_type TEXT NOT NULL,
            metric_value REAL,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "alerts": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            message TEXT,
            project_id TEXT,
            acknowledged INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "code_reviews": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS code_reviews (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            reviewer TEXT,
            status TEXT DEFAULT 'pending',
            findings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "maintenance_audits": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS maintenance_audits (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            audit_type TEXT NOT NULL,
            score REAL,
            findings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),
}


COMPLIANCE_TABLES: Dict[str, str] = {
    "compliance_controls": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS compliance_controls (
            id TEXT PRIMARY KEY,
            family TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            impact_level TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "project_controls": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS project_controls (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL REFERENCES compliance_controls(id),
            implementation_status TEXT DEFAULT 'planned',
            implementation_description TEXT,
            evidence_path TEXT,
            last_assessed TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, control_id)
        );"""),

    "ssp_documents": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ssp_documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version TEXT NOT NULL,
            system_name TEXT NOT NULL,
            content TEXT NOT NULL,
            file_path TEXT,
            classification TEXT DEFAULT 'CUI',
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "poam_items": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            weakness_id TEXT NOT NULL,
            weakness_description TEXT NOT NULL,
            severity TEXT NOT NULL,
            control_id TEXT REFERENCES compliance_controls(id),
            status TEXT DEFAULT 'open',
            corrective_action TEXT,
            milestone_date DATE,
            responsible_party TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "stig_findings": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS stig_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            stig_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Open',
            assessed_by TEXT,
            assessed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "sbom_records": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sbom_records (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version TEXT NOT NULL,
            format TEXT DEFAULT 'cyclonedx',
            file_path TEXT NOT NULL,
            component_count INTEGER,
            vulnerability_count INTEGER,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "fedramp_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS fedramp_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            baseline TEXT NOT NULL,
            control_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, baseline, control_id)
        );"""),

    "cmmc_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cmmc_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            practice_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, practice_id)
        );"""),

    "oscal_artifacts": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS oscal_artifacts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            oscal_version TEXT DEFAULT '1.1.2',
            format TEXT DEFAULT 'json',
            file_path TEXT NOT NULL,
            file_hash TEXT,
            schema_valid INTEGER DEFAULT 0,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            classification TEXT DEFAULT 'CUI',
            UNIQUE(project_id, artifact_type, format)
        );"""),

    "cato_evidence": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cato_evidence (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            evidence_path TEXT,
            evidence_hash TEXT,
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_fresh INTEGER DEFAULT 1,
            status TEXT DEFAULT 'current',
            UNIQUE(project_id, control_id, evidence_type, evidence_source)
        );"""),

    "cssp_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS cssp_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            functional_area TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),

    "ivv_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS ivv_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            process_area TEXT NOT NULL,
            verification_type TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),

    "sbd_assessments": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sbd_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence_description TEXT,
            evidence_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, requirement_id)
        );"""),

    "control_crosswalk": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS control_crosswalk (
            id TEXT PRIMARY KEY,
            nist_800_53_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            framework_control_id TEXT NOT NULL,
            mapping_type TEXT DEFAULT 'equivalent',
            notes TEXT,
            UNIQUE(nist_800_53_id, framework_id)
        );"""),

    "pi_compliance_tracking": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS pi_compliance_tracking (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            pi_number TEXT NOT NULL,
            pi_start_date TEXT,
            pi_end_date TEXT,
            compliance_score_start REAL,
            compliance_score_end REAL,
            controls_implemented INTEGER DEFAULT 0,
            controls_remaining INTEGER DEFAULT 0,
            poam_items_closed INTEGER DEFAULT 0,
            poam_items_opened INTEGER DEFAULT 0,
            findings_remediated INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, pi_number)
        );"""),
}


MBSE_TABLES: Dict[str, str] = {
    "sysml_elements": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sysml_elements (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            xmi_id TEXT NOT NULL,
            element_type TEXT NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            parent_id TEXT REFERENCES sysml_elements(id),
            stereotype TEXT,
            description TEXT,
            properties TEXT,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, xmi_id)
        );"""),

    "sysml_relationships": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS sysml_relationships (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            target_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            relationship_type TEXT NOT NULL,
            name TEXT,
            properties TEXT,
            source_file TEXT,
            UNIQUE(project_id, source_element_id, target_element_id, relationship_type)
        );"""),

    "doors_requirements": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS doors_requirements (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            doors_id TEXT NOT NULL,
            module_name TEXT,
            requirement_type TEXT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT,
            status TEXT DEFAULT 'active',
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, doors_id)
        );"""),

    "digital_thread_links": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS digital_thread_links (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            evidence TEXT,
            created_by TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, source_type, source_id, target_type, target_id, link_type)
        );"""),

    "model_imports": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_imports (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            import_type TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            elements_imported INTEGER DEFAULT 0,
            relationships_imported INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            error_details TEXT,
            status TEXT DEFAULT 'completed',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""),

    "model_snapshots": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            pi_number TEXT,
            snapshot_type TEXT NOT NULL,
            element_count INTEGER DEFAULT 0,
            relationship_count INTEGER DEFAULT 0,
            requirement_count INTEGER DEFAULT 0,
            thread_link_count INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            snapshot_data TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, pi_number, snapshot_type)
        );"""),

    "model_code_mappings": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS model_code_mappings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sysml_element_id TEXT NOT NULL REFERENCES sysml_elements(id),
            code_path TEXT NOT NULL,
            code_type TEXT NOT NULL,
            mapping_direction TEXT DEFAULT 'model_to_code',
            sync_status TEXT DEFAULT 'synced',
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model_hash TEXT,
            code_hash TEXT,
            UNIQUE(project_id, sysml_element_id, code_path)
        );"""),

    "des_compliance": textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS des_compliance (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            requirement_title TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence TEXT,
            automation_result TEXT,
            assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            UNIQUE(project_id, requirement_id)
        );"""),
}


# ============================================================
# CAPABILITY → TABLE GROUP MAPPING
# ============================================================

CAPABILITY_TABLE_MAP: Dict[str, Dict[str, str]] = {
    "compliance": COMPLIANCE_TABLES,
    "mbse": MBSE_TABLES,
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _sanitize_name(name: str) -> str:
    """Sanitize app name for use as a Python identifier and filename."""
    return re.sub(r'[^a-z0-9_]', '_', name.lower().replace('-', '_')).strip('_')


def _build_sql_block(tables: Dict[str, str], block_comment: str) -> str:
    """Join table DDL statements into a single SQL string with a section comment."""
    lines = [f"-- {'=' * 60}", f"-- {block_comment}", f"-- {'=' * 60}"]
    for _table_name, ddl in tables.items():
        lines.append(ddl)
        lines.append("")
    return "\n".join(lines)


def _indent(text: str, prefix: str = "    ") -> str:
    """Indent every line of *text* by *prefix*."""
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())


# ============================================================
# MAIN GENERATOR
# ============================================================

def generate_init_script(blueprint: Dict[str, Any]) -> str:
    """Generate a complete, standalone Python init script for a child app.

    Args:
        blueprint: Blueprint dict produced by app_blueprint.py.  Expected keys:
            - app_name (str)
            - classification (str, e.g. 'CUI')
            - capabilities (dict[str, bool])

    Returns:
        The full Python source code of the generated init script.
    """
    app_name: str = blueprint.get("app_name", "child_app")
    classification: str = blueprint.get("classification", "CUI")
    capabilities: Dict[str, bool] = blueprint.get("capabilities", {})
    safe_name = _sanitize_name(app_name)

    # --- Determine which capability SQL blocks to include -----------------
    enabled_caps: List[str] = sorted(
        cap for cap, enabled in capabilities.items()
        if enabled and cap in CAPABILITY_TABLE_MAP
    )

    # --- Build the SQL constant strings that will live in the generated file
    core_sql = _build_sql_block(CORE_TABLES, "CORE TABLES")

    capability_sql_constants: List[str] = []  # Python source fragments
    capability_init_calls: List[str] = []      # Lines inside init_db()
    migrate_cases: List[str] = []              # Cases for migrate_add_capability()

    for cap_name in CAPABILITY_TABLE_MAP:
        var_name = f"{cap_name.upper()}_SQL"
        sql_block = _build_sql_block(CAPABILITY_TABLE_MAP[cap_name], f"{cap_name.upper()} TABLES")
        # Always emit the constant so migrate_add_capability can reference it
        capability_sql_constants.append(
            f'{var_name} = """\n{sql_block}\n"""'
        )
        migrate_cases.append(
            f'    "{cap_name}": {var_name},'
        )
        # Only call it in init_db if this capability is currently enabled
        if cap_name in enabled_caps:
            capability_init_calls.append(
                f'    conn.executescript({var_name})'
            )

    capability_constants_src = "\n\n".join(capability_sql_constants)
    "\n".join(capability_init_calls) if capability_init_calls else "    pass  # No optional capabilities enabled at init time"
    migrate_map_src = "\n".join(migrate_cases) if migrate_cases else '    # No optional table groups defined'

    # --- Enabled capabilities comment for the header ----------------------
    caps_comment = ", ".join(enabled_caps) if enabled_caps else "none"

    # --- Classification banner --------------------------------------------
    if classification == "SECRET":
        cui_banner = (
            "# SECRET // NOFORN\n"
            "# Classified by: Department of Defense\n"
            "# Reason: 1.4(c)\n"
            "# Declassify on: 25X1"
        )
    else:
        cui_banner = (
            f"# {classification} // SP-CTI\n"
            "# Controlled by: Department of Defense\n"
            "# CUI Category: CTI\n"
            "# Distribution: D\n"
            "# POC: System Administrator"
        )

    # --- Assemble the generated script ------------------------------------
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    cap_names_literal = repr(list(CAPABILITY_TABLE_MAP.keys()))

    parts: List[str] = []
    parts.append("#!/usr/bin/env python3")
    parts.append(cui_banner)
    parts.append(f'"""Initialize the {app_name} database.')
    parts.append("")
    parts.append(f"Auto-generated by ICDEV db_init_generator on {generated_at}.")
    parts.append("Decision D27: Minimal DB + migration -- core tables first, expand as capabilities activate.")
    parts.append("")
    parts.append(f"Enabled capabilities at generation time: {caps_comment}")
    parts.append("")
    parts.append("Usage:")
    parts.append(f'    python init_{safe_name}_db.py [--db-path DATA/{safe_name}.db] [--reset]')
    parts.append('"""')
    parts.append("")
    parts.append("import argparse")
    parts.append("import sqlite3")
    parts.append("import sys")
    parts.append("from pathlib import Path")
    parts.append("")
    parts.append(f'DB_PATH = Path(__file__).resolve().parent / "data" / "{safe_name}.db"')
    parts.append("")
    parts.append("")
    parts.append("# " + "=" * 60)
    parts.append("# CORE SQL -- always created")
    parts.append("# " + "=" * 60)
    parts.append(f'CORE_SQL = """\n{core_sql}\n"""')
    parts.append("")
    parts.append("")
    parts.append("# " + "=" * 60)
    parts.append("# OPTIONAL CAPABILITY SQL BLOCKS")
    parts.append("# " + "=" * 60)
    parts.append(capability_constants_src)
    parts.append("")
    parts.append("")
    parts.append("# Mapping from capability name to SQL constant")
    parts.append("_CAPABILITY_SQL_MAP = {")
    parts.append(migrate_map_src)
    parts.append("}")
    parts.append("")
    parts.append("")

    # init_db function
    parts.append("def init_db(db_path=None):")
    parts.append(f'    """Initialize the {app_name} database with core + enabled capability tables."""')
    parts.append("    path = Path(db_path) if db_path else DB_PATH")
    parts.append("    path.parent.mkdir(parents=True, exist_ok=True)")
    parts.append("")
    parts.append("    conn = sqlite3.connect(str(path))")
    parts.append("    try:")
    parts.append("        # Core tables -- always present")
    parts.append("        conn.executescript(CORE_SQL)")
    parts.append("")
    parts.append("        # Capability tables enabled at generation time")
    if capability_init_calls:
        for call_line in capability_init_calls:
            parts.append(f"        {call_line.strip()}")
    else:
        parts.append("        pass  # No optional capabilities enabled at init time")
    parts.append("")
    parts.append("        conn.commit()")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append("    # Verify")
    parts.append("    conn = sqlite3.connect(str(path))")
    parts.append("    try:")
    parts.append("        cur = conn.cursor()")
    parts.append('        cur.execute("SELECT name FROM sqlite_master WHERE type=\'table\' ORDER BY name")')
    parts.append("        tables = [row[0] for row in cur.fetchall()]")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append('    print(f"Database initialized at {path}")')
    parts.append('    print(f"Tables created ({len(tables)}): {\', \'.join(tables)}")')
    parts.append("    return tables")
    parts.append("")
    parts.append("")

    # migrate_add_capability function
    parts.append("def migrate_add_capability(db_path, capability_name):")
    parts.append('    """Add tables for a capability that was not enabled at init time.')
    parts.append("")
    parts.append("    Args:")
    parts.append("        db_path: Path to the SQLite database file.")
    parts.append(f"        capability_name: One of {cap_names_literal}.")
    parts.append("")
    parts.append("    Raises:")
    parts.append("        ValueError: If capability_name is not recognized.")
    parts.append('    """')
    parts.append("    if capability_name not in _CAPABILITY_SQL_MAP:")
    parts.append("        raise ValueError(")
    parts.append('            f"Unknown capability \'{capability_name}\'. "')
    parts.append('            f"Valid options: {list(_CAPABILITY_SQL_MAP.keys())}"')
    parts.append("        )")
    parts.append("")
    parts.append("    path = Path(db_path)")
    parts.append("    if not path.exists():")
    parts.append('        raise FileNotFoundError(f"Database not found: {path}")')
    parts.append("")
    parts.append("    sql = _CAPABILITY_SQL_MAP[capability_name]")
    parts.append("    conn = sqlite3.connect(str(path))")
    parts.append("    try:")
    parts.append("        conn.executescript(sql)")
    parts.append("        conn.commit()")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append("    # Verify new tables")
    parts.append("    conn = sqlite3.connect(str(path))")
    parts.append("    try:")
    parts.append("        cur = conn.cursor()")
    parts.append('        cur.execute("SELECT name FROM sqlite_master WHERE type=\'table\' ORDER BY name")')
    parts.append("        tables = [row[0] for row in cur.fetchall()]")
    parts.append("    finally:")
    parts.append("        conn.close()")
    parts.append("")
    parts.append('    print(f"Capability \'{capability_name}\' tables added to {path}")')
    parts.append('    print(f"Total tables ({len(tables)}): {\', \'.join(tables)}")')
    parts.append("    return tables")
    parts.append("")
    parts.append("")

    # main function
    parts.append("def main():")
    parts.append('    """CLI entry point."""')
    parts.append("    parser = argparse.ArgumentParser(")
    parts.append(f'        description="Initialize the {app_name} database"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--db-path", type=Path, default=DB_PATH,')
    parts.append('        help="Database file path (default: %(default)s)"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--reset", action="store_true",')
    parts.append('        help="Drop and recreate all tables"')
    parts.append("    )")
    parts.append("    parser.add_argument(")
    parts.append('        "--add-capability", type=str, default=None,')
    parts.append("        help=\"Add tables for a capability post-init (e.g. 'compliance', 'mbse')\"")
    parts.append("    )")
    parts.append("    args = parser.parse_args()")
    parts.append("")
    parts.append("    if args.add_capability:")
    parts.append("        migrate_add_capability(args.db_path, args.add_capability)")
    parts.append("        return")
    parts.append("")
    parts.append("    if args.reset and args.db_path.exists():")
    parts.append("        args.db_path.unlink()")
    parts.append('        print(f"Removed existing database: {args.db_path}")')
    parts.append("")
    parts.append("    init_db(args.db_path)")
    parts.append("")
    parts.append("")
    parts.append('if __name__ == "__main__":')
    parts.append("    main()")
    parts.append("")

    script = "\n".join(parts)

    return script


def write_init_script(blueprint: Dict[str, Any], output_dir: Path) -> Path:
    """Generate the init script and write it to *output_dir*.

    Args:
        blueprint: Blueprint dict from app_blueprint.py.
        output_dir: Directory where the generated script will be placed.

    Returns:
        Path to the written file.
    """
    app_name: str = blueprint.get("app_name", "child_app")
    safe_name = _sanitize_name(app_name)
    filename = f"init_{safe_name}_db.py"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    source = generate_init_script(blueprint)
    output_path.write_text(source, encoding="utf-8")

    logger.info("Wrote init script: %s (%d bytes)", output_path, len(source))

    # Audit trail
    audit_log_event(
        event_type="code_generated",
        actor="icdev-db-init-generator",
        action=f"Generated DB init script for {app_name}",
        details=json.dumps({
            "app_name": app_name,
            "output_path": str(output_path),
            "capabilities": {
                k: v for k, v in blueprint.get("capabilities", {}).items() if v
            },
            "classification": blueprint.get("classification", "CUI"),
        }),
        project_id=blueprint.get("blueprint_id", "unknown"),
    )

    return output_path


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    """CLI entry point for the DB init generator."""
    parser = argparse.ArgumentParser(
        description="Generate a standalone database init script for a child app"
    )
    parser.add_argument(
        "--blueprint", required=True, type=Path,
        help="Path to blueprint JSON file (from app_blueprint.py)"
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write the generated init script"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output result as JSON"
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load blueprint
    if not args.blueprint.exists():
        logger.error("Blueprint file not found: %s", args.blueprint)
        sys.exit(1)

    try:
        blueprint = json.loads(args.blueprint.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load blueprint: %s", exc)
        sys.exit(1)

    # Generate and write
    output_path = write_init_script(blueprint, args.output_dir)

    # Determine enabled capabilities for summary
    capabilities = blueprint.get("capabilities", {})
    enabled = sorted(k for k, v in capabilities.items() if v and k in CAPABILITY_TABLE_MAP)
    core_count = len(CORE_TABLES)
    cap_count = sum(len(CAPABILITY_TABLE_MAP[c]) for c in enabled)
    total_tables = core_count + cap_count

    result = {
        "status": "success",
        "output_path": str(output_path),
        "app_name": blueprint.get("app_name", "child_app"),
        "classification": blueprint.get("classification", "CUI"),
        "core_tables": core_count,
        "capability_tables": cap_count,
        "total_tables": total_tables,
        "enabled_capabilities": enabled,
        "available_migrations": sorted(CAPABILITY_TABLE_MAP.keys()),
    }

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated: {output_path}")
        print(f"  App:            {result['app_name']}")
        print(f"  Classification: {result['classification']}")
        print(f"  Core tables:    {core_count}")
        print(f"  Cap tables:     {cap_count} ({', '.join(enabled) if enabled else 'none'})")
        print(f"  Total tables:   {total_tables}")
        print(f"  Migrations:     {', '.join(sorted(CAPABILITY_TABLE_MAP.keys()))}")


if __name__ == "__main__":
    main()
