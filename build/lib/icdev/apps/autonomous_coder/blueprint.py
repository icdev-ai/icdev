# CUI // SP-CTI
"""Flask blueprint — mounts the Autonomous Coder web UI at /autonomous-coder/."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

ac_bp = Blueprint("autonomous_coder", __name__, url_prefix="/autonomous-coder")


@ac_bp.route("/")
def index():
    return render_template("autonomous_coder/index.html")


@ac_bp.route("/api/run", methods=["POST"])
def run_coder():
    data = request.get_json(force=True) or {}
    task = (data.get("task") or "").strip()
    if not task:
        return jsonify({"error": "task is required"}), 400

    backend = data.get("backend", "auto")
    max_duration_s = float(data.get("max_duration_s", 300))

    try:
        import apps.autonomous_coder as ac

        result = ac.run(
            task,
            backend=backend,
            max_duration_s=max_duration_s,
        )

        return jsonify({
            "session_id": result.session_id,
            "status": result.status,
            "elapsed_s": result.elapsed_s,
            "plan": result.plan,
            "code": result.code,
            "validation": {
                "verdict": result.validation.verdict,
                "score": result.validation.score,
                "issues": result.validation.issues,
                "suggestion": result.validation.suggestion,
            },
            "syntax_ok": result.validation.syntax_ok,
            "audit_log": result.audit_log,
            "error": result.error,
        })

    except Exception as e:
        return jsonify({"error": str(e), "status": "failed"}), 500
