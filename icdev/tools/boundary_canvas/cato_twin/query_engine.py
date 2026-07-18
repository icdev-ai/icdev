#!/usr/bin/env python3
# CUI // SP-CTI
"""BDC IQE Query Engine — compliance domain.

Executes IQE (ICDEV Query Engine) queries against compliance_twin_snapshots.
The IQE DSL is a Forward-NQE-compatible SQL-like language:

  foreach <var> in <collection> [where <predicate>]* select <projection>

Collections understood by this engine:
  framework('<name>').controls        — latest snapshot rows for that framework
  framework('<name>').violations      — violation rows for that framework
  framework('<name>').runs            — run summary rows

Predicates supported:
  ctrl.status != 'satisfied'
  ctrl.status == 'not_satisfied'
  ctrl.implementation_status != 'satisfied'
  ctrl.evidence_ref is null
  ctrl.evidence_ref is not null
  ctrl.score < 0.5
  ctrl.score >= 0.8
  ctrl.control_id starts_with 'AC'
  ctrl.control_id starts_with 'IA'

Security model (bdr-sec-1):
  * Projection and predicate FIELD tokens are resolved through a strict,
    per-collection whitelist (``_CONTROL_FIELDS`` / ``_VIOLATION_FIELDS`` /
    ``_RUNS_FIELDS``). Any token that is not in the whitelist raises
    ``ValueError`` — no field name ever reaches the SQL string un-vetted, so
    there is no raw-token interpolation path for SQL injection.
  * Unknown WHERE predicates fail CLOSED: an unrecognised clause raises
    ``ValueError`` instead of being silently dropped (which previously widened
    result sets by ignoring the filter).
  * Snapshot/violation/run reads are scoped to a single ``project_id`` when the
    caller supplies one (parameterised ``project_id = %s``), preventing
    cross-project data bleed when a project context exists.

Execution model:
  1. Parse the IQE string with regex (no grammar lib needed for Phase 1)
  2. Translate to SQL against compliance_twin_snapshots (latest snapshot per project)
  3. Return list[dict]

Phase 2 will add a proper Lark grammar; this Phase 1 parser handles the 20 seed queries.
"""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# IQE string → SQL translator
# ---------------------------------------------------------------------------

# Regex: foreach <var> in framework('<name>').controls [where <pred>] select <proj>
_FOREACH_RE = re.compile(
    r"foreach\s+(\w+)\s+in\s+framework\('([^']+)'\)\.(\w+)"
    r"(?:\s+where\s+(.+?))?\s+select\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)

# ---------------------------------------------------------------------------
# Per-collection field whitelists.
#
# Keys are the IQE dotted tokens (lower-cased); values are the *validated* SQL
# column expressions they map to. A token that is absent from the relevant map
# is rejected with ValueError — this is the single choke point that guarantees
# no attacker-controlled identifier is ever interpolated into a SQL string.
#
# Violation columns are pre-qualified with the ``v.`` table alias so that both
# projections and predicates reference the joined violations table correctly
# without any fragile post-hoc string surgery.
# ---------------------------------------------------------------------------
_CONTROL_FIELDS = {
    "ctrl.status": "implementation_status",
    "ctrl.implementation_status": "implementation_status",
    "ctrl.control_id": "control_id",
    "ctrl.evidence_ref": "evidence_ref",
    "ctrl.score": "score",
    "ctrl.project_id": "project_id",
    "ctrl.framework": "framework",
    "ctrl.assessor": "assessor",
    "ctrl.notes": "notes",
    "ctrl.snapshot_id": "snapshot_id",
    "ctrl.classification": "classification",
    "ctrl.created_at": "created_at",
}

_VIOLATION_FIELDS = {
    "viol.control_id": "v.control_id",
    "viol.violation_type": "v.violation_type",
    "viol.severity": "v.severity",
    "viol.project_id": "v.project_id",
    "viol.snapshot_id": "v.snapshot_id",
    "viol.framework": "v.framework",
    "viol.details": "v.details",
    "viol.poam_id": "v.poam_id",
    "viol.classification": "v.classification",
    "viol.created_at": "v.created_at",
}

_RUNS_FIELDS = {
    "run.snapshot_id": "snapshot_id",
    "run.framework": "framework",
    "run.project_id": "project_id",
    "run.triggered_by": "triggered_by",
    "run.started_at": "started_at",
    "run.completed_at": "completed_at",
    "run.total_controls": "total_controls",
    "run.satisfied": "satisfied",
    "run.partially_satisfied": "partially_satisfied",
    "run.not_satisfied": "not_satisfied",
    "run.not_applicable": "not_applicable",
    "run.not_assessed": "not_assessed",
    "run.classification": "classification",
    "run.created_at": "created_at",
}


def _resolve_field(dotted: str, field_map: Dict[str, str]) -> str:
    """Resolve an IQE dotted token to a whitelisted SQL column expression.

    Raises:
        ValueError: if the token is not present in ``field_map``. This is the
            fail-closed guard that blocks SQL injection via unmapped tokens.
    """
    key = dotted.strip().lower()
    col = field_map.get(key)
    if col is None:
        raise ValueError(f"Unknown or disallowed IQE field token: {dotted!r}")
    return col


def _op(symbol: str) -> str:
    return "=" if symbol in ("==", "=") else symbol


# Condition → SQL fragment translator. Each builder receives the regex match and
# the active collection field map, and resolves its field through the whitelist.
_COND_PATTERNS = [
    # ctrl.evidence_ref is null
    (re.compile(r"(\w+\.\w+)\s+is\s+null", re.I),
     lambda m, fm: (f"{_resolve_field(m.group(1), fm)} IS NULL", [])),
    # ctrl.evidence_ref is not null
    (re.compile(r"(\w+\.\w+)\s+is\s+not\s+null", re.I),
     lambda m, fm: (f"{_resolve_field(m.group(1), fm)} IS NOT NULL", [])),
    # ctrl.status != 'satisfied'
    (re.compile(r"(\w+\.\w+)\s+(!=|==|<>|<=|>=|<|>)\s+'([^']*)'", re.I),
     lambda m, fm: (f"{_resolve_field(m.group(1), fm)} {_op(m.group(2))} %s", [m.group(3)])),
    # ctrl.score < 0.5
    (re.compile(r"(\w+\.\w+)\s+(!=|==|<>|<=|>=|<|>)\s+(-?\d+\.?\d*)", re.I),
     lambda m, fm: (f"{_resolve_field(m.group(1), fm)} {_op(m.group(2))} %s", [float(m.group(3))])),
    # ctrl.control_id starts_with 'AC'
    (re.compile(r"(\w+\.\w+)\s+starts_with\s+'([^']*)'", re.I),
     lambda m, fm: (f"{_resolve_field(m.group(1), fm)} LIKE %s", [m.group(2) + "%"])),
]


def _parse_predicate(pred_str, field_map: Dict[str, str]) -> Tuple[Optional[str], List[Any]]:
    """Parse a WHERE predicate string into (sql_fragment, params).

    Fields are resolved against ``field_map`` (fail-closed on unknown fields).
    Unknown/unsupported clauses raise ``ValueError`` (fail-closed) rather than
    being silently dropped.
    """
    pred_str = (pred_str or "").strip()
    if not pred_str:
        return None, []

    sql_parts: List[str] = []
    params: List[Any] = []

    # Split on AND (OR not supported in Phase 1)
    clauses = re.split(r"\s+and\s+", pred_str, flags=re.I)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        matched = False
        for pattern, builder in _COND_PATTERNS:
            m = pattern.fullmatch(clause)
            if m:
                frag, p = builder(m, field_map)
                sql_parts.append(frag)
                params.extend(p)
                matched = True
                break
        if not matched:
            # Fail CLOSED: an unrecognised predicate must not be silently
            # ignored (that would widen the result set by dropping the filter).
            raise ValueError(f"Unknown or unsupported IQE WHERE predicate: {clause!r}")

    return (" AND ".join(sql_parts) if sql_parts else None), params


def _parse_projection(proj_str: str, field_map: Dict[str, str]) -> List[str]:
    """Map an IQE projection to a list of whitelisted SQL column expressions.

    Returns ``["*"]`` for the ``*`` projection. Every other token is resolved
    through ``field_map``; an unmapped token raises ``ValueError`` (no raw
    token is ever interpolated into the SELECT list).
    """
    proj_str = (proj_str or "").strip()
    if proj_str == "*":
        return ["*"]

    cols: List[str] = []
    for token in proj_str.split(","):
        token = token.strip()
        if not token:
            continue
        cols.append(_resolve_field(token, field_map))
    if not cols:
        raise ValueError("Empty IQE projection")
    return cols


def _latest_snapshot_cte(framework: str) -> str:
    """CTE that selects only the latest snapshot per project for a framework."""
    return """
        WITH latest_run AS (
            SELECT project_id, MAX(started_at) AS max_started
            FROM compliance_twin_runs
            WHERE framework = %s
            GROUP BY project_id
        ),
        latest_snap AS (
            SELECT s.*
            FROM compliance_twin_snapshots s
            JOIN latest_run lr
              ON s.project_id = lr.project_id
             AND s.framework = %s
            JOIN compliance_twin_runs r
              ON r.snapshot_id = s.snapshot_id
             AND r.started_at = lr.max_started
        )
    """


def run_query(iqe_string: str, conn=None, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Execute an IQE query string and return results as list[dict].

    Args:
        iqe_string: IQE DSL query string.
        conn:       Optional existing DB connection (for tests).
        project_id: Optional project scope. When provided, snapshot/violation/run
                    reads are restricted to this project (parameterised), which
                    prevents cross-project data bleed. When ``None`` the query
                    spans every project (legacy fleet-wide behaviour).

    Returns:
        List of dicts matching the SELECT projection.
        Returns [] on unknown framework or a query that does not parse as a
        ``foreach ... select`` statement.

    Raises:
        ValueError: on an unmapped projection/predicate field or an
            unsupported WHERE predicate (fail-closed).
    """
    iqe_string = " ".join(iqe_string.split())  # normalise whitespace

    m = _FOREACH_RE.match(iqe_string)
    if not m:
        return []

    _var, framework, collection, pred_str, proj_str = m.groups()
    collection = collection.lower()

    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()

    try:
        # Check framework has any data
        exists = conn.execute(
            "SELECT 1 FROM compliance_twin_runs WHERE framework = %s LIMIT 1",
            (framework,),
        ).fetchone()
        if not exists:
            return []

        if collection == "controls":
            return _query_controls(conn, framework, pred_str, proj_str, project_id)
        elif collection == "violations":
            return _query_violations(conn, framework, pred_str, proj_str, project_id)
        elif collection == "runs":
            return _query_runs(conn, framework, pred_str, proj_str, project_id)
        else:
            return []
    finally:
        if _own_conn:
            conn.close()


def _query_controls(conn, framework: str, pred_str, proj_str: str,
                    project_id: Optional[str] = None):
    cte = _latest_snapshot_cte(framework)
    where_sql, params = _parse_predicate(pred_str, _CONTROL_FIELDS)
    cols = _parse_projection(proj_str, _CONTROL_FIELDS)
    proj = "*" if cols == ["*"] else ", ".join(cols)

    # nosec B608 — `proj` is built exclusively from whitelisted column names
    # (see _CONTROL_FIELDS / _parse_projection); no user token reaches the SQL.
    sql = cte + f"SELECT {proj} FROM latest_snap"  # nosec B608
    base_params: List[Any] = [framework, framework]

    conditions: List[str] = []
    if project_id is not None:
        conditions.append("project_id = %s")
        base_params.append(project_id)
    if where_sql:
        conditions.append(where_sql)
        base_params.extend(params)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    rows = conn.execute(sql, base_params).fetchall()
    return [dict(r) for r in rows]


def _query_violations(conn, framework: str, pred_str, proj_str: str,
                      project_id: Optional[str] = None):
    where_sql, params = _parse_predicate(pred_str, _VIOLATION_FIELDS)
    cols = _parse_projection(proj_str, _VIOLATION_FIELDS)
    proj = "v.*" if cols == ["*"] else ", ".join(cols)

    # Latest snapshot's violations for the framework.
    # nosec B608 — `proj` columns come from the _VIOLATION_FIELDS whitelist.
    sql = f"""
        SELECT {proj}
        FROM compliance_twin_violations v
        JOIN (
            SELECT snapshot_id FROM compliance_twin_runs
            WHERE framework = %s
            ORDER BY started_at DESC LIMIT 1
        ) r ON v.snapshot_id = r.snapshot_id
    """  # nosec B608
    base_params: List[Any] = [framework]

    conditions: List[str] = []
    if project_id is not None:
        conditions.append("v.project_id = %s")
        base_params.append(project_id)
    if where_sql:
        # where_sql columns are already ``v.``-qualified via _VIOLATION_FIELDS.
        conditions.append(where_sql)
        base_params.extend(params)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    rows = conn.execute(sql, base_params).fetchall()
    return [dict(r) for r in rows]


def _query_runs(conn, framework: str, pred_str, proj_str: str,
                project_id: Optional[str] = None):
    where_sql, params = _parse_predicate(pred_str, _RUNS_FIELDS)
    cols = _parse_projection(proj_str, _RUNS_FIELDS)
    proj = "*" if cols == ["*"] else ", ".join(cols)

    # nosec B608 — `proj` columns come from the _RUNS_FIELDS whitelist.
    sql = f"SELECT {proj} FROM compliance_twin_runs WHERE framework = %s"  # nosec B608
    base_params: List[Any] = [framework]
    if project_id is not None:
        sql += " AND project_id = %s"
        base_params.append(project_id)
    if where_sql:
        sql += f" AND {where_sql}"
        base_params.extend(params)

    rows = conn.execute(sql, base_params).fetchall()
    return [dict(r) for r in rows]


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Run an IQE compliance query")
    parser.add_argument("query", help="IQE query string")
    parser.add_argument("--project-id", dest="project_id", default=None,
                        help="Scope the query to a single project_id")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()
    results = run_query(args.query, project_id=args.project_id)
    if args.json_out:
        print(json.dumps(results, default=str))
    else:
        for row in results:
            print(row)


if __name__ == "__main__":
    main()
