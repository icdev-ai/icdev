#!/usr/bin/env python3
# CUI // SP-CTI
"""Run ICDEV[IT]'s modernisation tooling over one real application, against a scratch database, and export its
ICDEV[OS] app twin bundle with a gap report (ICDEV[OS] Phase 254, docs/programmes/icdev-os-modsamples.md there).

    python tools/modernization/sample_pipeline.py --source DIR --name daytrader7 --scratch-db /tmp/x/icdev.db \\
        --out /tmp/x/daytrader7 [--strategy rehost] [--at 2026-11-12T00:00:00Z]
    python tools/modernization/sample_pipeline.py --resume --scratch-db /tmp/x/icdev.db --out /tmp/x/daytrader7 \\
        --icos-decisions decisions.json --icos-jwks icos_jwks.json

A migration is planned only on ICDEV[OS]'s signed may-plan (icx-mdz-02): the CAM refactor jobs (5) and the plan (8)
are refused without it, and the gap report says so. ICDEV[OS] decides after it has twinned the bundle this run
exports, so ``--resume`` plans the same application (the same scratch database) once its decisions export exists.

Every step is the existing tool, run as a subprocess with the scratch database in its environment:

     1. project_create             a scratch project
     2. legacy_analyzer            --register, then --analyze
        static_features            the entry points grouped into features (features, provenance static)
        bundle_features            (icx-mdz-03) those features with file:line evidence and capability hints, merged
                                   with the routes an REA observed inventory names (--observed; never captured here),
                                   and the processes: the actions one controller offers in source order, or an REA
                                   capture (--observed-process), each with its step signature -- no model
     3. architecture_extractor     --extract db-schema          (the bundle's data)
     4. seven_r_assessor           --json                       (seven_r)
     5. refactor_jobs              the migrators as CAM refactor jobs (refactor; osx-fth-13): the version migrator
                                   (declared level -> current), the framework migrator (EJB / Struts / WCF) and the
                                   database planner (over the extracted schema), each a job whose files are bound by
                                   SHA-256 in the bundle and labelled unreviewed
        cloud_native_jobs          the cloud-native target as CAM jobs (cloud_native; osx-fth-14): a container
                                   definition and deployment manifest, the hard-coded configuration by location with
                                   its environment mapping, a twelve-factor report and, for message-driven beans, an
                                   event contract -- generated from the source, unreviewed, bound by SHA-256
     6. aiify.engine scan          (aiify; its opportunities are also DRAFTED as AI use cases in cloud_native)
     7. processify --from-doc      the README (processes, basis document); skipped by --no-model, the only step that
                                   calls a model. The static and observed processes above need none.
     8. monolith_decomposer        --create-plan with the strategy (migration)
     9. app_wave_planner           the plan's waves, each with its backout (migration.waves; osx-fth-13), dated only from
                                   a team capacity a person declared and reviewed (--capacity, else
                                   args/modernisation_capacity.yaml; icx-mdz-05): the plan's schedule says which
    10. apptwin_export             every section a step produced, and every section's present/missing state
                                   (``sections``, in the bundle itself since icx-mdz-04 and in gaps.json: the waves,
                                   refactor jobs and cloud-native parts too); the waves derived before a plan as
                                   ``derived_waves``; the application's environment only as --environments (or
                                   args/modernisation_environments.yaml) declares it, with its source -- else none,
                                   and gaps.json says so

The 7R (4) reads retire's functional_redundancy from ICDEV[OS]'s signed decisions export (icx-mdz-04): none on a first
run, so unmeasured; ``--resume`` reruns it on the export it is given.

The source is UNTRUSTED DATA: it is read, never built or run, and a step that would need the application running
(observed inventory) is reported, not attempted: features are reported statically, from the entry points the
analyser read, labelled ``static`` (never ``observed``) unless an operator hands in an REA capture made elsewhere. A section no step produced stays null in the bundle and is listed
in ``gaps.json`` with why -- nothing is filled in by hand. The scratch database is created here (``init_icdev_db``)
and must not be the operational one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root

BASE = repo_root(__file__)
PY = sys.executable


class PipelineError(RuntimeError):
    pass


def _last_json(text: str):
    """The last top-level JSON value in ``text``: several tools print log lines before their JSON."""
    for i in [m.start() for m in re.finditer(r"(?m)^[\[{]", text)]:
        try:
            return json.loads(text[i:])
        except ValueError:
            continue
    return None


def _summary(res) -> dict:
    """A tool's scalar results, without the local paths it reports (a report never says where it ran)."""
    return {k: v for k, v in (res or {}).items()
            if not isinstance(v, (list, dict)) and not (isinstance(v, str) and v.startswith(("/", "\\")))}


class Run:
    def __init__(self, scratch_db: Path, out: Path):
        self.out, self.steps = out, []
        self.env = {**os.environ, "ICDEV_DB_PATH": str(scratch_db), "ICDEV_STORAGE_BACKEND": "sqlite",
                    "ICDEV_CANVAS_STORAGE_BACKEND": "sqlite", "AIIFY_STORAGE_BACKEND": "sqlite",
                    "AIIFY_DB_PATH": str(scratch_db.with_name("aiify_canvas.db")), "PYTHONPATH": str(BASE),
                    # CAM's canvas database and its jobs' files: the run's own, never data/ (osx-fth-13)
                    "MC_DB_PATH": str(scratch_db.with_name("migration_canvas.db")),
                    "CAM_ARTIFACTS_DIR": str(out / "artefacts" / "cam")}

    def tool(self, step: str, *args: str, timeout: int = 1800) -> tuple[int, str, str]:
        cmd = [PY, *args] if args[0] == "-m" else [PY, str(BASE / args[0]), *args[1:]]
        p = subprocess.run(cmd, cwd=BASE, env=self.env, capture_output=True, text=True, timeout=timeout, check=False)
        (self.out / "logs").mkdir(parents=True, exist_ok=True)
        (self.out / "logs" / f"{step}.log").write_text(f"$ {' '.join(args)}\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}")
        return p.returncode, p.stdout, p.stderr

    def note(self, step: str, ok: bool, section: str | None = None, reason: str | None = None, **extra):
        self.steps.append({"step": step, "ok": ok, **({"section": section} if section else {}),
                           **({"reason": reason} if reason else {}), **extra})


def _why(stderr: str, stdout: str) -> str:
    lines = [ln for ln in (stderr or stdout).strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "no output")[:300]


def _refused(rc: int, so: str) -> str | None:
    """The reason a planner refused for want of ICDEV[OS]'s may-plan (exit 3, icx-mdz-02), else None."""
    if rc != 3:
        return None
    d = _last_json(so) or {}
    return f"refused, no ICDEV[OS] may-plan ({d.get('status') or 'refused'}): {d.get('reason') or 'no reason given'}"


def _refactor(r: Run, app: str, chosen: str | None, a: dict, gate: list[str]) -> list:
    """Step 5: the migrators as CAM jobs -- only on ICDEV[OS]'s may-plan (refactor_jobs refuses without it)."""
    ref_path = r.out / "refactor.json"
    rc, so, se = r.tool("refactor", "tools/modernization/refactor_jobs.py", "--app-id", app, "--out", str(ref_path),
                        *(["--strategy", chosen] if chosen else []), *gate)
    refused = _refused(rc, so)
    ref = json.loads(ref_path.read_text()) if rc == 0 and ref_path.is_file() else None
    jobs = (ref or {}).get("jobs") or []
    r.note("refactor", bool(jobs), "refactor", None if jobs else refused or
           (_why(se, so) if rc else f"no CAM rule applies to {a['primary_language']} {a['language_version'] or ''} / "
                                    f"{a['framework'] or 'no framework'}, and no schema was extracted"),
           jobs=[{"kind": j["refactor_type"], "status": j["status"], "files": len(j.get("artifacts") or []),
                  **({"error": _why(j["error_message"], "")} if j.get("error_message") else {})} for j in jobs])
    if ref is not None and not any(j["refactor_type"] == "framework_migration" for j in jobs):
        r.note("framework", False, None, f"no framework migrator for {a['framework'] or 'an undetected framework'}")
    return jobs


def _plan_and_waves(r: Run, app: str, project: str, chosen: str | None, strategy_from: str, gate: list[str],
                    capacity: Path | None = None):
    """Steps 8 and 9: the migration plan -- only on ICDEV[OS]'s may-plan -- and its waves, dated only from a reviewed
    entry of the declared capacity file (``capacity``; else the planner's default)."""
    if chosen:
        rc, so, se = r.tool("plan", "tools/modernization/monolith_decomposer.py", "--app-id", app, "--create-plan",
                            "--project-id", project, "--strategy", chosen, "--target-arch", "modular_monolith",
                            "--json", *gate)
        r.note("plan", rc == 0, "migration", None if rc == 0 else _refused(rc, so) or _why(se, so), strategy=chosen,
               strategy_from=strategy_from)
    else:
        r.note("plan", False, "migration", "no strategy: neither --strategy nor a 7R recommendation")
    waves_path = r.out / "waves.json"
    rc, so, se = r.tool("waves", "tools/modernization/app_wave_planner.py", "--app-id", app, "--out", str(waves_path),
                        *(["--capacity", str(capacity)] if capacity else []))
    waves = json.loads(waves_path.read_text()) if rc == 0 and waves_path.is_file() else None
    sch = (waves or {}).get("schedule") or {}
    r.note("waves", bool((waves or {}).get("waves")), "migration.waves", None if waves else _why(se, so),
           waves=len((waves or {}).get("waves") or []), basis=(waves or {}).get("basis"),
           schedule={"dated": bool(sch.get("dated")), "reason": sch.get("reason")} if sch else None)
    return waves


#: Why a section no step of this pipeline makes is empty.
UNPRODUCED = {
    "consumers": "the exporter never carries consumers",
    "integrity": "no SIPA assessment was run",
    "ivv": "no IV&V assessment was run", "sbd": "no Secure by Design assessment was run",
    "rtm": "no requirements traceability was run", "coherence": "no coherence check was run",
    "readiness": "no readiness assessment was run"}


def _export(r: Run, *, name: str, app: str, project: str, source_name: str, inputs: dict, at: str | None,
            may_plan: dict, environments: Path | None = None) -> dict:
    """Step 10: the bundle from every section a step produced, and the gap report. Since icx-mdz-04 the bundle carries
    its features' and processes' evidence, hints, step signatures and reviews and its ``sections`` inline (contract
    1.189.0), so no side file is needed beside it; the gap report stays: the run's steps, and why each gap is one."""
    out = r.out
    bundle_path = out / f"{name}.apptwin.json"
    args = ["tools/modernization/apptwin_export.py", "--app-id", app, "--out", str(bundle_path)]
    feats = ("--extract", "extract") if inputs.get("extract") else ("--static-features", "static_features")
    for flag, key in (feats, ("--seven-r", "seven_r"), ("--aiify", "aiify"),
                      ("--refactor", "refactor"), ("--waves", "waves"), ("--cloud-native", "cloud_native")):
        if inputs.get(key):
            args += [flag, str(out / inputs[key])]
    for p in inputs.get("processes") or []:
        args += ["--process", str(out / p)]
    if at:
        args += ["--at", at]
    if environments:
        args += ["--environments", str(Path(environments).resolve())]
    reasons = {**UNPRODUCED, **{s["section"]: s.get("reason") or "the step produced nothing"
                                for s in r.steps if s.get("section") and not s["ok"]}}
    cn = next((s for s in reversed(r.steps) if s["step"] == "cloud-native"), None)
    if cn and cn["ok"] and not any(j["kind"] == "event_contract" for j in cn.get("jobs") or []):
        reasons["cloud_native.events"] = (f"no event_contract job: CAM's cloud_native rules queue one only for an EJB "
                                          f"application's message-driven beans (framework {inputs.get('framework')})")
    (out / "reasons.json").write_text(json.dumps(reasons, indent=2, sort_keys=True) + "\n")
    args += ["--reasons", str(out / "reasons.json"), "--sections-out", str(out / "sections.json")]
    rc, so, se = r.tool("export", *args)
    if rc != 0 or not bundle_path.is_file():
        raise PipelineError(f"apptwin_export: {_why(se, so)}")
    exported = _last_json(so) or {}
    bundle = json.loads(bundle_path.read_text())
    sections = json.loads((out / "sections.json").read_text())
    produced = {k for k, v in bundle.items() if v not in (None, [], {})}
    unproduced = dict(UNPRODUCED)
    for s in r.steps:
        if s.get("section") and not s["ok"]:
            unproduced[s["section"]] = s.get("reason") or "the step produced nothing"
    gaps = {k: unproduced.get(k, "no step produced it") for k in sorted(set(bundle) - produced - {"schema"})}
    if "derived_waves" in gaps:
        gaps["derived_waves"] = ("a migration plan carries the waves (migration.waves)" if bundle.get("migration")
                                 else unproduced.get("migration.waves") or "no wave was derived")
    if (bundle.get("migration") or {}) and not (bundle["migration"].get("waves")):
        gaps["migration.waves"] = next((s.get("reason") for s in reversed(r.steps) if s["step"] == "waves" and not s["ok"]),
                                       None) or "no wave was planned, so ICDEV[OS]'s migration-plan gate will block"
    report = {"application": name, "source": source_name, "app_id": app, "project_id": project,
              "bundle": bundle_path.name, "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
              "produced": sorted(produced - {"schema", "producer", "produced_at"}), "gaps": gaps, "steps": r.steps,
              "sections": sections, "inputs": inputs, "may_plan": may_plan,
              # icx-mdz-04: the application's environment, declared with its source or unreported with why
              "environment": exported.get("environment") or {"declared": False, "reason": "the exporter said nothing"},
              "contract": exported.get("contract"),
              "at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out / "gaps.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _extract(r: Run, app: str, observed: Path | None, observed_processes: list[Path]) -> tuple[dict | None, Path]:
    """bundle_features (icx-mdz-03): the features with their evidence and hints, and the processes, without a model.
    An REA capture the operator made elsewhere is copied into the run, so the run says what it read."""
    path = r.out / "features.extract.json"
    args = ["tools/modernization/bundle_features.py", "--app-id", app, "--out", str(path)]
    def keep(src: Path, name: str) -> Path:
        dest = r.out / name
        if Path(src).resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        return dest
    if observed:
        args += ["--observed", str(keep(observed, "observed.inventory.json"))]
    for i, p in enumerate(observed_processes):
        args += ["--observed-process", str(keep(p, f"observed.process-{i + 1}.json"))]
    rc, so, se = r.tool("extract", *args)
    rep = json.loads(path.read_text()) if rc == 0 and path.is_file() else None
    c = (rep or {}).get("counts") or {}
    r.note("extract", rep is not None and bool(rep.get("features")), None,
           None if rep else _why(se, so), rules_digest=((rep or {}).get("rules") or {}).get("digest"),
           **{k: c[k] for k in ("features", "features_static", "features_observed", "features_both", "processes")
              if k in c}, observed=(rep or {}).get("observed"))
    return (rep if rep and (rep.get("features") or rep.get("processes")) else None), path


def _seven_r(r: Run, project: str, app: str, gate: list[str]) -> dict | None:
    """Step 4: the 7R. Retire's functional_redundancy is ICDEV[OS]'s measured redundancy from the same signed export the
    planners ask (icx-mdz-04); with none, it is unmeasured, and the step says which."""
    rc, so, se = r.tool("seven-r", "tools/modernization/seven_r_assessor.py", "--project-id", project, "--app-id", app,
                        "--json", *gate)
    seven = _last_json(so) if rc == 0 else None
    if seven:
        (r.out / "seven_r.json").write_text(json.dumps(seven, indent=2))
    red = ((seven or {}).get("evidence") or {}).get("redundancy") if isinstance((seven or {}).get("evidence"), dict) else None
    r.note("seven-r", bool(seven), "seven_r", None if seven else _why(se, so),
           recommended=(seven or {}).get("recommended_strategy"), confidence=(seven or {}).get("confidence"),
           recommendation=((seven or {}).get("recommendation") or {}).get("reason"),
           redundancy={k: (red or {}).get(k) for k in ("share", "state", "why")} if red else None)
    return seven


def _gate(icos_decisions: Path | None, icos_jwks: Path | None) -> tuple[list[str], dict]:
    """The planners' may-plan options, and what the report says about them (never where the files are)."""
    if icos_decisions and icos_jwks:
        return (["--icos-decisions", str(Path(icos_decisions).resolve()), "--icos-jwks", str(Path(icos_jwks).resolve())],
                {"given": True})
    return [], {"given": False, "reason": "no signed ICDEV[OS] decisions export given: the plan and the CAM refactor "
                                          "jobs wait for its may-plan (rerun with --resume once ICDEV[OS] decides)"}


def pipeline(source: Path, name: str, scratch_db: Path, out: Path, *, strategy: str | None = None,
             at: str | None = None, no_model: bool = False, targets: dict | None = None,
             icos_decisions: Path | None = None, icos_jwks: Path | None = None, observed: Path | None = None,
             observed_processes: list[Path] | None = None, environments: Path | None = None,
             capacity: Path | None = None) -> dict:
    source, out = source.resolve(), out.resolve()
    if not source.is_dir():
        raise PipelineError(f"no source directory {source}")
    live = (BASE / "data" / "icdev.db").resolve()
    if scratch_db.resolve() == live or scratch_db.exists():
        raise PipelineError(f"{scratch_db}: the scratch database must be a new file, never the operational one")
    out.mkdir(parents=True, exist_ok=True)
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    r = Run(scratch_db, out)
    gate, may_plan = _gate(icos_decisions, icos_jwks)
    rc, so, se = r.tool("init-db", "tools/db/init_icdev_db.py", "--db-path", str(scratch_db))
    if rc != 0:
        raise PipelineError(f"init_icdev_db: {_why(se, so)}")

    rc, so, se = r.tool("project", "tools/project/project_create.py", "--name", f"modsamples-{name}", "--type", "webapp",
                        "--classification", "Public", "--impact-level", "IL2", "--skip-scaffold")
    m = re.search(r"ID:\s+(\S+)", so)
    if rc != 0 or not m:
        raise PipelineError(f"project_create: {_why(se, so)}")
    project = m.group(1)
    r.note("project", True, project_id=project)

    rc, so, se = r.tool("register", "tools/modernization/legacy_analyzer.py", "--register", "--project-id", project,
                        "--name", name, "--source-path", str(source), "--json")
    c = sqlite3.connect(scratch_db)   # the scratch file itself, never through storage: sqlite3's own ? placeholders
    try:
        row = c.execute("SELECT id FROM legacy_applications WHERE name = ?", (name,)).fetchone()
    finally:
        c.close()
    if rc != 0 or row is None:
        raise PipelineError(f"legacy_analyzer --register: {_why(se, so)}")
    app = row[0]
    rc, so, se = r.tool("analyze", "tools/modernization/legacy_analyzer.py", "--analyze", "--project-id", project,
                        "--app-id", app, "--json")
    if rc != 0:
        raise PipelineError(f"legacy_analyzer --analyze: {_why(se, so)}")
    c = sqlite3.connect(scratch_db)
    try:
        c.row_factory = sqlite3.Row
        a = dict(c.execute("SELECT * FROM legacy_applications WHERE id = ?", (app,)).fetchone())
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE legacy_app_id = ?", (app,)).fetchone()[0]
                  for t in ("legacy_components", "legacy_apis", "legacy_dependencies")}
    finally:
        c.close()
    r.note("analyze", True, app_id=app, language=a["primary_language"], language_version=a["language_version"],
           framework=a["framework"], **counts)

    feats_path = out / "features.static.json"
    rc, so, se = r.tool("features", "tools/modernization/static_features.py", "--app-id", app, "--out", str(feats_path))
    feats = json.loads(feats_path.read_text()) if rc == 0 and feats_path.is_file() else None
    n_feats = len((feats or {}).get("features") or [])
    r.note("features", n_feats > 0, "features", None if n_feats else
           (f"no HTTP entry point found statically ({_why(se, so)})" if rc else
            "no HTTP entry point (API, controller, servlet or route) found statically"),
           provenance="static", features=n_feats, entry_points=(feats or {}).get("entry_points", 0),
           detected_by=(feats or {}).get("detected_by", {}))
    extract, ext_path = _extract(r, app, observed, observed_processes or [])

    rc, so, se = r.tool("db-schema", "tools/modernization/architecture_extractor.py", "--app-id", app, "--extract",
                        "db-schema", "--source-path", str(source))
    c = sqlite3.connect(scratch_db)
    try:
        tables = c.execute("SELECT COUNT(DISTINCT table_name) FROM legacy_db_schemas WHERE legacy_app_id = ?",
                           (app,)).fetchone()[0]
    finally:
        c.close()
    r.note("db-schema", rc == 0 and tables > 0, "data", None if tables else
           (f"no ORM model or SQL DDL the extractor reads ({_why(se, so)})" if rc else "no ORM model or SQL DDL the extractor reads"),
           tables=tables)

    seven = _seven_r(r, project, app, gate)

    chosen = strategy or (seven or {}).get("recommended_strategy")
    jobs = _refactor(r, app, chosen, a, gate)

    cn_path = out / "cloud_native.json"
    cn_args = [x for t in (targets or {}).get("cloud") or [] for x in ("--target-cloud", t)] + \
              [x for t in (targets or {}).get("ai") or [] for x in ("--target-ai", t)]
    rc, so, se = r.tool("cloud-native", "tools/modernization/cloud_native_jobs.py", "--app-id", app, "--out",
                        str(cn_path), *cn_args)
    cn = json.loads(cn_path.read_text()) if rc == 0 and cn_path.is_file() else None
    cn_jobs = (cn or {}).get("jobs") or []
    cn_ok = any(j["scaffold"] == "twelve_factor_report" and j["status"] == "completed" for j in cn_jobs)
    r.note("cloud-native", cn_ok, "cloud_native", None if cn_ok else
           (_why(se, so) if rc else "the twelve-factor job did not complete: " +
            "; ".join(_why(j.get("error_message") or "", "") for j in cn_jobs if j["status"] == "failed")),
           jobs=[{"kind": j["scaffold"], "status": j["status"], "files": len(j.get("artifacts") or []),
                  **({"error": _why(j["error_message"], "")} if j.get("error_message") else {})} for j in cn_jobs])

    rc, so, se = r.tool("aiify", "-m", "tools.aiify.engine", "scan", "--input-type", "code", "--input-ref", str(source),
                        "--il-level", "IL2")
    aiify = _last_json(so) if rc == 0 else None
    if aiify:
        (out / "aiify.json").write_text(json.dumps(aiify, indent=2))
    r.note("aiify", bool(aiify), "aiify", None if aiify else _why(se, so),
           opportunities=len((aiify or {}).get("opportunities") or []), verdict=(aiify or {}).get("overall_verdict"))

    procs = []
    readme = next((p for p in (source / n for n in ("README.md", "README.txt", "README")) if p.is_file()), None)
    n_procs = len((extract or {}).get("processes") or [])
    r.note("processify-source", n_procs > 0, "processes", None if n_procs else
           "no controller offers two or more actions, and no REA capture was given: no process without a model",
           processes=n_procs, static=sum(1 for p in (extract or {}).get("processes") or [] if p["source"] == "static"),
           observed=sum(1 for p in (extract or {}).get("processes") or [] if p["source"] == "observed"),
           rules_digest=((extract or {}).get("rules") or {}).get("digest"))
    if no_model:
        r.note("processify", False, None, "skipped (--no-model): Process-Ify reads a document with a language model")
    elif readme is None:
        r.note("processify", False, None, "no README to read a process from")
    else:
        doc = out / "readme.txt"
        shutil.copyfile(readme, doc)
        rc, so, se = r.tool("processify", "tools/workflow_canvas/processify.py", "--from-doc", str(doc))
        d = _last_json(so) if rc == 0 else None
        if d:
            (out / "process.json").write_text(json.dumps(d, indent=2))
            procs.append("process.json")
        r.note("processify", bool(d), "processes", None if d else _why(se, so))

    waves = _plan_and_waves(r, app, project, chosen, "--strategy" if strategy else "the 7R recommendation", gate,
                            _keep(out, capacity, "capacity.yaml"))

    inputs = {"static_features": feats_path.name if n_feats else None, "extract": ext_path.name if extract else None,
              "seven_r": "seven_r.json" if seven else None,
              "aiify": "aiify.json" if aiify else None, "refactor": "refactor.json" if jobs else None,
              "waves": "waves.json" if (waves or {}).get("waves") else None,
              "cloud_native": cn_path.name if cn_ok else None, "processes": procs,
              "language": a["primary_language"], "language_version": a["language_version"],
              "framework": a["framework"]}
    return _export(r, name=name, app=app, project=project, source_name=source.name, inputs=inputs, at=at,
                   may_plan=may_plan, environments=_keep_environments(out, environments))


def _keep_environments(out: Path, environments: Path | None) -> Path | None:
    """The declared environments file this run used (icx-mdz-04), copied into the run so --resume reads the same one;
    with none given, the repository's ``args/modernisation_environments.yaml`` (the exporter's default)."""
    return _keep(out, environments, "environments.yaml")


def _keep(out: Path, given: Path | None, name: str) -> Path | None:
    """A declared file this run used (environments, icx-mdz-04; team capacity, osx-mdz-09), copied into the run as
    ``name`` so --resume reads the same one; none given: the one an earlier run kept, else None (the tool's default)."""
    dest = out / name
    if given:
        if Path(given).resolve() != dest.resolve():
            shutil.copyfile(given, dest)
        return dest
    return dest if dest.is_file() else None


def resume(out: Path, scratch_db: Path, *, icos_decisions: Path, icos_jwks: Path, strategy: str | None = None,
           at: str | None = None, environments: Path | None = None, capacity: Path | None = None) -> dict:
    """Plan the application a first run assessed, now that ICDEV[OS] has decided (icx-mdz-02).

    The first run cannot plan: a migration is planned only on ICDEV[OS]'s signed may-plan, which ICDEV[OS] gives after
    it has twinned the bundle that run exported. Given that export and the pinned JWKS, this reruns the CAM refactor
    jobs, the plan and its waves against the SAME scratch database (``gaps.json`` names the application), and
    re-exports the bundle. A refusal is reported again, with its reason; nothing is filled in by hand.
    """
    out = out.resolve()
    prior = json.loads((out / "gaps.json").read_text())
    live = (BASE / "data" / "icdev.db").resolve()
    if scratch_db.resolve() == live or not scratch_db.is_file():
        raise PipelineError(f"{scratch_db}: resume needs the first run's scratch database, never the operational one")
    r = Run(scratch_db, out)
    r.steps = [s for s in prior.get("steps") or [] if s.get("step") not in ("refactor", "framework", "plan", "waves",
                                                                           "extract", "processify-source", "seven-r")]
    gate, may_plan = _gate(icos_decisions, icos_jwks)
    app, project, inputs = prior["app_id"], prior["project_id"], dict(prior.get("inputs") or {})
    # icx-mdz-04: the 7R again, now that retire can read ICDEV[OS]'s measured redundancy from this export
    seven = _seven_r(r, project, app, gate) or (json.loads((out / inputs["seven_r"]).read_text())
                                                 if inputs.get("seven_r") else {})
    inputs["seven_r"] = "seven_r.json" if (out / "seven_r.json").is_file() else inputs.get("seven_r")
    chosen = strategy or seven.get("recommended_strategy")
    a = {"primary_language": inputs.get("language"), "language_version": inputs.get("language_version"),
         "framework": inputs.get("framework")}
    # icx-mdz-03: the processes again, so a review a person took since the first run is carried
    obs = out / "observed.inventory.json"
    extract, ext_path = _extract(r, app, obs if obs.is_file() else None, sorted(out.glob("observed.process-*.json")))
    n = len((extract or {}).get("processes") or [])
    r.note("processify-source", n > 0, "processes", None if n else
           "no controller offers two or more actions, and no REA capture was given: no process without a model",
           processes=n, rules_digest=((extract or {}).get("rules") or {}).get("digest"))
    jobs = _refactor(r, app, chosen, a, gate)
    waves = _plan_and_waves(r, app, project, chosen, "--strategy" if strategy else "the 7R recommendation", gate,
                            _keep(out, capacity, "capacity.yaml"))
    inputs.update(refactor="refactor.json" if jobs else None, extract=ext_path.name if extract else None,
                  waves="waves.json" if (waves or {}).get("waves") else None)
    return _export(r, name=prior["application"], app=app, project=project, source_name=prior.get("source") or "",
                   inputs=inputs, at=at or prior.get("at"), may_plan={**may_plan, "resumed": True},
                   environments=_keep_environments(out, environments))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", help="the application's source directory (read only)")
    ap.add_argument("--name", help="the application's id in the bundle")
    ap.add_argument("--scratch-db", required=True, help="a NEW sqlite file for this run (with --resume: the first run's)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--strategy", help="the 7R strategy to plan for (default: the 7R recommendation)")
    ap.add_argument("--at", help="produced_at (ISO 8601) for a reproducible bundle")
    ap.add_argument("--no-model", action="store_true", help="run no step that calls a language model (Process-Ify)")
    ap.add_argument("--target-cloud", action="append", default=[],
                    help="one item of the plan's cloud-native target (ICDEV[OS] apps.yaml target.cloud); repeatable")
    ap.add_argument("--target-ai", action="append", default=[],
                    help="one item of the plan's AI target (ICDEV[OS] apps.yaml target.ai); repeatable")
    ap.add_argument("--icos-decisions", help="ICDEV[OS]'s signed decisions export: the plan and the CAM refactor jobs "
                                             "run only on its may-plan (icx-mdz-02)")
    ap.add_argument("--icos-jwks", help="the ICDEV[OS] JWKS you PINNED")
    ap.add_argument("--observed", help="an REA observed inventory captured elsewhere (observed_inventory JSON); the "
                                       "source is never run here")
    ap.add_argument("--observed-process", action="append", default=[],
                    help="an observed Process-Ify draft from an REA capture made elsewhere; repeatable")
    ap.add_argument("--environments",
                    help="a declared environments file (args/modernisation_environments.yaml's shape): each "
                         "application's security domain, impact level, ATO boundary and hosting as its deployment "
                         "declares them, with the source (default: that file)")
    ap.add_argument("--capacity",
                    help="a declared team capacity file (args/modernisation_capacity.yaml's shape, osx-mdz-09): the "
                         "waves are dated only from an entry a person reviewed at its digest (default: that file)")
    ap.add_argument("--resume", action="store_true",
                    help="plan the application a first run in --out assessed, once ICDEV[OS] decided "
                         "(needs --icos-decisions and --icos-jwks)")
    a = ap.parse_args(argv)
    try:
        if a.resume:
            if not (a.icos_decisions and a.icos_jwks):
                ap.error("--resume needs --icos-decisions and --icos-jwks")
            rep = resume(Path(a.out), Path(a.scratch_db), icos_decisions=Path(a.icos_decisions),
                         icos_jwks=Path(a.icos_jwks), strategy=a.strategy, at=a.at,
                         environments=Path(a.environments) if a.environments else None,
                         capacity=Path(a.capacity) if a.capacity else None)
        else:
            if not (a.source and a.name):
                ap.error("--source and --name are required (or --resume)")
            rep = pipeline(Path(a.source), a.name, Path(a.scratch_db), Path(a.out), strategy=a.strategy, at=a.at,
                           no_model=a.no_model, targets={"cloud": a.target_cloud, "ai": a.target_ai},
                           icos_decisions=Path(a.icos_decisions) if a.icos_decisions else None,
                           icos_jwks=Path(a.icos_jwks) if a.icos_jwks else None,
                           observed=Path(a.observed) if a.observed else None,
                           observed_processes=[Path(p) for p in a.observed_process],
                           environments=Path(a.environments) if a.environments else None,
                           capacity=Path(a.capacity) if a.capacity else None)
    except (PipelineError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1
    print(json.dumps({k: rep[k] for k in ("application", "bundle", "bundle_sha256", "produced", "gaps")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
