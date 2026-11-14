#!/usr/bin/env python3
# CUI // SP-CTI
"""Run ICDEV[IT]'s modernisation tooling over one real application, against a scratch database, and export its
ICDEV[OS] app twin bundle with a gap report (ICDEV[OS] Phase 254, docs/programmes/icdev-os-modsamples.md there).

    python tools/modernization/sample_pipeline.py --source DIR --name daytrader7 --scratch-db /tmp/x/icdev.db \\
        --out /tmp/x/daytrader7 [--strategy rehost] [--at 2026-11-12T00:00:00Z]

Every step is the existing tool, run as a subprocess with the scratch database in its environment:

     1. project_create             a scratch project
     2. legacy_analyzer            --register, then --analyze
        static_features            the entry points grouped into features (features, provenance static)
     3. architecture_extractor     --extract db-schema          (the bundle's data)
     4. seven_r_assessor           --json                       (seven_r)
     5. refactor_jobs              the migrators as CAM refactor jobs (refactor; osx-fth-13): the version migrator
                                   (declared level -> current), the framework migrator (EJB / Struts / WCF) and the
                                   database planner (over the extracted schema), each a job whose files are bound by
                                   SHA-256 in the bundle and labelled unreviewed
     6. aiify.engine scan          (aiify)
     7. processify --from-doc      the README (processes); skipped by --no-model, the only step that calls a model
     8. monolith_decomposer        --create-plan with the strategy (migration)
     9. app_wave_planner           the plan's waves, each with its backout (migration.waves; osx-fth-13)
    10. apptwin_export             every section a step produced

The source is UNTRUSTED DATA: it is read, never built or run, and a step that would need the application running
(observed inventory) is reported, not attempted: features are reported statically, from the entry points the
analyser read, labelled ``static`` (never ``observed``). A section no step produced stays null in the bundle and is listed
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


def pipeline(source: Path, name: str, scratch_db: Path, out: Path, *, strategy: str | None = None,
             at: str | None = None, no_model: bool = False) -> dict:
    source, out = source.resolve(), out.resolve()
    if not source.is_dir():
        raise PipelineError(f"no source directory {source}")
    live = (BASE / "data" / "icdev.db").resolve()
    if scratch_db.resolve() == live or scratch_db.exists():
        raise PipelineError(f"{scratch_db}: the scratch database must be a new file, never the operational one")
    out.mkdir(parents=True, exist_ok=True)
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    r = Run(scratch_db, out)
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
    lang = a["primary_language"]
    r.note("analyze", True, app_id=app, language=lang, language_version=a["language_version"], framework=a["framework"],
           **counts)

    feats_path = out / "features.static.json"
    rc, so, se = r.tool("features", "tools/modernization/static_features.py", "--app-id", app, "--out", str(feats_path))
    feats = json.loads(feats_path.read_text()) if rc == 0 and feats_path.is_file() else None
    n_feats = len((feats or {}).get("features") or [])
    r.note("features", n_feats > 0, "features", None if n_feats else
           (f"no HTTP entry point found statically ({_why(se, so)})" if rc else
            "no HTTP entry point (API, controller, servlet or route) found statically"),
           provenance="static", features=n_feats, entry_points=(feats or {}).get("entry_points", 0),
           detected_by=(feats or {}).get("detected_by", {}))

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

    rc, so, se = r.tool("seven-r", "tools/modernization/seven_r_assessor.py", "--project-id", project, "--app-id", app, "--json")
    seven = _last_json(so) if rc == 0 else None
    if seven:
        (out / "seven_r.json").write_text(json.dumps(seven, indent=2))
    r.note("seven-r", bool(seven), "seven_r", None if seven else _why(se, so),
           recommended=(seven or {}).get("recommended_strategy"))

    chosen = strategy or (seven or {}).get("recommended_strategy")
    ref_path = out / "refactor.json"
    rc, so, se = r.tool("refactor", "tools/modernization/refactor_jobs.py", "--app-id", app, "--out", str(ref_path),
                        *(["--strategy", chosen] if chosen else []))
    ref = json.loads(ref_path.read_text()) if rc == 0 and ref_path.is_file() else None
    jobs = (ref or {}).get("jobs") or []
    r.note("refactor", bool(jobs), "refactor", None if jobs else
           (_why(se, so) if rc else f"no CAM rule applies to {lang} {a['language_version'] or ''} / "
                                    f"{a['framework'] or 'no framework'}, and no schema was extracted"),
           jobs=[{"kind": j["refactor_type"], "status": j["status"], "files": len(j.get("artifacts") or []),
                  **({"error": _why(j["error_message"], "")} if j.get("error_message") else {})} for j in jobs])
    if not any(j["refactor_type"] == "framework_migration" for j in jobs):
        r.note("framework", False, None, f"no framework migrator for {a['framework'] or 'an undetected framework'}")

    rc, so, se = r.tool("aiify", "-m", "tools.aiify.engine", "scan", "--input-type", "code", "--input-ref", str(source),
                        "--il-level", "IL2")
    aiify = _last_json(so) if rc == 0 else None
    if aiify:
        (out / "aiify.json").write_text(json.dumps(aiify, indent=2))
    r.note("aiify", bool(aiify), "aiify", None if aiify else _why(se, so),
           opportunities=len((aiify or {}).get("opportunities") or []), verdict=(aiify or {}).get("overall_verdict"))

    procs = []
    readme = next((p for p in (source / n for n in ("README.md", "README.txt", "README")) if p.is_file()), None)
    if no_model:
        r.note("processify", False, "processes", "skipped (--no-model): Process-Ify reads a document with a language model")
    elif readme is None:
        r.note("processify", False, "processes", "no README to read a process from")
    else:
        doc = out / "readme.txt"
        shutil.copyfile(readme, doc)
        rc, so, se = r.tool("processify", "tools/workflow_canvas/processify.py", "--from-doc", str(doc))
        d = _last_json(so) if rc == 0 else None
        if d:
            (out / "process.json").write_text(json.dumps(d, indent=2))
            procs.append(out / "process.json")
        r.note("processify", bool(d), "processes", None if d else _why(se, so))

    if chosen:
        rc, so, se = r.tool("plan", "tools/modernization/monolith_decomposer.py", "--app-id", app, "--create-plan",
                            "--project-id", project, "--strategy", chosen, "--target-arch", "modular_monolith", "--json")
        r.note("plan", rc == 0, "migration", None if rc == 0 else _why(se, so), strategy=chosen,
               strategy_from="--strategy" if strategy else "the 7R recommendation")
    else:
        r.note("plan", False, "migration", "no strategy: neither --strategy nor a 7R recommendation")
    waves_path = out / "waves.json"
    rc, so, se = r.tool("waves", "tools/modernization/app_wave_planner.py", "--app-id", app, "--out", str(waves_path))
    waves = json.loads(waves_path.read_text()) if rc == 0 and waves_path.is_file() else None
    r.note("waves", bool((waves or {}).get("waves")), "migration.waves", None if waves else _why(se, so),
           waves=len((waves or {}).get("waves") or []), basis=(waves or {}).get("basis"))

    bundle_path = out / f"{name}.apptwin.json"
    args = ["tools/modernization/apptwin_export.py", "--app-id", app, "--out", str(bundle_path)]
    if n_feats:
        args += ["--static-features", str(feats_path)]
    if seven:
        args += ["--seven-r", str(out / "seven_r.json")]
    if aiify:
        args += ["--aiify", str(out / "aiify.json")]
    if jobs:
        args += ["--refactor", str(ref_path)]
    if (waves or {}).get("waves"):
        args += ["--waves", str(waves_path)]
    for p in procs:
        args += ["--process", str(p)]
    if at:
        args += ["--at", at]
    rc, so, se = r.tool("export", *args)
    if rc != 0 or not bundle_path.is_file():
        raise PipelineError(f"apptwin_export: {_why(se, so)}")
    bundle = json.loads(bundle_path.read_text())
    produced = {k for k, v in bundle.items() if v not in (None, [], {})}
    unproduced = {
        "consumers": "the exporter never carries consumers",
        "integrity": "no SIPA assessment was run",
        "ivv": "no IV&V assessment was run", "sbd": "no Secure by Design assessment was run",
        "rtm": "no requirements traceability was run", "coherence": "no coherence check was run",
        "readiness": "no readiness assessment was run"}
    for s in r.steps:
        if s.get("section") and not s["ok"]:
            unproduced[s["section"]] = s.get("reason") or "the step produced nothing"
    gaps = {k: unproduced.get(k, "no step produced it") for k in sorted(set(bundle) - produced - {"schema"})}
    if (bundle.get("migration") or {}) and not (bundle["migration"].get("waves")):
        gaps["migration.waves"] = next((s.get("reason") for s in r.steps if s["step"] == "waves" and not s["ok"]),
                                       None) or "no wave was planned, so ICDEV[OS]'s migration-plan gate will block"
    report = {"application": name, "source": source.name, "app_id": app, "project_id": project,
              "bundle": bundle_path.name, "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
              "produced": sorted(produced - {"schema", "producer", "produced_at"}), "gaps": gaps, "steps": r.steps,
              "at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out / "gaps.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, help="the application's source directory (read only)")
    ap.add_argument("--name", required=True, help="the application's id in the bundle")
    ap.add_argument("--scratch-db", required=True, help="a NEW sqlite file for this run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--strategy", help="the 7R strategy to plan for (default: the 7R recommendation)")
    ap.add_argument("--at", help="produced_at (ISO 8601) for a reproducible bundle")
    ap.add_argument("--no-model", action="store_true", help="run no step that calls a language model (Process-Ify)")
    a = ap.parse_args(argv)
    try:
        rep = pipeline(Path(a.source), a.name, Path(a.scratch_db), Path(a.out), strategy=a.strategy, at=a.at,
                       no_model=a.no_model)
    except (PipelineError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1
    print(json.dumps({k: rep[k] for k in ("application", "bundle", "bundle_sha256", "produced", "gaps")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
