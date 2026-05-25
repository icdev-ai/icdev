#!/usr/bin/env python3
# CUI // SP-CTI
"""SparkPilot Dashboard — AI Co-Pilot for Embedded Systems.

Flask web dashboard for SparkPilot: missions, simulator, device fleet,
firmware management, edge AI, and compliance.

Usage:
    python tools/dashboard/app.py                # Start on port 5050
    python tools/dashboard/app.py --port 5050    # Custom port
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template, jsonify, request

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "sparkpilot.db"

app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))
app.secret_key = os.environ.get("SPARKPILOT_SECRET", "sparkpilot-dev-key")

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Template Context ──────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {
        "app_name": "SparkPilot",
        "app_version": "1.0.0",
        "now": datetime.now(timezone.utc),
    }


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Home dashboard with stats overview."""
    conn = _get_db()
    try:
        stats = {
            "devices": conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
            "devices_online": conn.execute("SELECT COUNT(*) FROM devices WHERE status='online'").fetchone()[0],
            "firmware_builds": conn.execute("SELECT COUNT(*) FROM firmware_builds").fetchone()[0],
            "missions_total": conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0],
            "missions_completed": conn.execute("SELECT COUNT(*) FROM mission_completion_log WHERE status='completed'").fetchone()[0],
            "ml_models": conn.execute("SELECT COUNT(*) FROM ml_models").fetchone()[0],
            "sim_sessions": conn.execute("SELECT COUNT(*) FROM simulator_sessions WHERE status='running'").fetchone()[0],
            "crash_dumps": conn.execute("SELECT COUNT(*) FROM crash_dump_log").fetchone()[0],
            "ota_pending": conn.execute("SELECT COUNT(*) FROM ota_update_log WHERE status='pending'").fetchone()[0],
            "nl_commands": conn.execute("SELECT COUNT(*) FROM nl_commands").fetchone()[0],
        }
        recent_audit = conn.execute(
            "SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        return render_template("index.html", stats=stats,
                               recent_audit=[dict(r) for r in recent_audit])
    finally:
        conn.close()


@app.route("/missions")
def missions():
    """Gamified missions page."""
    conn = _get_db()
    try:
        all_missions = conn.execute(
            "SELECT * FROM missions ORDER BY mission_number"
        ).fetchall()
        missions_list = []
        for m in all_missions:
            md = dict(m)
            for f in ("objectives", "hints", "prerequisites"):
                if md.get(f):
                    try:
                        md[f] = json.loads(md[f])
                    except (json.JSONDecodeError, TypeError):
                        pass
            missions_list.append(md)

        # User progress (default player)
        progress = conn.execute(
            "SELECT * FROM user_progress WHERE user_id = 'player1'"
        ).fetchone()
        completed_ids = set()
        if progress:
            rows = conn.execute(
                "SELECT mission_id FROM mission_completion_log "
                "WHERE user_id='player1' AND status='completed'"
            ).fetchall()
            completed_ids = {r["mission_id"] for r in rows}

        return render_template("missions.html", missions=missions_list,
                               progress=dict(progress) if progress else None,
                               completed_ids=completed_ids)
    finally:
        conn.close()


@app.route("/devices")
def devices():
    """Fleet management / device registry."""
    conn = _get_db()
    try:
        all_devices = conn.execute(
            "SELECT * FROM devices ORDER BY created_at DESC"
        ).fetchall()
        total = len(all_devices)
        online = sum(1 for d in all_devices if d["status"] == "online")
        return render_template("devices.html",
                               devices=[dict(d) for d in all_devices],
                               total=total, online=online)
    finally:
        conn.close()


@app.route("/simulator")
def simulator():
    """Simulator sessions and virtual peripherals."""
    conn = _get_db()
    try:
        sessions = conn.execute(
            "SELECT * FROM simulator_sessions ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        peripherals = conn.execute(
            "SELECT * FROM virtual_peripherals ORDER BY peripheral_type"
        ).fetchall()
        return render_template("simulator.html",
                               sessions=[dict(s) for s in sessions],
                               peripherals=[dict(p) for p in peripherals])
    finally:
        conn.close()


@app.route("/firmware")
def firmware():
    """Firmware builds and OTA deployments."""
    conn = _get_db()
    try:
        builds = conn.execute(
            "SELECT * FROM firmware_builds ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        ota_log = conn.execute(
            "SELECT * FROM ota_update_log ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return render_template("firmware.html",
                               builds=[dict(b) for b in builds],
                               ota_log=[dict(o) for o in ota_log])
    finally:
        conn.close()


@app.route("/edge-ai")
def edge_ai():
    """Edge AI / TinyML model management."""
    conn = _get_db()
    try:
        models = conn.execute(
            "SELECT * FROM ml_models ORDER BY created_at DESC"
        ).fetchall()
        inference = conn.execute(
            "SELECT model_id, COUNT(*) as count, AVG(latency_ms) as avg_latency, "
            "AVG(confidence) as avg_confidence FROM inference_telemetry GROUP BY model_id"
        ).fetchall()
        return render_template("edge_ai.html",
                               models=[dict(m) for m in models],
                               inference=[dict(i) for i in inference])
    finally:
        conn.close()


@app.route("/crashes")
def crashes():
    """Crash dump log and self-healing status."""
    conn = _get_db()
    try:
        crashes = conn.execute(
            "SELECT * FROM crash_dump_log ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return render_template("crashes.html",
                               crashes=[dict(c) for c in crashes])
    finally:
        conn.close()


# ── API Endpoints ─────────────────────────────────────────────────

@app.route("/api/nl-command", methods=["POST"])
def api_nl_command():
    """Process a natural language command."""
    data = request.get_json(force=True)
    command = data.get("command", "")
    board = data.get("board", "simulator")

    # Import NL engine
    sys.path.insert(0, str(BASE_DIR / "tools" / "embedded"))
    from nl_to_firmware import generate_code
    result = generate_code(command, board)
    return jsonify(result)


@app.route("/api/mission/<int:num>")
def api_mission_detail(num):
    """Get full mission details including hints and starter code."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM missions WHERE mission_number = ?", (num,)
        ).fetchone()
        if not row:
            return jsonify({"status": "error", "message": f"Mission {num} not found"}), 404
        m = dict(row)
        for field in ("objectives", "hints", "prerequisites"):
            if m.get(field):
                try:
                    m[field] = json.loads(m[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return jsonify({"status": "success", "mission": m})
    finally:
        conn.close()


@app.route("/api/mission/start", methods=["POST"])
def api_mission_start():
    """Start a mission."""
    data = request.get_json(force=True)
    mission_num = data.get("mission", 1)
    user_id = data.get("user_id", "player1")

    sys.path.insert(0, str(BASE_DIR / "tools" / "missions"))
    from mission_engine import start_mission
    result = start_mission(mission_num, user_id)
    return jsonify(result)


@app.route("/api/mission/complete", methods=["POST"])
def api_mission_complete():
    """Complete a mission."""
    data = request.get_json(force=True)
    mission_num = data.get("mission", 1)
    user_id = data.get("user_id", "player1")
    code = data.get("code", "")

    sys.path.insert(0, str(BASE_DIR / "tools" / "missions"))
    from mission_engine import complete_mission
    result = complete_mission(mission_num, user_id, code)
    return jsonify(result)


@app.route("/api/sim/create", methods=["POST"])
def api_sim_create():
    """Create a simulator session."""
    data = request.get_json(force=True) if request.is_json else {}
    user_id = data.get("user_id", "player1")

    sys.path.insert(0, str(BASE_DIR / "tools" / "simulator"))
    from sim_runner import create_session
    result = create_session(user_id)
    return jsonify(result)


@app.route("/api/device/register", methods=["POST"])
def api_device_register():
    """Register a new device."""
    data = request.get_json(force=True)
    name = data.get("name", "device")
    board = data.get("board", "simulator")

    sys.path.insert(0, str(BASE_DIR / "tools" / "fleet"))
    from device_registry import register_device
    result = register_device(name, board=board)
    return jsonify(result)


@app.route("/agents")
def agents():
    """AI Agents and LLM orchestration status."""
    conn = _get_db()
    try:
        nl_commands = conn.execute(
            "SELECT * FROM nl_commands ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        audit = conn.execute(
            "SELECT * FROM audit_trail WHERE event_type LIKE '%agent%' OR event_type LIKE '%llm%' "
            "OR action LIKE '%agent%' OR action LIKE '%generate%' "
            "ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
        # Agent definitions from SparkPilot spec
        agent_defs = [
            {"name": "Orchestrator", "port": 9443, "role": "Task routing, workflow management, NL command parsing", "tier": "Core", "status": "active"},
            {"name": "Architect", "port": 9444, "role": "Firmware architecture design, ANVIL A/T phases", "tier": "Core", "status": "active"},
            {"name": "Embedded Builder", "port": 9445, "role": "TDD C/C++ code gen, CMake, FreeRTOSConfig.h", "tier": "Domain", "status": "active"},
            {"name": "Compliance", "port": 9446, "role": "SBOM, NIST, IEC 62443, DO-178C (Pro Mode)", "tier": "Domain", "status": "standby"},
            {"name": "Security", "port": 9447, "role": "SAST, CVE triage, secret detection", "tier": "Domain", "status": "active"},
            {"name": "Knowledge", "port": 9449, "role": "Self-healing patterns, crash analysis, best practices", "tier": "Support", "status": "active"},
            {"name": "Monitor", "port": 9450, "role": "Fleet health, telemetry, inference metrics", "tier": "Support", "status": "active"},
            {"name": "Edge AI", "port": 9451, "role": "TFLite Micro, model lifecycle, Edge Impulse", "tier": "Domain", "status": "active"},
            {"name": "Fleet Manager", "port": 9452, "role": "Device registry, OTA deployment, canary rollouts", "tier": "Domain", "status": "active"},
            {"name": "MBSE", "port": 9453, "role": "SysML model-to-firmware traceability", "tier": "Domain", "status": "standby"},
            {"name": "DevSecOps", "port": 9457, "role": "Embedded CI/CD, firmware signing, secure boot", "tier": "Domain", "status": "standby"},
        ]
        stats = {
            "total_agents": len(agent_defs),
            "active_agents": sum(1 for a in agent_defs if a["status"] == "active"),
            "nl_commands": conn.execute("SELECT COUNT(*) FROM nl_commands").fetchone()[0],
            "crash_analyses": conn.execute("SELECT COUNT(*) FROM crash_dump_log WHERE analysis IS NOT NULL").fetchone()[0],
        }
        return render_template("agents.html", agents=agent_defs, stats=stats,
                               nl_commands=[dict(r) for r in nl_commands],
                               audit=[dict(r) for r in audit])
    finally:
        conn.close()


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "app": "sparkpilot", "version": "1.0.0"})


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SparkPilot Dashboard")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"SparkPilot Dashboard starting on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
