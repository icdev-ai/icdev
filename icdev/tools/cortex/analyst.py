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

Questions that resolve to no registered collection — and translated queries
that target an unregistered collection — raise :class:`CortexAnalystError`.
The semantic-search fallback is layered on top by ctx-analyst-02, not here.

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

_VALID_MODES = ("auto", "iqe")

# Governance gate names, in execution order, recorded on every ask().
_GATE_RESOLUTION = "collection_resolution"
_GATE_TRANSLATION = "iqe_translation"
_GATE_AUTHORIZATION = "collection_authorization"
_GATE_EXECUTION = "iqe_execution"


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


def ask(
    question: str,
    mode: str = "auto",
    ctx: Optional[CortexContext] = None,
    *,
    canvas: Optional[str] = None,
    collections: Optional[list[str]] = None,
    conn: Any = None,
) -> CortexResult:
    """Answer a natural-language data question through the IQE engine.

    Args:
        question:    Free-form question (e.g. "show all satellites").
        mode:        "auto" or "iqe" — both take the IQE-primary path here;
                     "auto" additionally allows the ctx-analyst-02 fallback
                     layered above this function.
        ctx:         Caller identity/policy context; tenant_id and
                     classification are threaded into the DB connection.
        canvas:      Restrict to one canvas's collections (component registry key).
        collections: Explicit collection names, bypassing resolution.
        conn:        Existing DB connection; one is opened (and closed) via
                     ``get_connection()`` when omitted. The security context
                     is applied either way.

    Returns:
        CortexResult — ``text`` is a formatted summary, ``data`` holds
        ``rows``/``row_count``/``iqe``, and ``citations`` carries one
        synthesized ``Citation(source_type="analyst")`` per collection read.

    Raises:
        CortexAnalystError: no matching/registered collection, untranslatable
            question, or execution failure.
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
