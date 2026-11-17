# CUI // SP-CTI — Modernisation governance twin (icx-mdz-01)
"""Governance twin over application modernisation, canvas key ``mdc`` (the migration canvas, where its page lives).

It observes the six modernisation features in ``args/feature_conformance.yaml``: the 7R assessment, AI-Ify,
Process-Ify, app waves and CAM refactor jobs, cloud-native targets, and the ICDEV[OS] exporter. Their state is
assessments and plans, the decisions people took on them (``wf_decision_steps``), CAM jobs and their files, observed
drafts, and the vendored bundle contract, so that is what it reads:

* **7R** -- an application's latest assessment with no strategy recorded after ``strategy_record_warn_days``; a
  migration plan whose strategy is not the one a person recorded for the application, or that exists with none
  recorded. A recorded strategy is a proposal to ICDEV[OS], so a plan that contradicts it was not planned on it.
  (icx-mdz-02) A latest assessment that recommends nothing -- no strategy reached the catalogue's minimum confidence --
  or whose recommendation rests on less than ``seven_r_confidence_warn`` (or that predates criteria keyed by name);
  and a plan attempted without ICDEV[OS]'s may-plan (``plan_gate`` refused it and audited the refusal), until a plan
  for that application exists after it.
* **AI-Ify** -- a promotion decision in ``aiify_hitl_decisions`` with no decided ``decl-wft-mdz-aiify-promote`` step
  for it: a decision taken outside the declared workflow (Send to Kanban reads that table).
* **Process-Ify** -- an observed draft still waiting for a person after ``draft_review_warn_days``.
* **CAM jobs** -- a completed refactor or cloud-native job whose generated output nobody reviewed after
  ``job_review_warn_days``; a file a completed job lists that is no longer on disk (the bundle would not bind it).
* **Exporter** -- the vendored ``icos-apptwin/v1`` contract still hashes to the digest the exporter pins.
  (icx-mdz-03) Each application's bundle as the exporter would write it now (``modernisation.bundle_preview``, no
  model): one that would carry no features; extracted processes no person has reviewed at their step-signature digest
  (``decl-wft-mdz-process-review``; stale counts as unreviewed); and the parts ICDEV[OS] plans on that it would not
  carry (``bundle_required``: waves, refactor jobs, cloud-native parts), each with the exporter's reason.
  (icx-mdz-04) A bundle that fails the pinned contract (1.189.0) or ICDEV[OS]'s agreement rules -- ICDEV[OS] would
  refuse it whole -- and one whose application has no declared security domain (``args/modernisation_environments.yaml``),
  so ICDEV[OS] can only class its overlaps unverified.
  (icx-mdz-05) A team capacity declared for an application (``args/modernisation_capacity.yaml``) that no person has
  approved at its digest (``decl-wft-mdz-capacity-review``): it dates nothing until then; and a migration plan whose
  waves carry no dates, with the planner's reason (no capacity, unreviewed, a wave with no estimate). ICDEV[OS]'s
  twin can only read their overdue and late-start checks as unverified.

A source that cannot be read is an unverified check, never a pass. Threads: ``mdz_assessment:<id>``,
``migration_plan:<id>``, ``audit_trail:<id>``, ``aiify_decision:<id>``, ``studio_workflow:<id>``, ``mc_refactor_job:<id>``,
``config:<path>@<sha256>``, ``legacy_app:<id>``.
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


def _bundles(limit: int) -> list[dict]:
    """Each legacy application's bundle as the exporter would write it now: features, processes and their review
    states, and what it would not carry (with the exporter's reason). An application whose preview fails says so."""
    from tools.db.storage import get_canvas_connection
    from tools.migration_canvas.modernisation import bundle_preview, features

    conn = get_canvas_connection()
    try:
        apps = rows_as_dicts(conn.execute("SELECT id, name FROM legacy_applications ORDER BY id LIMIT %s", (limit,)),
                             ("id", "name"))
    finally:
        close_quietly(conn)
    out = []
    for a in apps:
        app = str(a["id"])
        try:
            pre = bundle_preview(app)
            rep = features(app)
        except Exception as exc:  # noqa: BLE001 — one application unreadable is said, never skipped
            out.append({"app": app, "name": str(a["name"]), "error": str(exc)})
            continue
        schema_errors = list(pre.get("schema_errors") or [])
        sch = pre.get("schedule") or {}
        cap = sch.get("capacity") or {}
        out.append({"app": app, "name": str(a["name"]), "valid": bool(pre.get("valid")),
                    "error": None if pre.get("valid") or schema_errors else pre.get("error"),
                    # icx-mdz-04: what the pinned contract refuses, and the security domain (None: undeclared)
                    "schema_errors": schema_errors[:20],
                    "security_domain": pre.get("security_domain"),
                    "environment_reason": (pre.get("environment_report") or {}).get("reason"),
                    "features": len(rep.get("features") or []),
                    "processes": sorted(({"id": p["id"], "state": (p.get("review") or {}).get("state") or "unreviewed",
                                          "digest": p["signature"]["digest"]} for p in rep.get("processes") or []),
                                        key=lambda p: p["id"]),
                    "missing": dict(sorted((pre.get("missing") or {}).items())),
                    # icx-mdz-05: the waves' dates, from a reviewed team capacity, or why none
                    "schedule": {"planned_waves": int(pre.get("waves") or 0), "dated": bool(sch.get("dated")),
                                 "reason": sch.get("reason"),
                                 "capacity": {"application": cap.get("application"), "digest": cap.get("digest"),
                                              "review": (cap.get("review") or {}).get("state")} if cap else None}})
    return out


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
                    "job_reviews": None, "schema": None, "refusals": None, "bundles": None, "errors": {}}
        conn = None
        try:
            conn = get_canvas_connection()
            if not table_exists(conn, "migration_assessments"):
                ev["errors"]["assessments"] = "migration_assessments does not exist"
            else:
                from tools.modernization.seven_r_assessor import summarise

                cols = ("id", "legacy_app_id", "recommended_strategy", "assessed_at", "evidence")
                rows = rows_as_dicts(conn.execute(
                    "SELECT id, legacy_app_id, recommended_strategy, assessed_at, evidence FROM migration_assessments "
                    "WHERE assessment_scope = 'application' ORDER BY assessed_at DESC LIMIT %s", (limit,)), cols)
                latest: dict[str, dict] = {}
                for r in rows:             # newest first: the first per application is its latest
                    app = str(r["legacy_app_id"])
                    if app in latest:
                        continue
                    seen = summarise(r)    # icx-mdz-02: the confidence the recommendation rests on
                    latest[app] = {
                        "id": str(r["id"]), "app": app,
                        "recommended": str(r["recommended_strategy"] or ""), "at": ts_text(r["assessed_at"]),
                        "confidence": seen["confidence"], "min_confidence": seen["min_confidence"],
                        "not_assessable": seen["not_assessable"], "method": seen["method"],
                        "reason": (seen.get("recommendation") or {}).get("reason")}
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
            from tools.modernization import plan_gate

            ev["refusals"] = sorted(({k: r[k] for k in ("id", "application", "tool", "status", "reason",
                                                         "strategy_asked")} | {"at": ts_text(r["at"])}
                                     for r in plan_gate.refusals(limit=limit)), key=lambda r: r["id"])
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"]["refusals"] = str(exc)

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
            ev["bundles"] = _bundles(int(policy.get("bundle_scan_limit") or 50))
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"]["bundles"] = str(exc)

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
        if evidence.get("assessments") is not None and "assessments" not in errors:
            warn = float(policy.get("seven_r_confidence_warn", 0.7))
            for a in evidence["assessments"]:
                na = ", ".join(a.get("not_assessable") or []) or "none"
                if a.get("method", "").startswith("positional"):
                    findings.append(finding(
                        "mdz.seven_r.low_confidence", "medium",
                        f"{a['app']}: 7R recommends {a['recommended'] or '?'} on criteria paired by position",
                        thread_id=f"mdz_assessment:{a['id']}",
                        detail=f"assessed {a['at']}, before criteria were keyed by name; no confidence recorded",
                        recommendation="Re-assess the application on /migration."))
                elif not a["recommended"]:
                    findings.append(finding(
                        "mdz.seven_r.not_assessable", "medium",
                        f"{a['app']}: 7R recommends nothing: no strategy reaches confidence "
                        f"{a.get('min_confidence') if a.get('min_confidence') is not None else '?'}",
                        thread_id=f"mdz_assessment:{a['id']}",
                        detail=f"{a.get('reason') or 'insufficient evidence'}; not assessable: {na}",
                        recommendation="Measure what is missing (or decide on other evidence) before recording a "
                                       "strategy on /migration."))
                elif a.get("confidence") is not None and float(a["confidence"]) < warn:
                    findings.append(finding(
                        "mdz.seven_r.low_confidence", "medium",
                        f"{a['app']}: 7R recommends {a['recommended']} at confidence {float(a['confidence']):.2f}",
                        thread_id=f"mdz_assessment:{a['id']}",
                        detail=f"below {warn:.2f}: most of its criteria were not measured; not assessable: {na}",
                        recommendation="Treat the recommendation as thin: check what was not measured on /migration."))
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

        if readable("refusals", "Plans refused without ICDEV[OS] may-plan (audit trail)", evidence.get("refusals")) \
                and evidence.get("plans") is not None:
            newest: dict[str, dict] = {}
            for r in evidence["refusals"]:           # ordered by id: the last one per application is its newest
                newest[r["application"]] = r
            planned_at: dict[str, str] = {}
            for p in evidence["plans"]:
                if p["at"] > planned_at.get(p["app"], ""):
                    planned_at[p["app"]] = p["at"]
            for app, r in sorted(newest.items()):
                if planned_at.get(app, "") > r["at"]:
                    continue                          # planned since, on a may-plan that allowed it
                findings.append(finding(
                    "mdz.seven_r.plan_refused", "high",
                    f"{app}: a migration plan was attempted without ICDEV[OS]'s may-plan ({r['status']})",
                    thread_id=f"audit_trail:{r['id']}",
                    detail=f"{r['tool']} at {r['at']}: {r['reason']}",
                    recommendation="Plan only after ICDEV[OS]'s mission owner accepts a strategy (signed "
                                   "migration-assessment) and its decisions export says may-plan."))

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

        if readable("bundles", "Each application's bundle as the exporter would write it", evidence.get("bundles")):
            required = list(policy.get("bundle_required") or ())
            for b in evidence["bundles"]:
                thread = f"legacy_app:{b['app']}"
                if b.get("error"):
                    findings.append(finding(
                        "mdz.exporter.bundle_invalid", "high", f"{b['name']}: the bundle cannot be assembled",
                        thread_id=thread, detail=str(b["error"])[:300],
                        recommendation="Fix what the exporter refuses; ICDEV[OS] gets nothing for this application."))
                    continue
                if b.get("schema_errors"):
                    findings.append(finding(
                        "mdz.exporter.schema_invalid", "high",
                        f"{b['name']}: the bundle fails the pinned icos-apptwin/v1 contract "
                        f"({len(b['schema_errors'])} error(s))", thread_id=thread,
                        detail="; ".join(b["schema_errors"][:5])[:600],
                        recommendation="ICDEV[OS] refuses the whole bundle: fix what the exporter emits, never the "
                                       "pinned schema."))
                if "security_domain" in b and not b["security_domain"]:
                    findings.append(finding(
                        "mdz.exporter.no_security_domain", "medium",
                        f"{b['name']}: the bundle reports no security domain", thread_id=thread,
                        detail=str(b.get("environment_reason") or "no environment is declared for it")[:300],
                        recommendation="Declare the application's environment from its deployment record, with the "
                                       "source, in args/modernisation_environments.yaml; ICDEV[OS] classes its "
                                       "overlaps unverified until then."))
                if not b["features"]:
                    findings.append(finding(
                        "mdz.exporter.no_features", "high", f"{b['name']}: the bundle would carry no features",
                        thread_id=thread, detail=(b["missing"].get("features") or "no entry point found, no REA "
                                                  "capture given"),
                        recommendation="ICDEV[OS]'s overlap engine finds nothing without features: re-analyse the "
                                       "source, or hand in an REA observed inventory."))
                open_ = [p for p in b["processes"] if p["state"] != "reviewed"]
                if open_:
                    findings.append(finding(
                        "mdz.exporter.processes_unreviewed", "medium",
                        f"{b['name']}: {len(open_)} of {len(b['processes'])} extracted process(es) not reviewed",
                        thread_id=thread,
                        detail=", ".join(f"{p['id']} ({p['state']})" for p in open_[:10]),
                        recommendation="Confirm or reject each on /migration-canvas/modernisation "
                                       "(decl-wft-mdz-process-review)."))
                sch = b.get("schedule")
                cap = (sch or {}).get("capacity")
                if cap and cap.get("review") != "reviewed":
                    findings.append(finding(
                        "mdz.waves.capacity_unreviewed", "medium",
                        f"{b['name']}: the declared team capacity is {cap.get('review') or 'unreviewed'}, so it dates "
                        "no wave", thread_id=thread,
                        detail=f"{cap.get('application')} sha256 {str(cap.get('digest') or '')[:12]}: "
                               f"{sch.get('reason') or ''}"[:600],
                        recommendation="Approve or reject the capacity on /migration-canvas/modernisation "
                                       "(decl-wft-mdz-capacity-review); a changed entry is reviewed again."))
                if sch and sch.get("planned_waves") and not sch.get("dated"):
                    findings.append(finding(
                        "mdz.waves.undated", "low",
                        f"{b['name']}: the plan's {sch['planned_waves']} wave(s) carry no dates", thread_id=thread,
                        detail=str(sch.get("reason") or "no reason given")[:600],
                        recommendation="Declare the team's capacity, with its source, in "
                                       "args/modernisation_capacity.yaml and have it reviewed, and give every task an "
                                       "estimate; ICDEV[OS] reads the waves' overdue checks as unverified until then."))
                gone = [k for k in required if k in b["missing"]]
                if gone:
                    findings.append(finding(
                        "mdz.exporter.sections_missing", "low",
                        f"{b['name']}: the bundle would not carry {', '.join(gone)}", thread_id=thread,
                        detail="; ".join(f"{k}: {b['missing'][k]}" for k in gone)[:600],
                        recommendation="Each is null in the bundle (unverified in ICDEV[OS]); produce it, or let the "
                                       "reason stand."))

        summary = {
            "bundles_previewed": len(evidence.get("bundles") or []),
            "bundles_without_features": sum(1 for b in evidence.get("bundles") or []
                                            if not b.get("error") and not b.get("features")),
            "bundles_failing_contract": sum(1 for b in evidence.get("bundles") or [] if b.get("schema_errors")),
            "bundles_without_security_domain": sum(1 for b in evidence.get("bundles") or []
                                                   if "security_domain" in b and not b["security_domain"]),
            "plans_with_undated_waves": sum(1 for b in evidence.get("bundles") or []
                                            if (b.get("schedule") or {}).get("planned_waves")
                                            and not b["schedule"].get("dated")),
            "applications_assessed": len(evidence.get("assessments") or []),
            "applications_without_recommendation": sum(1 for a in evidence.get("assessments") or []
                                                       if not a["recommended"]),
            "plans_refused": len(evidence.get("refusals") or []),
            "strategies_recorded": len(records),
            "plans": len(evidence.get("plans") or []),
            "aiify_decisions": len(evidence.get("aiify_decisions") or []),
            "observed_drafts_pending": sum(1 for d in evidence.get("drafts") or [] if d["state"] == "pending_hitl"),
            "jobs_completed": len(evidence.get("jobs") or []),
            "jobs_reviewed": len({r["subject"] for r in evidence.get("job_reviews") or []}),
        }
        return {"checks": checks, "findings": findings, "summary": summary}
