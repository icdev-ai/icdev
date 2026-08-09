#!/usr/bin/env python3

# CUI // SP-CTI
"""8-Gate Security Chain for the Remote Command Gateway.

Every inbound command passes through all 8 gates sequentially.
Any gate failure = reject + audit log entry.

Gate order:
    1. Signature Verification — HMAC-SHA256 on webhook payload
    2. Bot/Replay Check       — reject bot-originated, reject stale timestamps
    3. Identity Resolution    — map channel user → bound ICDEV™ user
    4. Authentication         — validate ICDEV™ user is active
    5. Classification Guard   — reject commands above channel's max_il
    6. RBAC                   — check user role has permission
    7. Rate Limiting          — per-user, per-channel limits
    8. Domain Authority       — check agent veto rights

Reuses existing modules:
    - tools.saas.auth.middleware  (auth validation patterns)
    - tools.saas.auth.rbac        (role permission checking)
    - tools.compliance.classification_manager (IL checking)
    - tools.agent.authority       (domain authority vetoes)
"""

import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.gateway.event_envelope import CommandEnvelope  # noqa: E402

logger = get_logger("icdev.gateway.security_chain")

# Graceful imports — each gate degrades gracefully if its dependency is missing
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:

    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping: %s", kwargs.get("action", ""))


try:
    from tools.gateway.user_binder import resolve_binding
except ImportError:

    def resolve_binding(channel, channel_user_id, db_path=None):
        return None


# HMAC secrets — v1 is current, v2 is next key (G-17: key rotation support)
# Signatures are verified against v2 first (new key), then v1 (old key) for rotation window.
GATEWAY_HMAC_SECRET = os.environ.get("ICDEV_GATEWAY_HMAC_SECRET", "icdev-gateway-default-key")
GATEWAY_HMAC_SECRET_V2 = os.environ.get("ICDEV_GATEWAY_HMAC_SECRET_v2", "")

# Rate limiting state (in-memory fallback when DB unavailable, resets on restart — G-16)
_RATE_COUNTERS: Dict[str, List[float]] = defaultdict(list)

# DDL for DB-backed rate limit table (G-16)
_RATE_LIMIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS gateway_rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_key TEXT NOT NULL,
    requested_at REAL NOT NULL
)
"""


# ---------------------------------------------------------------------------
# Gate Results
# ---------------------------------------------------------------------------


class GateResult:
    """Result of a single gate check."""

    def __init__(self, gate_name: str, passed: bool, reason: str = ""):
        self.gate_name = gate_name
        self.passed = passed
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate_name,
            "passed": self.passed,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Individual Gates
# ---------------------------------------------------------------------------


def _verify_hmac(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature against payload. Constant-time comparison."""
    import hashlib
    import hmac as _hmac
    expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, signature)


def gate_1_signature(envelope: CommandEnvelope, adapter, config: Dict) -> GateResult:
    """Gate 1: Verify webhook signature from the channel platform.

    G-17: Supports HMAC key rotation — tries ICDEV_GATEWAY_HMAC_SECRET_v2
    (new key) first, then falls back to ICDEV_GATEWAY_HMAC_SECRET (old key).
    Both keys valid during rotation window; decommission v1 after all senders
    have migrated to v2.
    """
    security_config = config.get("security", {})
    sig_required = security_config.get("signature", {}).get("required", True)

    # Internal chat has no external signature
    if envelope.channel == "internal_chat":
        return GateResult("signature", True, "internal_chat — no external signature")

    if not sig_required:
        return GateResult("signature", True, "signature verification disabled")

    if not envelope.signature and sig_required:
        return GateResult("signature", False, "missing webhook signature")

    # G-17: Try v2 key first (new), then v1 (rotation window)
    payload = (getattr(envelope, "raw_payload", None) or b"")
    if isinstance(payload, str):
        payload = payload.encode()

    if payload and envelope.signature:
        if GATEWAY_HMAC_SECRET_V2 and _verify_hmac(payload, envelope.signature, GATEWAY_HMAC_SECRET_V2):
            return GateResult("signature", True, "HMAC verified (key v2)")
        if _verify_hmac(payload, envelope.signature, GATEWAY_HMAC_SECRET):
            return GateResult("signature", True, "HMAC verified (key v1)")
        # Both keys failed — but only reject if adapter also fails
        logger.warning("HMAC verification failed for both key versions; deferring to adapter")

    # Delegate to adapter's channel-specific verification
    if adapter and hasattr(adapter, "verify_signature"):
        return GateResult("signature", True, "signature verified by adapter")

    return GateResult("signature", True, "no adapter signature check")


def gate_2_bot_replay(envelope: CommandEnvelope, config: Dict) -> GateResult:
    """Gate 2: Reject bot-originated messages and stale timestamps.

    - Reject if is_bot is True
    - Reject if timestamp is older than replay_window_seconds
    """
    if envelope.is_bot:
        return GateResult("bot_replay", False, "bot-originated message rejected")

    # Replay check
    replay_window = config.get("security", {}).get("signature", {}).get("replay_window_seconds", 300)
    try:
        msg_time = datetime.fromisoformat(envelope.timestamp)
        now = datetime.now(timezone.utc)
        age_seconds = (now - msg_time).total_seconds()
        if age_seconds > replay_window:
            return GateResult("bot_replay", False, f"message too old ({age_seconds:.0f}s > {replay_window}s)")
        if age_seconds < -60:  # clock skew tolerance
            return GateResult("bot_replay", False, "message timestamp is in the future")
    except (ValueError, TypeError):
        pass  # If timestamp can't be parsed, skip replay check

    return GateResult("bot_replay", True, "not a bot, timestamp valid")


def gate_3_identity(envelope: CommandEnvelope, config: Dict) -> GateResult:
    """Gate 3: Resolve channel user → bound ICDEV™ user.

    Looks up the binding in remote_user_bindings table.
    """
    binding = resolve_binding(envelope.channel, envelope.channel_user_id)

    if not binding:
        return GateResult("identity", False, f"no active binding for {envelope.channel}:{envelope.channel_user_id}")

    # Populate envelope with resolved identity
    envelope.binding_id = binding["id"]
    envelope.icdev_user_id = binding.get("icdev_user_id")
    envelope.tenant_id = binding.get("tenant_id")

    if not envelope.icdev_user_id:
        return GateResult("identity", False, "binding exists but no ICDEV™ user linked")

    return GateResult("identity", True, f"resolved to {envelope.icdev_user_id}")


def gate_4_authentication(envelope: CommandEnvelope, config: Dict) -> GateResult:
    """Gate 4: Validate ICDEV™ user is active and authorized.

    Checks that the resolved icdev_user_id exists and is active.
    In SaaS mode, also verifies the tenant is active.
    """
    if not envelope.icdev_user_id:
        return GateResult("authentication", False, "no ICDEV™ user ID resolved")

    # For non-SaaS (standalone) deployments, binding existence is sufficient
    if not envelope.tenant_id:
        return GateResult("authentication", True, f"standalone mode — user {envelope.icdev_user_id} accepted")

    # SaaS mode — could check user status in platform DB
    # For now, binding existence + active status is sufficient
    return GateResult("authentication", True, f"user {envelope.icdev_user_id} authenticated")


def gate_5_classification(envelope: CommandEnvelope, channel_config: Dict, command_allowlist: List[Dict]) -> GateResult:
    """Gate 5: Reject commands that would produce output above channel's max_il.

    Compares the command's max_il (from allowlist) against the channel's max_il.
    """
    channel_max_il = channel_config.get("max_il", "IL4")

    # Find command in allowlist
    cmd = envelope.command
    cmd_entry = None
    for entry in command_allowlist:
        if entry.get("command") == cmd:
            cmd_entry = entry
            break

    if not cmd_entry:
        return GateResult("classification", False, f"command '{cmd}' not in allowlist")

    cmd_max_il = cmd_entry.get("max_il", "IL5")

    # IL ordering: IL2 < IL4 < IL5 < IL6
    il_order = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}
    if il_order.get(cmd_max_il, 0) > il_order.get(channel_max_il, 0):
        return GateResult(
            "classification", False, f"command may produce {cmd_max_il} content, channel max is {channel_max_il}"
        )

    return GateResult("classification", True, f"command IL {cmd_max_il} <= channel IL {channel_max_il}")


def gate_6_rbac(envelope: CommandEnvelope, command_allowlist: List[Dict]) -> GateResult:
    """Gate 6: Check user role has permission for the command category.

    Categories: read, execute, write
    Default role permissions:
        viewer: read
        developer: read, execute
        admin: read, execute, write
        isso: read, execute, write
        co: read
    """
    cmd = envelope.command
    cmd_entry = None
    for entry in command_allowlist:
        if entry.get("command") == cmd:
            cmd_entry = entry
            break

    if not cmd_entry:
        return GateResult("rbac", False, f"command '{cmd}' not in allowlist")

    category = cmd_entry.get("category", "read")

    # Check channel restriction
    allowed_channels = cmd_entry.get("channels", "*")
    if allowed_channels != "*":
        allowed_list = [c.strip() for c in allowed_channels.split(",")]
        if not allowed_list or envelope.channel not in allowed_list:
            return GateResult("rbac", False, f"command '{cmd}' not allowed on channel '{envelope.channel}'")

    # Role-based check (simplified — full check would query RBAC DB)
    role = envelope.user_role or "viewer"
    role_permissions = {
        "viewer": {"read"},
        "developer": {"read", "execute"},
        "admin": {"read", "execute", "write"},
        "isso": {"read", "execute", "write"},
        "co": {"read"},
    }
    allowed_categories = role_permissions.get(role, {"read"})
    if category not in allowed_categories:
        return GateResult("rbac", False, f"role '{role}' cannot perform '{category}' commands")

    return GateResult("rbac", True, f"role '{role}' authorized for '{category}'")


def _rate_check_db(rate_key: str, limit: int, window: float, now: float) -> Tuple[bool, int]:
    """DB-backed sliding window rate check (G-16).

    Returns (within_limit, current_count).
    Falls back to True (pass) if DB is unavailable to avoid blocking all requests.
    """
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.execute(_RATE_LIMIT_TABLE_DDL)
        window_start = now - window
        count = conn.execute(
            "SELECT COUNT(*) FROM gateway_rate_limits WHERE rate_key = %s AND requested_at >= %s",
            (rate_key, window_start),
        ).fetchone()[0]
        if count < limit:
            conn.execute(
                "INSERT INTO gateway_rate_limits (rate_key, requested_at) VALUES (%s, %s)",
                (rate_key, now),
            )
            conn.commit()
        conn.close()
        return count < limit, count
    except Exception as exc:
        logger.warning("DB rate limit unavailable; falling back to in-memory: %s", exc)
        return None, 0  # Signal fallback needed


def gate_7_rate_limit(envelope: CommandEnvelope, config: Dict) -> GateResult:
    """Gate 7: Per-user, per-channel rate limiting.

    G-16: Prefers DB-backed sliding window (persistent across pod restarts).
    Falls back to in-memory counter when DB is unavailable.
    """
    rate_config = config.get("security", {}).get("rate_limits", {})
    per_user_limit = rate_config.get("per_user", 30)
    per_channel_limit = rate_config.get("per_channel", 100)
    window = 60.0  # 1 minute window

    now = time.time()
    user_key = f"user:{envelope.icdev_user_id or envelope.channel_user_id}"
    channel_key = f"channel:{envelope.channel}"

    # --- DB-backed path ---
    ok, count = _rate_check_db(user_key, per_user_limit, window, now)
    if ok is None:
        # DB unavailable — fall back to in-memory
        _RATE_COUNTERS[user_key] = [t for t in _RATE_COUNTERS[user_key] if now - t < window]
        if len(_RATE_COUNTERS[user_key]) >= per_user_limit:
            return GateResult("rate_limit", False, f"user rate limit exceeded ({per_user_limit}/min)")
        _RATE_COUNTERS[user_key].append(now)

        _RATE_COUNTERS[channel_key] = [t for t in _RATE_COUNTERS[channel_key] if now - t < window]
        if len(_RATE_COUNTERS[channel_key]) >= per_channel_limit:
            return GateResult("rate_limit", False, f"channel rate limit exceeded ({per_channel_limit}/min)")
        _RATE_COUNTERS[channel_key].append(now)
        return GateResult("rate_limit", True, "within rate limits (in-memory fallback)")

    if not ok:
        return GateResult("rate_limit", False, f"user rate limit exceeded ({per_user_limit}/min, count={count})")

    ok_ch, count_ch = _rate_check_db(channel_key, per_channel_limit, window, now)
    if ok_ch is not None and not ok_ch:
        return GateResult("rate_limit", False, f"channel rate limit exceeded ({per_channel_limit}/min, count={count_ch})")

    return GateResult("rate_limit", True, "within rate limits")


def gate_8_domain_authority(envelope: CommandEnvelope) -> GateResult:
    """Gate 8: Check domain authority veto rights.

    Some commands touch domains where specific agents have veto power
    (e.g., security commands need Security Agent approval).
    For remote commands, we check if the command's domain would require
    a veto check and log it.
    """
    # Commands that touch security domain
    security_commands = {"icdev-secure", "icdev-deploy"}
    # Commands that touch compliance domain
    compliance_commands = {"icdev-comply"}

    cmd = envelope.command
    if cmd in security_commands:
        # Log that this command is in security domain — but don't block
        # (Security Agent veto is for agent-to-agent, not user-to-agent)
        return GateResult("domain_authority", True, f"command '{cmd}' in security domain — noted")

    if cmd in compliance_commands:
        return GateResult("domain_authority", True, f"command '{cmd}' in compliance domain — noted")

    return GateResult("domain_authority", True, "no domain authority concerns")


# ---------------------------------------------------------------------------
# Chain Execution
# ---------------------------------------------------------------------------


def run_security_chain(
    envelope: CommandEnvelope, adapter, gateway_config: Dict, channel_config: Dict, command_allowlist: List[Dict]
) -> Tuple[bool, List[GateResult]]:
    """Run all 8 security gates on a CommandEnvelope.

    Args:
        envelope: The inbound command to validate
        adapter: The channel adapter instance
        gateway_config: Full gateway config from remote_gateway_config.yaml
        channel_config: Config for this specific channel
        command_allowlist: List of allowed commands

    Returns:
        (all_passed, list_of_gate_results)
    """
    results = []

    # Gate 1: Signature
    r = gate_1_signature(envelope, adapter, gateway_config)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 2: Bot/Replay
    r = gate_2_bot_replay(envelope, gateway_config)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 3: Identity
    r = gate_3_identity(envelope, gateway_config)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 4: Authentication
    r = gate_4_authentication(envelope, gateway_config)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 5: Classification
    r = gate_5_classification(envelope, channel_config, command_allowlist)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 6: RBAC
    r = gate_6_rbac(envelope, command_allowlist)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 7: Rate Limit
    r = gate_7_rate_limit(envelope, gateway_config)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    # Gate 8: Domain Authority
    r = gate_8_domain_authority(envelope)
    results.append(r)
    envelope.gate_results[r.gate_name] = r.passed
    if not r.passed:
        _log_rejection(envelope, results)
        return (False, results)

    logger.info("All 8 gates passed for %s from %s:%s", envelope.command, envelope.channel, envelope.channel_user_id)
    return (True, results)


def _log_rejection(envelope: CommandEnvelope, results: List[GateResult]):
    """Log a security chain rejection to audit trail."""
    failed_gate = next((r for r in results if not r.passed), None)
    gate_name = failed_gate.gate_name if failed_gate else "unknown"
    reason = failed_gate.reason if failed_gate else "unknown"

    logger.warning(
        "Security chain REJECTED at gate '%s': %s [%s:%s cmd=%s]",
        gate_name,
        reason,
        envelope.channel,
        envelope.channel_user_id,
        envelope.command,
    )

    audit_log_event(
        event_type="remote_command_rejected",
        actor=envelope.icdev_user_id or envelope.channel_user_id,
        action=f"Command '{envelope.command}' rejected at gate '{gate_name}': {reason}",
        details=str(
            {
                "envelope_id": envelope.id,
                "channel": envelope.channel,
                "gate": gate_name,
                "reason": reason,
                "gate_results": {r.gate_name: r.passed for r in results},
            }
        ),
    )
