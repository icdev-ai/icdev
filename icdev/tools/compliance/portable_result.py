# CUI // SP-CTI
"""IV&V, Secure by Design and RTM as PORTABLE, dated results for an imported legacy application (icx-ivv-01).

ICDEV[OS] carries these into its application twin (the ``ivv``, ``sbd`` and ``rtm`` sections of an
``icos-apptwin/v1`` bundle; ``tools/modernization/apptwin_export.py`` passes them through) and its decision and
cutover gates read them, so each result says what it is:

    assessed_at   when it was run
    kind          ``automated`` -- every result this module makes. An IV&V of record is a person's
                  ``independent`` attestation, which no tool run can be, and nothing here claims it.
    expires_at    assessed_at + the validity this run was given (``--valid-days``, default 30). ICDEV[OS] reads an
                  expired result as no result.
    recommendation / score / coverage, from the SAME rules the reports use (``ivv_report_generator`` and
                  ``sbd_report_generator``), never re-derived differently here.

The three assessors need a row in ``projects``. ``register`` creates one for an imported application from its
bundle (``application.id`` / ``title``) and the directory its source was unpacked into, through the canonical
``project_create.create_project`` (no scaffold), so an application registered from a bundle is assessed like any
other.

    python tools/compliance/portable_result.py register --bundle app.json --project-dir /src/app --json
    python tools/compliance/portable_result.py ivv --project-id <id> --emit-json ivv.json [--valid-days 30]
    python tools/compliance/portable_result.py sbd --project-id <id> --emit-json sbd.json
    python tools/compliance/portable_result.py rtm --project-id <id> --emit-json rtm.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from icdev.core.paths import repo_root

_BASE = repo_root(__file__)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

DEFAULT_VALID_DAYS = 30


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ivv_section(result: dict, *, at: datetime, valid_days: int = DEFAULT_VALID_DAYS) -> dict:
    """The bundle's ``ivv`` section from ``ivv_assessor.run_ivv_assessment``'s result."""
    from tools.compliance.ivv_assessor import PROCESS_AREA_CODES
    from tools.compliance.ivv_report_generator import _determine_certification_recommendation
    scores = result["scores"]
    area = scores.get("area_scores") or {}
    # The report's rule reads {area NAME: {score, total}} and skips areas with no assessments; the assessor keeps
    # {area CODE: score} for the areas it assessed. Translate, or an area below 60 would never be seen.
    name_of = {code: name for name, code in PROCESS_AREA_CODES.items()}
    rec, _why = _determine_certification_recommendation(
        scores["overall_score"], {name_of.get(k, k): {"score": v, "total": 1} for k, v in area.items()},
        result.get("findings") or [])
    critical = sum(1 for f in result.get("findings") or [] if f.get("severity") == "critical")
    return {"tool": "icdev-it/ivv_assessor", "kind": "automated", "assessor": None, "assessed_at": _iso(at),
            "expires_at": _iso(at + timedelta(days=valid_days)), "recommendation": rec,
            "overall_score": float(scores["overall_score"]), "critical_findings": critical,
            "areas": {k: float(v) for k, v in sorted(area.items())}}


def sbd_section(result: dict, *, at: datetime) -> dict:
    """The bundle's ``sbd`` section from ``sbd_assessor.run_sbd_assessment``'s result (the report's own score)."""
    from tools.compliance.sbd_report_generator import _calculate_domain_scores, _calculate_overall_status
    overall, _label = _calculate_overall_status(_calculate_domain_scores(result.get("results") or []))
    critical = sum(1 for r in result.get("results") or [] if r.get("priority") == "critical"
                   and r.get("status") == "not_satisfied")
    return {"tool": "icdev-it/sbd_assessor", "assessed_at": _iso(at), "score": float(overall),
            "critical_not_satisfied": critical}


def rtm_section(result: dict, *, at: datetime) -> dict:
    """The bundle's ``rtm`` section from ``traceability_matrix.generate_rtm``'s result (and its rtm-data.json)."""
    reqs = []
    jf = result.get("json_file")
    if jf and Path(jf).exists():
        data = json.loads(Path(jf).read_text(encoding="utf-8"))
        tests_for: dict[str, list[str]] = {}
        for t in data.get("forward_trace") or []:          # _build_forward_trace: requirement_id, test_files
            rid = t.get("requirement_id")
            if rid:
                tests_for[rid] = sorted({str(x.get("file_path") or x.get("name") or x.get("id"))
                                         for x in t.get("test_files") or []})
        for r in data.get("requirements") or []:
            rid = r.get("id")
            if rid:
                reqs.append({"id": str(rid), "tests": tests_for.get(rid, [])})
    return {"tool": "icdev-it/traceability_matrix", "assessed_at": _iso(at),
            "coverage_pct": max(0.0, min(100.0, float(result.get("coverage") or 0.0))),
            "requirements": sorted(reqs, key=lambda r: r["id"])}


def register(bundle: dict, project_dir: str | Path) -> dict:
    """A ``projects`` row for an application imported from its bundle, via the canonical project creator."""
    from tools.project.project_create import create_project
    app = bundle["application"]
    out = create_project(name=f"apptwin-{app['id']}", project_type="webapp",
                         description=f"Imported legacy application {app['title']} (icos-apptwin bundle)",
                         tech_backend=",".join(app.get("languages") or []), skip_scaffold=True)
    from tools.db.storage import get_connection
    conn = get_connection()
    try:                                         # the assessors read the SOURCE, wherever it was unpacked
        conn.execute("UPDATE projects SET directory_path = %s WHERE id = %s", (str(project_dir), out["project_id"]))
        conn.commit()
    finally:
        conn.close()
    return {"project_id": out["project_id"], "application": app["id"], "directory": str(project_dir)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IV&V / SbD / RTM as portable, dated bundle sections (JSON out)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="a projects row for an application imported from its bundle")
    r.add_argument("--bundle", type=Path, required=True)
    r.add_argument("--project-dir", required=True)
    for name in ("ivv", "sbd", "rtm"):
        p = sub.add_parser(name)
        p.add_argument("--project-id", required=True)
        p.add_argument("--project-dir", default=None)
        p.add_argument("--emit-json", type=Path, required=True, help="write the bundle section here")
        if name == "ivv":
            p.add_argument("--valid-days", type=int, default=DEFAULT_VALID_DAYS)
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", help="JSON out (the default; kept for CLI parity)")
    a = ap.parse_args(argv)
    at = datetime.now(timezone.utc)
    try:
        if a.cmd == "register":
            out = register(json.loads(a.bundle.read_text(encoding="utf-8")), a.project_dir)
            print(json.dumps({"status": "ok", **out}))
            return 0
        if a.cmd == "ivv":
            from tools.compliance.ivv_assessor import run_ivv_assessment
            sec = ivv_section(run_ivv_assessment(a.project_id, project_dir=a.project_dir), at=at, valid_days=a.valid_days)
        elif a.cmd == "sbd":
            from tools.compliance.sbd_assessor import run_sbd_assessment
            sec = sbd_section(run_sbd_assessment(a.project_id, project_dir=a.project_dir), at=at)
        else:
            from tools.compliance.traceability_matrix import generate_rtm
            sec = rtm_section(generate_rtm(a.project_id, project_dir=a.project_dir), at=at)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    a.emit_json.write_text(json.dumps(sec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "section": a.cmd, "out": str(a.emit_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
