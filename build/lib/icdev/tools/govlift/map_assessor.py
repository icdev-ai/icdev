# CUI // SP-CTI
"""GovLift — MAP Readiness Assessor.

Automatically evaluates workloads against the AWS Migration Acceleration Program
(MAP) 7-dimension readiness framework and recommends a 6R migration strategy.

Dimensions (each scored 1-5):
  1. business    — Business value, regulatory constraints, cost sensitivity
  2. operational — Runbook maturity, monitoring, SLAs, DR plan
  3. platform    — OS/cloud compatibility, containerization, IaC readiness
  4. security    — STIG posture, encryption, IAM readiness, data classification
  5. people      — Team cloud skill, training, documentation
  6. process     — CI/CD, change management, automated testing
  7. data        — Data gravity, sensitivity, volume, transfer feasibility

6R Strategy recommendation computed from dimension scores + workload metadata.

All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import MAP_STRATEGIES


# ── MAP Dimension weights ────────────────────────────────────────────────────
# AWS MAP prioritizes security + platform + operational for DoD workloads
_DIMENSION_WEIGHTS = {
    "business": 0.15,
    "operational": 0.20,
    "platform": 0.20,
    "security": 0.20,
    "people": 0.10,
    "process": 0.10,
    "data": 0.05,
}

_DIMENSION_NAMES = list(_DIMENSION_WEIGHTS.keys())

# ── Heuristic scoring tables ─────────────────────────────────────────────────

# OS → cloud-readiness score (higher = more ready)
_OS_READINESS = {
    "rhel": 4, "red hat": 4, "centos": 3, "rocky": 4, "almalinux": 4,
    "ubuntu": 4, "debian": 3, "suse": 3, "sles": 3,
    "windows server 2022": 4, "windows server 2019": 3, "windows server 2016": 2,
    "windows": 3, "windows server": 3,
    "oracle linux": 3, "aix": 1, "solaris": 1, "hp-ux": 1,
}

# Workload type → inherent platform readiness
_TYPE_PLATFORM = {
    "web_app": 4, "api_service": 4, "batch_job": 3,
    "database": 2, "message_queue": 3, "storage": 2,
    "network_appliance": 1, "legacy_app": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _normalize_os(os_name: str) -> str:
    return (os_name or "").lower().strip()


def _score_os_readiness(os_name: str) -> int:
    name = _normalize_os(os_name)
    for key, score in _OS_READINESS.items():
        if key in name:
            return score
    return 2  # default: somewhat ready


def _score_type_platform(workload_type: str) -> int:
    return _TYPE_PLATFORM.get(workload_type, 2)


def _get_stig_counts(workload_id: str) -> dict[str, int]:
    """Return open STIG findings by severity for a workload."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                translate_sql(
                    "SELECT severity, COUNT(*) AS cnt FROM govlift_stig_checks "
                    "WHERE workload_id = ? AND status = 'open' GROUP BY severity"
                ),
                (workload_id,),
            ).fetchall()
            return {r["severity"]: r["cnt"] for r in rows}
        finally:
            conn.close()
    except Exception:
        return {}


def _get_dependency_count(workload_id: str) -> int:
    """Count upstream dependencies for a workload."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                translate_sql(
                    "SELECT COUNT(*) AS cnt FROM govlift_dependencies "
                    "WHERE dependent_workload_id = ?"
                ),
                (workload_id,),
            ).fetchone()
            return _row_to_dict(row).get("cnt", 0) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _get_resource_score(cpu: int, memory: float, storage: float) -> int:
    """Score resource sizing simplicity (smaller = easier to migrate)."""
    score = 5
    if cpu > 32:
        score -= 2
    elif cpu > 16:
        score -= 1
    if memory > 128:
        score -= 2
    elif memory > 64:
        score -= 1
    if storage > 10:
        score -= 2
    elif storage > 5:
        score -= 1
    return max(1, score)


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------

def assess_business(wl: dict) -> int:
    """Business readiness: regulatory, cost, value."""
    score = 3  # baseline
    risk = wl.get("risk_level", "medium")
    # Critical/high risk workloads need more business validation
    if risk == "critical":
        score -= 2
    elif risk == "high":
        score -= 1
    elif risk == "low":
        score += 1
    # Production workloads have higher business impact
    env = (wl.get("environment") or "").lower()
    if "prod" in env:
        score -= 1
    elif "dev" in env or "test" in env:
        score += 1
    # Larger workloads = higher business stakes
    if wl.get("cpu_cores", 0) > 16 or wl.get("memory_gb", 0) > 64:
        score -= 1
    return max(1, min(5, score))


def assess_operational(wl: dict) -> int:
    """Operational readiness: DR, monitoring, runbooks."""
    score = 3
    env = (wl.get("environment") or "").lower()
    if "prod" in env:
        score += 1  # prod usually has better ops
    if wl.get("last_scanned"):
        score += 1  # actively managed
    # Older OS versions = worse operational posture
    os_ver = (wl.get("os_version") or "").lower()
    if "eol" in os_ver or any(x in os_ver for x in ("2008", "2012", "7.", "8.0")):
        score -= 2
    elif any(x in os_ver for x in ("2016", "9.0", "8.")):
        score -= 1
    return max(1, min(5, score))


def assess_platform(wl: dict) -> int:
    """Platform readiness: OS compatibility, containerization, IaC."""
    score = _score_os_readiness(wl.get("os_name", ""))
    score += _score_type_platform(wl.get("workload_type", "")) - 3  # delta around 3
    # Resource sizing penalty for very large workloads
    if wl.get("cpu_cores", 0) > 64 or wl.get("memory_gb", 0) > 256:
        score -= 1
    return max(1, min(5, score))


def assess_security(wl: dict, stig_open: dict[str, int]) -> int:
    """Security readiness: STIG posture, encryption, IAM."""
    score = 5
    cat1 = stig_open.get("cat1", 0)
    cat2 = stig_open.get("cat2", 0)
    cat3 = stig_open.get("cat3", 0)
    score -= min(cat1 * 3, 4)
    score -= min(cat2, 2)
    score -= min(cat3 // 3, 1)
    risk = wl.get("risk_level", "medium")
    if risk == "critical":
        score -= 1
    return max(1, min(5, score))


def assess_people(wl: dict) -> int:
    """People readiness: team skill, documentation, training."""
    score = 3
    # App owner presence suggests accountability
    if wl.get("app_owner"):
        score += 1
    if wl.get("business_unit"):
        score += 1
    # Legacy/mainframe types suggest skills gap
    if wl.get("workload_type") == "legacy_app":
        score -= 2
    elif wl.get("workload_type") == "network_appliance":
        score -= 1
    return max(1, min(5, score))


def assess_process(wl: dict) -> int:
    """Process readiness: CI/CD, change management, testing."""
    score = 3
    # Modern app types usually have better process maturity
    wtype = wl.get("workload_type", "")
    if wtype in ("web_app", "api_service"):
        score += 1
    elif wtype in ("legacy_app", "network_appliance"):
        score -= 2
    # Dev/staging environments suggest process maturity
    env = (wl.get("environment") or "").lower()
    if "dev" in env or "staging" in env:
        score += 1
    return max(1, min(5, score))


def assess_data(wl: dict) -> int:
    """Data readiness: gravity, sensitivity, volume, transfer."""
    score = 3
    storage = wl.get("storage_tb", 0)
    if storage > 10:
        score -= 2
    elif storage > 5:
        score -= 1
    elif storage < 1:
        score += 1
    # Databases have higher data gravity
    if wl.get("workload_type") == "database":
        score -= 1
    return max(1, min(5, score))


# ---------------------------------------------------------------------------
# 6R Strategy recommendation
# ---------------------------------------------------------------------------

def recommend_strategy(dimensions: dict[str, int], wl: dict) -> str:
    """Recommend a 6R strategy from the 7 MAP dimension scores + workload metadata.

    Rules (ordered by priority):
      1. platform <= 2 and security <= 2          → retain  (not ready)
      2. platform == 1 and workload_type in (legacy_app, network_appliance)
                                                 → retire  (end of life)
      3. security >= 4 and platform >= 4          → refactor  (cloud-native ready)
      4. platform >= 4 and process >= 4           → replatform (PaaS ready)
      5. platform >= 3 and operational >= 3       → rehost   (lift-and-shift)
      6. business <= 2 and people <= 2            → repurchase (SaaS replacement)
      7. default                                  → rehost
    """
    plat = dimensions.get("platform", 3)
    sec = dimensions.get("security", 3)
    proc = dimensions.get("process", 3)
    biz = dimensions.get("business", 3)
    peop = dimensions.get("people", 3)
    ops = dimensions.get("operational", 3)
    wtype = wl.get("workload_type", "")

    if plat <= 2 and sec <= 2:
        return "retain"
    if plat == 1 and wtype in ("legacy_app", "network_appliance"):
        return "retire"
    if sec >= 4 and plat >= 4 and proc >= 4:
        return "refactor"
    if plat >= 4 and proc >= 4:
        return "replatform"
    if plat >= 3 and ops >= 3:
        return "rehost"
    if biz <= 2 and peop <= 2:
        return "repurchase"
    return "rehost"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assess_workload(workload_id: str, auto: bool = True) -> dict:
    """Run the full 7-dimension MAP assessment on a single workload.

    Returns:
        {
            "workload_id": str,
            "overall_score": int (1-5),
            "dimensions": {"business": int, ...},
            "strategy": str (6R),
            "stig_open": {"cat1": int, ...},
            "dependency_count": int,
            "assessed_at": str,
            "auto": bool,
        }
    """
    wl = _get_workload_raw(workload_id)
    if not wl:
        return {"error": f"Workload {workload_id} not found"}

    stig_open = _get_stig_counts(workload_id)
    dep_count = _get_dependency_count(workload_id)

    dimensions = {
        "business": assess_business(wl),
        "operational": assess_operational(wl),
        "platform": assess_platform(wl),
        "security": assess_security(wl, stig_open),
        "people": assess_people(wl),
        "process": assess_process(wl),
        "data": assess_data(wl),
    }

    overall = _compute_overall(dimensions)
    strategy = recommend_strategy(dimensions, wl)

    result = {
        "workload_id": workload_id,
        "overall_score": overall,
        "dimensions": dimensions,
        "strategy": strategy,
        "stig_open": stig_open,
        "dependency_count": dep_count,
        "assessed_at": _now(),
        "auto": auto,
    }

    _persist_assessment(result)
    return result


def assess_all_discovered(batch_size: int = 50) -> dict:
    """Bulk-assess all workloads with migration_status='discovered'.

    Returns summary of assessments performed.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                translate_sql(
                    "SELECT id FROM govlift_workloads WHERE migration_status = 'discovered'"
                )
            ).fetchall()
            workload_ids = [r["id"] for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc), "assessed": 0, "failed": 0}

    assessed = 0
    failed = 0
    strategies: dict[str, int] = {s: 0 for s in MAP_STRATEGIES}

    for wl_id in workload_ids:
        try:
            result = assess_workload(wl_id, auto=True)
            if "error" not in result:
                assessed += 1
                strategies[result.get("strategy", "rehost")] += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return {
        "assessed": assessed,
        "failed": failed,
        "strategies": strategies,
        "timestamp": _now(),
    }


def get_assessment(workload_id: str) -> dict | None:
    """Return the persisted assessment for a workload, or None."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                translate_sql(
                    "SELECT * FROM govlift_workloads WHERE id = ?"
                ),
                (workload_id,),
            ).fetchone()
            if not row:
                return None
            wl = _row_to_dict(row)
            dims = {
                "business": wl.get("dim_business", 0),
                "operational": wl.get("dim_operational", 0),
                "platform": wl.get("dim_platform", 0),
                "security": wl.get("dim_security", 0),
                "people": wl.get("dim_people", 0),
                "process": wl.get("dim_process", 0),
                "data": wl.get("dim_data", 0),
            }
            if all(v == 0 for v in dims.values()):
                return None  # never assessed
            overall = wl.get("map_score", 0)
            return {
                "workload_id": workload_id,
                "overall_score": overall,
                "dimensions": dims,
                "strategy": wl.get("map_strategy", ""),
                "assessed_at": wl.get("assessed_at", ""),
                "auto_assessed": bool(wl.get("auto_assessed", 0)),
            }
        finally:
            conn.close()
    except Exception:
        return None


def get_assessment_summary() -> dict:
    """Return aggregate assessment statistics across all workloads."""
    try:
        conn = get_connection()
        try:
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_workloads")
            ).fetchone()
            assessed_row = conn.execute(
                translate_sql(
                    "SELECT COUNT(*) AS cnt FROM govlift_workloads WHERE assessed_at IS NOT NULL"
                )
            ).fetchone()
            score_rows = conn.execute(
                translate_sql(
                    "SELECT map_score, COUNT(*) AS cnt FROM govlift_workloads "
                    "WHERE map_score > 0 GROUP BY map_score"
                )
            ).fetchall()
            strategy_rows = conn.execute(
                translate_sql(
                    "SELECT map_strategy, COUNT(*) AS cnt FROM govlift_workloads "
                    "WHERE map_strategy != '' GROUP BY map_strategy"
                )
            ).fetchall()
            avg_row = conn.execute(
                translate_sql(
                    "SELECT AVG(map_score) AS avg_score FROM govlift_workloads WHERE map_score > 0"
                )
            ).fetchone()

            total = _row_to_dict(total_row).get("cnt", 0) if total_row else 0
            assessed = _row_to_dict(assessed_row).get("cnt", 0) if assessed_row else 0

            return {
                "total_workloads": total,
                "assessed_count": assessed,
                "unassessed_count": total - assessed,
                "by_score": {r["map_score"]: r["cnt"] for r in score_rows},
                "by_strategy": {r["map_strategy"]: r["cnt"] for r in strategy_rows},
                "average_score": round(_row_to_dict(avg_row).get("avg_score", 0) or 0, 2) if avg_row else 0,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"map_assessor.get_assessment_summary error: {exc}", file=sys.stderr)
        return {
            "total_workloads": 0,
            "assessed_count": 0,
            "unassessed_count": 0,
            "by_score": {},
            "by_strategy": {},
            "average_score": 0,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_workload_raw(workload_id: str) -> dict | None:
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_workloads WHERE id = ?"),
                (workload_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _compute_overall(dimensions: dict[str, int]) -> int:
    """Weighted average of dimension scores, rounded to nearest int, clamped 1-5."""
    weighted = sum(dimensions.get(d, 3) * w for d, w in _DIMENSION_WEIGHTS.items())
    total_weight = sum(_DIMENSION_WEIGHTS.values())
    return max(1, min(5, round(weighted / total_weight)))


def _persist_assessment(result: dict) -> None:
    """Write assessment results back to govlift_workloads."""
    wl_id = result["workload_id"]
    dims = result.get("dimensions", {})
    now = result.get("assessed_at", _now())
    try:
        conn = get_connection()
        try:
            conn.execute(
                translate_sql(
                    "UPDATE govlift_workloads SET "
                    "map_score = ?, map_strategy = ?, "
                    "dim_business = ?, dim_operational = ?, dim_platform = ?, "
                    "dim_security = ?, dim_people = ?, dim_process = ?, dim_data = ?, "
                    "dependency_count = ?, assessed_at = ?, auto_assessed = ?, "
                    "migration_status = CASE WHEN migration_status = 'discovered' THEN 'assessed' ELSE migration_status END, "
                    "updated_at = ? "
                    "WHERE id = ?"
                ),
                (
                    result["overall_score"],
                    result["strategy"],
                    dims.get("business", 0),
                    dims.get("operational", 0),
                    dims.get("platform", 0),
                    dims.get("security", 0),
                    dims.get("people", 0),
                    dims.get("process", 0),
                    dims.get("data", 0),
                    result.get("dependency_count", 0),
                    now,
                    1 if result.get("auto") else 0,
                    now,
                    wl_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"map_assessor._persist_assessment error: {exc}", file=sys.stderr)
