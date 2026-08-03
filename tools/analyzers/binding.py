#!/usr/bin/env python3
# CUI // SP-CTI
"""Invoke a declared analyzer through its contract binding (anz-mig-01).

anz-con-01 declared WHAT each analyzer accepts and emits. This module is the
other half of a migration: given a declaration with an ``input_binding``, hand
the analyzer an observable and get its result back — WITHOUT touching the
analyzer.

The one rule this module exists to keep:

    **The callable's return value is passed back untouched.**

No wrapping in a report envelope, no ``dict`` coercion, no exception swallowing.
A ported analyzer returns exactly what it returned before, or raises exactly
what it raised before. That is what makes an interface migration a null
behavioural change, and :mod:`tools.analyzers.parity` proves it call-by-call
rather than asserting it in a docstring. Taxonomy tagging and report envelopes
belong to anz-disp-01, downstream of here; rate limiting and sandbox
enforcement belong to anz-rate-01, upstream of here.

Why exceptions propagate: every hand-written call site for these analyzers is a
``tools/mcp/gap_handlers.py`` function wrapping the call in
``except Exception: return {"error": str(exc)}``. If this module caught too, a
port would convert a raise into an error dict and the MCP handler's own except
clause would go dead — the output would change shape for every failure path.
So the boundary that swallows stays exactly where it already is.

Usage:
    python tools/analyzers/binding.py --verify
    python tools/analyzers/binding.py --verify --json
    python tools/analyzers/binding.py --describe pvm_risk_prediction
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional

from tools.analyzers.contract import (
    AnalyzerContract,
    AnalyzerDeclaration,
    get_contract,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_binding")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BindingError(RuntimeError):
    """Base class for every failure to bind an observable to a callable."""


class UnknownAnalyzer(BindingError):
    """No declaration carries the requested key."""


class BindingNotDeclared(BindingError):
    """The declaration exists but has no ``input_binding``, so it cannot run."""


class BindingResolutionError(BindingError):
    """The declared module, entrypoint or connection factory does not resolve."""


class ObservableNotAccepted(BindingError):
    """The analyzer did not declare the offered observable type."""


class MissingContext(BindingError):
    """A declared ``context_params`` name was not supplied by the caller."""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _import_attr(module: str, attr: str, *, what: str) -> Any:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # ImportError, but a bad module can raise anything
        raise BindingResolutionError(
            f"{what}: cannot import {module!r}: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        raise BindingResolutionError(
            f"{what}: {module!r} has no attribute {attr!r}"
        ) from exc


def resolve_entrypoint(decl: AnalyzerDeclaration) -> Callable[..., Any]:
    """Import and return the callable a declaration names."""
    fn = _import_attr(
        decl.module, decl.entrypoint, what=f"analyzer {decl.key!r} entrypoint"
    )
    if not callable(fn):
        raise BindingResolutionError(
            f"analyzer {decl.key!r} entrypoint {decl.module}:{decl.entrypoint} "
            f"is not callable (got {type(fn).__name__})"
        )
    return fn


def resolve_connection_factory(decl: AnalyzerDeclaration) -> Callable[[], Any]:
    """Import and return the declared connection factory."""
    binding = decl.input_binding
    if binding is None or binding.connection is None:
        raise BindingNotDeclared(
            f"analyzer {decl.key!r} declares no connection factory"
        )
    factory = _import_attr(
        binding.connection.factory_module,
        binding.connection.factory_attr,
        what=f"analyzer {decl.key!r} connection factory",
    )
    if not callable(factory):
        raise BindingResolutionError(
            f"analyzer {decl.key!r} connection factory "
            f"{binding.connection.factory} is not callable"
        )
    return factory


def get_declaration(
    key: str, *, contract: Optional[AnalyzerContract] = None
) -> AnalyzerDeclaration:
    """The declaration named *key*, or :class:`UnknownAnalyzer`."""
    contract = contract or get_contract()
    decl = contract.get(key)
    if decl is None:
        raise UnknownAnalyzer(
            f"no analyzer declared with key {key!r}; "
            f"declared keys: {', '.join(d.key for d in contract.analyzers)}"
        )
    return decl


# ---------------------------------------------------------------------------
# Argument assembly
# ---------------------------------------------------------------------------


def build_kwargs(
    decl: AnalyzerDeclaration,
    observable_type: str,
    observable: Any,
    context: Optional[Mapping[str, Any]] = None,
    *,
    connection: Any = None,
) -> Dict[str, Any]:
    """Assemble the keyword arguments the declared callable will receive.

    Separated from :func:`invoke` on purpose: it is a pure function of the
    declaration plus the caller's inputs, so a parity test can assert that the
    binding produces byte-for-byte the same call the hand-written call site
    made, without executing the analyzer or touching a database.
    """
    binding = decl.input_binding
    if binding is None:
        raise BindingNotDeclared(
            f"analyzer {decl.key!r} has no `input_binding`; it is declared but "
            "not wired. Add an input_binding block to args/analyzer_contract.yaml."
        )
    if not decl.accepts_observable(observable_type):
        raise ObservableNotAccepted(
            f"analyzer {decl.key!r} does not accept observable type "
            f"{observable_type!r}; it accepts: {', '.join(decl.accepts)}"
        )

    context = dict(context or {})
    kwargs: Dict[str, Any] = {}

    if binding.connection is not None:
        kwargs[binding.connection.param] = connection

    kwargs[binding.observable_param] = (
        [observable] if binding.observable_form == "list" else observable
    )

    missing = [name for name in binding.context_params if name not in context]
    if missing:
        raise MissingContext(
            f"analyzer {decl.key!r} requires context value(s) {missing} that the "
            f"caller did not supply; required: {list(binding.context_params)}"
        )
    for name in binding.context_params:
        kwargs[name] = context[name]
    for name in binding.optional_context_params:
        if name in context:
            kwargs[name] = context[name]

    unknown = sorted(
        set(context)
        - set(binding.context_params)
        - set(binding.optional_context_params)
    )
    if unknown:
        # Silently dropping an unrecognised context key is how a caller ends up
        # believing it passed a filter that never reached the analyzer.
        raise MissingContext(
            f"analyzer {decl.key!r} was given context key(s) {unknown} that its "
            f"input_binding does not declare; declared: "
            f"{sorted(set(binding.context_params) | set(binding.optional_context_params))}"
        )
    return kwargs


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def invoke(
    key: str,
    observable_type: str,
    observable: Any,
    context: Optional[Mapping[str, Any]] = None,
    *,
    contract: Optional[AnalyzerContract] = None,
) -> Any:
    """Run the declared analyzer against *observable* and return its result as-is.

    Connection lifecycle, when the declaration names a factory, matches what
    every hand-written call site already does: open, call, commit only if the
    declaration says the analyzer writes, close in a ``finally``.
    """
    contract = contract or get_contract()
    decl = get_declaration(key, contract=contract)
    if not decl.enabled:
        raise BindingNotDeclared(f"analyzer {decl.key!r} is disabled in the contract")

    binding = decl.input_binding
    if binding is None:
        raise BindingNotDeclared(
            f"analyzer {decl.key!r} has no `input_binding`; it is declared but not wired"
        )

    fn = resolve_entrypoint(decl)

    if binding.connection is None:
        kwargs = build_kwargs(decl, observable_type, observable, context)
        return fn(**kwargs)

    factory = resolve_connection_factory(decl)
    conn = factory()
    try:
        kwargs = build_kwargs(
            decl, observable_type, observable, context, connection=conn
        )
        result = fn(**kwargs)
        if binding.connection.commit:
            conn.commit()
        return result
    finally:
        try:
            conn.close()
        except Exception:  # a already-closed handle must not mask the real result
            logger.debug("analyzer %s: connection close failed", decl.key, exc_info=True)


# ---------------------------------------------------------------------------
# Verification — the guard that keeps a declaration honest
# ---------------------------------------------------------------------------


def verify_declaration(decl: AnalyzerDeclaration) -> Dict[str, Any]:
    """Check one declaration against the real signature of its callable.

    A binding is data, so nothing stops it naming a parameter that does not
    exist — the call would fail at dispatch with a ``TypeError``, which is the
    late-and-silent failure mode anz-con-01 was written to avoid. This runs the
    check eagerly instead: entrypoint resolves, every declared parameter name
    exists, and every required parameter of the callable is bound by something.
    """
    report: Dict[str, Any] = {
        "key": decl.key,
        "module": decl.module,
        "entrypoint": decl.entrypoint,
        "dispatchable": decl.is_dispatchable,
        "status": "ok",
        "errors": [],
    }
    try:
        fn = resolve_entrypoint(decl)
    except BindingResolutionError as exc:
        report["status"] = "error"
        report["errors"].append(str(exc))
        return report

    if not decl.is_dispatchable:
        report["status"] = "declared_only"
        return report

    binding = decl.input_binding
    assert binding is not None  # narrowed by is_dispatchable

    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        report["status"] = "error"
        report["errors"].append(f"cannot introspect signature: {exc}")
        return report

    params = signature.parameters
    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )

    for name in binding.bound_param_names():
        param = params.get(name)
        if param is None:
            if accepts_var_kw:
                continue
            report["errors"].append(
                f"binds {name!r}, which {decl.module}:{decl.entrypoint} "
                f"does not accept (parameters: {', '.join(params)})"
            )
        elif param.kind is inspect.Parameter.POSITIONAL_ONLY:
            # Everything is passed by keyword so the assembled call is
            # order-independent; a positional-only parameter cannot be reached.
            report["errors"].append(
                f"binds {name!r}, which is positional-only and cannot be "
                "supplied by keyword"
            )

    bound = set(binding.bound_param_names())
    for name, param in params.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty and name not in bound:
            report["errors"].append(
                f"leaves required parameter {name!r} unbound; add it to "
                "context_params (or observable_param / connection)"
            )

    if binding.connection is not None:
        try:
            resolve_connection_factory(decl)
        except BindingError as exc:
            report["errors"].append(str(exc))

    if report["errors"]:
        report["status"] = "error"
    return report


def verify_bindings(
    contract: Optional[AnalyzerContract] = None,
) -> Dict[str, Any]:
    """Verify every declaration. Returns a machine-readable report."""
    contract = contract or get_contract()
    results = [verify_declaration(d) for d in contract.analyzers]
    return {
        "source": str(contract.path),
        "total": len(results),
        "bound": sum(1 for r in results if r["dispatchable"]),
        "declared_only": [r["key"] for r in results if r["status"] == "declared_only"],
        "errors": sum(1 for r in results if r["status"] == "error"),
        "valid": not any(r["status"] == "error" for r in results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _describe(decl: AnalyzerDeclaration) -> Dict[str, Any]:
    payload = decl.to_dict()
    try:
        fn = resolve_entrypoint(decl)
        payload["signature"] = f"{decl.entrypoint}{inspect.signature(fn)}"
    except Exception as exc:
        payload["signature_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and describe analyzer contract input bindings."
    )
    parser.add_argument(
        "--verify", action="store_true", help="Verify every declared binding"
    )
    parser.add_argument("--describe", help="Describe one analyzer by key")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    contract = get_contract()

    if args.describe:
        try:
            decl = get_declaration(args.describe, contract=contract)
        except UnknownAnalyzer as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(_describe(decl), indent=2))
        return 0

    report = verify_bindings(contract)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 1

    for result in report["results"]:
        if result["status"] == "ok":
            print(f"  ok         {result['key']}")
        elif result["status"] == "declared_only":
            print(f"  declared   {result['key']}  (no input_binding — not dispatchable)")
        else:
            print(f"  ERROR      {result['key']}")
            for err in result["errors"]:
                print(f"               {err}")
    print(
        f"\n{report['bound']}/{report['total']} declaration(s) bound, "
        f"{report['errors']} with errors"
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
