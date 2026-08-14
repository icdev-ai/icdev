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

TRUST labeling (ctx-analyst-03): every result leaving ``ask()`` carries
validated citations and a confidence label so governance layers can gate
analyst answers exactly like search-derived ones. Each collection/table read
gets a ``Citation(source_type="analyst")`` whose ``snippet`` is a compact row
summary (the evidence text). Raw row answers are grounded by construction and
marked ``grounding="rows_by_construction"``. When ``summarize=True`` produces
LLM prose, the prose must carry inline ``[source: <id>]`` tags referencing
those citations; the SHARED ``tools.quality.citation_grounding`` module
(TRUST invariant — never re-implemented here) parses/validates the tags and
scores attribution, and missing/hallucinated tags or an "abstain" attribution
band flag the result ``grounded=False`` (a degrade, not a refusal — the text
is returned, flagged, and the ``citation_validation`` gate records "fail"
without setting ``governance.blocked``).

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
from tools.quality.citation_grounding import (
    classify_confidence,
    compute_attribution_score,
    parse_citations,
    validate_citations,
)

from .config import resolve_fail_closed
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
# Citation/grounding gate (ctx-analyst-03): recorded only when the answer text
# is LLM-summarized — raw row answers are grounded by construction and add no
# gate entry, so the gate sequences asserted by ctx-analyst-01/-02 stay stable.
_GATE_CITATION = "citation_validation"

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
        if resolve_fail_closed(ctx):
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
    allowed = _allowed_tables()
    off_allowlist = [t for t in tables if t not in allowed]
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


def _build_security_context(ctx: CortexContext) -> Any:
    """Construct a platform SecurityContext from the CortexContext.

    tenant_id and classification key the RLS predicates (tenant scope +
    Bell-LaPadula read-down via ``classifications_dominated_by()``); an empty
    tenant_id maps to ``None`` so the injector treats it as "unscoped" rather
    than filtering on the literal empty string.
    """
    from tools.security.security_context import SecurityContext

    return SecurityContext(
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id or None,
        classification=ctx.classification or "CUI",
    )


def _apply_security_context(conn: Any, ctx: CortexContext) -> None:
    """Thread tenant/classification from the CortexContext into the connection.

    A connection with no ``set_security_context`` cannot carry the tenant /
    classification RLS predicate, so the query would run UNSCOPED — read-down
    and tenant isolation silently lost. Treat that exactly like a failed apply:
    warn by default, and refuse (raise) when ``ctx.fail_closed``. Never silently
    fall through to an unscoped connection.
    """
    setter = getattr(conn, "set_security_context", None)
    if setter is None:
        msg = "connection cannot carry a security context (query would be unscoped)"
        if resolve_fail_closed(ctx):
            raise CortexAnalystError(f"failed to apply security context: {msg}")
        logger.warning("cortex.analyst: security context not applied: %s", msg)
        return
    try:
        setter(_build_security_context(ctx))
    except Exception as exc:  # noqa: BLE001
        if resolve_fail_closed(ctx):
            raise CortexAnalystError(
                f"failed to apply security context: {exc}"
            ) from exc
        logger.warning("cortex.analyst: security context not applied: %s", exc)


def _open_connection() -> Any:
    from tools.db.storage import get_connection

    return get_connection()


def _execute_nlq_readonly(sql: str, ctx: CortexContext) -> dict:
    """Execute a validated read-only SELECT with the caller's RLS context applied.

    The dashboard's ``nlq_processor.execute_safely`` opens a security-context-
    free connection, so on the NLQ fallback the tenant_id + classification
    read-down isolation that the IQE path applies via ``_apply_security_context``
    would be silently lost — tenant A's question could read tenant B's rows
    (ctx-expose-03). This mirrors execute_safely's ``MAX_ROWS`` cap but threads
    the CortexContext into the connection (PG session vars + app-level predicate
    injection) so RLS filters the result set. Row shape is normalized for both
    psycopg2 RealDictRow (mapping) and sqlite3 tuple cursors.
    """
    from tools.dashboard import nlq_processor as _nlq

    conn = _open_connection()
    _apply_security_context(conn, ctx)
    try:
        # busy_timeout is a SQLite pragma; issuing it on PG errors and aborts
        # the transaction, so it is applied on the SQLite backend only.
        if getattr(conn, "_backend", "sqlite") == "sqlite":
            conn.execute(f"PRAGMA busy_timeout = {_nlq.QUERY_TIMEOUT_SECONDS * 1000}")
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(_nlq.MAX_ROWS)
        truncated = cursor.fetchone() is not None
        out = [dict(r) if isinstance(r, dict) else dict(zip(columns, r)) for r in rows]
        return {
            "columns": columns,
            "rows": out,
            "row_count": len(out),
            "truncated": truncated,
            "max_rows": _nlq.MAX_ROWS,
        }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass


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


# ---------------------------------------------------------------------------
# Citations + confidence labeling (ctx-analyst-03) — TRUST layer on ask()
# ---------------------------------------------------------------------------
def _rows_snippet(rows: list, limit: int = 3) -> str:
    """Compact row summary used as citation evidence (``Citation.snippet``).

    This is the text attribution is scored against, so it holds data values,
    not the executed query (that stays in ``data["iqe"]``/``data["sql"]``).
    Rows from a join/union are not attributable per source deterministically,
    so every citation on a multi-source result carries the same summary.
    """
    n = len(rows)
    parts = [f"{n} row{'s' if n != 1 else ''}"]
    for row in rows[:limit]:
        parts.append(", ".join(f"{k}={row[k]!r}" for k in list(row)[:6]))
    return "; ".join(parts)


def _label_rows_result(result: CortexResult) -> CortexResult:
    """Mark a raw row-only answer grounded by construction.

    No LLM touched the answer text — every value is a real row from a
    registered collection — so it is grounded without citation tags.
    """
    result.grounded = True
    result.metadata.update(
        {
            "grounding": "rows_by_construction",
            "confidence": "include",
            "confidence_score": 1.0,
        }
    )
    return result


def _label_llm_result(result: CortexResult) -> CortexResult:
    """Validate and grade an LLM-summarized answer via the SHARED grounding module.

    Inline ``[source: <id>]`` tags are parsed/validated by
    ``tools.quality.citation_grounding`` against this result's citation ids
    (``source_id`` or ``source_table`` forms both accepted); attribution is
    the best token-overlap of any citation snippet with the answer text.
    ``grounded`` requires tags present, none hallucinated, and a non-"abstain"
    attribution band. A defect degrades (result returned flagged) rather than
    refuses, so the gate outcome is recorded without setting
    ``governance.blocked`` — that flag means the question was refused.
    """
    allowed: set = set()
    for c in result.citations:
        allowed.update(x for x in (c.source_id, c.source_table) if x)
    cited = parse_citations(result.text)
    report = validate_citations(result.text, allowed)

    attribution = {
        c.source_id: compute_attribution_score(c.snippet, result.text)
        for c in result.citations
        if c.source_id
    }
    score = max(attribution.values(), default=0.0)
    label = classify_confidence(score)

    result.grounded = bool(cited) and report["valid"] and label != "abstain"
    result.metadata.update(
        {
            "grounding": "llm_summary",
            "confidence": label,
            "confidence_score": score,
            "citation_report": report,
            "attribution": attribution,
        }
    )
    result.governance.gates_run.append(_GATE_CITATION)
    result.governance.outcomes[_GATE_CITATION] = "pass" if result.grounded else "fail"
    return result


def _llm_summarize(
    question: str, rows: list, citations: list, ctx: Optional[CortexContext] = None
) -> Optional[str]:
    """LLM prose summary of the result rows; None degrades to the raw format.

    The prompt requires an inline ``[source: <id>]`` tag per claim, citing
    only this result's citation ids — but the output is never trusted:
    ``_label_llm_result`` validates whatever comes back.

    Routed exactly like every other Cortex LLM call (ctx-trust-01). It used to
    do ``LLMRouter().invoke("summarization", ...)``, which was wrong three ways:

    1. ``summarization`` is declared only under ``task_categories:`` in
       args/llm_config.yaml, never under ``routing:``. LLMRouter resolves
       ``routing.get(fn, routing.get("default", {}))``, so it silently took
       routing.default — a CLOUD-FIRST chain. Now ``cortex_summarize``.
    2. No ``exclude_model_ids`` was passed, so an air-gapped caller would still
       reach for the cloud tier. Now threaded from ``ctx`` like ``api._invoke``.
    3. ``LLMRouter()`` was constructed per call, re-parsing the ~2000-line
       config and starting from a cold availability cache every time. Now the
       late-bound ``tools.llm.get_router`` singleton.
    """
    ids = ", ".join(c.source_id for c in citations if c.source_id)
    evidence = "\n".join(f"[{c.source_id}] {c.snippet}" for c in citations if c.source_id)
    try:
        from tools.cortex.api import CORTEX_SUMMARIZE_FUNCTION
        from tools.cortex.config import airgap_exclusions
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        exclusions = airgap_exclusions(ctx) if ctx is not None else None
        kwargs = {"exclude_model_ids": exclusions} if exclusions else {}
        response = get_router().invoke(
            CORTEX_SUMMARIZE_FUNCTION,
            LLMRequest(
                system_prompt=(
                    "Summarize the query result rows as a direct answer to the "
                    "user's question, in at most 4 sentences. Every factual "
                    "claim MUST end with an inline citation tag of the form "
                    f"[source: <id>], using ONLY these source ids: {ids}. "
                    "Never state a value that is not present in the rows."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\nEvidence:\n{evidence}\n\n"
                            f"Rows:\n{rows[:20]!r}"
                        ),
                    }
                ],
                max_tokens=512,
                temperature=0.0,
                skip_injection_scan=True,  # question already passed _screen_question
            ),
            **kwargs,
        )
        return (response.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — summarization is optional
        logger.warning("cortex.analyst: LLM summarization unavailable: %s", exc)
        return None


def _finalize_result(
    result: CortexResult,
    question: str,
    summarize: bool,
    ctx: Optional[CortexContext] = None,
) -> CortexResult:
    """TRUST labeling applied to every result before it leaves ask().

    A requested-but-unavailable summary is RECORDED (ctx-trust-01). It used to
    fall through silently to ``_label_rows_result``, which stamps
    ``grounding="rows_by_construction"``, ``confidence="include"`` and
    ``confidence_score: 1.0`` — a STRONGER label than the summarized path earns.
    So a caller that asked for prose, and whose LLM call failed (or was routed
    to a provider air-gap forbids), got back a maximally-trusted row dump with
    nothing to distinguish it from a summary that was never requested.
    The rows are still grounded by construction, so this degrades rather than
    refuses — it just stops the degradation being invisible.
    """
    if summarize:
        rows = result.data.get("rows") or []
        text = _llm_summarize(question, rows, result.citations, ctx) if rows else None
        if text:
            result.text = text
            result.data["summarized"] = True
            return _label_llm_result(result)
        result = _label_rows_result(result)
        result.data["summarized"] = False
        result.metadata["summary_requested"] = True
        result.metadata["summary_unavailable"] = True
        result.metadata["summary_unavailable_reason"] = (
            "no rows to summarize" if not rows else "summarization call returned nothing"
        )
        return result
    return _label_rows_result(result)


def _ask_nlq(
    question: str,
    ctx: CortexContext,
    governance: GovernanceReport,
    started: float,
    mode: str,
    summarize: bool = False,
) -> CortexResult:
    """NL→SQL path: nlq_processor generation, shared safety gates, execution.

    Reuses the dashboard NLQ pipeline piecewise (rather than
    ``process_nlq_query``) so the allowlist + read-only gates run between
    generation and execution. Execution goes through ``_execute_nlq_readonly``
    (row cap, decision D34) — NOT the dashboard's context-free
    ``execute_safely`` — so the caller's tenant_id + classification RLS context
    is threaded into the connection exactly like the IQE path (ctx-expose-03).
    Successes and refusals both land in the ``nlq_queries`` audit table.
    """
    from tools.dashboard import nlq_processor as _nlq

    try:
        schema = _nlq.extract_schema()
    except Exception as exc:  # noqa: BLE001 — schema context is best-effort
        logger.warning("cortex.analyst: NLQ schema extraction failed: %s", exc)
        schema = {}

    # Honor per-call air-gap (context.air_gap / ICDEV_AIRGAP) for the NL->SQL
    # generation step too, exactly like cortex.api._invoke does for every other
    # Cortex LLM call — otherwise this sub-path could resolve to a cloud model
    # even when the caller demanded local-only.
    from tools.cortex.config import airgap_exclusions
    _excl = airgap_exclusions(ctx)
    sql = (_nlq.generate_sql_via_bedrock(question, schema, exclude_model_ids=_excl) or "").strip()
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
        results = _execute_nlq_readonly(sql, ctx)
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
            snippet=_rows_snippet(rows),
            classification=ctx.classification or "CUI",
        )
        for table in tables
    ]

    result = CortexResult(
        text=_format_answer(rows, tables, ""),
        citations=citations,
        governance=governance,
        provider="nlq",
        latency_ms=duration_ms,
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
    return _finalize_result(result, question, summarize, ctx)


def ask(
    question: str,
    mode: str = "auto",
    ctx: Optional[CortexContext] = None,
    *,
    canvas: Optional[str] = None,
    collections: Optional[list[str]] = None,
    conn: Any = None,
    summarize: bool = False,
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
        summarize:   When True, replace the deterministic row summary with
                     LLM prose carrying inline ``[source: ...]`` tags, then
                     validate the tags and grade attribution via the shared
                     ``citation_grounding`` module. Missing/hallucinated tags
                     or an "abstain" attribution band flag the result
                     ``grounded=False``. An unavailable LLM degrades back to
                     the deterministic (grounded-by-construction) summary.

    Returns:
        CortexResult — ``text`` is a formatted summary, ``data`` holds
        ``rows``/``row_count`` plus the executed ``iqe`` or ``sql``, and
        ``citations`` carries one ``Citation(source_type="analyst")`` per
        collection/table read (``snippet`` is a compact row summary).
        ``provider`` is "iqe" or "nlq". ``metadata`` carries the TRUST
        labels: ``confidence`` ("include"/"flag"/"abstain"),
        ``confidence_score``, ``grounding`` ("rows_by_construction" or
        "llm_summary"), and — on LLM-summarized answers — the
        ``citation_report`` and per-source ``attribution`` scores.

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
        return _ask_nlq(question, ctx, governance, started, mode, summarize)

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
            summarize=summarize,
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
            return _ask_nlq(question, ctx, governance, started, mode, summarize)
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
    summarize: bool = False,
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
            snippet=_rows_snippet(rows),
            classification=ctx.classification or "CUI",
        )
        for target in targets
    ]

    result = CortexResult(
        text=_format_answer(rows, targets, explanation),
        citations=citations,
        governance=governance,
        provider="iqe",
        latency_ms=int((time.perf_counter() - started) * 1000),
        data={
            "rows": rows,
            "row_count": len(rows),
            "iqe": iqe_str,
            "explanation": explanation,
            "collections": targets,
            "mode": mode,
        },
    )
    return _finalize_result(result, question, summarize, ctx)
