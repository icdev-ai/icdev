# CUI // SP-CTI
"""Export one legacy application as an ICDEV[OS] ``icos-apptwin/v1`` bundle (icx-bun-01).

ICDEV[OS] twins every legacy application it is asked to consolidate from one bundle per application; it carries
what is in the bundle and recomputes nothing. This module only ASSEMBLES what ICDEV[IT]'s own tools already
produced, so every section is traceable to the tool that made it:

    inventory     components, apis, data, dependencies   the legacy_* tables (tools/modernization/legacy_analyzer.py)
    --features    features (basis observed)              tools/aiify/observed_inventory.py output (its ``features``)
    --static-features  features (provenance static)      tools/modernization/static_features.py (entry points)
    --process     processes (basis document | observed)  python -m tools.workflow_canvas.processify --json
    --aiify       aiify                                  python -m tools.aiify.engine scan --json
    --integrity   integrity                              python tools/integrity/engine.py --json (SIPA)
    --seven-r     seven_r                                python tools/modernization/seven_r_assessor.py --json
    (database)    migration                              the latest migration_plans row and its migration_tasks
    --waves       migration.waves                        tools/modernization/app_wave_planner.py (the application's own
                                                         waves, osx-fth-13), or the migration canvas's (JSON list)
    --strangler   migration.strangler                    tools/modernization/strangler_fig_manager.py status (JSON)
    --refactor    refactor                               tools/modernization/refactor_jobs.py (CAM's jobs), or
                                                         cam_refactor_engine.py --list-jobs rows (JSON list)
    --ivv/--sbd/--rtm/--coherence/--readiness            already in the bundle's section shape

A verification result (``--ivv``/``--sbd``/``--rtm``/``--coherence``) is passed through as it was made, with the
``subject`` its tool recorded (``tools/compliance/portable_result.py --bundle``, ``tools/workflow/app_coherence.py
--bundle``): this module never stamps a subject on one, because only the run that assessed the code knows what it saw.

A section with no input stays ``null``: ICDEV[OS] reads null as UNVERIFIED, never as a pass, so nothing here
fills a gap with a default. Nothing is invented either: a static feature is only what static_features grouped
from the entry points the analyser found, labelled ``static`` and never ``observed``, and ``consumers`` (which
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

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

_BASE = repo_root(__file__)

SCHEMA = "icos-apptwin/v1"
SCHEMA_PATH = _BASE / "context" / "icos" / "apptwin.schema.json"
#: The digest of the vendored ICDEV[OS] contract (1.181.0: a refactor job's artefacts, each by SHA-256, its summary and
#: review, ICDEV[OS] osx-fth-13). A changed file is refused, never silently used.
SCHEMA_SHA256 = "fa6d7e6347d0c77b1679596718d0b848cb89426ec17f5e4f9197643a3669f959"
PRODUCER = "icdev-it/apptwin_export"
PASSTHROUGH = ("ivv", "sbd", "rtm", "coherence", "readiness")


#: The inventory sections ICDEV[OS]'s ``inventory_digest`` covers (its icdev_os/twin/apptwin.py INVENTORY), in order.
INVENTORY = ("components", "apis", "data", "dependencies", "consumers", "features", "processes")


class BundleError(ValueError):
    pass


def _pruned(x):
    """Null, [] and {} dropped at every depth: a field left out and a field given empty are the same."""
    if isinstance(x, dict):
        out = {k: _pruned(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {})}
    if isinstance(x, list):
        return [_pruned(v) for v in x]
    return x


def inventory_digest(bundle: dict) -> str:
    """ICDEV[OS]'s inventory digest of ``bundle`` (icos-apptwin/v1, contract 1.120.0): SHA-256 over the canonical JSON of
    ``{section: [item, ...] | null}`` for each INVENTORY section, each item pruned (``_pruned``), each list of items
    sorted by its canonical JSON, keys sorted, no whitespace. ICDEV[OS] recomputes it from the bundle it imports; a
    verification result whose ``subject`` names another digest was taken before a significant change, and counts for
    nothing there."""
    def canon(x) -> str:
        return json.dumps(x, sort_keys=True, separators=(",", ":"))
    doc = {s: None if bundle.get(s) is None else sorted((_pruned(i) for i in bundle[s]), key=canon) for s in INVENTORY}
    return hashlib.sha256(canon(doc).encode()).hexdigest()


def subject(bundle: dict) -> dict:
    """What a verification result run on the code ``bundle`` describes assessed: its version (if any) and digest."""
    v = (bundle.get("application") or {}).get("version")
    return {**({"version": v} if v else {}), "inventory_digest": inventory_digest(bundle)}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _iso(ts) -> str | None:
    if ts in (None, ""):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(ts).replace(" ", "T")
    s = re.sub(r"([+-]\d\d:?\d\d)Z$", r"\1", s)  # "+00:00Z" (an offset AND a Z) is not a timestamp
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

def _source_root(app_id: str, conn) -> str | None:
    row = conn.execute("SELECT source_path FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
    return row["source_path"] if row else None


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


def features_from_static(doc: dict) -> list[dict] | None:
    """Static features (static_features.report output), carried as that tool grouped them: basis ``source``,
    provenance ``static``, its confidence. ``features: null`` there (no entry point found) is null here."""
    feats = doc.get("features")
    if feats is None:
        return None
    out = []
    for f in feats:
        if f.get("provenance") != "static" or f.get("basis") not in ("source", "decompiled"):
            raise BundleError(f"static feature {f.get('id')!r} is not labelled static from source")
        out.append({k: f[k] for k in ("id", "title", "basis", "provenance", "confidence", "signatures", "evidence")
                    if f.get(k) not in (None, [], {})})
    return sorted(out, key=lambda f: f["id"])


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


def _rel(path: str, root: str | None) -> str:
    """A module path relative to the application's source root: a bundle never carries where it was analysed."""
    if root:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return path


def _disqualifiers(o: dict) -> list[str]:
    d = o.get("disqualifiers")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = [d] if d.strip() else []
    return [str(x) for x in d or []]


VERDICTS = ("ai_ready", "partial", "not_suitable")


def _verdict(scan: dict) -> str:
    """AI-Ify's verdict as the contract's enum. A live scan puts the enum in ``overall_ai_readiness`` and a sentence
    ("Partially AI-ready") in ``overall_verdict``; older outputs had the enum in ``overall_verdict``."""
    for k in ("overall_ai_readiness", "overall_verdict"):
        if scan.get(k) in VERDICTS:
            return scan[k]
    raise BundleError(f"AI-Ify reported no verdict in {VERDICTS}: {scan.get('overall_verdict')!r}")


def aiify_from_scan(scan: dict, *, at: str, root: str | None = None) -> dict:
    """``python -m tools.aiify.engine scan --json`` output. AI-Ify's posture grade is database-wide, not this
    application's, so it is never carried (null). Module paths are relative to ``root`` (the application's source
    path), and AI-Ify's disqualifiers are carried: ICDEV[OS] refuses to enable a disqualified opportunity."""
    pillars = {k: {"passed": int(v["passed"]), "total": int(v["total"]),
                   "percentage": round(float(v["percentage"]) * (100 if float(v["percentage"]) <= 1 else 1), 2)}
               for k, v in (scan.get("pillar_scores") or {}).items()} or None
    def sub(o, k):
        v = (o.get("score") or {}).get(k)
        return None if v is None else float(v)
    opps = [{"pattern_type": o["pattern_type"], "module_path": _rel(o["module_path"], root),
             **({"disqualifiers": dq} if (dq := _disqualifiers(o)) else {}),
             "ai_readiness": (o.get("score") or {}).get("ai_readiness"),
             "composite_score": (o.get("score") or {}).get("composite_score"),
             **({"id": str(o["opportunity_id"])} if o.get("opportunity_id") is not None else {}),
             **{k: v for k, v in (("value", sub(o, "value_score")), ("feasibility", sub(o, "feasibility_score")),
                                  ("risk", sub(o, "risk_score"))) if v is not None}}
            for o in scan.get("opportunities") or []]
    opps = [o for o in opps if o["ai_readiness"] is not None and o["composite_score"] is not None]
    rs = scan.get("overall_readiness_score")
    return {"tool": "icdev-it/aiify", "assessed_at": _iso(scan.get("completed_at")) or at,
            "overall_verdict": _verdict(scan), "posture_grade": None,
            "readiness_score": None if rs is None else float(rs), "pillars": pillars,
            "opportunities": sorted(opps, key=lambda o: (o["module_path"], o["pattern_type"])),
            # ICDEV[OS] Phase 247: the roadmap and the decisions people made here, carried
            "roadmap": [{"phase": str(ph.get("phase") or ph.get("id")), "title": str(ph.get("title") or ph.get("name") or ""),
                         "opportunities": [str(x) for x in ph.get("opportunities") or []],
                         **({"effort_days": float(ph["effort_days"])} if ph.get("effort_days") is not None else {})}
                        for ph in ((scan.get("roadmap") or {}).get("phases") or [])],
            "decisions": [{"opportunity": str(d["source_id"]), "decision": {"accept": "accepted", "reject": "rejected"}.get(d["decision"], d["decision"]),
                           "by": str(d.get("actor") or "user"), "at": _iso(d.get("decided_at")) or at}
                          for d in scan.get("hitl_decisions") or [] if d.get("decision") in ("accept", "reject", "accepted", "rejected", "deferred")]}


def integrity_from_sipa(r: dict, *, at: str) -> dict:
    caps = r.get("capabilities") or []
    return {"tool": "icdev-it/sipa", "assessed_at": _iso(r.get("assessed_at")) or at,
            "verdict": str(r["verdict"]).upper(),
            "risk_score": None if r.get("risk_score") is None else float(r["risk_score"]),
            "capabilities": sorted({c if isinstance(c, str) else str(c.get("capability_type") or c.get("type"))
                                    for c in caps})}


def _json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def seven_r_from_assessment(a: dict, *, at: str) -> dict:
    """The 7R assessment, carried whole (ICDEV[OS] Phase 247): the ranking, the recommendation and its own estimates."""
    ev = _json(a.get("evidence"), {})
    ranking = (ev.get("ranking") if isinstance(ev, dict) else None) or a.get("ranking") or []
    out = {"tool": "icdev-it/seven_r_assessor", "assessed_at": _iso(a.get("assessed_at") or a.get("created_at")) or at,
           "ranked": [{"strategy": r.get("strategy_id") or r.get("strategy"), "score": float(r["score"])} for r in ranking]}
    for k, src in (("recommended", "recommended_strategy"), ("ato_impact", "ato_impact")):
        if a.get(src):
            out[k] = str(a[src])
    for k in ("cost_estimate_hours", "risk_score", "timeline_weeks", "tech_debt_reduction"):
        if a.get(k) is not None:
            out[k] = float(a[k])
    w = _json(a.get("scoring_weights"), {})
    if isinstance(w, dict) and w:
        out["weights"] = {str(k): float(v) for k, v in w.items() if isinstance(v, int | float)}
    notes = [str(x) for x in (ev.get("rationale") or ev.get("notes") or [])] if isinstance(ev, dict) else []
    if notes:
        out["evidence"] = notes[:20]
    return out


_WAVE = {"planned": "planned", "in_progress": "in_progress", "complete": "closed", "closed": "closed",
         "backed_out": "backed_out", "blocked": "in_progress"}


def migration_from_plan(app_id: str, conn, *, at: str, waves: list | None = None, strangler: list | None = None) -> dict | None:
    """The application's latest migration plan (ICDEV[OS] Phase 247): strategy, approach, task counts by type, and the
    waves and strangler status the planners produced. No plan recorded: null (unverified in ICDEV[OS])."""
    try:
        row = conn.execute("SELECT * FROM migration_plans WHERE legacy_app_id = %s ORDER BY created_at DESC LIMIT 1", (app_id,)).fetchone()
        if row is None:
            return None
        p = dict(row)
        tasks = [dict(t) for t in conn.execute("SELECT task_type, status FROM migration_tasks WHERE plan_id = %s", (p["id"],))]
    except Exception as exc:  # no migration tables in this database: nothing was planned here
        if "no such table" in str(exc) or "does not exist" in str(exc):
            return None
        raise
    by: dict[str, int] = {}
    for t in tasks:
        by[t["task_type"]] = by.get(t["task_type"], 0) + 1
    out = {"tool": "icdev-it/monolith_decomposer", "assessed_at": _iso(p.get("created_at")) or at, "plan_id": p["id"],
           "strategy": p["strategy"], "approach": p.get("migration_approach") or "strangler_fig", "status": p.get("status") or "draft",
           "tasks_total": len(tasks), "tasks_done": sum(t["status"] == "completed" for t in tasks), "tasks_by_type": by}
    if p.get("target_architecture"):
        out["target_architecture"] = p["target_architecture"]
    if isinstance(waves, dict):                       # app_wave_planner's report: its waves
        waves = waves.get("waves")
    out["waves"] = [{"id": str(w.get("id") or f"w{w.get('wave_number')}"), "title": str(w.get("name") or w.get("title")),
                     "order": int(w.get("wave_number") or w.get("order") or i + 1),
                     "status": _WAVE.get(str(w.get("status") or "planned"), "planned"),
                     **({"components": [str(c) for c in w["components"]]} if w.get("components") else {}),
                     **({"backout": str(w["backout"])} if w.get("backout") else {}),
                     **({"ends": str(w["cutover_date"])} if w.get("cutover_date") else {})} for i, w in enumerate(waves or [])]
    out["strangler"] = [{"component": _slug(str(s["component"])), "status": s["status"],
                         **({"routed_pct": float(s["routed_pct"])} if s.get("routed_pct") is not None else {})}
                        for s in strangler or [] if s.get("status") in ("legacy", "routing", "migrated")]
    return out


def _manifest_digest(arts: list[dict]) -> str:
    """ICDEV[OS]'s binding of a job's artefacts (its apptwin.manifest_digest): SHA-256 over ``"<sha256>  <path>\\n"`` per
    file, sorted by path -- what ``sha256sum`` prints for them."""
    return hashlib.sha256("".join(f"{a['sha256']}  {a['path']}\n" for a in sorted(arts, key=lambda a: a["path"])).encode()).hexdigest()


def _job_artefacts(j: dict) -> list[dict] | None:
    """Each file a CAM job wrote, by its path under the job's output and the SHA-256 of the bytes on disk now. A file
    that is gone is not listed (never a guessed digest); a job that lists files none of which is here gives None."""
    arts = _json(j.get("artifacts_json"), []) if "artifacts_json" in j and not j.get("artifacts") else j.get("artifacts") or []
    if not arts or not all(isinstance(a, dict) for a in arts):
        return None
    out = {}
    root = Path(j["output_path"]) if j.get("output_path") else None
    for a in arts:
        f = Path(str(a.get("path") or ""))
        if not f.is_file():
            continue
        rel = a.get("rel") or (f.relative_to(root).as_posix() if root is not None and f.is_relative_to(root) else f.name)
        out[rel] = {"path": rel, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    return [out[k] for k in sorted(out)] or None


def refactor_from_jobs(jobs, *, at: str) -> dict:
    """CAM's refactor jobs (ICDEV[OS] Phase 247; osx-fth-13). A job's artefacts are each file it wrote, by its SHA-256,
    and ``artefact_digest`` binds them all (``_manifest_digest``). What a migrator writes is generated code that nobody
    here reviewed, so a job that wrote files says ``review: unreviewed``. The older form (``artifacts_json`` a list of
    paths under the repository) is digested by its first file, as before; never a guessed digest."""
    if isinstance(jobs, dict):                        # refactor_jobs.py's report: its jobs
        jobs = jobs.get("jobs") or []
    out = []
    for j in jobs:
        files = _job_artefacts(j)
        if files:
            extra = {"artefact_digest": _manifest_digest(files), "artefacts": files, "review": "unreviewed"}
        else:
            arts = _json(j.get("artifacts_json"), []) if "artifacts_json" in j else j.get("artifacts") or []
            first = _BASE / str(arts[0]) if arts and isinstance(arts[0], str) else None
            dig = hashlib.sha256(first.read_bytes()).hexdigest() if first is not None and first.is_file() else None
            extra = {"artefact_digest": dig} if dig else {}
        why = j.get("result_summary") if j.get("status") != "failed" else j.get("error_message") or j.get("result_summary")
        out.append({"id": str(j["id"]), "kind": str(j.get("refactor_type") or j.get("kind")),
                    **({"component": str(j["component_name"])} if j.get("component_name") else {}),
                    "status": str(j.get("status") or "queued"), **extra,
                    **({"summary": re.sub(r"(?:[A-Za-z]:)?[/\\][^\s'\"]+", lambda m: Path(m.group()).name, str(why))[:300]}
                       if why else {})})   # a summary never says where the job ran
    return {"tool": "icdev-it/cam_refactor_engine", "assessed_at": at, "jobs": out}


def build(app_id: str, conn, *, at: str | None = None, features: dict | None = None, processes: list[dict] = (),
          aiify: dict | None = None, integrity: dict | None = None, seven_r: dict | None = None,
          sections: dict | None = None, waves: list | dict | None = None, strangler: list | None = None,
          refactor: list | dict | None = None, static_features: dict | None = None) -> dict:
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if features is not None and static_features is not None:
        raise BundleError("give observed features or static features, not both: each is one tool's report")
    inv = inventory(app_id, conn)
    procs = [p for p in (process_from_processify(d) for d in processes) if p is not None]
    bundle = {"schema": SCHEMA, "producer": {"tool": PRODUCER, "version": _version()}, "produced_at": at, **inv,
              "features": features_from_inventory(features) if features is not None else
                          features_from_static(static_features) if static_features is not None else None,
              "processes": sorted(procs, key=lambda p: p["id"]) or None,   # no measured steps: unverified, never "none"
              "consumers": None,
              "aiify": aiify_from_scan(aiify, at=at, root=_source_root(app_id, conn)) if aiify is not None else None,
              "integrity": integrity_from_sipa(integrity, at=at) if integrity is not None else None,
              "seven_r": seven_r_from_assessment(seven_r, at=at) if seven_r is not None else None,
              "migration": migration_from_plan(app_id, conn, at=at, waves=waves, strangler=strangler),
              "refactor": refactor_from_jobs(refactor, at=at) if refactor is not None else None}
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
    ap.add_argument("--static-features", type=Path, help="static_features.py output (JSON)")
    ap.add_argument("--process", type=Path, action="append", default=[], help="a Process-Ify draft (JSON); repeatable")
    ap.add_argument("--aiify", type=Path, help="python -m tools.aiify.engine scan --json output")
    ap.add_argument("--integrity", type=Path, help="SIPA assessment (JSON)")
    ap.add_argument("--seven-r", type=Path, help="7R assessment (JSON)")
    ap.add_argument("--waves", type=Path, help="the migration canvas's waves (JSON list)")
    ap.add_argument("--strangler", type=Path, help="strangler-fig component status (JSON list)")
    ap.add_argument("--refactor", type=Path, help="CAM refactor jobs (JSON list)")
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
                  seven_r=load(a.seven_r), sections={k: load(getattr(a, k)) for k in PASSTHROUGH},
                  waves=load(a.waves), strangler=load(a.strangler), refactor=load(a.refactor),
                  static_features=load(a.static_features))
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
