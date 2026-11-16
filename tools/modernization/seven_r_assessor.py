# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""7R Migration Strategy Recommendation Engine for ICDEV™ DoD Modernization.

This is THE 7R engine (icx-mdz-02): ``migration_intelligence/strategy_generator.py`` and
``migration_canvas/coa_engine.py`` consume its assessment (``strategy_for_app``) and never score 7R themselves.
Every criterion is the catalog's, measured by name or unmeasured (None); an unmeasured criterion is left out of the
score and lowers the strategy's reported confidence; a strategy with nothing measured is not assessable; and only a
strategy whose confidence reaches the catalog's ``min_confidence_to_recommend`` is recommended.

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
import os
import sys
import uuid
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_backend, get_connection
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH") or BASE_DIR / "data" / "icdev.db")  # the rule tools/db/storage.py follows
CATALOG_PATH = BASE_DIR / "context" / "modernization" / "seven_rs_catalog.json"

# ---------------------------------------------------------------------------
# Known EOL frameworks / languages for fitness checks
# ---------------------------------------------------------------------------
EOL_FRAMEWORKS = {
    "struts",
    "struts2",
    "ejb2",
    "jsf",
    "jsf1",
    "spring2",
    "spring3",
    "wcf",
    "webforms",
    "aspnet-webforms",
    "silverlight",
    "django1",
    "flask0",
    "rails3",
    "rails4",
    "angularjs",
    ".net-framework",
    "dotnet-framework",
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
    # The file is SQLite's; on PostgreSQL (the primary backend) there is no file to find (icx-mdz-01).
    if get_backend() == "sqlite" and not Path(path).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
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
            f"Seven Rs catalog not found: {path}\nExpected at: context/modernization/seven_rs_catalog.json"
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
        row = conn.execute("SELECT * FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
        if row is None:
            raise ValueError(f"Application '{app_id}' not found in legacy_applications.")
        profile = dict(row)

        # --- Component aggregates ---
        comp_rows = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = %s",
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
            "SELECT * FROM legacy_dependencies WHERE legacy_app_id = %s",
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
        profile["external_dep_count"] = (
            dep_type_counts.get("external", 0)
            + dep_type_counts.get("third_party", 0)
            + dep_type_counts.get("library", 0)
        )

        # --- API aggregates ---
        api_rows = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = %s",
            (app_id,),
        ).fetchall()
        profile["apis"] = [dict(r) for r in api_rows]
        profile["api_count"] = len(api_rows)

        # --- DB schema aggregates ---
        db_rows = conn.execute(
            "SELECT * FROM legacy_db_schemas WHERE legacy_app_id = %s",
            (app_id,),
        ).fetchall()
        db_schemas = [dict(r) for r in db_rows]
        profile["db_schemas"] = db_schemas
        profile["db_schema_count"] = len(db_schemas)
        profile["db_types"] = list(set((s.get("db_type") or "unknown").lower() for s in db_schemas))

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
# Fitness check functions -- one per strategy, keyed BY NAME by the catalog's own criteria (icx-mdz-02)
# ============================================================================
#
# Every check returns exactly the criteria its strategy declares in seven_rs_catalog.json, by name. A criterion this
# tool can measure from the application's profile is a float in [0.0, 1.0]; one it cannot (team skills, licences,
# costs, sign-offs, an extraction that never ran) is None -- UNMEASURED. ``_strategy_evidence`` leaves an unmeasured
# criterion out of the score and says so; it never stands in a neutral 0.5. A check whose names differ from the
# catalog's is refused there (CriteriaMismatch), so nothing is ever paired by list position again.

#: Analysis states in which the analyser's component, dependency and metric figures were measured.
ANALYSED_STATUSES = {"analyzed", "planning", "migrating", "completed"}

#: Markers of host coupling in a dependency (its type or the import it records): local files, processes, native code.
HOST_COUPLING_MARKERS = (
    "os-specific", "hardware", "native", "driver", "kernel", "file-io", "local-storage", "nfs", "smb", "cifs",
    "shared-drive", "java.io.file", "java.nio.file", "jni", "com.sun.jna", "ctypes", "subprocess", "os.system",
    "system.io", "microsoft.win32", "runtime.getruntime", "processbuilder",
)


def _analysed(profile):
    """True when the analyser ran over the source, so its figures (components, dependencies, maintainability,
    technical debt, test components) are measurements; a registered-only application has none of them."""
    status = str(profile.get("analysis_status") or "").lower()
    return status in ANALYSED_STATUSES and int(profile.get("component_count") or 0) > 0


def _band(value, good, fair, *, higher_is_better=True):
    """1.0 at or past ``good``, 0.5 at or past ``fair``, else 0.0 (a measured partial, never a default)."""
    if higher_is_better:
        return 1.0 if value >= good else 0.5 if value >= fair else 0.0
    return 1.0 if value <= good else 0.5 if value <= fair else 0.0


def _db_types(profile):
    """The database engines the schema extraction found, or None when no schema was extracted (no tables is not the
    same as no database: the extractor may not have run)."""
    if not int(profile.get("db_schema_count") or 0):
        return None
    types = {t for t in (profile.get("db_types") or []) if t and t != "unknown"}
    return types or None


def _managed_db_fit(profile):
    """How directly the extracted database engine maps to a managed service: standard 1.0, a commercial engine with a
    known migration path 0.5, an exotic engine 0.0; an engine none of the lists knows, or no schema, is unmeasured."""
    types = _db_types(profile)
    if types is None:
        return None
    if types <= STANDARD_DB_TYPES:
        return 1.0
    if types & EXOTIC_DB_TYPES:
        return 0.0
    if types <= STANDARD_DB_TYPES | MIGRATABLE_DB_TYPES:
        return 0.5
    return None


def _table_count(profile):
    tables = {s.get("table_name") for s in profile.get("db_schemas") or [] if s.get("table_name")}
    return len(tables) if tables else None


def _eol(profile):
    """True / False when the framework or language level is known, None when neither is (an EOL check over an
    unknown stack would be a guess)."""
    framework = (profile.get("framework") or "").lower().strip().replace(" ", "-").replace("_", "-")
    lang = profile.get("primary_language", "")
    lang_ver = profile.get("language_version") or ""
    lang_ver_major = lang_ver.split(".")[0] if lang_ver else ""
    known_framework = bool(framework) and framework != "unknown"
    if not known_framework and not lang_ver:
        return None
    if framework in EOL_FRAMEWORKS or (lang, lang_ver_major) in EOL_LANGUAGES:
        return True
    if known_framework:
        fw_ver = profile.get("framework_version") or ""
        fw_ver_major = fw_ver.split(".")[0] if fw_ver else ""
        if f"{framework}{fw_ver_major}" in EOL_FRAMEWORKS:
            return True
    return False


def _check_rehost_fitness(profile):
    """Rehost (lift and shift), keyed by the catalog's rehost criteria.

    Criteria:
        infrastructure_compatibility -- dependencies coupled to the host (local files, processes, native code:
                                        HOST_COUPLING_MARKERS): none 1.0, up to 3 0.5, more 0.0; measured when analysed
        dependency_portability       -- the extracted database engine's managed-service fit (``_managed_db_fit``)
        data_migration_complexity    -- extracted tables: up to 10 1.0, up to 50 0.5, more 0.0 (volume and CUI are not
                                        known here, so this is the schema's size only)
        licensing_transferability, operational_readiness -- licence terms and the team's cloud operations: None
    """
    coupled = None
    if _analysed(profile):
        coupled = sum(1 for d in profile.get("dependencies") or []
                      if any(m in f"{d.get('dependency_type') or ''} {d.get('evidence') or ''}".lower()
                             for m in HOST_COUPLING_MARKERS))
    tables = _table_count(profile)
    return {
        "infrastructure_compatibility": None if coupled is None else _band(coupled, 0, 3, higher_is_better=False),
        "dependency_portability": _managed_db_fit(profile),
        "licensing_transferability": None,
        "data_migration_complexity": None if tables is None else _band(tables, 10, 50, higher_is_better=False),
        "operational_readiness": None,
    }


def _check_replatform_fitness(profile):
    """Replatform (lift, tinker and shift), keyed by the catalog's replatform criteria.

    Criteria:
        managed_service_availability -- the extracted database engine's managed-service fit (``_managed_db_fit``)
        modification_scope           -- technical-debt hours per 100 lines and the maintainability index: under 5 and
                                        over 50 1.0, under 15 and over 30 0.5, else 0.0; measured when analysed
        operational_improvement, cost_optimization, security_posture_change -- run costs, the operating model and the
                                        control baseline are not known here: None
    """
    scope = None
    if _analysed(profile):
        maint = profile.get("maintainability_index", 0)
        density = profile.get("tech_debt_hours", 0) / max(profile.get("loc_total", 1) / 100.0, 1.0)
        scope = 1.0 if density < 5 and maint > 50 else 0.5 if density < 15 and maint > 30 else 0.0
    return {
        "managed_service_availability": _managed_db_fit(profile),
        "modification_scope": scope,
        "operational_improvement": None,
        "cost_optimization": None,
        "security_posture_change": None,
    }


def _check_refactor_fitness(profile):
    """Refactor (code-level modernisation), keyed by the catalog's refactor criteria.

    Criteria:
        code_modularity          -- the maintainability index (which folds in coupling and complexity): over 50 1.0,
                                    25 or more 0.5, else 0.0; measured when analysed
        test_coverage            -- the share of test components: 20% or more 1.0, any 0.5, none 0.0; when analysed
        technical_debt_reduction -- a known upgrade path for the language level or the framework 1.0; a level or a
                                    framework known with no known path 0.3; neither known: None
        cloud_service_adoption, team_capability, compliance_alignment -- not measurable from the source here: None
    """
    analysed = _analysed(profile)
    lang = profile.get("primary_language", "")
    lang_ver = profile.get("language_version", "")
    framework = profile.get("framework", "").lower().strip().replace(" ", "-").replace("_", "-")
    known_framework = bool(framework) and framework != "unknown"
    if (lang, lang_ver.split(".")[0] if lang_ver else "") in KNOWN_UPGRADE_PATHS or (
            framework in KNOWN_FRAMEWORK_MIGRATIONS):
        debt = 1.0
    elif lang_ver or known_framework:
        debt = 0.3
    else:
        debt = None
    test_ratio = profile.get("test_component_ratio", 0.0)
    maint = profile.get("maintainability_index", 0)
    return {
        "code_modularity": (1.0 if maint > 50 else 0.5 if maint >= 25 else 0.0) if analysed else None,
        "cloud_service_adoption": None,
        "test_coverage": (1.0 if test_ratio >= 0.20 else 0.5 if test_ratio > 0.0 else 0.0) if analysed else None,
        "team_capability": None,
        "technical_debt_reduction": debt,
        "compliance_alignment": None,
    }


def _check_rearchitect_fitness(profile):
    """Rearchitect (rebuild cloud-native), keyed by the catalog's rearchitect criteria.

    Every rearchitect criterion -- the architectural gap to the target, mission criticality, scalability needs, the
    team's readiness, the capacity for a new ATO and the five-year return -- needs a target architecture, mission
    data or people this tool does not have, so each is None and rearchitect is NOT ASSESSABLE here. (The old check
    scored bounded contexts, API and data-store counts under other names, paired with these criteria by position, and
    two inputs it could not measure as 0.5.) Decomposition feasibility is monolith_decomposer's, not a 7R score.
    """
    return {
        "architectural_gap": None,
        "mission_criticality": None,
        "scalability_requirements": None,
        "team_readiness": None,
        "ato_reassessment_capacity": None,
        "long_term_roi": None,
    }


def _check_repurchase_fitness(profile):
    """Repurchase (buy a COTS/GOTS replacement), keyed by the catalog's repurchase criteria.

    Criteria:
        data_migration_feasibility -- the extracted database engine's managed-service fit (``_managed_db_fit``)
        product_fit, fedramp_authorization, total_cost_of_ownership, vendor_viability -- need a candidate product and
                                      its authorisation, costs and vendor: None (the old check scored three of these
                                      as 0.5 with no product in view)
    """
    return {
        "product_fit": None,
        "fedramp_authorization": None,
        "data_migration_feasibility": _managed_db_fit(profile),
        "total_cost_of_ownership": None,
        "vendor_viability": None,
    }


def _check_retire_fitness(profile, redundancy=None):
    """Evaluate retire (decommission) fitness, keyed by the catalog's own retire criteria (icx-rtr-01).

    A criterion with no evidence is ``None`` -- UNMEASURED -- and ``_score_strategy`` leaves it out of the score
    and says so; it is never a neutral 0.5 (the old defaults made every unmeasured application look half-retirable,
    and their names matched no catalog criterion, so they were scored POSITIONALLY as other criteria).

    Criteria:
        functional_redundancy -- the share of this application's features another application also provides, as
                                 ICDEV[OS] MEASURED it and exported it signed (``redundancy``: ``plan_gate.redundancy``,
                                 icx-mdz-04); unmeasured, unreported, missing or untrusted there: None
        risk_reduction        -- removing it removes end-of-life dependencies (framework/language at EOL): 1.0/0.0;
                                 None when neither the framework nor the language level is known (icx-mdz-02)
        dependency_analysis, data_disposition, cost_savings, stakeholder_alignment -- need a dependency map, a
                                 records plan, cost data and stakeholder sign-off this tool does not have: None

    Args:
        profile:    the application profile (legacy_* tables).
        redundancy: optional ``{"share": float in [0, 1] | None, "state": str, "source": str}``
                    (``plan_gate.redundancy``); a None share is unmeasured, never a default.

    Returns:
        Dict mapping criterion name to a float in [0.0, 1.0], or None when unmeasured.
    """
    is_eol = _eol(profile)
    share = None
    if isinstance(redundancy, dict) and redundancy.get("share") is not None:
        share = max(0.0, min(1.0, float(redundancy["share"])))
    return {
        "dependency_analysis": None,
        "data_disposition": None,
        "cost_savings": None,
        "risk_reduction": None if is_eol is None else (1.0 if is_eol else 0.0),
        "stakeholder_alignment": None,
        "functional_redundancy": share,
    }


def icos_redundancy(app_id, export=None, jwks=None):
    """Retire's ``functional_redundancy`` input: ``app_id``'s redundancy as ICDEV[OS] measured it, from its SIGNED
    decisions export verified against the pinned JWKS (icx-mdz-04; ``plan_gate.redundancy``). An unsigned file is no
    source: a share anyone could write would decide retire. Missing, untrusted, unmeasured or unreported: share None."""
    from tools.modernization import plan_gate

    return plan_gate.redundancy(app_id, export=export, jwks=jwks)


def _check_retain_fitness(profile):
    """Retain (keep in place), keyed by the catalog's retain criteria.

    Criteria:
        current_environment_viability -- the framework and language level are not end-of-life 1.0, are 0.0; neither
                                         known: None
        migration_blocker_validity, cost_justification, reassessment_plan, risk_acceptance -- documented blockers, a
                                         cost analysis, a reassessment plan and the AO's risk acceptance: None
    """
    is_eol = _eol(profile)
    return {
        "migration_blocker_validity": None,
        "current_environment_viability": None if is_eol is None else (0.0 if is_eol else 1.0),
        "cost_justification": None,
        "reassessment_plan": None,
        "risk_acceptance": None,
    }


FITNESS_CHECKS = {
    "rehost": _check_rehost_fitness,
    "replatform": _check_replatform_fitness,
    "refactor": _check_refactor_fitness,
    "rearchitect": _check_rearchitect_fitness,
    "repurchase": _check_repurchase_fitness,
    "retire": _check_retire_fitness,
    "retain": _check_retain_fitness,
}


# ============================================================================
# Scoring engine
# ============================================================================


class CriteriaMismatch(ValueError):
    """A check's criterion names are not exactly its strategy's catalog criteria (or a custom weight names one the
    catalog does not declare). Refused: a criterion is scored by its name, never paired by list position."""


def min_confidence(catalog=None):
    """The confidence a strategy needs before it may be recommended (the catalog's ``assessment`` block)."""
    cat = catalog if catalog is not None else load_seven_rs_catalog()
    return float(((cat.get("assessment") or {}).get("min_confidence_to_recommend")) or 0.5)


def _strategy_evidence(strategy_id, check_results, catalog_criteria, weights=None):
    """Score one strategy from its checks, BY NAME, and say how much of it rests on measurement (icx-mdz-02).

    * the check's criteria must be exactly the catalog's (``CriteriaMismatch`` otherwise: there is no positional
      fallback, and a criterion the catalog does not declare is never scored);
    * ``score`` is the weighted mean of the MEASURED criteria only, in [0.0, 1.0]; an unmeasured (None) criterion is
      left out, never scored 0.5;
    * ``confidence`` is the share of the strategy's criterion weight that was measured, in [0.0, 1.0];
    * with no measured criterion, ``score`` is None and ``assessable`` False: not assessable, not a score.

    Custom ``weights`` (``{strategy: {criterion: weight}}``) override the catalog's, and must name its criteria.
    """
    declared, measured_names = set(catalog_criteria or {}), set(check_results or {})
    if declared != measured_names:
        raise CriteriaMismatch(
            f"{strategy_id}: the catalog declares {sorted(declared - measured_names)} which the check does not "
            f"return, and the check returns {sorted(measured_names - declared)} which the catalog does not declare")
    effective = {name: float((info or {}).get("weight", 0.0)) for name, info in (catalog_criteria or {}).items()}
    overrides = (weights or {}).get(strategy_id) or {}
    unknown = sorted(set(overrides) - declared)
    if unknown:
        raise CriteriaMismatch(f"{strategy_id}: custom weights name {unknown}, which the catalog does not declare")
    effective.update({name: float(w) for name, w in overrides.items()})

    total = sum(effective.values())
    measured = {n: float(s) for n, s in check_results.items() if s is not None}
    measured_weight = sum(effective[n] for n in measured)
    score = None
    if measured and measured_weight > 0:
        score = max(0.0, min(1.0, sum(s * effective[n] for n, s in measured.items()) / measured_weight))
    confidence = (measured_weight / total) if total > 0 else 0.0
    return {
        "strategy": strategy_id,
        "score": None if score is None else round(score, 4),
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "assessable": score is not None,
        "measured": sorted(measured),
        "unmeasured": sorted(n for n, s in check_results.items() if s is None),
        "weights": {n: round(w, 4) for n, w in sorted(effective.items())},
    }


def _score_strategy(strategy_id, check_results, catalog_criteria, weights=None):
    """The strategy's score from its MEASURED criteria by name (``_strategy_evidence``), or None when not assessable."""
    return _strategy_evidence(strategy_id, check_results, catalog_criteria, weights=weights)["score"]


def _rank_strategies(evidence, min_conf, order=None):
    """Rank strategies on their evidence (icx-mdz-02).

    Strategies whose confidence reaches ``min_conf`` come first, by score (then confidence, then catalog order); then
    the assessable ones below it, by score; then the not-assessable ones, which have no score. Only the first group
    may be recommended (``eligible``).

    Args:
        evidence: ``{strategy_id: _strategy_evidence(...)}``.
        min_conf: the confidence a strategy needs to be recommended.
        order:    the catalog's strategy order, for ties.

    Returns:
        ``[{rank, strategy_id, score, confidence, assessable, eligible}, ...]``.
    """
    pos = {s: i for i, s in enumerate(order or list(evidence))}

    def key(item):
        sid, ev = item
        eligible = ev["assessable"] and ev["confidence"] >= min_conf
        group = 0 if eligible else 1 if ev["assessable"] else 2
        return (group, -(ev["score"] or 0.0), -ev["confidence"], pos.get(sid, len(pos)))

    ranked = []
    for rank_idx, (sid, ev) in enumerate(sorted(evidence.items(), key=key), start=1):
        ranked.append({"rank": rank_idx, "strategy_id": sid, "score": ev["score"], "confidence": ev["confidence"],
                       "assessable": ev["assessable"],
                       "eligible": bool(ev["assessable"] and ev["confidence"] >= min_conf)})
    return ranked


def _recommendation(ranking, evidence, min_conf):
    """The recommended strategy and why, or why there is none: never a strategy below the minimum confidence."""
    top = next((r for r in ranking if r["eligible"]), None)
    if top is not None:
        sid = top["strategy_id"]
        ev = evidence[sid]
        return {"status": "recommended", "strategy": sid, "score": ev["score"], "confidence": ev["confidence"],
                "min_confidence": min_conf, "unmeasured": ev["unmeasured"],
                "reason": f"{sid} scores {ev['score']:.2f} on {len(ev['measured'])} measured criteria "
                          f"(confidence {ev['confidence']:.2f})"}
    assessable = [r["strategy_id"] for r in ranking if r["assessable"]]
    return {"status": "insufficient_evidence", "strategy": None, "score": None, "confidence": None,
            "min_confidence": min_conf, "unmeasured": [],
            "reason": (f"no strategy reaches confidence {min_conf:.2f}: "
                       + (f"{', '.join(assessable)} rest on too few measured criteria" if assessable
                          else "no criterion of any strategy was measured")
                       + "; record what is missing (licences, team, costs, sign-offs) or decide on other evidence")}


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
    risk = strategy_risk * 0.4 + health_risk * 0.3 + ato_risk * 0.2 + dep_risk * 0.1
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
        row = conn.execute("SELECT metadata FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
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
        if adjusted.get(strategy_id) is not None:   # a not-assessable strategy gets no score from a nudge (icx-mdz-02)
            adjusted[strategy_id] = max(0.0, min(1.0, adjusted[strategy_id] + adj))

    return adjusted


def run_seven_r_assessment(project_id, app_id, custom_weights=None, db_path=None, redundancy=None):
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
        redundancy:     Optional measured redundancy for this app, ``{"share", "state", "source"}``
                        (``icos_redundancy``: ICDEV[OS]'s signed export); a None share is unmeasured.

    Returns:
        Dict containing the full assessment: scores, ranking, recommendation,
        cost, timeline, ATO impact, evidence, and metadata.
    """
    catalog = load_seven_rs_catalog()
    profile = _get_app_profile(app_id, db_path=db_path)
    min_conf = min_confidence(catalog)

    # icx-mdz-04: with none given, retire reads ICDEV[OS]'s measured redundancy from the export the operator configured
    # (ICDEV_ICOS_DECISIONS / ICDEV_ICOS_JWKS); none configured, untrusted or unmeasured: share None, never a default
    if redundancy is None:
        redundancy = icos_redundancy(app_id)

    # Run all fitness checks: each keyed by its strategy's catalog criteria, None where unmeasured (icx-mdz-02)
    fitness_results = {sid: (check(profile, redundancy=redundancy) if sid == "retire" else check(profile))
                       for sid, check in FITNESS_CHECKS.items()}

    # Build a lookup: strategy_id -> scoring_criteria from catalog
    catalog_criteria_map = {strat["id"]: strat.get("scoring_criteria", {}) for strat in catalog.get("strategies", [])}
    order = [strat["id"] for strat in catalog.get("strategies", [])]

    # Score each strategy BY NAME: a mismatch is refused, an unmeasured criterion lowers confidence, never scores 0.5
    strategy_evidence = {sid: _strategy_evidence(sid, checks, catalog_criteria_map.get(sid, {}), weights=custom_weights)
                         for sid, checks in fitness_results.items()}

    # Optional: Apply UI complexity adjustment (D85 -- backward compatible), to assessable strategies only
    ui_complexity = _get_ui_complexity(app_id, project_id, db_path=db_path)
    if ui_complexity is not None:
        adjusted = _apply_ui_complexity_adjustment({k: v["score"] for k, v in strategy_evidence.items()},
                                                   ui_complexity)
        for sid, score in adjusted.items():
            if score is not None:
                strategy_evidence[sid]["score"] = round(score, 4)
    strategy_scores = {k: v["score"] for k, v in strategy_evidence.items()}

    # Rank on evidence; recommend only a strategy whose confidence reaches the catalog's minimum
    ranking = _rank_strategies(strategy_evidence, min_conf, order=order)
    recommendation = _recommendation(ranking, strategy_evidence, min_conf)
    recommended = recommendation["strategy"]

    # ATO impact, cost, timeline, risk and debt reduction describe the recommended strategy; with none, none
    ato_impact = _assess_ato_impact(profile, recommended) if recommended else None
    cost_hours = _estimate_cost(profile, recommended, catalog) if recommended else None
    timeline_weeks = _estimate_timeline(profile, recommended, catalog) if recommended else None
    risk_score = _compute_risk_score(profile, recommended, catalog) if recommended else None
    tech_debt_reduction = _estimate_tech_debt_reduction(profile, recommended) if recommended else None

    # The weights each strategy was scored with (custom overrides were checked against the catalog's names)
    scoring_weights = {sid: ev["weights"] for sid, ev in strategy_evidence.items()}

    # Compile evidence
    evidence = {
        "fitness_results": {k: {ck: (None if cv is None else round(cv, 4)) for ck, cv in v.items()}
                            for k, v in fitness_results.items()},
        # icx-rtr-01: what each strategy's score could NOT rest on, by name (left out of the score, never 0.5)
        "unmeasured": {k: sorted(ck for ck, cv in v.items() if cv is None) for k, v in fitness_results.items()
                       if any(cv is None for cv in v.values())},
        # icx-mdz-02: per strategy, its score from measured criteria, its confidence and whether it is assessable
        "strategies": {k: {f: v[f] for f in ("score", "confidence", "assessable", "measured", "unmeasured")}
                       for k, v in strategy_evidence.items()},
        "recommendation": recommendation,
        "method": "icx-mdz-02: criteria by name; unmeasured left out of the score and out of the confidence",
        "redundancy": redundancy,
        "strategy_scores": strategy_scores,
        "ranking": ranking,
        "profile_summary": {
            "name": profile.get("name", "unknown"),
            "analysis_status": profile.get("analysis_status"),
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
        # NULL for a strategy that is not assessable: no score, never 0.0 (icx-mdz-02)
        "rehost_score": strategy_scores.get("rehost"),
        "replatform_score": strategy_scores.get("replatform"),
        "refactor_score": strategy_scores.get("refactor"),
        "rearchitect_score": strategy_scores.get("rearchitect"),
        "repurchase_score": strategy_scores.get("repurchase"),
        "retire_score": strategy_scores.get("retire"),
        "retain_score": strategy_scores.get("retain"),
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
        "recommendation": recommendation,
        "confidence": recommendation["confidence"],
        "strategies": evidence["strategies"],
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
# Reading an assessment back (the one shape every consumer uses, icx-mdz-02)
# ============================================================================

STRATEGY_IDS = ("rehost", "replatform", "refactor", "rearchitect", "repurchase", "retire", "retain")


def summarise(row):
    """The evidence an assessment row carries, in the shape the page, the MCP tool, the twin and the other 7R
    consumers read: the recommendation with its confidence, and per strategy its score, confidence, whether it is
    assessable and what was not measured.

    A row assessed before icx-mdz-02 has no per-strategy confidence (its criteria were paired by position);
    ``method`` is then ``"positional (before icx-mdz-02)"`` and every confidence is None -- re-assess it.
    """
    row = dict(row or {})
    ev = row.get("evidence")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev or "{}")
        except ValueError:
            ev = {}
    ev = ev if isinstance(ev, dict) else {}
    strategies = ev.get("strategies")
    rec = ev.get("recommendation") if isinstance(ev.get("recommendation"), dict) else None
    if not isinstance(strategies, dict):
        strategies = {sid: {"score": row.get(f"{sid}_score"), "confidence": None, "assessable": None,
                            "measured": None, "unmeasured": None} for sid in STRATEGY_IDS}
        rec = {"status": "unknown", "strategy": row.get("recommended_strategy"), "confidence": None,
               "reason": "assessed before icx-mdz-02 (criteria paired by position): re-assess this application"}
        method = "positional (before icx-mdz-02)"
    else:
        method = ev.get("method") or "by name"
    return {
        "assessment_id": row.get("id"),
        "legacy_app_id": row.get("legacy_app_id"),
        "assessed_at": str(row.get("assessed_at") or ""),
        "recommended_strategy": row.get("recommended_strategy"),
        "confidence": (rec or {}).get("confidence"),
        "min_confidence": (rec or {}).get("min_confidence"),
        "recommendation": rec,
        "strategies": strategies,
        "not_assessable": sorted(s for s, v in strategies.items() if v.get("assessable") is False),
        "method": method,
    }


def strategy_for_app(app_id, db_path=None, conn=None):
    """The latest application-level 7R assessment of ``app_id``, summarised (``summarise``); None when it was never
    assessed. Reads only: it never runs an assessment. This is what the other 7R generators consume."""
    own = conn is None
    conn = conn or _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, legacy_app_id, recommended_strategy, rehost_score, replatform_score, refactor_score, "
            "rearchitect_score, repurchase_score, retire_score, retain_score, evidence, assessed_at "
            "FROM migration_assessments WHERE legacy_app_id = %s AND assessment_scope = 'application' "
            "ORDER BY assessed_at DESC LIMIT 1", (app_id,)).fetchone()
    finally:
        if own:
            conn.close()
    if row is None:
        return None
    cols = ("id", "legacy_app_id", "recommended_strategy", "rehost_score", "replatform_score", "refactor_score",
            "rearchitect_score", "repurchase_score", "retire_score", "retain_score", "evidence", "assessed_at")
    return summarise(dict(zip(cols, tuple(row))))


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
               WHERE legacy_app_id = %s
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
        cost_h = _estimate_cost(
            profile_summary
            if "loc_code" in profile_summary
            else {
                "loc_code": profile_summary.get("loc_code", 0),
                "loc_total": profile_summary.get("loc_total", 0),
                "maintainability_index": profile_summary.get("maintainability_index", 50),
                "avg_complexity": profile_summary.get("avg_complexity", 0),
            },
            sid,
            catalog,
        )
        timeline_w = _estimate_timeline(
            {
                "loc_code": profile_summary.get("loc_code", 0),
                "loc_total": profile_summary.get("loc_total", 0),
                "maintainability_index": profile_summary.get("maintainability_index", 50),
                "avg_complexity": profile_summary.get("avg_complexity", 0),
            },
            sid,
            catalog,
        )
        tdr = _estimate_tech_debt_reduction(profile_summary, sid)
        risk_s = _compute_risk_score(
            {
                "maintainability_index": profile_summary.get("maintainability_index", 50),
                "external_dep_count": profile_summary.get("dependency_count", 0),
            },
            sid,
            catalog,
        )

        strategy_details.append(
            {
                "rank": rank,
                "name": cat_info.get("name", sid.title()),
                "id": sid,
                "score": score,
                "confidence": entry.get("confidence"),
                "risk_level": risk_level,
                "risk_score": risk_s,
                "cost_hours": cost_h,
                "timeline_weeks": timeline_w,
                "ato_impact": ato,
                "tech_debt_reduction": tdr,
            }
        )

    # Format the matrix
    lines = []
    lines.append("=" * 100)
    lines.append("CUI // SP-CTI")
    lines.append("=" * 100)
    lines.append("")
    lines.append("7R MIGRATION STRATEGY DECISION MATRIX")
    lines.append(f"Application: {profile_summary.get('name', app_id)}")
    lines.append(
        f"Language:    {profile_summary.get('primary_language', 'N/A')} {profile_summary.get('language_version', '')}"
    )
    lines.append(
        f"Framework:   {profile_summary.get('framework', 'N/A')} {profile_summary.get('framework_version', '')}"
    )
    lines.append(
        f"LOC:         {profile_summary.get('loc_total', 'N/A'):,}"
        if isinstance(profile_summary.get("loc_total"), (int, float))
        else f"LOC:         {profile_summary.get('loc_total', 'N/A')}"
    )
    lines.append(f"Components:  {profile_summary.get('component_count', 'N/A')}")
    lines.append(f"Assessed:    {row_dict.get('assessed_at', 'N/A')}")
    lines.append("")
    lines.append("-" * 100)

    # Table header
    header = f"{'Rank':<6}{'Strategy':<15}{'Score':<9}{'Conf.':<7}{'Risk':<10}{'Cost (hrs)':<12}{'Timeline':<12}{'ATO Impact':<14}{'Debt Reduction':<15}"
    lines.append(header)
    lines.append("-" * 100)

    # Table rows
    recommended = row_dict.get("recommended_strategy")
    for sd in strategy_details:
        marker = " <<" if sd["id"] == recommended else ""
        score_txt = "n/a" if sd["score"] is None else f"{sd['score']:.4f}"   # not assessable: no score (icx-mdz-02)
        conf_txt = "?" if sd["confidence"] is None else f"{sd['confidence']:.2f}"
        row_str = (
            f"{sd['rank']:<6}"
            f"{sd['name']:<15}"
            f"{score_txt:<9}"
            f"{conf_txt:<7}"
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
    rec_why = (evidence.get("recommendation") or {}).get("reason")
    rec_details = None
    for sd in strategy_details:
        if sd["id"] == recommended:
            rec_details = sd
            break

    lines.append("RECOMMENDATION")
    lines.append(f"  Strategy:        {rec_details['name'] if rec_details else (recommended or 'none')}")
    if rec_why:
        lines.append(f"  Basis:           {rec_why}")
    lines.append(f"  Score:           {rec_details['score']:.4f}" if rec_details else "  Score:           N/A")
    lines.append(
        f"  Risk:            {rec_details['risk_level'] if rec_details else 'N/A'} ({rec_details['risk_score']:.2f})"
        if rec_details
        else ""
    )
    lines.append(f"  Estimated Cost:  {rec_details['cost_hours']:,} hours" if rec_details else "  Estimated Cost:  N/A")
    lines.append(
        f"  Timeline:        {rec_details['timeline_weeks']} weeks" if rec_details else "  Timeline:        N/A"
    )
    lines.append(f"  ATO Impact:      {rec_details['ato_impact']}" if rec_details else "  ATO Impact:      N/A")
    lines.append(
        f"  Debt Reduction:  {rec_details['tech_debt_reduction']:.0f}%" if rec_details else "  Debt Reduction:  N/A"
    )
    lines.append("")

    # Rationale
    lines.append("RATIONALE")
    if rec_details:
        # Build a short rationale from the fitness scores
        fit = fitness_results.get(recommended, {})
        high_fit = [k for k, v in fit.items() if v is not None and v >= 0.8]
        low_fit = [k for k, v in fit.items() if v is not None and v <= 0.2]
        unmeasured = [k for k, v in fit.items() if v is None]

        if high_fit:
            lines.append(f"  Strengths:   {', '.join(high_fit)}")
        if low_fit:
            lines.append(f"  Weaknesses:  {', '.join(low_fit)}")
        if unmeasured:
            lines.append(f"  Unmeasured:  {', '.join(unmeasured)} (left out of the score)")

        # Compare with the next assessable strategy
        others = [sd for sd in strategy_details if sd["id"] != recommended and sd["score"] is not None]
        if others:
            second = others[0]
            delta = rec_details["score"] - second["score"]
            lines.append(f"  Margin:      {recommended} leads {second['id']} by {delta:.4f} ({delta * 100:.1f}%)")
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
        score = strategy_scores.get(sid)
        criteria_strs = [f"{k}={'unmeasured' if v is None else format(v, '.1f')}" for k, v in fit.items()]
        score_txt = "not assessable" if score is None else f"score: {score:.4f}"
        lines.append(f"  {sid:<14} ({score_txt}): {', '.join(criteria_strs)}")
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
    parser.add_argument(
        "--icos-decisions",
        help="ICDEV[OS]'s signed decisions export: retire's functional_redundancy is its MEASURED redundancy for this "
             "application (else ICDEV_ICOS_DECISIONS; none or unmeasured: the criterion is unmeasured)",
    )
    parser.add_argument("--icos-jwks", help="the ICDEV[OS] JWKS you PINNED (else ICDEV_ICOS_JWKS)")

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
        redundancy = icos_redundancy(args.app_id, export=args.icos_decisions, jwks=args.icos_jwks)
        assessment = run_seven_r_assessment(
            project_id=args.project_id,
            app_id=args.app_id,
            custom_weights=custom_weights,
            redundancy=redundancy,
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
        output["scoring_weights"] = (
            json.loads(output["scoring_weights"])
            if isinstance(output["scoring_weights"], str)
            else output["scoring_weights"]
        )
        output["evidence"] = (
            json.loads(output["evidence"]) if isinstance(output["evidence"], str) else output["evidence"]
        )
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
        rec = assessment["recommended_strategy"]
        print(f"  Recommended Strategy: {(rec or 'none').upper()}")
        print(f"  Basis:               {assessment['recommendation']['reason']}")
        if rec:
            print(f"  Score:               {assessment.get(rec + '_score', 'N/A')}")
            print(f"  Confidence:          {assessment['confidence']}")
            print(f"  Risk Score:          {assessment['risk_score']}")
            print(f"  Cost Estimate:       {assessment['cost_estimate_hours']:,} hours")
            print(f"  Timeline:            {assessment['timeline_weeks']} weeks")
            print(f"  ATO Impact:          {assessment['ato_impact']}")
            print(f"  Tech Debt Reduction: {assessment['tech_debt_reduction']:.0f}%")
        print()
        print("Strategy Rankings:")
        for entry in assessment["ranking"]:
            marker = " << RECOMMENDED" if entry["strategy_id"] == rec else ""
            score = "not assessable" if entry["score"] is None else f"{entry['score']:.4f}"
            print(f"  #{entry['rank']}  {entry['strategy_id']:<14} {score:<15} confidence {entry['confidence']:.2f}{marker}")
        print()
        print("Run with --matrix for full decision matrix.")
        print("Run with --json for machine-readable output.")
        print("=" * 60)
        print("CUI // SP-CTI")
        print("=" * 60)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
