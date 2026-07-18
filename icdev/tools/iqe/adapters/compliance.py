# CUI // SP-CTI
"""IQE compliance collection adapters.

Importing this module registers these collections on the module-level Executor:
  compliance.snapshots      — PI-level compliance score snapshots (pi_compliance_tracking)
  compliance.controls       — Per-project control implementation status (project_controls + compliance_controls)
  compliance.violations     — Open findings / POA&M items (poam_items)
  compliance.twin_snapshots — cATO twin per-control snapshot rows (compliance_twin_snapshots)
  compliance.twin_violations— cATO twin violation rows (compliance_twin_violations)
  compliance.twin_runs      — cATO twin run summary rows (compliance_twin_runs)

The ``compliance.twin_*`` collections back the cATO Twin Genesis reflex
(``tools/genesis/reflexes/cato_twin.py``) and CLI. They are parameterised by
framework and (optionally) project_id — ``compliance.twin_snapshots("FedRAMP
Moderate", "proj-A")`` — and resolve the *latest snapshot per project* the same
way the retired Phase-1 regex engine did, but through the maintained IQE
parser/executor. ``run_query`` is the validated entrypoint that enforces the
fail-closed field whitelist (unknown token → ValueError, never silent widening)
and project scoping that the hardened engine guaranteed (bdr-sec-1 / bdt-iqe-1).
"""
from __future__ import annotations

from typing import Any, Iterator, List, Optional

from tools.iqe.ast_nodes import AttrRef, BinOp, CollectionCall, ForeachNode, Literal
from tools.iqe.executor import execute_query, register_collection
from tools.iqe.parser import parse


def snapshots_adapter(conn: Any) -> list[dict]:
    """Return rows from pi_compliance_tracking."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, project_id, pi_number, pi_start_date, pi_end_date, "
        "compliance_score_start, compliance_score_end, controls_implemented, "
        "controls_remaining, poam_items_closed, poam_items_opened, "
        "findings_remediated, notes, created_at "
        "FROM pi_compliance_tracking"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def controls_adapter(conn: Any) -> list[dict]:
    """Return project_controls rows enriched with compliance_controls metadata."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT pc.id, pc.project_id, pc.control_id, "
        "cc.family, cc.title, cc.impact_level, "
        "pc.implementation_status, pc.implementation_description, "
        "pc.responsible_role "
        "FROM project_controls pc "
        "LEFT JOIN compliance_controls cc ON cc.id = pc.control_id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def violations_adapter(conn: Any) -> list[dict]:
    """Return rows from poam_items."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, project_id, weakness_id, weakness_description, "
        "severity, source, control_id, status, corrective_action, "
        "milestone_date, completion_date, responsible_party, created_at "
        "FROM poam_items"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# cATO Twin collections (compliance_twin_*) — bdt-iqe-1
#
# These replace the retired Phase-1 regex engine
# (tools/boundary_canvas/cato_twin/query_engine.py). Each adapter is
# parameterised by ``framework`` and an optional ``project_id`` and returns the
# *latest snapshot per project* for that framework — authored PG-native (``%s``
# placeholders), scoped at the SQL layer when a project_id is supplied. Rows are
# keyed by column name; a ``status`` alias mirrors ``implementation_status`` so
# queries can read ``ctrl.status`` (the historical field) unchanged. Any SQL
# error degrades to an empty list after a rollback (mirrors the bdc.py adapters
# and keeps a PostgreSQL transaction from being left aborted).
# ---------------------------------------------------------------------------

# Per-collection field whitelists. A projection/predicate token that is not a
# key below is rejected by ``run_query`` with ValueError (fail-closed) — this is
# the single choke point that preserves the hardened engine's strictness on the
# lenient (silent-None) IQE executor.
_TWIN_SNAPSHOT_FIELDS = frozenset({
    "snapshot_id", "project_id", "framework", "control_id",
    "implementation_status", "status", "evidence_ref", "score",
    "assessor", "notes", "classification", "created_at",
})
_TWIN_VIOLATION_FIELDS = frozenset({
    "id", "snapshot_id", "project_id", "framework", "control_id",
    "violation_type", "severity", "details", "poam_id",
    "classification", "created_at",
})
_TWIN_RUN_FIELDS = frozenset({
    "snapshot_id", "framework", "project_id", "triggered_by",
    "started_at", "completed_at", "total_controls", "satisfied",
    "partially_satisfied", "not_satisfied", "not_applicable",
    "not_assessed", "classification", "created_at",
})

_TWIN_FIELD_WHITELISTS = {
    "compliance.twin_snapshots": _TWIN_SNAPSHOT_FIELDS,
    "compliance.twin_violations": _TWIN_VIOLATION_FIELDS,
    "compliance.twin_runs": _TWIN_RUN_FIELDS,
}


def _twin_conn(conn: Any):
    """Return (connection, owned) — owned=True means the adapter must close it."""
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection(), True


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def twin_snapshots_adapter(conn: Any, framework: str,
                           project_id: Optional[str] = None) -> list[dict]:
    """Latest-snapshot-per-project control rows for a framework.

    Scoped to ``project_id`` at the SQL layer when supplied (parameterised),
    which is the cross-project-bleed guard the hardened regex engine provided.
    """
    c, owned = _twin_conn(conn)
    try:
        run_where = "WHERE framework = %s"
        run_params: List[Any] = [framework]
        if project_id is not None:
            run_where += " AND project_id = %s"
            run_params.append(project_id)
        # nosec B608 — no user tokens interpolated; only static SQL + %s params.
        sql = f"""
            WITH latest_run AS (
                SELECT project_id, MAX(started_at) AS max_started
                FROM compliance_twin_runs
                {run_where}
                GROUP BY project_id
            ),
            latest_snap AS (
                SELECT s.*
                FROM compliance_twin_snapshots s
                JOIN latest_run lr
                  ON s.project_id = lr.project_id AND s.framework = %s
                JOIN compliance_twin_runs r
                  ON r.snapshot_id = s.snapshot_id AND r.started_at = lr.max_started
            )
            SELECT snapshot_id, project_id, framework, control_id,
                   implementation_status, implementation_status AS status,
                   evidence_ref, score, assessor, notes, classification, created_at
            FROM latest_snap
        """  # nosec B608
        params: List[Any] = run_params + [framework]
        if project_id is not None:
            sql += " WHERE project_id = %s"
            params.append(project_id)
        return _rows(c.execute(sql, params))
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        return []
    finally:
        if owned:
            c.close()


def twin_violations_adapter(conn: Any, framework: str,
                            project_id: Optional[str] = None) -> list[dict]:
    """Latest snapshot's violation rows for a framework (optionally project-scoped)."""
    c, owned = _twin_conn(conn)
    try:
        # nosec B608 — no user tokens interpolated; only static SQL + %s params.
        sql = """
            SELECT v.id, v.snapshot_id, v.project_id, v.framework, v.control_id,
                   v.violation_type, v.severity, v.details, v.poam_id,
                   v.classification, v.created_at
            FROM compliance_twin_violations v
            JOIN (
                SELECT snapshot_id FROM compliance_twin_runs
                WHERE framework = %s
                ORDER BY started_at DESC LIMIT 1
            ) r ON v.snapshot_id = r.snapshot_id
        """  # nosec B608
        params: List[Any] = [framework]
        if project_id is not None:
            sql += " WHERE v.project_id = %s"
            params.append(project_id)
        return _rows(c.execute(sql, params))
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        return []
    finally:
        if owned:
            c.close()


def twin_runs_adapter(conn: Any, framework: str,
                      project_id: Optional[str] = None) -> list[dict]:
    """Run summary rows for a framework (optionally project-scoped)."""
    c, owned = _twin_conn(conn)
    try:
        # nosec B608 — no user tokens interpolated; only static SQL + %s params.
        sql = """
            SELECT snapshot_id, framework, project_id, triggered_by,
                   started_at, completed_at, total_controls, satisfied,
                   partially_satisfied, not_satisfied, not_applicable,
                   not_assessed, classification, created_at
            FROM compliance_twin_runs
            WHERE framework = %s
        """  # nosec B608
        params: List[Any] = [framework]
        if project_id is not None:
            sql += " AND project_id = %s"
            params.append(project_id)
        return _rows(c.execute(sql, params))
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        return []
    finally:
        if owned:
            c.close()


# ---------------------------------------------------------------------------
# Validated IQE entrypoint for the compliance twin surface.
# ---------------------------------------------------------------------------

def _iter_attr_refs(node: Any) -> Iterator[AttrRef]:
    """Yield every AttrRef inside a predicate AST (BinOp tree / bare ref)."""
    if isinstance(node, AttrRef):
        yield node
    elif isinstance(node, BinOp):
        if node.left is not None:
            yield from _iter_attr_refs(node.left)
        if node.right is not None:
            yield from _iter_attr_refs(node.right)
    # Literal / None: no field references.


def _validate_fields(ast: ForeachNode, whitelist: frozenset) -> None:
    """Fail-closed: every projected/filtered field must be whitelisted.

    An unmapped token raises ValueError instead of silently resolving to None
    (which the lenient executor would otherwise do, widening the result set).
    """
    var = ast.var
    refs: List[AttrRef] = []
    if ast.select is not None and not ast.select.wildcard:
        refs.extend(ast.select.fields)
    for clause in ast.where_clauses:
        refs.extend(_iter_attr_refs(clause.predicate))
    for ref in refs:
        parts = ref.parts[1:] if ref.parts and ref.parts[0] == var else list(ref.parts)
        if not parts:
            raise ValueError(f"Empty IQE field reference: {ref}")
        field = parts[0]
        if field not in whitelist:
            raise ValueError(
                f"Unknown or disallowed IQE field token: {'.'.join(ref.parts)!r}"
            )


def run_query(iqe_string: str, conn: Any = None,
              project_id: Optional[str] = None) -> list[dict]:
    """Execute a compliance-twin IQE query through the maintained executor.

    Parses ``iqe_string`` with the shared IQE parser, validates every field
    against the target collection's whitelist (fail-closed on unknown tokens),
    injects ``project_id`` as the collection's scope argument when supplied, and
    dispatches to the module-level Executor.

    Args:
        iqe_string: IQE DSL query, e.g.
            ``foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate")
              where ctrl.status != "satisfied" select ctrl.control_id``.
        conn:       Optional DB connection; one is opened/closed when omitted.
        project_id: Optional project scope. When provided, snapshot/violation/run
                    reads are restricted to this project (parameterised), the
                    cross-project-bleed guard from the hardened engine.

    Returns:
        list[dict] projected rows.

    Raises:
        ValueError: on an unknown collection, or an unmapped projection/predicate
            field (fail-closed — never a silently widened result set).
        IQESyntaxError: on a malformed query (fail-closed at parse time).
    """
    ast = parse(iqe_string)

    coll = ast.collection
    cname = str(coll.name) if isinstance(coll, CollectionCall) else str(coll)
    whitelist = _TWIN_FIELD_WHITELISTS.get(cname)
    if whitelist is None:
        raise ValueError(f"Unknown or disallowed IQE collection: {cname!r}")

    _validate_fields(ast, whitelist)

    # Inject the project scope as the collection's second argument so the adapter
    # filters at the SQL layer. The query string itself carries only the
    # framework, keeping scope a runtime concern (as with the old run_query).
    if project_id is not None and isinstance(coll, CollectionCall):
        coll.args = list(coll.args[:1]) + [Literal(value=project_id)]

    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        return execute_query(ast, conn)
    finally:
        if own:
            conn.close()


register_collection("compliance.snapshots", snapshots_adapter)
register_collection("compliance.controls", controls_adapter)
register_collection("compliance.violations", violations_adapter)
register_collection("compliance.twin_snapshots", twin_snapshots_adapter)
register_collection("compliance.twin_violations", twin_violations_adapter)
register_collection("compliance.twin_runs", twin_runs_adapter)
