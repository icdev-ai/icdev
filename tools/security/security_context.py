#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
"""ICDEV™ Security Context — thread-local user security attributes.

Provides a ``SecurityContext`` dataclass that captures:
- user_id, role, tenant_id
- clearance_level (numeric, computed from classification string)
- compartments (COI, LAC, ECI tags)
- impact_level, classification, mtls_cn, auth_method

Thread-local and Flask ``g`` integration for synchronous code;
``contextvars`` for async.  Reuses
``tools.compliance.classification_manager::get_clearance_order``.
"""

import json
import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional, Set

logger = get_logger("security.context")

# ---------------------------------------------------------------------------
# Classification helper
# ---------------------------------------------------------------------------

def _get_clearance_order(cls: str) -> int:
    """Return numeric clearance order (higher = more sensitive)."""
    try:
        from tools.compliance.classification_manager import get_clearance_order as _go
        return _go(cls)
    except Exception:
        return {"PUBLIC": 0, "CUI": 1, "SECRET": 2, "TOP SECRET": 3, "TOP SECRET//SCI": 4}.get(
            (cls or "CUI").upper(), 1
        )


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SecurityContext:
    user_id: str = ""
    role: str = ""
    clearance_level: int = 0
    compartments: frozenset[str] = field(default_factory=frozenset)
    impact_level: str = "IL4"
    tenant_id: Optional[str] = None
    classification: str = "CUI"
    mtls_cn: Optional[str] = None
    auth_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "clearance_level": self.clearance_level,
            "compartments": sorted(self.compartments),
            "impact_level": self.impact_level,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
            "mtls_cn": self.mtls_cn,
            "auth_method": self.auth_method,
        }

    def has_compartment(self, tag: str) -> bool:
        return tag in self.compartments

    def has_any_compartment(self, tags: Set[str]) -> bool:
        return bool(self.compartments & tags)

    def has_all_compartments(self, tags: Set[str]) -> bool:
        return tags.issubset(self.compartments)


# ---------------------------------------------------------------------------
# Thread-local + contextvar storage
# ---------------------------------------------------------------------------

_ctx_var: ContextVar[Optional[SecurityContext]] = ContextVar("security_context", default=None)
_thread_local = threading.local()


def get_security_context() -> Optional[SecurityContext]:
    """Return the current security context from thread-local or contextvar."""
    # Prefer contextvar (works in async)
    try:
        cv = _ctx_var.get()
        if cv is not None:
            return cv
    except LookupError:
        pass
    # Fallback to thread-local
    return getattr(_thread_local, "ctx", None)


def set_security_context(ctx: Optional[SecurityContext]) -> None:
    """Set the current security context for this thread / async task."""
    _ctx_var.set(ctx)
    _thread_local.ctx = ctx


def clear_security_context() -> None:
    """Remove the current security context."""
    _ctx_var.set(None)
    _thread_local.ctx = None


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------

def _extract_from_flask_g() -> Optional[SecurityContext]:
    """Build a SecurityContext from Flask ``g`` objects set by auth middleware."""
    try:
        from flask import g, request
    except ImportError:
        return None

    try:
        # Already attached?
        existing = getattr(g, "security_context", None)
        if isinstance(existing, SecurityContext):
            return existing

        # Derive from auth middleware fields
        user = getattr(g, "current_user", None) or {}
        tenant_id = getattr(g, "tenant_id", None)
        user_role = getattr(g, "user_role", None) or user.get("role", "")
        user_id = getattr(g, "user_id", None) or user.get("id", "") or user.get("user_id", "")

        # Compartments may be stored on the user dict or g
        compartments = set(user.get("compartments", []))
        extra = getattr(g, "compartments", None)
        if isinstance(extra, (list, tuple, set)):
            compartments |= set(extra)

        classification = user.get("classification", "CUI")
        clearance = _get_clearance_order(classification)

        mtls_cn = request.headers.get("X-Client-Cert-CN") if request else None
        auth_method = user.get("auth_method", "")

        ctx = SecurityContext(
            user_id=str(user_id) if user_id else "",
            role=str(user_role) if user_role else "",
            clearance_level=clearance,
            compartments=frozenset(compartments),
            impact_level=user.get("impact_level", "IL4"),
            tenant_id=str(tenant_id) if tenant_id else None,
            classification=classification.upper(),
            mtls_cn=mtls_cn,
            auth_method=auth_method,
        )
        g.security_context = ctx
        return ctx
    except RuntimeError:
        # Working outside of application context
        return None


def from_request(request) -> Optional[SecurityContext]:
    """Factory: build a SecurityContext from a Flask request object.

    Falls back to reading ``flask.g`` when request fields are sparse.
    """
    ctx = _extract_from_flask_g()
    if ctx is not None:
        return ctx

    # Minimal extraction from request alone
    headers = getattr(request, "headers", {}) or {}
    tenant_id = headers.get("X-Tenant-ID", None)
    role = headers.get("X-User-Role", "")
    user_id = headers.get("X-User-ID", "")
    classification = headers.get("X-Classification", "CUI")
    compartments = set()
    raw_compartments = headers.get("X-Compartments", "")
    if raw_compartments:
        compartments = {c.strip() for c in raw_compartments.split(",") if c.strip()}

    return SecurityContext(
        user_id=user_id,
        role=role,
        clearance_level=_get_clearance_order(classification),
        compartments=frozenset(compartments),
        tenant_id=tenant_id,
        classification=classification.upper(),
    )


def attach_to_flask_g(ctx: SecurityContext) -> None:
    """Attach an existing SecurityContext to Flask ``g`` and thread-local."""
    try:
        from flask import g
        g.security_context = ctx
    except ImportError:
        pass
    set_security_context(ctx)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Security Context CLI")
    parser.add_argument("--whoami", action="store_true", help="Show current context")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ctx = get_security_context()
    if args.whoami:
        if ctx:
            print(json.dumps(ctx.to_dict(), indent=2) if args.json else str(ctx))
        else:
            print(json.dumps({"error": "No security context set"}, indent=2) if args.json else "No security context set")


if __name__ == "__main__":
    main()
