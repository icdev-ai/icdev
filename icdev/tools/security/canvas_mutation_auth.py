# CUI // SP-CTI
"""Fail-closed auth gate for state-changing canvas routes (cnr-ops-01).

``guard_component_access`` only *blocks* unauthenticated requests when
``ICDEV_ENFORCE_CANVAS_ACCESS`` is set — otherwise it returns early and leaves
mutating endpoints (POST/PUT/DELETE/PATCH) open. That is a defense-in-depth gap
for canvases whose write routes change state (OHC model runs / SLOs / incidents,
NOCC alarms / incidents / RFCs / MOPs / maintenance).

This module provides a ``require_mutation_auth`` Flask ``before_request`` hook
that fails closed on mutating methods *regardless* of the enforce flag. Attach it
on a blueprint::

    from tools.security.canvas_mutation_auth import require_mutation_auth
    bp.before_request(require_mutation_auth)

Authentication is satisfied (in order) by:

  1. an active dashboard session (``session['user_id']`` / ``g.current_user``),
  2. ``ICDEV_AUTH_BYPASS`` — an explicit test-only opt-in,
  3. a *presented* ``ICDEV_DASHBOARD_API_KEY`` (``X-ICDEV-API-Key`` header or
     ``Authorization: Bearer``), compared constant-time via
     ``hmac.compare_digest``. Merely having the var set does NOT auto-authenticate;
     the caller must present it.

Read (GET/HEAD/OPTIONS) requests are never gated here — those keep whatever
access policy the canvas already applies. CSRF is a separate concern (cnr-plat-01).
"""
from __future__ import annotations

import hmac
import os

from flask import g, jsonify, request, session

_MUTATING_METHODS = ("POST", "PUT", "DELETE", "PATCH")


def is_authenticated() -> bool:
    """Return True if the current request carries a valid identity."""
    try:
        if session.get("user_id"):
            return True
    except Exception:
        # No session (blueprint mounted without a secret_key, etc.) — fall through.
        pass

    if getattr(g, "current_user", None):
        return True

    # Explicit test-only opt-in.
    if os.environ.get("ICDEV_AUTH_BYPASS"):
        return True

    # Presented-key semantics: only authenticate if the request actually presents
    # the configured key, compared in constant time.
    api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
    if api_key:
        presented = request.headers.get("X-ICDEV-API-Key", "")
        if not presented:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                presented = auth_header[len("Bearer "):].strip()
        if presented and hmac.compare_digest(presented, api_key):
            return True

    return False


def require_mutation_auth():
    """Flask ``before_request``: 401 on unauthenticated mutating requests.

    Returns ``None`` (request proceeds) for read methods and for authenticated
    mutating requests; returns a JSON 401 response otherwise.
    """
    if request.method not in _MUTATING_METHODS:
        return None
    if is_authenticated():
        return None
    return jsonify({"error": "Authentication required"}), 401
