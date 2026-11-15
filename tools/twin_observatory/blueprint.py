# CUI // SP-CTI — Twin Observatory blueprint (twx-obs-01)
"""Flask blueprint for the cross-canvas Twin Observatory page.

Registry-driven (args/component_registry.yaml key `twin_observatory`). Read-only:
renders the twin_core observer grid + twin_* event stream, the governance twins
(icx-cnf-03) with each finding's thread and a replay, and answers IQE questions
over the twin_observatory.* collections.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger
from tools.twin_observatory.constants import IQE_COLLECTIONS
from tools.twin_observatory.observatory import get_observatory_data

logger = get_logger("icdev.twin_observatory.blueprint")

twin_observatory_bp = Blueprint(
    "twin_observatory",
    __name__,
    url_prefix="/twin-observatory",
    template_folder="../../tools/dashboard/templates",
)


@twin_observatory_bp.route("/")
def index():
    """Render the Twin Observatory: per-twin health grid + drift event stream."""
    try:
        data = get_observatory_data()
    except Exception as exc:  # noqa: BLE001 — page must always render
        logger.warning("twin_observatory index degraded: %s", exc)
        data = {"report": {"twins": [], "twin_count": 0, "summary": {}}, "events": [], "generated_at": None}
    return render_template("twin_observatory/index.html", **data)


@twin_observatory_bp.route("/api/data")
def api_data():
    """JSON: the same observatory payload (for polling / external consumers)."""
    return jsonify(get_observatory_data(
        window_hours=int(request.args.get("window_hours", 24)),
        event_limit=int(request.args.get("event_limit", 50)),
    ))


def _conformance() -> dict:
    """The committed feature-conformance report (icx-cnf-01), or why it is absent.

    Served from the snapshot ``tools/conformance/feature_audit.py --markdown``
    writes, never re-derived per request: the audit probes the whole url_map in a
    subprocess (~25s). ``--check`` fails when the snapshot is stale.
    """
    try:
        from tools.conformance.feature_audit import load_snapshot

        return {"ok": True, "report": load_snapshot()}
    except Exception as exc:  # noqa: BLE001 — the page states the cause, never a blank grid
        logger.warning("twin_observatory conformance unavailable: %s", exc)
        return {"ok": False, "error": str(exc), "report": None}


@twin_observatory_bp.route("/conformance")
def conformance_page():
    """Feature conformance: each feature judged against the five rules, with evidence."""
    return render_template("twin_observatory/conformance.html", **_conformance())


@twin_observatory_bp.route("/api/conformance")
def api_conformance():
    """JSON: the feature-conformance report (read-only)."""
    data = _conformance()
    return jsonify(data), (200 if data["ok"] else 503)


# ── governance twins (icx-cnf-03) ───────────────────────────────────────────


def _governance() -> dict:
    """Each governance twin's latest recorded observation, re-judged (replayed)."""
    try:
        from tools.twin_core.governance import latest_envelopes

        return {"ok": True, "twins": latest_envelopes()}
    except Exception as exc:  # noqa: BLE001 — the page states the cause
        logger.warning("twin_observatory governance unavailable: %s", exc)
        return {"ok": False, "error": str(exc), "twins": []}


@twin_observatory_bp.route("/governance")
def governance_page():
    """Governance twins: Cortex, ACE, Co-Workers, the conformance audit; why and replay."""
    return render_template("twin_observatory/governance.html", **_governance())


@twin_observatory_bp.route("/api/governance")
def api_governance():
    """JSON: the latest observation of every governance twin (read-only)."""
    data = _governance()
    return jsonify(data), (200 if data["ok"] else 503)


@twin_observatory_bp.route("/api/governance/observe", methods=["POST"])
def api_governance_observe():
    """Take and record a fresh observation (one twin, or all). Decides nothing."""
    from tools.twin_core.governance import adapter_for, governance_adapters, observe

    canvas = str((request.get_json(silent=True) or {}).get("canvas") or "").strip()
    try:
        keys = [canvas] if canvas else list(governance_adapters())
        envs = [observe(adapter_for(k)) for k in keys]
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"observations": [
        {"canvas": e["canvas"], "verdict": e["verdict"],
         "coverage_score": e["coverage_score"], "observation_id": e["extra"]["observation_id"],
         "recorded": e["extra"].get("recorded")} for e in envs]})


@twin_observatory_bp.route("/api/governance/replay/<observation_id>")
def api_governance_replay(observation_id: str):
    """Re-judge a recorded observation from its evidence; ``match`` says if it holds."""
    from tools.twin_core.governance import replay_any

    try:
        result = replay_any(observation_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    result.pop("envelope", None)
    return jsonify(result), (200 if result.get("found") else 404)


@twin_observatory_bp.route("/api/governance/thread")
def api_governance_thread():
    """Follow a violation's thread to its record (and provenance, where it has one)."""
    from tools.twin_core.governance import resolve_thread

    thread_id = (request.args.get("id") or "").strip()
    if not thread_id:
        return jsonify({"error": "id is required"}), 400
    result = resolve_thread(thread_id)
    return jsonify(result), (200 if result["resolved"] else 404)


@twin_observatory_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """Answer a plain-English question over the twin_observatory.* collections.

    Importing the adapter registers the collections on the module-level Executor.
    """
    import importlib

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    execute = data.get("execute", True)
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.twin_observatory")
    except Exception:  # noqa: BLE001
        pass

    iqe_str = ""
    try:
        from tools.iqe.executor import execute_query
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse

        translation = nl_to_iqe(question, collections=list(IQE_COLLECTIONS))
        iqe_str = translation["iqe"]
        ast = _parse(iqe_str)
        results = execute_query(ast, None) if execute else []
        return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
