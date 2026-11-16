# CUI // SP-CTI
"""Seed what the modernisation write e2e needs on screen (icx-mdz-01).

The e2e gate (``tools/conformance/feature_audit.py --e2e-gate``) holds that every write
the audit credits to a page was SENT BY A PAGE in the run. Some modernisation controls
appear only when there is something to act on. This puts it there through the SAME tools
the product uses, never a hand-written INSERT of derived state:

* a project (``project_create.create_project``) and the synthetic legacy application in
  ``tests/e2e/fixtures/mdz01_app`` registered and analysed by ``legacy_analyzer`` (its
  components and dependencies, so the page has waves and the 7R assessor has a profile);
* CAM's cloud-native jobs over that source (``cloud_native_jobs.run``), so the
  Modernisation page has a completed job whose output a person can approve;
* one observed Process-Ify draft (``observed_processify.build_draft`` + ``save_draft``)
  from a capture record written as REA reports one, so the Process-Ify page offers
  Approve / Reject.

The 7R assessment itself is NOT seeded: the test presses Assess on /migration.
Everything is named ``mdz01-e2e-*`` so it cannot be mistaken for real data. Synthetic.
The server must be on a throwaway database (globalSetup asserts it). Prints JSON.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "tests" / "e2e" / "fixtures" / "mdz01_app"


def seed_application(run: str) -> dict:
    from tools.modernization import legacy_analyzer
    from tools.project.project_create import create_project

    project = create_project(f"mdz01-e2e-project-{run}", description="icx-mdz-01 e2e fixture (synthetic)",
                             skip_scaffold=True)
    project_id = project.get("project_id") or project.get("id")
    app = legacy_analyzer.register_application(project_id, f"mdz01-e2e-shop-{run}", str(SOURCE),
                                               description="icx-mdz-01 e2e fixture (synthetic)")
    app_id = app.get("app_id") or app.get("id")
    analysed = legacy_analyzer.analyze_python(app_id, str(SOURCE))
    return {"project_id": project_id, "app_id": app_id, "analysed": analysed}


def seed_cloud_native(app_id: str) -> dict:
    import os

    from tools.modernization import cloud_native_jobs

    # Generated files go to the run's scratch, never data/ (the jobs record where they wrote).
    os.environ.setdefault("CAM_ARTIFACTS_DIR", str(ROOT / ".tmp" / "mdz01-e2e-cam"))

    rep = cloud_native_jobs.run(app_id)
    return {"project_id": rep["project_id"],
            "jobs": [{"id": j["id"], "scaffold": j["scaffold"], "status": j["status"]} for j in rep["jobs"]]}


def seed_observed_draft(run: str) -> str:
    from tools.workflow_canvas import observed_processify as op

    capture = {"status": "ok", "kind": "process",
               "scenario": {"executable": str(SOURCE / "shop" / "orders.py")},
               "provenance": {"authorization_basis": "owned", "synthetic": True},
               "record": {"evidence_id": f"mdz01-e2e-evidence-{run}",
                          "normalized_result": {"steps": [{"label": "open an order"},
                                                          {"label": "price the cart"},
                                                          {"label": "mark the priority"}]}}}
    return op.save_draft(op.build_draft(capture, name=f"mdz01-e2e observed draft {run}"))


def main() -> int:
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    app = seed_application(run)
    out = {**app, "source": str(SOURCE), "cloud_native": seed_cloud_native(app["app_id"]),
           "observed_workflow_id": seed_observed_draft(run)}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
