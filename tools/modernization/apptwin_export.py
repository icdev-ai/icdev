# CUI // SP-CTI
"""Export one legacy application as an ICDEV[OS] ``icos-apptwin/v1`` bundle (icx-bun-01).

ICDEV[OS] twins every legacy application it is asked to consolidate from one bundle per application; it carries
what is in the bundle and recomputes nothing. This module only ASSEMBLES what ICDEV[IT]'s own tools already
produced, so every section is traceable to the tool that made it:

    inventory     components, apis, data, dependencies   the legacy_* tables (tools/modernization/legacy_analyzer.py)
    --features    features (basis observed)              tools/aiify/observed_inventory.py output (its ``features``)
    --process     processes (basis document | observed)  python -m tools.workflow_canvas.processify --json
    --aiify       aiify                                  python -m tools.aiify.engine scan --json
    --integrity   integrity                              python tools/integrity/engine.py --json (SIPA)
    --seven-r     seven_r                                python tools/modernization/seven_r_assessor.py --json
    --ivv/--sbd/--rtm/--coherence/--readiness            already in the bundle's section shape

A section with no input stays ``null``: ICDEV[OS] reads null as UNVERIFIED, never as a pass, so nothing here
fills a gap with a default. Nothing is invented either: no feature is guessed from code, and ``consumers`` (which
needs every other application) is left to ICDEV[OS]. The bundle is validated against the vendored contract
(``context/icos/apptwin.schema.json``, pinned by SHA-256) before it is written.

    python tools/modernization/apptwin_export.py --app-id <legacy app id> [--aiify scan.json] [--process p.json ...]
        [--features inv.json] [--integrity sipa.json] [--seven-r 7r.json] [--at 2026-11-07T00:00:00Z] --out app.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from icdev.core.paths import repo_root

_BASE = repo_root(__file__)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

SCHEMA = "icos-apptwin/v1"
SCHEMA_PATH = _BASE / "context" / "icos" / "apptwin.schema.json"
#: The digest of the vendored ICDEV[OS] contract (1.111.0). A changed file is refused, never silently used.
SCHEMA_SHA256 = "9f565b1486176fdc11e0ba3704a9af6092d0a62c92e6f71ad0bf53a79ff07f9e"
PRODUCER = "icdev-it/apptwin_export"
PASSTHROUGH = ("ivv", "sbd", "rtm", "coherence", "readiness")


class BundleError(ValueError):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _iso(ts) -> str | None:
    if ts in (None, ""):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(ts).replace(" ", "T")
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    return s


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("icdev")
    except Exception:  # noqa: BLE001 -- an unpackaged checkout still says what it is
        return "unpackaged"


def schema() -> dict:
    raw = SCHEMA_PATH.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise BundleError(f"{SCHEMA_PATH} is not the pinned ICDEV[OS] contract (sha256 {got}, pinned {SCHEMA_SHA256})")
    return json.loads(raw)


def validate(bundle: dict) -> list[str]:
    import jsonschema
    v = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(bundle))


# --- inventory: the legacy_* tables ---------------------------------------------------------------

def inventory(app_id: str, conn) -> dict:
    """The application row and its components, APIs, tables and dependencies, in a stable order."""
    app = conn.execute("SELECT id, name, primary_language, framework FROM legacy_applications WHERE id = %s",
                       (app_id,)).fetchone()
    if app is None:
        raise BundleError(f"no legacy application {app_id!r} (register it with legacy_analyzer first)")
    comps = conn.execute("SELECT id, name, qualified_name, component_type, file_path FROM legacy_components "
                         "WHERE legacy_app_id = %s", (app_id,)).fetchall()
    name = {c["id"]: c["qualified_name"] or c["name"] for c in comps}
    apis = conn.execute("SELECT method, path, handler_function FROM legacy_apis WHERE legacy_app_id = %s",
                        (app_id,)).fetchall()
    cols = conn.execute("SELECT table_name, column_name FROM legacy_db_schemas WHERE legacy_app_id = %s",
                        (app_id,)).fetchall()
    deps = conn.execute("SELECT source_component_id, target_component_id, dependency_type FROM legacy_dependencies "
                        "WHERE legacy_app_id = %s", (app_id,)).fetchall()
    tables: dict[str, list[str]] = {}
    for r in cols:
        tables.setdefault(r["table_name"], []).append(r["column_name"])
    return {
        "application": {"id": app["id"], "title": app["name"],
                        "languages": sorted({x for x in (app["primary_language"],) if x})},
        "components": sorted(({"id": name[c["id"]], "kind": c["component_type"], "path": c["file_path"]} for c in comps),
                             key=lambda x: (x["id"], x["kind"])),
        "apis": sorted(({"method": a["method"] or "ALL", "path": a["path"],
                         **({"handler": a["handler_function"]} if a["handler_function"] else {})} for a in apis),
                       key=lambda x: (x["path"], x["method"])),
        "data": [{"name": t, "columns": sorted(set(c))} for t, c in sorted(tables.items())],
        "dependencies": sorted(({"source": name.get(d["source_component_id"], d["source_component_id"]),
                                 "target": name.get(d["target_component_id"], d["target_component_id"]),
                                 "kind": d["dependency_type"]} for d in deps if d["target_component_id"]),
                               key=lambda x: (x["source"], x["target"], x["kind"])),
    }


# --- sections from each tool's own JSON ------------------------------------------------------------

def features_from_inventory(inv: dict) -> list[dict] | None:
    """Observed features (observed_inventory.build_inventory output). ``features: null`` there is null here."""
    feats = inv.get("features")
    if feats is None:
        return None
    out = {}
    for f in feats:
        label = f.get("label") or f.get("title")
        if not label:
            continue
        fid = _slug(label)                         # one feature per label: a repeat adds its evidence
        f0 = out.setdefault(fid, {"id": fid, "title": label, "basis": "observed", "evidence": []})
        f0["evidence"] = sorted(set(f0["evidence"]) | {str(e) for e in f.get("evidence_ids") or []})
    return [out[k] for k in sorted(out)]


def process_from_processify(doc: dict) -> dict | None:
    """A Process-Ify draft (CLI output or its ``workflow``). A draft with no measured steps is no process."""
    wf = doc.get("workflow", doc)
    steps = wf.get("steps")
    if not steps:
        return None
    basis = wf.get("basis") if wf.get("basis") in ("document", "observed") else "document"
    return {"id": _slug(wf.get("workflow_name") or wf.get("source_name") or "process"),
            "title": wf.get("workflow_name") or wf.get("source_name") or "process", "basis": basis,
            "steps": [{"id": str(s.get("id") or f"step-{i + 1}"), "title": s.get("title") or f"step {i + 1}",
                       **({"role": s["assignee_role"]} if s.get("assignee_role") else {})} for i, s in enumerate(steps)]}


def aiify_from_scan(scan: dict, *, at: str) -> dict:
    """``python -m tools.aiify.engine scan --json`` output. AI-Ify's posture grade is database-wide, not this
    application's, so it is never carried (null)."""
    pillars = {k: {"passed": int(v["passed"]), "total": int(v["total"]),
                   "percentage": round(float(v["percentage"]) * (100 if float(v["percentage"]) <= 1 else 1), 2)}
               for k, v in (scan.get("pillar_scores") or {}).items()} or None
    opps = [{"pattern_type": o["pattern_type"], "module_path": o["module_path"],
             "ai_readiness": (o.get("score") or {}).get("ai_readiness"),
             "composite_score": (o.get("score") or {}).get("composite_score")}
            for o in scan.get("opportunities") or []]
    opps = [o for o in opps if o["ai_readiness"] is not None and o["composite_score"] is not None]
    rs = scan.get("overall_readiness_score")
    return {"tool": "icdev-it/aiify", "assessed_at": _iso(scan.get("completed_at")) or at,
            "overall_verdict": scan["overall_verdict"], "posture_grade": None,
            "readiness_score": None if rs is None else float(rs), "pillars": pillars,
            "opportunities": sorted(opps, key=lambda o: (o["module_path"], o["pattern_type"]))}


def integrity_from_sipa(r: dict, *, at: str) -> dict:
    caps = r.get("capabilities") or []
    return {"tool": "icdev-it/sipa", "assessed_at": _iso(r.get("assessed_at")) or at,
            "verdict": str(r["verdict"]).upper(),
            "risk_score": None if r.get("risk_score") is None else float(r["risk_score"]),
            "capabilities": sorted({c if isinstance(c, str) else str(c.get("capability_type") or c.get("type"))
                                    for c in caps})}


def seven_r_from_assessment(a: dict, *, at: str) -> dict:
    ranking = (a.get("evidence") or {}).get("ranking") or a.get("ranking") or []
    if isinstance((a.get("evidence") or {}), str):
        ranking = json.loads(a["evidence"]).get("ranking") or []
    return {"tool": "icdev-it/seven_r_assessor", "assessed_at": _iso(a.get("assessed_at") or a.get("created_at")) or at,
            "ranked": [{"strategy": r.get("strategy_id") or r.get("strategy"), "score": float(r["score"])} for r in ranking]}


def build(app_id: str, conn, *, at: str | None = None, features: dict | None = None, processes: list[dict] = (),
          aiify: dict | None = None, integrity: dict | None = None, seven_r: dict | None = None,
          sections: dict | None = None) -> dict:
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inv = inventory(app_id, conn)
    procs = [p for p in (process_from_processify(d) for d in processes) if p is not None]
    bundle = {"schema": SCHEMA, "producer": {"tool": PRODUCER, "version": _version()}, "produced_at": at, **inv,
              "features": features_from_inventory(features) if features is not None else None,
              "processes": sorted(procs, key=lambda p: p["id"]) or None,   # no measured steps: unverified, never "none"
              "consumers": None,
              "aiify": aiify_from_scan(aiify, at=at) if aiify is not None else None,
              "integrity": integrity_from_sipa(integrity, at=at) if integrity is not None else None,
              "seven_r": seven_r_from_assessment(seven_r, at=at) if seven_r is not None else None}
    for k in PASSTHROUGH:
        bundle[k] = (sections or {}).get(k)
    errors = validate(bundle)
    if errors:
        raise BundleError("the bundle does not meet the icos-apptwin/v1 contract: " + "; ".join(errors[:10]))
    return bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a legacy application as an ICDEV[OS] icos-apptwin/v1 bundle")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--features", type=Path, help="observed_inventory output (JSON)")
    ap.add_argument("--process", type=Path, action="append", default=[], help="a Process-Ify draft (JSON); repeatable")
    ap.add_argument("--aiify", type=Path, help="python -m tools.aiify.engine scan --json output")
    ap.add_argument("--integrity", type=Path, help="SIPA assessment (JSON)")
    ap.add_argument("--seven-r", type=Path, help="7R assessment (JSON)")
    for k in PASSTHROUGH:
        ap.add_argument(f"--{k}", type=Path, help=f"the bundle's {k} section (JSON)")
    ap.add_argument("--at", help="produced_at (ISO 8601; default now), so a bundle can be re-made byte for byte")
    ap.add_argument("--out", type=Path, help="write the bundle here (default: stdout)")
    a = ap.parse_args(argv)

    def load(p):
        return None if p is None else json.loads(p.read_text(encoding="utf-8"))
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        b = build(a.app_id, conn, at=_iso(a.at) if a.at else None, features=load(a.features),
                  processes=[load(p) for p in a.process], aiify=load(a.aiify), integrity=load(a.integrity),
                  seven_r=load(a.seven_r), sections={k: load(getattr(a, k)) for k in PASSTHROUGH})
    except (BundleError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    finally:
        conn.close()
    text = json.dumps(b, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), "application": b["application"]["id"],
                          "unverified": sorted(k for k, v in b.items() if v is None)}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
