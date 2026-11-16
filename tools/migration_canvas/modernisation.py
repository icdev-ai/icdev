# CUI // SP-CTI
"""The Modernisation page: one legacy application's waves, CAM jobs, cloud-native output and ICDEV[OS] bundle (icx-mdz-01).

The application's own waves (``tools/modernization/app_wave_planner.py``), its CAM refactor jobs
(``refactor_jobs.py``), its cloud-native jobs (``cloud_native_jobs.py``) and the ``icos-apptwin/v1`` bundle
(``apptwin_export.py``) were built for the ICDEV[OS] exchange and had no console page. This module adds one, on the
migration canvas, and the routes it calls:

    GET  /migration-canvas/modernisation                                 the page
    GET  /migration-canvas/api/modernisation/apps                        legacy applications, with their latest 7R
    GET  /migration-canvas/api/modernisation/apps/<app_id>/waves         app_wave_planner.report (derived, never stored)
    GET  /migration-canvas/api/modernisation/apps/<app_id>/refactor-jobs refactor_jobs.report, each job's review
    POST /migration-canvas/api/modernisation/refactor-jobs/<job_id>/approve
                                                                         a person approves or rejects a job's output
                                                                         (declared step decl-wft-mdz-cam-refactor-approve)
    GET  /migration-canvas/api/modernisation/apps/<app_id>/cloud-native  cloud_native_jobs.report and the section the
                                                                         exporter would carry
    GET  /migration-canvas/api/modernisation/apps/<app_id>/bundle        the bundle the exporter would write, which
                                                                         sections are null (unverified) and why
    POST /migration-canvas/api/modernisation/icos-decisions/check        verify a signed ICDEV[OS] decisions export
                                                                         against the PINNED JWKS; may-plan / may-retire

Everything here reads, except the one approval, which is a person's step. Nothing runs a job, plans a migration or
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
            for r in conn.execute(
                    "SELECT id, legacy_app_id, recommended_strategy, assessed_at FROM migration_assessments "
                    "WHERE assessment_scope = 'application' ORDER BY assessed_at").fetchall():
                latest[r[1]] = {"id": r[0], "recommended_strategy": r[2], "assessed_at": str(r[3] or "")}
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


def bundle_preview(app_id: str) -> dict:
    """The ``icos-apptwin/v1`` bundle the exporter would assemble from what this database holds (inventory, the latest
    plan with the derived waves, CAM's refactor and cloud-native jobs). Writes nothing. A section with no input stays
    null, which ICDEV[OS] reads as UNVERIFIED."""
    from tools.modernization import apptwin_export, app_wave_planner, cloud_native_jobs, refactor_jobs as rj

    conn = _core()
    try:
        waves = app_wave_planner.report(app_id, conn)
        refactor = rj.report(app_id)
        cn = cloud_native_jobs.report(app_id)
        try:
            bundle = apptwin_export.build(app_id, conn, waves=waves, refactor=refactor["jobs"] or None,
                                          cloud_native=cn if cn["jobs"] else None)
        except apptwin_export.BundleError as exc:
            return {"app_id": app_id, "valid": False, "error": str(exc), "sections": None}
    finally:
        conn.close()
    sections = {k: v is not None for k, v in sorted(bundle.items())
                if k not in ("schema", "producer", "produced_at", "application")}
    return {"app_id": app_id, "valid": True, "schema": bundle["schema"],
            "schema_sha256": apptwin_export.SCHEMA_SHA256,
            "inventory_digest": apptwin_export.inventory_digest(bundle),
            "sections": sections,
            "unverified": sorted(k for k, filled in sections.items() if not filled),
            "counts": {k: len(bundle[k]) for k in ("components", "apis", "data", "dependencies")
                       if isinstance(bundle.get(k), list)},
            "waves": len(((bundle.get("migration") or {}).get("waves")) or []),
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
