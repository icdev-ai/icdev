# CUI // SP-CTI
#!/usr/bin/env python3
"""DoDI 5000.87 Digital Engineering Strategy (DES) compliance assessor.

Loads DES requirements from context/mbse/des_requirements.json, performs automated
checks against MBSE database tables (sysml_elements, doors_requirements,
digital_thread_links, model_code_mappings, model_snapshots, model_imports),
stores results in des_compliance table, evaluates DES gates, applies CUI markings,
and logs audit events.

Categories assessed:
  model_authority  -- DSM as authoritative source of truth
  data_management  -- Data standards, exchange formats, repositories
  infrastructure   -- DE environment, tools, platforms
  workforce        -- Training, competency, organizational adoption
  policy           -- Governance, standards compliance, IP management
  lifecycle        -- Integration across acquisition lifecycle phases
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
DES_REQUIREMENTS_PATH = BASE_DIR / "context" / "mbse" / "des_requirements.json"

# Try to import audit logger
try:
    sys.path.insert(0, str(BASE_DIR / "tools" / "audit"))
    from audit_logger import log_event as _audit_log_event
except ImportError:
    _audit_log_event = None


# -----------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn):
    """Create des_compliance table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS des_compliance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            requirement_title TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN (
                'model_authority','data_management','infrastructure',
                'workforce','policy','lifecycle'
            )),
            status TEXT DEFAULT 'not_assessed' CHECK(status IN (
                'not_assessed','compliant','partially_compliant',
                'non_compliant','not_applicable'
            )),
            evidence TEXT,
            automation_result TEXT,
            assessed_at TEXT DEFAULT (datetime('now')),
            notes TEXT,
            UNIQUE(project_id, requirement_id)
        )
    """)
    conn.commit()


def _get_project(conn, project_id):
    """Load project data from the projects table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


# -----------------------------------------------------------------
# Configuration helpers
# -----------------------------------------------------------------

def load_des_requirements(catalog_path=None):
    """Load DES requirements from context/mbse/des_requirements.json.

    Args:
        catalog_path: Override path to the DES requirements JSON catalog.

    Returns:
        list of requirement dicts from the catalog.
    """
    path = Path(catalog_path) if catalog_path else DES_REQUIREMENTS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"DES requirements file not found: {path}\n"
            "Expected: context/mbse/des_requirements.json"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("requirements", [])


def _load_cui_config():
    """Load CUI marking configuration."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CUI // SP-CTI | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event (append-only, NIST AU compliant)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "des_assessed",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)] if file_path else []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# -----------------------------------------------------------------
# Auto-check functions
# Each returns a dict:
#   {"status": "compliant"|"partially_compliant"|"non_compliant",
#    "evidence": "description of what was found",
#    "details": "specifics"}
# -----------------------------------------------------------------

def _check_model_authority(project_id, project_dir, conn):
    """DES-1.x: DSM exists and is current.

    Check sysml_elements count > 0 and last import within 90 days.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sysml_elements WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        element_count = row["cnt"] if row else 0
    except Exception:
        element_count = 0

    if element_count == 0:
        return {
            "status": "non_compliant",
            "evidence": "No SysML elements found in sysml_elements table.",
            "details": (
                "The Digital System Model (DSM) does not exist or has no "
                "elements registered. Import a SysML model to establish "
                "the authoritative source of truth."
            ),
        }

    # Check last import within 90 days
    try:
        import_row = conn.execute(
            """SELECT MAX(imported_at) as last_import
               FROM model_imports WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        last_import_str = import_row["last_import"] if import_row else None
    except Exception:
        last_import_str = None

    if last_import_str:
        try:
            last_import = datetime.fromisoformat(last_import_str.replace("Z", "+00:00").replace("+00:00", ""))
        except (ValueError, AttributeError):
            last_import = None
    else:
        last_import = None

    now = datetime.utcnow()
    if last_import and (now - last_import).days <= 90:
        return {
            "status": "compliant",
            "evidence": (
                f"DSM contains {element_count} element(s). Last import "
                f"{last_import.strftime('%Y-%m-%d')} ({(now - last_import).days}d ago)."
            ),
            "details": "Model is current (imported within 90 days).",
        }
    elif last_import:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"DSM contains {element_count} element(s) but last import "
                f"was {last_import.strftime('%Y-%m-%d')} "
                f"({(now - last_import).days}d ago, exceeds 90-day threshold)."
            ),
            "details": "Re-import model data to restore currency.",
        }

    return {
        "status": "partially_compliant",
        "evidence": (
            f"DSM contains {element_count} element(s) but no import "
            "records found in model_imports table."
        ),
        "details": "Cannot verify model currency without import records.",
    }


def _check_model_completeness(project_id, project_dir, conn):
    """DES-1.x: All major system elements modeled.

    Check element types coverage -- blocks, activities, and requirements
    must all be present in sysml_elements.
    """
    required_types = {"Block", "Activity", "Requirement"}
    try:
        rows = conn.execute(
            """SELECT DISTINCT element_type FROM sysml_elements
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
        found_types = {r["element_type"] for r in rows}
    except Exception:
        found_types = set()

    if not found_types:
        return {
            "status": "non_compliant",
            "evidence": "No element types found in sysml_elements table.",
            "details": (
                "Required element types: Block, Activity, Requirement. "
                "None found."
            ),
        }

    # Normalize type names for comparison (case-insensitive)
    found_lower = {t.lower() for t in found_types}
    required_lower = {t.lower() for t in required_types}
    matched = required_lower & found_lower
    missing = required_lower - found_lower

    if len(matched) == len(required_lower):
        return {
            "status": "compliant",
            "evidence": (
                f"All required element types present: "
                f"{', '.join(sorted(found_types))}."
            ),
            "details": (
                f"Found {len(found_types)} distinct element type(s) "
                f"including all required types (Block, Activity, Requirement)."
            ),
        }
    elif matched:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"Found element types: {', '.join(sorted(found_types))}. "
                f"Missing required: {', '.join(sorted(missing))}."
            ),
            "details": (
                "Partial model completeness. Add missing element types "
                "to achieve full coverage."
            ),
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"Found element types: {', '.join(sorted(found_types))}. "
            f"None of the required types (Block, Activity, Requirement) present."
        ),
        "details": "Model lacks fundamental structural, behavioral, and requirement elements.",
    }


def _check_digital_thread(project_id, project_dir, conn):
    """DES-2.x: End-to-end traceability exists.

    Check digital_thread_links coverage >= 60%.
    """
    try:
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sysml_elements WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        total_elements = total_row["cnt"] if total_row else 0
    except Exception:
        total_elements = 0

    try:
        linked_row = conn.execute(
            """SELECT COUNT(DISTINCT source_id) as cnt
               FROM digital_thread_links WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        linked_elements = linked_row["cnt"] if linked_row else 0
    except Exception:
        linked_elements = 0

    if total_elements == 0:
        return {
            "status": "non_compliant",
            "evidence": "No SysML elements found; cannot compute thread coverage.",
            "details": "Import model elements first, then establish digital thread links.",
        }

    coverage = (linked_elements / total_elements) * 100 if total_elements > 0 else 0.0

    if coverage >= 60.0:
        return {
            "status": "compliant",
            "evidence": (
                f"Digital thread coverage: {coverage:.1f}% "
                f"({linked_elements}/{total_elements} elements linked)."
            ),
            "details": "Meets 60% minimum traceability threshold.",
        }
    elif coverage > 0:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"Digital thread coverage: {coverage:.1f}% "
                f"({linked_elements}/{total_elements} elements linked). "
                "Below 60% threshold."
            ),
            "details": "Add traceability links to reach 60% coverage.",
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"No digital thread links found for {total_elements} element(s)."
        ),
        "details": "No traceability established. Create digital_thread_links entries.",
    }


def _check_model_currency(project_id, project_dir, conn):
    """DES-2.x: Model updated within current PI.

    Check model_imports last date is within the past 42 days (approx 1 PI).
    """
    try:
        row = conn.execute(
            """SELECT MAX(imported_at) as last_import
               FROM model_imports WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        last_import_str = row["last_import"] if row else None
    except Exception:
        last_import_str = None

    if not last_import_str:
        return {
            "status": "non_compliant",
            "evidence": "No model import records found.",
            "details": "Cannot verify model currency. Import model data.",
        }

    try:
        last_import = datetime.fromisoformat(
            last_import_str.replace("Z", "+00:00").replace("+00:00", "")
        )
    except (ValueError, AttributeError):
        return {
            "status": "non_compliant",
            "evidence": f"Invalid import date format: {last_import_str}.",
            "details": "Cannot parse last import timestamp.",
        }

    now = datetime.utcnow()
    days_since = (now - last_import).days

    if days_since <= 42:
        return {
            "status": "compliant",
            "evidence": (
                f"Last model import: {last_import.strftime('%Y-%m-%d')} "
                f"({days_since}d ago). Within current PI window (42 days)."
            ),
            "details": "Model is current for this Program Increment.",
        }
    elif days_since <= 90:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"Last model import: {last_import.strftime('%Y-%m-%d')} "
                f"({days_since}d ago). Exceeds PI window but within 90 days."
            ),
            "details": "Model may be stale; re-import to align with current PI.",
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"Last model import: {last_import.strftime('%Y-%m-%d')} "
            f"({days_since}d ago). Exceeds 90-day threshold."
        ),
        "details": "Model is stale. Immediate re-import required.",
    }


def _check_data_management(project_id, project_dir, conn):
    """DES-3.x: Model artifacts stored and versioned.

    Check source files exist in project_dir and model_snapshots are recorded.
    """
    # Check for model source files on disk
    source_found = False
    source_files = []
    if project_dir:
        project_path = Path(project_dir)
        model_patterns = ["*.sysml", "*.xmi", "*.reqif", "*.mdzip", "*.mdxml"]
        for pattern in model_patterns:
            matches = list(project_path.rglob(pattern))
            source_files.extend(matches)
        source_found = len(source_files) > 0

    # Check model_snapshots table
    try:
        snap_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM model_snapshots WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        snapshot_count = snap_row["cnt"] if snap_row else 0
    except Exception:
        snapshot_count = 0

    if source_found and snapshot_count > 0:
        return {
            "status": "compliant",
            "evidence": (
                f"Model source files found ({len(source_files)} file(s)) "
                f"and {snapshot_count} snapshot(s) recorded."
            ),
            "details": (
                "Files: "
                + "; ".join(f.name for f in source_files[:5])
                + f". Snapshots: {snapshot_count}."
            ),
        }
    elif source_found or snapshot_count > 0:
        parts = []
        if source_found:
            parts.append(f"{len(source_files)} source file(s)")
        if snapshot_count > 0:
            parts.append(f"{snapshot_count} snapshot(s)")
        missing = []
        if not source_found:
            missing.append("model source files on disk")
        if snapshot_count == 0:
            missing.append("model snapshots in database")
        return {
            "status": "partially_compliant",
            "evidence": (
                f"Partial data management: found {', '.join(parts)}. "
                f"Missing: {', '.join(missing)}."
            ),
            "details": "Both source files and versioned snapshots are required.",
        }

    return {
        "status": "non_compliant",
        "evidence": "No model source files found and no snapshots recorded.",
        "details": (
            "Expected: .sysml, .xmi, .reqif, .mdzip, or .mdxml files in "
            "project directory AND model_snapshots entries in database."
        ),
    }


def _check_model_code_sync(project_id, project_dir, conn):
    """DES-3.x: Model and code in sync.

    Check model_code_mappings sync_status for the project.
    """
    try:
        rows = conn.execute(
            """SELECT sync_status, COUNT(*) as cnt
               FROM model_code_mappings WHERE project_id = ?
               GROUP BY sync_status""",
            (project_id,),
        ).fetchall()
        status_counts = {r["sync_status"]: r["cnt"] for r in rows}
    except Exception:
        status_counts = {}

    total = sum(status_counts.values())
    if total == 0:
        return {
            "status": "non_compliant",
            "evidence": "No model-code mappings found in model_code_mappings table.",
            "details": "Establish model-to-code mappings to enable sync tracking.",
        }

    synced = status_counts.get("synced", 0) + status_counts.get("in_sync", 0)
    out_of_sync = status_counts.get("out_of_sync", 0) + status_counts.get("stale", 0)
    unknown = total - synced - out_of_sync

    sync_ratio = synced / total if total > 0 else 0.0

    if sync_ratio >= 0.8:
        return {
            "status": "compliant",
            "evidence": (
                f"Model-code sync: {synced}/{total} mappings synced "
                f"({sync_ratio:.0%}). Out-of-sync: {out_of_sync}."
            ),
            "details": (
                "Sync statuses: "
                + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
            ),
        }
    elif sync_ratio >= 0.5:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"Model-code sync: {synced}/{total} mappings synced "
                f"({sync_ratio:.0%}). Out-of-sync: {out_of_sync}."
            ),
            "details": "Between 50-80% sync. Target >= 80% for full compliance.",
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"Model-code sync: {synced}/{total} mappings synced "
            f"({sync_ratio:.0%}). Out-of-sync: {out_of_sync}."
        ),
        "details": "Below 50% sync. Significant model-code divergence detected.",
    }


def _check_requirements_linked(project_id, project_dir, conn):
    """DES-4.x: All DOORS requirements linked to model elements.

    Check digital_thread_links for requirement-type links.
    """
    try:
        req_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM doors_requirements WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        req_count = req_row["cnt"] if req_row else 0
    except Exception:
        req_count = 0

    if req_count == 0:
        return {
            "status": "non_compliant",
            "evidence": "No DOORS requirements found in doors_requirements table.",
            "details": "Import requirements from DOORS NG to enable traceability.",
        }

    try:
        linked_row = conn.execute(
            """SELECT COUNT(DISTINCT source_id) as cnt
               FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'requirement'""",
            (project_id,),
        ).fetchone()
        linked_count = linked_row["cnt"] if linked_row else 0
    except Exception:
        linked_count = 0

    link_ratio = linked_count / req_count if req_count > 0 else 0.0

    if link_ratio >= 0.8:
        return {
            "status": "compliant",
            "evidence": (
                f"{linked_count}/{req_count} requirements linked to model "
                f"elements ({link_ratio:.0%})."
            ),
            "details": "Meets 80% linkage threshold.",
        }
    elif link_ratio > 0:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"{linked_count}/{req_count} requirements linked "
                f"({link_ratio:.0%}). Below 80% threshold."
            ),
            "details": "Add traceability links for unlinked requirements.",
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"{req_count} requirements found but none linked to model elements."
        ),
        "details": "No requirement-to-model links in digital_thread_links.",
    }


def _check_model_based_testing(project_id, project_dir, conn):
    """DES-4.x: Tests generated from or linked to model.

    Check code->test thread links in digital_thread_links.
    """
    try:
        test_row = conn.execute(
            """SELECT COUNT(*) as cnt
               FROM digital_thread_links
               WHERE project_id = ?
                 AND (target_type = 'test' OR source_type = 'test'
                      OR link_type = 'verifies' OR link_type = 'verify')""",
            (project_id,),
        ).fetchone()
        test_links = test_row["cnt"] if test_row else 0
    except Exception:
        test_links = 0

    if test_links == 0:
        return {
            "status": "non_compliant",
            "evidence": "No model-to-test traceability links found.",
            "details": (
                "No 'verifies' or test-type links in digital_thread_links. "
                "Link test cases to model requirements and design elements."
            ),
        }

    try:
        total_row = conn.execute(
            """SELECT COUNT(DISTINCT source_id) as cnt
               FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'requirement'""",
            (project_id,),
        ).fetchone()
        total_reqs_linked = total_row["cnt"] if total_row else 0
    except Exception:
        total_reqs_linked = 0

    if test_links >= 5 or (total_reqs_linked > 0 and test_links >= total_reqs_linked):
        return {
            "status": "compliant",
            "evidence": (
                f"{test_links} model-to-test traceability link(s) found."
            ),
            "details": "Model-based testing traceability is established.",
        }

    return {
        "status": "partially_compliant",
        "evidence": (
            f"{test_links} model-to-test link(s) found. "
            "Additional links recommended for full coverage."
        ),
        "details": "Expand verify/test links to cover all requirements.",
    }


def _check_model_compliance_mapping(project_id, project_dir, conn):
    """DES-5.x: Model elements mapped to NIST controls.

    Check model->control thread links in digital_thread_links.
    """
    try:
        ctrl_row = conn.execute(
            """SELECT COUNT(*) as cnt
               FROM digital_thread_links
               WHERE project_id = ?
                 AND (target_type = 'control' OR source_type = 'control'
                      OR link_type = 'implements_control'
                      OR link_type = 'satisfies_control')""",
            (project_id,),
        ).fetchone()
        control_links = ctrl_row["cnt"] if ctrl_row else 0
    except Exception:
        control_links = 0

    if control_links == 0:
        return {
            "status": "non_compliant",
            "evidence": "No model-to-NIST-control traceability links found.",
            "details": (
                "No control-type links in digital_thread_links. "
                "Map model elements to NIST 800-53 controls for compliance traceability."
            ),
        }

    if control_links >= 5:
        return {
            "status": "compliant",
            "evidence": (
                f"{control_links} model-to-control traceability link(s) found."
            ),
            "details": "NIST control mapping is established in the digital thread.",
        }

    return {
        "status": "partially_compliant",
        "evidence": (
            f"Only {control_links} model-to-control link(s) found. "
            "Additional mappings recommended."
        ),
        "details": "Expand control mappings to cover critical NIST families.",
    }


def _check_pi_snapshots(project_id, project_dir, conn):
    """DES-6.x: Model snapshots exist for current PI.

    Check model_snapshots table for recent entries (within 42 days).
    """
    try:
        rows = conn.execute(
            """SELECT snapshot_date FROM model_snapshots
               WHERE project_id = ?
               ORDER BY snapshot_date DESC""",
            (project_id,),
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return {
            "status": "non_compliant",
            "evidence": "No model snapshots found in model_snapshots table.",
            "details": "Create PI baseline snapshots to establish version history.",
        }

    # Check if most recent snapshot is within current PI (42 days)
    try:
        latest_str = rows[0]["snapshot_date"]
        latest = datetime.fromisoformat(
            latest_str.replace("Z", "+00:00").replace("+00:00", "")
        )
    except (ValueError, AttributeError, TypeError):
        return {
            "status": "partially_compliant",
            "evidence": (
                f"{len(rows)} snapshot(s) found but cannot parse latest date."
            ),
            "details": "Verify snapshot date format in model_snapshots table.",
        }

    now = datetime.utcnow()
    days_since = (now - latest).days

    if days_since <= 42:
        return {
            "status": "compliant",
            "evidence": (
                f"{len(rows)} snapshot(s) total. Latest: "
                f"{latest.strftime('%Y-%m-%d')} ({days_since}d ago). "
                "Within current PI."
            ),
            "details": "PI baseline snapshot is current.",
        }
    elif days_since <= 90:
        return {
            "status": "partially_compliant",
            "evidence": (
                f"{len(rows)} snapshot(s) total. Latest: "
                f"{latest.strftime('%Y-%m-%d')} ({days_since}d ago). "
                "Exceeds PI window."
            ),
            "details": "Create a new snapshot for the current PI.",
        }

    return {
        "status": "non_compliant",
        "evidence": (
            f"{len(rows)} snapshot(s) total. Latest: "
            f"{latest.strftime('%Y-%m-%d')} ({days_since}d ago). "
            "Severely outdated."
        ),
        "details": "Snapshots are stale. Create new PI baseline immediately.",
    }


# -----------------------------------------------------------------
# Auto-check dispatch table
# -----------------------------------------------------------------

AUTO_CHECKS = {
    "model_authority": [_check_model_authority, _check_model_completeness],
    "data_management": [_check_data_management, _check_model_code_sync],
    "infrastructure": [],  # Manual checks
    "workforce": [],  # Manual checks
    "policy": [_check_requirements_linked, _check_model_compliance_mapping],
    "lifecycle": [
        _check_digital_thread,
        _check_model_currency,
        _check_model_based_testing,
        _check_pi_snapshots,
    ],
}

# Map individual requirement IDs to specific check functions
_REQ_CHECK_MAP = {
    "DES-1.1": _check_model_authority,
    "DES-1.2": _check_model_completeness,
    "DES-2.1": _check_digital_thread,
    "DES-2.3": _check_data_management,
    "DES-2.4": _check_model_code_sync,
    "DES-5.3": _check_requirements_linked,
    "DES-5.4": _check_model_compliance_mapping,
    "DES-6.2": _check_model_based_testing,
    "DES-6.4": _check_digital_thread,
}


# -----------------------------------------------------------------
# Core assessment function
# -----------------------------------------------------------------

def run_des_assessment(project_id, project_dir, db_path=None):
    """Run full DES compliance assessment.

    Steps:
        1. Load DES requirements catalog
        2. Run auto-checks per category
        3. Store results in des_compliance table (INSERT OR REPLACE)
        4. Compute gate status (0 non_compliant on critical = PASS)
        5. Log audit trail (des_assessed)

    Args:
        project_id: The project identifier.
        project_dir: Project directory path for file-based checks.
        db_path: Override database path.

    Returns:
        dict with total, compliant, partial, non_compliant, not_applicable,
        not_assessed, gate_status, score, and detailed results.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        project = _get_project(conn, project_id)

        # 1. Load DES requirements catalog
        requirements = load_des_requirements()

        now = datetime.utcnow()
        results = []

        # 2. Assess each requirement
        for req in requirements:
            req_id = req["id"]
            category = req["category"]
            title = req["title"]
            priority = req.get("priority", "medium")
            automation_level = req.get("automation_level", "manual")

            status = "not_assessed"
            evidence = ""
            automation_result = ""
            notes = ""

            # Determine if an auto-check exists for this requirement
            check_func = _REQ_CHECK_MAP.get(req_id)

            # Also check category-level auto-check list
            if not check_func and automation_level in ("auto", "semi_auto"):
                category_checks = AUTO_CHECKS.get(category, [])
                # Use the first available check for this category if not
                # specifically mapped
                if category_checks:
                    check_func = category_checks[0]

            if check_func and automation_level in ("auto", "semi_auto"):
                try:
                    check_result = check_func(project_id, project_dir, conn)
                    status = check_result["status"]
                    evidence = check_result["evidence"]
                    automation_result = json.dumps({
                        "check_function": check_func.__name__,
                        "automation_level": automation_level,
                        "details": check_result.get("details", ""),
                    })
                    if automation_level == "semi_auto":
                        notes = (
                            "Semi-automated check completed. "
                            "Manual review recommended to verify full compliance."
                        )
                except Exception as e:
                    status = "not_assessed"
                    evidence = f"Auto-check error: {e}"
                    notes = "Auto-check failed; manual review required."
                    automation_result = json.dumps({
                        "check_function": check_func.__name__,
                        "error": str(e),
                    })
            elif automation_level == "manual":
                status = "not_assessed"
                evidence = "Manual assessment required."
                notes = (
                    "This requirement must be verified manually. "
                    "Assessment criteria: "
                    + "; ".join(req.get("assessment_criteria", ["See requirement description."]))
                )
            else:
                # Auto or semi_auto but no check function mapped
                status = "not_assessed"
                evidence = "No automated check implemented for this requirement."
                notes = "Manual review required."

            result_entry = {
                "requirement_id": req_id,
                "requirement_title": title,
                "category": category,
                "priority": priority,
                "automation_level": automation_level,
                "nist_controls": req.get("nist_controls", []),
                "des_goal": req.get("des_goal"),
                "status": status,
                "evidence": evidence,
                "automation_result": automation_result,
                "notes": notes,
            }
            results.append(result_entry)

            # 3. Store in des_compliance table (INSERT OR REPLACE)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO des_compliance
                       (project_id, requirement_id, requirement_title,
                        category, status, evidence, automation_result,
                        assessed_at, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        req_id,
                        title,
                        category,
                        status,
                        evidence,
                        automation_result,
                        now.isoformat(),
                        notes if notes else None,
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert DES assessment for {req_id}: {e}",
                    file=sys.stderr,
                )

        conn.commit()

        # 4. Compute summary counts
        total = len(results)
        compliant = sum(1 for r in results if r["status"] == "compliant")
        partial = sum(1 for r in results if r["status"] == "partially_compliant")
        non_compliant = sum(1 for r in results if r["status"] == "non_compliant")
        not_applicable = sum(1 for r in results if r["status"] == "not_applicable")
        not_assessed = sum(1 for r in results if r["status"] == "not_assessed")

        # Score: 100 * (compliant + partial * 0.5) / (total - not_applicable)
        scoreable = total - not_applicable
        if scoreable > 0:
            score = round(
                100.0 * (compliant + partial * 0.5) / scoreable, 1
            )
        else:
            score = 100.0

        # Gate logic: PASS if 0 non_compliant on critical priority requirements
        # WARN if any partially_compliant on critical. FAIL otherwise.
        critical_non_compliant = sum(
            1 for r in results
            if r["priority"] == "critical" and r["status"] == "non_compliant"
        )
        critical_partial = sum(
            1 for r in results
            if r["priority"] == "critical" and r["status"] == "partially_compliant"
        )

        if critical_non_compliant == 0 and critical_partial == 0:
            gate_status = "PASS"
        elif critical_non_compliant == 0 and critical_partial > 0:
            gate_status = "WARN"
        else:
            gate_status = "FAIL"

        # Build category summary
        category_summary = {}
        for cat in ["model_authority", "data_management", "infrastructure",
                     "workforce", "policy", "lifecycle"]:
            cat_results = [r for r in results if r["category"] == cat]
            cat_total = len(cat_results)
            cat_na = sum(1 for r in cat_results if r["status"] == "not_applicable")
            cat_scoreable = cat_total - cat_na
            cat_compliant = sum(1 for r in cat_results if r["status"] == "compliant")
            cat_partial = sum(1 for r in cat_results if r["status"] == "partially_compliant")
            cat_score = (
                round(100.0 * (cat_compliant + cat_partial * 0.5) / cat_scoreable, 1)
                if cat_scoreable > 0 else 100.0
            )
            category_summary[cat] = {
                "total": cat_total,
                "compliant": cat_compliant,
                "partially_compliant": cat_partial,
                "non_compliant": sum(1 for r in cat_results if r["status"] == "non_compliant"),
                "not_applicable": cat_na,
                "not_assessed": sum(1 for r in cat_results if r["status"] == "not_assessed"),
                "score": cat_score,
            }

        # 5. Log audit trail
        audit_details = {
            "total": total,
            "compliant": compliant,
            "partially_compliant": partial,
            "non_compliant": non_compliant,
            "not_applicable": not_applicable,
            "not_assessed": not_assessed,
            "score": score,
            "gate_status": gate_status,
            "critical_non_compliant": critical_non_compliant,
            "critical_partial": critical_partial,
            "category_summary": category_summary,
        }
        _log_audit_event(
            conn,
            project_id,
            f"DES assessment completed (score={score}%, gate={gate_status})",
            audit_details,
        )

        # Console output
        print("DES assessment completed:")
        print(f"  Project:           {project.get('name', project_id)}")
        print(f"  Requirements:      {total}")
        print(f"  Compliant:         {compliant}")
        print(f"  Partial:           {partial}")
        print(f"  Non-Compliant:     {non_compliant}")
        print(f"  Not Assessed:      {not_assessed}")
        print(f"  Not Applicable:    {not_applicable}")
        print(f"  Score:             {score}%")
        print(f"  Gate Status:       {gate_status}")
        print()
        for cat, cs in category_summary.items():
            print(
                f"  {cat}: "
                f"C={cs['compliant']} "
                f"P={cs['partially_compliant']} "
                f"NC={cs['non_compliant']} "
                f"NA={cs['not_assessed']} "
                f"Score={cs['score']}%"
            )

        return {
            "total": total,
            "compliant": compliant,
            "partial": partial,
            "non_compliant": non_compliant,
            "not_applicable": not_applicable,
            "not_assessed": not_assessed,
            "gate_status": gate_status,
            "score": score,
            "category_summary": category_summary,
            "results": results,
        }

    finally:
        conn.close()


# -----------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DoDI 5000.87 Digital Engineering Strategy Assessment"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--project-dir", required=True,
        help="Project directory for file-based checks"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Also generate DES compliance report"
    )
    parser.add_argument(
        "--output", help="Report output path"
    )
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH,
        help="Override database path"
    )
    args = parser.parse_args()

    try:
        result = run_des_assessment(
            project_id=args.project_id,
            project_dir=args.project_dir,
            db_path=args.db_path,
        )

        if args.json:
            # Exclude full results list for concise JSON output
            output = {
                "total": result["total"],
                "compliant": result["compliant"],
                "partial": result["partial"],
                "non_compliant": result["non_compliant"],
                "not_applicable": result["not_applicable"],
                "not_assessed": result["not_assessed"],
                "gate_status": result["gate_status"],
                "score": result["score"],
                "category_summary": result["category_summary"],
            }
            print(json.dumps(output, indent=2))

        if args.report:
            try:
                from des_report_generator import generate_des_report
                report_result = generate_des_report(
                    project_id=args.project_id,
                    output_path=args.output,
                    db_path=args.db_path,
                )
                print(f"\n  Report: {report_result.get('file_path', 'N/A')}")
            except ImportError:
                # Try absolute import
                try:
                    sys.path.insert(0, str(Path(__file__).resolve().parent))
                    from des_report_generator import generate_des_report
                    report_result = generate_des_report(
                        project_id=args.project_id,
                        output_path=args.output,
                        db_path=args.db_path,
                    )
                    print(f"\n  Report: {report_result.get('file_path', 'N/A')}")
                except ImportError as ie:
                    print(
                        f"Warning: Could not import des_report_generator: {ie}",
                        file=sys.stderr,
                    )

        if result["gate_status"] == "FAIL":
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
# CUI // SP-CTI
