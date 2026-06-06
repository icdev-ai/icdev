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
  days_since_last_assessment(proj) > N    (runs collection)

Execution model:
  1. Parse the IQE string with regex (no grammar lib needed for Phase 1)
  2. Translate to SQL against compliance_twin_snapshots (latest snapshot per project)
  3. Return list[dict]

Phase 2 will add a proper Lark grammar; this Phase 1 parser handles the 20 seed queries.
"""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

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

# Supported field aliases
_FIELD_MAP = {
    "ctrl.status": "implementation_status",
    "ctrl.implementation_status": "implementation_status",
    "ctrl.control_id": "control_id",
    "ctrl.evidence_ref": "evidence_ref",
    "ctrl.score": "score",
    "ctrl.project_id": "project_id",
    "ctrl.framework": "framework",
    "ctrl.assessor": "assessor",
    "ctrl.notes": "notes",
    # violations collection
    "viol.control_id": "control_id",
    "viol.violation_type": "violation_type",
    "viol.severity": "severity",
    "viol.project_id": "project_id",
}

# Condition → SQL fragment translator
_COND_PATTERNS = [
    # ctrl.evidence_ref is null
    (re.compile(r"(\w+\.\w+)\s+is\s+null", re.I),
     lambda m: (f"{_col(m.group(1))} IS NULL", [])),
    # ctrl.evidence_ref is not null
    (re.compile(r"(\w+\.\w+)\s+is\s+not\s+null", re.I),
     lambda m: (f"{_col(m.group(1))} IS NOT NULL", [])),
    # ctrl.status != 'satisfied'
    (re.compile(r"(\w+\.\w+)\s+(!=|==|<>|<=|>=|<|>)\s+'([^']*)'", re.I),
     lambda m: (f"{_col(m.group(1))} {_op(m.group(2))} ?", [m.group(3)])),
    # ctrl.score < 0.5
    (re.compile(r"(\w+\.\w+)\s+(!=|==|<>|<=|>=|<|>)\s+(-?\d+\.?\d*)", re.I),
     lambda m: (f"{_col(m.group(1))} {_op(m.group(2))} ?", [float(m.group(3))])),
    # ctrl.control_id starts_with 'AC'
    (re.compile(r"(\w+\.\w+)\s+starts_with\s+'([^']*)'", re.I),
     lambda m: (f"{_col(m.group(1))} LIKE ?", [m.group(2) + "%"])),
]


def _col(dotted: str) -> str:
    return _FIELD_MAP.get(dotted.strip().lower(), dotted.split(".")[-1])


def _op(symbol: str) -> str:
    return "=" if symbol in ("==", "=") else symbol


def _parse_predicate(pred_str: str):
    """Parse a WHERE predicate string into (sql_fragment, params)."""
    pred_str = pred_str.strip()
    sql_parts = []
    params = []

    # Split on AND (OR not supported in Phase 1)
    clauses = re.split(r"\s+and\s+", pred_str, flags=re.I)
    for clause in clauses:
        clause = clause.strip()
        matched = False
        for pattern, builder in _COND_PATTERNS:
            m = pattern.fullmatch(clause)
            if m:
                frag, p = builder(m)
                sql_parts.append(frag)
                params.extend(p)
                matched = True
                break
        if not matched:
            # Unknown predicate — skip silently (fail-open for unknown syntax)
            pass

    return " AND ".join(sql_parts) if sql_parts else None, params


def _parse_projection(proj_str: str, var: str) -> str:
    """Map IQE projection to SQL SELECT columns."""
    proj_str = proj_str.strip()
    if proj_str == "*":
        return "*"
    # Split on comma, map each field
    cols = []
    for token in proj_str.split(","):
        token = token.strip()
        col = _FIELD_MAP.get(token.lower(), token.split(".")[-1] if "." in token else token)
        cols.append(col)
    return ", ".join(cols)


def _latest_snapshot_cte(framework: str) -> str:
    """CTE that selects only the latest snapshot per project for a framework."""
    return """
        WITH latest_run AS (
            SELECT project_id, MAX(started_at) AS max_started
            FROM compliance_twin_runs
            WHERE framework = ?
            GROUP BY project_id
        ),
        latest_snap AS (
            SELECT s.*
            FROM compliance_twin_snapshots s
            JOIN latest_run lr
              ON s.project_id = lr.project_id
             AND s.framework = ?
            JOIN compliance_twin_runs r
              ON r.snapshot_id = s.snapshot_id
             AND r.started_at = lr.max_started
        )
    """


def run_query(iqe_string: str, conn=None) -> List[Dict[str, Any]]:
    """Execute an IQE query string and return results as list[dict].

    Args:
        iqe_string: IQE DSL query string.
        conn:       Optional existing DB connection (for tests).

    Returns:
        List of dicts matching the SELECT projection.
        Returns [] on unknown framework or parse failure.
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
            "SELECT 1 FROM compliance_twin_runs WHERE framework = ? LIMIT 1",
            (framework,),
        ).fetchone()
        if not exists:
            return []

        if collection == "controls":
            return _query_controls(conn, framework, pred_str, proj_str)
        elif collection == "violations":
            return _query_violations(conn, framework, pred_str, proj_str)
        elif collection == "runs":
            return _query_runs(conn, framework, pred_str, proj_str)
        else:
            return []
    finally:
        if _own_conn:
            conn.close()


def _query_controls(conn, framework: str, pred_str, proj_str: str):
    cte = _latest_snapshot_cte(framework)
    where_sql, params = _parse_predicate(pred_str or "")
    proj = _parse_projection(proj_str, "ctrl")

    sql = cte + f"SELECT {proj} FROM latest_snap"
    base_params = [framework, framework]

    if where_sql:
        sql += f" WHERE {where_sql}"
        base_params.extend(params)

    rows = conn.execute(sql, base_params).fetchall()
    return [dict(r) for r in rows]


def _query_violations(conn, framework: str, pred_str, proj_str: str):
    where_sql, params = _parse_predicate(pred_str or "")
    proj = _parse_projection(proj_str, "viol")
    # Use latest snapshot violations
    sql = f"""
        SELECT v.{proj.replace(', ', ', v.')}
        FROM compliance_twin_violations v
        JOIN (
            SELECT snapshot_id FROM compliance_twin_runs
            WHERE framework = ?
            ORDER BY started_at DESC LIMIT 1
        ) r ON v.snapshot_id = r.snapshot_id
    """
    base_params = [framework]
    if where_sql:
        # Prefix columns with v. for violations table
        sql += f" WHERE v.{where_sql}"
        base_params.extend(params)
    rows = conn.execute(sql, base_params).fetchall()
    return [dict(r) for r in rows]


def _query_runs(conn, framework: str, pred_str, proj_str: str):
    where_sql, params = _parse_predicate(pred_str or "")
    proj = "*" if proj_str.strip() == "*" else proj_str
    sql = f"SELECT {proj} FROM compliance_twin_runs WHERE framework = ?"
    base_params = [framework]
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
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()
    results = run_query(args.query)
    if args.json_out:
        print(json.dumps(results, default=str))
    else:
        for row in results:
            print(row)


if __name__ == "__main__":
    main()
