#!/usr/bin/env python3
# CUI // SP-CTI
"""Run ICDEV[IT]'s modernisation tooling over one real application, against a scratch database, and export its
ICDEV[OS] app twin bundle with a gap report (ICDEV[OS] Phase 254, docs/programmes/icdev-os-modsamples.md there).

    python tools/modernization/sample_pipeline.py --source DIR --name daytrader7 --scratch-db /tmp/x/icdev.db \\
        --out /tmp/x/daytrader7 [--strategy rehost] [--at 2026-11-12T00:00:00Z]

Every step is the existing tool, run as a subprocess with the scratch database in its environment:

     1. project_create             a scratch project
     2. legacy_analyzer            --register, then --analyze
     3. architecture_extractor     --extract db-schema          (the bundle's data)
     4. seven_r_assessor           --json                       (seven_r)
     5. db_migration_planner       --type all --target postgresql (artefacts only)
     6. version_migrator           the declared language level -> the current one (artefacts only)
     7. framework_migrator         EJB / Struts / WCF / WebForms (artefacts only)
     8. aiify.engine scan          (aiify)
     9. processify --from-doc      the README (processes); skipped by --no-model, the only step that calls a model
    10. monolith_decomposer        --create-plan with the strategy (migration)
    11. apptwin_export             every section a step produced

The source is UNTRUSTED DATA: it is read, never built or run, and a step that would need the application running
(observed inventory) is reported, not attempted. A section no step produced stays null in the bundle and is listed
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

BASE = Path(__file__).resolve().parents[2]
PY = sys.executable
UPGRADE_TO = {"java": "17", "csharp": "8.0"}
FRAMEWORK_TO = {"ejb": "spring", "struts": "spring-boot", "wcf": "aspnet-core-grpc", "webforms": "razor"}


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


def _digests(root: Path) -> list[dict]:
    if not root.exists():
        return []
    return [{"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob("*")) if p.is_file()]


class Run:
    def __init__(self, scratch_db: Path, out: Path):
        self.out, self.steps = out, []
        self.env = {**os.environ, "ICDEV_DB_PATH": str(scratch_db), "ICDEV_STORAGE_BACKEND": "sqlite",
                    "ICDEV_CANVAS_STORAGE_BACKEND": "sqlite", "AIIFY_STORAGE_BACKEND": "sqlite",
                    "AIIFY_DB_PATH": str(scratch_db.with_name("aiify_canvas.db")), "PYTHONPATH": str(BASE)}

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
    with sqlite3.connect(scratch_db) as c:
        row = c.execute("SELECT id FROM legacy_applications WHERE name = ?", (name,)).fetchone()
    if rc != 0 or row is None:
        raise PipelineError(f"legacy_analyzer --register: {_why(se, so)}")
    app = row[0]
    rc, so, se = r.tool("analyze", "tools/modernization/legacy_analyzer.py", "--analyze", "--project-id", project,
                        "--app-id", app, "--json")
    if rc != 0:
        raise PipelineError(f"legacy_analyzer --analyze: {_why(se, so)}")
    with sqlite3.connect(scratch_db) as c:
        c.row_factory = sqlite3.Row
        a = dict(c.execute("SELECT * FROM legacy_applications WHERE id = ?", (app,)).fetchone())
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE legacy_app_id = ?", (app,)).fetchone()[0]
                  for t in ("legacy_components", "legacy_apis", "legacy_dependencies")}
    lang, fw = a["primary_language"], (a["framework"] or "").strip().lower()
    r.note("analyze", True, app_id=app, language=lang, language_version=a["language_version"], framework=a["framework"],
           **counts)

    rc, so, se = r.tool("db-schema", "tools/modernization/architecture_extractor.py", "--app-id", app, "--extract",
                        "db-schema", "--source-path", str(source))
    with sqlite3.connect(scratch_db) as c:
        tables = c.execute("SELECT COUNT(DISTINCT table_name) FROM legacy_db_schemas WHERE legacy_app_id = ?",
                           (app,)).fetchone()[0]
    r.note("db-schema", rc == 0 and tables > 0, "data", None if tables else
           (f"no ORM model or SQL DDL the extractor reads ({_why(se, so)})" if rc else "no ORM model or SQL DDL the extractor reads"),
           tables=tables)

    rc, so, se = r.tool("seven-r", "tools/modernization/seven_r_assessor.py", "--project-id", project, "--app-id", app, "--json")
    seven = _last_json(so) if rc == 0 else None
    if seven:
        (out / "seven_r.json").write_text(json.dumps(seven, indent=2))
    r.note("seven-r", bool(seven), "seven_r", None if seven else _why(se, so),
           recommended=(seven or {}).get("recommended_strategy"))

    db_out = out / "artefacts" / "db"
    rc, so, se = r.tool("db-plan", "tools/modernization/db_migration_planner.py", "--app-id", app, "--target", "postgresql",
                        "--source-path", str(source), "--output-dir", str(db_out), "--type", "all", "--json")
    r.note("db-plan", rc == 0 and bool(_digests(db_out)), None, None if rc == 0 else _why(se, so),
           artefacts=_digests(db_out))

    ver = a["language_version"]
    if lang in UPGRADE_TO and ver:
        v_out = out / "artefacts" / "version"
        frm = {"framework-4": "4.8"}.get(ver, ver)
        rc, so, se = r.tool("version", "tools/modernization/version_migrator.py", "--source", str(source), "--output",
                            str(v_out), "--language", lang, "--from", frm, "--to", UPGRADE_TO[lang], "--json")
        res = _last_json(so) if rc == 0 else None
        r.note("version", rc == 0, None, None if rc == 0 else _why(se, so), from_version=frm, to_version=UPGRADE_TO[lang],
               summary={k: v for k, v in (res or {}).items() if not isinstance(v, (list, dict))},
               artefacts=len(_digests(v_out)))
    else:
        r.note("version", False, None, f"no upgrade rule for {lang} at a declared level ({ver or 'none declared'})")

    if fw in FRAMEWORK_TO:
        f_out = out / "artefacts" / "framework"
        rc, so, se = r.tool("framework", "tools/modernization/framework_migrator.py", "--source", str(source), "--output",
                            str(f_out), "--from", fw, "--to", FRAMEWORK_TO[fw], "--report", "--json")
        res = _last_json(so) if rc == 0 else None
        r.note("framework", rc == 0, None, None if rc == 0 else _why(se, so), migration=f"{fw} -> {FRAMEWORK_TO[fw]}",
               summary={k: v for k, v in (res or {}).items() if not isinstance(v, (list, dict))},
               artefacts=len(_digests(f_out)))
    else:
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

    chosen = strategy or (seven or {}).get("recommended_strategy")
    if chosen:
        rc, so, se = r.tool("plan", "tools/modernization/monolith_decomposer.py", "--app-id", app, "--create-plan",
                            "--project-id", project, "--strategy", chosen, "--target-arch", "modular_monolith", "--json")
        r.note("plan", rc == 0, "migration", None if rc == 0 else _why(se, so), strategy=chosen,
               strategy_from="--strategy" if strategy else "the 7R recommendation")
    else:
        r.note("plan", False, "migration", "no strategy: neither --strategy nor a 7R recommendation")

    bundle_path = out / f"{name}.apptwin.json"
    args = ["tools/modernization/apptwin_export.py", "--app-id", app, "--out", str(bundle_path)]
    if seven:
        args += ["--seven-r", str(out / "seven_r.json")]
    if aiify:
        args += ["--aiify", str(out / "aiify.json")]
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
        "features": "observed inventory needs the application running; the source is never run here",
        "consumers": "the exporter never carries consumers",
        "refactor": "no CAM refactor job ran: the migrators above ran directly, and their outputs are artefacts here",
        "integrity": "no SIPA assessment was run",
        "ivv": "no IV&V assessment was run", "sbd": "no Secure by Design assessment was run",
        "rtm": "no requirements traceability was run", "coherence": "no coherence check was run",
        "readiness": "no readiness assessment was run"}
    for s in r.steps:
        if s.get("section") and not s["ok"]:
            unproduced[s["section"]] = s.get("reason") or "the step produced nothing"
    gaps = {k: unproduced.get(k, "no step produced it") for k in sorted(set(bundle) - produced - {"schema"})}
    if (bundle.get("migration") or {}) and not (bundle["migration"].get("waves")):
        gaps["migration.waves"] = ("the wave planner plans server inventory sessions, not an application's migration "
                                   "plan: no waves, so ICDEV[OS]'s migration-plan gate will block")
    report = {"application": name, "source": str(source), "app_id": app, "project_id": project,
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
