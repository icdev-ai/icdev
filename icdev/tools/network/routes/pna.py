# CUI // SP-CTI
"""Network Canvas — PNA Predictive Network Analytics routes.

Provides API and page routes for six predictive models:
  1. Device EOL/EOS risk scoring
  2. BGP session instability forecasting
  3. Compliance drift prediction (DISA STIG)
  4. Network capacity exhaustion forecasting
  5. Change failure probability scoring
  6. Supply chain risk scoring (vendor/model CVE aggregation)
"""

from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.routes.pna")


def register_pna_routes(bp):
    """Register PNA Predictive Network Analytics routes on the NDC blueprint."""
    from flask import jsonify, render_template, request

    # ── Page ─────────────────────────────────────────────────────────────

    @bp.route("/network/predictive-analytics")
    def pna_dashboard():
        """Predictive Network Analytics — 6-panel predictive intelligence UI."""
        from tools.network.db.init_db import get_connection as _nc_conn

        def _count(table, where="1=1"):
            try:
                conn = _nc_conn()
                try:
                    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
                finally:
                    conn.close()
            except Exception:
                return 0

        return render_template(
            "network/predictive_analytics.html",
            title="Predictive Network Analytics",
            eol_critical=_count("nc_eol_predictions", "risk_tier='critical'"),
            bgp_high=_count("nc_bgp_predictions", "risk_tier IN ('critical','high')"),
            compliance_failing=_count("nc_compliance_drift", "days_to_failure IS NOT NULL AND CAST(days_to_failure AS REAL) < 30"),
            capacity_saturating=_count("nc_capacity_predictions", "days_to_saturation IS NOT NULL AND CAST(days_to_saturation AS REAL) < 14"),
            change_high_risk=_count("nc_change_risk", "failure_probability >= 0.70"),
            supply_chain_critical=_count("nc_supply_chain_risk", "vendor_risk_rating='critical'"),
        )

    # ── EOL / EOS Prediction ─────────────────────────────────────────────

    @bp.route("/api/network/predict/eol", methods=["POST"])
    def pna_predict_eol():
        try:
            from tools.network.eol_predictor import predict_eol_risk
            body = request.json or {}
            result = predict_eol_risk(network_id=body.get("network_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("EOL prediction failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/eol", methods=["GET"])
    def pna_get_eol():
        try:
            from tools.network.eol_predictor import get_eol_predictions
            tier = request.args.get("tier")
            limit = int(request.args.get("limit", 100))
            return jsonify(get_eol_predictions(tier=tier, limit=limit))
        except Exception as exc:
            logger.error("EOL fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/eol/summary", methods=["GET"])
    def pna_eol_summary():
        try:
            from tools.network.eol_predictor import get_eol_summary
            return jsonify(get_eol_summary())
        except Exception as exc:
            logger.error("EOL summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── BGP Instability Prediction ────────────────────────────────────────

    @bp.route("/api/network/predict/bgp", methods=["POST"])
    def pna_predict_bgp():
        try:
            from tools.network.bgp_predictor import predict_bgp_stability
            body = request.json or {}
            result = predict_bgp_stability(network_id=body.get("network_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("BGP prediction failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/bgp", methods=["GET"])
    def pna_get_bgp():
        try:
            from tools.network.bgp_predictor import get_bgp_predictions
            risk = request.args.get("risk")
            limit = int(request.args.get("limit", 100))
            return jsonify(get_bgp_predictions(risk=risk, limit=limit))
        except Exception as exc:
            logger.error("BGP fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/bgp/summary", methods=["GET"])
    def pna_bgp_summary():
        try:
            from tools.network.bgp_predictor import get_bgp_summary
            return jsonify(get_bgp_summary())
        except Exception as exc:
            logger.error("BGP summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Compliance Drift Prediction ───────────────────────────────────────

    @bp.route("/api/network/predict/compliance", methods=["POST"])
    def pna_predict_compliance():
        try:
            from tools.network.compliance_drift_predictor import predict_compliance_drift
            body = request.json or {}
            result = predict_compliance_drift(network_id=body.get("network_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("Compliance drift prediction failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/compliance", methods=["GET"])
    def pna_get_compliance():
        try:
            from tools.network.compliance_drift_predictor import get_compliance_drift
            device_name = request.args.get("device_name")
            framework = request.args.get("framework")
            limit = int(request.args.get("limit", 100))
            return jsonify(get_compliance_drift(device_name=device_name, framework=framework, limit=limit))
        except Exception as exc:
            logger.error("Compliance drift fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/compliance/summary", methods=["GET"])
    def pna_compliance_summary():
        try:
            from tools.network.compliance_drift_predictor import get_compliance_summary
            return jsonify(get_compliance_summary())
        except Exception as exc:
            logger.error("Compliance summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Capacity Exhaustion Prediction ────────────────────────────────────

    @bp.route("/api/network/predict/capacity", methods=["POST"])
    def pna_predict_capacity():
        try:
            from tools.network.capacity_predictor import predict_capacity_exhaustion
            body = request.json or {}
            result = predict_capacity_exhaustion(network_id=body.get("network_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("Capacity prediction failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/capacity", methods=["GET"])
    def pna_get_capacity():
        try:
            from tools.network.capacity_predictor import get_capacity_predictions
            device_name = request.args.get("device_name")
            min_risk = float(request.args.get("min_risk", 0.0))
            limit = int(request.args.get("limit", 100))
            return jsonify(get_capacity_predictions(device_name=device_name, min_risk=min_risk, limit=limit))
        except Exception as exc:
            logger.error("Capacity fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/capacity/summary", methods=["GET"])
    def pna_capacity_summary():
        try:
            from tools.network.capacity_predictor import get_capacity_summary
            return jsonify(get_capacity_summary())
        except Exception as exc:
            logger.error("Capacity summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Change Failure Probability ────────────────────────────────────────

    @bp.route("/api/network/predict/change", methods=["POST"])
    def pna_predict_change():
        try:
            from tools.network.change_failure_predictor import predict_change_failure
            body = request.json or {}
            result = predict_change_failure(plan_id=body.get("plan_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("Change failure prediction failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/change", methods=["GET"])
    def pna_get_change():
        try:
            from tools.network.change_failure_predictor import get_change_risks
            risk_tier = request.args.get("risk_tier")
            plan_id = request.args.get("plan_id")
            limit = int(request.args.get("limit", 100))
            return jsonify(get_change_risks(risk_tier=risk_tier, plan_id=plan_id, limit=limit))
        except Exception as exc:
            logger.error("Change risks fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/change/summary", methods=["GET"])
    def pna_change_summary():
        try:
            from tools.network.change_failure_predictor import get_change_risk_summary
            return jsonify(get_change_risk_summary())
        except Exception as exc:
            logger.error("Change risk summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Supply Chain Risk ─────────────────────────────────────────────────

    @bp.route("/api/network/predict/supply-chain", methods=["POST"])
    def pna_score_supply_chain():
        try:
            from tools.network.supply_chain_risk_scorer import score_supply_chain_risk
            body = request.json or {}
            result = score_supply_chain_risk(network_id=body.get("network_id"))
            return jsonify(result)
        except Exception as exc:
            logger.error("Supply chain scoring failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/supply-chain", methods=["GET"])
    def pna_get_supply_chain():
        try:
            from tools.network.supply_chain_risk_scorer import get_supply_chain_risks
            vendor = request.args.get("vendor")
            rating = request.args.get("rating")
            limit = int(request.args.get("limit", 100))
            return jsonify(get_supply_chain_risks(vendor=vendor, rating=rating, limit=limit))
        except Exception as exc:
            logger.error("Supply chain fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network/predict/supply-chain/summary", methods=["GET"])
    def pna_supply_chain_summary():
        try:
            from tools.network.supply_chain_risk_scorer import get_supply_chain_summary
            return jsonify(get_supply_chain_summary())
        except Exception as exc:
            logger.error("Supply chain summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Run All Predictors ────────────────────────────────────────────────

    @bp.route("/api/network/predict/all", methods=["POST"])
    def pna_predict_all():
        """Run all six predictors in sequence and return combined results."""
        body = request.json or {}
        network_id = body.get("network_id")
        results = {}

        predictors = [
            ("eol", "tools.network.eol_predictor", "predict_eol_risk", {"network_id": network_id}),
            ("bgp", "tools.network.bgp_predictor", "predict_bgp_stability", {"network_id": network_id}),
            ("compliance", "tools.network.compliance_drift_predictor", "predict_compliance_drift", {"network_id": network_id}),
            ("capacity", "tools.network.capacity_predictor", "predict_capacity_exhaustion", {"network_id": network_id}),
            ("change", "tools.network.change_failure_predictor", "predict_change_failure", {"plan_id": body.get("plan_id")}),
            ("supply_chain", "tools.network.supply_chain_risk_scorer", "score_supply_chain_risk", {"network_id": network_id}),
        ]

        import importlib
        for key, module_path, func_name, kwargs in predictors:
            try:
                mod = importlib.import_module(module_path)
                fn = getattr(mod, func_name)
                results[key] = fn(**kwargs)
            except Exception as exc:
                logger.error("Predictor %s failed: %s", key, exc)
                results[key] = {"error": str(exc)}

        return jsonify(results)

    logger.info("PNA Predictive Network Analytics routes registered")
