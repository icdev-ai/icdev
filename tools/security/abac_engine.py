#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations
"""XACML-style ABAC Engine for ICDEV™.

Components:
- PIP (Policy Information Point): attribute lookup
- PDP (Policy Decision Point): decision evaluation with 60-second LRU cache
- PEP (Policy Enforcement Point): decorator for Flask routes

Policies are loaded from ``args/security_config.yaml`` under the
``abac_policies`` key.

Supported operators:
    >=, <, equals, contains_any, in, not_in

Public API:
    evaluate(subject_attrs, resource_attrs, action, env_attrs) -> Decision
    abac_protect(resource_attr_fn, action) -> decorator
"""

import functools
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("security.abac")

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE_DIR / "args" / "security_config.yaml"


def _load_config() -> dict:
    """Load security config YAML."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Decision:
    permit: bool
    policy_name: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"permit": self.permit, "policy_name": self.policy_name, "reason": self.reason}


PERMIT = Decision(permit=True, policy_name="default_permit", reason="No applicable policy found — default permit")
DENY = Decision(permit=False, policy_name="default_deny", reason="No applicable policy found — default deny")


# ---------------------------------------------------------------------------
# PIP — Policy Information Point
# ---------------------------------------------------------------------------

class PIP:
    """Lookup subject/resource/environment attributes."""

    @staticmethod
    def resolve(value_expr: Any, ctx: dict) -> Any:
        """Resolve a value expression against a context dict.

        Supports:
        - static values (string, int, bool)
        - "${subject.user_id}" style references into ctx
        """
        if isinstance(value_expr, str) and value_expr.startswith("${") and value_expr.endswith("}"):
            path = value_expr[2:-1]  # e.g. subject.user_id
            parts = path.split(".")
            current = ctx
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None
            return current
        return value_expr


# ---------------------------------------------------------------------------
# PDP — Policy Decision Point
# ---------------------------------------------------------------------------

class PDP:
    """Evaluate ABAC policies against a request."""

    def __init__(self, policies: Optional[List[dict]] = None):
        self._policies = policies or []
        self._cache: Dict[tuple, tuple[Decision, float]] = {}
        self._cache_ttl = 60.0

    def load_from_config(self) -> None:
        config = _load_config()
        self._policies = config.get("abac_policies", [])

    def _match_condition(self, condition: Any, actual_value: Any) -> bool:
        """Evaluate a single condition against an actual value."""
        if condition is None or condition == "*":
            return True
        if isinstance(condition, dict):
            op = condition.get("operator", "equals")
            target = condition.get("value")
            values = condition.get("values", [])
            if op == ">=":
                try:
                    return float(actual_value) >= float(target)
                except (TypeError, ValueError):
                    return False
            if op == "<":
                try:
                    return float(actual_value) < float(target)
                except (TypeError, ValueError):
                    return False
            if op == "equals":
                return str(actual_value) == str(target)
            if op == "in":
                return actual_value in values
            if op == "not_in":
                return actual_value not in values
            if op == "contains_any":
                if not isinstance(actual_value, (list, tuple, set, frozenset)):
                    return False
                return bool(set(actual_value) & set(values))
            if op == "contains_all":
                if not isinstance(actual_value, (list, tuple, set, frozenset)):
                    return False
                return set(values).issubset(set(actual_value))
            return False
        # Simple literal comparison
        if isinstance(condition, list):
            return actual_value in condition
        return str(actual_value) == str(condition)

    def _match_policy(self, policy: dict, subject: dict, resource: dict, action: str, env: dict) -> bool:
        """Check if a policy matches the request."""
        for section_name, section_attrs in (("subject", subject), ("resource", resource), ("environment", env)):
            policy_section = policy.get(section_name, {})
            for attr_name, condition in policy_section.items():
                actual = section_attrs.get(attr_name)
                if not self._match_condition(condition, actual):
                    return False
        # Action matching
        policy_actions = policy.get("action", ["*"])
        if policy_actions != ["*"] and action not in policy_actions:
            return False
        return True

    def evaluate(self, subject: dict, resource: dict, action: str, env: Optional[dict] = None) -> Decision:
        """Evaluate all policies and return a Decision.

        Uses a 60-second LRU cache keyed by a hash of the inputs.
        """
        env = env or {}
        # Build cache key from sorted JSON of inputs
        cache_key = (
            json.dumps(subject, sort_keys=True, default=str),
            json.dumps(resource, sort_keys=True, default=str),
            action,
            json.dumps(env, sort_keys=True, default=str),
        )
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[1]) < self._cache_ttl:
            return cached[0]

        if not self._policies:
            self.load_from_config()

        # PIP resolution: expand ${...} references in policy conditions
        resolved_policies = []
        for policy in self._policies:
            resolved = {}
            for key, val in policy.items():
                if key in ("subject", "resource", "environment") and isinstance(val, dict):
                    resolved[key] = {k: PIP.resolve(v, {**subject, **resource, **env}) for k, v in val.items()}
                else:
                    resolved[key] = val
            resolved_policies.append(resolved)

        # Evaluate policies — first match wins ( Permit overrides Deny in order )
        for policy in resolved_policies:
            if self._match_policy(policy, subject, resource, action, env):
                decision_str = policy.get("decision", "Permit")
                decision = Decision(
                    permit=decision_str.upper() == "PERMIT",
                    policy_name=policy.get("name", "unknown"),
                    reason=f"Matched policy '{policy.get('name', 'unknown')}'",
                )
                self._cache[cache_key] = (decision, now)
                return decision

        # Default deny
        decision = DENY
        self._cache[cache_key] = (decision, now)
        return decision

    def clear_cache(self) -> None:
        self._cache.clear()


# Singleton PDP instance
_pdp = PDP()


def evaluate(subject: dict, resource: dict, action: str, env: Optional[dict] = None) -> Decision:
    """Public API: evaluate ABAC policies."""
    return _pdp.evaluate(subject, resource, action, env)


def reload_policies() -> None:
    """Reload policies from disk and clear the PDP cache."""
    _pdp.load_from_config()
    _pdp.clear_cache()


# ---------------------------------------------------------------------------
# PEP — Policy Enforcement Point
# ---------------------------------------------------------------------------

def abac_protect(resource_attr_fn: Callable, action: str):
    """Flask route decorator: enforce ABAC before entering the handler.

    Args:
        resource_attr_fn: callable(request) -> dict of resource attributes
        action: HTTP method or action string (e.g. "GET", "DELETE")
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                from flask import g, request, jsonify, make_response
            except ImportError:
                return f(*args, **kwargs)

            ctx = getattr(g, "security_context", None)
            subject = _ctx_to_subject(ctx) if ctx else {}
            resource = resource_attr_fn(request)
            env = {"remote_addr": request.remote_addr or "", "timestamp": time.time()}

            decision = evaluate(subject, resource, action, env)
            if not decision.permit:
                logger.warning(
                    "ABAC deny: policy=%s user=%s action=%s resource=%s",
                    decision.policy_name,
                    subject.get("user_id"),
                    action,
                    resource.get("type", "?"),
                )
                if _wants_json():
                    return make_response(
                        jsonify(
                            {
                                "error": "Access denied by policy",
                                "code": "ABAC_DENIED",
                                "policy": decision.policy_name,
                                "reason": decision.reason,
                            }
                        ),
                        403,
                    )
                from flask import abort
                abort(403, description="Access denied by policy")
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _ctx_to_subject(ctx) -> dict:
    """Convert SecurityContext to ABAC subject dict."""
    return {
        "user_id": getattr(ctx, "user_id", ""),
        "role": getattr(ctx, "role", ""),
        "clearance_level": getattr(ctx, "clearance_level", 0),
        "compartments": list(getattr(ctx, "compartments", [])),
        "tenant_id": getattr(ctx, "tenant_id", None),
        "classification": getattr(ctx, "classification", "CUI"),
    }


def _wants_json() -> bool:
    try:
        from flask import request
        accept = request.headers.get("Accept", "")
        return "application/json" in accept or request.is_json
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_abac_decision(
    conn,
    decision: Decision,
    subject: dict,
    resource: dict,
    action: str,
) -> None:
    """Log a PDP decision to the append-only ``abac_decisions`` table."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO abac_decisions
            (policy_name, decision, subject, resource, action, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                decision.policy_name,
                "permit" if decision.permit else "deny",
                json.dumps(subject, default=str),
                json.dumps(resource, default=str),
                action,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log ABAC decision: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ABAC Engine CLI")
    parser.add_argument("--review", action="store_true", help="Review loaded policies")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.review:
        _pdp.load_from_config()
        policies = _pdp._policies
        print(json.dumps(policies, indent=2) if args.json else f"Loaded {len(policies)} policies")


if __name__ == "__main__":
    main()
