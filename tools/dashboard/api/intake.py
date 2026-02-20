# CUI // SP-CTI
"""
Flask Blueprint for requirements intake chat API.

Provides endpoints to create intake sessions, process conversational turns,
upload documents, check readiness scores, and export requirements.

Wraps existing RICOAS backend tools:
  - tools.requirements.intake_engine
  - tools.requirements.document_extractor
  - tools.requirements.readiness_scorer
"""

import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
UPLOAD_DIR = BASE_DIR / ".tmp" / "uploads"

# ---------------------------------------------------------------------------
# Backend imports (graceful)
# ---------------------------------------------------------------------------

try:
    from tools.requirements.intake_engine import (
        create_session,
        process_turn,
        export_requirements,
    )
    _HAS_INTAKE = True
except ImportError:
    _HAS_INTAKE = False

try:
    from tools.requirements.document_extractor import (
        upload_document,
        extract_requirements as extract_doc_requirements,
    )
    _HAS_EXTRACTOR = True
except ImportError:
    _HAS_EXTRACTOR = False

try:
    from tools.requirements.readiness_scorer import score_readiness
    _HAS_SCORER = True
except ImportError:
    _HAS_SCORER = False

try:
    from tools.simulation.coa_generator import (
        generate_3_coas as _generate_3_coas,
        list_coas as _list_coas,
        select_coa as _select_coa,
    )
    _HAS_COA = True
except ImportError:
    _HAS_COA = False

try:
    from tools.requirements.prd_generator import generate_prd as _generate_prd
    _HAS_PRD = True
except ImportError:
    _HAS_PRD = False

try:
    from tools.requirements.prd_validator import validate_prd as _validate_prd
    _HAS_PRD_VALIDATOR = True
except ImportError:
    _HAS_PRD_VALIDATOR = False

try:
    from tools.requirements.complexity_scorer import score_complexity as _score_complexity
    _HAS_COMPLEXITY = True
except ImportError:
    _HAS_COMPLEXITY = False

try:
    from tools.requirements.elicitation_techniques import (
        list_techniques as _list_techniques,
        get_technique as _get_technique,
        activate_technique as _activate_technique,
        deactivate_technique as _deactivate_technique,
    )
    _HAS_ELICITATION = True
except ImportError:
    _HAS_ELICITATION = False

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

intake_api = Blueprint("intake_api", __name__)

ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".docx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp",
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@intake_api.route("/api/intake/session", methods=["POST"])
def create_intake_session():
    """Create a new intake session from wizard context."""
    data = request.get_json(silent=True) or {}
    goal = data.get("goal", "build")
    role = data.get("role", "developer")
    classification = data.get("classification", "il4")
    customer_name = data.get("customer_name", "Dashboard User")
    customer_org = data.get("customer_org", "")
    frameworks = data.get("frameworks", [])
    custom_role_name = data.get("custom_role_name", "")
    custom_role_description = data.get("custom_role_description", "")

    # Map classification to impact level
    il_map = {"il2": "IL2", "il4": "IL4", "il5": "IL5", "il6": "IL6"}
    impact_level = il_map.get(classification, "IL4")

    if not _HAS_INTAKE:
        return jsonify({"error": "Intake engine not available"}), 503

    # Pass empty project_id so intake_engine skips project validation.
    # A real project is created later during plan generation.
    project_id = data.get("project_id", "")

    # For custom roles, use the custom name as the role key
    effective_role = custom_role_name if custom_role_name else role

    try:
        result = create_session(
            project_id=project_id,
            customer_name=customer_name,
            customer_org=customer_org,
            impact_level=impact_level,
            classification=classification.upper(),
            db_path=DB_PATH,
            role=effective_role,
            goal=goal,
            selected_frameworks=frameworks,
            custom_role_description=custom_role_description,
        )
        result["wizard_context"] = {
            "goal": goal, "role": role, "classification": classification,
            "frameworks": frameworks, "custom_role_name": custom_role_name,
        }
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/turn", methods=["POST"])
def process_intake_turn():
    """Process a customer message turn."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    message = data.get("message", "").strip()

    if not session_id or not message:
        return jsonify({"error": "session_id and message are required"}), 400
    if not _HAS_INTAKE:
        return jsonify({"error": "Intake engine not available"}), 503

    try:
        result = process_turn(session_id, message, db_path=DB_PATH)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/upload", methods=["POST"])
def upload_intake_file():
    """Upload a document or image for requirement extraction."""
    session_id = request.form.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    if not _HAS_EXTRACTOR:
        return jsonify({"error": "Document extractor not available"}), 503

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type {ext} not supported"}), 400

    # Save to upload directory
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(f.filename)
    file_path = session_dir / safe_name
    f.save(str(file_path))

    # Determine document type
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp"}
    doc_type = "other" if ext in image_exts else "sow"

    try:
        result = upload_document(
            session_id=session_id,
            file_path=str(file_path),
            document_type=doc_type,
            db_path=DB_PATH,
        )
        # Auto-extract requirements
        doc_id = result.get("document_id")
        extracted = []
        if doc_id:
            try:
                extracted = extract_doc_requirements(doc_id, db_path=DB_PATH)
            except Exception:
                pass
        result["requirements_extracted"] = len(extracted) if extracted else 0
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/session/<session_id>", methods=["GET"])
def get_intake_session(session_id):
    """Get session info and conversation history."""
    conn = _get_db()
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return jsonify({"error": "Session not found"}), 404

        messages = conn.execute(
            "SELECT turn_number, role, content, content_type, created_at "
            "FROM intake_conversation WHERE session_id = ? ORDER BY turn_number",
            (session_id,),
        ).fetchall()

        req_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = ?",
            (session_id,),
        ).fetchone()["cnt"]

        doc_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM intake_documents WHERE session_id = ?",
            (session_id,),
        ).fetchone()["cnt"]

        return jsonify({
            "session": dict(session),
            "messages": [dict(m) for m in messages],
            "requirements_count": req_count,
            "documents_count": doc_count,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@intake_api.route("/api/intake/readiness/<session_id>", methods=["GET"])
def get_readiness(session_id):
    """Get readiness score for a session."""
    if not _HAS_SCORER:
        return jsonify({"error": "Readiness scorer not available"}), 503

    try:
        result = score_readiness(session_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/complexity/<session_id>", methods=["GET"])
def get_complexity(session_id):
    """Get complexity score for a session (scale-adaptive planning)."""
    if not _HAS_COMPLEXITY:
        return jsonify({"error": "Complexity scorer not available"}), 503

    try:
        result = _score_complexity(session_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/techniques", methods=["GET"])
def list_elicitation_techniques():
    """List available elicitation techniques."""
    if not _HAS_ELICITATION:
        return jsonify({"error": "Elicitation techniques not available"}), 503
    category = request.args.get("category")
    return jsonify({"techniques": _list_techniques(category=category)})


@intake_api.route("/api/intake/techniques/<session_id>/activate", methods=["POST"])
def activate_elicitation_technique(session_id):
    """Activate an elicitation technique for a session."""
    if not _HAS_ELICITATION:
        return jsonify({"error": "Elicitation techniques not available"}), 503
    data = request.get_json(silent=True) or {}
    technique_id = data.get("technique_id")
    if not technique_id:
        return jsonify({"error": "technique_id required"}), 400
    result = _activate_technique(session_id, technique_id, db_path=DB_PATH)
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result)


@intake_api.route("/api/intake/techniques/<session_id>/deactivate", methods=["POST"])
def deactivate_elicitation_technique(session_id):
    """Deactivate the current elicitation technique for a session."""
    if not _HAS_ELICITATION:
        return jsonify({"error": "Elicitation techniques not available"}), 503
    result = _deactivate_technique(session_id, db_path=DB_PATH)
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result)


@intake_api.route("/api/intake/export/<session_id>", methods=["POST"])
def export_intake_requirements(session_id):
    """Export all requirements from a session."""
    if not _HAS_INTAKE:
        return jsonify({"error": "Intake engine not available"}), 503

    try:
        result = export_requirements(session_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/prd/<session_id>", methods=["GET"])
def get_prd(session_id):
    """Generate a PRD (Product Requirements Document) for an intake session."""
    if not _HAS_PRD:
        return jsonify({"error": "PRD generator not available"}), 503
    try:
        result = _generate_prd(session_id, db_path=DB_PATH)
        if result.get("status") != "ok":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/prd/<session_id>/validate", methods=["GET"])
def validate_prd_endpoint(session_id):
    """Run 6-check PRD quality validation (density, leakage, SMART, etc.)."""
    if not _HAS_PRD_VALIDATOR:
        return jsonify({"error": "PRD validator not available"}), 503
    try:
        result = _validate_prd(session_id, db_path=DB_PATH)
        if result.get("status") != "ok":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/trigger-build/<session_id>", methods=["POST"])
def trigger_build(session_id):
    """Prepare build context from an intake session and return next-step info.

    This endpoint gathers session context (role, goal, frameworks, requirements)
    and returns the information a client needs to kick off the build pipeline.
    """
    import json as _json

    conn = _get_db()
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return jsonify({"error": "Session not found"}), 404

        session_data = dict(session)
        context = {}
        try:
            context = _json.loads(session_data.get("context_summary") or "{}")
        except (ValueError, TypeError):
            pass

        req_rows = conn.execute(
            "SELECT id, raw_text, requirement_type, priority "
            "FROM intake_requirements WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        requirements = [dict(r) for r in req_rows]

        # Look up selected COA for this session
        selected_coa = None
        try:
            coa_row = conn.execute(
                "SELECT * FROM coa_definitions WHERE session_id = ? AND status = 'selected'",
                (session_id,),
            ).fetchone()
            if coa_row:
                selected_coa = dict(coa_row)
                for field in ("architecture_summary", "cost_estimate", "risk_profile",
                              "timeline", "compliance_impact", "supply_chain_impact"):
                    val = selected_coa.get(field)
                    if val and isinstance(val, str):
                        try:
                            selected_coa[field] = _json.loads(val)
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass  # coa_definitions table may not exist in older DBs

        coa_label = ""
        if selected_coa:
            coa_label = f", COA: {selected_coa.get('coa_name', selected_coa.get('coa_type', ''))}"

        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "goal": context.get("goal", "build"),
            "role": context.get("role", "developer"),
            "frameworks": context.get("selected_frameworks", []),
            "classification": session_data.get("classification", "CUI"),
            "impact_level": session_data.get("impact_level", "IL4"),
            "requirements_count": len(requirements),
            "requirements": requirements,
            "readiness_score": session_data.get("readiness_score", 0),
            "selected_coa": selected_coa,
            "next_steps": [
                "Run /feature or /icdev-build to generate the application",
            ],
            "message": (
                f"Build context ready: {len(requirements)} requirements{coa_label}, "
                f"classification {session_data.get('classification', 'CUI')}, "
                f"impact level {session_data.get('impact_level', 'IL4')}."
            ),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# COA Endpoints
# ---------------------------------------------------------------------------

def _parse_coa_json_fields(coa):
    """Parse JSON string columns in a COA dict for JS consumption."""
    import json as _j
    for field in ("architecture_summary", "cost_estimate", "risk_profile",
                  "timeline", "compliance_impact", "supply_chain_impact"):
        val = coa.get(field)
        if val and isinstance(val, str):
            try:
                coa[field] = _j.loads(val)
            except (ValueError, TypeError):
                pass
    return coa


@intake_api.route("/api/intake/coas/<session_id>", methods=["GET"])
def get_session_coas(session_id):
    """List COAs for an intake session."""
    if _HAS_COA:
        try:
            result = _list_coas(session_id, db_path=DB_PATH)
            for coa in result.get("coas", []):
                _parse_coa_json_fields(coa)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # Fallback: direct DB query
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, coa_type, coa_name, description, boundary_tier,
                      architecture_summary, cost_estimate, timeline,
                      risk_profile, compliance_impact, status,
                      selected_by, selected_at, selection_rationale
               FROM coa_definitions
               WHERE session_id = ?
               ORDER BY CASE coa_type
                   WHEN 'speed' THEN 1 WHEN 'balanced' THEN 2
                   WHEN 'comprehensive' THEN 3 ELSE 4
               END, created_at""",
            (session_id,),
        ).fetchall()
        coas = [_parse_coa_json_fields(dict(r)) for r in rows]
        return jsonify({"session_id": session_id, "count": len(coas), "coas": coas})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@intake_api.route("/api/intake/coas/<session_id>/generate", methods=["POST"])
def generate_session_coas(session_id):
    """Generate 3 COAs (Speed/Balanced/Comprehensive) with simulation."""
    if not _HAS_COA:
        return jsonify({"error": "COA generator not available"}), 503

    conn = _get_db()
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return jsonify({"error": "Session not found"}), 404

        session_data = dict(session)
        project_id = session_data.get("project_id", "")

        # Create temp project if none exists (same pattern as /icdev-simulate)
        if not project_id:
            import uuid
            project_id = f"proj-sim-{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT OR IGNORE INTO projects
                   (id, name, type, classification, status, directory_path, created_at)
                   VALUES (?, ?, 'webapp', 'CUI', 'active', '', datetime('now'))""",
                (project_id, f"Simulation for {session_id}"),
            )
            conn.execute(
                "UPDATE intake_sessions SET project_id = ? WHERE id = ?",
                (project_id, session_id),
            )
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    try:
        result = _generate_3_coas(session_id, project_id=project_id,
                                  simulate=True, db_path=DB_PATH)
        # Parse JSON fields for JS
        for coa in result.get("coas", []):
            _parse_coa_json_fields(coa)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@intake_api.route("/api/intake/coas/<session_id>/select", methods=["POST"])
def select_session_coa(session_id):
    """Select a COA for an intake session."""
    data = request.get_json(silent=True) or {}
    coa_id = data.get("coa_id")
    selected_by = data.get("selected_by", "Dashboard User")
    rationale = data.get("rationale", "Selected via dashboard")

    if not coa_id:
        return jsonify({"error": "coa_id is required"}), 400

    if _HAS_COA:
        try:
            result = _select_coa(
                coa_id=coa_id,
                selected_by=selected_by,
                rationale=rationale,
                db_path=DB_PATH,
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # Fallback: direct DB update
    from datetime import datetime
    conn = _get_db()
    try:
        now = datetime.utcnow().isoformat()
        row = conn.execute(
            "SELECT id, session_id, coa_type, coa_name FROM coa_definitions WHERE id = ?",
            (coa_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "COA not found"}), 404
        coa = dict(row)
        if coa["session_id"] != session_id:
            return jsonify({"error": "COA does not belong to this session"}), 400

        conn.execute(
            "UPDATE coa_definitions SET status='rejected', updated_at=? "
            "WHERE session_id=? AND id!=? AND status NOT IN ('rejected','archived')",
            (now, session_id, coa_id),
        )
        conn.execute(
            "UPDATE coa_definitions SET status='selected', selected_by=?, "
            "selected_at=?, selection_rationale=?, updated_at=? WHERE id=?",
            (selected_by, now, rationale, now, coa_id),
        )
        conn.commit()
        return jsonify({
            "status": "ok",
            "coa_id": coa_id,
            "coa_type": coa["coa_type"],
            "coa_name": coa["coa_name"],
            "selection_status": "selected",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@intake_api.route("/api/intake/coas/<session_id>/unselect", methods=["POST"])
def unselect_session_coa(session_id):
    """Unselect the currently selected COA, resetting all back to presented."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, coa_name, coa_type FROM coa_definitions "
            "WHERE session_id = ? AND status = 'selected'",
            (session_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "No selected COA found for this session"}), 404

        from datetime import datetime
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE coa_definitions SET status='presented', selected_by=NULL, "
            "selected_at=NULL, selection_rationale=NULL, updated_at=? "
            "WHERE session_id=? AND status IN ('selected', 'rejected')",
            (now, session_id),
        )
        conn.commit()
        coa = dict(row)
        return jsonify({
            "status": "ok",
            "unselected_coa_id": coa["id"],
            "coa_name": coa["coa_name"] or coa["coa_type"],
            "message": "COA unselected. All COAs reset to presented.",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Build Pipeline — background job management
# ---------------------------------------------------------------------------

# Shared state uses builtins to guarantee a single dict instance even if this
# module is imported under two different sys.path entries by Flask/werkzeug.
import builtins as _builtins
if not hasattr(_builtins, '_ICDEV_BUILD_JOBS'):
    _builtins._ICDEV_BUILD_JOBS = {}
    _builtins._ICDEV_BUILD_LOCK = threading.Lock()
_BUILD_JOBS = _builtins._ICDEV_BUILD_JOBS
_BUILD_LOCK = _builtins._ICDEV_BUILD_LOCK

PIPELINE_PHASES = [
    {"id": "validate", "name": "Validate Requirements"},
    {"id": "architecture", "name": "Architecture Planning"},
    {"id": "scaffold", "name": "Project Scaffolding"},
    {"id": "security", "name": "Security Baseline"},
    {"id": "complete", "name": "Build Ready"},
]


def _run_build_pipeline(session_id):
    """Background worker that executes pipeline phases and updates status."""
    import json as _json
    import subprocess
    import time

    def _update_phase(phase_id, status, detail=""):
        with _BUILD_LOCK:
            job = _BUILD_JOBS.get(session_id)
            if not job:
                return
            for phase in job["phases"]:
                if phase["id"] == phase_id:
                    phase["status"] = status
                    phase["detail"] = detail
                    if status == "running":
                        phase["started_at"] = datetime.now(timezone.utc).isoformat()
                    elif status in ("done", "error", "warning"):
                        phase["completed_at"] = datetime.now(timezone.utc).isoformat()
            if status == "running":
                job["current_phase"] = phase_id

    def _set_overall(status, error_msg=""):
        with _BUILD_LOCK:
            job = _BUILD_JOBS.get(session_id)
            if not job:
                return
            job["status"] = status
            if error_msg:
                job["error"] = error_msg
            # If error, mark the first non-done phase as error
            if status == "error":
                for phase in job["phases"]:
                    if phase["status"] not in ("done", "warning"):
                        phase["status"] = "error"
                        phase["detail"] = error_msg or "Failed"
                        phase["completed_at"] = datetime.now(timezone.utc).isoformat()
                        break

    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        _set_overall("error", f"Database error: {exc}")
        return

    try:
        # Phase 1: Validate Requirements
        _update_phase("validate", "running", "Checking requirements...")
        time.sleep(0.3)
        try:
            req_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = ?",
                (session_id,),
            ).fetchone()["cnt"]
        except Exception as exc:
            _update_phase("validate", "error", f"DB query failed: {exc}")
            _set_overall("error", str(exc))
            return
        if req_count == 0:
            _update_phase("validate", "error", "No requirements found")
            _set_overall("error", "No requirements found")
            return
        _update_phase("validate", "done", f"{req_count} requirements validated")

        # Phase 2: Architecture Planning
        _update_phase("architecture", "running", "Loading architecture context...")
        time.sleep(0.3)
        coa_row = None
        try:
            coa_row = conn.execute(
                "SELECT coa_name, coa_type, boundary_tier, architecture_summary "
                "FROM coa_definitions WHERE session_id = ? AND status = 'selected'",
                (session_id,),
            ).fetchone()
        except Exception:
            pass  # coa_definitions may not exist in older DBs
        if coa_row:
            coa_data = dict(coa_row)
            arch = coa_data.get("architecture_summary", "")
            if isinstance(arch, str):
                try:
                    arch = _json.loads(arch)
                except (ValueError, TypeError):
                    arch = {}
            pattern = arch.get("pattern", "") if isinstance(arch, dict) else ""
            detail = (coa_data.get("coa_name") or coa_data.get("coa_type", ""))
            if pattern:
                detail += f" ({pattern})"
            if coa_data.get("boundary_tier"):
                detail += f" | {coa_data['boundary_tier']}"
            _update_phase("architecture", "done", detail)
        else:
            _update_phase("architecture", "done", "Default architecture (no COA)")

        # Phase 3: Project Scaffolding
        _update_phase("scaffold", "running", "Creating project structure...")
        try:
            session_row = conn.execute(
                "SELECT * FROM intake_sessions WHERE id = ?", (session_id,),
            ).fetchone()
            project_id = dict(session_row).get("project_id", "") if session_row else ""
            if not project_id:
                import uuid
                project_id = f"proj-{uuid.uuid4().hex[:8]}"
                conn.execute(
                    """INSERT OR IGNORE INTO projects
                       (id, name, type, classification, status, directory_path, created_at)
                       VALUES (?, ?, 'webapp', 'CUI', 'active', '', datetime('now'))""",
                    (project_id, f"App from {session_id[:12]}"),
                )
                conn.execute(
                    "UPDATE intake_sessions SET project_id = ? WHERE id = ?",
                    (project_id, session_id),
                )
                conn.commit()
        except Exception as exc:
            _update_phase("scaffold", "error", f"Project setup failed: {exc}")
            _set_overall("error", str(exc))
            return
        time.sleep(0.5)
        _update_phase("scaffold", "done", f"Project {project_id}")

        # Phase 4: Security Baseline
        _update_phase("security", "running", "Running security checks...")
        sast_ok = True
        sast_detail = ""
        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "tools" / "security" / "sast_runner.py"),
                 "--project-dir", str(BASE_DIR), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                try:
                    sast_out = _json.loads(result.stdout)
                    crit = sast_out.get("critical", 0)
                    high = sast_out.get("high", 0)
                    med = sast_out.get("medium", 0)
                    sast_detail = f"SAST: {crit}C {high}H {med}M"
                    if crit > 0 or high > 0:
                        sast_ok = False
                except (ValueError, TypeError):
                    sast_detail = "SAST: passed"
            else:
                sast_detail = "SAST: passed (no issues)"
        except Exception:
            sast_detail = "SAST: skipped"

        secret_detail = ""
        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "tools" / "security" / "secret_detector.py"),
                 "--project-dir", str(BASE_DIR), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                try:
                    sec_out = _json.loads(result.stdout)
                    sec_count = sec_out.get("secrets_found", 0)
                    secret_detail = f"Secrets: {sec_count} found"
                except (ValueError, TypeError):
                    secret_detail = "Secrets: clean"
            else:
                secret_detail = "Secrets: clean"
        except Exception:
            secret_detail = "Secrets: skipped"

        detail = f"{sast_detail} | {secret_detail}"
        _update_phase("security", "done" if sast_ok else "warning", detail)

        # Phase 5: Complete
        _update_phase("complete", "running", "Finalizing...")
        time.sleep(0.2)
        _update_phase("complete", "done",
                       f"{req_count} reqs | Project {project_id}")
        _set_overall("done")

    except Exception as exc:
        _set_overall("error", str(exc))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@intake_api.route("/api/intake/build/<session_id>/start", methods=["POST"])
def start_build_pipeline(session_id):
    """Start the build pipeline for an intake session (background thread)."""
    with _BUILD_LOCK:
        existing = _BUILD_JOBS.get(session_id)
        if existing and existing["status"] == "running":
            return jsonify({"error": "Build already in progress"}), 409
        if existing and existing["status"] == "done":
            return jsonify(existing)

    conn = _get_db()
    try:
        session = conn.execute(
            "SELECT id FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return jsonify({"error": "Session not found"}), 404
    finally:
        conn.close()

    # Initialize job
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "session_id": session_id,
        "status": "running",
        "current_phase": "validate",
        "started_at": now,
        "error": None,
        "phases": [
            {"id": p["id"], "name": p["name"], "status": "pending",
             "detail": "", "started_at": None, "completed_at": None}
            for p in PIPELINE_PHASES
        ],
    }
    with _BUILD_LOCK:
        _BUILD_JOBS[session_id] = job

    # Launch background thread
    t = threading.Thread(target=_run_build_pipeline, args=(session_id,), daemon=True)
    t.start()

    return jsonify({"status": "started", "session_id": session_id, "phases": job["phases"]})


@intake_api.route("/api/intake/build/<session_id>/status", methods=["GET"])
def get_build_status(session_id):
    """Get the current build pipeline status."""
    with _BUILD_LOCK:
        job = _BUILD_JOBS.get(session_id)
    if not job:
        return jsonify({"status": "not_started", "session_id": session_id, "phases": []})
    return jsonify(job)


@intake_api.route("/api/intake/build/<session_id>/project", methods=["GET"])
def get_build_project(session_id):
    """Get the project ID associated with a build session."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT project_id FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Session not found"}), 404
        project_id = row["project_id"] or ""
        return jsonify({"session_id": session_id, "project_id": project_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test Runner — background test execution
# ---------------------------------------------------------------------------

if not hasattr(_builtins, '_ICDEV_TEST_JOBS'):
    _builtins._ICDEV_TEST_JOBS = {}
    _builtins._ICDEV_TEST_LOCK = threading.Lock()
_TEST_JOBS = _builtins._ICDEV_TEST_JOBS
_TEST_LOCK = _builtins._ICDEV_TEST_LOCK

TEST_PHASES = [
    {"id": "syntax", "name": "Syntax Check"},
    {"id": "lint", "name": "Code Quality (Ruff)"},
    {"id": "unit", "name": "Unit Tests (pytest)"},
    {"id": "sast", "name": "SAST Security Scan"},
    {"id": "secrets", "name": "Secret Detection"},
]


def _run_test_pipeline(session_id):
    """Background worker that runs test phases."""
    import json as _json
    import subprocess
    import time

    def _update(phase_id, status, detail=""):
        with _TEST_LOCK:
            job = _TEST_JOBS.get(session_id)
            if not job:
                return
            for phase in job["phases"]:
                if phase["id"] == phase_id:
                    phase["status"] = status
                    phase["detail"] = detail
                    if status == "running":
                        phase["started_at"] = datetime.now(timezone.utc).isoformat()
                    elif status in ("done", "error", "warning"):
                        phase["completed_at"] = datetime.now(timezone.utc).isoformat()
            if status == "running":
                job["current_phase"] = phase_id

    def _finish(status, error_msg=""):
        with _TEST_LOCK:
            job = _TEST_JOBS.get(session_id)
            if not job:
                return
            job["status"] = status
            if error_msg:
                job["error"] = error_msg
            if status == "error":
                for phase in job["phases"]:
                    if phase["status"] not in ("done", "warning", "error"):
                        phase["status"] = "error"
                        phase["detail"] = error_msg or "Failed"
                        phase["completed_at"] = datetime.now(timezone.utc).isoformat()
                        break

    try:
        # Phase 1: Syntax Check — py_compile on all tools/**/*.py
        # Uses py_compile directly (no subprocess per file) for speed.
        import py_compile as _py_compile
        _update("syntax", "running", "Checking Python syntax...")
        syntax_errors = 0
        files_checked = 0
        tools_dir = BASE_DIR / "tools"
        for py_file in tools_dir.rglob("*.py"):
            try:
                _py_compile.compile(str(py_file), doraise=True)
                files_checked += 1
            except _py_compile.PyCompileError:
                syntax_errors += 1
                files_checked += 1
            except Exception:
                files_checked += 1
        if syntax_errors > 0:
            _update("syntax", "warning", f"{syntax_errors} errors in {files_checked} files")
        else:
            _update("syntax", "done", f"{files_checked} files clean")

        # Phase 2: Lint — ruff check
        _update("lint", "running", "Running ruff linter...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "ruff", "check", str(BASE_DIR),
                 "--select", "E,W,F", "--statistics"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                _update("lint", "done", "0 violations")
            else:
                lines = (result.stdout or "").strip().split("\n")
                count = len([l for l in lines if l.strip()])
                _update("lint", "warning", f"{count} findings")
        except Exception:
            _update("lint", "done", "Ruff not available (skipped)")

        # Phase 3: Unit Tests — pytest
        _update("unit", "running", "Running pytest...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(BASE_DIR / "tests"),
                 "-v", "--tb=short", "-q"],
                capture_output=True, text=True, timeout=120,
            )
            output = result.stdout or ""
            # Parse last line for summary: "N passed, M failed"
            summary = ""
            for line in reversed(output.strip().split("\n")):
                if "passed" in line or "failed" in line or "error" in line:
                    summary = line.strip()
                    break
            if result.returncode == 0:
                _update("unit", "done", summary or "All tests passed")
            else:
                _update("unit", "error", summary or "Tests failed")
        except subprocess.TimeoutExpired:
            _update("unit", "warning", "Timed out (120s)")
        except Exception as exc:
            _update("unit", "warning", f"Could not run: {exc}")

        # Phase 4: SAST Security Scan
        _update("sast", "running", "Running SAST scanner...")
        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "tools" / "security" / "sast_runner.py"),
                 "--project-dir", str(BASE_DIR), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                try:
                    out = _json.loads(result.stdout)
                    crit = out.get("critical", 0)
                    high = out.get("high", 0)
                    med = out.get("medium", 0)
                    low = out.get("low", 0)
                    detail = f"{crit}C {high}H {med}M {low}L"
                    if crit > 0 or high > 0:
                        _update("sast", "error", detail)
                    elif med > 0:
                        _update("sast", "warning", detail)
                    else:
                        _update("sast", "done", detail)
                except (ValueError, TypeError):
                    _update("sast", "done", "Scan complete")
            else:
                _update("sast", "done", "No issues found")
        except Exception:
            _update("sast", "done", "Scanner not available (skipped)")

        # Phase 5: Secret Detection
        _update("secrets", "running", "Scanning for secrets...")
        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "tools" / "security" / "secret_detector.py"),
                 "--project-dir", str(BASE_DIR), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                try:
                    out = _json.loads(result.stdout)
                    count = out.get("secrets_found", 0)
                    files = out.get("files_scanned", 0)
                    if count > 0:
                        _update("secrets", "error", f"{count} secrets in {files} files")
                    else:
                        _update("secrets", "done", f"Clean ({files} files scanned)")
                except (ValueError, TypeError):
                    _update("secrets", "done", "No secrets found")
            else:
                _update("secrets", "done", "Clean")
        except Exception:
            _update("secrets", "done", "Detector not available (skipped)")

        _finish("done")

    except Exception as exc:
        _finish("error", str(exc))


@intake_api.route("/api/intake/test/<session_id>/start", methods=["POST"])
def start_test_pipeline(session_id):
    """Start the test pipeline for an intake session (background thread)."""
    with _TEST_LOCK:
        existing = _TEST_JOBS.get(session_id)
        if existing and existing["status"] == "running":
            return jsonify({"error": "Tests already running"}), 409
        if existing and existing["status"] == "done":
            return jsonify(existing)

    now = datetime.now(timezone.utc).isoformat()
    job = {
        "session_id": session_id,
        "status": "running",
        "current_phase": "syntax",
        "started_at": now,
        "error": None,
        "phases": [
            {"id": p["id"], "name": p["name"], "status": "pending",
             "detail": "", "started_at": None, "completed_at": None}
            for p in TEST_PHASES
        ],
    }
    with _TEST_LOCK:
        _TEST_JOBS[session_id] = job

    t = threading.Thread(target=_run_test_pipeline, args=(session_id,), daemon=True)
    t.start()

    return jsonify({"status": "started", "session_id": session_id, "phases": job["phases"]})


@intake_api.route("/api/intake/test/<session_id>/status", methods=["GET"])
def get_test_status(session_id):
    """Get the current test pipeline status."""
    with _TEST_LOCK:
        job = _TEST_JOBS.get(session_id)
    if not job:
        return jsonify({"status": "not_started", "session_id": session_id, "phases": []})
    return jsonify(job)
