# CUI // SP-CTI
"""Run a legacy application's cloud-native jobs through CAM (ICDEV[OS] osx-fth-14).

The application's CAM project is the one ``refactor_jobs.py`` made (the same ids; created here if it was not). CAM's
``cloud_native`` rules (``context/migration/refactor_rules.yaml``) queue, for an inventory row with a source:

    container_definition    Containerfile and deployment.yaml
    config_externalisation  config-findings.json, env.mapping.json, config.env.example
    twelve_factor_report    twelve-factor.json
    event_contract          asyncapi.yaml and event-contract.json (EJB applications: their message-driven beans)

Each job reads the source (``tools/modernization/cloud_native.py``), never builds or runs it, and writes generated,
unreviewed files under ``CAM_ARTIFACTS_DIR``. The output is what ``apptwin_export.py --cloud-native`` carries; the
targets given here (ICDEV[OS] examples/modernisation/apps.yaml) are carried with it, for the exporter to map each one
to what proposes it.

    python tools/modernization/cloud_native_jobs.py --app-id <legacy app id> [--target-cloud containerised ...]
        [--target-ai "demand forecast" ...] [--out cloud_native.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TOOL = "icdev-it/cloud_native"
RULE_SET = "cloud_native"


def run(app_id: str, *, targets: dict | None = None) -> dict:
    from tools.migration_canvas import cam_refactor_engine as cam
    from tools.modernization import refactor_jobs
    app, _tables = refactor_jobs._legacy(app_id)
    pid = refactor_jobs.project(app, None)
    for j in cam.dispatch(pid, rule_set=RULE_SET):
        cam.run_job(j["id"])
    jobs = [j for j in cam.get_jobs(pid) if (json.loads(j.get("params_json") or "{}").get("scaffold_type") or "")
            in ("container_definition", "config_externalisation", "twelve_factor_report", "event_contract")]
    jobs = sorted(jobs, key=lambda j: (json.loads(j["params_json"])["scaffold_type"], j["created_at"], j["id"]))
    return {"tool": TOOL, "app_id": app_id, "project_id": pid,
            "targets": {"cloud": list((targets or {}).get("cloud") or []), "ai": list((targets or {}).get("ai") or [])},
            "jobs": [{"id": j["id"], "scaffold": json.loads(j["params_json"])["scaffold_type"],
                      **{k: j.get(k) for k in ("component_name", "refactor_type", "status", "result_summary",
                                               "error_message", "output_path", "artifacts", "completed_at")}}
                     for j in jobs]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a legacy application's cloud-native jobs through CAM")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--target-cloud", action="append", default=[], help="one item of the plan's cloud target; repeatable")
    ap.add_argument("--target-ai", action="append", default=[], help="one item of the plan's AI target; repeatable")
    ap.add_argument("--out", type=Path, help="write the jobs here (default: stdout)")
    a = ap.parse_args(argv)
    try:
        rep = run(a.app_id, targets={"cloud": a.target_cloud, "ai": a.target_ai})
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
