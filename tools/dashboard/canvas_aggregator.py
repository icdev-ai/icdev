# CUI // SP-CTI
"""ICDEV™ — Canvas DB Aggregator.

Shared helper that queries across all 7 canvas SQLite DBs to surface
compliance summaries, finding counts, recent findings, and activity trends
for the ICDEV™ dashboard.

All results are cached for 60 seconds to avoid repeated cross-DB queries
on page refresh.  The cache is module-level and process-scoped — no shared
state across workers.

Canvas map (7 canvases):
    JSON-based (findings live as JSON inside an assessments table):
        security_canvas.db      → sc_assessments  / sc_audit  (ts)
        infra_canvas.db         → idc_assessments / idc_audit (created_at)
        observability_canvas.db → od_assessments  / od_audit  (created_at)
        boundary_canvas.db      → bd_assessments  / bd_audit  (created_at)
        data_canvas.db          → dd_assessments  / dd_audit  (created_at)

    Direct-row (one finding per row):
        network_canvas.db       → nc_compliance_findings / nc_audit (ts)
        pipeline_canvas.db      → pc_compliance_findings / pc_audit (ts)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import time
from datetime import timedelta, timezone, datetime
from pathlib import Path
from typing import Any

from tools.db.storage import get_connection

logger = get_logger("icdev.dashboard.canvas_aggregator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# ---------------------------------------------------------------------------
# Connection cache (read-only, reused across requests — same pattern as
# findings_aggregator.py).  check_same_thread=False is safe because these
# connections are SELECT-only.
# ---------------------------------------------------------------------------
_conn_cache: dict[str, Any] = {}


def _get_canvas_conn(path: Path) -> Any | None:
    """Return a cached connection for *path*, creating one if needed.

    Returns None if the file does not exist (canvas not yet initialised).
    Reconnects automatically if the cached connection has been closed.
    """
    key = str(path)
    conn = _conn_cache.get(key)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            _conn_cache.pop(key, None)
    if not path.exists():
        return None
    try:
        conn = get_connection(str(path))
        _conn_cache[key] = conn
        return conn
    except Exception as exc:
        logger.warning("canvas_aggregator: cannot open %s: %s", path.name, exc)
        return None


def close_canvas_connections() -> None:
    """Close all cached canvas connections.

    Register with Flask teardown_appcontext so connections are released
    at the end of each request::

        @app.teardown_appcontext
        def _close_canvas_agg_conns(exc):
            close_canvas_connections()
    """
    for conn in list(_conn_cache.values()):
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass
    _conn_cache.clear()


# ---------------------------------------------------------------------------
# 60-second result cache
# ---------------------------------------------------------------------------
_result_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 60.0  # seconds


def _cache_get(key: str) -> Any | None:
    entry = _result_cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _result_cache[key]
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _result_cache[key] = (time.monotonic(), val)


def invalidate_cache() -> None:
    """Clear the result cache.  Useful after a bulk import or forced rescan."""
    _result_cache.clear()


# ---------------------------------------------------------------------------
# Canvas registry
# Fields: db_filename, label, slug, assessment_table, score_col, grade_col,
#         time_col, audit_table, audit_time_col
# grade_col is "" when the assessment table has no grade column.
# ---------------------------------------------------------------------------
_JSON_CANVASES: list[tuple[str, str, str, str, str, str, str, str, str]] = [
    (
        "security_canvas.db", "Security Canvas", "security",
        "sc_assessments", "risk_score", "posture_grade", "ran_at",
        "sc_audit", "ts",
    ),
    (
        "infra_canvas.db", "Infra Canvas", "infra",
        "idc_assessments", "score", "", "created_at",
        "idc_audit", "created_at",
    ),
    (
        "observability_canvas.db", "Observability Canvas", "observability",
        "od_assessments", "score", "grade", "created_at",
        "od_audit", "created_at",
    ),
    (
        "boundary_canvas.db", "Boundary Canvas", "boundary",
        "bd_assessments", "score", "grade", "created_at",
        "bd_audit", "created_at",
    ),
    (
        "data_canvas.db", "Data Canvas", "data",
        "dd_assessments", "score", "", "created_at",
        "dd_audit", "created_at",
    ),
]

# Fields: db_filename, label, slug, findings_table, checks_table,
#         audit_table, audit_time_col
_DIRECT_CANVASES: list[tuple[str, str, str, str, str, str, str]] = [
    (
        "network_canvas.db", "Network Canvas", "network",
        "nc_compliance_findings", "nc_compliance_checks",
        "nc_audit", "ts",
    ),
    (
        "pipeline_canvas.db", "Pipeline Canvas", "pipeline",
        "pc_compliance_findings", "pc_compliance_checks",
        "pc_audit", "ts",
    ),
]

# Flattened list of (db_filename, audit_table, audit_time_col) for all 7.
_ALL_AUDIT_TABLES: list[tuple[str, str, str]] = (
    [(db, atbl, atcol) for db, _, _, _, _, _, _, atbl, atcol in _JSON_CANVASES]
    + [(db, atbl, atcol) for db, _, _, _, _, atbl, atcol in _DIRECT_CANVASES]
)

# Severity sort order — lower is higher priority.
_SEV_ORDER: dict[str, int] = {"CAT1": 0, "CAT2": 1, "CAT3": 2}


def _sev_key(sev: str) -> int:
    return _SEV_ORDER.get((sev or "CAT3").upper(), 2)


def _dedup_key(f: dict) -> str:
    """Lightweight dedup key for JSON-canvas findings (not the full SHA-256 hash)."""
    return "|".join([
        f.get("rule_id") or "",
        f.get("title") or "",
        f.get("affected_entity") or "",
    ])


# ---------------------------------------------------------------------------
# 1. get_canvas_compliance_summary
# ---------------------------------------------------------------------------

def get_canvas_compliance_summary() -> list[dict]:
    """Return a compliance summary row for each canvas.

    cnr-cc-02: delegates to the canonical posture aggregator
    (tools/canvas_compliance/posture.py) so the home index route, this summary,
    and the MCP surfaces share ONE implementation instead of three parallel
    ones. The posture module is backend-aware (reads each canvas through its own
    init_db.get_connection(), honoring PG/SQLite), unlike the previous
    implementation here which only scanned the stale per-canvas SQLite ``.db``
    files and returned ``available=False`` for every canvas on a PostgreSQL
    deployment.

    Each row::

        {
            "name":            str,   # human-readable canvas label
            "slug":            str,   # short identifier
            "latest_score":    float | None,
            "grade":           str,   # letter grade or "" (posture is numeric)
            "open_findings":   int,
            "closed_findings": int,
            "last_assessed":   str,   # "" (posture aggregates latest per design)
            "available":       bool,
        }
    """
    cached = _cache_get("compliance_summary")
    if cached is not None:
        return cached

    from tools.canvas_compliance.posture import compute_canvas_posture

    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: cross-canvas system aggregate; icdev tables read platform-wide with RLS disabled
    except Exception:
        pass
    try:
        posture, _overall = compute_canvas_posture(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    results: list[dict] = [
        {
            "name": row["name"],
            "slug": row["name"].lower().replace("/", "_").replace(" ", "_"),
            "latest_score": row.get("score"),
            "grade": "",
            "open_findings": row.get("open_findings", 0),
            "closed_findings": row.get("closed_findings", 0),
            "last_assessed": "",
            "available": True,
        }
        for row in posture
    ]
    _cache_set("compliance_summary", results)
    return results


# ---------------------------------------------------------------------------
# 2. get_canvas_finding_counts
# ---------------------------------------------------------------------------

def get_canvas_finding_counts() -> dict:
    """Return total CAT1/CAT2/CAT3 counts for open findings across all 7 canvases.

    Returns::

        {"CAT1": int, "CAT2": int, "CAT3": int, "total": int}

    Only open findings are counted (status not in remediated/accepted_risk/
    false_positive).
    """
    cached = _cache_get("finding_counts")
    if cached is not None:
        return cached

    counts: dict[str, int] = {"CAT1": 0, "CAT2": 0, "CAT3": 0, "total": 0}

    # JSON canvases — parse findings_json from the most recent 10 assessments.
    for (db, _, _, atbl, _, _, time_col, _, _) in _JSON_CANVASES:
        path = DATA_DIR / db
        conn = _get_canvas_conn(path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                f"SELECT findings_json FROM {atbl}"  # nosec B608
                f" ORDER BY {time_col} DESC LIMIT 10",
            ).fetchall()
            seen: set[str] = set()
            for row in rows:
                try:
                    findings = json.loads(row[0] or "[]")
                except (json.JSONDecodeError, TypeError):
                    findings = []
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    dk = _dedup_key(f)
                    if dk in seen:
                        continue
                    seen.add(dk)
                    status = (f.get("status") or "open").lower()
                    if status in ("remediated", "accepted_risk", "false_positive"):
                        continue
                    sev = (f.get("severity") or "CAT3").upper()
                    if sev not in counts:
                        sev = "CAT3"
                    counts[sev] += 1
                    counts["total"] += 1
        except Exception as exc:
            logger.warning(
                "canvas_aggregator: finding_counts failed for %s: %s", db, exc
            )
            _conn_cache.pop(str(path), None)

    # Direct canvases — aggregate in SQL.
    for (db, _, _, findings_tbl, _, _, _) in _DIRECT_CANVASES:
        path = DATA_DIR / db
        conn = _get_canvas_conn(path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                f"SELECT severity, COUNT(*) AS cnt FROM {findings_tbl}"  # nosec B608
                f" WHERE status NOT IN ('remediated', 'accepted_risk', 'false_positive')"
                f" GROUP BY severity",
            ).fetchall()
            for row in rows:
                sev = (row["severity"] or "CAT3").upper()
                cnt = row["cnt"] or 0
                if sev not in counts:
                    sev = "CAT3"
                counts[sev] += cnt
                counts["total"] += cnt
        except Exception as exc:
            logger.warning(
                "canvas_aggregator: finding_counts failed for %s: %s", db, exc
            )
            _conn_cache.pop(str(path), None)

    _cache_set("finding_counts", counts)
    return counts


# ---------------------------------------------------------------------------
# 3. get_recent_canvas_findings
# ---------------------------------------------------------------------------

def get_recent_canvas_findings(limit: int = 10) -> list[dict]:
    """Return the most recent open findings across all 7 canvases.

    Sorted by severity (CAT1 first) then by discovery date (newest first)
    within each severity tier.

    Each row::

        {
            "canvas_label":    str,
            "canvas_source":   str,   # slug
            "rule_id":         str,
            "title":           str,
            "severity":        str,   # CAT1 | CAT2 | CAT3
            "status":          str,   # open | remediated | …
            "affected_entity": str,
            "discovered_at":   str,   # ISO timestamp or ""
        }
    """
    cache_key = f"recent_findings_{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    all_findings: list[dict] = []

    # JSON canvases.
    for (db, label, slug, atbl, _, _, time_col, _, _) in _JSON_CANVASES:
        path = DATA_DIR / db
        conn = _get_canvas_conn(path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                f"SELECT findings_json, {time_col} FROM {atbl}"  # nosec B608
                f" ORDER BY {time_col} DESC LIMIT 10",
            ).fetchall()
            seen: set[str] = set()
            for row in rows:
                assessment_ts = row[1] or ""
                try:
                    findings = json.loads(row[0] or "[]")
                except (json.JSONDecodeError, TypeError):
                    findings = []
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    dk = _dedup_key(f)
                    if dk in seen:
                        continue
                    seen.add(dk)
                    all_findings.append({
                        "canvas_label": label,
                        "canvas_source": slug,
                        "rule_id": f.get("rule_id") or "",
                        "title": f.get("title") or f.get("description") or "(untitled)",
                        "severity": (f.get("severity") or "CAT3").upper(),
                        "status": (f.get("status") or "open").lower(),
                        "affected_entity": f.get("affected_entity") or "",
                        "discovered_at": f.get("created_at") or assessment_ts,
                    })
        except Exception as exc:
            logger.warning(
                "canvas_aggregator: recent_findings failed for %s: %s", db, exc
            )
            _conn_cache.pop(str(path), None)

    # Direct canvases.
    for (db, label, slug, findings_tbl, _, _, _) in _DIRECT_CANVASES:
        path = DATA_DIR / db
        conn = _get_canvas_conn(path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                f"SELECT rule_id, title, description, severity, status,"  # nosec B608
                f" affected_entity, created_at"
                f" FROM {findings_tbl}"
                f" ORDER BY created_at DESC LIMIT 200",
            ).fetchall()
            for row in rows:
                row = dict(row)
                all_findings.append({
                    "canvas_label": label,
                    "canvas_source": slug,
                    "rule_id": row.get("rule_id") or "",
                    "title": row.get("title") or row.get("description") or "(untitled)",
                    "severity": (row.get("severity") or "CAT3").upper(),
                    "status": (row.get("status") or "open").lower(),
                    "affected_entity": row.get("affected_entity") or "",
                    "discovered_at": row.get("created_at") or "",
                })
        except Exception as exc:
            logger.warning(
                "canvas_aggregator: recent_findings failed for %s: %s", db, exc
            )
            _conn_cache.pop(str(path), None)

    # Two-pass stable sort: newest-first by date, then severity ascending.
    # Stable sort preserves the date ordering within each severity tier.
    all_findings.sort(key=lambda f: f["discovered_at"] or "", reverse=True)
    all_findings.sort(key=lambda f: _sev_key(f["severity"]))

    result = all_findings[:limit]
    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# 4. get_canvas_activity_trend
# ---------------------------------------------------------------------------

def get_canvas_activity_trend(days: int = 7) -> list[dict]:
    """Return daily audit event counts across all 7 canvas audit tables.

    The result covers the last *days* calendar days (inclusive of today)
    in chronological order.  Days with zero events are included so the
    caller always receives exactly *days* rows.

    Each row::

        {
            "date":  str,  # "YYYY-MM-DD"
            "count": int,  # total audit events across all canvases on that day
        }
    """
    cache_key = f"activity_trend_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).date()
    buckets: dict[str, int] = {}
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        buckets[d.isoformat()] = 0

    # Oldest date we care about — used as the WHERE lower bound.
    cutoff = (today - timedelta(days=days - 1)).isoformat()

    for (db, audit_tbl, time_col) in _ALL_AUDIT_TABLES:
        path = DATA_DIR / db
        conn = _get_canvas_conn(path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                f"SELECT substr({time_col}, 1, 10) AS day, COUNT(*) AS cnt"  # nosec B608
                f" FROM {audit_tbl}"
                f" WHERE {time_col} >= %s"
                f" GROUP BY day",
                (cutoff,),
            ).fetchall()
            for row in rows:
                day = row["day"] or ""
                if day in buckets:
                    buckets[day] += row["cnt"] or 0
        except Exception as exc:
            logger.warning(
                "canvas_aggregator: activity_trend failed for %s.%s: %s",
                db, audit_tbl, exc,
            )
            _conn_cache.pop(str(path), None)

    result = [{"date": d, "count": c} for d, c in sorted(buckets.items())]
    _cache_set(cache_key, result)
    return result
