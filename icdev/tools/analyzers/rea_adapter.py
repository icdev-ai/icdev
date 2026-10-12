#!/usr/bin/env python3
# CUI // SP-CTI
"""REA analyzers on the analyzer contract (rea-wire-02, project card ``rea``).

WHAT THIS IS. The adapter between ``args/analyzer_contract.yaml`` and REA
(morluto/rea, ``rea-agents`` 6.4.0), the stdio MCP server rea-wire-01
registered in ``args/external_mcp_servers.yaml``. One entrypoint,
:func:`analyze`, declared once per observable family (``rea_native``,
``rea_dotnet``, ``rea_javascript``, ``rea_har``, ``rea_apk``, ``rea_wasm``,
``rea_website``). It calls REA THROUGH ``tools/mcp_client`` -- never by
spawning ``node`` itself -- so every rail that card built applies unchanged:
the exact pin, the read-only allowlist, single-user-only, air-gap removal, and
one ``re_analysis.*`` audit row per call with the target sha256.

WHICH FAMILIES ACTUALLY RUN. Exactly the rea-spike-01 GO rows reachable through
the rea-wire-01 allowlist:

    binary           open_binary -> binary_overview -> list_unknowns
                     (+ procedure_pseudo_code when ``procedure`` is given)
    dotnet_assembly  inspect_managed_artifact          (static, no engine)
    js_bundle        analyze_javascript_application, detail=summary
    har              inspect_web_network_capture       (needs sensitive_values)

and three that NEVER run, each ``unavailable`` with the measured reason rather
than an empty success:

    wasm     ``rea_no_wasm_analysis`` -- REA 6.4.0 has no WebAssembly analysis
             (rea-spike-01: ``open_binary`` -> unsupported binary format)
    apk      ``not_allowlisted`` -- REA's JADX path is not on the rea-wire-01
             allowlist and no card has measured it
    website  ``not_allowlisted`` -- REA's website tools declare accessesNetwork

THE PROVIDER IS CHOSEN BY THE LICENSE GATE, NEVER BY REA. A native run names its
engine (``engine=``, default ``ghidra``); :func:`tools.analyzers.re_engines.
evaluate` decides it first, and ``open_binary`` is called with that
``provider_id`` -- never ``auto``, which would let REA pick Hopper or IDA
behind the gate's back. A refused engine is ``unavailable`` naming the gate's
reason; the decision's ``provenance`` rides on every report.

EVIDENCE MAPPING -- the house rules, applied to REA's record shape:

    normalized_result     -> ``body[]`` (one entry per evidence record)
    evidence_id           -> kept, per entry and in ``evidence_ids``
    raw_result            -> dropped (it duplicates normalized_result; the
                             full record stays in REA's session / an export)
    limitations           -> ``limitations``; residual_unknowns / unknowns /
                             list_unknowns -> ``residual_unknowns``.
                             REA's ``[]`` means MEASURED, none, and is kept.
                             A call that was not made or failed gives
                             ``None`` with ``*_basis`` naming why -- never ``[]``
    truncated: true       -> status ``partial`` (reason ``truncated``)
    response over budget  -> ``partial`` (``response_over_budget``): the
                             analysis completed, the inline answer did not
                             arrive; its evidence_id is kept for an export
    engine missing        -> ``unavailable`` (``engine_unavailable``), never ok
    pseudocode            -> ``basis: decompiled`` with ICDEV's own
                             low confidence. REA calls it ``observed``; that
                             value is kept as ``rea_confidence`` only. It is a
                             reconstruction, never recovered source

AUTHORIZED TARGETS ONLY. A run needs an operator basis (``owned`` |
``licensed`` | ``customer-authorized``) from ``authorization_basis=`` or
``ICDEV_RE_AUTHORIZATION_BASIS`` -- the same rule and variable as
``ghidra_headless``. With none it is refused (``error`` /
``authorization_missing``) before REA is reached; the target sha256 is recorded
on the refusal too. The basis becomes a ``target_auth`` grant on THIS run's
registry only, so it cannot authorize anything else.

CONTAINMENT. Declared ``sandbox: sandboxed``: the dispatcher runs this module
inside ``SandboxExecutor`` and refuses (``sandbox_unavailable``) rather than
fall back in-process. Inside that boundary the REA process is additionally
launched under the omx-guard-05 L3 write sandbox when the host has one
(bubblewrap / Landlock); where it has none (Windows, macOS, a kernel without
Landlock) ``containment.write_sandbox`` says so on the report. The sandbox image
must carry ICDEV, Node, the pinned REA install and Ghidra -- the same
deployment step Gap 71 records for ``ghidra_decompile``.

Statuses (the dispatcher lifts them through ``status_key: status``):
``ok`` | ``partial`` | ``unavailable`` | ``error``.

Usage:
    python -m tools.analyzers.rea_adapter <target> --family binary --authorization-basis owned --json
    python -m tools.analyzers.rea_adapter <dir> --family js_bundle --authorization-basis owned
    python -m tools.analyzers.rea_adapter <har> --family har --sensitive-value <csrf> --authorization-basis owned
    python -m tools.analyzers.rea_adapter --families

Exit codes: 0 a report was produced, whatever it says; 2 none could be.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "FAMILIES",
    "STATUSES",
    "analyze",
    "map_response",
]

SERVER = "rea"
REA_VERSION = "6.4.0"

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"
STATUSES: Tuple[str, ...] = (STATUS_OK, STATUS_PARTIAL, STATUS_UNAVAILABLE, STATUS_ERROR)

#: Why ``limitations`` / ``residual_unknowns`` is what it is. Each names a
#: different repair, so none collapses into a generic "unknown".
BASIS_REPORTED = "reported_by_rea"
BASIS_NOT_REPORTED = "not_reported_by_tool"
BASIS_NOT_RUN = "not_run"

#: ICDEV's own confidence for decompiler output -- the ghidra_headless value,
#: so two backends do not state two confidences for the same kind of claim.
DECOMPILED_CONFIDENCE = "low"
#: REA tools whose normalized_result is decompiler output.
DECOMPILING_TOOLS = frozenset({"procedure_pseudo_code", "batch_decompile"})

ENV_AUTHORIZATION = "ICDEV_RE_AUTHORIZATION_BASIS"
AUTHORIZATION_BASES: Tuple[str, ...] = ("owned", "licensed", "customer-authorized")
DEFAULT_ENGINE = "ghidra"

#: family -> how it is analysed. ``unavailable`` families never reach REA.
FAMILIES: Dict[str, Dict[str, Any]] = {
    "binary": {"engine": True},
    "dotnet_assembly": {"engine": False},
    "js_bundle": {"engine": False},
    "har": {"engine": False},
    "wasm": {"unavailable": (
        "rea_no_wasm_analysis",
        "REA 6.4.0 has no WebAssembly analysis: rea-spike-01 measured open_binary -> "
        "'unsupported binary format' on Windows and Linux. Nothing was run.")},
    "apk": {"unavailable": (
        "not_allowlisted",
        "REA's APK path (JADX) is not on the rea-wire-01 allowlist in "
        "args/external_mcp_servers.yaml and no card has measured it. Nothing was run.")},
    "website": {"unavailable": (
        "not_allowlisted",
        "REA's website tools declare accessesNetwork and are excluded from the "
        "read-only allowlist (rea-wire-01). Nothing was run.")},
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# The one report constructor
# ---------------------------------------------------------------------------

def _report(
    target: str,
    family: str,
    status: str,
    *,
    reason: Optional[str] = None,
    reason_detail: Optional[str] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    unknowns: Optional[Tuple[List[Any], str]] = None,
    not_run_basis: str = BASIS_NOT_RUN,
    engine: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    containment: Optional[Dict[str, Any]] = None,
    rea_errors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Every status carries the same keys, so no caller writes ``.get(x) or []``."""
    records = list(records or [])
    measured_lims = [r["limitations"] for r in records if r["limitations"] is not None]
    if measured_lims:
        limitations: Optional[List[str]] = []
        for group in measured_lims:
            limitations.extend(x for x in group if x not in limitations)
        limitations_basis = BASIS_REPORTED
    else:
        limitations = None
        limitations_basis = BASIS_NOT_REPORTED if records else not_run_basis

    residual: Optional[List[Any]] = None
    residual_basis = BASIS_NOT_REPORTED if records else not_run_basis
    measured_unknowns = [r["residual_unknowns"] for r in records if r["residual_unknowns"] is not None]
    if unknowns is not None:
        residual, residual_basis = list(unknowns[0]), unknowns[1]
    if measured_unknowns:
        residual = list(residual or [])
        for group in measured_unknowns:
            residual.extend(x for x in group if x not in residual)
        residual_basis = BASIS_REPORTED

    truncated: Optional[bool] = (
        any(r["truncated"] for r in records) if records else None
    )
    return {
        "analyzer": SERVER,
        "family": family,
        "target": target,
        "status": status,
        "reason": reason,
        "reason_detail": reason_detail,
        "body": [
            {k: r[k] for k in ("tool", "evidence_id", "operation", "basis", "confidence",
                               "rea_confidence", "authority", "provider", "subject",
                               "normalized_result")}
            for r in records
        ],
        "evidence_ids": [r["evidence_id"] for r in records] if records else None,
        "limitations": limitations,
        "limitations_basis": limitations_basis,
        "residual_unknowns": residual,
        "residual_unknowns_basis": residual_basis,
        "truncated": truncated,
        "rea_errors": list(rea_errors or []),
        "engine": engine,
        "provenance": provenance
        if provenance is not None
        else {"target_sha256": None, "authorization_basis": None, "analysis": "local"},
        "containment": containment,
        "rea_version": REA_VERSION,
        "taxonomy": _taxonomy(family, records, residual),
        "generated_at": _iso_now(),
    }


def _taxonomy(family: str, records: List[Dict[str, Any]], residual: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Structure only, ``info`` only: this adapter REPORTS and derives no verdict."""
    tags: List[Dict[str, Any]] = []
    if not records:
        return tags
    tags.append({"predicate": "evidence", "level": "info", "value": len(records)})
    fmt = next((r["subject"].get("format") for r in records
                if isinstance(r.get("subject"), dict) and r["subject"].get("format")), None)
    if fmt:
        tags.append({"predicate": "format", "level": "info", "value": fmt})
    if any(r["basis"] == "decompiled" for r in records):
        tags.append({"predicate": "decompiled", "level": "info",
                     "value": sum(1 for r in records if r["basis"] == "decompiled")})
    if residual is not None:
        tags.append({"predicate": "unknowns", "level": "info", "value": len(residual)})
    return tags


# ---------------------------------------------------------------------------
# REA's response -> one mapped step
# ---------------------------------------------------------------------------

def _payload(result: Any) -> Tuple[Optional[Dict[str, Any]], bool]:
    """``(parsed body, is_error)`` from an MCP ``tools/call`` result."""
    if not isinstance(result, dict):
        return None, True
    body = result.get("structuredContent")
    if not isinstance(body, dict):
        text = "".join(c.get("text", "") for c in result.get("content") or []
                       if isinstance(c, dict) and c.get("type") == "text")
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = None
    is_error = bool(result.get("isError")) or (isinstance(body, dict) and "error" in body)
    return (body if isinstance(body, dict) else None), is_error


def _any_truncated(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, dict):
        if value.get("truncated") is True:
            return True
        return any(_any_truncated(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(_any_truncated(v, depth + 1) for v in value)
    return False


def _record(tool: str, body: Dict[str, Any]) -> Dict[str, Any]:
    normalized = body.get("normalized_result")
    decompiled = tool in DECOMPILING_TOOLS
    unknowns = None
    if isinstance(normalized, dict):
        for key in ("residual_unknowns", "unknowns"):
            if isinstance(normalized.get(key), list):
                unknowns = list(normalized[key])
                break
    lims = body.get("limitations")
    return {
        "tool": tool,
        "evidence_id": body.get("evidence_id"),
        "operation": body.get("operation"),
        "basis": "decompiled" if decompiled else "observed",
        "confidence": DECOMPILED_CONFIDENCE if decompiled else body.get("confidence"),
        "rea_confidence": body.get("confidence"),
        "authority": body.get("authority"),
        "provider": body.get("provider"),
        "subject": body.get("subject"),
        "normalized_result": normalized,
        "limitations": list(lims) if isinstance(lims, list) else None,
        "residual_unknowns": unknowns,
        "truncated": _any_truncated(normalized),
    }


def map_response(tool: str, call: Mapping[str, Any]) -> Dict[str, Any]:
    """One registry call result -> ``{kind, ...}``.

    kinds: ``evidence`` (an REA evidence record), ``result`` (a session answer:
    open_binary, list_unknowns), ``over_budget``, ``unavailable``, ``error``.
    """
    if not call.get("ok"):
        refused = call.get("refused")
        return {"kind": "error", "reason": refused or "call_failed",
                "detail": call.get("error"), "rea_error": None}
    body, is_error = _payload(call.get("result"))
    if is_error:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {"code": "unparseable_error", "category": None,
                                                 "message": "REA returned an error with no body"}
        code, category = err.get("code"), err.get("category")
        rea_error = {"tool": tool, "code": code, "category": category,
                     "message": err.get("message"), "details": err.get("details")}
        if category == "resource_constraint":
            limits = ((err.get("details") or {}).get("reported_limits") or {})
            return {"kind": "over_budget", "reason": "response_over_budget",
                    "detail": err.get("message"), "evidence_id": limits.get("evidence_id"),
                    "rea_error": rea_error}
        if category == "unavailable":
            reason = "engine_unavailable" if code == "provider_unavailable" else f"rea_{code}"
            return {"kind": "unavailable", "reason": reason, "detail": err.get("message"),
                    "rea_error": rea_error}
        return {"kind": "error", "reason": f"rea_{code}", "detail": err.get("message"),
                "rea_error": rea_error}
    if body is None:
        return {"kind": "error", "reason": "rea_unparseable", "detail": "no JSON body",
                "rea_error": None}
    if "evidence_id" in body:
        return {"kind": "evidence", "record": _record(tool, body)}
    return {"kind": "result", "result": body.get("result", body)}


# ---------------------------------------------------------------------------
# Rails: authorization, license gate, containment
# ---------------------------------------------------------------------------

def _digest(target: Path) -> Optional[str]:
    from icdev.tools.mcp_client import target_auth

    try:
        return target_auth.target_digest(target)
    except Exception:  # noqa: BLE001 -- unreadable: no digest, said so by the caller
        return None


def _license_gate(engine: str) -> Dict[str, Any]:
    from icdev.tools.analyzers import re_engines

    return re_engines.evaluate(engine)


def _write_sandbox(scratch: str) -> Tuple[List[str], Dict[str, Any]]:
    """The L3 prefix for the REA process, or ``[]`` with the reason it is absent."""
    try:
        from icdev.tools.hooks import write_sandbox

        state = write_sandbox.status()
        if not state.get("supported"):
            return [], {"applied": False, "backend": state.get("backend"),
                        "reason": state.get("reason")}
        return write_sandbox.prefix(scratch), {"applied": True, "backend": state.get("backend"),
                                               "reason": state.get("reason")}
    except Exception as exc:  # noqa: BLE001 -- reported on the report, never silent
        return [], {"applied": False, "backend": None, "reason": f"{type(exc).__name__}: {exc}"}


def _classification() -> str:
    return os.environ.get("ICDEV_CLASSIFICATION", "CUI").strip().upper() or "CUI"


# ---------------------------------------------------------------------------
# The entrypoint
# ---------------------------------------------------------------------------

def _plan(family: str, path: str, *, engine: Optional[str], procedure: Optional[str],
          sensitive_values: Optional[Sequence[str]]) -> List[Tuple[str, Dict[str, Any], bool]]:
    """``[(tool, arguments, required)]``. A non-required step may fail alone."""
    if family == "binary":
        steps = [("open_binary", {"path": path, "provider_id": engine}, True),
                 ("binary_overview", {}, True)]
        if procedure:
            steps.append(("procedure_pseudo_code", {"procedure": procedure}, True))
        steps.append(("list_unknowns", {}, False))
        return steps
    if family == "dotnet_assembly":
        return [("inspect_managed_artifact", {"path": path}, True)]
    if family == "js_bundle":
        fmt = "directory" if Path(path).is_dir() else "auto"
        return [("analyze_javascript_application",
                 {"input_path": path, "format": fmt, "detail": "summary"}, True)]
    if family == "har":
        return [("inspect_web_network_capture",
                 {"capture_path": path, "format": "har",
                  "sensitive_values": list(sensitive_values or [])}, True)]
    raise KeyError(family)


def analyze(
    target: str,
    *,
    family: str,
    engine: Optional[str] = None,
    procedure: Optional[str] = None,
    sensitive_values: Optional[List[str]] = None,
    authorization_basis: Optional[str] = None,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse one authorized artifact with REA, or say exactly why not.

    Every keyword carries a default except ``family``, which each contract
    declaration binds as a static argument.
    """
    if family not in FAMILIES:
        return _report(str(target), family, STATUS_ERROR, reason="unknown_family",
                       reason_detail=f"family {family!r} is not one of {sorted(FAMILIES)}")
    spec = FAMILIES[family]
    if "unavailable" in spec:
        reason, detail = spec["unavailable"]
        return _report(str(target), family, STATUS_UNAVAILABLE, reason=reason, reason_detail=detail)

    path = Path(str(target)).expanduser()
    try:
        path = path.resolve()
        readable = path.exists()
    except OSError:
        readable = False
    sha256 = _digest(path) if readable else None
    if sha256 is None:
        return _report(str(target), family, STATUS_ERROR, reason="source_unreadable",
                       reason_detail="not a readable file or directory; nothing was run.")

    basis_raw = authorization_basis if authorization_basis is not None else os.environ.get(ENV_AUTHORIZATION, "")
    auth = str(basis_raw or "").strip().lower()
    provenance = {"target_sha256": sha256,
                  "authorization_basis": auth if auth in AUTHORIZATION_BASES else None,
                  "analysis": "local", "server": SERVER, "rea_version": REA_VERSION}
    if auth not in AUTHORIZATION_BASES:
        return _report(str(path), family, STATUS_ERROR, reason="authorization_missing",
                       reason_detail=("no authorization basis for this target (%s): pass "
                                      "authorization_basis= or set %s. Nothing was run."
                                      % (" | ".join(AUTHORIZATION_BASES), ENV_AUTHORIZATION)),
                       provenance=provenance)

    if family == "har" and sensitive_values is None:
        return _report(str(path), family, STATUS_ERROR, reason="sensitive_values_required",
                       reason_detail=("REA keeps response-BODY secrets verbatim in HAR evidence "
                                      "(rea-spike-01: a CSRF token survived). Pass "
                                      "sensitive_values -- [] to assert there are none."),
                       provenance=provenance)

    decision = None
    if spec["engine"]:
        engine = (engine or DEFAULT_ENGINE).strip().lower()
        decision = _license_gate(engine)
        provenance["engine"] = decision.get("provenance")
        if not decision.get("allowed"):
            return _report(str(path), family, STATUS_UNAVAILABLE, reason=decision.get("reason"),
                           reason_detail=decision.get("message"), engine=decision,
                           provenance=provenance)

    from icdev.tools.mcp_client import registry as reg

    scratch = tempfile.mkdtemp(prefix="icdev-rea-")
    prefix, l3 = _write_sandbox(scratch)
    containment = {"sandbox_posture": "sandboxed", "write_sandbox": l3}
    common = dict(engine=decision, provenance=provenance, containment=containment)

    registry = reg.ExternalToolRegistry(command_prefix=prefix)
    try:
        if not any(s.get("name") == SERVER for s in reg.enabled_servers()):
            return _report(str(path), family, STATUS_UNAVAILABLE, reason="rea_not_enabled",
                           reason_detail=("the `rea` entry in args/external_mcp_servers.yaml is "
                                          "off (or the master switch is), this is an air-gapped "
                                          "or shared deployment. Nothing was run."), **common)
        tools = {t.remote_name for t in registry.discover() if t.server == SERVER}
        if not tools:
            return _report(str(path), family, STATUS_UNAVAILABLE, reason="rea_not_connected",
                           reason_detail=("REA is enabled but did not start: ICDEV_REA_ENTRY "
                                          "unset, the installed rea-agents is not %s, or the "
                                          "process failed to spawn. Nothing was run." % REA_VERSION),
                           **common)
        registry.grant_target(SERVER, path, basis=auth, operator=actor,
                              note=f"rea_adapter {family}")
        return _run(registry, tools, str(path), family,
                    _plan(family, str(path), engine=engine, procedure=procedure,
                          sensitive_values=sensitive_values), common)
    finally:
        registry.close()
        _rmtree(scratch)


def _run(registry: Any, tools: set, path: str, family: str,
         plan: List[Tuple[str, Dict[str, Any], bool]], common: Dict[str, Any]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    rea_errors: List[Dict[str, Any]] = []
    unknowns: Optional[Tuple[List[Any], str]] = None
    failure: Optional[Dict[str, Any]] = None
    over_budget: Optional[Dict[str, Any]] = None
    for tool, args, required in plan:
        if tool not in tools:
            step = {"kind": "error", "reason": "not_allowlisted",
                    "detail": f"{tool!r} is not on the rea allowlist", "rea_error": None}
        else:
            step = map_response(tool, registry.call(f"ext__{SERVER}__{tool}", args,
                                                    classification=_classification()))
        if step.get("rea_error"):
            rea_errors.append(step["rea_error"])
        kind = step["kind"]
        if kind == "evidence":
            records.append(step["record"])
            continue
        if kind == "result":
            if tool == "list_unknowns":
                items = step["result"].get("items") if isinstance(step["result"], dict) else None
                if isinstance(items, list):
                    unknowns = (items, BASIS_REPORTED)
            continue
        if kind == "over_budget":
            over_budget = step
            break
        if required:
            failure = step
            break

    if over_budget is not None:
        evidence = over_budget.get("evidence_id")
        return _report(path, family, STATUS_PARTIAL, reason=over_budget["reason"],
                       reason_detail=("%s Retained evidence: %s -- export it with "
                                      "export_evidence_bundle." % (over_budget["detail"], evidence)),
                       records=records, unknowns=unknowns, rea_errors=rea_errors, **common)
    if failure is not None:
        status = STATUS_PARTIAL if records else (
            STATUS_UNAVAILABLE if failure["kind"] == "unavailable" else STATUS_ERROR)
        return _report(path, family, status, reason=failure["reason"],
                       reason_detail=failure.get("detail"), records=records, unknowns=unknowns,
                       rea_errors=rea_errors, not_run_basis="run_failed", **common)
    if any(r["truncated"] for r in records):
        return _report(path, family, STATUS_PARTIAL, reason="truncated",
                       reason_detail="REA reported truncated: true; the answer covers a prefix.",
                       records=records, unknowns=unknowns, rea_errors=rea_errors, **common)
    return _report(path, family, STATUS_OK, records=records, unknowns=unknowns,
                   rea_errors=rea_errors, **common)


def _rmtree(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--family", choices=sorted(FAMILIES))
    ap.add_argument("--engine", default=None, help="native provider, through the license gate")
    ap.add_argument("--procedure", default=None, help="also decompile this procedure (binary)")
    ap.add_argument("--sensitive-value", action="append", dest="sensitive_values")
    ap.add_argument("--no-sensitive-values", action="store_true",
                    help="assert a HAR holds no secrets (passes [])")
    ap.add_argument("--authorization-basis", default=None, choices=AUTHORIZATION_BASES)
    ap.add_argument("--families", action="store_true", help="list the families and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.families:
        print(json.dumps({k: (v.get("unavailable") or ["runs", None])[0] for k, v in FAMILIES.items()},
                         indent=2))
        return 0
    if not args.target or not args.family:
        ap.error("target and --family are required")
    sensitive = [] if args.no_sensitive_values else args.sensitive_values
    try:
        report = analyze(args.target, family=args.family, engine=args.engine,
                         procedure=args.procedure, sensitive_values=sensitive,
                         authorization_basis=args.authorization_basis)
    except Exception as exc:  # noqa: BLE001 -- exit 2: no report could be produced
        print(f"rea_adapter: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    print(f"{report['status'].upper():<12} {report['family']}  {report['target']}")
    if report["reason"]:
        print(f"  reason: {report['reason']} -- {report['reason_detail']}")
    for entry in report["body"]:
        print(f"  {entry['tool']:<32} {entry['basis']:<11} {entry['evidence_id']}")
    print(f"  limitations: {report['limitations_basis']}   "
          f"residual_unknowns: {report['residual_unknowns_basis']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
