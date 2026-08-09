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
"""XACML-style ABAC Engine for ICDEV™.

Components:
- PIP (Policy Information Point): attribute lookup
- PDP (Policy Decision Point): decision evaluation with 60-second LRU cache
- PEP (Policy Enforcement Point): decorator for Flask routes
- CrossDomainABACEnforcer: ABAC + mandatory classification markings for cross-domain data pulls (MCIP)

Policies are loaded from ``args/security_config.yaml`` under the
``abac_policies`` key.

Supported operators:
    >=, <, equals, contains_any, in, not_in

Public API:
    evaluate(subject_attrs, resource_attrs, action, env_attrs) -> Decision
    abac_protect(resource_attr_fn, action) -> decorator
    enforce_cross_domain_pull(subject, resource, data_objects) -> CrossDomainPullResult

NIST 800-53 controls: AC-3, AC-4, AC-4(4), AU-2, SC-7(5).
"""

import copy
import functools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = get_logger("security.abac")

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

        # PIP resolution: expand ${...} references in policy conditions.
        # References are resolved against a NESTED context so "${subject.user_id}"
        # walks ctx["subject"]["user_id"]. A reference that resolves to None must
        # NOT collapse to a match-all condition (prop-sec-01): substitute a
        # sentinel that can never equal a real attribute value, so the policy
        # simply fails to match and evaluation falls through (default deny).
        ref_ctx = {"subject": subject, "resource": resource, "environment": env}
        resolved_policies = []
        for policy in self._policies:
            resolved = {}
            for key, val in policy.items():
                if key in ("subject", "resource", "environment") and isinstance(val, dict):
                    section = {}
                    for k, v in val.items():
                        is_ref = isinstance(v, str) and v.startswith("${") and v.endswith("}")
                        rv = PIP.resolve(v, ref_ctx)
                        if is_ref and rv is None:
                            rv = "__UNRESOLVED_ABAC_REF__"
                        section[k] = rv
                    resolved[key] = section
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
            VALUES (%s, %s, %s, %s, %s, %s)
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
# Cross-domain data pull: ABAC + mandatory classification markings (MCIP)
# NIST 800-53: AC-3, AC-4, AC-4(4), AU-2, SC-7(5)
# ---------------------------------------------------------------------------

@dataclass
class CrossDomainPullResult:
    """Result of an ABAC-enforced cross-domain data pull.

    Attributes:
        permitted: True if the pull was approved by ABAC; False otherwise.
        policy_name: Name of the matching ABAC policy (or "default_deny").
        reason: Human-readable explanation of the decision.
        data_objects: List of data objects with mandatory classification markings applied.
                      Always empty when permitted is False — no data leak on deny.
    """

    permitted: bool
    policy_name: str = ""
    reason: str = ""
    data_objects: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "permitted": self.permitted,
            "policy_name": self.policy_name,
            "reason": self.reason,
            "data_objects": self.data_objects,
        }


class CrossDomainABACEnforcer:
    """ABAC enforcement for cross-domain data pulls with mandatory classification markings.

    Decision logic (all conditions must be satisfied for PERMIT):
    1. Subject must hold the ``cross_domain_pull`` entitlement.
    2. Subject's clearance_level must be >= the resource classification's clearance order.
    3. All resource compartments must be a subset of the subject's compartments.

    On PERMIT, applies mandatory classification markings to each returned data object
    (copies the dict and sets ``classification_marking`` = resource classification string).
    On DENY, returns an empty data_objects list to prevent information leakage.
    """

    # Clearance ordering for numeric comparison
    _CLEARANCE_ORDER: Dict[str, int] = {
        "PUBLIC": 0,
        "CUI": 1,
        "SECRET": 2,
        "TOP SECRET": 3,
        "TOP SECRET//SCI": 4,
    }

    def _clearance_order(self, classification: str) -> int:
        """Return numeric clearance order for a classification string."""
        try:
            from tools.compliance.classification_manager import get_clearance_order
            return get_clearance_order(classification)
        except Exception:
            # Normalize: strip leading/trailing spaces, take the base level token
            normalized = classification.strip().upper()
            # Match longest prefix first
            for key in ("TOP SECRET//SCI", "TOP SECRET", "SECRET", "CUI", "PUBLIC"):
                if normalized.startswith(key):
                    return self._CLEARANCE_ORDER[key]
            return self._CLEARANCE_ORDER.get(normalized, 1)

    def _check_entitlement(self, subject: Dict[str, Any]) -> bool:
        """Subject must have the cross_domain_pull entitlement."""
        entitlements = subject.get("entitlements", [])
        return "cross_domain_pull" in entitlements

    def _check_clearance(self, subject: Dict[str, Any], resource: Dict[str, Any]) -> bool:
        """Subject clearance_level must meet or exceed the resource classification order."""
        resource_cls = resource.get("classification", "CUI")
        required_order = self._clearance_order(resource_cls)
        subject_clearance = subject.get("clearance_level", 0)
        return int(subject_clearance) >= required_order

    def _check_compartments(self, subject: Dict[str, Any], resource: Dict[str, Any]) -> bool:
        """All resource compartments must be a subset of the subject's compartments."""
        resource_compartments = set(resource.get("compartments", []))
        if not resource_compartments:
            return True
        subject_compartments = set(subject.get("compartments", []))
        return resource_compartments.issubset(subject_compartments)

    def apply_classification_markings(
        self,
        data_objects: List[Dict[str, Any]],
        source_classification: str,
    ) -> List[Dict[str, Any]]:
        """Apply mandatory classification markings to each data object.

        Returns a new list of dicts with all original fields preserved and
        ``classification_marking`` set to ``source_classification``.
        """
        marked: List[Dict[str, Any]] = []
        for obj in data_objects:
            marked_obj = copy.copy(obj)
            marked_obj["classification_marking"] = source_classification
            marked.append(marked_obj)
        return marked

    def enforce(
        self,
        subject: Dict[str, Any],
        resource: Dict[str, Any],
        data_objects: List[Dict[str, Any]],
        action: str = "cross_domain_pull",
    ) -> CrossDomainPullResult:
        """Enforce ABAC on a cross-domain data pull request.

        Evaluates three conditions in order:
        1. Entitlement check — subject must hold ``cross_domain_pull``.
        2. Clearance check — subject clearance_level >= resource classification order.
        3. Compartment check — resource compartments ⊆ subject compartments.

        On PERMIT, applies classification markings to all data objects.
        On DENY, returns an empty data_objects list (no data leak).

        Args:
            subject: Dict of subject attributes (user_id, clearance_level, entitlements, compartments, …).
            resource: Dict of resource attributes (classification, compartments, source_il, …).
            data_objects: Raw data objects to transfer (will receive classification_marking on permit).
            action: Action string (default "cross_domain_pull").

        Returns:
            CrossDomainPullResult with permit decision, policy name, reason, and marked data objects.
        """
        # 1. Entitlement check
        if not self._check_entitlement(subject):
            logger.warning(
                "ABAC cross-domain deny: user=%s missing cross_domain_pull entitlement",
                subject.get("user_id"),
            )
            return CrossDomainPullResult(
                permitted=False,
                policy_name="deny_missing_entitlement",
                reason="Subject lacks 'cross_domain_pull' entitlement",
                data_objects=[],
            )

        # 2. Clearance check
        if not self._check_clearance(subject, resource):
            resource_cls = resource.get("classification", "CUI")
            logger.warning(
                "ABAC cross-domain deny: user=%s clearance=%s insufficient for classification=%s",
                subject.get("user_id"),
                subject.get("clearance_level"),
                resource_cls,
            )
            return CrossDomainPullResult(
                permitted=False,
                policy_name="deny_insufficient_clearance",
                reason=f"Subject clearance_level {subject.get('clearance_level')} insufficient for '{resource_cls}'",
                data_objects=[],
            )

        # 3. Compartment check
        if not self._check_compartments(subject, resource):
            logger.warning(
                "ABAC cross-domain deny: user=%s missing required compartments for resource compartments=%s",
                subject.get("user_id"),
                resource.get("compartments"),
            )
            return CrossDomainPullResult(
                permitted=False,
                policy_name="deny_missing_compartment",
                reason=f"Subject missing required compartments: {resource.get('compartments')}",
                data_objects=[],
            )

        # All checks passed — PERMIT and apply mandatory classification markings
        resource_cls = resource.get("classification", "CUI")
        marked_objects = self.apply_classification_markings(data_objects, resource_cls)

        logger.info(
            "ABAC cross-domain permit: user=%s action=%s resource_classification=%s objects=%d",
            subject.get("user_id"),
            action,
            resource_cls,
            len(marked_objects),
        )
        return CrossDomainPullResult(
            permitted=True,
            policy_name="permit_cross_domain_pull",
            reason=f"ABAC permit for cross-domain pull; classification_marking='{resource_cls}' applied",
            data_objects=marked_objects,
        )


def enforce_cross_domain_pull(
    subject: Dict[str, Any],
    resource: Dict[str, Any],
    data_objects: List[Dict[str, Any]],
) -> "CrossDomainPullResult":
    """Public API: enforce ABAC on a cross-domain data pull and apply mandatory classification markings.

    Args:
        subject: Subject attribute dict (user_id, clearance_level, entitlements, compartments).
        resource: Resource attribute dict (classification, compartments, source_il, target_il).
        data_objects: List of raw data objects to transfer.

    Returns:
        CrossDomainPullResult — permitted with marked objects, or denied with empty data_objects.

    NIST 800-53: AC-3, AC-4, AC-4(4), AU-2, SC-7(5).
    """
    enforcer = CrossDomainABACEnforcer()
    return enforcer.enforce(subject=subject, resource=resource, data_objects=data_objects)


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
