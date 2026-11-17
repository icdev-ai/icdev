# CUI // SP-CTI
"""The Modernisation page: one legacy application's waves, CAM jobs, cloud-native output and ICDEV[OS] bundle (icx-mdz-01).

The application's own waves (``tools/modernization/app_wave_planner.py``), its CAM refactor jobs
(``refactor_jobs.py``), its cloud-native jobs (``cloud_native_jobs.py``) and the ``icos-apptwin/v1`` bundle
(``apptwin_export.py``) were built for the ICDEV[OS] exchange and had no console page. This module adds one, on the
migration canvas, and the routes it calls:

    GET  /migration-canvas/modernisation                                 the page
    GET  /migration-canvas/api/modernisation/apps                        legacy applications, with their latest 7R
    GET  /migration-canvas/api/modernisation/apps/<app_id>/waves         app_wave_planner.report (derived, never stored);
                                                                         (icx-mdz-05) each wave's estimate and dates
                                                                         from a reviewed team capacity, or why none
    POST /migration-canvas/api/modernisation/apps/<app_id>/capacity/review
                                                                         a person approves or rejects the team
                                                                         capacity declared for the application, at its
                                                                         digest (declared step
                                                                         decl-wft-mdz-capacity-review)
    GET  /migration-canvas/api/modernisation/apps/<app_id>/refactor-jobs refactor_jobs.report, each job's review
    POST /migration-canvas/api/modernisation/refactor-jobs/<job_id>/approve
                                                                         a person approves or rejects a job's output
                                                                         (declared step decl-wft-mdz-cam-refactor-approve)
    GET  /migration-canvas/api/modernisation/apps/<app_id>/cloud-native  cloud_native_jobs.report and the section the
                                                                         exporter would carry
    GET  /migration-canvas/api/modernisation/apps/<app_id>/bundle        the bundle the exporter would write, which
                                                                         sections are null (unverified) and why;
                                                                         (icx-mdz-04) its errors against the pinned
                                                                         1.189.0 contract, the 7R's confidence and
                                                                         unmeasured criteria, the derived waves and
                                                                         the declared security domain, or why none
    GET  /migration-canvas/api/modernisation/apps/<app_id>/features      (icx-mdz-03) the features and processes
                                                                         derived without a model, with their evidence,
                                                                         capability hints and step signatures
    POST /migration-canvas/api/modernisation/apps/<app_id>/processes/<process_id>/review
                                                                         a person confirms or rejects an extracted
                                                                         process at its signature digest (declared step
                                                                         decl-wft-mdz-process-review)
    POST /migration-canvas/api/modernisation/icos-decisions/check        verify a signed ICDEV[OS] decisions export
                                                                         against the PINNED JWKS; may-plan / may-retire

Everything here reads, except the job approval, the process review and the capacity review, which are a person's
steps. Nothing runs a job, plans a migration or
accepts a strategy: ICDEV[OS] signs the portfolio decisions, and ICDEV only records or proposes.

The pinned ICDEV[OS] JWKS is a file the operator pinned once, named by ``ICDEV_ICOS_JWKS``. With none pinned, the check
answers ``untrusted`` and says so; it never trusts a key that came with the export.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.migration_canvas.modernisation")

JWKS_ENV = "ICDEV_ICOS_JWKS"


def _core():
    from tools.db.storage import get_connection
    return get_connection()


def list_apps() -> list[dict]:
    """Every legacy application with its component count and latest application-level 7R assessment."""
    conn = _core()
    try:
        apps = [dict(r) for r in conn.execute(
            "SELECT id, project_id, name, primary_language, framework, analysis_status FROM legacy_applications "
            "ORDER BY name").fetchall()]
        comps = {r[0]: int(r[1] or 0) for r in conn.execute(
            "SELECT legacy_app_id, COUNT(*) FROM legacy_components GROUP BY legacy_app_id").fetchall()}
        latest: dict[str, dict] = {}
        try:
            from tools.modernization.seven_r_assessor import summarise

            for r in conn.execute(
                    "SELECT id, legacy_app_id, recommended_strategy, assessed_at, evidence FROM migration_assessments "
                    "WHERE assessment_scope = 'application' ORDER BY assessed_at").fetchall():
                seen = summarise({"id": r[0], "legacy_app_id": r[1], "recommended_strategy": r[2], "evidence": r[4]})
                latest[r[1]] = {"id": r[0], "recommended_strategy": r[2], "assessed_at": str(r[3] or ""),
                                "confidence": seen["confidence"], "not_assessable": seen["not_assessable"]}
        except Exception as exc:  # noqa: BLE001 — no assessments table: nothing assessed here
            logger.debug("modernisation: assessments unreadable: %s", exc)
            conn.rollback()
    finally:
        conn.close()
    for a in apps:
        a["components"] = comps.get(a["id"], 0)
        a["assessment"] = latest.get(a["id"])
    return apps


def _artefacts(job: dict) -> list[dict]:
    """Each file the job lists, by name and the SHA-256 of its bytes now; a missing file says so."""
    out = []
    for a in job.get("artifacts") or []:
        if not isinstance(a, dict):
            continue
        path = Path(str(a.get("path") or ""))
        sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        out.append({"name": a.get("name") or path.name, "sha256": sha, "present": sha is not None})
    return out


def refactor_jobs(app_id: str) -> dict:
    """The application's CAM refactor jobs, each with its artefacts and the review a person took, if any."""
    from tools.modernization import decisions, refactor_jobs as rj

    rep = rj.report(app_id)
    reviews = decisions.decided(decisions.TEMPLATE_CAM, [j["id"] for j in rep["jobs"]])
    for j in rep["jobs"]:
        j["artefacts"] = _artefacts(j)
        j.pop("artifacts", None)
        j["review"] = decisions.job_review(reviews.get(j["id"]))
    return rep


def cloud_native(app_id: str) -> dict:
    """The application's cloud-native jobs, and the ``cloud_native`` section the exporter would carry (null when no
    twelve-factor report has run: unverified, never filled in)."""
    from tools.modernization import apptwin_export, cloud_native_jobs, decisions

    rep = cloud_native_jobs.report(app_id)
    section, section_error = None, None
    try:
        section = apptwin_export.cloud_native_from_jobs(rep, at=_now(), app=app_id)
    except Exception as exc:  # noqa: BLE001 — a file that cannot be read is said, never skipped
        section_error = str(exc)
    reviews = decisions.decided(decisions.TEMPLATE_CAM, [j["id"] for j in rep["jobs"]])
    for j in rep["jobs"]:
        j["artefacts"] = _artefacts(j)
        j.pop("artifacts", None)
        j["review"] = decisions.job_review(reviews.get(j["id"]))
    factors = (section or {}).get("factors") or []
    return {**rep, "section": section, "section_error": section_error,
            "factors": [{k: f.get(k) for k in ("id", "title", "status", "evidence") if k in f} for f in factors],
            "targets": (section or {}).get("targets")}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def features(app_id: str) -> dict:
    """The application's features and processes, derived without a model (``bundle_features.report``), each process
    with the review a person took at its signature digest, if any. Writes nothing."""
    from tools.modernization import bundle_features

    conn = _core()
    try:
        return bundle_features.report(app_id, conn)
    finally:
        conn.close()


def _latest_assessment(app_id: str, conn) -> dict | None:
    """The application's latest application-level 7R assessment row, or None (none, or no assessments table)."""
    try:
        row = conn.execute("SELECT * FROM migration_assessments WHERE legacy_app_id = %s AND assessment_scope = "
                           "'application' ORDER BY assessed_at DESC LIMIT 1", (app_id,)).fetchone()
    except Exception as exc:  # noqa: BLE001 — no assessments table: nothing assessed here
        logger.debug("modernisation: assessments unreadable: %s", exc)
        conn.rollback()
        return None
    return dict(row) if row else None


def bundle_preview(app_id: str) -> dict:
    """The ``icos-apptwin/v1`` bundle the exporter would assemble from what this database holds (inventory, the latest
    7R assessment, the latest plan with the derived waves, CAM's refactor and cloud-native jobs, the declared
    environment). Writes nothing. A section with no input stays null, which ICDEV[OS] reads as UNVERIFIED.

    icx-mdz-04: validated against the pinned contract (1.189.0) and ICDEV[OS]'s agreement rules, and every error is
    returned (``schema_errors``), never hidden behind a refusal; the 7R's confidence and unmeasured criteria per
    strategy, the waves derived before a plan (and each planned wave's estimate), and the application's security
    domain -- or why it has none -- are said."""
    from tools.modernization import app_wave_planner, apptwin_export, bundle_features, cloud_native_jobs
    from tools.modernization import refactor_jobs as rj

    conn = _core()
    try:
        name = (conn.execute("SELECT name FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
                or {"name": None})["name"]
        waves = app_wave_planner.report(app_id, conn)
        seven = _latest_assessment(app_id, conn)
        try:
            env, env_report = apptwin_export.environment(app_id, name)
        except apptwin_export.BundleError as exc:   # a declaration that is refused is said, never used
            env, env_report = None, {"declared": False, "error": str(exc), "reason": str(exc)}
        refactor = rj.report(app_id)
        cn = cloud_native_jobs.report(app_id)
        extract, extract_error = None, None               # icx-mdz-03: features and processes, no model
        try:
            extract = bundle_features.report(app_id, conn)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — unreadable is said, and the sections stay null (unverified)
            extract_error = str(exc)
            conn.rollback()
        try:
            bundle = apptwin_export.build(app_id, conn, waves=waves, refactor=refactor["jobs"] or None,
                                          cloud_native=cn if cn["jobs"] else None, extract=extract, seven_r=seven,
                                          environment=env, check=False, reasons={
                                              k: f"the features and processes could not be derived: {extract_error}"
                                              for k in ("features", "processes")} if extract_error else None)
        except apptwin_export.BundleError as exc:
            return {"app_id": app_id, "valid": False, "error": str(exc), "sections": None}
    finally:
        conn.close()
    schema_errors = apptwin_export.validate(bundle)
    states = bundle["sections"]
    sections = {k: bundle.get(k) is not None for k in apptwin_export.SECTIONS}
    sr = bundle.get("seven_r") or {}
    plan_waves = ((bundle.get("migration") or {}).get("waves")) or []
    dw = bundle.get("derived_waves")
    return {"app_id": app_id, "valid": not schema_errors, "schema": bundle["schema"],
            "contract": apptwin_export.CONTRACT_VERSION, "schema_sha256": apptwin_export.SCHEMA_SHA256,
            **({"error": "the bundle does not meet the pinned icos-apptwin/v1 contract: " + "; ".join(schema_errors[:5]),
                "schema_errors": schema_errors} if schema_errors else {"schema_errors": []}),
            "inventory_digest": apptwin_export.inventory_digest(bundle),
            "sections": sections,
            "section_states": states,
            "unverified": sorted(k for k, filled in sections.items() if not filled),
            "counts": {k: len(bundle[k]) for k in ("components", "apis", "data", "dependencies", "features", "processes")
                       if isinstance(bundle.get(k), list)},
            "waves": len(plan_waves),
            # icx-mdz-05: the waves' dates come only from a reviewed team capacity; else why not (unmeasured)
            "schedule": waves.get("schedule"),
            "planned_waves": [{k: w.get(k) for k in ("id", "title", "order", "estimated_hours", "starts", "ends")}
                              | {"components": len(w.get("components") or [])} for w in plan_waves],
            "derived_waves": None if dw is None else {
                "basis": dw["basis"], "tool": dw["tool"],
                "waves": [{"id": w["id"], "title": w["title"], "order": w["order"],
                           "components": len(w.get("components") or []), "estimated_hours": w.get("estimated_hours")}
                          for w in dw["waves"]],
                "means": "derived before any plan: shown in ICDEV[OS], never approved (its gate reads migration.waves)"},
            "seven_r": None if not sr else {
                "recommended": sr.get("recommended"), "confidence": sr.get("confidence"),
                "ranked": [{k: x.get(k) for k in ("strategy", "score", "confidence", "assessable")}
                           | {"unmeasured": x.get("unmeasured") or []} for x in sr.get("ranked") or []]},
            "environment": (bundle["application"].get("environment") or None),
            "environment_report": env_report,
            "security_domain": (bundle["application"].get("environment") or {}).get("security_domain"),
            "missing": {k: v.get("reason") for k, v in states.items() if v["state"] == "missing"},
            "processes_unreviewed": sum(1 for p in (extract or {}).get("processes") or []
                                        if (p.get("review") or {}).get("state") != "reviewed"),
            **({"extract_error": extract_error} if extract_error else {}),
            "write_with": f"python tools/modernization/apptwin_export.py --app-id {app_id} --out <file>"}


def icos_check(export: Any, app: str, strategy: str = "") -> tuple[dict, int]:
    """Verify a signed ICDEV[OS] decisions export against the pinned JWKS; answer may-plan and may-retire."""
    from tools.modernization import icos_decisions

    pinned_path = os.environ.get(JWKS_ENV, "").strip()
    if not pinned_path:
        return {"status": "untrusted", "application": app,
                "error": f"no pinned ICDEV[OS] JWKS: set {JWKS_ENV} to the file you pinned from its /oauth/jwks"}, 409
    try:
        keys = icos_decisions.pinned(json.loads(Path(pinned_path).read_text(encoding="utf-8")))
        doc = icos_decisions.verify(export if isinstance(export, dict) else json.loads(str(export or "")), keys)
    except (icos_decisions.Untrusted, OSError, ValueError, TypeError) as exc:
        return {"status": "untrusted", "application": app, "error": str(exc)}, 409
    return {"status": "ok", "issuer": doc.get("issuer"), "application": app,
            "may_plan": icos_decisions.may_plan(doc, app, strategy or None),
            "may_retire": icos_decisions.may_retire(doc, app)}, 200


def register_modernisation_routes(bp, login_required: Callable) -> None:
    """Attach the Modernisation page and its routes to the migration canvas blueprint."""

    @bp.route("/modernisation")
    @login_required
    def modernisation_page():
        """Modernisation: an application's waves, CAM jobs, cloud-native output and ICDEV[OS] bundle (icx-mdz-01)."""
        return render_template("migration_canvas/modernisation.html", jwks_pinned=bool(os.environ.get(JWKS_ENV)))

    @bp.route("/api/modernisation/apps")
    @login_required
    def api_modernisation_apps():
        try:
            return jsonify({"apps": list_apps()})
        except Exception as exc:  # noqa: BLE001 — unreadable is said, never an empty list
            logger.warning("modernisation apps: %s", exc)
            return jsonify({"error": str(exc), "apps": None}), 500

    @bp.route("/api/modernisation/apps/<app_id>/waves")
    @login_required
    def api_modernisation_waves(app_id):
        from tools.modernization import app_wave_planner
        conn = _core()
        try:
            return jsonify(app_wave_planner.report(app_id, conn))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:  # noqa: BLE001
            logger.warning("modernisation waves %s: %s", app_id, exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/modernisation/apps/<app_id>/capacity/review", methods=["POST"])
    @login_required
    def api_modernisation_capacity_review(app_id):
        """A person approves or rejects the team capacity declared for one application, with a reason, at its digest.

        Body: ``{decision: approve|reject, reason, digest}``. The person's step of the declared template
        ``decl-wft-mdz-capacity-review``; the decided step is the review, bound to the entry and its digest. An entry
        that is not the one the person saw (the file changed) is refused, and one already reviewed at this digest is
        not reviewed twice. The file and the plan are not changed: the wave planner reads the review.
        """
        from tools.modernization import decisions, wave_schedule
        from tools.workflow_hitl import declared

        data = request.get_json(force=True, silent=True) or {}
        conn = _core()
        try:
            row = conn.execute("SELECT name FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
            if row is None:
                return jsonify({"error": f"no legacy application {app_id}"}), 404
            try:
                key, entry = wave_schedule.capacity_for(app_id, row["name"])
            except wave_schedule.CapacityError as exc:
                return jsonify({"error": f"the capacity file is refused: {exc}"}), 409
            if entry is None:
                return jsonify({"error": f"no team capacity is declared for {row['name']} in "
                                         f"{wave_schedule.shown()}: declare one, with its source, first"}), 404
            if str(data.get("digest") or "") != entry["digest"]:
                return jsonify({"error": "the capacity is not what you reviewed: its digest is now "
                                         f"{entry['digest'][:12]} (reload it)", "digest": entry["digest"]}), 409
            if wave_schedule.review_state(key, entry, conn)["state"] in ("reviewed", "rejected"):
                return jsonify({"error": f"the capacity for {key} has already been reviewed at this digest"}), 409
        finally:
            conn.close()
        payload = {"app_id": app_id, "application": key, "file": wave_schedule.shown(),
                   **{k: entry[k] for k in (*wave_schedule.FIELDS, "digest")}}
        try:
            step = declared.take_step(decisions.TEMPLATE_CAPACITY,
                                      surface="migration_canvas.api_modernisation_capacity_review",
                                      decision=str(data.get("decision") or "").strip().lower(),
                                      subject=wave_schedule.subject(key),
                                      reason=str(data.get("reason") or "").strip(), payload=payload)
        except declared.StepRefused as exc:
            return jsonify(exc.to_dict()), exc.status
        return jsonify({"app_id": app_id, "application": key, "decision_step": step,
                        "review": {"state": "reviewed" if step["affirmative"] else "rejected",
                                   "decision": step["decision"], "by": step["decided_by"],
                                   "digest": entry["digest"]}})

    @bp.route("/api/modernisation/apps/<app_id>/refactor-jobs")
    @login_required
    def api_modernisation_refactor_jobs(app_id):
        try:
            return jsonify(refactor_jobs(app_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("modernisation refactor jobs %s: %s", app_id, exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/modernisation/refactor-jobs/<job_id>/approve", methods=["POST"])
    @login_required
    def api_refactor_job_approve(job_id):
        """A person approves or rejects one completed CAM job's generated output, with a reason.

        Body: ``{decision: approve|reject, reason}``. The person's step of the declared template
        ``decl-wft-mdz-cam-refactor-approve``; the decided step is the review, bound to the SHA-256 of each artefact
        the person saw. The job row and its files are not changed, and nothing is applied.
        """
        from tools.migration_canvas import cam_refactor_engine as cam
        from tools.modernization import decisions
        from tools.workflow_hitl import declared

        data = request.get_json(force=True, silent=True) or {}
        job = cam.get_job_detail(job_id)
        if job is None:
            return jsonify({"error": f"no refactor job {job_id}"}), 404
        if job.get("status") != "completed":
            return jsonify({"error": f"job {job_id} is {job.get('status')}: only a completed job's output is "
                                     "reviewed"}), 409
        if decisions.decided(decisions.TEMPLATE_CAM, [job_id]):
            return jsonify({"error": f"job {job_id} has already been reviewed"}), 409
        arts = _artefacts(job)
        payload = {"job_id": job_id, "project_id": job.get("project_id"), "refactor_type": job.get("refactor_type"),
                   "scaffold": (job.get("params") or {}).get("scaffold_type"), "artefacts": arts}
        try:
            step = declared.take_step(decisions.TEMPLATE_CAM, surface="migration_canvas.api_refactor_job_approve",
                                      decision=str(data.get("decision") or "").strip().lower(), subject=job_id,
                                      reason=str(data.get("reason") or "").strip(), payload=payload)
        except declared.StepRefused as exc:
            return jsonify(exc.to_dict()), exc.status
        return jsonify({"job_id": job_id, "decision_step": step,
                        "review": {"decision": step["decision"], "approved": step["affirmative"],
                                   "by": step["decided_by"], "artefacts": arts}})

    @bp.route("/api/modernisation/apps/<app_id>/cloud-native")
    @login_required
    def api_modernisation_cloud_native(app_id):
        try:
            return jsonify(cloud_native(app_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("modernisation cloud-native %s: %s", app_id, exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/modernisation/apps/<app_id>/bundle")
    @login_required
    def api_modernisation_bundle(app_id):
        try:
            return jsonify(bundle_preview(app_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:  # noqa: BLE001
            logger.warning("modernisation bundle %s: %s", app_id, exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/modernisation/apps/<app_id>/features")
    @login_required
    def api_modernisation_features(app_id):
        try:
            return jsonify(features(app_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:  # noqa: BLE001
            logger.warning("modernisation features %s: %s", app_id, exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/modernisation/apps/<app_id>/processes/<process_id>/review", methods=["POST"])
    @login_required
    def api_modernisation_process_review(app_id, process_id):
        """A person confirms or rejects one extracted process, with a reason, at the signature digest they saw.

        Body: ``{decision: confirm|reject, reason, signature_digest}``. The person's step of the declared template
        ``decl-wft-mdz-process-review``; the decided step is the review, bound to the step signature, its digest and
        the rules digest. A digest that is not the process's now (the source or the rules changed) is refused, and a
        process already reviewed at this digest is not reviewed twice. Nothing else is changed.
        """
        from tools.modernization import bundle_features, decisions
        from tools.workflow_hitl import declared

        data = request.get_json(force=True, silent=True) or {}
        try:
            rep = features(app_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        proc = next((p for p in rep.get("processes") or [] if p["id"] == process_id), None)
        if proc is None:
            return jsonify({"error": f"{app_id} has no process {process_id}"}), 404
        if rep.get("review_error"):
            return jsonify({"error": f"the review ledger could not be read: {rep['review_error']}"}), 503
        digest = proc["signature"]["digest"]
        if str(data.get("signature_digest") or "") != digest:
            return jsonify({"error": "the process is not what you reviewed: its step signature is now "
                                     f"{digest[:12]} (reload it)", "signature_digest": digest}), 409
        if (proc.get("review") or {}).get("state") in ("reviewed", "rejected"):
            return jsonify({"error": f"{process_id} has already been reviewed at this signature"}), 409
        payload = {"app_id": app_id, "process_id": process_id, "source": proc["source"],
                   "signature": proc["signature"]["steps"], "signature_digest": digest,
                   "rules_digest": proc["signature"]["rules_digest"]}
        try:
            step = declared.take_step(decisions.TEMPLATE_PROCESS,
                                      surface="migration_canvas.api_modernisation_process_review",
                                      decision=str(data.get("decision") or "").strip().lower(),
                                      subject=bundle_features.subject(app_id, process_id),
                                      reason=str(data.get("reason") or "").strip(), payload=payload)
        except declared.StepRefused as exc:
            return jsonify(exc.to_dict()), exc.status
        return jsonify({"app_id": app_id, "process_id": process_id, "decision_step": step,
                        "review": {"state": "reviewed" if step["affirmative"] else "rejected",
                                   "decision": step["decision"], "by": step["decided_by"],
                                   "signature_digest": digest}})

    @bp.route("/api/modernisation/icos-decisions/check", methods=["POST"])
    @login_required
    def api_modernisation_icos_check():
        """Verify a signed ICDEV[OS] decisions export (pasted) against the pinned JWKS. Writes nothing.

        Body: ``{app, strategy, export}``. ``export`` is the JSON ``GET /api/v1/portfolio/decisions/export`` returned.
        """
        data = request.get_json(force=True, silent=True) or {}
        app = str(data.get("app") or "").strip()
        if not app:
            return jsonify({"error": "app (the application's id in its icos-apptwin bundle) is required"}), 400
        body, status = icos_check(data.get("export"), app, str(data.get("strategy") or "").strip().lower())
        return jsonify(body), status
