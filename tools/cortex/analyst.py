# CUI // SP-CTI
"""Cortex Analyst — natural-language ask-your-data over IQE (primary path).

``ask()`` is the ICDEV equivalent of Snowflake Cortex Analyst: a natural
language question is translated to the IQE DSL (``foreach … where … select``),
validated against the registered collection adapters, and executed
deterministically. The LLM (when nl_to_iqe falls through to it) only ever
produces the *query*, never the answer, so every returned row is real data
from a registered collection — TRUST by construction.

Pipeline::

    question → resolve collections (explicit / canvas registry / question match)
             → nl_to_iqe() → parse() → authorize collections → execute_query()
             → CortexResult(text summary, rows in .data, Citation per collection)

In ``mode="auto"`` (the default), questions the IQE path cannot serve — no
matching collection, untranslatable, or targeting an unregistered collection —
fall back to the dashboard's NL→SQL pipeline (``tools.dashboard.nlq_processor``,
decision D34 read-only enforcement). ``mode="iqe"`` and ``mode="nlq"`` pin one
engine. The fallback never runs when the caller pinned the scope explicitly
via ``canvas=`` or ``collections=`` — an error there is a caller error.

A shared safety layer runs before EITHER engine executes anything:

1. Injection screen on the raw question — deterministic SQL-injection shapes
   (stacked statements, ``DROP TABLE``, ``UNION SELECT`` exfil, comment
   terminators) plus ``tools.llm.gateway.check_text()`` guardrails.
2. SELECT-only statement check on generated SQL (rejects INSERT/UPDATE/
   DELETE/DDL and multi-statement input) — reuses ``nlq_processor.validate_sql``.
3. Collection/table allowlist — only collections registered in
   ``args/component_registry.yaml`` ``iqe:`` blocks (via ``get_iqe_mapping()``)
   or directly on the IQE executor are queryable; SQL table names match a
   collection's dotted name or its ``dots→underscores`` table form
   (``aadc.designs`` → ``aadc_designs``).

Every safety rejection is audited (best-effort ``nlq_queries`` row with
status ``blocked``) and raised as :class:`CortexQueryBlocked`.

NOTE ON IMPORT NAMESPACE: the IQE collection registry is a module-level
singleton inside ``tools.iqe.executor``, and the ``tools/`` shim does NOT
alias ``tools.iqe.*`` and ``icdev.tools.iqe.*`` to the same module objects.
Every adapter (and the dashboard's ``iqe_dispatch``) registers/executes via
the ``tools.*`` namespace, so the IQE imports below intentionally use
``tools.*`` — importing the ``icdev.tools.*`` copies here would bind an
empty second registry and break ``isinstance`` checks on parsed AST nodes.
"""
from __future__ import annotations

import importlib
import re
import time
from typing import Any, Optional

from tools.iqe.ast_nodes import CollectionCall, ForeachNode
from tools.iqe.executor import execute_query, list_collections
from tools.iqe.nl_to_iqe import nl_to_iqe
from tools.iqe.parser import IQESyntaxError, parse
from tools.logging.icdev_logger import get_logger

from .schemas import Citation, CortexContext, CortexResult, GovernanceReport

logger = get_logger(__name__)

_VALID_MODES = ("auto", "iqe", "nlq")

# Governance gate names, in execution order, recorded on every ask().
_GATE_RESOLUTION = "collection_resolution"
_GATE_TRANSLATION = "iqe_translation"
_GATE_AUTHORIZATION = "collection_authorization"
_GATE_EXECUTION = "iqe_execution"
# Safety-layer and NLQ-fallback gates (ctx-analyst-02). The safety screen is
# recorded only when it warns or blocks — a clean pass adds no gate entry, so
# the IQE happy-path gate sequence asserted by ctx-analyst-01 stays stable.
_GATE_SAFETY = "safety_screen"
_GATE_NLQ_TRANSLATION = "nlq_translation"
_GATE_SQL_READONLY = "sql_readonly"
_GATE_ALLOWLIST = "table_allowlist"
_GATE_NLQ_EXECUTION = "nlq_execution"

# IQE gate failures that make an auto-mode question eligible for the NLQ
# fallback: the question could not be served, as opposed to a valid query
# whose execution failed (those propagate — masking data-layer errors by
# re-asking a different engine hides real faults).
_FALLBACK_GATES = (_GATE_RESOLUTION, _GATE_TRANSLATION, _GATE_AUTHORIZATION)

# Deterministic SQL-injection shapes screened on the RAW question before
# either engine runs. Patterns are attack-shaped, not keyword-shaped, so
# ordinary analytics phrasing ("recently updated projects") is not caught.
_QUESTION_INJECTION_PATTERNS = [
    (r";\s*\S", "stacked statement after ';'"),
    (r"\b(?:DROP|TRUNCATE|ALTER)\s+(?:TABLE|DATABASE|INDEX|VIEW)\b", "DDL statement"),
    (r"\bUNION\s+(?:ALL\s+)?SELECT\b", "UNION-based exfiltration"),
    (r"\bINSERT\s+INTO\b", "INSERT statement"),
    (r"\bDELETE\s+FROM\b", "DELETE statement"),
    (r"\bUPDATE\s+\S+\s+SET\b", "UPDATE statement"),
    (r"\bOR\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+", "tautology (OR 1=1)"),
    (r"--\s*$|/\*", "SQL comment terminator"),
]


class CortexAnalystError(Exception):
    """A question could not be answered through the IQE analyst path.

    Raised when no registered collection matches the question, the translated
    IQE fails to parse, the query targets an unregistered collection, or
    execution fails. Carries the ``question``, the offending ``collection``
    (when known), and the partial ``governance`` report so the ctx-analyst-02
    fallback can decide how to degrade.
    """

    def __init__(
        self,
        message: str,
        *,
        question: str = "",
        collection: str = "",
        governance: Optional[GovernanceReport] = None,
    ) -> None:
        super().__init__(message)
        self.question = question
        self.collection = collection
        self.governance = governance or GovernanceReport()


class CortexQueryBlocked(CortexAnalystError):
    """A question or generated query was refused by the shared safety layer.

    Raised on injection-shaped questions, non-SELECT / multi-statement SQL,
    and off-allowlist tables. The refusal is audited (``nlq_queries`` row,
    status ``blocked``) before this is raised; it is never eligible for the
    NLQ fallback — a blocked question stays blocked on every engine.
    """


def _record(report: GovernanceReport, gate: str, outcome: str) -> None:
    report.gates_run.append(gate)
    report.outcomes[gate] = outcome
    if outcome == "fail":
        report.blocked = True


def _mentions(question_lc: str, collection: str) -> bool:
    """True when *collection* is plausibly what the question is about.

    Matches the full dotted name anywhere, or the (roughly singularized)
    last segment at a word boundary — the same containment heuristic
    ``nl_to_iqe`` uses to pick a collection. Roots shorter than 3 chars are
    skipped: after rstrip("s") they match almost any sentence.
    """
    name = collection.lower()
    if name in question_lc:
        return True
    root = name.split(".")[-1].rstrip("s")
    if len(root) < 3:
        return False
    return re.search(rf"\b{re.escape(root)}", question_lc) is not None


# ---------------------------------------------------------------------------
# Shared safety layer (ctx-analyst-02) — applies to BOTH engines pre-execution
# ---------------------------------------------------------------------------
def _audit_refusal(
    question: str, sql: Optional[str], reason: str, ctx: CortexContext, started: float
) -> None:
    """Best-effort audited refusal — an ``nlq_queries`` row with status 'blocked'."""
    try:
        from tools.dashboard.nlq_processor import log_nlq_query

        log_nlq_query(
            question,
            sql,
            0,
            int((time.perf_counter() - started) * 1000),
            ctx.user_id or "cortex-analyst",
            "blocked",
            reason,
        )
    except Exception as exc:  # noqa: BLE001 — audit sink down must not mask the refusal
        logger.warning("cortex.analyst: refusal audit failed: %s", exc)


def _refuse(
    question: str,
    sql: Optional[str],
    reason: str,
    ctx: CortexContext,
    governance: GovernanceReport,
    gate: str,
    started: float,
) -> None:
    """Record the gate failure, audit the refusal, raise CortexQueryBlocked."""
    _record(governance, gate, "fail")
    _audit_refusal(question, sql, reason, ctx, started)
    raise CortexQueryBlocked(reason, question=question, governance=governance)


def _screen_question(
    question: str, ctx: CortexContext, governance: GovernanceReport, started: float
) -> None:
    """Injection screen on the raw question, before either engine runs.

    Deterministic SQL-injection shapes first (no config/DB dependency), then
    the LLM gateway's guardrails. A gateway outage fails open — the
    deterministic screen already ran — unless ``ctx.fail_closed`` is set.
    """
    for pattern, label in _QUESTION_INJECTION_PATTERNS:
        if re.search(pattern, question, re.IGNORECASE):
            _refuse(
                question,
                None,
                f"question rejected by injection screen: {label}",
                ctx,
                governance,
                _GATE_SAFETY,
                started,
            )
    try:
        from tools.llm.gateway import check_text

        verdict = check_text(question).get("pre_invoke") or {}
    except Exception as exc:  # noqa: BLE001
        if ctx.fail_closed:
            _refuse(
                question,
                None,
                f"gateway screen unavailable and ctx.fail_closed is set: {exc}",
                ctx,
                governance,
                _GATE_SAFETY,
                started,
            )
        logger.warning("cortex.analyst: gateway check_text unavailable: %s", exc)
        return
    if not verdict.get("allowed", True):
        _refuse(
            question,
            None,
            f"question rejected by LLM gateway: {verdict.get('blocked_reason')}",
            ctx,
            governance,
            _GATE_SAFETY,
            started,
        )
    if verdict.get("warnings"):
        _record(governance, _GATE_SAFETY, "warn")


def _allowed_tables() -> set:
    """Queryable identifiers: registered IQE collections and their table forms.

    Built from the component registry's ``iqe:`` blocks (``get_iqe_mapping()``)
    plus collections registered directly on the executor. Each collection
    allows both its dotted name and the dots→underscores variant
    (``aadc.designs`` → ``aadc_designs``) — the convention every IQE adapter
    uses for its backing table.
    """
    names = set(list_collections())
    for _module, colls in _canvas_iqe_mapping().values():
        names.update(colls)
    allowed = set()
    for name in names:
        n = str(name).lower()
        allowed.add(n)
        allowed.add(n.replace(".", "_"))
    return allowed


_CTE_NAME_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_]\w*)\s+AS\s*\(", re.IGNORECASE)
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)


def _extract_sql_tables(sql: str) -> list[str]:
    """Table identifiers a SELECT reads from, excluding its own CTE names."""
    ctes = {m.lower() for m in _CTE_NAME_RE.findall(sql)}
    tables: list[str] = []
    for match in _TABLE_REF_RE.findall(sql):
        t = match.lower()
        if t not in ctes and t not in tables:
            tables.append(t)
    return tables


def _validate_sql_safety(
    question: str,
    sql: str,
    ctx: CortexContext,
    governance: GovernanceReport,
    started: float,
) -> None:
    """SELECT-only + single-statement + table-allowlist gates on generated SQL."""
    try:
        from tools.dashboard.nlq_processor import validate_sql

        ok, err = validate_sql(sql)
    except Exception as exc:  # noqa: BLE001 — validator unavailable = fail closed
        ok, err = False, f"read-only validator unavailable: {exc}"
    if ok and ";" in sql.rstrip().rstrip(";"):
        ok, err = False, "multi-statement SQL is not allowed"
    if not ok:
        _refuse(
            question,
            sql,
            f"generated SQL rejected: {err}",
            ctx,
            governance,
            _GATE_SQL_READONLY,
            started,
        )
    _record(governance, _GATE_SQL_READONLY, "pass")

    tables = _extract_sql_tables(sql)
    if not tables:
        _refuse(
            question,
            sql,
            "no table reference found in generated SQL; cannot verify allowlist",
            ctx,
            governance,
            _GATE_ALLOWLIST,
            started,
        )
    off_allowlist = [t for t in tables if t not in _allowed_tables()]
    if off_allowlist:
        _refuse(
            question,
            sql,
            f"table(s) not on the registered-collection allowlist: {off_allowlist}",
            ctx,
            governance,
            _GATE_ALLOWLIST,
            started,
        )
    _record(governance, _GATE_ALLOWLIST, "pass")


def _canvas_iqe_mapping() -> dict[str, tuple[str, list[str]]]:
    """Canvas key → (adapter_module, collections), from the component registry."""
    try:
        from tools.config.component_registry import get_registry

        return get_registry().get_iqe_mapping()
    except Exception as exc:  # noqa: BLE001 — registry unavailable ≠ analyst down
        logger.warning("cortex.analyst: component registry unavailable: %s", exc)
        return {}


def _import_adapter(module: str) -> bool:
    """Import *module* so it registers its collections; False on failure."""
    try:
        importlib.import_module(module)
        return True
    except Exception as exc:  # noqa: BLE001 — one broken adapter must not kill ask()
        logger.warning("cortex.analyst: adapter import failed [%s]: %s", module, exc)
        return False


def _resolve_collections(
    question: str,
    canvas: Optional[str],
    collections: Optional[list[str]],
) -> list[str]:
    """Determine which collections the question should be translated against.

    Precedence: explicit *collections* > *canvas* registry entry > question
    match against directly-registered collections and canvas registry entries.
    """
    if collections:
        return [str(c) for c in collections]

    mapping = _canvas_iqe_mapping()

    if canvas:
        entry = mapping.get(canvas.strip().lower())
        if not entry:
            raise CortexAnalystError(
                f"unknown canvas {canvas!r}; valid: {sorted(mapping)}",
                question=question,
            )
        adapter_module, canvas_colls = entry
        _import_adapter(adapter_module)
        return list(canvas_colls)

    q = question.lower()
    resolved: list[str] = []

    # Collections already registered on the executor (covers tests and any
    # caller that registered adapters directly).
    for name in list_collections():
        if _mentions(q, name) and name not in resolved:
            resolved.append(name)

    # Canvas registry entries whose collections the question mentions — import
    # the adapter so its collections register, then add hits first and the
    # canvas's sibling collections after (context for nl_to_iqe).
    for key in sorted(mapping):
        adapter_module, canvas_colls = mapping[key]
        hits = [c for c in canvas_colls if _mentions(q, c)]
        if not hits or not _import_adapter(adapter_module):
            continue
        for c in hits + [c for c in canvas_colls if c not in hits]:
            if c not in resolved:
                resolved.append(c)

    if not resolved:
        raise CortexAnalystError(
            "no registered collection matches the question; "
            "pass canvas= or collections= to disambiguate",
            question=question,
        )
    return resolved


def _ast_collections(ast: ForeachNode) -> list[str]:
    """Extract the collection name(s) a parsed IQE query reads from."""
    coll = ast.collection
    if isinstance(coll, CollectionCall):
        name = str(coll)
        args = [str(a.value) for a in coll.args]
        if name == "union":
            return args
        if name == "join":
            return args[:2]  # third arg is the join key, not a collection
        return [name]
    return [str(coll)]


def _apply_security_context(conn: Any, ctx: CortexContext) -> None:
    """Thread tenant/classification from the CortexContext into the connection."""
    setter = getattr(conn, "set_security_context", None)
    if setter is None:
        return
    try:
        from tools.security.security_context import SecurityContext

        setter(
            SecurityContext(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id or None,
                classification=ctx.classification or "CUI",
            )
        )
    except Exception as exc:  # noqa: BLE001
        if ctx.fail_closed:
            raise CortexAnalystError(
                f"failed to apply security context: {exc}"
            ) from exc
        logger.warning("cortex.analyst: security context not applied: %s", exc)


def _open_connection() -> Any:
    from tools.db.storage import get_connection

    return get_connection()


def _format_answer(rows: list[dict], targets: list[str], explanation: str) -> str:
    """Human-readable summary of an analyst result set."""
    n = len(rows)
    lines = [f"{n} row{'s' if n != 1 else ''} from {', '.join(targets)}."]
    if explanation:
        lines.append(explanation + ".")
    for row in rows[:3]:
        pairs = ", ".join(f"{k}={row[k]!r}" for k in list(row)[:6])
        lines.append(f"- {pairs}")
    if n > 3:
        lines.append(f"… and {n - 3} more row{'s' if n - 3 != 1 else ''}.")
    return "\n".join(lines)


def _ask_nlq(
    question: str,
    ctx: CortexContext,
    governance: GovernanceReport,
    started: float,
    mode: str,
) -> CortexResult:
    """NL→SQL path: nlq_processor generation, shared safety gates, execution.

    Reuses the dashboard NLQ pipeline piecewise (rather than
    ``process_nlq_query``) so the allowlist + read-only gates run between
    generation and execution. Execution goes through ``execute_safely``
    (row cap + timeout, decision D34); successes and refusals both land in
    the ``nlq_queries`` audit table.
    """
    from tools.dashboard import nlq_processor as _nlq

    try:
        schema = _nlq.extract_schema()
    except Exception as exc:  # noqa: BLE001 — schema context is best-effort
        logger.warning("cortex.analyst: NLQ schema extraction failed: %s", exc)
        schema = {}

    sql = (_nlq.generate_sql_via_bedrock(question, schema) or "").strip()
    if not sql:
        _record(governance, _GATE_NLQ_TRANSLATION, "fail")
        raise CortexAnalystError(
            "question did not translate to SQL (NLQ fallback)",
            question=question,
            governance=governance,
        )
    _record(governance, _GATE_NLQ_TRANSLATION, "pass")

    _validate_sql_safety(question, sql, ctx, governance, started)

    try:
        results = _nlq.execute_safely(sql)
    except Exception as exc:
        _record(governance, _GATE_NLQ_EXECUTION, "fail")
        raise CortexAnalystError(
            f"NLQ execution failed: {exc}", question=question, governance=governance
        ) from exc
    _record(governance, _GATE_NLQ_EXECUTION, "pass")

    duration_ms = int((time.perf_counter() - started) * 1000)
    rows = results.get("rows", [])
    _nlq.log_nlq_query(
        question, sql, len(rows), duration_ms, ctx.user_id or "cortex-analyst", "success"
    )

    tables = _extract_sql_tables(sql)
    citations = [
        Citation(
            source_id=f"nlq:{table}",
            source_type="analyst",
            source_table=table,
            title=f"NL-to-SQL query over {table}",
            snippet=sql,
            classification=ctx.classification or "CUI",
        )
        for table in tables
    ]

    return CortexResult(
        text=_format_answer(rows, tables, ""),
        citations=citations,
        governance=governance,
        provider="nlq",
        latency_ms=duration_ms,
        grounded=True,
        data={
            "rows": rows,
            "row_count": results.get("row_count", len(rows)),
            "columns": results.get("columns", []),
            "truncated": results.get("truncated", False),
            "sql": sql,
            "collections": tables,
            "mode": mode,
        },
    )


def ask(
    question: str,
    mode: str = "auto",
    ctx: Optional[CortexContext] = None,
    *,
    canvas: Optional[str] = None,
    collections: Optional[list[str]] = None,
    conn: Any = None,
) -> CortexResult:
    """Answer a natural-language data question (IQE primary, NL→SQL fallback).

    Args:
        question:    Free-form question (e.g. "show all satellites").
        mode:        "auto" — IQE first, NL→SQL fallback when IQE cannot
                     resolve/translate/authorize the question (never on
                     execution failures, and never when ``canvas=`` or
                     ``collections=`` pinned the scope);
                     "iqe" — IQE only; "nlq" — NL→SQL only.
        ctx:         Caller identity/policy context; tenant_id and
                     classification are threaded into the DB connection on
                     the IQE path. ``fail_closed`` also hardens the safety
                     screen (gateway outage blocks instead of degrading).
        canvas:      Restrict to one canvas's collections (component registry key).
        collections: Explicit collection names, bypassing resolution.
        conn:        Existing DB connection for the IQE path; one is opened
                     (and closed) via ``get_connection()`` when omitted. The
                     NLQ path always manages its own connection via
                     ``execute_safely``.

    Returns:
        CortexResult — ``text`` is a formatted summary, ``data`` holds
        ``rows``/``row_count`` plus the executed ``iqe`` or ``sql``, and
        ``citations`` carries one ``Citation(source_type="analyst")`` per
        collection/table read. ``provider`` is "iqe" or "nlq".

    Raises:
        CortexQueryBlocked: the shared safety layer refused the question or
            the generated SQL (injection shape, non-SELECT, multi-statement,
            or off-allowlist table). The refusal is audited first.
        CortexAnalystError: no engine could answer the question, or
            execution failed.
    """
    started = time.perf_counter()
    governance = GovernanceReport()

    question = (question or "").strip()
    if not question:
        raise CortexAnalystError("question is required", governance=governance)
    if mode not in _VALID_MODES:
        raise CortexAnalystError(
            f"unsupported mode {mode!r}; expected one of {list(_VALID_MODES)}",
            question=question,
            governance=governance,
        )
    ctx = ctx or CortexContext()

    _screen_question(question, ctx, governance, started)

    if mode == "nlq":
        return _ask_nlq(question, ctx, governance, started, mode)

    try:
        return _ask_iqe(
            question,
            mode,
            ctx,
            governance,
            started,
            canvas=canvas,
            collections=collections,
            conn=conn,
        )
    except CortexQueryBlocked:
        raise
    except CortexAnalystError as exc:
        failed_gates = {g for g, o in exc.governance.outcomes.items() if o == "fail"}
        eligible = (
            mode == "auto"
            and not canvas
            and not collections
            and failed_gates
            and failed_gates <= set(_FALLBACK_GATES)
        )
        if not eligible:
            raise
        logger.info("cortex.analyst: IQE path failed (%s); falling back to NLQ", exc)
        # The IQE gate failure stays recorded in outcomes as history, but it
        # is a degrade, not a refusal — only a safety/NLQ failure below may
        # re-set blocked on the report the caller finally receives.
        governance.blocked = False
        try:
            return _ask_nlq(question, ctx, governance, started, mode)
        except CortexAnalystError as nlq_exc:
            raise nlq_exc from exc


def _ask_iqe(
    question: str,
    mode: str,
    ctx: CortexContext,
    governance: GovernanceReport,
    started: float,
    *,
    canvas: Optional[str] = None,
    collections: Optional[list[str]] = None,
    conn: Any = None,
) -> CortexResult:
    """IQE-primary path: resolve → translate → authorize → execute."""
    try:
        resolved = _resolve_collections(question, canvas, collections)
    except CortexAnalystError as exc:
        _record(governance, _GATE_RESOLUTION, "fail")
        exc.governance = governance
        raise
    _record(governance, _GATE_RESOLUTION, "pass")

    translated = nl_to_iqe(question, resolved)
    iqe_str = (translated.get("iqe") or "").strip()
    explanation = translated.get("explanation") or ""
    try:
        ast = parse(iqe_str)
    except IQESyntaxError as exc:
        _record(governance, _GATE_TRANSLATION, "fail")
        raise CortexAnalystError(
            f"question did not translate to parseable IQE ({iqe_str!r}): {exc}",
            question=question,
            governance=governance,
        ) from exc
    _record(governance, _GATE_TRANSLATION, "pass")

    targets = _ast_collections(ast)
    registered = set(list_collections())
    for target in targets:
        if target not in registered:
            _record(governance, _GATE_AUTHORIZATION, "fail")
            raise CortexAnalystError(
                f"unknown collection {target!r}: not registered with the IQE executor",
                question=question,
                collection=target,
                governance=governance,
            )
    _record(governance, _GATE_AUTHORIZATION, "pass")

    owns_conn = conn is None
    if owns_conn:
        conn = _open_connection()
    _apply_security_context(conn, ctx)
    try:
        rows = execute_query(ast, conn)
    except Exception as exc:
        _record(governance, _GATE_EXECUTION, "fail")
        raise CortexAnalystError(
            f"IQE execution failed over {targets}: {exc}",
            question=question,
            collection=targets[0] if targets else "",
            governance=governance,
        ) from exc
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass
    _record(governance, _GATE_EXECUTION, "pass")

    citations = [
        Citation(
            source_id=f"iqe:{target}",
            source_type="analyst",
            source_table=target,
            title=f"IQE query over {target}",
            snippet=iqe_str,
            classification=ctx.classification or "CUI",
        )
        for target in targets
    ]

    return CortexResult(
        text=_format_answer(rows, targets, explanation),
        citations=citations,
        governance=governance,
        provider="iqe",
        latency_ms=int((time.perf_counter() - started) * 1000),
        grounded=True,
        data={
            "rows": rows,
            "row_count": len(rows),
            "iqe": iqe_str,
            "explanation": explanation,
            "collections": targets,
            "mode": mode,
        },
    )
