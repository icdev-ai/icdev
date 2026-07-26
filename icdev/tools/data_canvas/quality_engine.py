# CUI // SP-CTI — ICDEV Data Design Canvas — Data Quality Engine
"""Data quality rule evaluator for the DDC Quality tab.

Pure functions — no Flask, no LLM dependency.
Defines rules (completeness, uniqueness, range, pattern, freshness),
runs them against a connected DB, and feeds results into the
existing DDC Assessment engine via DDC-QUA-001.

Supported backends: sqlite, postgresql (psycopg2), duckdb.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

from tools.data_canvas.constants import DS_CHECK_TYPES
from tools.data_canvas.data_profiler import _open_connection

# ── Rule validation ───────────────────────────────────────────────────────────


def validate_rule(rule: dict) -> dict:
    """Return {valid, error} for a quality rule definition."""
    check_type = rule.get("check_type", "")
    if check_type not in DS_CHECK_TYPES:
        return {"valid": False, "error": f"check_type must be one of {DS_CHECK_TYPES}"}
    if not rule.get("table_name", "").strip():
        return {"valid": False, "error": "table_name is required"}
    if check_type != "range" and not rule.get("column_name", "").strip():
        return {"valid": False, "error": f"column_name is required for {check_type}"}
    threshold = rule.get("threshold")
    if threshold is None:
        return {"valid": False, "error": "threshold is required"}
    try:
        float(threshold)
    except (TypeError, ValueError):
        return {"valid": False, "error": "threshold must be a number"}
    return {"valid": True, "error": None}


# ── Individual rule runners ───────────────────────────────────────────────────


def _run_completeness(conn, db_kind: str, table: str, column: str, threshold: float) -> dict:
    """% non-null >= threshold (0-100)."""
    safe_t = f'"{table}"'
    safe_c = f'"{column}"'
    if db_kind == "postgresql":
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*), COUNT({safe_c}) FROM {safe_t}")
        total, non_null = cur.fetchone()
    else:
        cur = conn.execute(f"SELECT COUNT(*), COUNT({safe_c}) FROM {safe_t}")
        row = cur.fetchone()
        total, non_null = row[0], row[1]

    if not total:
        return {"actual_value": 100.0, "passed": True, "detail": "Table is empty"}
    pct = round(non_null / total * 100, 2)
    return {
        "actual_value": pct,
        "passed": pct >= threshold,
        "detail": f"{non_null}/{total} non-null ({pct}%) — threshold {threshold}%",
    }


def _run_uniqueness(conn, db_kind: str, table: str, column: str, threshold: float) -> dict:
    """% distinct values >= threshold (0-100)."""
    safe_t = f'"{table}"'
    safe_c = f'"{column}"'
    if db_kind == "postgresql":
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT {safe_c}) FROM {safe_t}")
        total, distinct = cur.fetchone()
    else:
        cur = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT {safe_c}) FROM {safe_t}")
        row = cur.fetchone()
        total, distinct = row[0], row[1]

    if not total:
        return {"actual_value": 100.0, "passed": True, "detail": "Table is empty"}
    pct = round(distinct / total * 100, 2)
    return {
        "actual_value": pct,
        "passed": pct >= threshold,
        "detail": f"{distinct} distinct / {total} total ({pct}%) — threshold {threshold}%",
    }


def _run_range(conn, db_kind: str, table: str, column: str, params: dict) -> dict:
    """MIN(col) >= min_val AND MAX(col) <= max_val."""
    safe_t = f'"{table}"'
    safe_c = f'"{column}"'
    min_val = params.get("min_val")
    max_val = params.get("max_val")

    if db_kind == "postgresql":
        cur = conn.cursor()
        cur.execute(f"SELECT MIN({safe_c}), MAX({safe_c}) FROM {safe_t}")
        actual_min, actual_max = cur.fetchone()
    else:
        cur = conn.execute(f"SELECT MIN({safe_c}), MAX({safe_c}) FROM {safe_t}")
        row = cur.fetchone()
        actual_min, actual_max = row[0], row[1]

    passed = True
    violations = []
    if min_val is not None and actual_min is not None:
        try:
            if float(actual_min) < float(min_val):
                passed = False
                violations.append(f"MIN={actual_min} < {min_val}")
        except (TypeError, ValueError):
            pass
    if max_val is not None and actual_max is not None:
        try:
            if float(actual_max) > float(max_val):
                passed = False
                violations.append(f"MAX={actual_max} > {max_val}")
        except (TypeError, ValueError):
            pass

    actual_value = float(actual_max) if actual_max is not None else 0.0
    return {
        "actual_value": actual_value,
        "passed": passed,
        "detail": f"MIN={actual_min}, MAX={actual_max}" + (f" — violations: {'; '.join(violations)}" if violations else ""),
    }


def _run_pattern(conn, db_kind: str, table: str, column: str, threshold: float, params: dict) -> dict:
    """% of rows matching regex pattern >= threshold (0-100). SQLite/DuckDB only."""
    pattern_str = params.get("pattern", ".*")
    safe_t = f'"{table}"'
    safe_c = f'"{column}"'

    # Compile to validate pattern early
    try:
        compiled = re.compile(pattern_str)
    except re.error as exc:
        return {"actual_value": 0.0, "passed": False, "detail": f"Invalid regex: {exc}"}

    if db_kind == "postgresql":
        # Use SIMILAR TO approximation
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {safe_c} ~ %s) FROM {safe_t}", (pattern_str,))
            total, matched = cur.fetchone()
        except Exception as exc:
            return {"actual_value": 0.0, "passed": False, "detail": f"PG pattern error: {exc}"}
    else:
        try:
            cur = conn.execute(f"SELECT {safe_c} FROM {safe_t} WHERE {safe_c} IS NOT NULL LIMIT 50000")
            rows = [r[0] for r in cur.fetchall()]
            total = len(rows)
            matched = sum(1 for v in rows if compiled.search(str(v)))
        except Exception as exc:
            return {"actual_value": 0.0, "passed": False, "detail": f"Pattern error: {exc}"}

    if not total:
        return {"actual_value": 100.0, "passed": True, "detail": "No non-null rows"}
    pct = round(matched / total * 100, 2)
    return {
        "actual_value": pct,
        "passed": pct >= threshold,
        "detail": f"{matched}/{total} match pattern /{pattern_str}/ ({pct}%) — threshold {threshold}%",
    }


def _run_freshness(conn, db_kind: str, table: str, column: str, threshold: float, params: dict) -> dict:
    """MAX(col) >= NOW - threshold days."""
    safe_t = f'"{table}"'
    safe_c = f'"{column}"'
    days = float(params.get("days", threshold))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if db_kind == "postgresql":
        cur = conn.cursor()
        cur.execute(f"SELECT MAX({safe_c})::text FROM {safe_t}")
        max_val = cur.fetchone()[0]
    else:
        cur = conn.execute(f"SELECT MAX({safe_c}) FROM {safe_t}")
        max_val = str(cur.fetchone()[0] or "")

    if not max_val:
        return {"actual_value": 0.0, "passed": False, "detail": "No data or NULL timestamp"}
    passed = str(max_val) >= cutoff
    return {
        "actual_value": 0.0,
        "passed": passed,
        "detail": f"Latest: {max_val} — cutoff: {cutoff[:10]} ({days:.0f}d)",
    }


# ── Rule runner ───────────────────────────────────────────────────────────────


def run_rule(rule: dict, conn_params: dict) -> dict:
    """Execute a single quality rule against a connected DB.

    Returns:
        {passed, actual_value, threshold, detail, classification, error?}
    """
    check_type = rule.get("check_type", "")
    table = rule.get("table_name", "")
    column = rule.get("column_name", "")
    threshold = float(rule.get("threshold", 0))
    params = rule.get("params_json") or {}
    if isinstance(params, str):
        import json
        try:
            params = json.loads(params)
        except Exception:
            params = {}
    classification = rule.get("classification", "CUI // SP-CTI")

    try:
        conn, db_kind = _open_connection(conn_params)
        if check_type == "completeness":
            result = _run_completeness(conn, db_kind, table, column, threshold)
        elif check_type == "uniqueness":
            result = _run_uniqueness(conn, db_kind, table, column, threshold)
        elif check_type == "range":
            result = _run_range(conn, db_kind, table, column, params)
        elif check_type == "pattern":
            result = _run_pattern(conn, db_kind, table, column, threshold, params)
        elif check_type == "freshness":
            result = _run_freshness(conn, db_kind, table, column, threshold, params)
        else:
            result = {"actual_value": 0.0, "passed": False, "detail": f"Unknown check_type: {check_type}"}
        conn.close()
    except Exception as exc:
        result = {"actual_value": 0.0, "passed": False, "detail": "", "error": str(exc)}

    result["threshold"] = threshold
    result["classification"] = classification
    return result


# ── Batch runner ──────────────────────────────────────────────────────────────


def run_all_rules(design_id: str, conn_params: dict, ddc_conn) -> list[dict]:
    """Run all enabled rules for a design_id. Stores results in dd_quality_runs.

    ddc_conn: connection to the data_canvas.db (from get_canvas_connection()).
    Returns list of {rule, result} dicts.
    """
    from tools.common.helpers import now_isoformat

    rows = ddc_conn.execute(
        "SELECT * FROM dd_quality_rules WHERE design_id=? AND enabled=1",
        (design_id,),
    ).fetchall()

    output = []
    for row in rows:
        rule = dict(row)
        result = run_rule(rule, conn_params)
        run_id = str(uuid.uuid4())[:8]

        import json
        ddc_conn.execute(
            """INSERT INTO dd_quality_runs
               (id, rule_id, db_conn_json, passed, actual_value, threshold, detail, classification, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                rule["id"],
                json.dumps({k: v for k, v in conn_params.items() if k != "password"}),
                1 if result.get("passed") else 0,
                result.get("actual_value", 0.0),
                result.get("threshold", 0.0),
                result.get("detail", "")[:500],
                result.get("classification", "CUI // SP-CTI"),
                now_isoformat(),
            ),
        )
        output.append({"rule": rule, "result": result})

    ddc_conn.commit()
    return output


def quality_score(run_results: list[dict]) -> float:
    """Compute overall quality score 0-100 from run results."""
    if not run_results:
        return 100.0
    passed = sum(1 for r in run_results if r.get("result", {}).get("passed"))
    return round(passed / len(run_results) * 100, 1)
