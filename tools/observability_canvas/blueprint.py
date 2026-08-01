
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV Observability Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /observability/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate observability_canvas.db, and feature flag
ICDEV_OBSERVABILITY_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.observability_canvas.blueprint import create_observability_blueprint
    bp = create_observability_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/observability")
"""

import json
import os
import uuid as _uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

logger = get_logger("icdev.observability_canvas")

# ── Paths ──────────────────────────────────────────────────────────────────────
_OC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _OC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"
_CONFIG_PATH = _ICDEV_ROOT / "args" / "observability_canvas_config.yaml"

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_config() -> dict:
    """Load ODC config from args/observability_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_ODC_CONFIG = _load_config()

# ── Import helper modules ──────────────────────────────────────────────────────
from tools.observability_canvas.constants import (  # noqa: E402
    OBSERVABILITY_OBJECTS,
    OBSERVABILITY_COMPLIANCE_RULES,
)
from tools.observability_canvas.observability_engine import (  # noqa: E402
    assess_observability_design,
    compute_coverage_score,
    compute_mitre_detection_coverage,
    detect_observability_gaps,
)
from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: E402

# Tracks whether the startup init_db() failed so page routes can lazily retry once.
# Default False; set True when create_observability_blueprint's init_db() raises so
# the first subsequent request re-attempts init before querying.
_init_failed = False


def create_observability_blueprint():
    """Create and return the Observability Design Canvas Blueprint.

    Returns None if ICDEV_OBSERVABILITY_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_OBSERVABILITY_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Observability Canvas disabled (ICDEV_OBSERVABILITY_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    global _init_failed
    try:
        from tools.observability_canvas.db.init_db import init_db

        init_db()
        _init_failed = False
    except Exception as exc:
        _init_failed = True
        logger.warning(
            "Observability Canvas DB init failed (will retry lazily on first request): %s",
            exc,
        )

    try:
        from tools.observability_canvas import bus_subscriber as _odc_bus
        _odc_bus.register()
    except Exception as exc:
        logger.warning("ODC bus subscriber registration failed: %s", exc)

    bp = Blueprint(
        "observability_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth wrapper (uses ICDEV dashboard session) ────────────────────────
    def oc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if (
                    request.is_json
                    or request.path.startswith("/observability/api/")
                    or request.method in ("DELETE", "POST", "PUT")
                ):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)

        return decorated

    # ── DB helpers ─────────────────────────────────────────────────────────
    from tools.observability_canvas.db.init_db import get_connection

    def _ensure_init():
        """Lazily retry DB init once if the startup init_db() failed.

        Cheap guard invoked at the top of every page route: if the canvas was
        mounted while the store was unreachable, the first request that lands
        after the store recovers re-runs init_db() and clears the failure flag.
        """
        global _init_failed
        if not _init_failed:
            return
        try:
            from tools.observability_canvas.db.init_db import init_db as _retry_init

            _retry_init()
            _init_failed = False
            logger.info("Observability Canvas DB init retry succeeded")
        except Exception as exc:
            logger.warning("Observability Canvas DB init retry failed: %s", exc)

    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _audit(action, design_id="", details=""):
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO od_audit (design_id, actor, action, detail, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (design_id, user_id, action, details, _now()),
                )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit: best-effort INSERT into od_audit failed (non-blocking): %s", exc)

    def _row_to_dict(row):
        return dict(row) if row else {}

    # ====================================================================
    # PAGE ROUTES
    # ====================================================================

    @bp.route("/")
    @oc_login_required
    def oc_index():
        """Observability Design Canvas dashboard — list designs + recent assessments."""
        _ensure_init()
        designs, recent_assessments, templates = [], [], []
        store_unavailable = False
        try:
            with get_connection() as conn:
                designs = [
                    _row_to_dict(r)
                    for r in conn.execute(
                        "SELECT id, name, description, classification, "
                        "created_at, updated_at "
                        "FROM observability_designs ORDER BY updated_at DESC"
                    ).fetchall()
                ]
                recent_assessments = [
                    _row_to_dict(r)
                    for r in conn.execute(
                        "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                        "a.grade, a.created_at, d.name AS design_name "
                        "FROM od_assessments a "
                        "JOIN observability_designs d ON a.design_id = d.id "
                        "ORDER BY a.created_at DESC LIMIT 10"
                    ).fetchall()
                ]
                templates = [
                    _row_to_dict(r)
                    for r in conn.execute(
                        "SELECT id, name, category, description, tags FROM od_templates ORDER BY category, name"
                    ).fetchall()
                ]
        except Exception as exc:
            store_unavailable = True
            logger.warning("ODC index page DB unavailable: %s", exc)
        return render_template(
            "observability_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
            templates=templates,
            objects=OBSERVABILITY_OBJECTS,
            store_unavailable=store_unavailable,
        )

    @bp.route("/canvas/new")
    @oc_login_required
    def oc_new_canvas():
        """Create a new observability design and open the canvas."""
        template_id = request.args.get("template")
        graph = json.dumps({"nodes": [], "edges": []})
        name = "Untitled Observability Design"
        if template_id:
            with get_connection() as conn:
                tpl = conn.execute(
                    "SELECT name, graph_json FROM od_templates WHERE id=%s",
                    (template_id,),
                ).fetchone()
            if tpl:
                tpl = _row_to_dict(tpl)
                graph = tpl["graph_json"]
                name = f"{tpl['name']} (copy)"
        return render_template(
            "observability_canvas/canvas.html",
            design_id="new",
            design_name=name,
            graph_json=graph,
            objects=OBSERVABILITY_OBJECTS,
            rules=OBSERVABILITY_COMPLIANCE_RULES,
        )

    @bp.route("/canvas/<design_id>")
    @oc_login_required
    def oc_canvas(design_id):
        """Canvas editor for a specific observability design."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return redirect("/observability/")
        design = _row_to_dict(row)
        return render_template(
            "observability_canvas/canvas.html",
            design_id=design["id"],
            design_name=design["name"],
            graph_json=design["graph_json"],
            design=design,
            objects=OBSERVABILITY_OBJECTS,
            rules=OBSERVABILITY_COMPLIANCE_RULES,
        )

    @bp.route("/templates")
    @oc_login_required
    def oc_templates():
        """Template gallery page."""
        _ensure_init()
        templates = []
        store_unavailable = False
        try:
            with get_connection() as conn:
                templates = [
                    _row_to_dict(r)
                    for r in conn.execute(
                        "SELECT id, name, category, description, tags FROM od_templates ORDER BY category, name"
                    ).fetchall()
                ]
        except Exception as exc:
            store_unavailable = True
            logger.warning("ODC templates page DB unavailable: %s", exc)
        return render_template(
            "observability_canvas/templates.html",
            templates=templates,
            store_unavailable=store_unavailable,
        )

    @bp.route("/assessments")
    @oc_login_required
    def oc_assessments():
        """Assessment history page."""
        _ensure_init()
        assessments = []
        store_unavailable = False
        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                    "a.grade, a.created_at, d.name AS design_name "
                    "FROM od_assessments a "
                    "LEFT JOIN observability_designs d ON a.design_id = d.id "
                    "ORDER BY a.created_at DESC LIMIT 50"
                ).fetchall()
                assessments = [_row_to_dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as exc:
            store_unavailable = True
            logger.warning("ODC assessments page DB unavailable: %s", exc)
        return render_template(
            "observability_canvas/assessments.html",
            assessments=assessments,
            store_unavailable=store_unavailable,
        )

    @bp.route("/coverage/<design_id>")
    @oc_login_required
    def oc_coverage_page(design_id):
        """Detection coverage page for a specific observability design."""
        _ensure_init()
        design = {}
        gap_score = None
        store_unavailable = False
        try:
            with get_connection() as conn:
                design = _row_to_dict(
                    conn.execute("SELECT id, name FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
                )
                if not design:
                    return redirect("/observability/")
                # obx-cov-01: surface the latest persisted signal-source gap score
                # (mitre_coverage_twin.compute_gap_score writer). This is a DISTINCT
                # metric from the client-side "design baseline" MITRE coverage the
                # page fetches via /assess — do not conflate the two.
                try:
                    gs = conn.execute(
                        "SELECT total_techniques, covered_count, partial_count, "
                        "gap_count, overall_gap_score, assessed_at "
                        "FROM odc_gap_scores WHERE design_id=%s "
                        "ORDER BY assessed_at DESC LIMIT 1",
                        (design_id,),
                    ).fetchone()
                    if gs:
                        gap_score = _row_to_dict(gs)
                except Exception as exc:
                    logger.debug("ODC coverage page gap-score read unavailable: %s", exc)
        except Exception as exc:
            store_unavailable = True
            logger.warning("ODC coverage page DB unavailable: %s", exc)
        return render_template(
            "observability_canvas/coverage.html",
            design=design,
            gap_score=gap_score,
            store_unavailable=store_unavailable,
        )

    @bp.route("/remediation/<design_id>")
    @oc_login_required
    def oc_remediation_page(design_id):
        """Remediation page — gap analysis with recommended fixes."""
        _ensure_init()
        design = {}
        store_unavailable = False
        try:
            with get_connection() as conn:
                design = _row_to_dict(
                    conn.execute("SELECT id, name FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
                )
            if not design:
                return redirect("/observability/")
        except Exception as exc:
            store_unavailable = True
            logger.warning("ODC remediation page DB unavailable: %s", exc)
        return render_template(
            "observability_canvas/remediation.html",
            design=design,
            store_unavailable=store_unavailable,
        )

    # ====================================================================
    # API — DESIGN CRUD
    # ====================================================================

    @bp.route("/api/health")
    def oc_health():
        return jsonify({"status": "ok", "module": "observability_canvas"})

    @bp.route("/api/designs", methods=["POST"])
    @oc_login_required
    def oc_api_create():
        """Create a new observability design."""
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        name = data.get("name", "Untitled Observability Design")[:200]
        template_id = data.get("template_id", "")
        if template_id:
            with get_connection() as conn:
                ex = conn.execute(
                    "SELECT id, name FROM observability_designs WHERE template_id=%s LIMIT 1",
                    (template_id,),
                ).fetchone()
            if ex:
                return jsonify({"id": ex["id"], "name": ex["name"]}), 200
        design_id = str(_uuid.uuid4())
        graph_json = data.get("graph_json", '{"nodes":[],"edges":[]}')

        # If template_id given and no graph_json, load from template
        if template_id and graph_json == '{"nodes":[],"edges":[]}':
            try:
                with get_connection() as conn:
                    tpl = conn.execute("SELECT graph_json FROM od_templates WHERE id=%s", (template_id,)).fetchone()
                    if tpl:
                        graph_json = tpl["graph_json"]
            except Exception:
                pass

        logger.info("Creating observability design: %s (%s)", name, design_id)
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO observability_designs "
                "(id, name, description, graph_json, template_id, classification, "
                "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    name,
                    data.get("description", ""),
                    graph_json,
                    template_id,
                    data.get("classification", "CUI"),
                    _now(),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        _audit("CREATE", design_id, name)
        # Hook: refresh ODC KG so /ask reflects the new design immediately.
        # Guarded — the design is already committed above, so a failure in the
        # post-commit reindex hook must NOT turn a successful create into a 500.
        try:
            from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
            reindex_canvas_on_save("odc")
        except Exception:
            pass
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs", methods=["GET"])
    @oc_login_required
    def oc_api_list():
        """List all observability designs."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, description, classification, template_id, "
                "created_at, updated_at FROM observability_designs ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @oc_login_required
    def oc_api_get(design_id):
        """Get a specific observability design."""
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @oc_login_required
    def oc_api_update(design_id):
        """Update an observability design."""
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        logger.info("Updating observability design: %s", design_id)
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE observability_designs SET name=%s, description=%s, "
                "graph_json=%s, classification=%s, updated_at=%s WHERE id=%s",
                (
                    data.get("name", ""),
                    data.get("description", ""),
                    data.get("graph_json", "{}"),
                    data.get("classification", "CUI"),
                    _now(),
                    design_id,
                ),
            )
            rowcount = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        # Unknown design id → 404 (correct contract). No post-commit hooks fire
        # for a design that does not exist.
        if rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        _audit("UPDATE", design_id, data.get("name", ""))
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("odc", "odc.source.added", {
                "design_id": design_id,
                "classification": data.get("classification", "CUI"),
                "graph_changed": "graph_json" in data,
            }, target_canvas="aadc")
        except Exception:
            pass
        # Hook: refresh ODC KG so /ask reflects the edit immediately
        from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
        reindex_canvas_on_save("odc")

        # Cross-canvas trigger: verify SIEM/logging controls match SDC requirements
        try:
            from tools.security_canvas.agent import on_odc_design_saved

            on_odc_design_saved(design_id)
        except Exception:
            pass

        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("odc", design_id)
        except Exception:
            pass
        # Blockchain provenance
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="odc",
                design_id=design_id,
                graph_json=data.get("graph_json", {}),
                project_id=data.get("project_id", ""),
            )
        except Exception:
            pass

        return jsonify({"updated": True})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @oc_login_required
    def oc_api_delete(design_id):
        """Delete an observability design."""
        logger.info("Deleting observability design: %s", design_id)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM od_assessments WHERE design_id=%s", (design_id,))
            conn.execute("DELETE FROM od_versions WHERE design_id=%s", (design_id,))
            cur = conn.execute("DELETE FROM observability_designs WHERE id=%s", (design_id,))
            rowcount = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        # Unknown design id → 404 (correct contract). The child-table deletes
        # above are idempotent no-ops when the design never existed.
        if rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        _audit("DELETE", design_id, "")
        return jsonify({"deleted": True})

    @bp.route("/api/designs", methods=["DELETE"])
    @oc_login_required
    def oc_api_delete_all():
        """Delete all observability designs and cascade child records."""
        conn = get_connection()
        try:
            ids = [r[0] for r in conn.execute("SELECT id FROM observability_designs").fetchall()]
            for did in ids:
                conn.execute("DELETE FROM od_assessments WHERE design_id=%s", (did,))
                conn.execute("DELETE FROM od_versions WHERE design_id=%s", (did,))
                conn.execute("DELETE FROM observability_designs WHERE id=%s", (did,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"deleted": len(ids)})

    # ====================================================================
    # API — ASSESSMENT
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @oc_login_required
    def oc_api_assess(design_id):
        """Run observability compliance assessment on a design."""
        data = request.get_json(force=True, silent=True) or {}
        use_cot = data.get("use_cot", False)
        chain_mode = "cot" if use_cot else ""
        conn = get_connection()
        try:
            row = conn.execute("SELECT graph_json FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Design not found"}), 404

        graph_raw = row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        # Run assessment
        assessment = assess_observability_design(graph_data)
        coverage = compute_coverage_score(graph_data)
        mitre = compute_mitre_detection_coverage(graph_data)
        gaps = detect_observability_gaps(assessment)

        # Persist assessment
        assessment_id = assessment["assessment_id"]
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO od_assessments "
                "(id, design_id, assessment_type, findings_json, score, grade, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    assessment_id,
                    design_id,
                    assessment["assessment_type"],
                    json.dumps(assessment["findings"]),
                    assessment["score"],
                    assessment["grade"],
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        _audit("ASSESS", design_id, f"Score: {assessment['score']}, Grade: {assessment['grade']}")

        # obx-cov-01: after a successful assessment, compute the signal-source
        # coverage gap score so the ODC compliance card (get_odc_card reads
        # odc_gap_scores) lights up in production. GUARDED — a coverage failure
        # must NEVER fail the assessment; it is a best-effort side effect.
        try:
            from tools.observability_canvas.mitre_coverage_twin import compute_gap_score

            compute_gap_score(design_id, graph_data)
        except Exception as exc:
            logger.warning("ODC coverage compute during assess failed (non-fatal): %s", exc)

        record_canvas_decision(
            canvas_type="odc",
            record_id=design_id,
            decision_type="compliance_finding",
            decision=f"Grade {assessment.get('grade','?')} — Score {assessment.get('score',0)}, MITRE coverage {mitre.get('coverage_pct',0):.0f}%",
            rationale=f"Gaps detected: {len(gaps)}",
            model_used=None,
            confidence=None,
        )
        # Blockchain provenance for assessment
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="odc",
                design_id=design_id,
                assessment_data=assessment,
                project_id="",
            )
        except Exception:
            pass

        return jsonify(
            {
                "assessment": assessment,
                "coverage": coverage,
                "mitre_detection": mitre,
                "gaps": gaps,
                "chain_mode": chain_mode,
            }
        )

    @bp.route("/api/replay-verify/<design_id>", methods=["POST"])
    @oc_login_required
    def oc_api_replay_verify(design_id):
        """Replay an SDC attack path and verify TTP detection coverage.

        Wraps tools.observability_canvas.replay_verify.verify_path — the only
        non-demo writer of od_ttp_coverage (the fallback source for the ODC
        compliance card). Persists one od_ttp_coverage + od_audit row per TTP.

        Body (JSON):
          ttp_ids: list[str] — MITRE technique IDs (alias: "path")

        Returns verify_path() result:
          {path, results[{ttp_id, state, coverage_row_id}], summary{full,partial,none,total}}
        """
        from tools.observability_canvas.replay_verify import verify_path

        data = request.get_json(force=True, silent=True) or {}
        raw = data.get("ttp_ids", data.get("path", []))
        if not isinstance(raw, list):
            return jsonify({"error": "ttp_ids must be a list"}), 400
        ttp_ids = [str(t).strip() for t in raw if str(t).strip()]

        try:
            result = verify_path(ttp_ids, design_id=design_id)
        except Exception as exc:
            logger.warning("replay verify failed for %s: %s", design_id, exc)
            return jsonify({"error": str(exc)}), 500

        _audit("replay_verify", design_id, f"ttps={len(ttp_ids)} summary={result.get('summary')}")
        return jsonify(result)

    # ====================================================================
    # API — MITRE SIGNAL-SOURCE COVERAGE (mitre_coverage_twin)
    # ====================================================================
    #
    # obx-cov-01: expose the previously-orphaned mitre_coverage_twin engine.
    # These routes are the runtime writers of odc_gap_scores /
    # odc_technique_coverage — the primary read of the ODC compliance card
    # (tools/canvas_compliance/compliance.py::get_odc_card). NOTE: this is the
    # signal-source gap metric (design signal sources vs required MITRE detection
    # sources), NOT the design-baseline coverage from a cmp-baseline node that
    # observability_engine.compute_mitre_detection_coverage computes.

    def _odc_load_graph(design_id):
        """Load and parse a design's graph_json; returns (graph_data, error_resp).

        error_resp is None on success, else a (json, status) tuple to return.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None, (jsonify({"error": "Design not found"}), 404)
        graph_raw = row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            return None, (jsonify({"error": "Invalid graph data"}), 400)
        return graph_data, None

    @bp.route("/api/coverage/<design_id>/compute", methods=["POST"])
    @oc_login_required
    def oc_api_coverage_compute(design_id):
        """Compute signal-source coverage gap score and persist odc_gap_scores.

        Runs mitre_coverage_twin.compute_gap_score, which persists both
        odc_gap_scores and odc_technique_coverage as a side effect, and returns
        the score JSON.
        """
        from tools.observability_canvas.mitre_coverage_twin import compute_gap_score

        graph_data, err = _odc_load_graph(design_id)
        if err is not None:
            return err
        result = compute_gap_score(design_id, graph_data)
        _audit("coverage_compute", design_id, f"gap_score={result.get('gap_score')}")
        return jsonify(result)

    @bp.route("/api/coverage/<design_id>/report", methods=["GET"])
    @oc_login_required
    def oc_api_coverage_report(design_id):
        """Return the full signal-source gap report (generate_gap_report output).

        Includes prioritized remediation steps and quick wins. Recomputes and
        persists the gap score as a side effect (shared compute_gap_score path).
        """
        from tools.observability_canvas.mitre_coverage_twin import generate_gap_report

        graph_data, err = _odc_load_graph(design_id)
        if err is not None:
            return err
        report = generate_gap_report(design_id, graph_data)
        return jsonify(report)

    # ====================================================================
    # API — AUTO-FIX
    # ====================================================================

    @bp.route("/api/designs/<design_id>/auto-fix", methods=["POST"])
    @oc_login_required
    def oc_api_auto_fix(design_id):
        """Auto-remediate an observability design by injecting missing nodes.

        Runs assessment, calls shared auto-remediation engine, saves updated
        graph, re-assesses, returns {fixes_applied, old_score, new_score}.
        """
        from tools.canvas.auto_remediate import auto_remediate_odc

        conn = get_connection()
        try:
            row = conn.execute("SELECT graph_json FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Design not found"}), 404

        graph_raw = row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        old_assessment = assess_observability_design(graph_data)
        old_score = old_assessment["score"]

        fix_result = auto_remediate_odc(design_id, graph_data, old_assessment["findings"])

        if fix_result.get("rate_limited"):
            return jsonify({"error": "Rate limit reached (max 5 auto-fixes/hour)"}), 429

        # Save updated graph
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE observability_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (json.dumps(graph_data), _now(), design_id),
            )
            conn.commit()
        finally:
            conn.close()

        new_assessment = assess_observability_design(graph_data)
        new_score = new_assessment["score"]

        _audit("AUTO_FIX", design_id, f"Fixes: {fix_result['fixes_applied']}, Score: {old_score} -> {new_score}")

        return jsonify(
            {
                "status": "ok",
                "design_id": design_id,
                "fixes_applied": fix_result["fixes_applied"],
                "old_score": old_score,
                "new_score": new_score,
                "nodes_added": fix_result["nodes_added"],
                "edges_added": fix_result["edges_added"],
            }
        )

    # ====================================================================
    # API — TEMPLATES
    # ====================================================================

    @bp.route("/api/templates", methods=["GET"])
    @oc_login_required
    def oc_api_templates():
        """List all observability design templates."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, category, description, tags FROM od_templates ORDER BY category, name"
            ).fetchall()
        finally:
            conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/snippets", methods=["GET"])
    @oc_login_required
    def oc_api_snippets():
        """List available ODC snippets (reusable graph fragments)."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, category, description, graph_json, tags FROM od_snippets ORDER BY category, name"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"snippets": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/templates/<template_id>", methods=["GET"])
    @oc_login_required
    def oc_api_get_template(template_id):
        """Get a specific template with full graph_json."""
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM od_templates WHERE id=%s", (template_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Template not found"}), 404
        return jsonify(_row_to_dict(row))

    # ====================================================================
    # API ROUTES — Design Versioning
    # ====================================================================

    def _odc_diff_graph(old: dict, new: dict) -> str:
        """Return a human-readable change summary between two graph states."""
        old_nodes = {n.get("id") for n in old.get("nodes", [])}
        new_nodes = {n.get("id") for n in new.get("nodes", [])}
        added = len(new_nodes - old_nodes)
        removed = len(old_nodes - new_nodes)
        old_edges = {(e.get("source"), e.get("target")) for e in old.get("edges", [])}
        new_edges = {(e.get("source"), e.get("target")) for e in new.get("edges", [])}
        e_added = len(new_edges - old_edges)
        e_removed = len(old_edges - new_edges)
        parts = []
        if added:
            parts.append(f"+{added} node(s)")
        if removed:
            parts.append(f"-{removed} node(s)")
        if e_added:
            parts.append(f"+{e_added} edge(s)")
        if e_removed:
            parts.append(f"-{e_removed} edge(s)")
        return ", ".join(parts) if parts else "No structural changes"

    @bp.route("/api/versions/<design_id>", methods=["GET"])
    @oc_login_required
    def oc_api_list_versions(design_id):
        """List all version snapshots for an observability design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, version_number, change_summary, user_id, created_at "
                "FROM od_versions WHERE design_id=%s ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @oc_login_required
    def oc_api_create_version(design_id):
        """Create a version snapshot of the current observability design state."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            raw = _row_to_dict(row)["graph_json"]
            try:
                current_graph = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                current_graph = {}
            ver_num = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM od_versions WHERE design_id=%s",
                (design_id,),
            ).fetchone()[0]
            change_summary = data.get("change_summary", "")
            if not change_summary:
                prev = conn.execute(
                    "SELECT graph_json FROM od_versions WHERE design_id=%s ORDER BY version_number DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if prev:
                    try:
                        prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                        change_summary = _odc_diff_graph(prev_graph, current_graph)
                    except Exception:
                        pass
            ver_id = str(_uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO od_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    ver_id,
                    design_id,
                    ver_num,
                    json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
                    change_summary,
                    session.get("user_id", ""),
                    now,
                ),
            )
        _audit("VERSION_CREATE", design_id, f"v{ver_num}")
        return jsonify(
            {"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}
        ), 201

    @bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
    @oc_login_required
    def oc_api_restore_version(design_id, version_id):
        """Restore an observability design to a previous version snapshot."""
        with get_connection() as conn:
            ver = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=%s AND design_id=%s",
                (version_id, design_id),
            ).fetchone()
            if not ver:
                return jsonify({"error": "Version not found"}), 404
            now = _now()
            conn.execute(
                "UPDATE observability_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (ver[0], now, design_id),
            )
        _audit("VERSION_RESTORE", design_id, f"restored to v{ver[1]}")
        return jsonify({"id": design_id, "restored_version": version_id, "version_number": ver[1], "updated_at": now})

    @bp.route("/api/versions/<design_id>/diff", methods=["POST"])
    @oc_login_required
    def oc_api_diff_versions(design_id):
        """Compare two version snapshots of an observability design."""
        data = request.get_json(force=True, silent=True) or {}
        ver_a_id = data.get("version_a")
        ver_b_id = data.get("version_b")
        if not ver_a_id or not ver_b_id:
            return jsonify({"error": "version_a and version_b required"}), 400
        with get_connection() as conn:
            ver_a = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=%s AND design_id=%s",
                (ver_a_id, design_id),
            ).fetchone()
            ver_b = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=%s AND design_id=%s",
                (ver_b_id, design_id),
            ).fetchone()
        if not ver_a or not ver_b:
            return jsonify({"error": "One or both versions not found"}), 404
        try:
            graph_a = json.loads(ver_a[0]) if isinstance(ver_a[0], str) else ver_a[0]
            graph_b = json.loads(ver_b[0]) if isinstance(ver_b[0], str) else ver_b[0]
        except Exception:
            return jsonify({"error": "Failed to parse graph data"}), 500
        summary = _odc_diff_graph(graph_a, graph_b)
        return jsonify(
            {
                "version_a": {"id": ver_a_id, "version_number": ver_a[1]},
                "version_b": {"id": ver_b_id, "version_number": ver_b[1]},
                "summary": summary,
            }
        )

    # ====================================================================
    # API — OBJECTS PALETTE
    # ====================================================================

    @bp.route("/api/objects")
    def oc_api_objects():
        """Return the full observability object palette for the canvas UI."""
        return jsonify(OBSERVABILITY_OBJECTS)

    @bp.route("/api/rules")
    def oc_api_rules():
        """Return the observability compliance rules."""
        return jsonify(OBSERVABILITY_COMPLIANCE_RULES)

    @bp.route("/api/export/<design_id>/vsdx", methods=["POST"])
    @oc_login_required
    def oc_api_export_vsdx(design_id):
        """Export observability design as Visio .vsdx file."""
        import base64

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph_data = json.loads(gj) if isinstance(gj, str) else gj
        from tools.network.visio_export import export_vsdx

        vsdx_bytes = export_vsdx(d["name"], graph_data)
        return jsonify(
            {
                "format": "vsdx",
                "filename": d["name"].replace(" ", "_"),
                "data": base64.b64encode(vsdx_bytes).decode("ascii"),
            }
        )

    def _odc_fetch(design_id):
        with get_connection() as conn:
            row = conn.execute("SELECT name, graph_json FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return None, None
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        return d["name"], graph

    @bp.route("/api/export/<design_id>/json", methods=["POST"])
    @oc_login_required
    def oc_api_export_json(design_id):
        """Export observability design as JSON."""
        import base64

        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_json

        data = base64.b64encode(export_json(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "json", "filename": f"{name.replace(' ', '_')}.json", "data": data})

    @bp.route("/api/export/<design_id>/markdown", methods=["POST"])
    @oc_login_required
    def oc_api_export_markdown(design_id):
        """Export observability design as Markdown."""
        import base64

        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_markdown

        data = base64.b64encode(export_markdown(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "markdown", "filename": f"{name.replace(' ', '_')}.md", "data": data})

    @bp.route("/api/export/<design_id>/csv", methods=["POST"])
    @oc_login_required
    def oc_api_export_csv(design_id):
        """Export observability design node inventory as CSV."""
        import base64

        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_csv

        data = base64.b64encode(export_csv(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "csv", "filename": f"{name.replace(' ', '_')}.csv", "data": data})

    @bp.route("/api/export/<design_id>/drawio", methods=["POST"])
    @oc_login_required
    def oc_api_export_drawio(design_id):
        """Export observability design as DrawIO XML."""
        import base64

        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_drawio

        data = base64.b64encode(export_drawio(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "drawio", "filename": f"{name.replace(' ', '_')}.drawio", "data": data})

    @bp.route("/api/export/<design_id>/svg", methods=["POST"])
    @oc_login_required
    def oc_api_export_svg(design_id):
        """Export observability design as SVG vector graphic."""
        import base64

        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_svg

        data = base64.b64encode(export_svg(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "svg", "filename": f"{name.replace(' ', '_')}.svg", "data": data})

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _ODCCollabMgr

    _odc_collab = _ODCCollabMgr("od")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @oc_login_required
    def oc_collab_join(design_id):
        """Join a collaborative ODC editing session."""
        body = request.json or {}
        user_id = body.get("user_id", str(_uuid_mod.uuid4())[:8])
        user_name = body.get("user_name", "")
        return jsonify(_odc_collab.join(design_id, user_id, user_name))

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @oc_login_required
    def oc_collab_leave(design_id):
        """Leave an ODC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        _odc_collab.leave(design_id, user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @oc_login_required
    def oc_collab_push(design_id):
        """Push an operation into an ODC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        seq = _odc_collab.push(design_id, user_id, op_type, data)
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @oc_login_required
    def oc_collab_poll(design_id):
        """Poll for ODC collaborative operations since a sequence number."""
        since = int(request.args.get("since", 0))
        user_id = request.args.get("user_id", "")
        cx = request.args.get("cx")
        cy = request.args.get("cy")
        if user_id and cx is not None and cy is not None:
            _odc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
        ops, participants, latest_seq = _odc_collab.poll(design_id, since)
        return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @oc_login_required
    def oc_collab_participants(design_id):
        """Return current participants in an ODC collaborative session."""
        return jsonify({"participants": _odc_collab.get_participants(design_id)})

    # ====================================================================
    # API — Sigma Rule Generation & Cost Estimation
    # ====================================================================

    @bp.route("/api/export/<design_id>/sigma", methods=["POST"])
    @oc_login_required
    def oc_export_sigma(design_id):
        """Generate Sigma detection rules from an observability design.

        Body params (all optional):
          format: 'sigma'|'splunk'|'elastic'|'sentinel'|'all' (default: all)
          retention_days: int — log retention for cost estimates (default: 90)

        Returns:
          rule_count, exports dict (sigma_yaml, splunk_spl, elastic_kql,
          sentinel_kql), volume_estimate with SIEM cost projections.
        """
        import base64
        from tools.observability_canvas.sigma_generator import generate_sigma_rules

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        row_d = _row_to_dict(row)
        try:
            graph = json.loads(row_d["graph_json"]) if isinstance(row_d["graph_json"], str) else row_d["graph_json"]
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        body = request.get_json(force=True, silent=True) or {}
        retention_days = int(body.get("retention_days", 90))
        fmt = body.get("format", "all").lower()

        result = generate_sigma_rules(graph, design_name=row_d["name"])

        # Override volume estimate retention_days if specified
        if retention_days != 90:
            from tools.observability_canvas.sigma_generator import estimate_log_volume

            result["volume_estimate"] = estimate_log_volume(graph.get("nodes", []), retention_days)

        _audit("export_sigma", design_id, f"rules={result['rule_count']} format={fmt}")

        # Filter exports if specific format requested
        exports = result["exports"]
        if fmt != "all":
            key_map = {
                "sigma": "sigma_yaml",
                "splunk": "splunk_spl",
                "elastic": "elastic_kql",
                "sentinel": "sentinel_kql",
            }
            key = key_map.get(fmt, "sigma_yaml")
            content = exports.get(key, "")
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            ext_map = {"sigma_yaml": "yml", "splunk_spl": "spl", "elastic_kql": "kql", "sentinel_kql": "kql"}
            filename = f"{row_d['name'].replace(' ', '_')}_sigma.{ext_map.get(key, 'txt')}"
            return jsonify(
                {
                    "rule_count": result["rule_count"],
                    "format": fmt,
                    "filename": filename,
                    "data": encoded,
                    "volume_estimate": result["volume_estimate"],
                    "generated_at": result["generated_at"],
                }
            )

        return jsonify(
            {
                "rule_count": result["rule_count"],
                "rules": result["rules"],
                "exports": exports,
                "volume_estimate": result["volume_estimate"],
                "generated_at": result["generated_at"],
            }
        )

    @bp.route("/api/designs/<design_id>/volume-estimate", methods=["GET"])
    @oc_login_required
    def oc_volume_estimate(design_id):
        """Compute log volume and SIEM cost estimates for a design.

        Query params:
          retention_days: int (default: 90)
        """
        from tools.observability_canvas.sigma_generator import estimate_log_volume

        retention_days = int(request.args.get("retention_days", 90))
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        try:
            row_d = _row_to_dict(row)
            graph = json.loads(row_d["graph_json"]) if isinstance(row_d["graph_json"], str) else row_d["graph_json"]
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        nodes = graph.get("nodes", [])
        result = estimate_log_volume(nodes, retention_days=retention_days)
        return jsonify(result)

    # ====================================================================
    # SOP ROUTES
    # ====================================================================

    from tools.observability_canvas import sops as _sops_mod

    @bp.route("/sops")
    @oc_login_required
    def oc_sops_page():
        """Render the SOPs management page."""
        sop_type = request.args.get("sop_type")
        approval_status = request.args.get("approval_status")
        sops_list = _sops_mod.get_all_sops(sop_type=sop_type, approval_status=approval_status)
        return render_template(
            "observability_canvas/sops.html",
            sops=sops_list,
            active_page="observability",
        )

    @bp.route("/api/sops", methods=["GET"])
    @oc_login_required
    def oc_api_sops_list():
        sop_type = request.args.get("sop_type")
        approval_status = request.args.get("approval_status")
        return jsonify(_sops_mod.get_all_sops(sop_type=sop_type, approval_status=approval_status))

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @oc_login_required
    def oc_api_sop_get(sop_id):
        sop = _sops_mod.get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops", methods=["POST"])
    @oc_login_required
    def oc_api_sop_create():
        data = request.get_json(silent=True) or {}
        sop = _sops_mod.create_sop(data)
        _audit("sop_create", details=sop.get("title", ""))
        return jsonify(sop), 201

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @oc_login_required
    def oc_api_sop_update(sop_id):
        data = request.get_json(silent=True) or {}
        sop = _sops_mod.update_sop(sop_id, data)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        _audit("sop_update", details=sop_id)
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @oc_login_required
    def oc_api_sop_delete(sop_id):
        deleted = _sops_mod.delete_sop(sop_id)
        if not deleted:
            return jsonify({"error": "Not found"}), 404
        _audit("sop_delete", details=sop_id)
        return jsonify({"deleted": True})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @oc_login_required
    def oc_api_sop_submit(sop_id):
        sop, err = _sops_mod.submit_for_review(sop_id)
        if err:
            return jsonify({"error": err}), 400
        _audit("sop_submit", details=sop_id)
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @oc_login_required
    def oc_api_sop_approve(sop_id):
        data = request.get_json(silent=True) or {}
        approved_by = data.get("approved_by", session.get("user_id", ""))
        sop, err = _sops_mod.approve_sop(sop_id, approved_by=approved_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("sop_approve", details=sop_id)
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/reject", methods=["POST"])
    @oc_login_required
    def oc_api_sop_reject(sop_id):
        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "")
        rejected_by = data.get("rejected_by", session.get("user_id", ""))
        sop, err = _sops_mod.reject_sop(sop_id, reason=reason, rejected_by=rejected_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("sop_reject", details=sop_id)
        return jsonify(sop)

    # ====================================================================
    # RUNBOOK ROUTES
    # ====================================================================

    from tools.observability_canvas import runbooks as _runbooks_mod

    @bp.route("/runbooks")
    @oc_login_required
    def oc_runbooks_page():
        """Render the runbooks management page."""
        category = request.args.get("category")
        severity = request.args.get("severity")
        runbooks_list = _runbooks_mod.get_all_runbooks(category=category, severity=severity)
        return render_template(
            "observability_canvas/runbooks.html",
            runbooks=runbooks_list,
            active_page="observability",
        )

    @bp.route("/api/runbooks", methods=["GET"])
    @oc_login_required
    def oc_api_runbooks_list():
        category = request.args.get("category")
        severity = request.args.get("severity")
        return jsonify(_runbooks_mod.get_all_runbooks(category=category, severity=severity))

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @oc_login_required
    def oc_api_runbook_get(runbook_id):
        rb = _runbooks_mod.get_runbook_by_id(runbook_id)
        if not rb:
            return jsonify({"error": "Not found"}), 404
        return jsonify(rb)

    @bp.route("/api/runbooks", methods=["POST"])
    @oc_login_required
    def oc_api_runbook_create():
        data = request.get_json(silent=True) or {}
        rb = _runbooks_mod.create_runbook(data)
        _audit("runbook_create", details=rb.get("title", ""))
        return jsonify(rb), 201

    @bp.route("/api/runbooks/<runbook_id>", methods=["PUT"])
    @oc_login_required
    def oc_api_runbook_update(runbook_id):
        data = request.get_json(silent=True) or {}
        rb = _runbooks_mod.update_runbook(runbook_id, data)
        if not rb:
            return jsonify({"error": "Not found"}), 404
        _audit("runbook_update", details=runbook_id)
        return jsonify(rb)

    @bp.route("/api/runbooks/<runbook_id>", methods=["DELETE"])
    @oc_login_required
    def oc_api_runbook_delete(runbook_id):
        deleted = _runbooks_mod.delete_runbook(runbook_id)
        if not deleted:
            return jsonify({"error": "Not found"}), 404
        _audit("runbook_delete", details=runbook_id)
        return jsonify({"deleted": True})

    @bp.route("/api/runbooks/<runbook_id>/execute", methods=["POST"])
    @oc_login_required
    def oc_api_runbook_execute(runbook_id):
        """Record that a runbook was executed."""
        rb = _runbooks_mod.record_execution(runbook_id)
        if not rb:
            return jsonify({"error": "Not found"}), 404
        _audit("runbook_execute", details=runbook_id)
        return jsonify(rb)

    # ====================================================================
    # MITRE ATT&CK MATRIX ROUTES
    # ====================================================================

    @bp.route("/mitre")
    @oc_login_required
    def oc_mitre_matrix():
        """MITRE ATT&CK matrix with coverage colors. Optional ?design_id= for scoped coverage."""
        from tools.observability_canvas.mitre_loader import load_techniques

        techniques = load_techniques()

        _catalog_path = _ICDEV_ROOT / "context" / "mitre" / "enterprise.json"
        tactic_name_map: dict = {}
        try:
            with open(_catalog_path, encoding="utf-8") as _fh:
                _cat = json.load(_fh)
            tactic_name_map = {t["id"]: t["name"] for t in _cat.get("tactics", [])}
        except Exception:
            pass

        tactics_order = list(tactic_name_map.keys())
        tactics_dict: dict = {}
        for tech in techniques:
            for tactic_id in tech.tactic_ids:
                if tactic_id not in tactics_dict:
                    tactics_dict[tactic_id] = {
                        "id": tactic_id,
                        "name": tactic_name_map.get(tactic_id, tactic_id),
                        "techniques": [],
                    }
                tactics_dict[tactic_id]["techniques"].append(
                    {
                        "id": tech.id,
                        "name": tech.name,
                        "is_sub": tech.is_sub_technique,
                        "parent_id": tech.parent_id,
                    }
                )

        tactics = [tactics_dict[tid] for tid in tactics_order if tid in tactics_dict]
        for tid, tactic in tactics_dict.items():
            if tid not in tactics_order:
                tactics.append(tactic)

        design_id = request.args.get("design_id", "")
        coverage: dict = {}
        if design_id:
            try:
                with get_connection() as conn:
                    row = conn.execute(
                        "SELECT graph_json FROM observability_designs WHERE id=%s",
                        (design_id,),
                    ).fetchone()
                if row:
                    graph_raw = row["graph_json"]
                    graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
                    mitre = compute_mitre_detection_coverage(graph_data)
                    for gap_tid in mitre.get("technique_gaps", []):
                        coverage[gap_tid] = "gap"
            except Exception:
                pass

        return render_template(
            "observability_canvas/mitre.html",
            tactics=tactics,
            coverage=coverage,
            design_id=design_id,
        )

    @bp.route("/mitre/sigma", methods=["POST"])
    @oc_login_required
    def oc_mitre_sigma():
        """Generate Sigma rule(s) for a MITRE technique + signal source list."""
        from tools.observability_canvas.sigma_generator import generate_rules

        data = request.get_json(force=True, silent=True) or {}
        tid = (data.get("tid") or "").strip()
        sources = data.get("sources") or []

        if not tid:
            return jsonify({"error": "tid is required"}), 400
        if not isinstance(sources, list) or not sources:
            return jsonify({"error": "sources must be a non-empty list"}), 400

        pairs = [(tid, src) for src in sources if isinstance(src, str) and src.strip()]
        if not pairs:
            return jsonify({"error": "No valid (tid, source) pairs"}), 400

        rules = generate_rules(pairs)
        combined = "\n---\n".join(rules)

        return jsonify(
            {
                "tid": tid,
                "rule_count": len(rules),
                "rules": rules,
                "sigma_yaml": combined,
            }
        )

    @bp.route("/mitre/<tid>")
    @oc_login_required
    def oc_mitre_detail(tid):
        """Technique detail: description, sub-techniques, Sigma generator form."""
        from tools.observability_canvas.mitre_loader import load_techniques
        from tools.observability_canvas.sigma_generator import _SRC_LOGSOURCE

        techniques = load_techniques()
        technique = next((t for t in techniques if t.id == tid), None)
        if not technique:
            from flask import abort

            abort(404)

        subs = [t for t in techniques if t.parent_id == tid]
        sigma_sources = sorted(_SRC_LOGSOURCE.keys())

        return render_template(
            "observability_canvas/mitre_detail.html",
            technique=technique,
            subs=subs,
            sigma_sources=sigma_sources,
        )

    # ── GraphRAG /ask — shared canvas_ask pattern ──────────────────────────
    @bp.route("/ask")
    @oc_login_required
    def odc_ask_page():
        return render_template(
            "canvas_ask.html",
            canvas_label="Observability Design Canvas",
            graph_id="odc-designs",
            profile="security",
            examples=["detection", "sigma", "MITRE", "SIEM", "log source"],
            api_url="/observability/api/ask",
            home_url="/observability/",
        )

    @bp.route("/api/ask", methods=["POST"])
    @oc_login_required
    def odc_api_ask():
        from tools.knowledge_graph.canvas_ask import handle_ask_request
        data = request.get_json(silent=True) or {}
        payload = handle_ask_request(
            query=data.get("query", ""),
            graph_id="odc-designs",
            profile="security",
            top_k=int(data.get("top_k", 10)),
            narrate=bool(data.get("narrate", False)),
            canvas_label="observability / detection coverage",
        )
        status = payload.pop("_status", 200)
        return jsonify(payload), status

    # ── Digital Twin ───────────────────────────────────────────────────────
    @bp.route("/twin/<design_id>")
    @oc_login_required
    def oc_twin_page(design_id):
        conn = get_connection()
        design = conn.execute("SELECT * FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        if not design:
            return render_template("404.html"), 404
        design = _row_to_dict(design)
        # Read snapshots through the same code path take_snapshot writes to
        # (twin.list_snapshots → canvas connection), so read DB == write DB.
        try:
            from tools.observability_canvas.twin import list_snapshots
            snapshots = list_snapshots(design_id)
        except Exception:
            snapshots = []
        return render_template(
            "observability_canvas/twin.html",
            design=design,
            snapshots=snapshots,
        )

    @bp.route("/api/twin/<design_id>/snapshot", methods=["POST"])
    @oc_login_required
    def oc_api_twin_snapshot(design_id):
        from tools.observability_canvas.twin import take_snapshot
        data = request.get_json(silent=True) or {}
        try:
            snap = take_snapshot(design_id, label=data.get("label"))
        except ValueError as exc:
            # Unknown design — surface a 404 rather than a fake 201.
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            # Persist failure — surface a 500 rather than a fake 201.
            return jsonify({"error": f"snapshot persist failed: {exc}"}), 500
        return jsonify(snap), 201

    @bp.route("/api/twin/<design_id>/simulate", methods=["POST"])
    @oc_login_required
    def oc_api_twin_simulate(design_id):
        from tools.observability_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            collector_delta=data.get("collector_delta", {}),
            signals=data.get("signals", ["all"]),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/slo-projection", methods=["POST"])
    @oc_login_required
    def oc_api_twin_slo(design_id):
        from tools.observability_canvas.twin import slo_projection
        data = request.get_json(silent=True) or {}
        result = slo_projection(design_id, collector_delta=data.get("collector_delta"), baseline_snap_id=data.get("baseline_snap_id"))
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/current-topology", methods=["GET"])
    @oc_login_required
    def oc_api_twin_current_topology(design_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM observability_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}
        return jsonify({"graph_json": graph}), 200

    @bp.route("/api/twin/<design_id>/chat-delta", methods=["POST"])
    @oc_login_required
    def oc_api_twin_chat_delta(design_id):
        from tools.twin_chat import observability_chat_to_delta
        data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        result = observability_chat_to_delta(message, data.get("graph_json"))
        return jsonify(result), (500 if "error" in result else 200)

    # ====================================================================
    # API — MITRE ATT&CK / Sigma (standalone technique endpoint)
    # ====================================================================

    @bp.route("/api/sigma/generate", methods=["POST"])
    @oc_login_required
    def oc_api_sigma_generate():
        """Generate a Sigma YAML rule for a single MITRE ATT&CK technique.

        Body params:
          technique_id: str — MITRE technique ID (e.g. 'T1055')

        Returns:
          {"technique_id": str, "sigma_yaml": str}
        """
        from tools.observability.sigma_generator import generate_sigma

        data = request.get_json(force=True, silent=True) or {}
        tid = (data.get("technique_id") or "").strip()
        if not tid:
            return jsonify({"error": "technique_id is required"}), 400

        try:
            sigma_yaml = generate_sigma(tid)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.warning("sigma generate failed for %s: %s", tid, exc)
            return jsonify({"error": "sigma generation failed"}), 500

        _audit("sigma_generate", details=tid)
        return jsonify({"technique_id": tid, "sigma_yaml": sigma_yaml})

    @bp.route("/api/mitre/ingest", methods=["POST"])
    @oc_login_required
    def oc_api_mitre_ingest():
        """Admin: ingest MITRE ATT&CK techniques into odc_mitre_techniques.

        Wraps tools.observability.mitre_ingestor.ingest (previously CLI-only).

        Body (JSON):
          source: 'local' (default) — seed FROM the single-source-of-truth
                  mitre_catalog.MITRE_CATALOG code constant (offline, drift-free);
                  'stix' — external MITRE STIX bundle / enterprise.json mirror.
          force_download: bool — fetch the external STIX bundle (source='stix').
          catalog_path:   str  — optional STIX/enterprise.json file (source='stix').

        Returns {ingested, skipped, errors, source}.
        """
        from tools.observability.mitre_ingestor import ingest as _mitre_ingest

        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "local").strip().lower()
        if source not in ("local", "stix"):
            return jsonify({"error": "source must be 'local' or 'stix'"}), 400

        try:
            if source == "stix":
                cat = data.get("catalog_path")
                result = _mitre_ingest(
                    catalog_path=Path(cat) if cat else None,
                    force_download=bool(data.get("force_download", False)),
                    source="stix",
                )
            else:
                result = _mitre_ingest(source="local")
        except Exception as exc:
            logger.warning("MITRE ingest failed (source=%s): %s", source, exc)
            return jsonify({"error": str(exc)}), 500

        result["source"] = source
        _audit("mitre_ingest", details=f"source={source} ingested={result.get('ingested')}")
        return jsonify(result)

    # ====================================================================
    # Kill Chain — force-directed graph page + API
    # ====================================================================

    @bp.route("/kill-chain")
    @oc_login_required
    def oc_kill_chain():
        """Kill Chain force-directed D3.js graph — war-domain actors (Sandworm/APT28)."""
        return render_template("observability_canvas/kill_chain.html")

    @bp.route("/api/kill-chain")
    @oc_login_required
    def oc_api_kill_chain():
        """Return KG nodes + edges from the 'sg' canvas for D3.js rendering.

        Query params
        ------------
        actor : optional filter — 'sandworm' | 'apt28' (matches label/aliases)
        limit : max nodes to return (default 300, max 1000)

        Returns
        -------
        {nodes: [{id, label, type, technique_ids, tactic, aoi, event_code}],
         links: [{source, target, relation, delta_hours}]}
        """
        actor = request.args.get("actor", "").strip().lower()
        try:
            limit = min(int(request.args.get("limit", 300)), 1000)
        except (ValueError, TypeError):
            limit = 300

        # Actor alias lookup for label-based filtering
        actor_aliases: set[str] = set()
        if actor:
            try:
                from tools.observability_canvas.mitre_loader import WAR_DOMAIN_ACTORS
                profile = WAR_DOMAIN_ACTORS.get(actor)
                if profile:
                    actor_aliases = {a.lower() for a in (profile.aliases + (profile.name,))}
                    actor_aliases.add(actor)
            except Exception as exc:
                logger.debug("actor alias lookup failed: %s", exc)

        # canvas_kg_nodes/canvas_kg_edges are canvas-namespaced tables with no
        # tenant_id/classification columns, so the global RLS predicate would raise
        # UndefinedColumn under any authenticated security context.  Use the
        # RLS-bypassing canvas connection.
        from tools.db.storage import get_canvas_connection

        conn = get_canvas_connection()
        try:
            # Ensure KG tables exist (created by stix_importer / temporal_correlator)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS canvas_kg_nodes ("
                "id TEXT PRIMARY KEY, canvas TEXT NOT NULL, design_id TEXT NOT NULL, "
                "node_id TEXT NOT NULL, node_type TEXT, label TEXT, "
                "metadata_json TEXT, updated_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS canvas_kg_edges ("
                "id TEXT PRIMARY KEY, canvas TEXT NOT NULL, design_id TEXT NOT NULL, "
                "source_id TEXT NOT NULL, target_id TEXT NOT NULL, edge_type TEXT, "
                "metadata_json TEXT, updated_at TEXT)"
            )

            raw_nodes = conn.execute(
                "SELECT node_id, node_type, label, metadata_json "
                "FROM canvas_kg_nodes WHERE canvas = 'sg' LIMIT %s",
                (limit * 3,),
            ).fetchall()

            raw_edges = conn.execute(
                "SELECT source_id, target_id, edge_type, metadata_json "
                "FROM canvas_kg_edges WHERE canvas = 'sg' LIMIT %s",
                (limit * 5,),
            ).fetchall()
        except Exception as exc:
            logger.warning("kill-chain KG query failed: %s", exc)
            return jsonify({"nodes": [], "links": [], "error": str(exc)})
        finally:
            conn.close()

        # Build node list
        nodes: list[dict] = []
        node_id_set: set[str] = set()

        for r in raw_nodes:
            node_id = r[0] if isinstance(r, (list, tuple)) else r["node_id"]
            node_type = (r[1] if isinstance(r, (list, tuple)) else r["node_type"]) or "Unknown"
            label = (r[2] if isinstance(r, (list, tuple)) else r["label"]) or node_id
            meta_raw = r[3] if isinstance(r, (list, tuple)) else r["metadata_json"]

            meta: dict = {}
            try:
                meta = json.loads(meta_raw or "{}")
            except (json.JSONDecodeError, TypeError):
                pass

            # Actor filter: if requested, keep only nodes whose label or aliases match,
            # plus all Technique/Malware/Tool nodes (context nodes)
            if actor_aliases and node_type == "ThreatActor":
                if not any(alias in label.lower() for alias in actor_aliases):
                    continue

            nodes.append({
                "id":            node_id,
                "label":         label,
                "type":          node_type,
                "technique_ids": meta.get("technique_ids") or meta.get("technique_ids"),
                "tactic":        meta.get("tactic_ids"),
                "aoi":           meta.get("aoi"),
                "event_code":    meta.get("event_code"),
            })
            node_id_set.add(node_id)

        # Build link list — only include edges where both endpoints are in node set
        links: list[dict] = []
        for r in raw_edges:
            src = r[0] if isinstance(r, (list, tuple)) else r["source_id"]
            tgt = r[1] if isinstance(r, (list, tuple)) else r["target_id"]
            rel = (r[2] if isinstance(r, (list, tuple)) else r["edge_type"]) or "related-to"
            meta_raw = r[3] if isinstance(r, (list, tuple)) else r["metadata_json"]

            if src not in node_id_set or tgt not in node_id_set:
                continue

            meta: dict = {}
            try:
                meta = json.loads(meta_raw or "{}")
            except (json.JSONDecodeError, TypeError):
                pass

            links.append({
                "source":      src,
                "target":      tgt,
                "relation":    rel,
                "delta_hours": meta.get("delta_hours"),
            })

        return jsonify({"nodes": nodes, "links": links})

    # ====================================================================
    # API — Temporal Correlator trigger
    # ====================================================================

    @bp.route("/api/correlator/run", methods=["POST"])
    @oc_login_required
    def oc_api_correlator_run():
        """Trigger the temporal correlator (CyberOperation ↔ ConflictEvent, 72 h window).

        Body (optional JSON):
          window_hours: int — override default 72 h window

        Returns correlate() summary dict.
        """
        data = request.get_json(silent=True) or {}
        try:
            window_hours = int(data.get("window_hours", 72))
            window_hours = max(1, min(window_hours, 8760))  # 1 h – 1 year
        except (ValueError, TypeError):
            window_hours = 72

        try:
            from tools.strategos.temporal_correlator import correlate
            result = correlate(window_hours=window_hours, dry_run=False, as_json=False)
        except Exception as exc:
            logger.warning("temporal correlator failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        _audit("correlator_run", details=f"window={window_hours}h edges={result.get('edges_written',0)}")
        return jsonify(result)

    @bp.route("/api/iqe-query", methods=["POST"])
    @oc_login_required
    def odc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against ODC MITRE coverage data."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.observability  # noqa: F401 — registers mitre.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        collections = ["mitre.techniques", "mitre.coverage", "mitre.gaps"]
        translation = nl_to_iqe(question, collections)
        iqe_str = translation.get("iqe", "")
        explanation = translation.get("explanation", "")

        if not data.get("execute", True):
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

        try:
            ast = parse(iqe_str)
            rows = execute_query(ast, None)
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                            "results": rows, "row_count": len(rows)}), 200
        except IQESyntaxError as exc:
            return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
        except Exception as exc:
            logger.warning("ODC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    @bp.route("/api/ai-trace")
    @oc_login_required
    def oc_api_ai_trace():
        """Return recent AI decisions made by ODC assessment engines."""
        limit = min(int(request.args.get("limit", 50)), 200)
        record_id = request.args.get("record_id")
        try:
            # canvas_ai_decisions is a canvas-namespaced table without
            # tenant_id/classification columns; the global RLS predicate would raise
            # UndefinedColumn under an authenticated security context.  Use the
            # RLS-bypassing canvas connection.
            from tools.db.storage import get_canvas_connection
            with get_canvas_connection() as _conn:
                if record_id:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='odc' AND record_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='odc' "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "odc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("ODC ai-trace query failed: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    def _compute_odc_governance(graph_data: dict) -> dict:
        """Observability governance check — DORA / SRE / OpenTelemetry aligned."""
        nodes = graph_data.get("nodes", [])
        types = [n.get("type", "").lower() for n in nodes]
        labels = [str(n.get("label", "")).lower() for n in nodes]

        def _any(*pfx): return any(any(t.startswith(p) for p in pfx) for t in types)
        def _lbl(*kws): return any(kw in l for l in labels for kw in kws)

        CHECKS = [
            ("Metrics Collection",            "Metrics Coverage",   "CAT1", _any("plt-prom","plt-datadog","plt-otel","src-") or _lbl("prometheus","metrics","statsd","telegraf","otel")),
            ("Time-Series Database",          "Metrics Coverage",   "CAT2", _any("plt-influx","plt-victoria","plt-thanos") or _lbl("influxdb","victoria","thanos","timescale","tsdb")),
            ("Dashboard / Visualization",     "Metrics Coverage",   "CAT2", _any("plt-grafana","plt-kibana","plt-dd") or _lbl("grafana","kibana","dashboard","panel","visualization")),
            ("Log Aggregation",               "Log Management",     "CAT1", _any("plt-elk","plt-loki","plt-splunk","plt-cwl") or _lbl("logstash","loki","splunk","fluentd","fluentbit","log aggr")),
            ("Structured Logging",            "Log Management",     "CAT2", _lbl("structured log","json log","log format","log schema")),
            ("Log Retention Policy",          "Log Management",     "CAT2", _lbl("retention","log lifecycle","archive","cold storage","s3 log")),
            ("Distributed Tracing",           "Tracing & APM",      "CAT1", _any("plt-jaeger","plt-zipkin","plt-tempo","plt-otel") or _lbl("jaeger","zipkin","tempo","tracing","trace id","span")),
            ("APM / Profiling",               "Tracing & APM",      "CAT2", _any("plt-newrelic","plt-dynatrace","plt-appdyn") or _lbl("apm","profil","newrelic","dynatrace","appdynamics")),
            ("SLO / SLA Definitions",         "SLO Governance",     "CAT1", _lbl("slo","sla","objective","error budget","burn rate")),
            ("SLI Instrumentation",           "SLO Governance",     "CAT2", _lbl("sli","indicator","latency","throughput","availability","error rate")),
            ("On-Call / Alerting",            "Alerting & Response","CAT1", _any("auto-pg","auto-og","auto-pagerduty") or _lbl("pagerduty","opsgenie","alert","on-call","incident")),
            ("Alert Deduplication / Routing", "Alerting & Response","CAT2", _lbl("alertmanager","dedup","route","silence","inhibit")),
            ("Anomaly Detection",             "Alerting & Response","CAT2", _lbl("anomaly","ml detect","outlier","forecast","trend")),
            ("Synthetic Monitoring",          "Alerting & Response","CAT3", _lbl("synthetic","canary","blackbox","heartbeat","probe","uptime")),
            ("Runbook Automation",            "SLO Governance",     "CAT3", _lbl("runbook","automation","remediat","self-heal","auto-remediat")),
            ("Cost & Resource Observability", "Metrics Coverage",   "CAT3", _lbl("cost","finops","resource usage","cloud spend","rightsiz")),
            ("Security Event Monitoring",     "Log Management",     "CAT2", _lbl("siem","security event","threat","intrusion","guard","defender")),
            ("Audit Trail Coverage",          "SLO Governance",     "CAT2", _lbl("audit","compliance log","change log","immutable log")),
        ]

        PILLARS = ["Metrics Coverage", "Log Management", "Tracing & APM", "Alerting & Response", "SLO Governance"]
        WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
        MATURITY = [
            (0,  "L1 — Initial",    "No formal observability strategy."),
            (30, "L2 — Developing", "Basic metrics and logs, no tracing."),
            (55, "L3 — Defined",    "Unified signals with SLO definitions."),
            (70, "L4 — Managed",    "Automated alerting with error budgets."),
            (85, "L5 — Optimised",  "Full-stack observability with ML-assisted detection."),
        ]

        check_results, total_w, passed_w = [], 0, 0
        cats = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
        for title, pillar, sev, passed in CHECKS:
            w = WEIGHTS[sev]
            total_w += w
            status = "pass" if passed else "fail"
            if passed:
                passed_w += w
            cats.setdefault(pillar, {"passed": 0, "total": 0, "pct": 0})
            cats[pillar]["total"] += 1
            if passed:
                cats[pillar]["passed"] += 1
            check_results.append({"title": title, "pillar": pillar, "severity": sev,
                                   "status": status, "weight": w, "detail": ""})
        for c in cats.values():
            c["pct"] = round(c["passed"] / c["total"] * 100) if c["total"] else 0

        score = round(passed_w / total_w * 100) if total_w else 0
        grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
        mat_level = sum(1 for t, *_ in MATURITY if score >= t)
        mat_label, mat_desc = MATURITY[mat_level - 1][1], MATURITY[mat_level - 1][2]

        recs = [{"title": c["title"], "pillar": c["pillar"], "priority": c["severity"]}
                for c in check_results if c["status"] == "fail"]
        recs.sort(key=lambda r: {"CAT1": 0, "CAT2": 1, "CAT3": 2}[r["priority"]])
        return {
            "score": score, "grade": grade,
            "maturity": {"level": mat_level, "label": mat_label.split(" — ")[1], "description": mat_desc},
            "checks": check_results, "categories": cats, "recommendations": recs,
            "total_checks": len(CHECKS), "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
            "assessed_at": _now(),
        }

    @bp.route("/api/designs/<design_id>/governance", methods=["POST"])
    @oc_login_required
    def odc_api_governance(design_id):
        """Run observability governance framework check."""
        import uuid as _uuid_mod
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=%s", (design_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph_data = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
            except (json.JSONDecodeError, TypeError):
                return jsonify({"error": "Invalid graph data"}), 400

            result = _compute_odc_governance(graph_data)

            assess_id = str(_uuid_mod.uuid4())
            conn.execute(
                "INSERT INTO od_assessments (id, design_id, assessment_type, findings_json, score, grade, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (assess_id, design_id, "governance",
                 json.dumps([{"title": c["title"], "severity": c["severity"], "status": c["status"]}
                             for c in result["checks"]]),
                 result["score"], result["grade"], _now()),
            )
            conn.commit()
        except Exception:
            # A raise in _compute_odc_governance or the INSERT must not leak the
            # connection (finally below) and must return a clean JSON error
            # rather than an unhandled 500 HTML page.
            logger.exception("ODC governance computation failed for %s", design_id)
            return jsonify({"error": "Governance computation failed"}), 500
        finally:
            # Always release the connection on every exit path.
            conn.close()
        return jsonify(result)

    return bp
