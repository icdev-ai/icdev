#!/usr/bin/env python3
# CUI // SP-CTI
"""PDP Client — Policy Decision Point integration (G-12).

Provides a unified access evaluation API backed by pluggable PDP adapters:
  - local:      delegates to tools.security.abac_engine.evaluate()
  - disa_icam:  stub for DISA ICAM integration
  - zscaler_zpa: stub for Zscaler ZPA integration

Public API:
    evaluate_access(user_ctx, resource, action) -> PDPDecision
    clear_cache() -> None

NIST 800-53: AC-3, AC-4, IA-3, ZTA Pillar 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.pdp_client")

# ---------------------------------------------------------------------------
# PDPDecision dataclass
# ---------------------------------------------------------------------------


@dataclass
class PDPDecision:
    permit: bool
    reason: str
    policy_name: str = ""
    adapter: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "permit": self.permit,
            "reason": self.reason,
            "policy_name": self.policy_name,
            "adapter": self.adapter,
        }


# ---------------------------------------------------------------------------
# Abstract base adapter
# ---------------------------------------------------------------------------


class PDPAdapter(ABC):
    """Abstract Policy Decision Point adapter."""

    @abstractmethod
    def evaluate(self, user_ctx: dict, resource: str, action: str) -> PDPDecision:
        """Evaluate access and return a PDPDecision."""
        ...


# ---------------------------------------------------------------------------
# LocalPDPAdapter — delegates to ABAC engine
# ---------------------------------------------------------------------------


class LocalPDPAdapter(PDPAdapter):
    """Delegates policy decisions to the local ABAC engine."""

    def evaluate(self, user_ctx: dict, resource: str, action: str) -> PDPDecision:
        try:
            from tools.security.abac_engine import evaluate  # type: ignore[import-untyped]
        except ImportError as exc:
            logger.warning(
                "ABAC engine not available; falling back to permit",
                extra={"extra": {"error": str(exc)}},
            )
            return PDPDecision(
                permit=True,
                reason="ABAC engine unavailable — fallback permit",
                adapter="local",
            )

        subject = {
            "user_id": user_ctx.get("user_id", ""),
            "role": user_ctx.get("role", ""),
            "clearance_level": user_ctx.get("clearance_level", ""),
            "tenant_id": user_ctx.get("tenant_id", ""),
            "classification": user_ctx.get("classification", ""),
        }
        resource_attrs: dict[str, Any] = {"resource_id": resource}
        env_attrs: dict[str, Any] = {}

        try:
            decision = evaluate(subject, resource_attrs, action, env_attrs)
            return PDPDecision(
                permit=bool(decision.permit),
                reason=decision.reason,
                policy_name=decision.policy_name,
                adapter="local",
            )
        except Exception as exc:
            logger.error(
                "ABAC evaluation error",
                extra={"extra": {"error": str(exc), "resource": resource, "action": action}},
            )
            raise


# ---------------------------------------------------------------------------
# DISA ICAM adapter stub
# ---------------------------------------------------------------------------


class DisaICAMAdapter(PDPAdapter):
    """Stub adapter for DISA ICAM integration.

    The live DISA ICAM PDP is not yet wired up, so this adapter is a stub.
    It fails **closed** (deny) by default — an unavailable PDP must not
    silently permit access. Dev/CI/e2e set ICDEV_ZT_ALLOW_STUB to opt into
    the permissive stub decision.
    """

    def evaluate(self, user_ctx: dict, resource: str, action: str) -> PDPDecision:
        logger.warning(
            "DISA ICAM adapter not configured",
            extra={"extra": {"resource": resource, "action": action}},
        )
        from tools.security.stub_gate import stub_allowed

        if stub_allowed():
            return PDPDecision(
                permit=True,
                reason="DISA ICAM adapter not configured — stub permit via ICDEV_ZT_ALLOW_STUB",
                adapter="disa_icam",
            )
        return PDPDecision(
            permit=False,
            reason="DISA ICAM adapter not configured — fail closed (set ICDEV_ZT_ALLOW_STUB to permit in dev)",
            adapter="disa_icam",
        )


# ---------------------------------------------------------------------------
# Zscaler ZPA adapter stub
# ---------------------------------------------------------------------------


class ZscalerZPAAdapter(PDPAdapter):
    """Stub adapter for Zscaler ZPA integration.

    The live Zscaler ZPA PDP is not yet wired up, so this adapter is a stub.
    It fails **closed** (deny) by default — an unavailable PDP must not
    silently permit access. Dev/CI/e2e set ICDEV_ZT_ALLOW_STUB to opt into
    the permissive stub decision.
    """

    def evaluate(self, user_ctx: dict, resource: str, action: str) -> PDPDecision:
        logger.warning(
            "Zscaler ZPA adapter not configured",
            extra={"extra": {"resource": resource, "action": action}},
        )
        from tools.security.stub_gate import stub_allowed

        if stub_allowed():
            return PDPDecision(
                permit=True,
                reason="Zscaler ZPA adapter not configured — stub permit via ICDEV_ZT_ALLOW_STUB",
                adapter="zscaler_zpa",
            )
        return PDPDecision(
            permit=False,
            reason="Zscaler ZPA adapter not configured — fail closed (set ICDEV_ZT_ALLOW_STUB to permit in dev)",
            adapter="zscaler_zpa",
        )


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------


def _build_pdp_adapter() -> PDPAdapter:
    """Build a PDP adapter from the ICDEV_PDP_ADAPTER env var.

    Supported values: local (default), disa_icam, zscaler_zpa.
    Falls back to LocalPDPAdapter for unrecognised values.
    """
    adapter_name = os.environ.get("ICDEV_PDP_ADAPTER", "local").lower().strip()
    if adapter_name == "disa_icam":
        return DisaICAMAdapter()
    if adapter_name == "zscaler_zpa":
        return ZscalerZPAAdapter()
    if adapter_name != "local":
        logger.warning(
            "Unknown ICDEV_PDP_ADAPTER value; defaulting to local",
            extra={"extra": {"value": adapter_name}},
        )
    return LocalPDPAdapter()


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[PDPDecision, float]] = {}


def _cache_key(user_ctx: dict, resource: str, action: str) -> str:
    payload = json.dumps({"user_ctx": user_ctx, "resource": resource, "action": action}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def clear_cache() -> None:
    """Evict all cached PDP decisions."""
    _cache.clear()
    logger.debug("PDP decision cache cleared")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_access(user_ctx: dict, resource: str, action: str) -> PDPDecision:
    """Evaluate whether user_ctx may perform action on resource.

    Uses a 60-second (configurable via ICDEV_PDP_CACHE_TTL) in-memory cache.
    If the underlying adapter raises and ICDEV_PDP_STRICT=true, returns deny.
    Otherwise returns permit to maintain availability.
    """
    ttl = float(os.environ.get("ICDEV_PDP_CACHE_TTL", "60"))
    strict = os.environ.get("ICDEV_PDP_STRICT", "false").lower() == "true"

    key = _cache_key(user_ctx, resource, action)
    now = time.monotonic()

    # Cache hit
    cached = _cache.get(key)
    if cached is not None:
        decision, ts = cached
        if (now - ts) < ttl:
            logger.debug(
                "PDP cache hit",
                extra={"extra": {"resource": resource, "action": action, "permit": decision.permit}},
            )
            return decision

    adapter = _build_pdp_adapter()
    try:
        decision = adapter.evaluate(user_ctx, resource, action)
    except Exception as exc:
        logger.error(
            "PDP adapter raised exception",
            extra={"extra": {"adapter": type(adapter).__name__, "error": str(exc)}},
        )
        if strict:
            return PDPDecision(
                permit=False,
                reason=f"PDP error in strict mode: {exc}",
                adapter=type(adapter).__name__.lower(),
            )
        return PDPDecision(
            permit=True,
            reason=f"PDP error (non-strict fallback): {exc}",
            adapter=type(adapter).__name__.lower(),
        )

    _cache[key] = (decision, now)
    logger.info(
        "PDP decision",
        extra={
            "extra": {
                "resource": resource,
                "action": action,
                "permit": decision.permit,
                "policy_name": decision.policy_name,
                "adapter": decision.adapter,
            }
        },
    )
    return decision


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(description="PDP Client — evaluate access decisions")
    parser.add_argument("--check", action="store_true", help="Evaluate an access decision")
    parser.add_argument("--user-id", default="", help="User ID")
    parser.add_argument("--resource", default="", help="Resource identifier")
    parser.add_argument("--action", default="read", help="Action (default: read)")
    parser.add_argument("--role", default="", help="User role")
    parser.add_argument("--clearance-level", default="", help="Clearance level")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--classification", default="", help="Classification level")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        return

    user_ctx = {
        "user_id": args.user_id,
        "role": args.role,
        "clearance_level": args.clearance_level,
        "tenant_id": args.tenant_id,
        "classification": args.classification,
    }
    decision = evaluate_access(user_ctx, args.resource, args.action)
    if args.as_json:
        print(json.dumps(decision.to_dict(), indent=2))
    else:
        status = "PERMIT" if decision.permit else "DENY"
        print(f"[{status}] {args.resource} / {args.action}")
        print(f"  reason:      {decision.reason}")
        print(f"  policy_name: {decision.policy_name}")
        print(f"  adapter:     {decision.adapter}")


if __name__ == "__main__":
    _cli()
