#!/usr/bin/env python3
# CUI // SP-CTI
"""Prove that porting an analyzer onto the contract changed nothing (anz-mig-01).

An interface migration has exactly one success criterion: the analyzer behaves
the same afterwards. Asserting that in a commit message is worth nothing, so
this harness checks it against a fixed input set declared in
``args/analyzer_parity_cases.yaml``.

Two checks per case, because there are two ways a port can silently change
behaviour:

**Input adaptation.** The binding could land a value in the wrong parameter.
Every case declares ``direct_kwargs`` — the call a hand-written call site makes,
written out from the existing call sites rather than generated from the binding.
The harness asserts the contract assembles byte-identical kwargs. This executes
nothing, so it holds on any machine with any database.

**Output transparency.** The binding could wrap the result in a report envelope,
coerce it to a dict, or swallow an exception. Cases marked ``live`` are executed
BOTH ways in the same process against the same database and their outcomes
compared: same return value, or the same exception type and message.

The comparison is direct-vs-bound in one environment, never against a golden
file captured on some other host. A golden file would bake in whatever rows that
host happened to hold and would be stale the first time one changed; this
comparison is true on an empty worktree DB, on a seeded one, and in CI.

Cases that are not safe to execute twice — anything that commits, anything that
reaches the network — are reported as ``call_shape_only`` with the reason, not
quietly dropped. A harness that reports 100% by not running the hard half is the
failure mode this card was written against.

Usage:
    python -m tools.analyzers.parity
    python -m tools.analyzers.parity --json
    python -m tools.analyzers.parity --live --json
    python -m tools.analyzers.parity --analyzer pvm_risk_prediction --live
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from tools.analyzers.binding import (
    BindingError,
    build_kwargs,
    get_declaration,
    invoke,
    resolve_connection_factory,
    resolve_entrypoint,
)
from tools.analyzers.contract import AnalyzerContract, get_contract

CASES_FILENAME = "analyzer_parity_cases.yaml"

#: Placeholder the harness substitutes for the live connection object when
#: comparing assembled kwargs. Connections are not comparable by value, so the
#: connection parameter is compared by presence and name, not by identity.
CONNECTION_SENTINEL = "<connection>"


class ParityError(RuntimeError):
    """The parity case file is missing or malformed."""


def find_cases_path(start: Optional[Path] = None) -> Path:
    """Locate ``args/analyzer_parity_cases.yaml`` the same way the contract is found."""
    here = (start or Path(__file__)).resolve()
    for rel in (("args",), ("data", "args")):
        for parent in here.parents:
            candidate = parent.joinpath(*rel, CASES_FILENAME)
            if candidate.is_file():
                return candidate
    raise ParityError(f"{CASES_FILENAME} not found in any args/ directory above {here}")


def load_cases(path: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], Path]:
    """Read the fixed input set. Raises on a malformed file."""
    cases_path = Path(path) if path else find_cases_path()
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ParityError(f"{cases_path}: top level must be a mapping")
    if data.get("version") != 1:
        raise ParityError(f"{cases_path}: unsupported version {data.get('version')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ParityError(f"{cases_path}: `cases` must be a non-empty list")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ParityError(f"{cases_path}: cases[{index}] must be a mapping")
        for field in ("analyzer", "name", "observable_type", "direct_kwargs"):
            if field not in case:
                raise ParityError(f"{cases_path}: cases[{index}] is missing `{field}`")
        if "observable" not in case:
            raise ParityError(f"{cases_path}: cases[{index}] is missing `observable`")
    return cases, cases_path


def _canonical(value: Any) -> str:
    """Stable text form so two structures can be compared as bytes."""
    return json.dumps(value, sort_keys=True, default=repr)


def capture(fn, *args, **kwargs) -> Dict[str, Any]:
    """Run *fn* and record its outcome — return value or exception — as data.

    An exception is an outcome, not a harness failure. Half of what these
    analyzers do on a database that has not been migrated is raise, and a port
    that turned a raise into ``{"error": ...}`` would be exactly the regression
    this harness is looking for.
    """
    try:
        return {"outcome": "return", "value": _canonical(fn(*args, **kwargs))}
    except BaseException as exc:  # noqa: BLE001 — recorded, then re-reported
        return {
            "outcome": "raise",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def check_call_shape(
    case: Mapping[str, Any], *, contract: Optional[AnalyzerContract] = None
) -> Dict[str, Any]:
    """Assert the contract assembles the same kwargs the hand-written call uses."""
    decl = get_declaration(case["analyzer"], contract=contract)
    binding = decl.input_binding
    connection_param = (
        binding.connection.param if binding and binding.connection else None
    )

    try:
        bound = build_kwargs(
            decl,
            case["observable_type"],
            case["observable"],
            case.get("context") or {},
            connection=CONNECTION_SENTINEL,
        )
    except BindingError as exc:
        # A binding that cannot even assemble the call is a parity FAILURE to be
        # reported, not a crash. The harness has to survive the broken case or
        # it cannot describe which case broke — and in --json mode a traceback
        # produces no report at all.
        return {
            "match": False,
            "error": f"{type(exc).__name__}: {exc}",
            "bound_kwargs": None,
            "direct_kwargs": _canonical(case["direct_kwargs"]),
        }
    expected = dict(case["direct_kwargs"])
    if connection_param is not None:
        # The hand-written call site opens the connection itself; the case file
        # declares the analysis arguments only. Both sides get the sentinel.
        expected[connection_param] = CONNECTION_SENTINEL

    match = _canonical(bound) == _canonical(expected)
    return {
        "match": match,
        "bound_kwargs": _canonical(bound),
        "direct_kwargs": _canonical(expected),
    }


def check_live(
    case: Mapping[str, Any], *, contract: Optional[AnalyzerContract] = None
) -> Dict[str, Any]:
    """Execute the analyzer both ways and diff the outcomes."""
    decl = get_declaration(case["analyzer"], contract=contract)
    binding = decl.input_binding
    fn = resolve_entrypoint(decl)
    direct_kwargs = dict(case["direct_kwargs"])

    if binding is not None and binding.connection is not None:
        factory = resolve_connection_factory(decl)
        conn = factory()
        try:
            direct = capture(fn, **{binding.connection.param: conn}, **direct_kwargs)
            if binding.connection.commit:
                conn.commit()
        finally:
            conn.close()
    else:
        direct = capture(fn, **direct_kwargs)

    bound = capture(
        invoke,
        case["analyzer"],
        case["observable_type"],
        case["observable"],
        case.get("context") or {},
        contract=contract,
    )
    return {
        "match": _canonical(direct) == _canonical(bound),
        "direct": direct,
        "bound": bound,
    }


def run(
    *,
    live: bool = False,
    analyzer: Optional[str] = None,
    cases_path: Optional[Path] = None,
    contract: Optional[AnalyzerContract] = None,
) -> Dict[str, Any]:
    """Run the whole fixed input set and return a machine-readable report."""
    contract = contract or get_contract()
    cases, resolved_path = load_cases(cases_path)
    if analyzer:
        cases = [c for c in cases if c["analyzer"] == analyzer]

    results: List[Dict[str, Any]] = []
    for case in cases:
        entry: Dict[str, Any] = {
            "analyzer": case["analyzer"],
            "case": case["name"],
            "observable_type": case["observable_type"],
            "call_shape": check_call_shape(case, contract=contract),
        }
        if not live:
            entry["live"] = {"ran": False, "reason": "--live not requested"}
        elif not case.get("live", False):
            entry["live"] = {
                "ran": False,
                "reason": case.get("live_skip_reason", "case declares live: false"),
            }
        else:
            entry["live"] = {"ran": True, **check_live(case, contract=contract)}
        entry["status"] = (
            "pass"
            if entry["call_shape"]["match"] and entry["live"].get("match", True)
            else "FAIL"
        )
        results.append(entry)

    executed = [r for r in results if r["live"].get("ran")]
    return {
        "source": str(resolved_path),
        "contract": str(contract.path),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "live_executed": len(executed),
        "call_shape_only": [
            {"analyzer": r["analyzer"], "case": r["case"], "reason": r["live"]["reason"]}
            for r in results
            if not r["live"].get("ran")
        ],
        "valid": all(r["status"] == "pass" for r in results),
        "results": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff analyzer behaviour before and after the contract port."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also execute cases declared live-safe and diff their outcomes",
    )
    parser.add_argument("--analyzer", help="Restrict to one analyzer key")
    parser.add_argument("--cases", help="Override the parity case file path")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    try:
        report = run(
            live=args.live,
            analyzer=args.analyzer,
            cases_path=Path(args.cases) if args.cases else None,
        )
    except ParityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 1

    for result in report["results"]:
        marker = "pass" if result["status"] == "pass" else "FAIL"
        live = result["live"]
        suffix = "live+call-shape" if live.get("ran") else "call-shape only"
        print(f"  {marker:4}  {result['analyzer']:22} {result['case']:34} {suffix}")
        if result["status"] == "FAIL":
            if not result["call_shape"]["match"]:
                if result["call_shape"].get("error"):
                    print(f"          error : {result['call_shape']['error']}")
                print(f"          bound : {result['call_shape']['bound_kwargs']}")
                print(f"          direct: {result['call_shape']['direct_kwargs']}")
            if live.get("ran") and not live.get("match"):
                print(f"          bound : {_canonical(live['bound'])}")
                print(f"          direct: {_canonical(live['direct'])}")
    if report["call_shape_only"]:
        print("\nNot executed live (input adaptation still checked):")
        for skipped in report["call_shape_only"]:
            print(f"  - {skipped['analyzer']}/{skipped['case']}: {skipped['reason']}")
    print(
        f"\n{report['passed']}/{report['total']} case(s) pass, "
        f"{report['live_executed']} executed live"
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
