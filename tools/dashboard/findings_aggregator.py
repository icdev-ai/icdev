# CUI // SP-CTI
"""ICDEV - Canvas Findings Aggregator.

Single source of truth for the dashboard POA&M counter and the /poam page.
Reads findings from all 7 canvas SQLite DBs, normalises them into a uniform
shape, computes a stable SHA-256 identity hash, and joins with the
finding_approvals table (icdev.db / Postgres) to attach the human decision.

Replaces the inline aggregation block in app.py:1700-1784 so the index page
counter and the /poam list use the same data.

Architecture:
    - 5 canvases store findings as JSON inside an assessments table
      (sc_assessments.findings_json, idc_assessments.findings_json, etc.)
    - 2 canvases store one finding per row (nc_compliance_findings,
      pc_compliance_findings)
    - For JSON-based canvases, only the most-recent N=10 assessments are read.
      Trade-off: slightly more memory and marginally slower aggregation per
      request vs. faster gap closure after auto_remediator runs (old findings
      shift out of the window after 3 fresh scans with N=3, but after 10 with
      N=10). For direct tables, the most recent 200 rows are read.
    - Each finding is hashed by SHA-256 of (canvas_source|rule_id|title|
      affected_entity). The hash is the join key into finding_approvals.
    - Findings whose source row status is 'remediated' are excluded from
      the open list, so the count matches the legacy dashboard counter.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

logger = get_logger("icdev.dashboard.findings")

# ---------------------------------------------------------------------------
# Module-level connection cache for the 7 canvas SQLite DBs.
# Connections are read-only (SELECT only) and reused across requests.
# Call close_canvas_connections() from Flask teardown_appcontext to clean up
# at the end of each request (or register it once for app shutdown).
# check_same_thread=False is safe here because we never write to these DBs.
# ---------------------------------------------------------------------------
_conn_cache: dict[str, Any] = {}


def _get_canvas_conn(path: Path):
    """Return a cached read connection for a canvas's findings tables.

    PG-primary: on PostgreSQL the per-canvas assessment tables (``sc_assessments``,
    ``idc_assessments``, …) live in the SHARED icdev database, so we hand back a
    single RLS-disabled ``StorageConnection`` to it and let the table name select
    the canvas — the per-canvas ``.db`` filename is irrelevant. On SQLite (backup)
    each canvas is a separate ``.db`` sidecar opened by *path*; the raw connection
    is wrapped in ``StorageConnection`` so the read queries' ``%s`` placeholders
    translate to ``?``.

    Returns None only in SQLite mode when the canvas ``.db`` does not exist yet.
    Reconnects automatically if a cached connection has gone stale.
    """
    from tools.db.storage import (
        StorageConnection,
        get_canvas_connection,
        resolve_canvas_backend,
    )

    def _alive(conn) -> bool:
        try:
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    if resolve_canvas_backend() == "postgresql":
        # All canvas tables share one PG database; one connection serves them all.
        key = "__pg_shared__"
        conn = _conn_cache.get(key)
        if conn is not None and _alive(conn):
            return conn
        _conn_cache.pop(key, None)
        # RLS-disabled: canvas tables lack tenant_id/classification columns.
        conn = get_canvas_connection()
        _conn_cache[key] = conn
        return conn

    # SQLite fallback: per-canvas .db sidecar file.
    key = str(path)
    conn = _conn_cache.get(key)
    if conn is not None and _alive(conn):
        return conn
    _conn_cache.pop(key, None)
    if not path.exists():
        return None
    try:
        raw = sqlite3.connect(str(path))  # pg-ok: per-canvas SQLite sidecar (SQLite-mode fallback)
        raw.row_factory = sqlite3.Row
        # Wrap so the read queries' %s placeholders translate to ? on SQLite.
        conn = StorageConnection(raw, "sqlite")
        _conn_cache[key] = conn
        return conn
    except sqlite3.Error as exc:
        logger.warning("findings_aggregator: cannot open %s: %s", path.name, exc)
        return None


def close_canvas_connections() -> None:
    """Close all cached canvas connections.

    Register this with Flask's teardown_appcontext so connections are
    released after each request::

        @app.teardown_appcontext
        def _close_canvas_conns(exc):
            close_canvas_connections()
    """
    for conn in list(_conn_cache.values()):
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass
    _conn_cache.clear()


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# (db_filename, table, json_column, time_column, label, slug)
JSON_CANVAS_DBS = [
    ("security_canvas.db", "sc_assessments", "findings_json", "ran_at", "Security Canvas", "security"),
    ("infra_canvas.db", "idc_assessments", "findings_json", "created_at", "Infra Canvas", "infra"),
    ("observability_canvas.db", "od_assessments", "findings_json", "created_at", "Observability Canvas", "observability"),
    ("boundary_canvas.db", "bd_assessments", "findings_json", "created_at", "Boundary Canvas", "boundary"),
    ("data_canvas.db", "dd_assessments", "findings_json", "created_at", "Data Canvas", "data"),
]

# (db_filename, table, label, slug)
DIRECT_CANVAS_DBS = [
    ("network_canvas.db", "nc_compliance_findings", "Network Canvas", "network"),
    ("pipeline_canvas.db", "pc_compliance_findings", "Pipeline Canvas", "pipeline"),
]

# How many of the most-recent assessments to read per JSON canvas.
# Bumped from 3 → 10 (OPT-07) so remediated findings shift out of the active
# window sooner after auto_remediator runs, preventing the "stuck findings"
# symptom on the POA&M counter.
# Trade-off: ~3× more SQLite rows read per JSON canvas per request (still well
# under 1 ms per canvas on typical hardware). Memory impact is proportional to
# the number of findings per assessment — negligible for normal scan sizes.
# Configurable via args/dashboard_config.yaml (json_recent_limit) if you need
# to tune this per deployment.
JSON_RECENT_LIMIT = 10
DIRECT_RECENT_LIMIT = 200


def compute_finding_hash(canvas_source: str, rule_id: str, title: str, affected_entity: str) -> str:
    """Stable SHA-256 identity for a finding.

    Used as the primary key into finding_approvals so a re-scan of the same
    canvas produces the same hash and the prior approval decision is reused.
    """
    payload = "|".join([
        (canvas_source or "").strip(),
        (rule_id or "").strip(),
        (title or "").strip(),
        (affected_entity or "").strip(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(canvas_label: str, canvas_slug: str, raw: dict, ran_at: str = "") -> dict:
    """Convert a raw finding (any source) into the dashboard finding shape."""
    rule_id = raw.get("rule_id") or raw.get("id") or ""
    title = raw.get("title") or raw.get("description") or "(untitled)"
    severity = (raw.get("severity") or "CAT3").upper()
    affected = raw.get("affected_entity") or raw.get("affected") or ""
    category = raw.get("category") or ""
    description = raw.get("description") or raw.get("detail") or ""
    status = (raw.get("status") or "open").lower()
    return {
        "finding_hash": compute_finding_hash(canvas_slug, rule_id, title, affected),
        "canvas_label": canvas_label,
        "canvas_source": canvas_slug,
        "rule_id": rule_id,
        "title": title,
        "severity": severity,
        "category": category,
        "description": description,
        "affected_entity": affected,
        "affected_type": raw.get("affected_type") or "",
        "source_status": status,
        "discovered_at": ran_at or raw.get("created_at", ""),
    }


def _read_json_canvas(db_filename: str, table: str, json_col: str, time_col: str,
                      label: str, slug: str) -> list[dict]:
    """Read findings from a canvas DB whose findings live as JSON in one column."""
    path = DATA_DIR / db_filename
    c = _get_canvas_conn(path)
    if c is None:
        return []
    out: list[dict] = []
    try:
        rows = c.execute(
            f"SELECT {json_col}, {time_col} FROM {table} ORDER BY {time_col} DESC LIMIT %s",  # nosec B608
            (JSON_RECENT_LIMIT,),
        ).fetchall()
        for row in rows:
            try:
                items = json.loads(row[0] or "[]")
            except (json.JSONDecodeError, TypeError):
                items = []
            ts = row[1] or ""
            for item in items:
                if not isinstance(item, dict):
                    continue
                out.append(_normalize(label, slug, item, ran_at=ts))
    except sqlite3.Error as exc:
        logger.warning("findings_aggregator: failed to read %s: %s", db_filename, exc)
        # Evict the bad connection so next call gets a fresh one.
        _conn_cache.pop(str(path), None)
    return out


def _read_direct_canvas(db_filename: str, table: str, label: str, slug: str) -> list[dict]:
    """Read findings from a canvas DB that stores one finding per row."""
    path = DATA_DIR / db_filename
    c = _get_canvas_conn(path)
    if c is None:
        return []
    out: list[dict] = []
    try:
        rows = c.execute(
            f"SELECT id, rule_id, severity, title, description, affected_entity, "  # nosec B608
            f"affected_type, status, created_at FROM {table} "
            f"ORDER BY created_at DESC LIMIT %s",
            (DIRECT_RECENT_LIMIT,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            out.append(_normalize(label, slug, d, ran_at=d.get("created_at", "")))
    except sqlite3.Error as exc:
        logger.warning("findings_aggregator: failed to read %s: %s", db_filename, exc)
        # Evict the bad connection so next call gets a fresh one.
        _conn_cache.pop(str(path), None)
    return out


def _load_approvals(get_db_conn) -> dict[str, dict]:
    """Fetch all rows from finding_approvals keyed by finding_hash."""
    if get_db_conn is None:
        return {}
    try:
        conn = get_db_conn()
        rows = conn.execute(
            "SELECT finding_hash, decision, decision_by, decision_at, decision_rationale, updated_at "
            "FROM finding_approvals"
        ).fetchall()
        conn.close()
        return {r["finding_hash"]: dict(r) for r in rows}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("findings_aggregator: failed to load approvals: %s", exc)
        return {}


def _read_simulation_findings(get_db_conn) -> list[dict]:
    """Return tfw_simulation findings stored directly in finding_approvals."""
    if get_db_conn is None:
        return []
    try:
        conn = get_db_conn()
        rows = conn.execute(
            "SELECT finding_hash, rule_id, severity, title, affected_entity, "
            "decision, decision_by, decision_at, decision_rationale, created_at "
            "FROM finding_approvals WHERE canvas_source = 'tfw_simulation'"
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            out.append({
                "finding_hash": d["finding_hash"],
                "canvas_label": "Simulation",
                "canvas_source": "tfw_simulation",
                "rule_id": d.get("rule_id") or "",
                "title": d.get("title") or "(untitled)",
                "severity": d.get("severity") or "CAT3",
                "category": "simulation",
                "description": d.get("decision_rationale") or "",
                "affected_entity": d.get("affected_entity") or "",
                "affected_type": "",
                "source_status": "open",
                "discovered_at": d.get("created_at") or "",
                "decision": d.get("decision") or "pending",
                "decision_by": d.get("decision_by") or "",
                "decision_at": str(d.get("decision_at") or ""),
                "decision_rationale": d.get("decision_rationale") or "",
            })
        return out
    except Exception as exc:
        logger.warning("findings_aggregator: failed to load simulation findings: %s", exc)
        return []


def aggregate_findings(get_db_conn=None, include_remediated: bool = False) -> list[dict]:
    """Return all canvas findings joined with their approval state.

    Args:
        get_db_conn: Callable that returns an open icdev.db connection.
            Pass dashboard `_get_db`. If None, approval state will be empty.
        include_remediated: When False (default), findings whose source row
            status is 'remediated' are filtered out, matching the legacy
            dashboard counter. The /poam page passes True so the user can
            still see and re-open remediated items if needed.
    """
    findings: list[dict] = []
    seen: set[str] = set()  # de-dupe identical findings across re-scans

    for db, tbl, jcol, tcol, label, slug in JSON_CANVAS_DBS:
        for f in _read_json_canvas(db, tbl, jcol, tcol, label, slug):
            if not include_remediated and f["source_status"] == "remediated":
                continue
            if f["finding_hash"] in seen:
                continue
            seen.add(f["finding_hash"])
            findings.append(f)

    for db, tbl, label, slug in DIRECT_CANVAS_DBS:
        for f in _read_direct_canvas(db, tbl, label, slug):
            if not include_remediated and f["source_status"] == "remediated":
                continue
            if f["finding_hash"] in seen:
                continue
            seen.add(f["finding_hash"])
            findings.append(f)

    # Simulation findings are self-contained rows in finding_approvals
    for f in _read_simulation_findings(get_db_conn):
        if f["finding_hash"] in seen:
            continue
        seen.add(f["finding_hash"])
        findings.append(f)

    approvals = _load_approvals(get_db_conn)
    for f in findings:
        appr = approvals.get(f["finding_hash"])
        if appr:
            f["decision"] = appr.get("decision") or "pending"
            f["decision_by"] = appr.get("decision_by") or ""
            f["decision_at"] = str(appr.get("decision_at") or "")
            f["decision_rationale"] = appr.get("decision_rationale") or ""
        else:
            f["decision"] = "pending"
            f["decision_by"] = ""
            f["decision_at"] = ""
            f["decision_rationale"] = ""

    # Sort: CAT1 first, then CAT2, CAT3; pending before decided.
    sev_order = {"CAT1": 0, "CAT2": 1, "CAT3": 2}
    decision_order = {"pending": 0, "approved": 1, "accepted_risk": 2, "declined": 3, "remediated": 4}
    findings.sort(key=lambda f: (
        sev_order.get(f["severity"], 9),
        decision_order.get(f["decision"], 9),
        f["canvas_source"],
        f["title"],
    ))
    return findings


def open_finding_count(get_db_conn=None) -> int:
    """Convenience: count of open (non-remediated, non-declined, non-accepted_risk) findings.

    Used by the dashboard index POA&M counter to keep its number identical to
    what the /poam page shows.
    """
    findings = aggregate_findings(get_db_conn=get_db_conn, include_remediated=False)
    excluded = {"declined", "accepted_risk", "remediated"}
    return sum(1 for f in findings if f["decision"] not in excluded)


def summary(get_db_conn=None) -> dict[str, Any]:
    """Return counts grouped by severity, canvas, and decision."""
    findings = aggregate_findings(get_db_conn=get_db_conn, include_remediated=True)
    by_sev: dict[str, int] = {}
    by_canvas: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_canvas[f["canvas_label"]] = by_canvas.get(f["canvas_label"], 0) + 1
        by_decision[f["decision"]] = by_decision.get(f["decision"], 0) + 1
    return {
        "total": len(findings),
        "by_severity": by_sev,
        "by_canvas": by_canvas,
        "by_decision": by_decision,
    }
