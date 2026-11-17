# CUI // SP-CTI
"""IV&V for survivor assurance: ICDEV's own IV&V over an application's modernisation outputs (icx-mdz-06).

ICDEV[OS] endorses a portfolio change only when every application that survives it (the one the others consolidate
into) carries an IV&V that counts (``portfolio.survivor_assured``). The exporter carried none, so retiring Cycle Store into
JPetStore was refused: "IV&V unreported". This module runs ICDEV's IV&V on the survivor and records the result; it does
not write a second IV&V:

* **The run** (``run``): ``tools/compliance/ivv_assessor.py``'s requirements, automated checks, findings and scores
  (``ivv_assessor.evaluate``, IEEE 1012) over the application's modernisation outputs -- the directory its CAM refactor
  and cloud-native jobs wrote (``<CAM_ARTIFACTS_DIR>/mcp-<app>``), or a directory the operator names (``--target-dir``).
  No outputs: nothing can be checked automatically, and every area is unverified. Writes nothing but its result.
* **The result**, per process area: ``pass`` only when every requirement there passed an automated check;
  ``fail`` when one failed (or only partly passed); otherwise ``unverified`` -- a requirement not assessed, deferred,
  manual, or semi-automated (the tool itself says a person must still review it). **Unverified is never pass.** Each
  requirement's evidence is bound by SHA-256, the outputs by the digest of their file list, and the whole result by
  ``digest``. It names the tool and its version, the date, and (``subject``) the inventory digest of the application's
  bundle when it ran. The recommendation is DENY with a critical finding, CERTIFY only when every area passed, else
  CONDITIONAL. It is recorded in the append-only audit trail (``compliance_check``, action
  ``mdz.survivor_ivv:<app>``); a newer run is a new result.
* **Independence is a person** (``decl-wft-mdz-survivor-ivv-signoff``): someone in a holder role signs or refuses the
  result, with a reason, on the Modernisation page, bound to its digest and taken once. They must be separate from
  whoever built or approved the application's plan (``builders``): anyone who recorded its 7R strategy, reviewed a CAM
  job's output, reviewed its team capacity or a process, or (as ICDEV[OS] names them in a may-plan it allowed) accepted,
  endorsed or approved its plan. A newer result leaves the sign-off ``stale``.
* **The bundle** (``for_bundle``): only a result signed at its current digest is carried, in ICDEV[OS]'s own ``ivv``
  section (``kind: independent``, the signer as ``assessor``; areas with a score are only those measured, pass or fail).
  An unsigned, stale or refused result is not carried: ``sections["ivv"]`` is missing and says why, so ICDEV[OS] reads it
  as unverified and its survivor gate does not pass. Since ICDEV[OS] 1.190.0 (osx-mdz-09) the section also carries each
  area's status, the findings, the evidence digests, the tool version and the sign-off itself in their own fields;
  ``sections["ivv"].reason`` still says them in words.

The real IV&V sign-off is a person's; this module never signs. Library and CLI:

    python tools/modernization/survivor_ivv.py --app-id <legacy app id> [--target-dir <dir>]   # run and record
    python tools/modernization/survivor_ivv.py --app-id <legacy app id> --show                 # the latest, signed or not
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root

_BASE = repo_root(__file__)

SCHEMA = "icdev-it/survivor-ivv/v1"
TOOL = "icdev-it/ivv_assessor"
TEMPLATE = "decl-wft-mdz-survivor-ivv-signoff"
SURFACE = "migration_canvas.api_modernisation_ivv_signoff"
ACTION = "mdz.survivor_ivv"
#: How long a signed result is offered as current (the bundle's ``expires_at``). Policy, not a standard: IEEE 1012 sets
#: none, and ICDEV[OS] also counts a carried result older than its own ``max_age_d`` for nothing.
VALID_DAYS = 365
#: The requirement statuses that are a measurement (``ivv_assessor``'s): anything else in an area leaves it unverified.
FAILED = ("fail", "partial")
_ASSESSOR = ("tools/compliance/ivv_assessor.py", "context/compliance/ivv_requirements.json")
_MANIFEST_MAX = 20000


class IvvError(ValueError):
    pass


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _sha(x: Any) -> str:
    return hashlib.sha256((x if isinstance(x, str) else _canon(x)).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject(app_id: str) -> str:
    """The sign-off's subject in ``wf_decision_steps``."""
    return f"ivv:{app_id}"


def tool_version() -> str:
    """The IV&V tool's version: its requirements catalogue's version and the SHA-256 of the assessor and catalogue."""
    h = hashlib.sha256()
    for rel in _ASSESSOR:
        h.update((_BASE / rel).read_bytes())
    meta = json.loads((_BASE / _ASSESSOR[1]).read_text(encoding="utf-8")).get("metadata") or {}
    return f"{meta.get('version') or 'unversioned'}+sha256.{h.hexdigest()[:12]}"


def application(app_id: str, conn) -> dict:
    row = conn.execute("SELECT id, project_id, name FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
    if row is None:
        raise IvvError(f"no legacy application {app_id!r}")
    return {"id": str(row[0]), "project_id": str(row[1] or ""), "name": str(row[2] or "")}


def outputs(app_id: str) -> Path | None:
    """Where the application's CAM refactor and cloud-native jobs wrote: the common parent of their outputs, or None."""
    from tools.modernization import cloud_native_jobs, refactor_jobs

    dirs = [Path(str(j["output_path"])) for rep in (refactor_jobs.report(app_id), cloud_native_jobs.report(app_id))
            for j in rep["jobs"] if j.get("status") == "completed" and j.get("output_path")]
    dirs = [d for d in dirs if d.is_dir()]
    if not dirs:
        return None
    return Path(os.path.commonpath([str(d.resolve()) for d in dirs]))


def manifest(root: Path) -> dict:
    """The files under ``root`` by relative path and SHA-256, and the digest of that list (what the run assessed)."""
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append((p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()))
            if len(files) > _MANIFEST_MAX:
                raise IvvError(f"{root} holds more than {_MANIFEST_MAX} files: name the modernised code itself")
    return {"files": len(files), "digest": _sha("\n".join(f"{n}\0{h}" for n, h in files))}


def _target(app_id: str, target_dir: str | None) -> dict:
    if target_dir:
        root = Path(target_dir).resolve()
        if not root.is_dir():
            raise IvvError(f"the target {target_dir} is not a directory")
        basis = "declared"
    else:
        root = outputs(app_id)
        if root is None:
            return {"path": None, "basis": "none", "files": 0, "digest": None,
                    "why": "no CAM refactor or cloud-native job of this application has completed: there are no "
                           "modernisation outputs to check, so nothing was checked automatically"}
        basis = "cam_outputs"
    return {"path": str(root), "basis": basis, **manifest(root)}


def _area(code: str, name: str, rows: list[dict], score: float, findings: list[dict]) -> dict:
    """One process area: ``pass`` only when every requirement there passed an automated check."""
    failed = [r["id"] for r in rows if r["status"] in FAILED]
    open_ = [r["id"] for r in rows if r["status"] not in FAILED + ("pass", "not_applicable")
             or (r["status"] == "pass" and r["automation"] != "auto")]
    passed = [r["id"] for r in rows if r["status"] == "pass" and r["automation"] == "auto"]
    if failed:
        status, why = "fail", f"{', '.join(failed)} not met"
    elif open_:
        status, why = "unverified", (f"{', '.join(open_)} not verified automatically (not assessed, manual, or "
                                     "semi-automated: a person must still review it)")
    elif passed:
        status, why = "pass", f"{', '.join(passed)} met"
    else:
        status, why = "unverified", "no requirement here applies, so nothing was verified"
    return {"code": code, "name": name, "status": status, "why": why,
            "score": score if status in ("pass", "fail") else None,
            "requirements": rows, "findings": sorted(f["finding_id"] for f in findings if f["process_area"] == name)}


def assess(app_id: str, conn, *, target_dir: str | None = None, at: datetime | None = None,
           inventory_digest: str | None = None) -> dict:
    """The survivor IV&V result for one application (not recorded). ``ivv_assessor.evaluate`` does the checking."""
    from tools.compliance import ivv_assessor as ivv

    app = application(app_id, conn)
    at = at or _now()
    target = _target(app_id, target_dir)
    ev = ivv.evaluate(app["project_id"] or app_id, target["path"], now=at)
    by_area: dict[str, list[dict]] = {}
    for r in ev["results"]:
        by_area.setdefault(r["process_area"], []).append({
            "id": r["requirement_id"], "title": r["title"], "priority": r["priority"],
            "automation": r["automation_level"], "status": r["status"],
            "evidence_digest": _sha({k: r[k] for k in ("requirement_id", "status", "evidence", "details")})})
    scores = ev["scores"]["area_scores"]
    areas = [_area(ivv.PROCESS_AREA_CODES[name], name, by_area[name], scores.get(ivv.PROCESS_AREA_CODES[name], 0.0),
                   ev["findings"])
             for name in ivv.PROCESS_AREAS if by_area.get(name)]
    critical = int(ev["gate_result"]["critical_findings_count"])
    recommendation = ("DENY" if critical else "CERTIFY" if all(a["status"] == "pass" for a in areas)
                      else "CONDITIONAL")
    result = {
        "schema": SCHEMA, "app_id": app_id, "application": app["name"],
        "tool": TOOL, "tool_version": tool_version(), "standard": "IEEE 1012-2016",
        "assessed_at": _iso(at), "expires_at": _iso(at + timedelta(days=VALID_DAYS)),
        "target": target,
        "subject": {"inventory_digest": inventory_digest} if inventory_digest else None,
        "areas": areas,
        "findings": [{k: f[k] for k in ("finding_id", "severity", "process_area", "title", "recommendation")}
                     for f in ev["findings"]],
        "critical_findings": critical,
        "overall_score": ev["scores"]["overall_score"],
        "recommendation": recommendation,
        "conditions": [f"{a['code']} {a['status']}: {a['why']}" for a in areas if a["status"] != "pass"],
    }
    result["digest"] = digest(result)
    return result


def digest(result: dict) -> str:
    """SHA-256 over the canonical JSON of the result, without its own digest and audit id."""
    return _sha({k: v for k, v in result.items() if k not in ("digest", "audit_id")})


def _inventory_digest(app_id: str) -> tuple[str | None, str | None]:
    try:
        from tools.migration_canvas.modernisation import bundle_preview

        pre = bundle_preview(app_id)
        return pre.get("inventory_digest"), None if pre.get("inventory_digest") else pre.get("error")
    except Exception as exc:  # noqa: BLE001 -- unbound is said: ICDEV[OS] then cannot tell a change after it
        return None, str(exc)


def run(app_id: str, *, target_dir: str | None = None) -> dict:
    """Run the IV&V and record the result. Raises when it cannot be recorded: an unrecorded result is not one."""
    from tools.audit.audit_logger import log_event
    from tools.db.storage import get_connection

    inv, why = _inventory_digest(app_id)
    conn = get_connection()
    try:
        result = assess(app_id, conn, target_dir=target_dir, inventory_digest=inv)
    finally:
        conn.close()
    if inv is None:
        result["subject_why"] = f"the bundle's inventory digest could not be computed: {why}"
        result["digest"] = digest(result)
    entry = log_event("compliance_check", actor=TOOL, action=f"{ACTION}:{app_id}", details=result,
                      raise_on_error=True)
    result["audit_id"] = entry if isinstance(entry, int) and entry > 0 else None
    return result


def latest(app_id: str, conn: Any = None) -> dict | None:
    """The application's latest recorded result (its audit id added), or None. Raises when the trail cannot be read."""
    from tools.db.storage import get_canvas_connection

    own = conn is None
    conn = conn or get_canvas_connection()
    try:
        row = conn.execute("SELECT id, details FROM audit_trail WHERE event_type = 'compliance_check' AND action = %s "
                           "ORDER BY id DESC LIMIT 1", (f"{ACTION}:{app_id}",)).fetchone()
    finally:
        if own:
            conn.close()
    if row is None:
        return None
    rid, details = tuple(row)
    result = json.loads(details or "{}")
    if result.get("digest") != digest(result):
        raise IvvError(f"the recorded IV&V result (audit {rid}) does not match its own digest")
    return {**result, "audit_id": int(rid)}


_BUILT = {
    "decl-wft-mdz-7r-strategy": "recorded its 7R strategy",
    "decl-wft-mdz-cam-refactor-approve": "reviewed a CAM job's output for it",
    "decl-wft-mdz-capacity-review": "reviewed its team capacity",
    "decl-wft-mdz-process-review": "reviewed one of its processes",
}


def builders(app_id: str, conn: Any = None) -> dict[str, str]:
    """``{person id: why}``: who built or approved the application's plan, so may not sign its IV&V off.

    Everyone who took a decided step on it (``_BUILT``), and whoever ICDEV[OS] named, in a may-plan it allowed and ICDEV
    recorded (``plan_gate``), as accepting, endorsing or approving its plan (compared as ICDEV[OS] names them). Raises
    when the ledger cannot be read: independence that cannot be checked is not assumed."""
    from tools.db.storage import get_canvas_connection
    from tools.modernization import plan_gate

    own = conn is None
    conn = conn or get_canvas_connection()
    try:
        rows = conn.execute(
            "SELECT template_id, actor, payload_json FROM wf_decision_steps WHERE event = 'decided' AND template_id IN "
            "(%s, %s, %s, %s) ORDER BY created_at, event_id", tuple(_BUILT)).fetchall()
        allowed = conn.execute("SELECT details FROM audit_trail WHERE event_type = 'approval_granted' AND action = %s "
                               "ORDER BY id", (f"{plan_gate.ALLOWED}:{app_id}",)).fetchall()
    finally:
        if own:
            conn.close()
    out: dict[str, str] = {}
    for tid, actor, payload in (tuple(r) for r in rows):
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            p = {}
        mine = (p.get("legacy_app_id") == app_id if tid == "decl-wft-mdz-7r-strategy" else
                p.get("project_id") == f"mcp-{app_id}" if tid == "decl-wft-mdz-cam-refactor-approve" else
                p.get("app_id") == app_id)
        if mine and actor:
            out.setdefault(str(actor), _BUILT[tid])
    for (details,) in (tuple(r) for r in allowed):
        try:
            d = json.loads(details or "{}")
        except ValueError:
            continue
        for k, why in (("accepted_by", "accepted its strategy in ICDEV[OS]"),
                       ("endorsed_by", "endorsed the change that plans it in ICDEV[OS]"),
                       ("approved_by", "approved the change that plans it in ICDEV[OS]")):
            for who in (d.get(k) if isinstance(d.get(k), list) else [d.get(k)]):
                if who:
                    out.setdefault(str(who), why)
    return out


def signoff(app_id: str, result: dict | None, conn: Any = None) -> dict:
    """The person's sign-off of ``result``: ``signed``/``refused`` at its digest, ``stale`` (of an earlier result) or
    ``unsigned``."""
    from tools.modernization import decisions

    row = decisions.decided(TEMPLATE, [subject(app_id)], conn=conn).get(subject(app_id))
    if row is None or result is None:
        return {"state": "unsigned"}
    p = row.get("payload") or {}
    out = {"step_id": row["step_id"], "decision": row["decision"], "by": row["actor"], "role": row["actor_role"],
           "reason": row["reason"], "at": row["created_at"], "result_digest": p.get("result_digest")}
    if p.get("result_digest") != result["digest"]:
        return {**out, "state": "stale"}
    return {**out, "state": "signed" if row["affirmative"] else "refused"}


def payload(result: dict) -> dict:
    """What a sign-off binds: the result's digest and what it says."""
    return {"app_id": result["app_id"], "result_digest": result["digest"], "assessed_at": result["assessed_at"],
            "tool_version": result["tool_version"], "recommendation": result["recommendation"],
            "areas": {a["code"]: a["status"] for a in result["areas"]}}


def _areas(result: dict) -> str:
    return ", ".join(f"{a['code']} {a['status']}" for a in result["areas"])


def _utc(at: Any) -> str:
    """A ledger timestamp (ISO text, or a datetime from PostgreSQL) as an aware UTC ISO 8601 string."""
    dt = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    return _iso(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _contract_fields(result: dict, so: dict) -> dict:
    """What ICDEV[OS] 1.190.0 carries beside the scores: ``area_status`` (every area, ``unverified`` never a pass), the
    ``findings`` (by area code), the ``evidence`` digests, the ``tool_version`` and the person's ``signoff`` over the
    result's digest. Only for a result signed at its current digest (``for_bundle``)."""
    code = {a["name"]: a["code"] for a in result["areas"]}
    target = result.get("target") or {}
    return {"area_status": {a["code"]: {"status": a["status"], "name": a["name"], "why": a["why"]}
                            for a in result["areas"]},
            "findings": [{"id": f["finding_id"], "severity": f["severity"],
                          "area": code.get(f["process_area"], f["process_area"]), "title": f["title"]}
                         for f in result.get("findings") or []],
            "evidence": {"result_digest": result["digest"],
                         **({"outputs_digest": target["digest"], "outputs_files": int(target.get("files") or 0)}
                            if target.get("digest") else {}),
                         "requirements": {q["id"]: q["evidence_digest"] for a in result["areas"]
                                          for q in a["requirements"]}},
            "tool_version": result["tool_version"],
            "signoff": {"signed_by": so["by"], "step_id": so["step_id"], "decided_at": _utc(so["at"]),
                        "result_digest": so["result_digest"], **({"role": so["role"]} if so.get("role") else {})}}


def for_bundle(app_id: str, conn: Any = None) -> dict:
    """``{section, reason}`` for the bundle: the ``ivv`` section (contract 1.189.0) only when an independent person
    signed the current result at its digest; else None and why."""
    result = latest(app_id, conn)
    if result is None:
        return {"section": None, "reason": "no survivor IV&V has run: python tools/modernization/survivor_ivv.py "
                                           f"--app-id {app_id}, then an independent person signs it off ({TEMPLATE})"}
    so = signoff(app_id, result, conn)
    head = (f"IV&V result sha256 {result['digest'][:12]} of {result['assessed_at']} ({result['recommendation']}; "
            f"{_areas(result)}; tool {result['tool_version']})")
    if so["state"] == "unsigned":
        return {"section": None, "reason": f"unsigned: {head} has no independent sign-off ({TEMPLATE}): not assured"}
    if so["state"] == "stale":
        return {"section": None, "reason": f"unsigned: {so['by']} signed off result {str(so['result_digest'])[:12]}, "
                                           f"not the current {head}: not assured"}
    if so["state"] == "refused":
        return {"section": None, "reason": f"refused by {so['by']} ({so['reason']}): {head}: not assured"}
    section = {"assessed_at": result["assessed_at"], "tool": f"{TOOL}@{result['tool_version']}",
               "recommendation": result["recommendation"], "overall_score": float(result["overall_score"]),
               "critical_findings": int(result["critical_findings"]),
               "areas": {a["code"]: float(a["score"]) for a in result["areas"] if a["score"] is not None},
               "kind": "independent", "assessor": so["by"], "expires_at": result["expires_at"],
               # osx-mdz-09 (ICDEV[OS] 1.190.0): each area's status, the findings, the evidence, the tool and the
               # sign-off, in the contract's own fields, so ICDEV[OS]'s survivor gate reads them rather than words
               **_contract_fields(result, so)}
    if result.get("subject"):
        section["subject"] = result["subject"]
    unv = [a["code"] for a in result["areas"] if a["status"] == "unverified"]
    return {"section": section, "reason": f"signed by {so['by']} (step {so['step_id']}): {head}"
                                          + (f"; unverified, never passed: {', '.join(unv)}" if unv else "")}


def report(app_id: str, conn: Any = None) -> dict:
    """The page's and the MCP tool's view: the latest result, its sign-off, who may not sign it, and what the bundle
    carries. Writes nothing."""
    result = latest(app_id, conn)
    return {"app_id": app_id, "result": result, "signoff": signoff(app_id, result, conn),
            "separate_from": builders(app_id, conn), "template": TEMPLATE, "bundle": for_bundle(app_id, conn),
            "run_with": f"python tools/modernization/survivor_ivv.py --app-id {app_id} [--target-dir <dir>]"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run ICDEV's IV&V on a consolidation survivor's modernisation outputs")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--target-dir", help="the modernised code to check (default: its CAM jobs' outputs)")
    ap.add_argument("--show", action="store_true", help="show the latest result and its sign-off; run nothing")
    a = ap.parse_args(argv)
    try:
        out = report(a.app_id) if a.show else run(a.app_id, target_dir=a.target_dir)
    except IvvError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
