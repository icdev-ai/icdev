#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
"""Bell-LaPadula MAC Enforcer for ICDEV™.

Implements:
- Simple Security Property (no read-up)
- *-Property (no write-down)
- Compartment strict-subset checks (COI, LAC, ECI)

Decorator API:
    @require_clearance("SECRET")
    @require_compartment("COI_FINANCE")
    def handler():
        ...

Reuses ``tools.compliance.classification_manager`` for clearance ordering.
"""

import functools
import json
from typing import Callable, Optional, Set

logger = get_logger("security.mac")

# ---------------------------------------------------------------------------
# Clearance helpers
# ---------------------------------------------------------------------------

def _get_clearance_order(cls: str) -> int:
    try:
        from tools.compliance.classification_manager import get_clearance_order as _go
        return _go(cls)
    except Exception:
        return {"PUBLIC": 0, "CUI": 1, "SECRET": 2, "TOP SECRET": 3, "TOP SECRET//SCI": 4}.get(
            (cls or "CUI").upper(), 1
        )


def _get_required_clearance(design_classification: str) -> str:
    try:
        from tools.compliance.classification_manager import get_required_clearance as _grc
        return _grc(design_classification)
    except Exception:
        return (design_classification or "CUI").upper()


def _is_readonly_for_user(design_classification: str, user_clearance: Optional[str]) -> bool:
    try:
        from tools.compliance.classification_manager import is_readonly_for_user as _iro
        return _iro(design_classification, user_clearance)
    except Exception:
        if user_clearance is None:
            return False
        return _get_clearance_order(user_clearance) < _get_clearance_order(design_classification)


# ---------------------------------------------------------------------------
# Core MAC checks
# ---------------------------------------------------------------------------

def can_read(resource_classification: str, ctx) -> bool:
    """Simple Security Property: no read-up.

    A subject can read an object only if the subject's clearance is
    greater than or equal to the object's classification.
    """
    if ctx is None:
        return True  # Unauthenticated / system context — allow for compatibility
    user_clearance = getattr(ctx, "clearance_level", 0)
    return user_clearance >= _get_clearance_order(resource_classification)


def can_write(resource_classification: str, ctx) -> bool:
    """Star Property: no write-down.

    A subject can write to an object only if the subject's clearance
    equals the object's classification (prevents information flow downward).
    """
    if ctx is None:
        return True
    user_clearance = getattr(ctx, "clearance_level", 0)
    return user_clearance == _get_clearance_order(resource_classification)


def can_access_compartment(resource_compartments: Set[str], ctx) -> bool:
    """Compartment strict-subset check.

    Resource compartments must be a subset of the user's compartments.
    Empty resource compartment set is always allowed.
    """
    if ctx is None:
        return True
    user_compartments = getattr(ctx, "compartments", frozenset())
    if not resource_compartments:
        return True
    return resource_compartments.issubset(user_compartments)


def check_mac(resource_classification: str, resource_compartments: Optional[Set[str]], ctx, action: str = "read") -> bool:
    """Combined MAC check for read or write."""
    if action == "read":
        if not can_read(resource_classification, ctx):
            return False
    elif action in ("write", "create", "update", "delete"):
        if not can_write(resource_classification, ctx):
            return False
    if resource_compartments and not can_access_compartment(resource_compartments, ctx):
        return False
    return True


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def require_clearance(min_level: str):
    """Flask route decorator: abort 403 if user clearance < min_level."""
    min_order = _get_clearance_order(min_level)

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                from flask import g, abort, jsonify, make_response
            except ImportError:
                return f(*args, **kwargs)

            ctx = getattr(g, "security_context", None)
            user_level = getattr(ctx, "clearance_level", 0) if ctx else 0
            if user_level < min_order:
                logger.warning(
                    "MAC denial: user %s clearance %s < required %s on %s",
                    getattr(ctx, "user_id", "?"),
                    user_level,
                    min_level,
                    f.__name__,
                )
                # JSON vs HTML response based on request Accept header
                if _wants_json():
                    return make_response(jsonify({"error": "Insufficient clearance", "code": "MAC_DENIED"}), 403)
                abort(403, description="Insufficient clearance")
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_compartment(compartment: str):
    """Flask route decorator: abort 403 if user lacks the compartment."""
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                from flask import g, abort, jsonify, make_response
            except ImportError:
                return f(*args, **kwargs)

            ctx = getattr(g, "security_context", None)
            user_compartments = getattr(ctx, "compartments", frozenset()) if ctx else frozenset()
            if compartment not in user_compartments:
                logger.warning(
                    "MAC compartment denial: user %s missing %s on %s",
                    getattr(ctx, "user_id", "?"),
                    compartment,
                    f.__name__,
                )
                if _wants_json():
                    return make_response(jsonify({"error": "Missing compartment", "code": "COMPARTMENT_DENIED"}), 403)
                abort(403, description="Missing compartment")
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _wants_json() -> bool:
    try:
        from flask import request
        accept = request.headers.get("Accept", "")
        return "application/json" in accept or request.is_json
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Violation logging
# ---------------------------------------------------------------------------

def log_mac_violation(
    conn,
    event_type: str,
    user_id: str,
    resource_classification: str,
    action: str,
    details: Optional[dict] = None,
) -> None:
    """Log a Bell-LaPadula violation to the append-only ``mac_violations`` table."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO mac_violations
            (user_id, event_type, resource_classification, action, details, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                event_type,
                resource_classification,
                action,
                json.dumps(details or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log MAC violation: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Classification Enforcer CLI")
    parser.add_argument("--check", action="store_true", help="Run self-check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.check:
        # Minimal self-check with synthetic contexts
        from tools.security.security_context import SecurityContext
        results = {
            "can_read_public": can_read("PUBLIC", SecurityContext(clearance_level=1)),
            "can_read_cui": can_read("CUI", SecurityContext(clearance_level=1)),
            "cannot_read_secret_from_cui": not can_read("SECRET", SecurityContext(clearance_level=1)),
            "can_write_same": can_write("CUI", SecurityContext(clearance_level=1)),
            "cannot_write_down": not can_write("PUBLIC", SecurityContext(clearance_level=1)),
            "compartment_ok": can_access_compartment({"COI_FINANCE"}, SecurityContext(compartments=frozenset({"COI_FINANCE", "LAC_DC_EAST"}))),
            "compartment_fail": not can_access_compartment({"COI_ENGINEERING"}, SecurityContext(compartments=frozenset({"COI_FINANCE"}))),
        }
        print(json.dumps(results, indent=2) if args.json else str(results))


if __name__ == "__main__":
    main()
