"""GeoSIGINT — Geospatial Signal Intelligence Analyzer.

Standalone Flask app for RF signal analysis, pattern detection,
anomaly scoring, and knowledge graph correlation.
"""

from __future__ import annotations

import argparse

import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from models import get_connection, init_db

# Allow importing domains/sea/tracker from repo root
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

app = Flask(__name__)


# ── Helpers ────────────────────────────────────────────────

def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


# ── Pages ──────────────────────────────────────────────────

@app.route("/")
def dashboard():
    conn = get_connection()
    try:
        stats = {
            "signals": conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"],
            "emitters": conn.execute("SELECT COUNT(*) c FROM emitters").fetchone()["c"],
            "stations": conn.execute("SELECT COUNT(*) c FROM stations").fetchone()["c"],
            "patterns": conn.execute("SELECT COUNT(*) c FROM patterns").fetchone()["c"],
            "anomalies": conn.execute("SELECT COUNT(*) c FROM anomalies WHERE resolved=0").fetchone()["c"],
            "kg_edges": conn.execute("SELECT COUNT(*) c FROM kg_edges").fetchone()["c"],
        }
        # Recent anomalies
        anomalies = _rows(conn.execute(
            "SELECT * FROM anomalies ORDER BY detected_at DESC LIMIT 10"
        ))
        # Signal timeline (last 30 days)
        timeline = _rows(conn.execute(
            "SELECT date(timestamp) as day, COUNT(*) as count "
            "FROM signals GROUP BY day ORDER BY day"
        ))
        # Modulation distribution
        mod_dist = _rows(conn.execute(
            "SELECT modulation, COUNT(*) as count FROM signals "
            "WHERE modulation IS NOT NULL GROUP BY modulation ORDER BY count DESC"
        ))
        # Space weather
        weather = _rows(conn.execute(
            "SELECT * FROM space_weather ORDER BY timestamp DESC LIMIT 7"
        ))
        return render_template("dashboard.html",
                               stats=stats, anomalies=anomalies,
                               timeline=timeline, mod_dist=mod_dist,
                               weather=weather)
    finally:
        conn.close()


@app.route("/map")
def map_view():
    return render_template("map.html")


@app.route("/signals")
def signals_page():
    return render_template("signals.html")


@app.route("/patterns")
def patterns_page():
    return render_template("patterns.html")


@app.route("/graph")
def graph_page():
    return render_template("graph.html")


@app.route("/sea")
def sea_page():
    return render_template("sea.html")


@app.route("/a2ad")
def a2ad_page():
    return render_template("a2ad.html")


@app.route("/amphibious")
def amphibious_page():
    return render_template("amphibious.html")


# ── API ────────────────────────────────────────────────────

@app.route("/api/signals")
def api_signals():
    conn = get_connection()
    try:
        limit = min(int(request.args.get("limit", 500)), 5000)
        offset = int(request.args.get("offset", 0))
        classification = request.args.get("classification")
        modulation = request.args.get("modulation")

        sql = "SELECT * FROM signals"
        params: list = []
        clauses = []
        if classification:
            clauses.append("classification = ?")
            params.append(classification)
        if modulation:
            clauses.append("modulation = ?")
            params.append(modulation)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = _rows(conn.execute(sql, params))
        total = conn.execute(
            "SELECT COUNT(*) c FROM signals" +
            (" WHERE " + " AND ".join(clauses) if clauses else ""),
            params[:-2] if clauses else []
        ).fetchone()["c"]
        return jsonify({"signals": rows, "total": total})
    finally:
        conn.close()


@app.route("/api/stations")
def api_stations():
    conn = get_connection()
    try:
        return jsonify({"stations": _rows(conn.execute("SELECT * FROM stations"))})
    finally:
        conn.close()


@app.route("/api/emitters")
def api_emitters():
    conn = get_connection()
    try:
        return jsonify({"emitters": _rows(conn.execute(
            "SELECT * FROM emitters ORDER BY signal_count DESC"
        ))})
    finally:
        conn.close()


@app.route("/api/map/signals")
def api_map_signals():
    """Signals with location for map markers — clustered by grid cell."""
    conn = get_connection()
    try:
        resolution = float(request.args.get("resolution", 1.0))
        rows = _rows(conn.execute("""
            SELECT
                ROUND(lat / ?) * ? as grid_lat,
                ROUND(lon / ?) * ? as grid_lon,
                COUNT(*) as count,
                AVG(power_dbm) as avg_power,
                GROUP_CONCAT(DISTINCT modulation) as modulations,
                GROUP_CONCAT(DISTINCT classification) as classifications
            FROM signals
            GROUP BY grid_lat, grid_lon
        """, [resolution, resolution, resolution, resolution]))
        return jsonify({"clusters": rows})
    finally:
        conn.close()


@app.route("/api/map/stations")
def api_map_stations():
    conn = get_connection()
    try:
        return jsonify({"stations": _rows(conn.execute(
            "SELECT station_id, callsign, name, lat, lon, station_type, status FROM stations"
        ))})
    finally:
        conn.close()


@app.route("/api/map/emitters")
def api_map_emitters():
    conn = get_connection()
    try:
        return jsonify({"emitters": _rows(conn.execute(
            "SELECT emitter_id, name, lat, lon, freq_hz, modulation, emitter_type, "
            "signal_count, confidence FROM emitters WHERE lat IS NOT NULL"
        ))})
    finally:
        conn.close()


@app.route("/api/patterns")
def api_patterns():
    conn = get_connection()
    try:
        return jsonify({"patterns": _rows(conn.execute(
            "SELECT * FROM patterns ORDER BY confidence DESC"
        ))})
    finally:
        conn.close()


@app.route("/api/anomalies")
def api_anomalies():
    conn = get_connection()
    try:
        severity = request.args.get("severity")
        sql = "SELECT * FROM anomalies"
        params: list = []
        if severity:
            sql += " WHERE severity = ?"
            params.append(severity)
        sql += " ORDER BY detected_at DESC"
        return jsonify({"anomalies": _rows(conn.execute(sql, params))})
    finally:
        conn.close()


@app.route("/api/graph")
def api_graph():
    """Return knowledge graph as nodes + edges for D3 force layout."""
    conn = get_connection()
    try:
        limit = min(int(request.args.get("limit", 300)), 1000)
        relation_filter = request.args.get("relation")

        # Get edges
        sql = "SELECT * FROM kg_edges"
        params: list = []
        if relation_filter:
            sql += " WHERE relation = ?"
            params.append(relation_filter)
        sql += " ORDER BY weight DESC LIMIT ?"
        params.append(limit)
        edges = _rows(conn.execute(sql, params))

        # Collect unique nodes
        node_set: dict[str, dict] = {}
        for e in edges:
            for prefix in ("source", "target"):
                ntype = e[f"{prefix}_type"]
                nid = e[f"{prefix}_id"]
                key = f"{ntype}:{nid}"
                if key not in node_set:
                    node_set[key] = {"id": key, "type": ntype, "entity_id": nid, "label": nid[:12]}

        # Enrich labels
        for key, node in node_set.items():
            ntype, nid = node["type"], node["entity_id"]
            if ntype == "station":
                row = conn.execute("SELECT name, callsign FROM stations WHERE station_id=?", (nid,)).fetchone()
                if row:
                    node["label"] = row["callsign"] or row["name"]
            elif ntype == "emitter":
                row = conn.execute("SELECT name, emitter_type FROM emitters WHERE emitter_id=?", (nid,)).fetchone()
                if row:
                    node["label"] = row["name"]
                    node["subtype"] = row["emitter_type"]
            elif ntype == "band":
                row = conn.execute("SELECT name FROM frequency_bands WHERE band_id=?", (nid,)).fetchone()
                if row:
                    node["label"] = row["name"]

        # Format for D3
        nodes = list(node_set.values())
        links = [{"source": f"{e['source_type']}:{e['source_id']}",
                   "target": f"{e['target_type']}:{e['target_id']}",
                   "relation": e["relation"],
                   "weight": e["weight"]} for e in edges]

        # Relation type counts
        rel_counts = _rows(conn.execute(
            "SELECT relation, COUNT(*) as count FROM kg_edges GROUP BY relation ORDER BY count DESC"
        ))

        return jsonify({"nodes": nodes, "links": links, "relation_counts": rel_counts})
    finally:
        conn.close()


@app.route("/api/bands")
def api_bands():
    conn = get_connection()
    try:
        return jsonify({"bands": _rows(conn.execute("SELECT * FROM frequency_bands ORDER BY min_freq_hz"))})
    finally:
        conn.close()


@app.route("/api/a2ad/zones")
def api_a2ad_zones():
    """Weapon system deployment sites with range ring metadata."""
    from a2ad_mapper import WEAPON_SYSTEMS, get_zones
    return jsonify({
        "systems": list(WEAPON_SYSTEMS.values()),
        "zones": get_zones(),
    })


@app.route("/api/amphibious/summary")
def api_amphibious_summary():
    from amphibious_analyzer import get_summary
    return jsonify(get_summary())


@app.route("/api/amphibious/zones")
def api_amphibious_zones():
    from amphibious_analyzer import LANDING_ZONES, slope_viability, slope_color
    zones = [
        {**z, "viability": slope_viability(z["slope_deg"]), "color": slope_color(z["slope_deg"])}
        for z in LANDING_ZONES
    ]
    return jsonify({"zones": zones})


@app.route("/api/amphibious/lift")
def api_amphibious_lift():
    from amphibious_analyzer import calc_lift_capacity, AMPHIBIOUS_FLEET
    result = calc_lift_capacity()
    result["fleet"] = AMPHIBIOUS_FLEET
    return jsonify(result)


@app.route("/api/amphibious/weather")
def api_amphibious_weather():
    from amphibious_analyzer import get_weather_windows
    return jsonify({"windows": get_weather_windows()})


@app.route("/api/amphibious/crossing")
def api_amphibious_crossing():
    from amphibious_analyzer import get_crossing_analysis
    return jsonify({"corridors": get_crossing_analysis()})


@app.route("/api/amphibious/detection")
def api_amphibious_detection():
    from amphibious_analyzer import get_detection_curve
    return jsonify({"curve": get_detection_curve()})


@app.route("/api/sea/vessels")
def api_sea_vessels():
    """Latest AIS position per MMSI — used by the vessel track layer."""
    from domains.sea.tracker import get_latest_positions
    limit = min(int(request.args.get("limit", 2000)), 10000)
    return jsonify({"vessels": get_latest_positions(limit)})


@app.route("/api/sea/history")
def api_sea_history():
    """Chronological position history for a single MMSI."""
    mmsi = request.args.get("mmsi", "").strip()
    if not mmsi:
        return jsonify({"error": "mmsi param required"}), 400
    from domains.sea.tracker import get_vessel_history
    limit = min(int(request.args.get("limit", 200)), 2000)
    return jsonify({"history": get_vessel_history(mmsi, limit)})


@app.route("/api/sea/summary")
def api_sea_summary():
    """Fleet-level statistics from sg_tracks."""
    from domains.sea.tracker import get_vessel_summary
    return jsonify(get_vessel_summary())


@app.route("/api/sea/kalibr-zones")
def api_kalibr_zones():
    """Latest position of every Kalibr-capable ORBAT vessel.

    Used by the Leaflet range ring layer to render 1 500 km threat circles.
    """
    from domains.sea.tracker import get_kalibr_positions
    positions = get_kalibr_positions()
    return jsonify({"kalibr_vessels": positions, "range_km": 1500})


@app.route("/api/orbat/vessels")
def api_orbat_vessels():
    """All ORBAT vessel records."""
    conn = get_connection()
    try:
        rows = _rows(conn.execute(
            "SELECT * FROM vessel_orbat ORDER BY nation, vessel_name"
        ))
        return jsonify({"vessels": rows, "total": len(rows)})
    finally:
        conn.close()


@app.route("/api/orbat/kg-nodes")
def api_orbat_kg_nodes():
    """Vessel KG nodes for knowledge-graph overlay."""
    conn = get_connection()
    try:
        rows = _rows(conn.execute(
            "SELECT * FROM vessel_kg_nodes ORDER BY nation, vessel_name"
        ))
        return jsonify({"nodes": rows})
    finally:
        conn.close()


@app.route("/api/orbat/sync", methods=["POST"])
def api_orbat_sync():
    """Sync vessel_orbat → vessel_kg_nodes and return counts."""
    from domains.sea.tracker import build_orbat_kg_nodes
    return jsonify(build_orbat_kg_nodes())


@app.route("/api/weather")
def api_weather():
    conn = get_connection()
    try:
        return jsonify({"weather": _rows(conn.execute(
            "SELECT * FROM space_weather ORDER BY timestamp DESC LIMIT 30"
        ))})
    finally:
        conn.close()


@app.route("/api/stats")
def api_stats():
    conn = get_connection()
    try:
        return jsonify({
            "signals": conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"],
            "emitters": conn.execute("SELECT COUNT(*) c FROM emitters").fetchone()["c"],
            "stations": conn.execute("SELECT COUNT(*) c FROM stations").fetchone()["c"],
            "patterns": conn.execute("SELECT COUNT(*) c FROM patterns").fetchone()["c"],
            "anomalies": conn.execute("SELECT COUNT(*) c FROM anomalies WHERE resolved=0").fetchone()["c"],
            "kg_edges": conn.execute("SELECT COUNT(*) c FROM kg_edges").fetchone()["c"],
        })
    finally:
        conn.close()


# ── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GeoSIGINT — Geospatial Signal Intelligence Analyzer")
    parser.add_argument("--port", type=int, default=5099)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--seed", action="store_true", help="Seed demo data before starting")
    args = parser.parse_args()

    init_db()

    if args.seed:
        from seed_data import run_seed
        run_seed()

    app.run(host="0.0.0.0", port=args.port, debug=args.debug)
