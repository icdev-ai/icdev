# CUI // SP-CTI
"""Pre-apply compliance gate for Terraform plans.

Parses ``terraform plan -json`` output, computes the resource delta vs the
prior state, then runs all infra/* IQE checks against the planned final state.

CLI::
    python tools/infra_canvas/preapply_gate.py plan.json
    python tools/infra_canvas/preapply_gate.py --gate plan.json   # exits 1 on fail
    cat plan.json | python tools/infra_canvas/preapply_gate.py -  # stdin

Return schema::
    {
        "gate": "pass" | "fail",
        "violations": [{"source", "check", "severity", "detail", "affected"}],
        "delta": {"add": [...], "modify": [...], "delete": [...]},
        "skipped": [{"check", "reason", "detail"}],
    }

``skipped`` is what tells `pass over eight satisfied rules` apart from `pass
over an empty rule set`. See ``_run_iqe_checks``.

THIS IS THE PRE-APPLY GATE. Singular, since flx-ci-02 -- it is what the floci
IaC job exercises (flx-ci-01, ``.github/workflows/floci-iac-gate.yml``) and
what ``tools/twin_core/adapters/idc.py::simulate_delta`` calls.

A second file used to sit next door (``pre_apply_gate.py``, "IDC IaC Twin
Phase 1") taking the same input and answering the same question with a
different verdict vocabulary. It was measured and deleted, not merged: it had
ZERO runtime callers, and it returned the identical verdict for the compliant
and the violating flx-ci-01 fixture because its rules asked
ESTATE-COMPLETENESS questions ("is there a KMS service in this design?") of a
plan DELTA -- so it could only ever pass a plan that was itself the whole
estate. Nothing was lost with it: its rulebook, ``infra_engine.
assess_infra_design``, is consumed live by ``tools/infra_canvas/blueprint.py``
over the full design graph, which is the input those rules were written for.

Derivation: ``docs/audits/flx-ci-02-two-preapply-gates.md``.
A SECOND GATE UNDER ``tools/infra_canvas/`` NOW FAILS
``tests/infra_canvas/test_one_preapply_gate.py`` -- including a shim, which is
just a second gate with a redirect.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.iqe.executor import Executor
from tools.iqe.parser import IQESyntaxError
from tools.iqe.parser import parse as iqe_parse
from tools.infra_canvas.importers.tf_state import (
    _detect_csp,
    _extract_region,
    _extract_tags,
    _map_type,
    _redact,
)

_IQE_QUERIES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "context" / "iqe" / "queries" / "infra"
)

_REPLACE_ACTIONS = frozenset({"delete", "create"})


#: Collections this gate REGISTERS on its executor. A query over anything else
#: cannot be answered here and is SKIPPED, not failed -- see `_run_iqe_checks`.
_PROVIDED_COLLECTIONS = frozenset({"infra.resources"})


def _load_iqe_queries() -> tuple[list[tuple[str, Any]], list[dict[str, str]]]:
    """Load and parse all .iqe files from context/iqe/queries/infra/.

    Returns ``(parsed, skipped)``. A file that will not PARSE used to be
    dropped on the floor by a bare ``except IQESyntaxError: pass`` -- so a
    query that stopped parsing simply stopped being a check, and the gate went
    on reporting `pass` with one fewer rule behind it. It is now reported.
    """
    if not _IQE_QUERIES_DIR.exists():
        return [], []
    queries: list[tuple[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for qfile in sorted(_IQE_QUERIES_DIR.glob("*.iqe")):
        src = qfile.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines()
            if not line.strip().startswith("#")
        ).strip()
        if not code:
            skipped.append({"check": qfile.stem, "reason": "empty"})
            continue
        try:
            queries.append((qfile.stem, iqe_parse(code)))
        except IQESyntaxError as exc:
            skipped.append({
                "check": qfile.stem,
                "reason": "parse_error",
                "detail": str(exc),
            })
    return queries, skipped


def _query_collection(ast: Any) -> str | None:
    """Dotted name of the collection a parsed IQE query reads, if discoverable."""
    parts = getattr(getattr(ast, "collection", None), "parts", None)
    if not parts:
        return None
    return ".".join(str(p) for p in parts)


def _resource_change_to_row(rc: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a resource_change entry to an infra.resources-compatible row.

    Returns None for pure-delete changes (resource absent after apply).
    """
    actions = rc.get("change", {}).get("actions", [])
    if list(actions) == ["delete"]:
        return None

    values: dict = (
        rc.get("change", {}).get("after")
        or rc.get("change", {}).get("before")
        or {}
    )
    csp = _detect_csp(rc)
    tags = _extract_tags(values)
    # Derive classification from a "Classification" tag; default CUI per ICDEV posture.
    classification = (
        tags.pop("Classification", None)
        or tags.pop("classification", None)
        or "CUI"
    )
    return {
        "id": rc.get("address", ""),
        "resource_name": rc.get("address", ""),
        "resource_id": str(
            values.get("id") or values.get("arn") or rc.get("address", "")
        ),
        "resource_type": _map_type(rc.get("type", "")),
        "csp": csp,
        "region": _extract_region(values, csp),
        "classification": classification,
        "tags": json.dumps(tags) if tags else None,
        "cost_per_month": float(values.get("cost_per_month", 0) or 0),
        "config": json.dumps(_redact(values), default=str),
        "created_at": None,
    }


def _compute_delta(resource_changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return {add, modify, delete} address lists from resource_changes."""
    adds: list[str] = []
    modifies: list[str] = []
    deletes: list[str] = []
    for rc in resource_changes:
        actions = list(rc.get("change", {}).get("actions", []))
        addr = rc.get("address", "")
        if set(actions) == _REPLACE_ACTIONS or actions == ["create"]:
            adds.append(addr)
        elif actions == ["delete"]:
            deletes.append(addr)
        elif actions == ["update"]:
            modifies.append(addr)
    return {"add": adds, "modify": modifies, "delete": deletes}


def _run_iqe_checks(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Run the applicable context/iqe/queries/infra/*.iqe against planned rows.

    Returns ``(violations, skipped)``.

    APPLICABLE IS THE WORD. This directory is the INFRA CANVAS' query library,
    not this gate's rule set: three of its eight files
    (``0{3,4,5}_ai_*.iqe``) read ``infra.ai_decisions``, an IDC dashboard
    collection this gate never registers. Running them raised inside the
    executor, each raise was recorded as a CAT3 violation, and so
    ``run_gate`` returned **fail for every plan ever put through it** --
    including a fully compliant one. Measured 2026-09-05: a compliant
    single-bucket plan failed on exactly those three "IQE query error" rows
    and nothing else. ``tools/twin_core/adapters/idc.py::simulate_delta`` is a
    live consumer.

    A query the gate cannot SERVE and a query that BROKE are different facts
    and are no longer merged: the first is skipped and named, the second is
    still a CAT3 violation. Neither is silent -- a check that quietly stopped
    running is how a gate reports clean with fewer rules behind it than anyone
    believes.
    """
    queries, skipped = _load_iqe_queries()

    runnable: list[tuple[str, Any]] = []
    for name, ast in queries:
        collection = _query_collection(ast)
        if collection not in _PROVIDED_COLLECTIONS:
            skipped.append({
                "check": name,
                "reason": "collection_not_provided",
                "detail": (
                    f"reads {collection or 'an unreadable collection'}; this "
                    f"gate provides {sorted(_PROVIDED_COLLECTIONS)}"
                ),
            })
            continue
        runnable.append((name, ast))

    # No rows means the plan creates and modifies nothing -- there is nothing
    # for a resource query to match. Reported through `skipped` above, which is
    # already populated, so an empty plan still says which checks did not run.
    if not rows or not runnable:
        return [], skipped

    ex = Executor()
    ex.register_collection("infra.resources", lambda _conn: rows)

    violations: list[dict[str, Any]] = []
    for name, ast in runnable:
        try:
            hits = ex.run(ast, None)
        except Exception as exc:  # noqa: BLE001
            violations.append({
                "source": "iqe",
                "check": name,
                "severity": "CAT3",
                "detail": f"IQE query error: {exc}",
                "affected": [],
            })
            continue
        if hits:
            violations.append({
                "source": "iqe",
                "check": name,
                "severity": "CAT2",
                "detail": (
                    f"{len(hits)} resource(s) matched violation query '{name}'"
                ),
                "affected": [
                    h.get("id") or h.get("resource_name", "") for h in hits
                ],
            })
    return violations, skipped


def run_gate(plan_json: dict[str, Any]) -> dict[str, Any]:
    """Execute the pre-apply compliance gate against a parsed plan dict.

    Args:
        plan_json: Parsed ``terraform plan -json`` object.

    Returns:
        {"gate": "pass"|"fail", "violations": [...], "delta": {...}}
    """
    resource_changes: list[dict] = plan_json.get("resource_changes", [])
    delta = _compute_delta(resource_changes)

    rows: list[dict[str, Any]] = []
    for rc in resource_changes:
        row = _resource_change_to_row(rc)
        if row is not None:
            rows.append(row)

    violations, skipped = _run_iqe_checks(rows)
    return {
        "gate": "fail" if violations else "pass",
        "violations": violations,
        "delta": delta,
        # WHICH CHECKS DID NOT RUN. `pass` over an empty rule set and `pass`
        # over eight satisfied rules are different facts, and a verdict alone
        # cannot tell them apart. Never counted as violations, never as passes.
        "skipped": skipped,
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Pre-apply compliance gate for Terraform plan JSON."
    )
    ap.add_argument(
        "plan",
        nargs="?",
        default="-",
        help="Path to plan JSON (default: stdin via '-')",
    )
    ap.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 and emit JSON to stdout when gate fails",
    )
    args = ap.parse_args()

    raw = (
        sys.stdin.read()
        if args.plan == "-"
        else Path(args.plan).read_text(encoding="utf-8")
    )
    try:
        plan_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        out: dict[str, Any] = {
            "gate": "fail",
            "violations": [{
                "source": "parse",
                "check": "json_decode",
                "severity": "CAT1",
                "detail": str(exc),
                "affected": [],
            }],
            "delta": {},
        }
        print(json.dumps(out, indent=2))
        sys.exit(1)

    result = run_gate(plan_json)
    print(json.dumps(result, indent=2))

    if args.gate and result["gate"] == "fail":
        sys.exit(1)


if __name__ == "__main__":
    main()
