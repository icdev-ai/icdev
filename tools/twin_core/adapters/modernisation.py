# CUI // SP-CTI — Modernisation governance twin (icx-mdz-01)
"""Governance twin over application modernisation, canvas key ``mdc`` (the migration canvas, where its page lives).

It observes the six modernisation features in ``args/feature_conformance.yaml``: the 7R assessment, AI-Ify,
Process-Ify, app waves and CAM refactor jobs, cloud-native targets, and the ICDEV[OS] exporter. Their state is
assessments and plans, the decisions people took on them (``wf_decision_steps``), CAM jobs and their files, observed
drafts, and the vendored bundle contract, so that is what it reads:

* **7R** -- an application's latest assessment with no strategy recorded after ``strategy_record_warn_days``; a
  migration plan whose strategy is not the one a person recorded for the application, or that exists with none
  recorded. A recorded strategy is a proposal to ICDEV[OS], so a plan that contradicts it was not planned on it.
* **AI-Ify** -- a promotion decision in ``aiify_hitl_decisions`` with no decided ``decl-wft-mdz-aiify-promote`` step
  for it: a decision taken outside the declared workflow (Send to Kanban reads that table).
* **Process-Ify** -- an observed draft still waiting for a person after ``draft_review_warn_days``.
* **CAM jobs** -- a completed refactor or cloud-native job whose generated output nobody reviewed after
  ``job_review_warn_days``; a file a completed job lists that is no longer on disk (the bundle would not bind it).
* **Exporter** -- the vendored ``icos-apptwin/v1`` contract still hashes to the digest the exporter pins.

A source that cannot be read is an unverified check, never a pass. Threads: ``mdz_assessment:<id>``,
``migration_plan:<id>``, ``aiify_decision:<id>``, ``studio_workflow:<id>``, ``mc_refactor_job:<id>``,
``config:<path>@<sha256>``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from tools.twin_core.governance import (
    BASE_DIR,
    GovernanceTwinAdapter,
    age_hours,
    check,
    close_quietly,
    finding,
    load_policy,
    now_iso,
    rows_as_dicts,
    ts_text,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin

SCHEMA_REL = "context/icos/apptwin.schema.json"


def _decided(template_id: str) -> list[dict]:
    from tools.modernization import decisions

    return sorted(({"subject": s, "decision": r["decision"], "affirmative": r["affirmative"],
                    "strategy": (r.get("payload") or {}).get("strategy"), "step_id": r["step_id"],
                    "at": ts_text(r["created_at"])}
                   for s, r in decisions.decided(template_id).items()), key=lambda r: r["subject"])


@register_twin
class ModernisationGovernanceTwin(GovernanceTwinAdapter):
    canvas_key = "mdc"
    display_name = "Application modernisation"
    # A literal: the conformance audit reads it without importing this module.
    observes = ("mdz.seven_r", "mdz.aiify", "mdz.processify", "mdz.waves_cam", "mdz.cloud_native",
                "mdz.exporter")

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    # -- collect -------------------------------------------------------------

    def collect(self) -> dict:
        from tools.db.storage import get_canvas_connection, table_exists
        from tools.modernization import decisions

        policy = load_policy("modernisation")
        limit = int(policy.get("scan_limit") or 500)
        ev: dict = {"canvas": self.canvas_key, "target_id": "fleet", "observed_at": now_iso(),
                    "policy": policy, "assessments": None, "plans": None, "strategy_records": None,
                    "aiify_decisions": None, "aiify_steps": None, "drafts": None, "jobs": None,
                    "job_reviews": None, "schema": None, "errors": {}}
        conn = None
        try:
            conn = get_canvas_connection()
            if not table_exists(conn, "migration_assessments"):
                ev["errors"]["assessments"] = "migration_assessments does not exist"
            else:
                cols = ("id", "legacy_app_id", "recommended_strategy", "assessed_at")
                rows = rows_as_dicts(conn.execute(
                    "SELECT id, legacy_app_id, recommended_strategy, assessed_at FROM migration_assessments "
                    "WHERE assessment_scope = 'application' ORDER BY assessed_at DESC LIMIT %s", (limit,)), cols)
                latest: dict[str, dict] = {}
                for r in rows:             # newest first: the first per application is its latest
                    latest.setdefault(str(r["legacy_app_id"]), {
                        "id": str(r["id"]), "app": str(r["legacy_app_id"]),
                        "recommended": str(r["recommended_strategy"] or ""), "at": ts_text(r["assessed_at"])})
                ev["assessments"] = sorted(latest.values(), key=lambda r: r["app"])
            if not table_exists(conn, "migration_plans"):
                ev["errors"]["plans"] = "migration_plans does not exist"
            else:
                cols = ("id", "legacy_app_id", "strategy", "status", "created_at")
                ev["plans"] = sorted(
                    ({"id": str(r["id"]), "app": str(r["legacy_app_id"]), "strategy": str(r["strategy"] or ""),
                      "status": str(r["status"] or ""), "at": ts_text(r["created_at"])}
                     for r in rows_as_dicts(conn.execute(
                         "SELECT id, legacy_app_id, strategy, status, created_at FROM migration_plans "
                         "ORDER BY created_at DESC LIMIT %s", (limit,)), cols)),
                    key=lambda r: r["id"])
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"].setdefault("assessments", str(exc))
        finally:
            close_quietly(conn)

        try:
            ev["strategy_records"] = _decided(decisions.TEMPLATE_7R)
            ev["aiify_steps"] = _decided(decisions.TEMPLATE_AIIFY_PROMOTE)
            ev["job_reviews"] = _decided(decisions.TEMPLATE_CAM)
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["ledger"] = str(exc)

        conn = None
        try:
            from tools.aiify.db.init_db import get_connection as aiify_conn

            conn = aiify_conn()
            cols = ("id", "source_type", "source_id", "decision", "decided_at")
            ev["aiify_decisions"] = sorted(
                ({"id": str(r["id"]), "subject": f"{r['source_type']}:{r['source_id']}",
                  "decision": str(r["decision"]), "at": ts_text(r["decided_at"])}
                 for r in rows_as_dicts(conn.execute(
                     "SELECT id, source_type, source_id, decision, decided_at FROM aiify_hitl_decisions "
                     "ORDER BY id DESC LIMIT %s", (limit,)), cols)),
                key=lambda r: r["subject"])
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["aiify"] = str(exc)
        finally:
            close_quietly(conn)

        try:
            from tools.workflow_canvas.observed_processify import list_drafts

            ev["drafts"] = sorted(({"id": d["workflow_id"], "state": str((d["review"] or {}).get("state") or ""),
                                    "at": ts_text(d["created_at"])} for d in list_drafts(limit=limit)),
                                  key=lambda r: r["id"])
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["drafts"] = str(exc)

        conn = None
        try:
            import json as _json

            from tools.migration_canvas.db.init_db import get_connection as mc_conn

            conn = mc_conn()
            cols = ("id", "project_id", "refactor_type", "params_json", "status", "artifacts_json", "completed_at")
            jobs = []
            for r in rows_as_dicts(conn.execute(
                    "SELECT id, project_id, refactor_type, params_json, status, artifacts_json, completed_at "
                    "FROM mc_refactor_jobs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT %s",
                    (limit,)), cols):
                arts = _json.loads(r["artifacts_json"] or "[]")
                files = [str(a.get("path") or "") for a in arts if isinstance(a, dict)]
                jobs.append({"id": str(r["id"]), "project_id": str(r["project_id"]),
                             "type": str(r["refactor_type"]),
                             "scaffold": str((_json.loads(r["params_json"] or "{}") or {}).get("scaffold_type") or ""),
                             "at": ts_text(r["completed_at"]), "files": len(files),
                             "missing": sorted(Path(f).name for f in files if f and not Path(f).is_file())})
            ev["jobs"] = sorted(jobs, key=lambda r: r["id"])
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["jobs"] = str(exc)
        finally:
            close_quietly(conn)

        try:
            from tools.modernization.apptwin_export import SCHEMA_SHA256

            raw = (BASE_DIR / SCHEMA_REL).read_bytes()
            ev["schema"] = {"path": SCHEMA_REL, "sha256": hashlib.sha256(raw).hexdigest(), "pinned": SCHEMA_SHA256}
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["schema"] = str(exc)
        return ev

    # -- judge (pure) --------------------------------------------------------

    def judge(self, evidence: dict) -> dict:
        errors = evidence.get("errors") or {}
        policy = evidence.get("policy") or {}
        at = evidence.get("observed_at") or ""
        checks, findings = [], []
        ledger_ok = "ledger" not in errors

        def readable(key: str, label: str, value) -> bool:
            ok = value is not None and key not in errors
            checks.append(check(f"mdz.{key}", label, ok, errors.get(key) or f"{label} not read"))
            return ok

        records = {r["subject"]: r for r in evidence.get("strategy_records") or []}
        checks.append(check("mdz.ledger", "Decision ledger (wf_decision_steps)", ledger_ok,
                            errors.get("ledger") or "the ledger was not read"))

        if readable("assessments", "7R assessments", evidence.get("assessments")) and ledger_ok:
            warn_h = float(policy.get("strategy_record_warn_days", 14)) * 24
            for a in evidence["assessments"]:
                age = age_hours(a["at"], at)
                if a["id"] not in records and age is not None and age >= warn_h:
                    findings.append(finding(
                        "mdz.seven_r.unrecorded", "medium",
                        f"{a['app']}: 7R recommends {a['recommended'] or '?'} and no strategy is recorded",
                        thread_id=f"mdz_assessment:{a['id']}",
                        detail=f"assessed {a['at']} ({age / 24:.0f} days ago)",
                        recommendation="Record the strategy ICDEV proposes, or decline to, on /migration."))
        by_app = {}
        for a in evidence.get("assessments") or []:
            rec = records.get(a["id"])
            if rec:
                by_app[a["app"]] = rec
        if readable("plans", "Migration plans", evidence.get("plans")) and ledger_ok:
            for p in evidence["plans"]:
                rec = by_app.get(p["app"])
                if rec is None:
                    findings.append(finding(
                        "mdz.seven_r.plan_unrecorded", "medium",
                        f"Plan {p['id']} ({p['strategy']}) for {p['app']} has no recorded strategy behind it",
                        thread_id=f"migration_plan:{p['id']}",
                        recommendation="Record the strategy on /migration; plan only when ICDEV[OS] may-plan allows."))
                elif rec["affirmative"] and rec["strategy"] and rec["strategy"] != p["strategy"]:
                    findings.append(finding(
                        "mdz.seven_r.plan_diverges", "high",
                        f"Plan {p['id']} is {p['strategy']}; the strategy recorded for {p['app']} is {rec['strategy']}",
                        thread_id=f"migration_plan:{p['id']}",
                        detail=f"recorded by step {rec['step_id']}",
                        recommendation="Re-plan on the recorded strategy, or record a new one with its reason."))

        if readable("aiify", "AI-Ify promotion decisions", evidence.get("aiify_decisions")) and ledger_ok:
            stepped = {r["subject"] for r in evidence.get("aiify_steps") or []}
            for d in evidence["aiify_decisions"]:
                if d["subject"] not in stepped:
                    findings.append(finding(
                        "mdz.aiify.undeclared_decision", "high",
                        f"AI-Ify {d['decision']} of {d['subject']} has no declared workflow step",
                        thread_id=f"aiify_decision:{d['id']}", detail=f"decided {d['at']}",
                        recommendation="Take the decision again from /ai-ify (decl-wft-mdz-aiify-promote)."))

        if readable("drafts", "Observed drafts", evidence.get("drafts")):
            warn_h = float(policy.get("draft_review_warn_days", 7)) * 24
            for d in evidence["drafts"]:
                age = age_hours(d["at"], at)
                if d["state"] == "pending_hitl" and age is not None and age >= warn_h:
                    findings.append(finding(
                        "mdz.processify.observed_pending", "medium",
                        f"Observed draft {d['id']} has waited {age / 24:.0f} days for review",
                        thread_id=f"studio_workflow:{d['id']}",
                        recommendation="Approve or reject it on /workflow-canvas/processify."))

        if readable("jobs", "CAM refactor and cloud-native jobs", evidence.get("jobs")) and ledger_ok:
            reviewed = {r["subject"] for r in evidence.get("job_reviews") or []}
            warn_h = float(policy.get("job_review_warn_days", 7)) * 24
            for j in evidence["jobs"]:
                kind = j["scaffold"] or j["type"]
                if j["missing"]:
                    findings.append(finding(
                        "mdz.cam.output_missing", "high",
                        f"Job {j['id']} ({kind}) lists {len(j['missing'])} file(s) no longer on disk",
                        thread_id=f"mc_refactor_job:{j['id']}", detail=", ".join(j["missing"][:10]),
                        recommendation="Re-run the job; a missing file cannot be bound into the bundle."))
                age = age_hours(j["at"], at)
                if j["files"] and j["id"] not in reviewed and age is not None and age >= warn_h:
                    findings.append(finding(
                        "mdz.cam.unreviewed", "medium",
                        f"Job {j['id']} ({kind}) wrote {j['files']} generated file(s) nobody has reviewed",
                        thread_id=f"mc_refactor_job:{j['id']}",
                        recommendation="Approve or reject its output on /migration-canvas/modernisation."))

        schema = evidence.get("schema")
        if readable("schema", "Vendored icos-apptwin/v1 contract", schema) and schema["sha256"] != schema["pinned"]:
            findings.append(finding(
                "mdz.exporter.contract_changed", "critical",
                "The vendored icos-apptwin/v1 contract does not hash to the digest the exporter pins",
                thread_id=f"config:{schema['path']}@{schema['sha256'][:12]}",
                detail=f"pinned {schema['pinned'][:12]}, file {schema['sha256'][:12]}",
                recommendation="Re-vendor the contract from ICDEV[OS] and update SCHEMA_SHA256 together."))

        summary = {
            "applications_assessed": len(evidence.get("assessments") or []),
            "strategies_recorded": len(records),
            "plans": len(evidence.get("plans") or []),
            "aiify_decisions": len(evidence.get("aiify_decisions") or []),
            "observed_drafts_pending": sum(1 for d in evidence.get("drafts") or [] if d["state"] == "pending_hitl"),
            "jobs_completed": len(evidence.get("jobs") or []),
            "jobs_reviewed": len({r["subject"] for r in evidence.get("job_reviews") or []}),
        }
        return {"checks": checks, "findings": findings, "summary": summary}
