# CUI // SP-CTI
"""NLQ (Natural Language Query) API blueprint — compliance database queries.

Endpoints:
    POST /api/nlq/query   — Execute NLQ against ICDEV database
    GET  /api/nlq/schema  — Return database schema for context
    GET  /api/nlq/history — Query history (audit trail)

Decision D30: Bedrock for NLQ→SQL (air-gap safe, GovCloud available).
Decision D34: Read-only SQL enforcement (append-only audit must not be compromised).
"""

import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request

nlq_bp = Blueprint("nlq_api", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@nlq_bp.route("/api/nlq/query", methods=["POST"])
def execute_nlq():
    """Execute a natural language query against the ICDEV database."""
    from tools.dashboard.nlq_processor import process_nlq_query

    data = request.get_json(force=True)
    query_text = data.get("query", "").strip()
    actor = data.get("actor", "dashboard-user")

    if not query_text:
        return jsonify({"error": "Query text is required"}), 400

    result = process_nlq_query(query_text, actor=actor)
    return jsonify(result)


@nlq_bp.route("/api/nlq/schema", methods=["GET"])
def get_schema():
    """Return the database schema for NLQ context."""
    from tools.dashboard.nlq_processor import extract_schema

    schema = extract_schema(DB_PATH)
    return jsonify({"schema": schema, "classification": "CUI"})


@nlq_bp.route("/api/nlq/history", methods=["GET"])
def get_history():
    """Return NLQ query history."""
    limit = min(int(request.args.get("limit", 20)), 100)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM nlq_queries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return jsonify({
            "queries": [dict(r) for r in rows],
            "classification": "CUI",
        })
    finally:
        conn.close()
