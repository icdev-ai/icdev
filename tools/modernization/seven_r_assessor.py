# CUI // SP-CTI
#!/usr/bin/env python3
"""7R Migration Strategy Recommendation Engine for ICDEV DoD Modernization.

Evaluates legacy applications against the 7 Rs of cloud migration:
  Rehost, Replatform, Refactor, Rearchitect, Repurchase, Retire, Retain

Reads application profile data from legacy_* tables in icdev.db, scores each
strategy using configurable weighted criteria, and writes a ranked assessment
to the migration_assessments table.  All scoring is deterministic — no LLM
calls, no external network access.

Usage:
    python tools/modernization/seven_r_assessor.py --project-id P-001 --app-id A-001
    python tools/modernization/seven_r_assessor.py --project-id P-001 --app-id A-001 --matrix
    python tools/modernization/seven_r_assessor.py --project-id P-001 --app-id A-001 --json
    python tools/modernization/seven_r_assessor.py --project-id P-001 --app-id A-001 --weights custom.json

Classification: CUI // SP-CTI
Environment:    AWS GovCloud (us-gov-west-1)
Compliance:     NIST 800-53 Rev 5 / RMF
"""

import argparse
import json
import math
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CATALOG_PATH = BASE_DIR / "context" / "modernization" / "seven_rs_catalog.json"

# ---------------------------------------------------------------------------
# Known EOL frameworks / languages for fitness checks
# ---------------------------------------------------------------------------
EOL_FRAMEWORKS = {
    "struts", "struts2", "ejb2", "jsf", "jsf1",
    "spring2", "spring3",
    "wcf", "webforms", "aspnet-webforms", "silverlight",
    "django1", "flask0",
    "rails3", "rails4",
    "angularjs",
    ".net-framework", "dotnet-framework",
}

EOL_LANGUAGES = {
    ("python", "2"): True,
    ("java", "6"): True,
    ("java", "7"): True,
    ("ruby", "1"): True,
    ("ruby", "2.5"): True,
    ("php", "5"): True,
    ("php", "7.0"): True,
    ("php", "7.1"): True,
    ("php", "7.2"): True,
    ("php", "7.3"): True,
}

# Known version upgrade paths
KNOWN_UPGRADE_PATHS = {
    ("python", "2"): "3",
    ("java", "8"): "17",
    ("java", "11"): "17",
    ("java", "7"): "17",
    ("dotnet", "framework-4"): "net-8",
    ("csharp", "framework-4"): "net-8",
    ("ruby", "2"): "3",
    ("php", "7"): "8",
}

# Known framework migration paths
KNOWN_FRAMEWORK_MIGRATIONS = {
    "struts": "spring-boot",
    "struts2": "spring-boot",
    "ejb": "spring-boot",
    "ejb2": "spring-boot",
    "ejb3": "spring-boot",
    "jsf": "spring-boot",
    "wcf": "grpc",
    "webforms": "blazor",
    "aspnet-webforms": "blazor",
    ".net-framework": ".net-8",
    "dotnet-framework": ".net-8",
    "django1": "django4",
    "flask0": "flask3",
    "angularjs": "angular",
    "spring3": "spring-boot",
    "spring4": "spring-boot",
    "rails3": "rails7",
    "rails4": "rails7",
}

# Standard databases that map well to managed services
STANDARD_DB_TYPES = {"postgres", "postgresql", "mysql", "mariadb", "aurora"}
MIGRATABLE_DB_TYPES = {"oracle", "mssql", "sqlserver", "sql-server", "db2"}
EXOTIC_DB_TYPES = {"informix", "sybase", "ingres", "pick", "mumps", "adabas"}

# Risk level numeric mapping
RISK_LEVEL_MAP = {
    "none": 0.0,
    "low": 0.2,
    "medium": 0.5,
    "high": 0.8,
    "critical": 1.0,
}

# ATO impact ordering for display
ATO_IMPACT_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# ============================================================================
# Database helper
# ============================================================================

def _get_db(db_path=None):
    """Return a sqlite3 connection with Row factory for dict-like access.

    Args:
        db_path: Optional override path to the SQLite database.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# Catalog loader
# ============================================================================

def load_seven_rs_catalog(catalog_path=None):
    """Load the 7 Rs strategy catalog from JSON.

    Args:
        catalog_path: Optional override path to the catalog JSON file.

    Returns:
        Parsed dict containing metadata, default_weights, and strategies list.

    Raises:
        FileNotFoundError: If the catalog file does not exist.
        json.JSONDecodeError: If the catalog contains invalid JSON.
    """
    path = catalog_path or CATALOG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Seven Rs catalog not found: {path}\n"
            "Expected at: context/modernization/seven_rs_catalog.json"
        )
    with open(path, "r", encoding="utf-8") as fh:
        catalog = json.load(fh)
    return catalog


# ============================================================================
# Application profile builder
# ============================================================================

def _get_app_profile(app_id, db_path=None):
    """Query legacy_applications and aggregate stats from related tables.

    Builds a comprehensive profile dict containing:
      - All columns from legacy_applications
      - Aggregated component statistics (count, avg complexity, avg coupling, etc.)
      - Dependency counts by type
      - API count
      - Database schema information
      - Derived metrics (test ratio, package count, etc.)

    Args:
        app_id: The legacy application ID to look up.
        db_path: Optional database path override.

    Returns:
        Dict with all metrics needed for scoring.

    Raises:
        ValueError: If the application ID is not found in the database.
    """
    conn = _get_db(db_path)
    try:
        # --- Core application row ---
        row = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ?", (app_id,)
        ).fetchone()
        if row is None:
            raise ValueError(
                f"Application '{app_id}' not found in legacy_applications."
            )
        profile = dict(row)

        # --- Component aggregates ---
        comp_rows = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()

        components = [dict(r) for r in comp_rows]
        profile["components"] = components
        profile["component_count"] = len(components)

        if components:
            complexities = [c.get("cyclomatic_complexity", 0) or 0 for c in components]
            couplings = [c.get("coupling_score", 0) or 0 for c in components]
            cohesions = [c.get("cohesion_score", 0) or 0 for c in components]
            deps_in = [c.get("dependencies_in", 0) or 0 for c in components]
            deps_out = [c.get("dependencies_out", 0) or 0 for c in components]
            locs = [c.get("loc", 0) or 0 for c in components]
            types = [c.get("component_type", "") for c in components]

            profile["avg_complexity"] = sum(complexities) / len(complexities)
            profile["max_complexity"] = max(complexities)
            profile["avg_coupling"] = sum(couplings) / len(couplings)
            profile["avg_cohesion"] = sum(cohesions) / len(cohesions)
            profile["total_deps_in"] = sum(deps_in)
            profile["total_deps_out"] = sum(deps_out)
            profile["total_component_loc"] = sum(locs)
            profile["component_types"] = list(set(types))

            # Count test-related components
            test_types = {"test", "tests", "test_suite", "unit_test", "integration_test", "spec"}
            test_count = sum(1 for t in types if t and t.lower() in test_types)
            profile["test_component_count"] = test_count
            profile["test_component_ratio"] = test_count / len(components) if components else 0.0

            # Count distinct packages / namespaces
            namespaces = set()
            for c in components:
                ctype = (c.get("component_type") or "").lower()
                if ctype in ("package", "namespace", "module"):
                    namespaces.add(c.get("id"))
            profile["distinct_namespaces"] = len(namespaces) if namespaces else max(1, len(set(types)))
        else:
            profile["avg_complexity"] = 0
            profile["max_complexity"] = 0
            profile["avg_coupling"] = 0
            profile["avg_cohesion"] = 0
            profile["total_deps_in"] = 0
            profile["total_deps_out"] = 0
            profile["total_component_loc"] = 0
            profile["component_types"] = []
            profile["test_component_count"] = 0
            profile["test_component_ratio"] = 0.0
            profile["distinct_namespaces"] = 0

        # --- Dependency aggregates ---
        dep_rows = conn.execute(
            "SELECT * FROM legacy_dependencies WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
        dependencies = [dict(r) for r in dep_rows]
        profile["dependencies"] = dependencies
        profile["dependency_count"] = len(dependencies)

        dep_type_counts = {}
        for d in dependencies:
            dtype = d.get("dependency_type", "unknown")
            dep_type_counts[dtype] = dep_type_counts.get(dtype, 0) + 1
        profile["dependency_type_counts"] = dep_type_counts
        profile["external_dep_count"] = dep_type_counts.get("external", 0) + dep_type_counts.get("third_party", 0) + dep_type_counts.get("library", 0)

        # --- API aggregates ---
        api_rows = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
        profile["apis"] = [dict(r) for r in api_rows]
        profile["api_count"] = len(api_rows)

        # --- DB schema aggregates ---
        db_rows = conn.execute(
            "SELECT * FROM legacy_db_schemas WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
        db_schemas = [dict(r) for r in db_rows]
        profile["db_schemas"] = db_schemas
        profile["db_schema_count"] = len(db_schemas)
        profile["db_types"] = list(set(
            (s.get("db_type") or "unknown").lower() for s in db_schemas
        ))

        # --- Normalize key fields with safe defaults ---
        profile["loc_total"] = profile.get("loc_total") or 0
        profile["loc_code"] = profile.get("loc_code") or 0
        profile["file_count"] = profile.get("file_count") or 0
        profile["complexity_score"] = profile.get("complexity_score") or 0.0
        profile["tech_debt_hours"] = profile.get("tech_debt_hours") or 0.0
        profile["maintainability_index"] = profile.get("maintainability_index") or 0.0
        profile["primary_language"] = (profile.get("primary_language") or "unknown").lower()
        profile["language_version"] = (profile.get("language_version") or "").lower()
        profile["framework"] = (profile.get("framework") or "unknown").lower()
        profile["framework_version"] = (profile.get("framework_version") or "").lower()
        profile["app_type"] = (profile.get("app_type") or "unknown").lower()

        return profile
    finally:
        conn.close()


# ============================================================================
# Fitness check functions — one per strategy
# ============================================================================

def _check_rehost_fitness(profile):
    """Evaluate rehost (lift-and-shift) fitness.

    Criteria:
        containerizable      — minimal OS/file-system coupling
        external_deps_minimal — few external dependencies
        config_externalizable — config separate from code
        stateless            — no heavy local state / file writes
        security_clean       — good maintainability, low complexity

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # containerizable: heuristic based on dependency types
    os_dep_keywords = {"os-specific", "hardware", "native", "driver", "kernel", "system"}
    file_dep_keywords = {"file-io", "local-storage", "nfs", "smb", "cifs", "shared-drive"}

    os_count = 0
    file_count = 0
    for d in profile.get("dependencies", []):
        dtype = (d.get("dependency_type") or "").lower()
        if any(k in dtype for k in os_dep_keywords):
            os_count += 1
        if any(k in dtype for k in file_dep_keywords):
            file_count += 1

    heavy_count = os_count + file_count
    if heavy_count == 0:
        scores["containerizable"] = 1.0
    elif heavy_count <= 3:
        scores["containerizable"] = 0.5
    else:
        scores["containerizable"] = 0.0

    # external_deps_minimal
    ext_count = profile.get("external_dep_count", 0)
    if ext_count < 5:
        scores["external_deps_minimal"] = 1.0
    elif ext_count <= 15:
        scores["external_deps_minimal"] = 0.5
    else:
        scores["external_deps_minimal"] = 0.0

    # config_externalizable: heuristic — if file_count > 0 and complexity is
    # low, config is likely separable.  We use a proxy: maintainability > 50
    # and low coupling suggest well-structured config.
    maint = profile.get("maintainability_index", 0)
    coupling = profile.get("avg_coupling", 0)
    if maint > 50 and coupling < 0.5:
        scores["config_externalizable"] = 1.0
    elif maint > 30 or coupling < 0.7:
        scores["config_externalizable"] = 0.5
    else:
        scores["config_externalizable"] = 0.0

    # stateless: check for local-file write type dependencies
    write_keywords = {"file-write", "local-write", "local-storage", "tmp-storage", "session-file"}
    write_count = 0
    for d in profile.get("dependencies", []):
        dtype = (d.get("dependency_type") or "").lower()
        if any(k in dtype for k in write_keywords):
            write_count += 1
    if write_count == 0:
        scores["stateless"] = 1.0
    elif write_count <= 2:
        scores["stateless"] = 0.5
    else:
        scores["stateless"] = 0.0

    # security_clean: maintainability > 60 AND avg complexity < 15
    avg_cx = profile.get("avg_complexity", 0)
    if maint > 60 and avg_cx < 15:
        scores["security_clean"] = 1.0
    elif maint > 40 and avg_cx < 25:
        scores["security_clean"] = 0.5
    else:
        scores["security_clean"] = 0.0

    return scores


def _check_replatform_fitness(profile):
    """Evaluate replatform (lift-tinker-shift) fitness.

    Criteria:
        managed_db_candidate          — database can move to managed service
        container_ready_with_tweaks   — mostly containerizable
        config_separable              — config extractable to env vars
        cloud_services_available      — standard service replacements exist
        minor_code_changes_only       — less than 10% code needs changes

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # managed_db_candidate
    db_types = set(profile.get("db_types", []))
    if not db_types or db_types <= STANDARD_DB_TYPES:
        scores["managed_db_candidate"] = 1.0
    elif db_types & MIGRATABLE_DB_TYPES:
        scores["managed_db_candidate"] = 0.5
    elif db_types & EXOTIC_DB_TYPES:
        scores["managed_db_candidate"] = 0.0
    else:
        # Unknown DB type — conservative score
        scores["managed_db_candidate"] = 0.5

    # container_ready_with_tweaks: reuse containerizable from rehost, but
    # accept slightly worse scores.
    rehost_fit = _check_rehost_fitness(profile)
    container_score = rehost_fit.get("containerizable", 0.0)
    if container_score >= 0.5:
        scores["container_ready_with_tweaks"] = 1.0
    elif container_score > 0.0:
        scores["container_ready_with_tweaks"] = 0.5
    else:
        scores["container_ready_with_tweaks"] = 0.0

    # config_separable
    maint = profile.get("maintainability_index", 0)
    coupling = profile.get("avg_coupling", 0)
    if maint > 40 and coupling < 0.6:
        scores["config_separable"] = 1.0
    elif maint > 25 or coupling < 0.8:
        scores["config_separable"] = 0.5
    else:
        scores["config_separable"] = 0.0

    # cloud_services_available: standard app types have good cloud equivalents
    app_type = profile.get("app_type", "unknown")
    standard_app_types = {"web", "api", "microservice", "batch", "worker", "queue-consumer", "rest-api"}
    custom_app_types = {"desktop", "embedded", "hardware-interface", "mainframe"}
    if app_type in standard_app_types:
        scores["cloud_services_available"] = 1.0
    elif app_type in custom_app_types:
        scores["cloud_services_available"] = 0.0
    else:
        scores["cloud_services_available"] = 0.5

    # minor_code_changes_only: low tech debt + good maintainability means
    # changes should be small
    loc_total = profile.get("loc_total", 1)
    tech_debt = profile.get("tech_debt_hours", 0)
    # Rough heuristic: tech_debt_hours / (loc_total/100) gives a debt density
    debt_density = (tech_debt / max(loc_total / 100.0, 1.0))
    if debt_density < 5 and maint > 50:
        scores["minor_code_changes_only"] = 1.0
    elif debt_density < 15 and maint > 30:
        scores["minor_code_changes_only"] = 0.5
    else:
        scores["minor_code_changes_only"] = 0.0

    return scores


def _check_refactor_fitness(profile):
    """Evaluate refactor (code-level modernization) fitness.

    Criteria:
        version_upgrade_path_exists      — known upgrade path for language
        framework_migration_path_exists  — known migration for framework
        test_coverage_adequate           — sufficient test components
        codebase_well_structured         — maintainability index
        dependencies_manageable          — component count not overwhelming

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # version_upgrade_path_exists
    lang = profile.get("primary_language", "")
    lang_ver = profile.get("language_version", "")
    # Normalize: take just the major version for matching
    lang_ver_major = lang_ver.split(".")[0] if lang_ver else ""
    key = (lang, lang_ver_major)
    if key in KNOWN_UPGRADE_PATHS:
        scores["version_upgrade_path_exists"] = 1.0
    elif lang_ver:
        # Version specified but no known path — partial credit
        scores["version_upgrade_path_exists"] = 0.3
    else:
        scores["version_upgrade_path_exists"] = 0.0

    # framework_migration_path_exists
    framework = profile.get("framework", "").lower().strip()
    framework_normalized = framework.replace(" ", "-").replace("_", "-")
    if framework_normalized in KNOWN_FRAMEWORK_MIGRATIONS:
        scores["framework_migration_path_exists"] = 1.0
    elif framework and framework != "unknown":
        scores["framework_migration_path_exists"] = 0.3
    else:
        scores["framework_migration_path_exists"] = 0.0

    # test_coverage_adequate: >20% test components is good, some is partial
    test_ratio = profile.get("test_component_ratio", 0.0)
    if test_ratio >= 0.20:
        scores["test_coverage_adequate"] = 1.0
    elif test_ratio > 0.0:
        scores["test_coverage_adequate"] = 0.5
    else:
        scores["test_coverage_adequate"] = 0.0

    # codebase_well_structured: maintainability index
    maint = profile.get("maintainability_index", 0)
    if maint > 50:
        scores["codebase_well_structured"] = 1.0
    elif maint >= 25:
        scores["codebase_well_structured"] = 0.5
    else:
        scores["codebase_well_structured"] = 0.0

    # dependencies_manageable: component count
    comp_count = profile.get("component_count", 0)
    if comp_count < 50:
        scores["dependencies_manageable"] = 1.0
    elif comp_count <= 200:
        scores["dependencies_manageable"] = 0.5
    else:
        scores["dependencies_manageable"] = 0.0

    return scores


def _check_rearchitect_fitness(profile):
    """Evaluate rearchitect (rebuild cloud-native) fitness.

    Criteria:
        bounded_contexts_identifiable — distinct packages with low coupling
        api_boundaries_clear          — well-defined API endpoints
        data_stores_separable         — database schemas cluster
        team_capacity_sufficient      — default 0.5 (needs manual input)
        business_value_high           — default 0.5 (needs manual input)

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # bounded_contexts_identifiable: >3 namespaces with avg_coupling < 0.5
    namespaces = profile.get("distinct_namespaces", 0)
    avg_coupling = profile.get("avg_coupling", 1.0)
    if namespaces > 3 and avg_coupling < 0.5:
        scores["bounded_contexts_identifiable"] = 1.0
    elif namespaces > 2 and avg_coupling < 0.7:
        scores["bounded_contexts_identifiable"] = 0.5
    else:
        scores["bounded_contexts_identifiable"] = 0.0

    # api_boundaries_clear: >5 API endpoints suggest clear grouping
    api_count = profile.get("api_count", 0)
    if api_count >= 5:
        scores["api_boundaries_clear"] = 1.0
    elif api_count >= 2:
        scores["api_boundaries_clear"] = 0.5
    else:
        scores["api_boundaries_clear"] = 0.0

    # data_stores_separable: multiple DB schemas suggest separable data
    db_count = profile.get("db_schema_count", 0)
    if db_count >= 3:
        scores["data_stores_separable"] = 1.0
    elif db_count >= 2:
        scores["data_stores_separable"] = 0.5
    else:
        scores["data_stores_separable"] = 0.0

    # team_capacity_sufficient: requires manual assessment, default 0.5
    scores["team_capacity_sufficient"] = 0.5

    # business_value_high: requires manual assessment, default 0.5
    scores["business_value_high"] = 0.5

    return scores


def _check_repurchase_fitness(profile):
    """Evaluate repurchase (buy COTS/GOTS replacement) fitness.

    Criteria:
        commodity_functionality     — is it a common business function (default)
        cots_alternative_available  — needs manual assessment (default)
        low_customization           — LOC indicates customization level
        data_migration_feasible     — default, needs manual assessment

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # commodity_functionality: default — needs manual assessment
    scores["commodity_functionality"] = 0.5

    # cots_alternative_available: default — needs manual assessment
    scores["cots_alternative_available"] = 0.5

    # low_customization: based on LOC
    loc = profile.get("loc_code", 0) or profile.get("loc_total", 0)
    if loc < 5000:
        scores["low_customization"] = 1.0
    elif loc <= 20000:
        scores["low_customization"] = 0.5
    else:
        scores["low_customization"] = 0.0

    # data_migration_feasible: default — needs manual assessment
    scores["data_migration_feasible"] = 0.5

    return scores


def _check_retire_fitness(profile):
    """Evaluate retire (decommission) fitness.

    Criteria:
        low_usage              — default 0.5 (needs usage data)
        redundant_functionality — default 0.5
        eol_dependencies       — framework/language at end-of-life
        no_active_development  — default 0.5

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    # low_usage: requires usage telemetry, default 0.5
    scores["low_usage"] = 0.5

    # redundant_functionality: default 0.5
    scores["redundant_functionality"] = 0.5

    # eol_dependencies: check if framework or language version is EOL
    framework = profile.get("framework", "").lower().strip().replace(" ", "-").replace("_", "-")
    lang = profile.get("primary_language", "")
    lang_ver = profile.get("language_version", "")
    lang_ver_major = lang_ver.split(".")[0] if lang_ver else ""

    is_eol = False
    if framework in EOL_FRAMEWORKS:
        is_eol = True
    if (lang, lang_ver_major) in EOL_LANGUAGES:
        is_eol = True
    # Also check framework + major version combos
    if framework and not is_eol:
        fw_ver = profile.get("framework_version", "")
        fw_ver_major = fw_ver.split(".")[0] if fw_ver else ""
        combined = f"{framework}{fw_ver_major}"
        if combined in EOL_FRAMEWORKS:
            is_eol = True

    scores["eol_dependencies"] = 1.0 if is_eol else 0.0

    # no_active_development: default 0.5
    scores["no_active_development"] = 0.5

    return scores


def _check_retain_fitness(profile):
    """Evaluate retain (keep in place) fitness.

    Criteria:
        stable_operation  — low complexity and good maintainability
        no_ato_blockers   — maintainability > 40 and complexity < 20
        low_risk          — tech debt hours < 100
        recent_framework  — framework is not EOL

    Returns:
        Dict mapping criterion name to float score in [0.0, 1.0].
    """
    scores = {}

    maint = profile.get("maintainability_index", 0)
    avg_cx = profile.get("avg_complexity", 0)
    tech_debt = profile.get("tech_debt_hours", 0)

    # stable_operation: low complexity and good maintainability
    if avg_cx < 10 and maint > 60:
        scores["stable_operation"] = 1.0
    elif avg_cx < 20 and maint > 40:
        scores["stable_operation"] = 0.5
    else:
        scores["stable_operation"] = 0.0

    # no_ato_blockers: maintainability > 40 and complexity < 20
    if maint > 40 and avg_cx < 20:
        scores["no_ato_blockers"] = 1.0
    elif maint > 25 and avg_cx < 30:
        scores["no_ato_blockers"] = 0.5
    else:
        scores["no_ato_blockers"] = 0.0

    # low_risk: tech_debt_hours < 100
    if tech_debt < 100:
        scores["low_risk"] = 1.0
    elif tech_debt < 500:
        scores["low_risk"] = 0.5
    else:
        scores["low_risk"] = 0.0

    # recent_framework: framework is NOT EOL
    framework = profile.get("framework", "").lower().strip().replace(" ", "-").replace("_", "-")
    lang = profile.get("primary_language", "")
    lang_ver = profile.get("language_version", "")
    lang_ver_major = lang_ver.split(".")[0] if lang_ver else ""

    is_eol = False
    if framework in EOL_FRAMEWORKS:
        is_eol = True
    if (lang, lang_ver_major) in EOL_LANGUAGES:
        is_eol = True

    scores["recent_framework"] = 0.0 if is_eol else 1.0

    return scores


# ============================================================================
# Scoring engine
# ============================================================================

def _score_strategy(strategy_id, check_results, catalog_criteria, weights=None):
    """Compute weighted score for a single strategy.

    For each criterion in the catalog, multiply the check_result by the
    criterion's weight.  Sum all weighted scores.  Apply the strategy's
    effort_multiplier as a penalty (higher effort = lower attractiveness).

    The raw weighted sum is in [0.0, 1.0] because criterion weights sum to
    ~1.0 and check results are in [0.0, 1.0].  The effort penalty adjusts
    the final score to favour lower-effort strategies when fitness is equal.

    Args:
        strategy_id:      The strategy identifier (e.g. "rehost").
        check_results:    Dict of {criterion_name: score} from _check_*_fitness.
        catalog_criteria: Dict of {criterion_name: {weight, description}} from
                          the catalog's scoring_criteria for this strategy.
        weights:          Optional dict of custom criterion weights to override
                          catalog defaults.

    Returns:
        Float score in [0.0, 1.0] (normalized).
    """
    if not catalog_criteria:
        return 0.0

    # Build effective weights: start with catalog, override with custom
    effective_weights = {}
    for crit_name, crit_info in catalog_criteria.items():
        effective_weights[crit_name] = crit_info.get("weight", 0.0)

    if weights and strategy_id in weights:
        for crit_name, w in weights[strategy_id].items():
            if crit_name in effective_weights:
                effective_weights[crit_name] = w

    # Calculate weighted sum.  For catalog criteria that have no matching
    # check_result key, we map by positional order (catalog criterion → check
    # result) to bridge the gap between catalog criterion names and the
    # check function criterion names.
    catalog_crit_names = list(catalog_criteria.keys())
    check_crit_names = list(check_results.keys())

    weighted_sum = 0.0
    total_weight = 0.0

    for i, cat_crit in enumerate(catalog_crit_names):
        weight = effective_weights.get(cat_crit, 0.0)
        # Direct name match first
        if cat_crit in check_results:
            score = check_results[cat_crit]
        elif i < len(check_crit_names):
            # Positional fallback: map i-th catalog criterion to i-th check result
            score = check_results[check_crit_names[i]]
        else:
            # No matching check result — assume neutral score
            score = 0.5
        weighted_sum += score * weight
        total_weight += weight

    # Normalize to [0.0, 1.0] if weights don't sum to 1.0
    if total_weight > 0:
        raw_score = weighted_sum / total_weight
    else:
        raw_score = 0.0

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, raw_score))


def _rank_strategies(scores):
    """Sort strategies by score descending and assign ranks.

    Args:
        scores: Dict of {strategy_id: score_float}.

    Returns:
        List of dicts: [{rank, strategy_id, score}, ...] ordered by score desc.
    """
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ranked = []
    for rank_idx, (strategy_id, score) in enumerate(sorted_items, start=1):
        ranked.append({
            "rank": rank_idx,
            "strategy_id": strategy_id,
            "score": round(score, 4),
        })
    return ranked


# ============================================================================
# ATO impact, cost, and timeline estimation
# ============================================================================

def _assess_ato_impact(profile, strategy):
    """Determine the ATO impact level for the recommended strategy.

    ATO impact levels:
        none     — rehost, retain, retire (no system change boundary)
        low      — replatform (minor infra changes)
        medium   — refactor (version change may affect compliance docs)
        high     — rearchitect (new architecture requires new ATO boundary)
        critical — repurchase (completely new system, full new ATO)

    Args:
        profile:  The application profile dict (unused in base impl but
                  available for future refinement).
        strategy: Strategy ID string.

    Returns:
        String: one of 'none', 'low', 'medium', 'high', 'critical'.
    """
    impact_map = {
        "rehost": "none",
        "replatform": "low",
        "refactor": "medium",
        "rearchitect": "high",
        "repurchase": "critical",
        "retire": "none",
        "retain": "none",
    }
    return impact_map.get(strategy, "medium")


def _estimate_cost(profile, strategy, catalog):
    """Estimate migration cost in person-hours.

    Formula: (LOC / 20) * effort_multiplier * complexity_factor

    The complexity factor adjusts for codebase health:
      - maintainability > 60: factor 0.8  (healthy code, faster work)
      - maintainability 30-60: factor 1.0 (normal)
      - maintainability < 30: factor 1.5  (poor code, slower work)

    An additional adjustment is made for high cyclomatic complexity:
      - avg_complexity > 25: +20%
      - avg_complexity > 40: +40%

    Args:
        profile:  Application profile dict.
        strategy: Strategy ID string.
        catalog:  Parsed catalog dict.

    Returns:
        Integer: estimated person-hours.
    """
    loc = max(profile.get("loc_code", 0), profile.get("loc_total", 0), 1)

    # Find effort_multiplier from catalog
    effort_multiplier = 1.0
    for s in catalog.get("strategies", []):
        if s["id"] == strategy:
            effort_multiplier = s.get("effort_multiplier", 1.0)
            break

    # Complexity factor based on maintainability index
    maint = profile.get("maintainability_index", 50)
    if maint > 60:
        complexity_factor = 0.8
    elif maint >= 30:
        complexity_factor = 1.0
    else:
        complexity_factor = 1.5

    # Cyclomatic complexity adjustment
    avg_cx = profile.get("avg_complexity", 0)
    cx_adjustment = 1.0
    if avg_cx > 40:
        cx_adjustment = 1.4
    elif avg_cx > 25:
        cx_adjustment = 1.2

    base_hours = loc / 20.0
    total_hours = base_hours * effort_multiplier * complexity_factor * cx_adjustment

    # Minimum cost: 8 hours (1 day) for any migration activity
    return max(8, int(math.ceil(total_hours)))


def _estimate_timeline(profile, strategy, catalog):
    """Estimate migration timeline in weeks.

    Formula: cost_hours / 40 (1 FTE equivalent per week).
    Minimum 2 weeks for any strategy.

    Also considers the catalog's typical_timeline_weeks as a floor/ceiling
    sanity check.

    Args:
        profile:  Application profile dict.
        strategy: Strategy ID string.
        catalog:  Parsed catalog dict.

    Returns:
        Integer: estimated weeks.
    """
    cost_hours = _estimate_cost(profile, strategy, catalog)
    raw_weeks = cost_hours / 40.0

    # Look up catalog typical timeline for bounds checking
    typical_min = 1
    typical_max = 999
    for s in catalog.get("strategies", []):
        if s["id"] == strategy:
            timeline = s.get("typical_timeline_weeks", {})
            typical_min = timeline.get("min", 1)
            typical_max = timeline.get("max", 999)
            break

    # Apply minimum of 2 weeks
    weeks = max(2, int(math.ceil(raw_weeks)))

    # Clamp to catalog bounds (with some flexibility — allow 50% over max)
    weeks = max(weeks, typical_min)
    weeks = min(weeks, int(typical_max * 1.5))

    return weeks


# ============================================================================
# Tech debt reduction estimator
# ============================================================================

def _estimate_tech_debt_reduction(profile, strategy):
    """Estimate the percentage of technical debt addressed by the strategy.

    Different strategies address different amounts of existing tech debt:
        retain:      0%  — no changes
        rehost:      5%  — minimal infra-related debt resolved
        replatform: 15%  — platform-level debt resolved
        refactor:   50%  — direct code-level debt remediation
        rearchitect: 80% — near-complete rebuild eliminates most debt
        repurchase: 90%  — new system eliminates legacy debt
        retire:    100%  — system removed entirely

    Args:
        profile:  Application profile dict.
        strategy: Strategy ID string.

    Returns:
        Float: percentage of tech debt reduction (0.0 to 100.0).
    """
    reduction_map = {
        "retain": 0.0,
        "rehost": 5.0,
        "replatform": 15.0,
        "refactor": 50.0,
        "rearchitect": 80.0,
        "repurchase": 90.0,
        "retire": 100.0,
    }
    return reduction_map.get(strategy, 0.0)


# ============================================================================
# Risk scoring
# ============================================================================

def _compute_risk_score(profile, strategy, catalog):
    """Compute a risk score in [0.0, 1.0] combining strategy risk, profile
    health, and ATO impact.

    Components (weighted):
      - Strategy inherent risk (from catalog risk_level): 40%
      - Application health risk (inverse of maintainability): 30%
      - ATO impact risk: 20%
      - Dependency risk (count of external deps): 10%

    Args:
        profile:  Application profile dict.
        strategy: Strategy ID string.
        catalog:  Parsed catalog dict.

    Returns:
        Float risk score in [0.0, 1.0].
    """
    # Strategy inherent risk
    strategy_risk = 0.5
    for s in catalog.get("strategies", []):
        if s["id"] == strategy:
            strategy_risk = RISK_LEVEL_MAP.get(s.get("risk_level", "medium"), 0.5)
            break

    # Application health risk: inverse maintainability (0-100 scale)
    maint = profile.get("maintainability_index", 50)
    health_risk = max(0.0, min(1.0, 1.0 - (maint / 100.0)))

    # ATO impact risk
    ato_impact = _assess_ato_impact(profile, strategy)
    ato_risk = ATO_IMPACT_ORDER.get(ato_impact, 2) / 4.0

    # Dependency risk
    ext_deps = profile.get("external_dep_count", 0)
    if ext_deps < 5:
        dep_risk = 0.0
    elif ext_deps <= 15:
        dep_risk = 0.3
    elif ext_deps <= 30:
        dep_risk = 0.6
    else:
        dep_risk = 1.0

    # Weighted combination
    risk = (
        strategy_risk * 0.4
        + health_risk * 0.3
        + ato_risk * 0.2
        + dep_risk * 0.1
    )
    return round(max(0.0, min(1.0, risk)), 4)


# ============================================================================
# Main orchestrator
# ============================================================================

def _get_ui_complexity(app_id, project_id, db_path=None):
    """Query stored UI analysis for a legacy application.

    If tools/modernization/ui_analyzer.py has been run against screenshots
    of this application, the complexity score is stored as JSON in the
    legacy_applications metadata column.

    Args:
        app_id: Legacy application ID.
        project_id: Project ID.
        db_path: Optional database path override.

    Returns:
        Float complexity score (0.0-1.0) or None if no UI analysis exists.
    """
    try:
        conn = _get_db(db_path)
        row = conn.execute(
            "SELECT metadata FROM legacy_applications WHERE id = ?", (app_id,)
        ).fetchone()
        conn.close()

        if row and row["metadata"]:
            metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            ui_analysis = metadata.get("ui_analysis", {})
            if "complexity_score" in ui_analysis:
                return float(ui_analysis["complexity_score"])
    except Exception:
        pass
    return None


def _apply_ui_complexity_adjustment(strategy_scores, ui_complexity):
    """Apply UI complexity adjustment to 7R strategy scores.

    High UI complexity favors Rearchitect, penalizes Rehost.
    Low UI complexity favors Replatform.
    This is an optional dimension (D85 — backward compatible).

    Args:
        strategy_scores: Dict of strategy_id -> score.
        ui_complexity: Float 0.0-1.0.

    Returns:
        Adjusted strategy_scores dict.
    """
    if ui_complexity is None:
        return strategy_scores

    # Weight for UI complexity dimension (10% of total)
    weight = 0.10

    adjustments = {}
    if ui_complexity > 0.7:
        # High UI complexity → favors Rearchitect, penalizes Rehost
        adjustments = {
            "rehost": -weight * 0.5,
            "replatform": -weight * 0.2,
            "rearchitect": weight * 0.5,
            "refactor": weight * 0.3,
        }
    elif ui_complexity < 0.3:
        # Low UI complexity → favors Replatform
        adjustments = {
            "rehost": weight * 0.2,
            "replatform": weight * 0.4,
            "rearchitect": -weight * 0.1,
        }
    else:
        # Moderate UI complexity → slight Refactor bias
        adjustments = {
            "refactor": weight * 0.2,
            "rearchitect": weight * 0.1,
        }

    adjusted = dict(strategy_scores)
    for strategy_id, adj in adjustments.items():
        if strategy_id in adjusted:
            adjusted[strategy_id] = max(0.0, min(1.0, adjusted[strategy_id] + adj))

    return adjusted


def run_seven_r_assessment(project_id, app_id, custom_weights=None, db_path=None):
    """Orchestrate a full 7R assessment for one legacy application.

    Steps:
        1. Load the 7Rs catalog
        2. Build the application profile from legacy_* tables
        3. Run all 7 fitness check functions
        4. Score each strategy against its catalog criteria
        4b. (Optional) Apply UI complexity adjustment if analysis available
        5. Rank strategies by score
        6. Assess ATO impact for recommended (top-ranked) strategy
        7. Estimate cost and timeline
        8. Store results in migration_assessments table (INSERT OR REPLACE)
        9. Return the complete assessment dict

    Args:
        project_id:     The project ID for context.
        app_id:         The legacy application ID to assess.
        custom_weights: Optional dict of custom weights per strategy.
        db_path:        Optional database path override.

    Returns:
        Dict containing the full assessment: scores, ranking, recommendation,
        cost, timeline, ATO impact, evidence, and metadata.
    """
    catalog = load_seven_rs_catalog()
    profile = _get_app_profile(app_id, db_path=db_path)

    # Run all fitness checks
    fitness_results = {
        "rehost": _check_rehost_fitness(profile),
        "replatform": _check_replatform_fitness(profile),
        "refactor": _check_refactor_fitness(profile),
        "rearchitect": _check_rearchitect_fitness(profile),
        "repurchase": _check_repurchase_fitness(profile),
        "retire": _check_retire_fitness(profile),
        "retain": _check_retain_fitness(profile),
    }

    # Build a lookup: strategy_id → scoring_criteria from catalog
    catalog_criteria_map = {}
    for strat in catalog.get("strategies", []):
        catalog_criteria_map[strat["id"]] = strat.get("scoring_criteria", {})

    # Score each strategy
    strategy_scores = {}
    for strategy_id, check_results in fitness_results.items():
        cat_criteria = catalog_criteria_map.get(strategy_id, {})
        strategy_scores[strategy_id] = _score_strategy(
            strategy_id, check_results, cat_criteria, weights=custom_weights
        )

    # Optional: Apply UI complexity adjustment (D85 — backward compatible)
    ui_complexity = _get_ui_complexity(app_id, project_id, db_path=db_path)
    if ui_complexity is not None:
        strategy_scores = _apply_ui_complexity_adjustment(strategy_scores, ui_complexity)

    # Rank strategies
    ranking = _rank_strategies(strategy_scores)
    recommended = ranking[0]["strategy_id"] if ranking else "retain"

    # ATO impact for recommended strategy
    ato_impact = _assess_ato_impact(profile, recommended)

    # Cost and timeline for recommended strategy
    cost_hours = _estimate_cost(profile, recommended, catalog)
    timeline_weeks = _estimate_timeline(profile, recommended, catalog)

    # Risk score for recommended strategy
    risk_score = _compute_risk_score(profile, recommended, catalog)

    # Tech debt reduction for recommended strategy
    tech_debt_reduction = _estimate_tech_debt_reduction(profile, recommended)

    # Compile the scoring weights used
    scoring_weights = {}
    for strat in catalog.get("strategies", []):
        weights_for_strat = {}
        for crit_name, crit_info in strat.get("scoring_criteria", {}).items():
            weights_for_strat[crit_name] = crit_info.get("weight", 0.0)
        scoring_weights[strat["id"]] = weights_for_strat

    if custom_weights:
        for strat_id, overrides in custom_weights.items():
            if strat_id in scoring_weights:
                scoring_weights[strat_id].update(overrides)

    # Compile evidence
    evidence = {
        "fitness_results": {k: {ck: round(cv, 4) for ck, cv in v.items()} for k, v in fitness_results.items()},
        "strategy_scores": {k: round(v, 4) for k, v in strategy_scores.items()},
        "ranking": ranking,
        "profile_summary": {
            "name": profile.get("name", "unknown"),
            "primary_language": profile.get("primary_language"),
            "language_version": profile.get("language_version"),
            "framework": profile.get("framework"),
            "framework_version": profile.get("framework_version"),
            "loc_total": profile.get("loc_total"),
            "loc_code": profile.get("loc_code"),
            "file_count": profile.get("file_count"),
            "component_count": profile.get("component_count"),
            "dependency_count": profile.get("dependency_count"),
            "api_count": profile.get("api_count"),
            "db_schema_count": profile.get("db_schema_count"),
            "complexity_score": profile.get("complexity_score"),
            "maintainability_index": profile.get("maintainability_index"),
            "tech_debt_hours": profile.get("tech_debt_hours"),
            "avg_complexity": round(profile.get("avg_complexity", 0), 2),
            "avg_coupling": round(profile.get("avg_coupling", 0), 2),
            "ui_complexity": round(ui_complexity, 2) if ui_complexity is not None else None,
        },
    }

    # Build the assessment record
    assessment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    assessment = {
        "id": assessment_id,
        "legacy_app_id": app_id,
        "component_id": None,  # app-level assessment
        "assessment_scope": "application",
        "rehost_score": round(strategy_scores.get("rehost", 0.0), 4),
        "replatform_score": round(strategy_scores.get("replatform", 0.0), 4),
        "refactor_score": round(strategy_scores.get("refactor", 0.0), 4),
        "rearchitect_score": round(strategy_scores.get("rearchitect", 0.0), 4),
        "repurchase_score": round(strategy_scores.get("repurchase", 0.0), 4),
        "retire_score": round(strategy_scores.get("retire", 0.0), 4),
        "retain_score": round(strategy_scores.get("retain", 0.0), 4),
        "recommended_strategy": recommended,
        "cost_estimate_hours": cost_hours,
        "risk_score": risk_score,
        "timeline_weeks": timeline_weeks,
        "ato_impact": ato_impact,
        "tech_debt_reduction": tech_debt_reduction,
        "scoring_weights": json.dumps(scoring_weights),
        "evidence": json.dumps(evidence),
        "assessed_at": now,
        "project_id": project_id,
        "ranking": ranking,
    }

    # Persist to database
    _persist_assessment(assessment, db_path=db_path)

    return assessment


def _persist_assessment(assessment, db_path=None):
    """Write the assessment record to migration_assessments via INSERT OR REPLACE.

    Args:
        assessment: Dict with all assessment fields.
        db_path:    Optional database path override.
    """
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO migration_assessments
               (id, legacy_app_id, component_id, assessment_scope,
                rehost_score, replatform_score, refactor_score,
                rearchitect_score, repurchase_score, retire_score,
                retain_score, recommended_strategy, cost_estimate_hours,
                risk_score, timeline_weeks, ato_impact, tech_debt_reduction,
                scoring_weights, evidence, assessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment["id"],
                assessment["legacy_app_id"],
                assessment.get("component_id"),
                assessment["assessment_scope"],
                assessment["rehost_score"],
                assessment["replatform_score"],
                assessment["refactor_score"],
                assessment["rearchitect_score"],
                assessment["repurchase_score"],
                assessment["retire_score"],
                assessment["retain_score"],
                assessment["recommended_strategy"],
                assessment["cost_estimate_hours"],
                assessment["risk_score"],
                assessment["timeline_weeks"],
                assessment["ato_impact"],
                assessment["tech_debt_reduction"],
                assessment["scoring_weights"],
                assessment["evidence"],
                assessment["assessed_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# Decision matrix display
# ============================================================================

def generate_decision_matrix(app_id, db_path=None):
    """Pretty-print a decision matrix with all 7 strategies scored.

    Reads the most recent assessment for the given app from the database.
    If no assessment exists, returns a message indicating so.

    The matrix includes:
      - Strategy name
      - Score (0.0-1.0)
      - Rank
      - Risk level
      - Estimated cost (hours)
      - Timeline (weeks)
      - ATO impact
      - Tech debt reduction

    Ends with the recommended strategy and rationale.

    Args:
        app_id:  The legacy application ID.
        db_path: Optional database path override.

    Returns:
        String containing the formatted decision matrix.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM migration_assessments
               WHERE legacy_app_id = ?
               ORDER BY assessed_at DESC LIMIT 1""",
            (app_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return (
            f"No assessment found for application '{app_id}'.\n"
            "Run an assessment first with: --project-id <PID> --app-id <AID>"
        )

    row_dict = dict(row)
    evidence = json.loads(row_dict.get("evidence", "{}"))
    ranking = evidence.get("ranking", [])
    profile_summary = evidence.get("profile_summary", {})
    fitness_results = evidence.get("fitness_results", {})
    strategy_scores = evidence.get("strategy_scores", {})

    catalog = load_seven_rs_catalog()

    # Build per-strategy detail rows
    strategy_details = []
    for entry in ranking:
        sid = entry["strategy_id"]
        score = entry["score"]
        rank = entry["rank"]

        # Look up catalog info
        cat_info = {}
        for s in catalog.get("strategies", []):
            if s["id"] == sid:
                cat_info = s
                break

        risk_level = cat_info.get("risk_level", "medium")
        ato = _assess_ato_impact(profile_summary, sid)
        cost_h = _estimate_cost(profile_summary if "loc_code" in profile_summary else {"loc_code": profile_summary.get("loc_code", 0), "loc_total": profile_summary.get("loc_total", 0), "maintainability_index": profile_summary.get("maintainability_index", 50), "avg_complexity": profile_summary.get("avg_complexity", 0)}, sid, catalog)
        timeline_w = _estimate_timeline({"loc_code": profile_summary.get("loc_code", 0), "loc_total": profile_summary.get("loc_total", 0), "maintainability_index": profile_summary.get("maintainability_index", 50), "avg_complexity": profile_summary.get("avg_complexity", 0)}, sid, catalog)
        tdr = _estimate_tech_debt_reduction(profile_summary, sid)
        risk_s = _compute_risk_score({"maintainability_index": profile_summary.get("maintainability_index", 50), "external_dep_count": profile_summary.get("dependency_count", 0)}, sid, catalog)

        strategy_details.append({
            "rank": rank,
            "name": cat_info.get("name", sid.title()),
            "id": sid,
            "score": score,
            "risk_level": risk_level,
            "risk_score": risk_s,
            "cost_hours": cost_h,
            "timeline_weeks": timeline_w,
            "ato_impact": ato,
            "tech_debt_reduction": tdr,
        })

    # Format the matrix
    lines = []
    lines.append("=" * 100)
    lines.append("CUI // SP-CTI")
    lines.append("=" * 100)
    lines.append("")
    lines.append("7R MIGRATION STRATEGY DECISION MATRIX")
    lines.append(f"Application: {profile_summary.get('name', app_id)}")
    lines.append(f"Language:    {profile_summary.get('primary_language', 'N/A')} {profile_summary.get('language_version', '')}")
    lines.append(f"Framework:   {profile_summary.get('framework', 'N/A')} {profile_summary.get('framework_version', '')}")
    lines.append(f"LOC:         {profile_summary.get('loc_total', 'N/A'):,}" if isinstance(profile_summary.get('loc_total'), (int, float)) else f"LOC:         {profile_summary.get('loc_total', 'N/A')}")
    lines.append(f"Components:  {profile_summary.get('component_count', 'N/A')}")
    lines.append(f"Assessed:    {row_dict.get('assessed_at', 'N/A')}")
    lines.append("")
    lines.append("-" * 100)

    # Table header
    header = f"{'Rank':<6}{'Strategy':<15}{'Score':<9}{'Risk':<10}{'Cost (hrs)':<12}{'Timeline':<12}{'ATO Impact':<14}{'Debt Reduction':<15}"
    lines.append(header)
    lines.append("-" * 100)

    # Table rows
    for sd in strategy_details:
        marker = " <<" if sd["rank"] == 1 else ""
        row_str = (
            f"{sd['rank']:<6}"
            f"{sd['name']:<15}"
            f"{sd['score']:<9.4f}"
            f"{sd['risk_level']:<10}"
            f"{sd['cost_hours']:<12,}"
            f"{sd['timeline_weeks']:<10} wk  "
            f"{sd['ato_impact']:<14}"
            f"{sd['tech_debt_reduction']:<10.0f}%"
            f"{marker}"
        )
        lines.append(row_str)

    lines.append("-" * 100)
    lines.append("")

    # Recommendation section
    recommended = row_dict.get("recommended_strategy", "unknown")
    rec_details = None
    for sd in strategy_details:
        if sd["id"] == recommended:
            rec_details = sd
            break

    lines.append("RECOMMENDATION")
    lines.append(f"  Strategy:        {rec_details['name'] if rec_details else recommended.title()}")
    lines.append(f"  Score:           {rec_details['score']:.4f}" if rec_details else "  Score:           N/A")
    lines.append(f"  Risk:            {rec_details['risk_level'] if rec_details else 'N/A'} ({rec_details['risk_score']:.2f})" if rec_details else "")
    lines.append(f"  Estimated Cost:  {rec_details['cost_hours']:,} hours" if rec_details else "  Estimated Cost:  N/A")
    lines.append(f"  Timeline:        {rec_details['timeline_weeks']} weeks" if rec_details else "  Timeline:        N/A")
    lines.append(f"  ATO Impact:      {rec_details['ato_impact']}" if rec_details else "  ATO Impact:      N/A")
    lines.append(f"  Debt Reduction:  {rec_details['tech_debt_reduction']:.0f}%" if rec_details else "  Debt Reduction:  N/A")
    lines.append("")

    # Rationale
    lines.append("RATIONALE")
    if rec_details:
        # Build a short rationale from the fitness scores
        fit = fitness_results.get(recommended, {})
        high_fit = [k for k, v in fit.items() if v >= 0.8]
        low_fit = [k for k, v in fit.items() if v <= 0.2]

        if high_fit:
            lines.append(f"  Strengths:   {', '.join(high_fit)}")
        if low_fit:
            lines.append(f"  Weaknesses:  {', '.join(low_fit)}")

        # Compare top two
        if len(strategy_details) >= 2:
            second = strategy_details[1]
            delta = rec_details["score"] - second["score"]
            lines.append(
                f"  Margin:      {recommended} leads {second['id']} by "
                f"{delta:.4f} ({delta * 100:.1f}%)"
            )
            if delta < 0.05:
                lines.append(
                    "  NOTE:        Scores are very close. Manual review of "
                    "business context and team capacity is recommended."
                )
    else:
        lines.append("  No detailed rationale available.")

    lines.append("")

    # Fitness detail section
    lines.append("FITNESS BREAKDOWN")
    lines.append("-" * 100)
    for sid in ["rehost", "replatform", "refactor", "rearchitect", "repurchase", "retire", "retain"]:
        fit = fitness_results.get(sid, {})
        if not fit:
            continue
        score = strategy_scores.get(sid, 0.0)
        criteria_strs = [f"{k}={v:.1f}" for k, v in fit.items()]
        lines.append(f"  {sid:<14} (score: {score:.4f}): {', '.join(criteria_strs)}")
    lines.append("-" * 100)

    lines.append("")
    lines.append("CUI // SP-CTI")
    lines.append("=" * 100)

    return "\n".join(lines)


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    """CLI entry point for the 7R assessment engine.

    Arguments:
        --project-id  (required): Project identifier
        --app-id      (required): Legacy application identifier
        --weights     (optional): Path to custom weights JSON file
        --json        (flag):     Output raw assessment as JSON
        --matrix      (flag):     Print the decision matrix table
    """
    parser = argparse.ArgumentParser(
        description=(
            "7R Migration Strategy Recommendation Engine — "
            "Evaluates legacy applications against 7 cloud migration strategies "
            "and recommends the optimal path for DoD/GovCloud environments."
        ),
        epilog="CUI // SP-CTI",
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Project identifier (e.g., P-001)",
    )
    parser.add_argument(
        "--app-id",
        required=True,
        help="Legacy application identifier (e.g., A-001)",
    )
    parser.add_argument(
        "--weights",
        default=None,
        help="Path to custom weights JSON file (overrides catalog defaults)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output the assessment as JSON",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Print the decision matrix table",
    )

    args = parser.parse_args()

    # Load custom weights if provided
    custom_weights = None
    if args.weights:
        weights_path = Path(args.weights)
        if not weights_path.exists():
            print(f"ERROR: Weights file not found: {weights_path}", file=sys.stderr)
            sys.exit(1)
        with open(weights_path, "r", encoding="utf-8") as fh:
            custom_weights = json.load(fh)

    # Run the assessment
    try:
        assessment = run_seven_r_assessment(
            project_id=args.project_id,
            app_id=args.app_id,
            custom_weights=custom_weights,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Output
    if args.output_json:
        # Serialise — strip non-serializable fields
        output = dict(assessment)
        # ranking is already a list of dicts, safe to serialize
        output["scoring_weights"] = json.loads(output["scoring_weights"]) if isinstance(output["scoring_weights"], str) else output["scoring_weights"]
        output["evidence"] = json.loads(output["evidence"]) if isinstance(output["evidence"], str) else output["evidence"]
        print(json.dumps(output, indent=2, default=str))
    elif args.matrix:
        matrix_output = generate_decision_matrix(args.app_id)
        print(matrix_output)
    else:
        # Default: summary output
        print("=" * 60)
        print("CUI // SP-CTI")
        print("=" * 60)
        print(f"7R Assessment Complete — {assessment['legacy_app_id']}")
        print(f"  Assessment ID:       {assessment['id']}")
        print(f"  Recommended Strategy: {assessment['recommended_strategy'].upper()}")
        print(f"  Score:               {assessment.get(assessment['recommended_strategy'] + '_score', 'N/A')}")
        print(f"  Risk Score:          {assessment['risk_score']}")
        print(f"  Cost Estimate:       {assessment['cost_estimate_hours']:,} hours")
        print(f"  Timeline:            {assessment['timeline_weeks']} weeks")
        print(f"  ATO Impact:          {assessment['ato_impact']}")
        print(f"  Tech Debt Reduction: {assessment['tech_debt_reduction']:.0f}%")
        print()
        print("Strategy Rankings:")
        for entry in assessment["ranking"]:
            marker = " << RECOMMENDED" if entry["rank"] == 1 else ""
            print(f"  #{entry['rank']}  {entry['strategy_id']:<14} {entry['score']:.4f}{marker}")
        print()
        print("Run with --matrix for full decision matrix.")
        print("Run with --json for machine-readable output.")
        print("=" * 60)
        print("CUI // SP-CTI")
        print("=" * 60)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
