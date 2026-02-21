#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Control Framework Crosswalk Engine for ICDEV.

Maps NIST SP 800-53 Rev 5 control implementations across multiple compliance
frameworks (FedRAMP Moderate/High, NIST 800-171, CMMC Level 2/3, DoD IL4/5/6,
CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001).

Dual-hub crosswalk model (ADR D111):
  - US Hub: NIST 800-53 Rev 5 (domestic frameworks map directly)
  - International Hub: ISO 27001:2022 (international frameworks map via bridge)
  - Bridge: iso27001_nist_bridge.json connects the two hubs bidirectionally

Enables "implement once, satisfy many" by computing per-framework coverage,
performing gap analysis, and auto-updating framework status when controls are
marked as implemented.

Usage:
    # Look up frameworks for a NIST control
    python tools/compliance/crosswalk_engine.py --control AC-2

    # List controls required by a framework
    python tools/compliance/crosswalk_engine.py --framework fedramp --baseline moderate

    # Controls for an impact level
    python tools/compliance/crosswalk_engine.py --impact-level IL4

    # Coverage report for a project
    python tools/compliance/crosswalk_engine.py --project-id proj-123 --coverage

    # Gap analysis for a target framework
    python tools/compliance/crosswalk_engine.py --project-id proj-123 --framework fedramp \\
        --baseline moderate --gap-analysis

    # Crosswalk summary stats
    python tools/compliance/crosswalk_engine.py --summary

Databases:
    - data/icdev.db: project_controls, control_crosswalk, project_framework_status
    - context/compliance/control_crosswalk.json: static crosswalk mapping data

See also:
    - tools/compliance/control_mapper.py (NIST 800-53 project mapping)
    - tools/compliance/nist_lookup.py (NIST control reference lookup)
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CROSSWALK_PATH = BASE_DIR / "context" / "compliance" / "control_crosswalk.json"
ISO_BRIDGE_PATH = BASE_DIR / "context" / "compliance" / "iso27001_nist_bridge.json"

# Module-level caches for crosswalk data
_CROSSWALK_CACHE = None
_ISO_BRIDGE_CACHE = None

# Framework key mappings for human-friendly names
# Phase 23: Extended with dual-hub frameworks (ADR D111)
FRAMEWORK_KEYS = {
    # ── US Hub: NIST 800-53 direct mappings ──
    "fedramp_moderate": "FedRAMP Moderate",
    "fedramp_high": "FedRAMP High",
    "nist_800_171": "NIST 800-171",
    "cmmc_level_2": "CMMC Level 2",
    "cmmc_level_3": "CMMC Level 3",
    "il4": "DoD IL4",
    "il5": "DoD IL5",
    "il6": "DoD IL6",
    "fips_199": "FIPS 199",
    "fips_200": "FIPS 200",
    "cnssi_1253": "CNSSI 1253",
    # ── Phase 23 Wave 1: Sector-specific frameworks ──
    "cjis": "CJIS Security Policy",
    "hipaa": "HIPAA Security Rule",
    "hitrust": "HITRUST CSF v11",
    "soc2": "SOC 2 Type II",
    "pci_dss": "PCI DSS v4.0",
    # ── Phase 25: Zero Trust Architecture ──
    "nist_800_207": "NIST SP 800-207 (ZTA)",
    # ── Phase 26: DoD MOSA ──
    "mosa": "DoD MOSA (10 U.S.C. §4401)",
    # ── International Hub: ISO 27001 ──
    "iso_27001": "ISO/IEC 27001:2022",
}

# Mapping from CLI framework names to crosswalk keys
FRAMEWORK_ALIASES = {
    "fedramp": {"moderate": "fedramp_moderate", "high": "fedramp_high"},
    "fedramp_moderate": {"moderate": "fedramp_moderate", None: "fedramp_moderate"},
    "fedramp_high": {"high": "fedramp_high", None: "fedramp_high"},
    "cmmc": {"l2": "cmmc_level_2", "l3": "cmmc_level_3", "level_2": "cmmc_level_2", "level_3": "cmmc_level_3"},
    "cmmc_level_2": {"l2": "cmmc_level_2", None: "cmmc_level_2"},
    "cmmc_level_3": {"l3": "cmmc_level_3", None: "cmmc_level_3"},
    "800-171": {None: "nist_800_171"},
    "nist_800_171": {None: "nist_800_171"},
    "nist-800-171": {None: "nist_800_171"},
    # Phase 23 Wave 1 aliases
    "cjis": {None: "cjis"},
    "hipaa": {None: "hipaa"},
    "hitrust": {None: "hitrust"},
    "hitrust_csf": {None: "hitrust"},
    "soc2": {None: "soc2"},
    "soc_2": {None: "soc2"},
    "pci": {None: "pci_dss"},
    "pci_dss": {None: "pci_dss"},
    "pci-dss": {None: "pci_dss"},
    "iso_27001": {None: "iso_27001"},
    # Phase 26 MOSA aliases
    "mosa": {None: "mosa"},
    "dod_mosa": {None: "mosa"},
    "modular_open_systems": {None: "mosa"},
    "iso27001": {None: "iso_27001"},
    "iso-27001": {None: "iso_27001"},
    # Phase 25: ZTA aliases
    "nist_800_207": {None: "nist_800_207"},
    "800-207": {None: "nist_800_207"},
    "zta": {None: "nist_800_207"},
    "zero_trust": {None: "nist_800_207"},
}

# Impact level to crosswalk key mapping
IL_KEYS = {
    "IL4": "il4",
    "IL5": "il5",
    "IL6": "il6",
}


# Impact level to NIST 800-53 baseline mapping (via FIPS 199)
IL_BASELINE_MAP = {"IL2": "Low", "IL4": "Moderate", "IL5": "High", "IL6": "High"}


# -----------------------------------------------------------------
# FIPS 199 baseline integration
# -----------------------------------------------------------------

def get_baseline_from_categorization(project_id, db_path=None):
    """Get the NIST 800-53 baseline from the project's FIPS 199 categorization.

    Priority:
    1. fips199_categorizations table (approved, then draft).
    2. projects.fips199_overall column.
    3. Impact level mapping via IL_BASELINE_MAP.

    Returns:
        {"baseline": "Moderate", "source": "fips199_categorization",
         "categorization": {"C": "Moderate", "I": "Moderate", "A": "Low", "overall": "Moderate"}}
    """
    conn = _get_connection(db_path)
    try:
        # Try fips199_categorizations table
        try:
            row = conn.execute(
                """SELECT confidentiality_impact, integrity_impact, availability_impact,
                          overall_categorization, baseline_selected, status
                   FROM fips199_categorizations
                   WHERE project_id = ? AND status IN ('approved', 'draft')
                   ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 2 END,
                            categorization_date DESC
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            if row:
                baseline = row["baseline_selected"] or row["overall_categorization"]
                return {
                    "baseline": baseline,
                    "source": "fips199_categorization",
                    "categorization": {
                        "C": row["confidentiality_impact"],
                        "I": row["integrity_impact"],
                        "A": row["availability_impact"],
                        "overall": row["overall_categorization"],
                    },
                }
        except Exception:
            pass  # Table may not exist yet

        # Try projects.fips199_overall
        proj = conn.execute(
            "SELECT fips199_overall, impact_level FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if proj:
            if proj["fips199_overall"]:
                return {
                    "baseline": proj["fips199_overall"],
                    "source": "projects_table",
                    "categorization": None,
                }
            il = proj["impact_level"] or "IL5"
            return {
                "baseline": IL_BASELINE_MAP.get(il, "Moderate"),
                "source": f"impact_level_{il}",
                "categorization": None,
            }

        return {"baseline": "Moderate", "source": "default", "categorization": None}
    finally:
        conn.close()


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


def _log_audit_event(conn, project_id, action, details):
    """Log an audit trail event (append-only, NIST AU compliant).

    Uses event_type 'compliance_check' which is an allowed value in the
    audit_trail CHECK constraint.
    """
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "compliance_check",
                "icdev-crosswalk-engine",
                action,
                json.dumps(details) if isinstance(details, dict) else str(details),
                json.dumps([]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _ensure_crosswalk_tables(conn):
    """Ensure crosswalk-specific tables exist in the database.

    Creates control_crosswalk and project_framework_status tables if they
    do not already exist. These supplement the existing project_controls table.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS control_crosswalk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nist_800_53_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            framework_control_id TEXT,
            mapping_type TEXT DEFAULT 'equivalent'
                CHECK(mapping_type IN ('equivalent', 'partial', 'superset', 'subset')),
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(nist_800_53_id, framework_id)
        );

        CREATE INDEX IF NOT EXISTS idx_crosswalk_nist
            ON control_crosswalk(nist_800_53_id);
        CREATE INDEX IF NOT EXISTS idx_crosswalk_framework
            ON control_crosswalk(framework_id);

        CREATE TABLE IF NOT EXISTS project_framework_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            total_controls INTEGER DEFAULT 0,
            implemented_controls INTEGER DEFAULT 0,
            coverage_pct REAL DEFAULT 0.0,
            gate_status TEXT DEFAULT 'not_started'
                CHECK(gate_status IN ('not_started', 'in_progress', 'compliant', 'non_compliant')),
            last_assessed TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, framework_id)
        );

        CREATE INDEX IF NOT EXISTS idx_pfs_project
            ON project_framework_status(project_id);
    """)
    conn.commit()


# -----------------------------------------------------------------
# Core functions
# -----------------------------------------------------------------

def load_crosswalk():
    """Load and cache the crosswalk JSON data.

    Returns the crosswalk array from control_crosswalk.json. Caches the
    result in the module-level _CROSSWALK_CACHE to avoid repeated disk I/O.

    Returns:
        list: Array of crosswalk mapping dicts from the JSON file.

    Raises:
        FileNotFoundError: If the crosswalk JSON file does not exist.
    """
    global _CROSSWALK_CACHE
    if _CROSSWALK_CACHE is not None:
        return _CROSSWALK_CACHE

    if not CROSSWALK_PATH.exists():
        raise FileNotFoundError(
            f"Crosswalk data file not found: {CROSSWALK_PATH}\n"
            "Expected: context/compliance/control_crosswalk.json"
        )
    with open(CROSSWALK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    _CROSSWALK_CACHE = data.get("crosswalk", [])
    return _CROSSWALK_CACHE


def load_iso_bridge():
    """Load and cache the ISO 27001 ↔ NIST 800-53 bridge data (ADR D111).

    Returns:
        list: Array of bridge mapping dicts from iso27001_nist_bridge.json.
    """
    global _ISO_BRIDGE_CACHE
    if _ISO_BRIDGE_CACHE is not None:
        return _ISO_BRIDGE_CACHE

    if not ISO_BRIDGE_PATH.exists():
        _ISO_BRIDGE_CACHE = []
        return _ISO_BRIDGE_CACHE

    with open(ISO_BRIDGE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    _ISO_BRIDGE_CACHE = data.get("mappings", [])
    return _ISO_BRIDGE_CACHE


def get_nist_for_iso_control(iso_id):
    """Given an ISO 27001 control ID, return mapped NIST 800-53 controls.

    Args:
        iso_id: ISO 27001 Annex A control ID (e.g., "A.5.1").

    Returns:
        list: List of NIST 800-53 control IDs that map to this ISO control.
    """
    bridge = load_iso_bridge()
    iso_upper = iso_id.upper()

    for entry in bridge:
        if entry.get("iso_27001", "").upper() == iso_upper:
            return entry.get("nist_800_53", [])

    return []


def get_iso_for_nist_control(nist_id):
    """Given a NIST 800-53 control ID, return mapped ISO 27001 controls.

    Args:
        nist_id: NIST 800-53 control ID (e.g., "AC-1").

    Returns:
        list: List of dicts with iso_27001, iso_title, mapping_type for each
              ISO control that maps to this NIST control.
    """
    bridge = load_iso_bridge()
    nist_upper = nist_id.upper()

    results = []
    for entry in bridge:
        nist_refs = entry.get("nist_800_53", [])
        if nist_upper in [r.upper() for r in nist_refs]:
            results.append({
                "iso_27001": entry.get("iso_27001"),
                "iso_title": entry.get("iso_title"),
                "mapping_type": entry.get("mapping_type", "equivalent"),
            })

    return results


def get_frameworks_for_control(nist_id):
    """Given a NIST 800-53 control ID, return all frameworks it satisfies.

    Args:
        nist_id: NIST 800-53 control ID (e.g., "AC-2").

    Returns:
        dict: Mapping of framework keys to their values. Boolean True for
              frameworks where the control applies, or a string identifier
              for frameworks with distinct control IDs (e.g., CMMC practice IDs).
              Returns empty dict if control not found.

    Example:
        >>> get_frameworks_for_control("AC-2")
        {
            "fedramp_moderate": True,
            "fedramp_high": True,
            "nist_800_171": "3.1.1",
            "cmmc_level_2": "AC.L2-3.1.1",
            ...
        }
    """
    crosswalk = load_crosswalk()
    nist_upper = nist_id.upper()

    for entry in crosswalk:
        entry_nist = entry.get("nist_id", entry.get("nist_800_53", ""))
        if entry_nist.upper() == nist_upper:
            result = {}
            for fw_key in FRAMEWORK_KEYS:
                val = entry.get(fw_key)
                if val is not None and val is not False:
                    result[fw_key] = val
            # Also check ISO 27001 bridge (ADR D111)
            if "iso_27001" not in result:
                iso_mappings = get_iso_for_nist_control(nist_upper)
                if iso_mappings:
                    result["iso_27001"] = [m["iso_27001"] for m in iso_mappings]
            return result

    return {}


def get_controls_for_framework(framework, baseline=None):
    """Return all NIST 800-53 controls required for a specific framework.

    Args:
        framework: Framework name (e.g., "fedramp", "cmmc", "800-171").
        baseline: Optional baseline level (e.g., "moderate", "high", "l2", "l3").

    Returns:
        list: List of crosswalk entry dicts for controls in the framework.
              Each dict contains nist_id, title, family, priority, and
              framework-specific mapping values.

    Example:
        >>> controls = get_controls_for_framework("fedramp", "moderate")
        >>> len(controls)  # Number of controls in FedRAMP Moderate
        39
    """
    crosswalk = load_crosswalk()

    # Resolve framework + baseline to a crosswalk key
    fw_lower = framework.lower().replace("-", "_").replace(" ", "_")
    crosswalk_key = None

    if fw_lower in FRAMEWORK_ALIASES:
        baseline_map = FRAMEWORK_ALIASES[fw_lower]
        if baseline:
            bl = baseline.lower().replace("-", "_").replace(" ", "_")
            crosswalk_key = baseline_map.get(bl)
        if crosswalk_key is None:
            crosswalk_key = baseline_map.get(None)
    elif fw_lower in FRAMEWORK_KEYS:
        crosswalk_key = fw_lower
    elif f"{fw_lower}_{baseline}" in FRAMEWORK_KEYS if baseline else False:
        crosswalk_key = f"{fw_lower}_{baseline}"

    if crosswalk_key is None:
        # Try direct key match as fallback
        for key in FRAMEWORK_KEYS:
            if fw_lower in key or key in fw_lower:
                crosswalk_key = key
                break

    if crosswalk_key is None:
        return []

    results = []
    for entry in crosswalk:
        val = entry.get(crosswalk_key)
        if val is not None and val is not False:
            results.append(entry)

    return results


def get_controls_for_impact_level(il_level):
    """Return required NIST 800-53 controls for a DoD Impact Level.

    Args:
        il_level: Impact level string ("IL4", "IL5", or "IL6").

    Returns:
        list: List of crosswalk entry dicts for controls required at
              the specified impact level.

    Raises:
        ValueError: If il_level is not IL4, IL5, or IL6.
    """
    il_upper = il_level.upper()
    if il_upper not in IL_KEYS:
        raise ValueError(
            f"Invalid impact level '{il_level}'. Valid: IL4, IL5, IL6"
        )

    crosswalk_key = IL_KEYS[il_upper]
    crosswalk = load_crosswalk()

    results = []
    for entry in crosswalk:
        val = entry.get(crosswalk_key)
        if val is not None and val is not False:
            results.append(entry)

    return results


def compute_crosswalk_coverage(project_id, db_path=None):
    """Query project_controls for implemented controls and compute per-framework coverage.

    Cross-references the project's implemented controls against the crosswalk
    data to determine coverage percentage for each framework.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        dict: Per-framework coverage data:
            {
                "fedramp_moderate": {
                    "total": 39, "implemented": 15, "coverage_pct": 38.5
                },
                ...
            }
    """
    conn = _get_connection(db_path)
    try:
        _ensure_crosswalk_tables(conn)

        # Get all implemented/partially-implemented control IDs for this project
        rows = conn.execute(
            """SELECT control_id, implementation_status
               FROM project_controls
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        implemented_ids = set()
        for row in rows:
            status = row["implementation_status"]
            if status in ("implemented", "partially_implemented"):
                implemented_ids.add(row["control_id"].upper())

        crosswalk = load_crosswalk()

        # Compute per-framework coverage
        coverage = {}
        for fw_key, fw_name in FRAMEWORK_KEYS.items():
            total = 0
            implemented = 0
            for entry in crosswalk:
                val = entry.get(fw_key)
                if val is not None and val is not False:
                    total += 1
                    nist = entry.get("nist_id", entry.get("nist_800_53", ""))
                    if nist.upper() in implemented_ids:
                        implemented += 1

            pct = round((implemented / total * 100), 1) if total > 0 else 0.0
            coverage[fw_key] = {
                "total": total,
                "implemented": implemented,
                "coverage_pct": pct,
            }

        # Update project_framework_status table
        now = datetime.now(timezone.utc).isoformat()
        for fw_key, data in coverage.items():
            gate = "not_started"
            if data["coverage_pct"] >= 100.0:
                gate = "compliant"
            elif data["coverage_pct"] > 0:
                gate = "in_progress"

            conn.execute(
                """INSERT OR REPLACE INTO project_framework_status
                   (project_id, framework_id, total_controls,
                    implemented_controls, coverage_pct, gate_status,
                    last_assessed, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, fw_key, data["total"],
                    data["implemented"], data["coverage_pct"],
                    gate, now, now,
                ),
            )
        conn.commit()

        # Log audit event
        _log_audit_event(
            conn, project_id,
            "Crosswalk coverage computed",
            {
                "frameworks_assessed": len(coverage),
                "implemented_controls": len(implemented_ids),
                "coverage_summary": {
                    k: v["coverage_pct"] for k, v in coverage.items()
                },
            },
        )

        return coverage
    finally:
        conn.close()


def get_gap_analysis(project_id, target_framework, baseline=None, db_path=None):
    """Return unimplemented controls for a target framework with priority ordering.

    Args:
        project_id: The project identifier.
        target_framework: Target framework name (e.g., "fedramp", "cmmc").
        baseline: Optional baseline level (e.g., "moderate", "high").
        db_path: Optional database path override.

    Returns:
        list: List of gap dicts sorted by priority (P1 first), each containing:
            {
                "nist_id": "AC-3",
                "title": "Access Enforcement",
                "priority": "P1",
                "family": "AC",
                "framework_id": "FedRAMP Moderate",
                "framework_control_id": True,
                "status": "planned"
            }
    """
    conn = _get_connection(db_path)
    try:
        # Get all control IDs and statuses for this project
        rows = conn.execute(
            """SELECT control_id, implementation_status
               FROM project_controls
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        project_controls = {}
        for row in rows:
            project_controls[row["control_id"].upper()] = row["implementation_status"]

        # Get all controls required by the target framework
        required = get_controls_for_framework(target_framework, baseline)

        # Resolve framework key for display
        fw_lower = target_framework.lower().replace("-", "_").replace(" ", "_")
        crosswalk_key = None
        if fw_lower in FRAMEWORK_ALIASES:
            baseline_map = FRAMEWORK_ALIASES[fw_lower]
            if baseline:
                bl = baseline.lower().replace("-", "_").replace(" ", "_")
                crosswalk_key = baseline_map.get(bl)
            if crosswalk_key is None:
                crosswalk_key = baseline_map.get(None)
        elif fw_lower in FRAMEWORK_KEYS:
            crosswalk_key = fw_lower

        fw_display = FRAMEWORK_KEYS.get(crosswalk_key, target_framework)

        # Find gaps: controls that are not 'implemented'
        gaps = []
        for entry in required:
            nist_id = entry.get("nist_id", entry.get("nist_800_53", "")).upper()
            status = project_controls.get(nist_id, "not_mapped")

            if status != "implemented":
                gap = {
                    "nist_id": entry.get("nist_id", entry.get("nist_800_53", "")),
                    "title": entry.get("title", ""),
                    "priority": entry.get("priority", "P3"),
                    "family": entry.get("family", ""),
                    "framework_id": fw_display,
                    "framework_control_id": entry.get(crosswalk_key, ""),
                    "status": status,
                }
                gaps.append(gap)

        # Sort by priority (P1 > P2 > P3), then by nist_id
        priority_order = {"P1": 0, "P2": 1, "P3": 2}
        gaps.sort(key=lambda g: (
            priority_order.get(g["priority"], 99),
            g["nist_id"],
        ))

        return gaps
    finally:
        conn.close()


def map_implementation_across_frameworks(project_id, control_id, db_path=None):
    """Auto-update framework status when a NIST 800-53 control is implemented.

    This is the key function enabling "implement once, satisfy many." When a
    control is marked as implemented in project_controls, this function:
    1. Looks up all frameworks the control satisfies via the crosswalk.
    2. Populates/updates the control_crosswalk table with DB-level mappings.
    3. Recomputes coverage for each affected framework.
    4. Updates the project_framework_status table.
    5. Logs the crosswalk mapping in the audit trail.

    Args:
        project_id: The project identifier.
        control_id: The NIST 800-53 control ID (e.g., "AC-2").
        db_path: Optional database path override.

    Returns:
        dict: Summary of frameworks updated:
            {
                "control_id": "AC-2",
                "frameworks_satisfied": ["fedramp_moderate", "fedramp_high", ...],
                "coverage_updated": {
                    "fedramp_moderate": {"coverage_pct": 38.5},
                    ...
                }
            }
    """
    conn = _get_connection(db_path)
    try:
        _ensure_crosswalk_tables(conn)
        control_upper = control_id.upper()

        # Verify the control is actually implemented for this project
        row = conn.execute(
            """SELECT implementation_status
               FROM project_controls
               WHERE project_id = ? AND control_id = ?""",
            (project_id, control_upper),
        ).fetchone()

        if not row:
            raise ValueError(
                f"Control '{control_id}' not found in project_controls "
                f"for project '{project_id}'."
            )

        # Look up all frameworks this control satisfies
        frameworks = get_frameworks_for_control(control_upper)
        if not frameworks:
            return {
                "control_id": control_upper,
                "frameworks_satisfied": [],
                "coverage_updated": {},
            }

        satisfied = list(frameworks.keys())

        # Populate control_crosswalk table
        now = datetime.now(timezone.utc).isoformat()
        for fw_key, fw_val in frameworks.items():
            fw_control_id = str(fw_val) if fw_val is not True else None
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO control_crosswalk
                       (nist_800_53_id, framework_id, framework_control_id,
                        mapping_type, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        control_upper, fw_key, fw_control_id,
                        "equivalent", now,
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert crosswalk for "
                    f"{control_upper} -> {fw_key}: {e}",
                    file=sys.stderr,
                )
        conn.commit()

        # Recompute coverage (this also updates project_framework_status)
        # Close and reopen to avoid lock issues with compute_crosswalk_coverage
        conn.close()
        coverage = compute_crosswalk_coverage(project_id, db_path=db_path)

        # Reopen for final audit log
        conn = _get_connection(db_path)
        coverage_summary = {
            k: {"coverage_pct": v["coverage_pct"]}
            for k, v in coverage.items()
            if k in satisfied
        }

        _log_audit_event(
            conn, project_id,
            f"Crosswalk mapped: {control_upper} -> {len(satisfied)} frameworks",
            {
                "control_id": control_upper,
                "implementation_status": row["implementation_status"],
                "frameworks_satisfied": satisfied,
                "coverage_summary": coverage_summary,
            },
        )

        return {
            "control_id": control_upper,
            "frameworks_satisfied": satisfied,
            "coverage_updated": coverage_summary,
        }
    finally:
        conn.close()


def get_crosswalk_summary():
    """Return summary statistics for the crosswalk dataset.

    Returns:
        dict: Summary containing:
            {
                "total_controls": 39,
                "frameworks": {
                    "fedramp_moderate": {"count": 39, "name": "FedRAMP Moderate"},
                    ...
                },
                "impact_levels": {
                    "IL4": 39, "IL5": 39, "IL6": 39
                },
                "families": {
                    "AC": 5, "AU": 5, "CM": 5, ...
                }
            }
    """
    crosswalk = load_crosswalk()

    # Per-framework counts
    fw_counts = {}
    for fw_key, fw_name in FRAMEWORK_KEYS.items():
        count = 0
        for entry in crosswalk:
            val = entry.get(fw_key)
            if val is not None and val is not False:
                count += 1
        fw_counts[fw_key] = {"count": count, "name": fw_name}

    # Per-IL counts
    il_counts = {}
    for il_name, il_key in IL_KEYS.items():
        count = 0
        for entry in crosswalk:
            val = entry.get(il_key)
            if val is not None and val is not False:
                count += 1
        il_counts[il_name] = count

    # Per-family counts
    family_counts = {}
    for entry in crosswalk:
        fam = entry.get("family", "??")
        family_counts[fam] = family_counts.get(fam, 0) + 1

    return {
        "total_controls": len(crosswalk),
        "frameworks": fw_counts,
        "impact_levels": il_counts,
        "families": dict(sorted(family_counts.items())),
    }


# -----------------------------------------------------------------
# Formatting helpers
# -----------------------------------------------------------------

def _resolve_framework_key(framework, baseline=None):
    """Resolve a CLI framework name + baseline to a crosswalk key."""
    fw_lower = framework.lower().replace("-", "_").replace(" ", "_")
    crosswalk_key = None

    if fw_lower in FRAMEWORK_ALIASES:
        baseline_map = FRAMEWORK_ALIASES[fw_lower]
        if baseline:
            bl = baseline.lower().replace("-", "_").replace(" ", "_")
            crosswalk_key = baseline_map.get(bl)
        if crosswalk_key is None:
            crosswalk_key = baseline_map.get(None)
    elif fw_lower in FRAMEWORK_KEYS:
        crosswalk_key = fw_lower

    return crosswalk_key


def _format_control_lookup(nist_id, frameworks, as_json=False):
    """Format the output of a control lookup."""
    if as_json:
        return json.dumps(
            {"nist_id": nist_id, "frameworks": frameworks},
            indent=2,
        )

    if not frameworks:
        return f"Control '{nist_id}' not found in crosswalk data."

    lines = [
        f"{'=' * 60}",
        f"  Crosswalk: {nist_id}",
        f"{'=' * 60}",
    ]
    for fw_key, fw_val in sorted(frameworks.items()):
        fw_name = FRAMEWORK_KEYS.get(fw_key, fw_key)
        if fw_val is True:
            lines.append(f"  {fw_name:<25} Required")
        else:
            lines.append(f"  {fw_name:<25} {fw_val}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def _format_framework_controls(framework, baseline, controls, as_json=False):
    """Format the list of controls for a framework."""
    if as_json:
        return json.dumps(
            {
                "framework": framework,
                "baseline": baseline,
                "total_controls": len(controls),
                "controls": [
                    {
                        "nist_id": c["nist_id"],
                        "title": c.get("title", ""),
                        "family": c.get("family", ""),
                        "priority": c.get("priority", ""),
                    }
                    for c in controls
                ],
            },
            indent=2,
        )

    if not controls:
        return f"No controls found for framework '{framework}' (baseline: {baseline or 'any'})."

    label = f"{framework}"
    if baseline:
        label += f" ({baseline})"

    lines = [
        f"Controls required for {label}: {len(controls)}",
        f"{'=' * 70}",
        f"{'NIST ID':<10} {'Family':<8} {'Priority':<10} {'Title'}",
        f"{'-' * 70}",
    ]
    for c in sorted(controls, key=lambda x: x["nist_id"]):
        lines.append(
            f"{c['nist_id']:<10} {c.get('family', ''):<8} "
            f"{c.get('priority', ''):<10} {c.get('title', '')}"
        )
    lines.append(f"{'=' * 70}")
    lines.append(f"Total: {len(controls)} controls")
    return "\n".join(lines)


def _format_coverage(project_id, coverage, as_json=False):
    """Format crosswalk coverage data."""
    if as_json:
        return json.dumps(
            {"project_id": project_id, "coverage": coverage},
            indent=2,
        )

    lines = [
        f"{'=' * 65}",
        f"  Crosswalk Coverage: {project_id}",
        f"{'=' * 65}",
        f"  {'Framework':<25} {'Implemented':<15} {'Total':<10} {'Coverage'}",
        f"  {'-' * 60}",
    ]
    for fw_key in FRAMEWORK_KEYS:
        if fw_key in coverage:
            data = coverage[fw_key]
            fw_name = FRAMEWORK_KEYS[fw_key]
            pct_str = f"{data['coverage_pct']:.1f}%"
            bar_len = int(data["coverage_pct"] / 5)
            bar = "#" * bar_len + "." * (20 - bar_len)
            lines.append(
                f"  {fw_name:<25} {data['implemented']:<15} "
                f"{data['total']:<10} {pct_str:<8} [{bar}]"
            )
    lines.append(f"{'=' * 65}")
    return "\n".join(lines)


def _format_gap_analysis(project_id, framework, gaps, as_json=False):
    """Format gap analysis results."""
    if as_json:
        return json.dumps(
            {
                "project_id": project_id,
                "framework": framework,
                "total_gaps": len(gaps),
                "gaps": gaps,
            },
            indent=2,
        )

    if not gaps:
        return f"No gaps found for project '{project_id}' against {framework}. Full coverage achieved."

    lines = [
        f"{'=' * 75}",
        f"  Gap Analysis: {project_id} -> {framework}",
        f"  Total Gaps: {len(gaps)}",
        f"{'=' * 75}",
        f"  {'NIST ID':<10} {'Priority':<10} {'Status':<22} {'Title'}",
        f"  {'-' * 70}",
    ]
    for gap in gaps:
        status_display = gap["status"].replace("_", " ")
        lines.append(
            f"  {gap['nist_id']:<10} {gap['priority']:<10} "
            f"{status_display:<22} {gap['title']}"
        )
    lines.append(f"{'=' * 75}")

    # Priority breakdown
    p1_count = sum(1 for g in gaps if g["priority"] == "P1")
    p2_count = sum(1 for g in gaps if g["priority"] == "P2")
    p3_count = sum(1 for g in gaps if g["priority"] == "P3")
    lines.append(f"  Priority breakdown: P1={p1_count}  P2={p2_count}  P3={p3_count}")
    return "\n".join(lines)


def _format_summary(summary, as_json=False):
    """Format crosswalk summary statistics."""
    if as_json:
        return json.dumps(summary, indent=2)

    lines = [
        f"{'=' * 60}",
        "  Control Framework Crosswalk Summary",
        f"{'=' * 60}",
        f"  Total NIST 800-53 controls mapped: {summary['total_controls']}",
        "",
        "  Framework Coverage:",
    ]
    for fw_key, fw_data in sorted(summary["frameworks"].items()):
        lines.append(f"    {fw_data['name']:<25} {fw_data['count']} controls")

    lines.append("")
    lines.append("  Impact Level Coverage:")
    for il_name, il_count in sorted(summary["impact_levels"].items()):
        lines.append(f"    {il_name:<10} {il_count} controls")

    lines.append("")
    lines.append("  Controls by Family:")
    for fam, count in summary["families"].items():
        lines.append(f"    {fam:<8} {count} controls")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


# -----------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Control Framework Crosswalk Engine"
    )
    parser.add_argument(
        "--control",
        help="Look up frameworks for a NIST 800-53 control (e.g., AC-2)",
    )
    parser.add_argument(
        "--framework",
        help="List controls for a framework (fedramp, cmmc, 800-171)",
    )
    parser.add_argument(
        "--baseline",
        help="Framework baseline (moderate, high, l2, l3)",
    )
    parser.add_argument(
        "--impact-level",
        choices=["IL4", "IL5", "IL6"],
        help="Controls for impact level",
    )
    parser.add_argument(
        "--project-id",
        help="Project ID for coverage/gap analysis",
    )
    parser.add_argument(
        "--gap-analysis",
        action="store_true",
        help="Show gap analysis for target framework",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Show crosswalk coverage",
    )
    parser.add_argument(
        "--map-control",
        help="Map a control implementation across frameworks (requires --project-id)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show crosswalk summary stats",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Database path override",
    )
    args = parser.parse_args()

    try:
        db_path = args.db_path if args.db_path else None

        # --control: Look up frameworks for a NIST control
        if args.control:
            frameworks = get_frameworks_for_control(args.control)
            print(_format_control_lookup(args.control.upper(), frameworks, args.json))

        # --framework (without --project-id): List controls for a framework
        elif args.framework and not args.project_id and not args.gap_analysis:
            controls = get_controls_for_framework(args.framework, args.baseline)
            print(_format_framework_controls(
                args.framework, args.baseline, controls, args.json
            ))

        # --impact-level: Controls for an impact level
        elif args.impact_level:
            controls = get_controls_for_impact_level(args.impact_level)
            print(_format_framework_controls(
                args.impact_level, None, controls, args.json
            ))

        # --project-id --coverage: Coverage report
        elif args.project_id and args.coverage:
            coverage = compute_crosswalk_coverage(args.project_id, db_path=db_path)
            print(_format_coverage(args.project_id, coverage, args.json))

        # --project-id --framework --gap-analysis: Gap analysis
        elif args.project_id and args.framework and args.gap_analysis:
            gaps = get_gap_analysis(
                args.project_id, args.framework,
                baseline=args.baseline, db_path=db_path,
            )
            print(_format_gap_analysis(
                args.project_id, args.framework, gaps, args.json
            ))

        # --project-id --map-control: Map implementation across frameworks
        elif args.project_id and args.map_control:
            result = map_implementation_across_frameworks(
                args.project_id, args.map_control, db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Mapped {result['control_id']} across "
                      f"{len(result['frameworks_satisfied'])} frameworks:")
                for fw in result["frameworks_satisfied"]:
                    fw_name = FRAMEWORK_KEYS.get(fw, fw)
                    pct = result["coverage_updated"].get(fw, {}).get("coverage_pct", "N/A")
                    print(f"  {fw_name}: coverage now {pct}%")

        # --summary: Crosswalk summary stats
        elif args.summary:
            summary = get_crosswalk_summary()
            print(_format_summary(summary, args.json))

        else:
            parser.print_help()
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
