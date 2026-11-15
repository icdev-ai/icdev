# CUI // SP-CTI
"""Run a legacy application's migrators as CAM refactor jobs (ICDEV[OS] osx-fth-13).

The sample pipeline ran the version, framework and database migrators directly, so their outputs were loose files and
the bundle's ``refactor`` section stayed null. This module runs the same migrators through CAM
(``tools/migration_canvas/cam_refactor_engine.py``), so each run is a job with a status and its artefacts:

1. a CAM project for the application in the canvas database (``MC_DB_PATH``; a scratch file in the pipeline), with one
   ``mc_app_inventory`` row whose id IS the legacy application's id (language, declared level, framework, source);
2. CAM's own rules (``context/migration/refactor_rules.yaml``) queue the version and framework jobs that apply;
3. when the analyser extracted a schema, one database job (``db_migration_planner``'s full run over that schema --
   never CAM's generic scaffold) is queued explicitly, since no rule sees the schema;
4. every job runs; its artefacts are written under ``CAM_ARTIFACTS_DIR`` and listed at every depth.

The source is read, never built or run. What the jobs write is GENERATED code, unreviewed: the exporter binds each file
by its SHA-256 and labels the job ``unreviewed``. The output is what ``apptwin_export.py --refactor`` carries.

    python tools/modernization/refactor_jobs.py --app-id <legacy app id> [--strategy rehost] [--out refactor.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TOOL = "icdev-it/cam_refactor_engine"
DB_TARGET = "postgresql"
STRATEGIES = ("rehost", "replatform", "refactor", "rearchitect", "repurchase", "retire", "retain")


def _legacy(app_id: str) -> tuple[dict, int]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        row = conn.execute("SELECT id, name, primary_language, language_version, framework, source_path "
                           "FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
        if row is None:
            raise ValueError(f"no legacy application {app_id!r}")
        tables = conn.execute("SELECT COUNT(DISTINCT table_name) AS n FROM legacy_db_schemas WHERE legacy_app_id = %s",
                              (app_id,)).fetchone()["n"]
        return dict(row), int(tables or 0)
    finally:
        conn.close()


def project(app: dict, strategy: str | None) -> str:
    """The application's CAM project and inventory row (created once; the same ids on a re-run)."""
    from tools.migration_canvas.db.init_db import get_connection, init_db
    init_db()
    sid, pid = f"mcs-{app['id']}", f"mcp-{app['id']}"
    conn = get_connection()
    try:
        if conn.execute("SELECT id FROM mc_projects WHERE id = %s", (pid,)).fetchone() is None:
            conn.execute("INSERT INTO mc_srv_sessions (id, migration_type, tgt_platform, notes) VALUES (%s, %s, %s, %s)",
                         (sid, "application", "", f"legacy application {app['id']} (ICDEV[OS] osx-fth-13)"))
            conn.execute("INSERT INTO mc_projects (id, name, description, mc_session_id) VALUES (%s, %s, %s, %s)",
                         (pid, f"refactor {app['name']}", "the legacy application's migrators, as CAM jobs", sid))
            conn.execute("INSERT INTO mc_app_inventory (id, session_id, name, version, language, framework, source_repo, "
                         "migration_strategy) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                         (app["id"], sid, app["name"], app.get("language_version") or "",
                          (app.get("primary_language") or "").lower(), (app.get("framework") or "").strip().lower(),
                          app.get("source_path") or "", strategy if strategy in STRATEGIES else None))
            conn.commit()
    finally:
        conn.close()
    return pid


def run(app_id: str, *, strategy: str | None = None) -> dict:
    from tools.migration_canvas import cam_refactor_engine as cam
    app, tables = _legacy(app_id)
    pid = project(app, strategy)
    queued = cam.dispatch(pid)
    if tables and not any(j["refactor_type"] == "db_ddl_generation" for j in cam.get_jobs(pid)):
        queued.append(cam.queue(pid, app["id"], {
            "type": "db_ddl_generation", "scope": "full", "target_db": DB_TARGET,
            "description": f"db_migration_planner over the {tables} extracted table(s), to {DB_TARGET}"}, "icdev-it/refactor_jobs"))
    for j in queued:
        cam.run_job(j["id"])
    jobs = sorted(cam.get_jobs(pid), key=lambda j: (j["refactor_type"], j["created_at"], j["id"]))
    return {"tool": TOOL, "app_id": app_id, "project_id": pid, "schema_tables": tables,
            "jobs": [{k: j.get(k) for k in ("id", "component_name", "refactor_type", "status", "result_summary",
                                            "error_message", "output_path", "artifacts")} for j in jobs]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a legacy application's migrators as CAM refactor jobs")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--strategy", help="the 7R strategy recorded on the CAM inventory row")
    ap.add_argument("--out", type=Path, help="write the jobs here (default: stdout)")
    a = ap.parse_args(argv)
    try:
        rep = run(a.app_id, strategy=a.strategy)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), "jobs": len(rep["jobs"]),
                          "by_status": {s: sum(j["status"] == s for j in rep["jobs"]) for s in
                                        sorted({j["status"] for j in rep["jobs"]})}}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
