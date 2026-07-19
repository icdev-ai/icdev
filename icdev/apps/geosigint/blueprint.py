"""GeoSIGINT — Flask Blueprint factory for dashboard integration.

Provides:
    create_geosigint_blueprint()     → page routes at /geosigint/
    create_geosigint_api_blueprint() → API routes at /api/geosigint/

Provenance note (nav-plat-03):
    Every layer served from this blueprint is a **static reference model** —
    hardcoded module-level constants, not a live feed or DB. Every API response
    carries ``{"data_source": "static_reference", "as_of": DATA_VINTAGE}`` and
    every page renders a persistent "Reference data (static)" badge so a viewer
    can tell. Wiring live sources is intentionally out of scope.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

_HERE = Path(__file__).parent

# Vintage of the static reference constants (a2ad_mapper 2026-04, the rest last
# curated 2026-05) — month precision, not a fabricated exact date.
DATA_VINTAGE = "2026-05"
DATA_SOURCE = "static_reference"


def _stamp(payload: dict) -> dict:
    """Attach provenance labels marking a response as static reference data."""
    payload["data_source"] = DATA_SOURCE
    payload["as_of"] = DATA_VINTAGE
    return payload


def _page_ctx() -> dict:
    """Template context that toggles the persistent 'static reference' badge."""
    return {"static_reference": True, "static_reference_as_of": DATA_VINTAGE}


def create_geosigint_blueprint() -> Blueprint:
    bp = Blueprint(
        "geosigint",
        __name__,
        url_prefix="/geosigint",
        template_folder="templates",
        static_folder="static",
        static_url_path="/static/geosigint",
    )

    @bp.route("/")
    def index():
        return render_template("geosigint_index.html", **_page_ctx())

    @bp.route("/a2ad")
    def a2ad():
        return render_template("a2ad.html", **_page_ctx())

    @bp.route("/amphibious")
    def amphibious():
        return render_template("amphibious.html", **_page_ctx())

    @bp.route("/strait-crossing")
    def strait_crossing():
        return render_template("strait_crossing.html", **_page_ctx())

    @bp.route("/island-chain")
    def island_chain():
        return render_template("island_chain.html", **_page_ctx())

    @bp.route("/militia")
    def militia():
        return render_template("militia.html", **_page_ctx())

    @bp.route("/semiconductor")
    def semiconductor():
        return render_template("semiconductor.html", **_page_ctx())

    return bp


def create_geosigint_api_blueprint() -> Blueprint:
    api = Blueprint("geosigint_api", __name__)

    # ── A2/AD ─────────────────────────────────────────────────────────────────

    @api.route("/api/geosigint/a2ad/zones")
    def api_a2ad_zones():
        from apps.geosigint.a2ad_mapper import WEAPON_SYSTEMS, get_zones
        return jsonify(_stamp({"systems": list(WEAPON_SYSTEMS.values()), "zones": get_zones()}))

    # ── Amphibious ────────────────────────────────────────────────────────────

    @api.route("/api/geosigint/amphibious/summary")
    def api_amphibious_summary():
        from apps.geosigint.amphibious_analyzer import get_summary as _s
        return jsonify(_stamp(_s()))

    @api.route("/api/geosigint/amphibious/zones")
    def api_amphibious_zones():
        from apps.geosigint.amphibious_analyzer import LANDING_ZONES, slope_viability, slope_color
        zones = [{**z, "viability": slope_viability(z["slope_deg"]), "color": slope_color(z["slope_deg"])} for z in LANDING_ZONES]
        return jsonify(_stamp({"zones": zones}))

    @api.route("/api/geosigint/amphibious/lift")
    def api_amphibious_lift():
        from apps.geosigint.amphibious_analyzer import calc_lift_capacity, AMPHIBIOUS_FLEET
        result = calc_lift_capacity()
        result["fleet"] = AMPHIBIOUS_FLEET
        return jsonify(_stamp(result))

    @api.route("/api/geosigint/amphibious/weather")
    def api_amphibious_weather():
        from apps.geosigint.amphibious_analyzer import get_weather_windows
        return jsonify(_stamp({"windows": get_weather_windows()}))

    @api.route("/api/geosigint/amphibious/crossing")
    def api_amphibious_crossing():
        from apps.geosigint.amphibious_analyzer import get_crossing_analysis
        return jsonify(_stamp({"corridors": get_crossing_analysis()}))

    @api.route("/api/geosigint/amphibious/detection")
    def api_amphibious_detection():
        from apps.geosigint.amphibious_analyzer import get_detection_curve
        return jsonify(_stamp({"curve": get_detection_curve()}))

    # ── Strait Crossing ───────────────────────────────────────────────────────

    @api.route("/api/geosigint/strait-crossing/summary")
    def api_strait_summary():
        from apps.geosigint.strait_crossing import get_summary as _s
        return jsonify(_stamp(_s()))

    @api.route("/api/geosigint/strait-crossing/speed-matrix")
    def api_strait_speed_matrix():
        from apps.geosigint.strait_crossing import get_speed_matrix
        return jsonify(_stamp({"scenarios": get_speed_matrix()}))

    @api.route("/api/geosigint/strait-crossing/intercept")
    def api_strait_intercept():
        from apps.geosigint.strait_crossing import get_intercept_table
        speed_kts = float(request.args.get("speed_kts", 12.0))
        return jsonify(_stamp({"table": get_intercept_table(speed_kts), "speed_kts": speed_kts}))

    @api.route("/api/geosigint/strait-crossing/detection")
    def api_strait_detection():
        from apps.geosigint.strait_crossing import get_detection_curve, RADAR_SYSTEMS, ROCAF_BASES
        return jsonify(_stamp({"curve": get_detection_curve(), "radar_systems": RADAR_SYSTEMS, "rocaf_bases": ROCAF_BASES}))

    @api.route("/api/geosigint/strait-crossing/corridor")
    def api_strait_corridor():
        from apps.geosigint.strait_crossing import PRIMARY_CORRIDOR
        return jsonify(_stamp(dict(PRIMARY_CORRIDOR)))

    # ── Island Chain ──────────────────────────────────────────────────────────

    @api.route("/api/geosigint/island-chain/summary")
    def api_island_summary():
        from apps.geosigint.island_chain_defense import get_summary as _s
        return jsonify(_stamp(_s()))

    @api.route("/api/geosigint/island-chain/bases")
    def api_island_bases():
        from apps.geosigint.island_chain_defense import get_all_bases
        return jsonify(_stamp({"bases": get_all_bases()}))

    @api.route("/api/geosigint/island-chain/thaad")
    def api_island_thaad():
        from apps.geosigint.island_chain_defense import THAAD_BATTERIES
        return jsonify(_stamp({"batteries": THAAD_BATTERIES}))

    @api.route("/api/geosigint/island-chain/chokepoints")
    def api_island_chokepoints():
        from apps.geosigint.island_chain_defense import CHOKEPOINTS
        return jsonify(_stamp({"chokepoints": CHOKEPOINTS}))

    # ── Militia Classifier ────────────────────────────────────────────────────

    @api.route("/api/geosigint/militia/summary")
    def api_militia_summary():
        from apps.geosigint.militia_classifier import get_summary as _s
        return jsonify(_stamp(_s()))

    @api.route("/api/geosigint/militia/classify", methods=["POST"])
    def api_militia_classify():
        from apps.geosigint.militia_classifier import classify_fleet
        payload = request.get_json(force=True, silent=True) or {}
        vessels = payload.get("vessels", [])
        if not vessels:
            return jsonify({"error": "vessels array required"}), 400
        return jsonify(_stamp({"results": classify_fleet(vessels)}))

    @api.route("/api/geosigint/militia/swarms", methods=["POST"])
    def api_militia_swarms():
        from apps.geosigint.militia_classifier import detect_swarm_events
        payload = request.get_json(force=True, silent=True) or {}
        vessels = payload.get("vessels", [])
        return jsonify(_stamp({"swarms": detect_swarm_events(vessels)}))

    @api.route("/api/geosigint/militia/zones")
    def api_militia_zones():
        from apps.geosigint.militia_classifier import DISPUTED_ZONES, ARTIFICIAL_ISLANDS
        return jsonify(_stamp({"disputed_zones": DISPUTED_ZONES, "artificial_islands": ARTIFICIAL_ISLANDS}))

    # ── Semiconductor Chain ───────────────────────────────────────────────────

    @api.route("/api/geosigint/semiconductor/summary")
    def api_semi_summary():
        from apps.geosigint.semiconductor_chain import get_summary as _s
        return jsonify(_stamp(_s()))

    @api.route("/api/geosigint/semiconductor/scenarios")
    def api_semi_scenarios():
        from apps.geosigint.semiconductor_chain import DISRUPTION_SCENARIOS
        return jsonify(_stamp({"scenarios": DISRUPTION_SCENARIOS}))

    @api.route("/api/geosigint/semiconductor/simulate", methods=["POST"])
    def api_semi_simulate():
        from apps.geosigint.semiconductor_chain import simulate_disruption, run_scenario
        payload = request.get_json(force=True, silent=True) or {}
        scenario_id = payload.get("scenario_id")
        if scenario_id:
            return jsonify(_stamp(run_scenario(scenario_id)))
        node_id = payload.get("node_id")
        if not node_id:
            return jsonify({"error": "scenario_id or node_id required"}), 400
        severity = float(payload.get("severity", 1.0))
        return jsonify(_stamp(simulate_disruption(node_id, severity)))

    @api.route("/api/geosigint/semiconductor/exposure-map")
    def api_semi_exposure():
        from apps.geosigint.semiconductor_chain import get_exposure_map
        return jsonify(_stamp({"nodes": get_exposure_map()}))

    @api.route("/api/geosigint/semiconductor/ree-flow")
    def api_semi_ree():
        from apps.geosigint.semiconductor_chain import get_ree_flow
        element = request.args.get("element")
        return jsonify(_stamp({"flows": get_ree_flow(element)}))

    return api
