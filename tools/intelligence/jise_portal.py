# CUI // SP-CTI
"""JISE Portal Data Retrieval API — tools.intelligence.jise_portal

Provides a GET REST endpoint that returns structured intelligence records
consumable by the Joint Intelligence Support Element (JISE) portal.

NIST 800-53 Controls:
  SA-11  — Developer Security Testing and Evaluation
  CM-3   — Configuration Change Control
  AU-2   — Event Logging
  AC-3   — Access Enforcement

Usage (Flask blueprint):
    from tools.intelligence.jise_portal import jise_bp
    app.register_blueprint(jise_bp)

    # GET /api/v1/jise/portal-data
    # GET /api/v1/jise/portal-data?classification=CUI
    # GET /api/v1/jise/portal-data?source=SIGINT&limit=50

Programmatic:
    from tools.intelligence.jise_portal import get_jise_portal_data
    records = get_jise_portal_data(classification="CUI", limit=100)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CLASSIFICATIONS = ("CUI", "FOUO", "UNCLASSIFIED", "SECRET")
VALID_SOURCES = ("SIGINT", "HUMINT", "OSINT", "GEOINT", "IMINT", "FININT")
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

# ---------------------------------------------------------------------------
# Seed data — deterministic records for testing and demo purposes.
# In production, replace _SEED_RECORDS with a DB query via get_connection().
# ---------------------------------------------------------------------------

_SEED_RECORDS: List[Dict[str, Any]] = [
    {
        "id": "jise-rec-0001",
        "source": "SIGINT",
        "classification": "CUI",
        "summary": "Intercept corroborating previously reported activity in AO-7.",
        "timestamp": "2026-05-16T00:00:00+00:00",
        "confidence": "HIGH",
        "region": "CENTCOM",
    },
    {
        "id": "jise-rec-0002",
        "source": "HUMINT",
        "classification": "CUI",
        "summary": "Source reports increased logistical movement near grid 4QFJ1234.",
        "timestamp": "2026-05-15T18:30:00+00:00",
        "confidence": "MEDIUM",
        "region": "EUCOM",
    },
    {
        "id": "jise-rec-0003",
        "source": "OSINT",
        "classification": "FOUO",
        "summary": "Open-source indicators of infrastructure exploitation attempt.",
        "timestamp": "2026-05-15T12:00:00+00:00",
        "confidence": "LOW",
        "region": "INDOPACOM",
    },
    {
        "id": "jise-rec-0004",
        "source": "GEOINT",
        "classification": "CUI",
        "summary": "Satellite imagery confirms construction of a staging area.",
        "timestamp": "2026-05-14T08:00:00+00:00",
        "confidence": "HIGH",
        "region": "AFRICOM",
    },
    {
        "id": "jise-rec-0005",
        "source": "SIGINT",
        "classification": "UNCLASSIFIED",
        "summary": "Unclassified traffic pattern analysis — public release authorized.",
        "timestamp": "2026-05-13T06:00:00+00:00",
        "confidence": "MEDIUM",
        "region": "NORTHCOM",
    },
]


# ---------------------------------------------------------------------------
# Core retrieval function
# ---------------------------------------------------------------------------

def get_jise_portal_data(
    classification: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    """Return intelligence records consumable by the JISE portal.

    Args:
        classification: Filter by classification level (e.g. "CUI").
        source:         Filter by collection source (e.g. "SIGINT").
        limit:          Maximum number of records to return (default 200).

    Returns:
        List of record dicts, each containing id, source, classification,
        timestamp, and optional metadata fields.

    Raises:
        ValueError: if limit is not a valid integer.
        TypeError:  if limit is of an incompatible type.
    """
    # Validate limit — raises TypeError/ValueError on bad input
    limit = int(limit)

    records = list(_SEED_RECORDS)

    if classification is not None:
        records = [r for r in records if r["classification"] == classification]

    if source is not None:
        records = [r for r in records if r["source"] == source]

    return records[:limit]


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------

jise_bp = Blueprint("jise", __name__, url_prefix="/api/v1/jise")


@jise_bp.route("/portal-data", methods=["GET"])
def portal_data():
    """GET /api/v1/jise/portal-data — Return JISE portal records.

    Query Parameters:
        classification (str, optional): Filter by classification level.
        source         (str, optional): Filter by collection source.
        limit          (int, optional): Max records to return (default 200).

    Returns:
        200 JSON: {"data": [...], "count": N, "timestamp": "..."}
        400 JSON: {"error": "<message>"} on invalid parameters.
    """
    classification = request.args.get("classification")
    source = request.args.get("source")
    raw_limit = request.args.get("limit", str(DEFAULT_LIMIT))

    # Validate limit
    try:
        limit = int(raw_limit)
    except (ValueError, TypeError):
        return jsonify({"error": f"Invalid 'limit' parameter: '{raw_limit}'. Must be an integer."}), 400

    if limit < 1 or limit > MAX_LIMIT:
        return jsonify({
            "error": f"'limit' must be between 1 and {MAX_LIMIT}, got {limit}."
        }), 400

    try:
        records = get_jise_portal_data(
            classification=classification,
            source=source,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "data": records,
        "count": len(records),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200
