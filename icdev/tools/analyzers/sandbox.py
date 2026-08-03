#!/usr/bin/env python3
# CUI // SP-CTI
"""Sandbox-posture enforcement for dispatched analyzers (anz-rate-01).

``args/analyzer_contract.yaml`` declares a ``sandbox:`` posture per analyzer
and defaults anything that declares nothing to the strictest one. Until now
that declaration was *surfaced* (``analyzer_capabilities`` reports it) but not
*enforced* — every analyzer ran in-process regardless of what it declared, so a
declaration of ``sandboxed`` bought exactly as much isolation as a declaration
of ``trusted_first_party``. This module is the gate that makes the difference
real.

ONE ISOLATION PATH, NOT TWO
---------------------------
Sandboxed execution goes through :class:`tools.security.sandbox_executor.
SandboxExecutor` — the same object behind the ``sandbox_execute`` MCP tool
(``tool_registry.py`` -> ``gap_handlers.handle_sandbox_execute``), and
therefore the same Docker isolation, resource caps, network isolation and audit
trail already reviewed under D-SEC-10. No second isolation mechanism is
introduced here: this module decides *whether* to sandbox and marshals the
call; the executor owns the boundary.

FAIL CLOSED
-----------
When an analyzer declares that it must be sandboxed and the sandbox cannot run
it, the analyzer does **not** quietly fall back to in-process execution — that
would turn the strictest posture into the weakest one precisely on the hosts
that lack a container runtime. :func:`run_sandboxed` raises
:class:`SandboxUnavailable`, which the dispatcher reports as
``sandbox_unavailable``. Refusing loudly is the point; an unknown posture is
treated as ``sandboxed`` for the same reason.

DEPLOYMENT NOTE — the sandbox image must contain the platform
-------------------------------------------------------------
The driver this module builds runs ``importlib.import_module(<declared
module>)`` inside the container, so the container image must have ICDEV
importable. The stock ``python:3.12-slim`` in ``args/sandbox_config.yaml`` does
not, and a sandboxed analyzer will report ``error`` naming ``ModuleNotFoundError``
against it. Operators who declare an analyzer ``sandboxed`` must point
``sandbox.images.python`` at an image carrying the platform. That is a
configuration step, and it fails loudly rather than silently degrading — which
is why the gate is still worth having on hosts that have not taken it.

Postures and what they mean here (the vocabulary is the contract's, and matches
``docs/security/sandbox-coverage.md``):

    sandboxed            always routed through SandboxExecutor
    sandboxed_on_demand  in-process, unless ICDEV_STRICT_SANDBOX=1 (IL5/air-gap)
    trusted_first_party  in-process
    bypass_documented    in-process
    <anything else>      sandboxed (fail closed)

Usage:
    >>> from tools.analyzers.sandbox import resolve_execution_mode
    >>> resolve_execution_mode(declaration)
    'in_process'
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Mapping, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_sandbox")

__all__ = [
    "EXECUTION_MODES",
    "IN_PROCESS",
    "SANDBOXED",
    "SandboxError",
    "SandboxUnavailable",
    "SandboxUnsupported",
    "resolve_execution_mode",
    "run_sandboxed",
    "strict_sandbox_enabled",
]

CLASSIFICATION = "CUI // SP-CTI"

#: Env flag that promotes ``sandboxed_on_demand`` to real isolation. Same flag
#: and same meaning as every other on-demand path in
#: docs/security/sandbox-coverage.md — one switch for an IL5 / air-gap host.
STRICT_SANDBOX_ENV = "ICDEV_STRICT_SANDBOX"

IN_PROCESS = "in_process"
SANDBOXED = "sandboxed"

#: Closed set of execution modes :func:`resolve_execution_mode` can return.
EXECUTION_MODES: Tuple[str, ...] = (IN_PROCESS, SANDBOXED)

#: Postures that run in-process. Anything absent from both this set and
#: :data:`_ON_DEMAND_POSTURES` is sandboxed, so a posture added to the contract
#: without a decision here gets the strict treatment rather than a free pass.
_IN_PROCESS_POSTURES = frozenset({"trusted_first_party", "bypass_documented"})
_ON_DEMAND_POSTURES = frozenset({"sandboxed_on_demand"})

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class SandboxError(RuntimeError):
    """Base class for sandbox-enforcement failures."""


class SandboxUnavailable(SandboxError):
    """Sandboxing is required but the sandbox cannot run — never a fallback."""


class SandboxUnsupported(SandboxError):
    """The declaration cannot be marshalled across the sandbox boundary."""


def strict_sandbox_enabled() -> bool:
    """True when ``ICDEV_STRICT_SANDBOX`` is set to a truthy value."""
    return os.environ.get(STRICT_SANDBOX_ENV, "").strip().lower() in _TRUTHY


def resolve_execution_mode(declaration: Any, *, strict: Optional[bool] = None) -> str:
    """How *declaration* must be executed, given its declared posture.

    Args:
        declaration: An ``AnalyzerDeclaration`` (anything with ``.sandbox``).
        strict: Override the ``ICDEV_STRICT_SANDBOX`` reading. ``None`` reads
            the environment.

    Returns:
        One of :data:`EXECUTION_MODES`. An unrecognised posture returns
        :data:`SANDBOXED`: a posture this module has no decision for must not
        be handed the most permissive outcome by default.
    """
    posture = getattr(declaration, "sandbox", None)
    if posture in _IN_PROCESS_POSTURES:
        return IN_PROCESS
    if posture in _ON_DEMAND_POSTURES:
        is_strict = strict_sandbox_enabled() if strict is None else bool(strict)
        return SANDBOXED if is_strict else IN_PROCESS
    if posture != SANDBOXED:
        logger.warning(
            "analyzer %s declares sandbox posture %r, which %s has no decision "
            "for — treating it as %r (fail closed)",
            getattr(declaration, "key", "?"),
            posture,
            __name__,
            SANDBOXED,
        )
    return SANDBOXED


def _build_driver(module: str, entrypoint: str, kwargs: Mapping[str, Any], sentinel: str) -> str:
    """The snippet executed inside the container.

    Kept deliberately dull: import the declared module, call the declared
    entrypoint with the already-bound keyword arguments, and print one JSON
    line behind *sentinel*. The analyzer's own stdout is therefore separable
    from its return value — an analyzer that prints a banner must not corrupt
    the result.

    ``module``/``entrypoint`` come from the first-party contract file, never
    from a caller, so this builds no attacker-controlled import.
    """
    spec = json.dumps({"module": module, "entrypoint": entrypoint, "kwargs": dict(kwargs)})
    return (
        "import importlib, json, sys, traceback\n"
        "SENTINEL = " + repr(sentinel) + "\n"
        "spec = json.loads(" + repr(spec) + ")\n"
        "try:\n"
        "    _mod = importlib.import_module(spec['module'])\n"
        "    _fn = getattr(_mod, spec['entrypoint'])\n"
        "    _out = {'ok': True, 'result': _fn(**spec['kwargs'])}\n"
        "except BaseException:\n"
        "    _out = {'ok': False, 'error': traceback.format_exc(limit=8)}\n"
        "try:\n"
        "    _body = json.dumps(_out, default=str)\n"
        "except BaseException:\n"
        "    _body = json.dumps({'ok': False, 'error': 'analyzer result is not JSON-serializable'})\n"
        "sys.stdout.write('\\n' + SENTINEL + _body + '\\n')\n"
    )


def _parse_driver_output(stdout: str, sentinel: str) -> Dict[str, Any]:
    """Pull the driver's JSON line out of container stdout."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(sentinel):
            return json.loads(line[len(sentinel):])
    raise SandboxError(
        "sandboxed analyzer produced no result line; the driver did not run to "
        "completion (stdout: " + (stdout[-400:] or "<empty>") + ")"
    )


def run_sandboxed(
    declaration: Any,
    kwargs: Mapping[str, Any],
    *,
    timeout_seconds: Optional[int] = None,
    actor: Optional[str] = None,
    project_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Any:
    """Run *declaration*'s entrypoint inside the platform sandbox.

    Args:
        declaration: The ``AnalyzerDeclaration`` to run.
        kwargs: Keyword arguments already bound by
            :func:`tools.analyzers.dispatch.build_call`.
        timeout_seconds: Wall-clock budget; the executor clamps it against
            ``sandbox.max_timeout_seconds``.
        actor, project_id, tenant_id: Audit attribution, passed straight
            through to the executor's audit record.

    Returns:
        Whatever the entrypoint returned, round-tripped through JSON.

    Raises:
        SandboxUnsupported: an argument or the declaration cannot cross the
            sandbox boundary. The dispatcher reports this as ``misdeclared`` —
            it is a declaration/reality mismatch, not an outage.
        SandboxUnavailable: the sandbox is disabled or has no runtime. Never
            downgraded to in-process execution.
        SandboxError: the driver ran but did not produce a usable result.
        Exception: re-raised from the analyzer itself when it raised inside the
            container, so it is reported exactly as an in-process raise is.
    """
    module = getattr(declaration, "module", None)
    entrypoint = getattr(declaration, "entrypoint", None)
    key = getattr(declaration, "key", "?")
    if not module or not entrypoint:
        raise SandboxUnsupported(f"analyzer {key!r} declares no module/entrypoint to run")

    # Everything crossing the boundary is JSON. A live DB connection or an open
    # file handle bound by `binding.context_args` cannot be marshalled, and
    # discovering that here — by name — beats a container-side stack trace.
    try:
        json.dumps(dict(kwargs))
    except (TypeError, ValueError) as exc:
        raise SandboxUnsupported(
            f"analyzer {key!r} is declared `sandboxed` but its arguments are not "
            f"JSON-serializable, so they cannot cross the sandbox boundary: {exc}"
        ) from exc

    try:
        from tools.security.sandbox_executor import SandboxExecutor
    except Exception as exc:  # pragma: no cover - import guard
        raise SandboxUnavailable(
            f"analyzer {key!r} requires the sandbox, which could not be imported: {exc}"
        ) from exc

    sentinel = f"__ICDEV_ANALYZER_{uuid.uuid4().hex}__"
    code = _build_driver(module, entrypoint, kwargs, sentinel)

    executor = SandboxExecutor()
    result = executor.execute(
        code=code,
        language="python",
        timeout_seconds=timeout_seconds,
        executor_type="analyzer",
        actor=actor,
        project_id=project_id,
        tenant_id=tenant_id,
    )

    status = getattr(result, "status", "")
    if status in ("disabled", "unavailable"):
        raise SandboxUnavailable(
            f"analyzer {key!r} declares a sandboxed posture but the sandbox is "
            f"{status}: {getattr(result, 'error', '') or 'no detail'}. Refusing to "
            "run it in-process."
        )
    if status == "timeout":
        raise SandboxError(f"analyzer {key!r} timed out inside the sandbox")
    if status not in ("completed", "success"):
        raise SandboxError(
            f"analyzer {key!r} sandbox execution returned status {status!r}: "
            f"{getattr(result, 'error', '') or 'no detail'}"
        )

    stdout = getattr(result, "stdout", "") or ""
    exit_code = int(getattr(result, "exit_code", 0) or 0)
    payload = _parse_driver_output(stdout, sentinel)

    if not payload.get("ok"):
        # The analyzer raised inside the container. Surface it as a raise so the
        # dispatcher's existing `error` reporting applies unchanged.
        raise RuntimeError(
            f"analyzer {key!r} raised inside the sandbox: {payload.get('error', '')}"
        )
    if exit_code != 0:
        raise SandboxError(
            f"analyzer {key!r} sandbox driver exited {exit_code}: "
            f"{(getattr(result, 'stderr', '') or '')[-400:]}"
        )
    return payload.get("result")
