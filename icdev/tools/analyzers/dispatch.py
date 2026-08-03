#!/usr/bin/env python3
# CUI // SP-CTI
"""One dispatch path for observables (anz-disp-01).

Before this module a caller had to know which module handled which indicator:
a CVE went to ``tools.supply_chain.cve_triager``, an IP to
``tools.security_canvas.threat_intel_engine``, a vendor to
``tools.supply_chain.ndaa_889_screener``. Adding a threat source meant editing
a blueprint. Here there is one entry point:

    >>> from tools.analyzers.dispatch import dispatch
    >>> result = dispatch("cve", "CVE-2024-3094", context={"project_id": "p1"})
    >>> result.partial                      # did every analyzer finish?
    False
    >>> [(r.analyzer, r.status) for r in result.reports]
    [('cve_triage', 'ok')]

Which analyzers run is read from ``args/analyzer_contract.yaml`` via
:mod:`tools.analyzers.contract` — every declaration whose ``accepts:`` names
that observable type. **Adding an analyzer is a YAML entry.** Nothing in this
file enumerates analyzers, observable types, or modules.

NOTHING IS DROPPED SILENTLY
---------------------------
Every declaration that matched the observable produces a report, and every
report carries a status from :data:`REPORT_STATUSES`. A fan-out that omitted a
timed-out analyzer would read identically to one where that analyzer found
nothing, so:

    ok                   the analyzer returned
    timeout              it exceeded its declared ``timeout_seconds`` budget
    error                it raised
    unavailable          its module or entrypoint could not be imported
    misdeclared          the declaration and the callable's signature disagree
    skipped              the caller's context lacks a key the declaration maps
    rate_limited         its declared ``rate_limit`` window is full
    sandbox_unavailable  it must be sandboxed and the sandbox cannot run it

``DispatchResult.partial`` is true whenever any report is not ``ok``, and
:meth:`DispatchResult.to_dict` names the offenders under ``partial_reasons``.
Declarations excluded before the fan-out (disabled, or a responder when only
analyzers were asked for) are listed under ``excluded`` for the same reason.

FAN-OUT
-------
Concurrent, over a process-wide bounded thread pool, with a per-analyzer
timeout — the shape already proven by ``tools/cortex/search_service.py``
``_run_backends``: submit onto a shared pool that is never shut down per call,
budget each future from a common start, and abandon (never join) a future that
overruns. A third executor would have been a third place for the thread leak
that pattern exists to prevent.

RATE LIMITS QUEUE OR REPORT — THEY NEVER DROP (anz-rate-01)
-----------------------------------------------------------
Each declaration's ``rate_limit`` is enforced by
:mod:`tools.analyzers.rate_limit` immediately before the entrypoint is called,
so an analyzer that is excluded, misdeclared or unsandboxable never burns quota
it did not spend. Exhausting a window has two possible outcomes and neither is
a discard: with ``rate_limit_wait_seconds`` the call **queues** for a slot, and
otherwise it is **reported** as ``rate_limited`` carrying ``retry_after_seconds``
— a real report, in ``reports``, that sets ``partial``.

SANDBOX POSTURE IS ENFORCED, NOT JUST DECLARED (anz-rate-01)
------------------------------------------------------------
:mod:`tools.analyzers.sandbox` turns each declaration's ``sandbox:`` posture
into an execution mode and routes the call accordingly, reusing the platform
``SandboxExecutor`` behind the ``sandbox_execute`` MCP tool rather than
introducing a second isolation path. An analyzer that must be sandboxed on a
host with no sandbox is reported ``sandbox_unavailable``; it is never
downgraded to an in-process call.

RESPONDERS DO NOT RUN BY DEFAULT
--------------------------------
The contract's ``kind`` splits analyzers (observe) from responders (act).
``dispatch()`` runs analyzers only. ``rtbh_blackhole`` blackholes a prefix;
submitting an IP for analysis must not trigger it. Responders require an
explicit ``kinds=("analyzer", "responder")``.

Usage:
    python tools/analyzers/dispatch.py --type cve --value CVE-2024-3094
    python tools/analyzers/dispatch.py --type ip --value 198.51.100.7 --json
    python tools/analyzers/dispatch.py --type vendor --value Acme --dry-run
    python tools/analyzers/dispatch.py --type cve --value CVE-2024-3094 --rate-limit-wait 5
    python tools/analyzers/dispatch.py --observables
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import importlib
import inspect
import json
import os
import sys
import threading
import time
from collections.abc import Mapping as MappingABC
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.analyzers.contract import (
    AnalyzerContract,
    AnalyzerDeclaration,
    ContractError,
    get_contract,
)
from tools.analyzers.rate_limit import AnalyzerRateLimiter, get_limiter
from tools.analyzers.sandbox import (
    SANDBOXED,
    SandboxUnavailable,
    SandboxUnsupported,
    resolve_execution_mode,
    run_sandboxed,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_dispatch")

__all__ = [
    "AnalyzerReport",
    "BindingError",
    "DispatchError",
    "DispatchResult",
    "MissingContextError",
    "Observable",
    "REPORT_STATUSES",
    "build_call",
    "capabilities",
    "dispatch",
    "extract_taxonomy",
    "resolve_entrypoint",
]

CLASSIFICATION = "CUI // SP-CTI"

#: Closed status vocabulary. Each value names a different remedy, which is why
#: they are not collapsed into "failed".
REPORT_STATUSES: Tuple[str, ...] = (
    "ok",
    "timeout",
    "error",
    "unavailable",
    "misdeclared",
    "skipped",
    "rate_limited",
    "sandbox_unavailable",
)

#: Default fan-out width. Overridable via ``ICDEV_ANALYZER_MAX_WORKERS``.
_DEFAULT_MAX_WORKERS = 8

_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """The lazily-initialized, process-wide analyzer fan-out pool.

    Thread-safe (double-checked lock), sized once on first use, and
    deliberately never shut down per call — same rule as the Cortex search
    pool. A per-call executor leaks a worker thread for every analyzer that
    times out, because the abandoned future keeps running and ``shutdown()``
    waits for it.
    """
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                try:
                    max_workers = int(
                        os.environ.get("ICDEV_ANALYZER_MAX_WORKERS")
                        or _DEFAULT_MAX_WORKERS
                    )
                except (ValueError, TypeError):
                    max_workers = _DEFAULT_MAX_WORKERS
                _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, max_workers),
                    thread_name_prefix="icdev-analyzer",
                )
    return _EXECUTOR


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DispatchError(RuntimeError):
    """Base class for dispatch-time failures."""


class BindingError(DispatchError):
    """The declaration and the callable's signature cannot be reconciled."""


class MissingContextError(DispatchError):
    """The declaration maps a context key the caller did not supply."""

    def __init__(self, missing: Sequence[str]):
        self.missing = list(missing)
        super().__init__(
            "dispatch context is missing required key(s): " + ", ".join(self.missing)
        )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Observable:
    """What was submitted: a type from the closed vocabulary, plus a value."""

    type: str
    value: Any

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "value": self.value}


@dataclasses.dataclass(frozen=True)
class AnalyzerReport:
    """One analyzer's outcome. Produced for every matched declaration."""

    analyzer: str
    kind: str
    display_name: str
    namespace: str
    status: str
    duration_seconds: float
    timeout_seconds: int
    taxonomy: Tuple[Dict[str, Any], ...] = ()
    taxonomy_defects: Tuple[str, ...] = ()
    data: Any = None
    detail: Optional[str] = None
    #: The declared posture this run was gated on, and the mode it resolved to.
    sandbox_posture: str = ""
    execution_mode: str = ""
    #: Seconds after which a ``rate_limited`` analyzer has a slot again, and how
    #: long this call queued for one. Both zero unless the limiter said so.
    retry_after_seconds: float = 0.0
    queued_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "kind": self.kind,
            "display_name": self.display_name,
            "namespace": self.namespace,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 4),
            "timeout_seconds": self.timeout_seconds,
            "taxonomy": [dict(tag) for tag in self.taxonomy],
            "taxonomy_defects": list(self.taxonomy_defects),
            "data": self.data,
            "detail": self.detail,
            "sandbox_posture": self.sandbox_posture,
            "execution_mode": self.execution_mode,
            "retry_after_seconds": round(self.retry_after_seconds, 4),
            "queued_seconds": round(self.queued_seconds, 4),
        }


@dataclasses.dataclass(frozen=True)
class DispatchResult:
    """Every report for one observable, plus what did not run and why."""

    observable: Observable
    reports: Tuple[AnalyzerReport, ...]
    excluded: Tuple[Dict[str, str], ...]
    started_at: str
    duration_seconds: float

    @property
    def partial(self) -> bool:
        """True when any dispatched analyzer did not return cleanly.

        Taxonomy defects count: a tag the analyzer emitted outside its declared
        vocabulary means the declaration and the analyzer disagree, and that
        must not be invisible either.
        """
        return any(not r.succeeded or r.taxonomy_defects for r in self.reports)

    def by_status(self, status: str) -> List[AnalyzerReport]:
        """Every report with *status*."""
        return [r for r in self.reports if r.status == status]

    @property
    def partial_reasons(self) -> Dict[str, List[str]]:
        """``{status: [analyzer keys]}`` for every non-``ok`` outcome."""
        reasons: Dict[str, List[str]] = {}
        for report in self.reports:
            if not report.succeeded:
                reasons.setdefault(report.status, []).append(report.analyzer)
            elif report.taxonomy_defects:
                reasons.setdefault("taxonomy_defect", []).append(report.analyzer)
        return reasons

    @property
    def counts(self) -> Dict[str, int]:
        counts = {status: 0 for status in REPORT_STATUSES}
        for report in self.reports:
            counts[report.status] = counts.get(report.status, 0) + 1
        counts["dispatched"] = len(self.reports)
        counts["excluded"] = len(self.excluded)
        return counts

    @property
    def taxonomy(self) -> List[Dict[str, Any]]:
        """Every validated taxonomy tag across all reports, in report order."""
        return [dict(tag) for report in self.reports for tag in report.taxonomy]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": CLASSIFICATION,
            "observable": self.observable.to_dict(),
            "partial": self.partial,
            "partial_reasons": self.partial_reasons,
            "counts": self.counts,
            "started_at": self.started_at,
            "duration_seconds": round(self.duration_seconds, 4),
            "reports": [r.to_dict() for r in self.reports],
            "excluded": [dict(e) for e in self.excluded],
        }


# ---------------------------------------------------------------------------
# Argument binding
# ---------------------------------------------------------------------------


def _positional_parameters(func: Any) -> List[inspect.Parameter]:
    """The callable's bindable parameters, in order.

    ``*args`` / ``**kwargs`` are excluded: a dispatcher cannot know what to put
    there, and a declaration that needs them is better written as an explicit
    adapter function.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as exc:  # builtins, C extensions
        raise BindingError(f"cannot inspect signature: {exc}") from exc
    return [
        param
        for name, param in signature.parameters.items()
        if name != "self"
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]


def _observable_argument(binding: Any, observable: Observable, nested_key: str) -> Any:
    """The observable value shaped the way the declaration says (anz-mig-01).

    ``observable_form: list`` wraps the scalar for batch-shaped callables like
    ``score_advisories(advisory_ids)``. Without it such a callable receives a
    bare string, iterates it character by character, and reports having scored
    nothing — a silent wrong answer rather than an error.
    """
    value = [observable.value] if binding.observable_form == "list" else observable.value
    return {nested_key: value} if nested_key else value


def build_call(
    func: Any,
    decl: AnalyzerDeclaration,
    observable: Observable,
    context: Mapping[str, Any],
) -> Dict[str, Any]:
    """Keyword arguments for calling *func* with *observable*.

    The algorithm is the whole of it — there is no fallback that guesses:

    1. ``binding.observable_arg`` names the parameter that receives the value
       (``param.key`` nests it in a dict). With no binding declared the value
       goes to the first parameter.
    2. ``binding.context_args`` maps parameters to dispatch-context keys.
    3. ``binding.static_args`` supplies constants.
    4. A parameter left over with no default raises :class:`BindingError`.

    Raises :class:`BindingError` for a declaration/signature mismatch and
    :class:`MissingContextError` when the caller omitted a mapped key — the
    two get different report statuses because they have different fixes.
    """
    parameters = _positional_parameters(func)
    names = [param.name for param in parameters]
    binding = decl.binding
    kwargs: Dict[str, Any] = {}

    # 0. the connection parameter, if one is declared. No value is produced
    #    here — build_call stays pure, and a connection opened during argument
    #    assembly would leak on every later BindingError. `dispatch` opens it
    #    around the call and injects it. Reserving the name keeps step 4 from
    #    reporting it as a parameter with no source (anz-mig-01).
    conn_param = binding.connection.param if binding.connection else None
    if conn_param and conn_param not in names:
        raise BindingError(
            f"binding.connection.param names parameter {conn_param!r}, which "
            f"{decl.module}.{decl.entrypoint}() does not accept "
            f"(parameters: {', '.join(names) or 'none'})"
        )

    # 1. the observable value
    if binding.observable_arg:
        target, _, nested_key = binding.observable_arg.partition(".")
        if target not in names:
            raise BindingError(
                f"binding.observable_arg names parameter {target!r}, which "
                f"{decl.module}.{decl.entrypoint}() does not accept "
                f"(parameters: {', '.join(names) or 'none'})"
            )
        kwargs[target] = _observable_argument(binding, observable, nested_key)
    elif names:
        if names[0] == conn_param:
            raise BindingError(
                f"{decl.module}.{decl.entrypoint}() takes the connection as its "
                "first parameter, so the observable needs an explicit "
                "`binding.observable_arg` — otherwise it would be passed as the "
                "connection"
            )
        kwargs[names[0]] = _observable_argument(binding, observable, "")
    else:
        raise BindingError(
            f"{decl.module}.{decl.entrypoint}() accepts no parameters, so it "
            "cannot receive an observable"
        )

    # 2/3. context and constants
    context_map = binding.context_map
    static_map = binding.static_map
    unknown = sorted(set(context_map) - set(names)) + sorted(set(static_map) - set(names))
    if unknown:
        raise BindingError(
            f"binding names parameter(s) {unknown} that "
            f"{decl.module}.{decl.entrypoint}() does not accept "
            f"(parameters: {', '.join(names) or 'none'})"
        )

    missing_context: List[str] = []
    for param in parameters:
        if param.name in kwargs:
            continue
        if param.name == conn_param:
            continue  # supplied by dispatch, around the call
        if param.name in static_map:
            kwargs[param.name] = static_map[param.name]
            continue
        if param.name in context_map:
            key = context_map[param.name]
            if key in context:
                kwargs[param.name] = context[key]
            elif param.default is inspect.Parameter.empty:
                missing_context.append(key)
            continue
        if param.default is inspect.Parameter.empty:
            raise BindingError(
                f"parameter {param.name!r} of {decl.module}.{decl.entrypoint}() "
                "has no default and no source; declare it under "
                "`binding.context_args` or `binding.static_args` in "
                "args/analyzer_contract.yaml"
            )

    if missing_context:
        raise MissingContextError(missing_context)
    return kwargs


def _call_with_connection(func: Any, decl: AnalyzerDeclaration, kwargs: Dict[str, Any]) -> Any:
    """Call *func* with a connection this function owns end to end (anz-mig-01).

    Opened here rather than in :func:`build_call` so a binding error raised
    during argument assembly cannot leak a handle, and so the lifecycle is
    visible in one place: open, call, commit if declared, always close.

    ``commit`` is honoured only on success. These analyzers write — a hijack
    detection committed after the call raised would persist a half-finished
    analysis, which is worse than not writing at all.

    The factory reference comes from the first-party contract file, never from
    a caller, and is resolved here rather than at load time so that reading the
    contract does not import every canvas's database module.
    """
    binding = decl.binding.connection
    try:
        module = importlib.import_module(binding.factory_module)
        factory = getattr(module, binding.factory_attr, None)
        if factory is None:
            raise AttributeError(
                f"{binding.factory_module} has no attribute {binding.factory_attr!r}"
            )
    except Exception as exc:
        raise BindingError(
            f"binding.connection.factory {binding.factory!r} for analyzer "
            f"{decl.key!r} could not be resolved: {exc}"
        ) from exc

    conn = factory()
    try:
        payload = func(**{**kwargs, binding.param: conn})
        if binding.commit:
            conn.commit()
        return payload
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — a close failure must not mask the result
            logger.debug("analyzer %s: connection close failed", decl.key, exc_info=True)


def resolve_entrypoint(decl: AnalyzerDeclaration) -> Any:
    """Import ``decl.module`` and return ``decl.entrypoint`` from it.

    The dotted path comes from the first-party, code-reviewed contract file —
    never from a caller. :func:`dispatch` takes an observable type, not a
    module path, so no caller can steer this import.
    """
    module = importlib.import_module(decl.module)
    func = getattr(module, decl.entrypoint, None)
    if func is None:
        raise AttributeError(
            f"module {decl.module!r} has no attribute {decl.entrypoint!r}"
        )
    if not callable(func):
        raise TypeError(f"{decl.module}.{decl.entrypoint} is not callable")
    return func


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def extract_taxonomy(
    decl: AnalyzerDeclaration, payload: Any
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[str, ...]]:
    """Validated ``{namespace, predicate, level, value}`` tags from *payload*.

    An analyzer opts in by returning a mapping with a ``taxonomy`` key (one
    entry or a list). The namespace is always stamped from the declaration —
    an analyzer cannot claim a namespace it was not declared under — while
    ``predicate`` and ``level`` are checked against what it declared it emits.

    Entries that fail that check are dropped from the tags and returned as
    defects rather than passed through. Silently accepting an undeclared
    predicate is how a closed vocabulary rots into a decorative one; silently
    dropping it is how the citation_type bug looked from outside.
    """
    if not isinstance(payload, MappingABC) or "taxonomy" not in payload:
        return (), ()

    raw = payload.get("taxonomy")
    entries = [raw] if isinstance(raw, MappingABC) else raw
    if not isinstance(entries, (list, tuple)):
        return (), (f"`taxonomy` must be a mapping or a list, got {type(raw).__name__}",)

    tags: List[Dict[str, Any]] = []
    defects: List[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, MappingABC):
            defects.append(f"taxonomy[{index}] must be a mapping, got {type(entry).__name__}")
            continue
        predicate = entry.get("predicate")
        level = entry.get("level")
        if predicate not in decl.taxonomy.predicates:
            defects.append(
                f"taxonomy[{index}] predicate {predicate!r} is not declared by "
                f"{decl.key!r} (declared: {', '.join(decl.taxonomy.predicates)})"
            )
            continue
        if level not in decl.taxonomy.levels:
            defects.append(
                f"taxonomy[{index}] level {level!r} is not declared by {decl.key!r} "
                f"(declared: {', '.join(decl.taxonomy.levels)})"
            )
            continue
        tags.append(
            {
                "namespace": decl.taxonomy.namespace,
                "predicate": predicate,
                "level": level,
                "value": entry.get("value"),
            }
        )
    return tuple(tags), tuple(defects)


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def _report(
    decl: AnalyzerDeclaration,
    status: str,
    *,
    duration: float,
    timeout_seconds: int,
    data: Any = None,
    detail: Optional[str] = None,
    taxonomy: Tuple[Dict[str, Any], ...] = (),
    taxonomy_defects: Tuple[str, ...] = (),
    execution_mode: str = "",
    retry_after_seconds: float = 0.0,
    queued_seconds: float = 0.0,
) -> AnalyzerReport:
    return AnalyzerReport(
        analyzer=decl.key,
        kind=decl.kind,
        display_name=decl.display_name,
        namespace=decl.taxonomy.namespace,
        status=status,
        duration_seconds=duration,
        timeout_seconds=timeout_seconds,
        taxonomy=taxonomy,
        taxonomy_defects=taxonomy_defects,
        data=data,
        detail=detail,
        sandbox_posture=decl.sandbox,
        execution_mode=execution_mode,
        retry_after_seconds=retry_after_seconds,
        queued_seconds=queued_seconds,
    )


def _run_one(
    decl: AnalyzerDeclaration,
    observable: Observable,
    context: Mapping[str, Any],
    timeout_seconds: int,
    *,
    limiter: Optional[AnalyzerRateLimiter] = None,
    rate_limit_wait_seconds: float = 0.0,
    strict_sandbox: Optional[bool] = None,
) -> AnalyzerReport:
    """Run one analyzer in a worker thread. Never raises.

    Import happens here rather than on the submitting thread so that a module
    which hangs on import is covered by the analyzer's timeout budget instead
    of stalling the whole fan-out.

    The order of the gates is deliberate: resolve, bind and settle the sandbox
    posture first, and take a rate-limit slot only once the call is certain to
    be made. An analyzer that turns out to be misdeclared, unimportable or
    unsandboxable must not spend quota against an external API it never
    reached.

    Note that the declared module is imported in-process even for a sandboxed
    analyzer, because binding needs its signature. That is first-party code
    named by the contract file; what the sandbox exists to contain is the
    *observable*, which only ever reaches the entrypoint inside the container.
    """
    started = time.monotonic()

    def elapsed() -> float:
        return time.monotonic() - started

    try:
        func = resolve_entrypoint(decl)
    except Exception as exc:
        logger.warning("analyzer %s unavailable: %s", decl.key, exc)
        return _report(
            decl,
            "unavailable",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=f"{type(exc).__name__}: {exc}",
        )

    try:
        kwargs = build_call(func, decl, observable, context)
    except MissingContextError as exc:
        return _report(
            decl,
            "skipped",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=str(exc),
        )
    except BindingError as exc:
        logger.warning("analyzer %s misdeclared: %s", decl.key, exc)
        return _report(
            decl,
            "misdeclared",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=str(exc),
        )

    # Declared posture -> execution mode. An unknown posture resolves to
    # sandboxed, so this cannot silently widen into an in-process call.
    mode = resolve_execution_mode(decl, strict=strict_sandbox)

    # Rate limit. Exceeding it queues (bounded) or is reported by name — the
    # one thing it must never do is drop the analyzer from `reports`.
    #
    # The queue is capped by what is left of this analyzer's own timeout budget.
    # Without the cap, an analyzer told to wait longer than its budget would be
    # abandoned as `timeout` by the fan-out loop, then acquire its slot and run
    # anyway — spending real quota on a result nobody is left to read. Capping
    # means it reports `rate_limited` while the caller is still listening.
    effective_wait = min(
        max(0.0, float(rate_limit_wait_seconds)),
        max(0.0, timeout_seconds - elapsed()),
    )
    decision = (limiter or get_limiter()).acquire(
        decl.key,
        decl.rate_limit.max_calls,
        decl.rate_limit.per_seconds,
        max_wait_seconds=effective_wait,
    )
    if not decision.allowed:
        return _report(
            decl,
            "rate_limited",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=decision.detail(),
            execution_mode=mode,
            retry_after_seconds=decision.retry_after_seconds,
            queued_seconds=decision.waited_seconds,
        )

    try:
        if mode == SANDBOXED:
            if decl.binding.connection is not None:
                # A live DB handle cannot cross into the sandbox, and quietly
                # dropping it would call the analyzer with conn=None. That is a
                # declaration/reality mismatch, which is what SandboxUnsupported
                # already means here.
                raise SandboxUnsupported(
                    "binding.connection cannot be honoured under `sandbox: "
                    "sandboxed` — a database handle cannot be serialised into "
                    "the sandbox. Declare this analyzer trusted_first_party or "
                    "drop the connection binding."
                )
            payload = run_sandboxed(
                decl,
                kwargs,
                timeout_seconds=timeout_seconds,
                actor=context.get("actor"),
                project_id=context.get("project_id"),
                tenant_id=context.get("tenant_id"),
            )
        elif decl.binding.connection is not None:
            payload = _call_with_connection(func, decl, kwargs)
        else:
            payload = func(**kwargs)
    except SandboxUnsupported as exc:
        # The declaration says sandbox me, and its own arguments make that
        # impossible — a declaration/reality mismatch, same class as a binding
        # error, so it gets the same status.
        logger.warning("analyzer %s cannot be sandboxed as declared: %s", decl.key, exc)
        return _report(
            decl,
            "misdeclared",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=str(exc),
            execution_mode=mode,
            queued_seconds=decision.waited_seconds,
        )
    except SandboxUnavailable as exc:
        logger.warning("analyzer %s requires a sandbox that is not available: %s", decl.key, exc)
        return _report(
            decl,
            "sandbox_unavailable",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=str(exc),
            execution_mode=mode,
            queued_seconds=decision.waited_seconds,
        )
    except Exception as exc:
        logger.warning("analyzer %s raised: %s", decl.key, exc)
        return _report(
            decl,
            "error",
            duration=elapsed(),
            timeout_seconds=timeout_seconds,
            detail=f"{type(exc).__name__}: {exc}",
            execution_mode=mode,
            queued_seconds=decision.waited_seconds,
        )

    tags, defects = extract_taxonomy(decl, payload)
    return _report(
        decl,
        "ok",
        duration=elapsed(),
        timeout_seconds=timeout_seconds,
        data=payload,
        taxonomy=tags,
        taxonomy_defects=defects,
        execution_mode=mode,
        queued_seconds=decision.waited_seconds,
    )


def _select(
    contract: AnalyzerContract,
    observable_type: str,
    kinds: Sequence[str],
    only: Optional[Iterable[str]],
) -> Tuple[List[AnalyzerDeclaration], List[Dict[str, str]]]:
    """Declarations to run, and the ones held back with the reason why.

    ``for_observable`` raises :class:`UnknownObservableType` for a type outside
    the closed vocabulary — a typo must not return an empty list that reads as
    "no analyzer applies".
    """
    declared = contract.for_observable(observable_type, enabled_only=False)
    selected: List[AnalyzerDeclaration] = []
    excluded: List[Dict[str, str]] = []
    requested = set(only) if only is not None else None

    for decl in declared:
        if requested is not None and decl.key not in requested:
            excluded.append({"analyzer": decl.key, "reason": "not_requested"})
        elif not decl.enabled:
            excluded.append({"analyzer": decl.key, "reason": "disabled"})
        elif decl.kind not in kinds:
            excluded.append({"analyzer": decl.key, "reason": f"kind_{decl.kind}_not_requested"})
        else:
            selected.append(decl)

    if requested is not None:
        known = {d.key for d in declared}
        for key in sorted(requested - known):
            excluded.append({"analyzer": key, "reason": "does_not_accept_observable"})
    return selected, excluded


def dispatch(
    observable_type: str,
    value: Any,
    *,
    context: Optional[Mapping[str, Any]] = None,
    kinds: Sequence[str] = ("analyzer",),
    analyzers: Optional[Iterable[str]] = None,
    timeout_seconds: Optional[int] = None,
    contract: Optional[AnalyzerContract] = None,
    rate_limit_wait_seconds: float = 0.0,
    limiter: Optional[AnalyzerRateLimiter] = None,
    strict_sandbox: Optional[bool] = None,
) -> DispatchResult:
    """Fan an observable out to every analyzer that declared it accepts the type.

    Args:
        observable_type: A key from the contract's closed vocabulary
            (``ip``, ``domain``, ``url``, ``file_hash``, ``email``,
            ``file_path``, ``cve``, ...). An unknown type raises
            :class:`~tools.analyzers.contract.UnknownObservableType`.
        value: The observable itself.
        context: Free-form dispatch context. Keys are consumed by name through
            each declaration's ``binding.context_args``.
        kinds: Which declaration kinds to run. Analyzers only by default —
            responders act, and are opt-in.
        analyzers: Restrict the fan-out to these declaration keys. Held-back
            declarations still appear under ``excluded``.
        timeout_seconds: Override every analyzer's declared budget.
        contract: An already-loaded contract (tests, or a pinned file).
        rate_limit_wait_seconds: How long an analyzer may queue for a
            rate-limit slot before being reported as ``rate_limited``. ``0.0``
            (the default) reports immediately rather than blocking a pooled
            worker; either way the analyzer still produces a report.
        limiter: An explicit rate limiter (tests, or an isolated window).
            Defaults to the process-wide one.
        strict_sandbox: Override the ``ICDEV_STRICT_SANDBOX`` reading that
            decides whether ``sandboxed_on_demand`` analyzers are isolated.

    Returns:
        A :class:`DispatchResult` holding one report per matched declaration.
        ``result.partial`` is true when any of them did not return cleanly.
    """
    contract = contract or get_contract()
    context = dict(context or {})
    observable = Observable(type=observable_type, value=value)

    selected, excluded = _select(contract, observable_type, tuple(kinds), analyzers)
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()

    if not selected:
        logger.info(
            "analyzer dispatch: no analyzer accepts observable type %r (%d excluded)",
            observable_type,
            len(excluded),
        )
        return DispatchResult(
            observable=observable,
            reports=(),
            excluded=tuple(excluded),
            started_at=started_at,
            duration_seconds=time.monotonic() - start,
        )

    budgets = {
        decl.key: int(timeout_seconds or decl.timeout_seconds) for decl in selected
    }
    executor = _get_executor()
    futures = {
        decl.key: executor.submit(
            _run_one,
            decl,
            observable,
            context,
            budgets[decl.key],
            limiter=limiter,
            rate_limit_wait_seconds=rate_limit_wait_seconds,
            strict_sandbox=strict_sandbox,
        )
        for decl in selected
    }

    reports: List[AnalyzerReport] = []
    for decl in selected:
        budget = budgets[decl.key]
        # Budget from a shared start: the analyzers run concurrently, so each
        # one's deadline is measured from when the fan-out began, not from when
        # this loop reached it.
        remaining = max(0.0, start + budget - time.monotonic())
        try:
            reports.append(futures[decl.key].result(timeout=remaining))
        except concurrent.futures.TimeoutError:
            # Abandon, do not join. The worker is returned to the shared pool
            # whenever the analyzer finishes; joining here would make one slow
            # analyzer the latency of the whole fan-out.
            futures[decl.key].cancel()
            logger.warning(
                "analyzer %s timed out after %ss on observable type %r — "
                "reported as timed-out, partial results returned",
                decl.key,
                budget,
                observable_type,
            )
            reports.append(
                _report(
                    decl,
                    "timeout",
                    duration=time.monotonic() - start,
                    timeout_seconds=budget,
                    detail=f"exceeded its {budget}s budget and was abandoned",
                )
            )

    result = DispatchResult(
        observable=observable,
        reports=tuple(reports),
        excluded=tuple(excluded),
        started_at=started_at,
        duration_seconds=time.monotonic() - start,
    )
    logger.info(
        "analyzer dispatch: type=%s dispatched=%d partial=%s reasons=%s",
        observable_type,
        len(reports),
        result.partial,
        result.partial_reasons or "-",
    )
    return result


def capabilities(
    observable_type: Optional[str] = None,
    *,
    contract: Optional[AnalyzerContract] = None,
    limiter: Optional[AnalyzerRateLimiter] = None,
) -> Dict[str, Any]:
    """What can be dispatched: the observable vocabulary and its analyzers.

    A caller needs this to know which types :func:`dispatch` will accept — the
    answer is derived from the contract, so a newly declared analyzer shows up
    here with no code change either. Each entry also reports its declared rate
    limit, the live state of that limit's window, and the execution mode its
    sandbox posture resolves to on this host. Reading this consumes no quota.
    """
    contract = contract or get_contract()
    limiter = limiter or get_limiter()
    types = (
        [observable_type] if observable_type else sorted(contract.observable_types)
    )
    out: Dict[str, Any] = {
        "classification": CLASSIFICATION,
        "contract": str(contract.path),
        "statuses": list(REPORT_STATUSES),
        "observable_types": {},
    }
    for name in types:
        matches = contract.for_observable(name, enabled_only=False)
        out["observable_types"][name] = {
            "description": contract.observable_types[name].description,
            "analyzers": [
                {
                    "key": d.key,
                    "kind": d.kind,
                    "display_name": d.display_name,
                    "namespace": d.taxonomy.namespace,
                    "predicates": list(d.taxonomy.predicates),
                    "levels": list(d.taxonomy.levels),
                    "timeout_seconds": d.timeout_seconds,
                    "sandbox": d.sandbox,
                    # What the posture actually resolves to on THIS host, so a
                    # caller can see that `sandboxed_on_demand` is running
                    # in-process here rather than assuming the strict reading.
                    "execution_mode": resolve_execution_mode(d),
                    "rate_limit": d.rate_limit.to_dict(),
                    "rate_limit_state": limiter.snapshot(
                        d.key, d.rate_limit.max_calls, d.rate_limit.per_seconds
                    ),
                    "enabled": d.enabled,
                }
                for d in matches
            ],
        }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch one observable to every analyzer that accepts its type."
    )
    parser.add_argument("--type", dest="observable_type", help="Observable type")
    parser.add_argument("--value", help="Observable value")
    parser.add_argument("--context", help="Dispatch context as a JSON object")
    parser.add_argument(
        "--analyzer",
        action="append",
        dest="analyzers",
        help="Restrict to this analyzer key (repeatable)",
    )
    parser.add_argument(
        "--responders",
        action="store_true",
        help="Include responders (they ACT — off by default)",
    )
    parser.add_argument("--timeout", type=int, help="Override every analyzer's budget")
    parser.add_argument(
        "--rate-limit-wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Queue up to SECONDS for a rate-limit slot instead of reporting "
            "`rate_limited` immediately (default: 0, report)"
        ),
    )
    parser.add_argument(
        "--strict-sandbox",
        action="store_true",
        help="Force sandboxed_on_demand analyzers through the sandbox",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which analyzers would run without calling them",
    )
    parser.add_argument(
        "--observables",
        action="store_true",
        help="List the observable vocabulary and its analyzers",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    try:
        if args.observables or (args.dry_run and not args.observable_type):
            payload = capabilities(args.observable_type)
            print(json.dumps(payload, indent=2, default=str))
            return 0

        if not args.observable_type or args.value is None:
            parser.error("--type and --value are required (or use --observables)")

        context = json.loads(args.context) if args.context else {}
        if not isinstance(context, dict):
            parser.error("--context must be a JSON object")

        if args.dry_run:
            payload = capabilities(args.observable_type)
            print(json.dumps(payload, indent=2, default=str))
            return 0

        result = dispatch(
            args.observable_type,
            args.value,
            context=context,
            kinds=("analyzer", "responder") if args.responders else ("analyzer",),
            analyzers=args.analyzers,
            timeout_seconds=args.timeout,
            rate_limit_wait_seconds=args.rate_limit_wait,
            strict_sandbox=True if args.strict_sandbox else None,
        )
    except (ContractError, DispatchError) as exc:
        message = {"error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(message, indent=2))
        else:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"ERROR: --context is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    state = "PARTIAL" if result.partial else "COMPLETE"
    print(f"{state}: {result.observable.type}={result.observable.value!r}")
    for report in result.reports:
        tags = ", ".join(
            f"{t['predicate']}={t['level']}" for t in report.taxonomy
        )
        print(
            f"  {report.analyzer:24} {report.status:12} "
            f"{report.duration_seconds:6.2f}s  {tags or '-'}"
        )
        if report.detail:
            print(f"      {report.detail}")
        for defect in report.taxonomy_defects:
            print(f"      taxonomy defect: {defect}")
    for entry in result.excluded:
        print(f"  {entry['analyzer']:24} excluded     ({entry['reason']})")
    # A partial fan-out is a non-zero exit: a caller scripting this must not
    # read "some analyzers never answered" as a clean run.
    return 2 if result.partial else 0


if __name__ == "__main__":
    sys.exit(main())
