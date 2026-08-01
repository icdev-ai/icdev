# CUI // SP-CTI
"""OHC — Ops Hub Canvas Flask Blueprint.

Routes:
  GET  /ops                    — overview dashboard
  GET  /ops/llm                — LLMOps page
  GET  /ops/models             — MLOps page
  GET  /ops/slos               — AIOps SLOs
  GET  /ops/incidents          — AIOps incidents
  GET  /ops/runbooks           — AIOps runbooks
  GET  /ops/topology           — agent topology
  GET  /ops/self-healing       — self-healing log

  JSON API:
  GET  /api/ops/overview       — health score + domain summaries
  GET  /api/ops/llm            — LLMOps data
  GET  /api/ops/models         — MLOps data
  GET  /api/ops/slos           — SLO dashboard
  GET  /api/ops/incidents      — incident list
  GET  /api/ops/runbooks       — runbook list
  GET  /api/ops/topology       — topology graph + SPOFs
  GET  /api/ops/self-healing   — self-healing log
  GET  /api/ops/adapters       — adapter health grid
  POST /api/ops/models/run     — create experiment run
  POST /api/ops/incidents      — create incident
  POST /api/ops/slos           — define SLO
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request


def create_ops_hub_blueprint() -> Blueprint:
    bp = Blueprint("ohc", __name__, url_prefix="")

    # cnr-ops-01: fail-closed auth on state-changing routes (models/run, register,
    # transition, slos POST, incidents POST, reasoned-codegen/advise) regardless
    # of ICDEV_ENFORCE_CANVAS_ACCESS. Defense-in-depth alongside guard_component_access.
    from tools.security.canvas_mutation_auth import require_mutation_auth
    bp.before_request(require_mutation_auth)

    # Init OHC canvas DB on first import so all route handlers find required tables
    try:
        from tools.ops_hub.db.init_db import init_db
        init_db()
    except Exception as exc:  # pragma: no cover
        from tools.logging.icdev_logger import get_logger as _get_logger
        _get_logger("ops_hub.blueprint").warning("OHC DB init failed: %s", exc)

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("/ops")
    def ops_index():
        from tools.ops_hub.ops_aggregator import get_ops_overview
        overview = get_ops_overview()
        return render_template("ops_hub/index.html",
                               overview=overview,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/llm")
    def ops_llm():
        from tools.ops_hub.llmops_engine import get_llmops_summary
        data = get_llmops_summary()
        return render_template("ops_hub/llm.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/models")
    def ops_models():
        from tools.ops_hub.mlops_engine import (
            list_experiments, list_runs, list_models, get_data_drift_summary,
        )
        experiments_list = list_experiments(limit=20)
        runs_list = list_runs(limit=20)
        models_list = list_models(limit=20)
        drift = get_data_drift_summary(limit=10)
        data = {
            "experiments": {
                "total_experiments": len(experiments_list),
                "running_runs": sum(1 for r in runs_list if r.get("status") == "running"),
                "recent_runs": runs_list,
            },
            "models": {
                "total_models": len(models_list),
                "models_in_production": sum(1 for m in models_list if m.get("stage") == "production"),
                "registry": models_list,
            },
            "drift": drift,
        }
        return render_template("ops_hub/models.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/slos")
    def ops_slos():
        from tools.ops_hub.aiops_engine import get_slo_dashboard
        raw = get_slo_dashboard()
        slos = raw.get("slos", [])
        met = sum(1 for s in slos if s.get("status") == "met")
        breached = sum(1 for s in slos if s.get("status") == "breached")
        at_risk = len(slos) - met - breached
        data = {
            "slos": slos,
            "summary": {
                "total_slos": len(slos),
                "met_count": met,
                "at_risk_count": at_risk,
                "breached_count": breached,
            },
        }
        return render_template("ops_hub/slos.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/incidents")
    def ops_incidents():
        from tools.ops_hub.aiops_engine import get_incidents
        incidents = get_incidents(limit=50)
        open_c = sum(1 for i in incidents if i.get("status") == "open")
        investigating_c = sum(1 for i in incidents if i.get("status") == "investigating")
        resolved_24h = sum(1 for i in incidents if i.get("status") == "resolved")
        data = {
            "incidents": incidents,
            "summary": {
                "open_count": open_c,
                "investigating_count": investigating_c,
                "resolved_24h": resolved_24h,
                "mttr_minutes": None,
            },
        }
        return render_template("ops_hub/incidents.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/runbooks")
    def ops_runbooks():
        from tools.ops_hub.aiops_engine import get_runbooks, get_runbook_executions
        runbooks = get_runbooks()
        executions = get_runbook_executions(limit=20)
        auto_count = sum(1 for r in runbooks if r.get("auto_executable"))
        data = {
            "runbooks": runbooks,
            "history": executions,
            "summary": {
                "total_runbooks": len(runbooks),
                "auto_runbooks": auto_count,
                "executions_24h": len(executions),
            },
        }
        return render_template("ops_hub/runbooks.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/topology")
    def ops_topology():
        from tools.ops_hub.aiops_engine import get_topology
        data = get_topology()
        return render_template("ops_hub/topology.html", data=data,
                               classification="CUI // SP-CTI")

    @bp.route("/ops/self-healing")
    def ops_self_healing():
        from tools.ops_hub.aiops_engine import get_self_healing_log
        log = get_self_healing_log(limit=50)
        auto_res = sum(1 for e in log if e.get("action") == "auto")
        suggested = sum(1 for e in log if e.get("action") == "suggested")
        confs = [e.get("confidence", 0) for e in log if e.get("confidence") is not None]
        avg_conf = sum(confs) / len(confs) if confs else None
        data = {
            "log": log,
            "summary": {
                "total_events": len(log),
                "auto_resolved": auto_res,
                "suggested": suggested,
                "avg_confidence": avg_conf,
            },
        }
        return render_template("ops_hub/self_healing.html", data=data,
                               classification="CUI // SP-CTI")

    # ── JSON API Routes ──────────────────────────────────────────────────────

    @bp.route("/api/ops/overview")
    def api_ops_overview():
        from tools.ops_hub.ops_aggregator import get_ops_overview
        return jsonify(get_ops_overview())

    @bp.route("/api/ops/llm")
    def api_ops_llm():
        from tools.ops_hub.llmops_engine import (
            get_llmops_summary, get_gateway_audit, get_cost_report,
            get_drift_events, get_prompt_registry, get_eval_results, get_langfuse_traces,
            get_proxy_metrics,
        )
        return jsonify({
            "summary": get_llmops_summary(),
            "gateway_audit": get_gateway_audit(limit=20),
            "cost": get_cost_report(),
            "proxy": get_proxy_metrics(),
            "drift_events": get_drift_events(limit=10),
            "prompts": get_prompt_registry(),
            "evals": get_eval_results(limit=5),
            "traces": get_langfuse_traces(limit=10),
        })

    @bp.route("/api/ops/reasoned-codegen")
    def api_ops_reasoned_codegen():
        from tools.ops_hub.llmops_engine import (
            get_reasoned_codegen_config, get_recent_chain_runs,
        )
        fn = request.args.get("function", "")
        return jsonify({
            "config": get_reasoned_codegen_config(),
            "recent_runs": get_recent_chain_runs(limit=25, function=fn),
        })

    @bp.route("/api/ops/reasoned-codegen/advise", methods=["POST"])
    def api_ops_reasoned_codegen_advise():
        from tools.ops_hub.llmops_engine import run_reasoned_codegen_advisor
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(run_reasoned_codegen_advisor(
            function=body.get("function", "code_generation"),
            spec=body.get("spec", ""),
            file_count=body.get("file_count", 0),
            past_failures=body.get("past_failures", 0),
            use_llm=bool(body.get("use_llm", False)),
        ))

    @bp.route("/api/ops/models")
    def api_ops_models():
        from tools.ops_hub.mlops_engine import (
            list_experiments, list_runs, list_models, get_data_drift_summary,
        )
        return jsonify({
            "experiments": list_experiments(limit=20),
            "runs": list_runs(limit=20),
            "models": list_models(limit=20),
            "drift": get_data_drift_summary(limit=10),
        })

    @bp.route("/api/ops/models/run", methods=["POST"])
    def api_ops_create_run():
        from tools.ops_hub.mlops_engine import create_run
        body = request.get_json(force=True, silent=True) or {}
        result = create_run(
            experiment_id=body.get("experiment_id", ""),
            run_name=body.get("run_name", ""),
            params=body.get("params"),
            tags=body.get("tags"),
        )
        return jsonify(result), 201

    @bp.route("/api/ops/models/register", methods=["POST"])
    def api_ops_register_model():
        from tools.ops_hub.mlops_engine import register_model
        body = request.get_json(force=True, silent=True) or {}
        result = register_model(
            name=body.get("name", ""),
            version=body.get("version", "1"),
            run_id=body.get("run_id", ""),
            artifact_path=body.get("artifact_path", ""),
            model_type=body.get("model_type", "llm"),
            framework=body.get("framework", ""),
            metrics=body.get("metrics"),
            tags=body.get("tags"),
        )
        return jsonify(result), 201

    @bp.route("/api/ops/models/transition", methods=["POST"])
    def api_ops_transition_model():
        from tools.ops_hub.mlops_engine import transition_model_stage
        body = request.get_json(force=True, silent=True) or {}
        result = transition_model_stage(
            name=body.get("name", ""),
            version=body.get("version", ""),
            stage=body.get("stage", "staging"),
        )
        return jsonify(result)

    @bp.route("/api/ops/slos", methods=["GET", "POST"])
    def api_ops_slos():
        if request.method == "POST":
            from tools.ops_hub.aiops_engine import define_slo
            body = request.get_json(force=True, silent=True) or {}
            result = define_slo(
                service=body.get("service", ""),
                slo_type=body.get("slo_type", "availability"),
                target=float(body.get("target", 99.9)),
                window_days=int(body.get("window_days", 30)),
            )
            return jsonify(result), 201
        from tools.ops_hub.aiops_engine import get_slo_dashboard
        return jsonify(get_slo_dashboard())

    @bp.route("/api/ops/incidents", methods=["GET", "POST"])
    def api_ops_incidents():
        if request.method == "POST":
            from tools.ops_hub.aiops_engine import create_incident
            body = request.get_json(force=True, silent=True) or {}
            result = create_incident(
                title=body.get("title", ""),
                severity=body.get("severity", "sev3"),
                description=body.get("description", ""),
            )
            return jsonify(result), 201
        from tools.ops_hub.aiops_engine import get_incidents, get_incident_dashboard
        status_filter = request.args.get("status", "")
        return jsonify({
            "dashboard": get_incident_dashboard(),
            "incidents": get_incidents(status=status_filter, limit=50),
        })

    @bp.route("/api/ops/runbooks")
    def api_ops_runbooks():
        from tools.ops_hub.aiops_engine import get_runbooks, get_runbook_executions
        return jsonify({
            "runbooks": get_runbooks(),
            "executions": get_runbook_executions(limit=20),
        })

    @bp.route("/api/ops/topology")
    def api_ops_topology():
        from tools.ops_hub.aiops_engine import get_topology, get_airgap_paths
        topo = get_topology()
        graph = topo.get("graph", {})
        return jsonify({
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
            "spofs": topo.get("spofs", []),
            "topology": topo,
            "airgap_paths": get_airgap_paths(),
        })

    @bp.route("/api/ops/self-healing")
    def api_ops_self_healing():
        from tools.ops_hub.aiops_engine import get_self_healing_log, get_self_healing_stats
        return jsonify({
            "stats": get_self_healing_stats(),
            "log": get_self_healing_log(limit=50),
        })

    @bp.route("/api/ops/adapters")
    def api_ops_adapters():
        from tools.ops_hub.adapter_registry import probe_all_cached
        raw = probe_all_cached(persist=True)  # cnr-ops-02: TTL-cached (60s)
        # Normalize to dict keyed by adapter_name for consistent API shape
        if isinstance(raw, list):
            adapters = {a.get("adapter_name", str(i)): a for i, a in enumerate(raw)}
        else:
            adapters = raw
        return jsonify({"adapters": adapters})

    # ── IQE Query ────────────────────────────────────────────────────────────

    @bp.route("/api/iqe-query", methods=["POST"])
    def ohc_iqe_query():
        from tools.iqe.engine import handle_iqe_query
        body = request.get_json(force=True, silent=True) or {}
        result = handle_iqe_query(canvas="ohc", query=body.get("query", ""),
                                   context_id=body.get("context_id"))
        return jsonify(result)

    return bp
