#!/usr/bin/env python3
# CUI // SP-CTI
"""Device Trust — CrowdStrike Falcon device posture adapter (G-15).

Verifies device posture via CrowdStrike Falcon API (stubbed until credentials
are configured). Results are cached in-memory for 5 minutes (configurable).

Public API:
    verify_device_posture(device_fingerprint) -> DeviceTrustResult

NIST 800-53: IA-3, SA-9, ZTA Pillar 2
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.device_trust")

# ---------------------------------------------------------------------------
# DeviceTrustResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class DeviceTrustResult:
    trusted: bool
    device_id: str = ""
    health_score: float = 0.0
    last_seen_seconds_ago: int = 0
    sensor_version: str = ""
    reason: str = ""
    # Posture status: "healthy" / "unhealthy" from a real evaluation, or
    # "unknown" when the result came from the CrowdStrike stub (live API
    # unavailable). Consumers MUST treat "unknown" as DENY unless the
    # zero-trust stub gate is explicitly enabled (ICDEV_ZT_ALLOW_STUB).
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trusted": self.trusted,
            "device_id": self.device_id,
            "health_score": self.health_score,
            "last_seen_seconds_ago": self.last_seen_seconds_ago,
            "sensor_version": self.sensor_version,
            "reason": self.reason,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# In-memory cache: fingerprint -> (DeviceTrustResult, timestamp_monotonic)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[DeviceTrustResult, float]] = {}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").lower().strip()
    if not val:
        return default
    return val in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# CrowdStrike API call (stub with live path)
# ---------------------------------------------------------------------------


def _call_crowdstrike_api(device_fingerprint: str) -> dict:
    """Retrieve device posture from CrowdStrike Falcon API.

    If ICDEV_CROWDSTRIKE_BASE_URL / CLIENT_ID / CLIENT_SECRET are not
    configured, raises RuntimeError("CrowdStrike not configured").

    When credentials are present, attempts an HTTP call; if that also
    fails, the caller handles the exception.

    Returns a dict with keys:
        device_id (str), health_score (float 0-1),
        last_seen_seconds_ago (int), sensor_version (str)
    """
    base_url = os.environ.get("ICDEV_CROWDSTRIKE_BASE_URL", "").strip()
    client_id = os.environ.get("ICDEV_CROWDSTRIKE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ICDEV_CROWDSTRIKE_CLIENT_SECRET", "").strip()

    if not (base_url and client_id and client_secret):
        raise RuntimeError("CrowdStrike not configured")

    # --- Live HTTP path ---
    # Try the lightweight session helper first; fall back to urllib.
    url = f"{base_url.rstrip('/')}/devices/entities/devices/v1?filter=device_fingerprint%3A%27{device_fingerprint}%27"
    # CrowdStrike uses OAuth2 client-credentials; for a production adapter
    # you would exchange client_id/client_secret for a bearer token here.
    # This stub logs a warning and reports the device posture as UNKNOWN —
    # NOT healthy — so the decision point can fail closed (deny) unless the
    # zero-trust stub gate (ICDEV_ZT_ALLOW_STUB) is explicitly enabled for
    # dev/CI/e2e. Fabricating a healthy device here is a fail-open defect.
    logger.warning(
        "CrowdStrike API call is a stub — device posture is UNKNOWN (not healthy)",
        extra={"extra": {"device_fingerprint": device_fingerprint, "url": url}},
    )
    # Stub response: status "unknown", no fabricated health. The "_stub"
    # marker lets verify_device_posture() gate this behind stub_allowed().
    return {
        "device_id": f"sim-{device_fingerprint[:16]}",
        "health_score": 0.0,
        "last_seen_seconds_ago": 300,
        "sensor_version": "7.14.0",
        "status": "unknown",
        "_stub": True,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_device_posture(device_fingerprint: str) -> DeviceTrustResult:
    """Check whether the device identified by device_fingerprint is trusted.

    Trust criteria:
      - health_score >= 0.7
      - last_seen_seconds_ago < 86400 (24 hours)

    Caching:
      Results are cached for ICDEV_DEVICE_TRUST_CACHE_TTL_SECONDS (default 300).

    Strict mode (ICDEV_DEVICE_TRUST_REQUIRED=true):
      On any exception, returns untrusted. In non-strict mode, failures
      return trusted with an explanatory reason so that a misconfigured
      adapter does not block all users.
    """
    trust_required = _env_bool("ICDEV_DEVICE_TRUST_REQUIRED", False)

    if not trust_required:
        return DeviceTrustResult(trusted=True, reason="device trust not required")

    ttl = _env_int("ICDEV_DEVICE_TRUST_CACHE_TTL_SECONDS", 300)
    now = time.monotonic()

    # Cache lookup
    cached = _cache.get(device_fingerprint)
    if cached is not None:
        result, ts = cached
        if (now - ts) < ttl:
            logger.debug(
                "Device trust cache hit",
                extra={"extra": {"device_fingerprint": device_fingerprint, "trusted": result.trusted}},
            )
            return result

    try:
        posture = _call_crowdstrike_api(device_fingerprint)
    except RuntimeError as exc:
        # CrowdStrike not configured at all
        logger.warning(
            "CrowdStrike API unavailable",
            extra={"extra": {"error": str(exc), "strict": trust_required}},
        )
        if trust_required:
            result = DeviceTrustResult(
                trusted=False,
                reason="API error",
                device_id=device_fingerprint,
            )
        else:
            result = DeviceTrustResult(
                trusted=True,
                reason="device trust check failed (non-strict)",
                device_id=device_fingerprint,
            )
        _cache[device_fingerprint] = (result, now)
        return result
    except Exception as exc:
        logger.error(
            "Unexpected error calling CrowdStrike API",
            extra={"extra": {"error": str(exc), "strict": trust_required}},
        )
        if trust_required:
            result = DeviceTrustResult(
                trusted=False,
                reason="API error",
                device_id=device_fingerprint,
            )
        else:
            result = DeviceTrustResult(
                trusted=True,
                reason="device trust check failed (non-strict)",
                device_id=device_fingerprint,
            )
        _cache[device_fingerprint] = (result, now)
        return result

    # Stub / unknown posture: the live CrowdStrike API was unavailable and we
    # only have a simulated response. Fail closed (deny, status "unknown")
    # unless the zero-trust stub gate is explicitly enabled for dev/CI/e2e.
    if posture.get("_stub") or posture.get("status") == "unknown":
        from tools.security.stub_gate import stub_allowed

        device_id = posture.get("device_id", device_fingerprint)
        if stub_allowed():
            result = DeviceTrustResult(
                trusted=True,
                device_id=device_id,
                health_score=float(posture.get("health_score", 0.0)),
                last_seen_seconds_ago=int(posture.get("last_seen_seconds_ago", 0)),
                sensor_version=posture.get("sensor_version", ""),
                status="unknown",
                reason="device posture unknown (CrowdStrike stub) — permitted via ICDEV_ZT_ALLOW_STUB",
            )
        else:
            result = DeviceTrustResult(
                trusted=False,
                device_id=device_id,
                health_score=0.0,
                status="unknown",
                reason="device posture unknown (CrowdStrike stub unavailable) — fail closed; set ICDEV_ZT_ALLOW_STUB to permit in dev",
            )
        _cache[device_fingerprint] = (result, now)
        logger.info(
            "Device trust evaluated (stub)",
            extra={
                "extra": {
                    "device_fingerprint": device_fingerprint,
                    "device_id": device_id,
                    "trusted": result.trusted,
                    "status": "unknown",
                    "stub_allowed": result.trusted,
                }
            },
        )
        return result

    device_id: str = posture.get("device_id", "")
    health_score: float = float(posture.get("health_score", 0.0))
    last_seen: int = int(posture.get("last_seen_seconds_ago", 99999))
    sensor_version: str = posture.get("sensor_version", "")

    trusted = health_score >= 0.7 and last_seen < 86400

    if not trusted:
        reasons: list[str] = []
        if health_score < 0.7:
            reasons.append(f"health_score {health_score:.2f} < 0.7")
        if last_seen >= 86400:
            reasons.append(f"last_seen {last_seen}s >= 86400s (24h)")
        reason_str = "; ".join(reasons)
    else:
        reason_str = "posture checks passed"

    result = DeviceTrustResult(
        trusted=trusted,
        device_id=device_id,
        health_score=health_score,
        last_seen_seconds_ago=last_seen,
        sensor_version=sensor_version,
        reason=reason_str,
        status="healthy" if trusted else "unhealthy",
    )

    _cache[device_fingerprint] = (result, now)
    logger.info(
        "Device trust evaluated",
        extra={
            "extra": {
                "device_fingerprint": device_fingerprint,
                "device_id": device_id,
                "trusted": trusted,
                "health_score": health_score,
                "last_seen_seconds_ago": last_seen,
                "reason": reason_str,
            }
        },
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Device Trust — verify device posture via CrowdStrike Falcon")
    parser.add_argument("--fingerprint", required=True, help="Device fingerprint to check")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = verify_device_posture(args.fingerprint)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        status = "TRUSTED" if result.trusted else "UNTRUSTED"
        print(f"[{status}] {args.fingerprint}")
        print(f"  device_id:            {result.device_id}")
        print(f"  health_score:         {result.health_score:.2f}")
        print(f"  last_seen_seconds_ago:{result.last_seen_seconds_ago}")
        print(f"  sensor_version:       {result.sensor_version}")
        print(f"  reason:               {result.reason}")
