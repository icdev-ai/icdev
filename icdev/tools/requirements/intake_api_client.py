#!/usr/bin/env python3
# CUI // SP-CTI
"""HTTP client for the ICDEV requirements intake REST API.

Replaces direct calls to the deprecated tools/requirements/intake_engine.py.
All calls go through the running dashboard at ICDEV_DASHBOARD_URL.

Usage:
    python tools/requirements/intake_api_client.py \\
        --create --project-id proj-123 --customer-name "USAF" --json
    python tools/requirements/intake_api_client.py \\
        --turn --session-id sess-abc --message "We need a secure login" --json
    python tools/requirements/intake_api_client.py \\
        --readiness --session-id sess-abc --json
    python tools/requirements/intake_api_client.py \\
        --gaps --session-id sess-abc --json
    python tools/requirements/intake_api_client.py \\
        --decompose --session-id sess-abc --level story --json
    python tools/requirements/intake_api_client.py \\
        --export --session-id sess-abc --json
    python tools/requirements/intake_api_client.py \\
        --get --session-id sess-abc --json

Programmatic API (import):
    from tools.requirements.intake_api_client import (
        create_session, process_turn, get_readiness,
        detect_gaps, decompose, export_session, get_session,
    )
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error as url_error
from urllib import request as url_request
from typing import Optional

BASE_URL = os.getenv("ICDEV_DASHBOARD_URL", "http://localhost:5050")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http(method: str, path: str, body: Optional[dict] = None, timeout: int = 30) -> dict:
    url = BASE_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = url_request.Request(url, data=data, headers=headers, method=method)
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            return json.loads(raw)
        except Exception:
            return {"error": f"HTTP {exc.code}: {raw[:200]}"}
    except url_error.URLError as exc:
        return {"error": f"Dashboard unreachable ({exc.reason}). Is it running at {BASE_URL}?"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_session(
    project_id: str = "",
    customer_name: str = "CLI User",
    customer_org: str = "",
    impact_level: str = "IL4",
    classification: str = "il4",
    role: str = "developer",
    goal: str = "build",
    frameworks: Optional[list] = None,
) -> dict:
    """Create a new intake session. Returns dict with session_id."""
    return _http("POST", "/api/intake/session", {
        "project_id": project_id,
        "customer_name": customer_name,
        "customer_org": customer_org,
        "classification": classification,
        "role": role,
        "goal": goal,
        "frameworks": frameworks or [],
    })


def process_turn(session_id: str, message: str) -> dict:
    """Process a conversation turn — extracts requirements and detects gaps."""
    return _http("POST", f"/api/intake/session/{session_id}/turn", {
        "message": message,
    })


def get_readiness(session_id: str) -> dict:
    """Score readiness across 5+ dimensions. Returns dict with overall_score."""
    return _http("GET", f"/api/intake/session/{session_id}/readiness")


def detect_gaps(session_id: str) -> dict:
    """Run gap detection for a session. Returns dict with gaps list."""
    return _http("GET", f"/api/intake/session/{session_id}/gaps")


def decompose(
    session_id: str,
    level: str = "story",
    bdd: bool = True,
) -> dict:
    """Decompose requirements into SAFe hierarchy. Returns dict with items_created."""
    return _http("POST", f"/api/intake/session/{session_id}/decompose", {
        "level": level,
        "generate_bdd": bdd,
    })


def export_session(session_id: str) -> dict:
    """Export all requirements and decomposition for a session."""
    return _http("GET", f"/api/intake/session/{session_id}/export")


def get_session(session_id: str) -> dict:
    """Get session metadata and current status."""
    return _http("GET", f"/api/intake/session/{session_id}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="ICDEV intake API client")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create intake session")
    group.add_argument("--turn", action="store_true", help="Process a conversation turn")
    group.add_argument("--readiness", action="store_true", help="Score readiness")
    group.add_argument("--gaps", action="store_true", help="Detect gaps")
    group.add_argument("--decompose", action="store_true", help="SAFe decomposition")
    group.add_argument("--export", action="store_true", help="Export session requirements")
    group.add_argument("--get", action="store_true", help="Get session details")

    p.add_argument("--session-id", metavar="SESS_ID", help="Intake session ID")
    p.add_argument("--project-id", default="", help="Project ID")
    p.add_argument("--customer-name", default="CLI User", help="Customer name")
    p.add_argument("--customer-org", default="", help="Customer org")
    p.add_argument("--classification", default="il4",
                   choices=["il2", "il4", "il5", "il6"], help="Classification level")
    p.add_argument("--role", default="developer", help="Stakeholder role")
    p.add_argument("--goal", default="build", help="Project goal")
    p.add_argument("--message", metavar="TEXT", help="Turn message (--turn)")
    p.add_argument("--level", default="story",
                   choices=["epic", "capability", "feature", "story"],
                   help="Decomposition level (--decompose)")
    p.add_argument("--no-bdd", action="store_true", help="Skip BDD generation (--decompose)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.create:
        result = create_session(
            project_id=args.project_id,
            customer_name=args.customer_name,
            customer_org=args.customer_org,
            classification=args.classification,
            role=args.role,
            goal=args.goal,
        )

    elif args.turn:
        if not args.session_id or not args.message:
            print(json.dumps({"error": "--session-id and --message required"}), file=sys.stderr)
            sys.exit(1)
        result = process_turn(args.session_id, args.message)

    elif args.readiness:
        if not args.session_id:
            print(json.dumps({"error": "--session-id required"}), file=sys.stderr)
            sys.exit(1)
        result = get_readiness(args.session_id)

    elif args.gaps:
        if not args.session_id:
            print(json.dumps({"error": "--session-id required"}), file=sys.stderr)
            sys.exit(1)
        result = detect_gaps(args.session_id)

    elif args.decompose:
        if not args.session_id:
            print(json.dumps({"error": "--session-id required"}), file=sys.stderr)
            sys.exit(1)
        result = decompose(args.session_id, level=args.level, bdd=not args.no_bdd)

    elif args.export:
        if not args.session_id:
            print(json.dumps({"error": "--session-id required"}), file=sys.stderr)
            sys.exit(1)
        result = export_session(args.session_id)

    elif args.get:
        if not args.session_id:
            print(json.dumps({"error": "--session-id required"}), file=sys.stderr)
            sys.exit(1)
        result = get_session(args.session_id)

    else:
        result = {"error": "unknown action"}

    if args.json or True:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
