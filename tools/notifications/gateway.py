#!/usr/bin/env python3

# CUI // SP-CTI
"""ICDEV™ Notification Gateway — multi-platform alert delivery (Phase 72).

Routes events to configured adapters (Slack, Teams, Email, Webhook)
with rate limiting, PII sanitization, and audit logging.

Usage:
    python tools/notifications/gateway.py --health --json
    python tools/notifications/gateway.py --test slack --json
    python tools/notifications/gateway.py --send --event-type compliance_alert \
        --severity critical --title "STIG CAT1 found" --body "Details..." --json
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.notification_gateway")

from tools.db.storage import get_connection  # noqa: E402
from tools.audit.audit_logger import atomic_log_event


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "notification_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

ADAPTER_CLASSES = {}


def _register_adapters():
    """Lazy-load adapter classes."""
    global ADAPTER_CLASSES
    if ADAPTER_CLASSES:
        return
    try:
        from tools.notifications.adapters.slack import SlackAdapter

        ADAPTER_CLASSES["slack"] = SlackAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.teams import TeamsAdapter

        ADAPTER_CLASSES["teams"] = TeamsAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.email_adapter import EmailAdapter

        ADAPTER_CLASSES["email"] = EmailAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.webhook import WebhookAdapter

        ADAPTER_CLASSES["webhook"] = WebhookAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.mattermost import MattermostNotificationAdapter

        ADAPTER_CLASSES["mattermost"] = MattermostNotificationAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.teams_bot import TeamsBotNotificationAdapter

        ADAPTER_CLASSES["teams_bot"] = TeamsBotNotificationAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.github_notify import GitHubNotificationAdapter

        ADAPTER_CLASSES["github"] = GitHubNotificationAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.gitlab_notify import GitLabNotificationAdapter

        ADAPTER_CLASSES["gitlab"] = GitLabNotificationAdapter
    except ImportError:
        pass
    try:
        from tools.notifications.adapters.skype_notify import SkypeNotificationAdapter

        ADAPTER_CLASSES["skype"] = SkypeNotificationAdapter
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_rate_counters: Dict[str, List[float]] = defaultdict(list)


def _check_rate_limit(adapter_name: str, config: Dict) -> bool:
    """Check if adapter is within rate limit. Returns True if allowed."""
    rate_cfg = config.get("rate_limit", {})
    max_per_hour = rate_cfg.get("max_per_adapter_per_hour", 10)
    now = time.time()
    cutoff = now - 3600

    # Clean old entries
    _rate_counters[adapter_name] = [ts for ts in _rate_counters[adapter_name] if ts > cutoff]

    if len(_rate_counters[adapter_name]) >= max_per_hour:
        return False

    _rate_counters[adapter_name].append(now)
    return True


# ---------------------------------------------------------------------------
# PII sanitization
# ---------------------------------------------------------------------------


def _sanitize_body(body: str) -> str:
    """Sanitize notification body before external delivery using redaction detector."""
    try:
        from tools.redaction.detector import RedactionDetector

        detector = RedactionDetector()
        detections = detector.detect(body)
        if detections:
            for d in detections:
                entity_type = d.get("entity_type", "REDACTED")
                start = d.get("start", 0)
                end = d.get("end", 0)
                if start < end:
                    body = body[:start] + f"[{entity_type}]" + body[end:]
    except (ImportError, Exception):
        pass  # Graceful fallback — send unsanitized if detector unavailable
    return body


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class NotificationGateway:
    """Central notification dispatcher."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or _load_config()
        self.enabled = self.config.get("enabled", True)
        env_override = self.config.get("env_override", "ICDEV_NOTIFICATIONS_ENABLED")
        env_val = os.environ.get(env_override, "").lower()
        if env_val in ("false", "0", "no"):
            self.enabled = False
        elif env_val in ("true", "1", "yes"):
            self.enabled = True

        _register_adapters()
        self.adapters = {}
        adapter_configs = self.config.get("adapters", {})
        for name, acfg in adapter_configs.items():
            if name in ADAPTER_CLASSES:
                self.adapters[name] = ADAPTER_CLASSES[name](acfg)

        self.routing = self.config.get("routing", {})
        self.sanitize = self.config.get("sanitize_before_delivery", True)

    def send(
        self,
        event_type: str,
        severity: str = "info",
        title: str = "",
        body: str = "",
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Route an event to configured adapters.

        Returns dict with delivery results per adapter.
        """
        result: Dict[str, Any] = {
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "sent_at": _utcnow_iso(),
            "deliveries": {},
        }

        # Write PIR alerts to the secure log store (audit_trail) regardless
        # of whether external notification delivery is enabled.
        if event_type == "pir_alert":
            result["secure_log"] = self._secure_log(
                event_type, severity, title, metadata
            )

        if not self.enabled:
            result["skipped"] = "notifications_disabled"
            return result

        # Resolve target adapters from routing config
        target_adapters = self.routing.get(event_type, [])
        if not target_adapters:
            # Default: send to all enabled adapters
            target_adapters = [n for n, a in self.adapters.items() if a.enabled]

        # Sanitize body before external delivery
        safe_body = _sanitize_body(body) if self.sanitize else body

        for adapter_name in target_adapters:
            adapter = self.adapters.get(adapter_name)
            if not adapter or not adapter.enabled:
                result["deliveries"][adapter_name] = {"status": "disabled"}
                continue

            if not _check_rate_limit(adapter_name, self.config):
                result["deliveries"][adapter_name] = {"status": "rate_limited"}
                self._audit_log(event_type, adapter_name, severity, title, False, "rate_limited")
                continue

            try:
                success = adapter.send(title, safe_body, severity, metadata)
                result["deliveries"][adapter_name] = {
                    "status": "delivered" if success else "failed",
                }
                self._audit_log(event_type, adapter_name, severity, title, success)
            except Exception as exc:
                result["deliveries"][adapter_name] = {
                    "status": "error",
                    "error": str(exc),
                }
                self._audit_log(event_type, adapter_name, severity, title, False, str(exc))

        return result

    def health(self) -> Dict[str, Any]:
        """Check health of all configured adapters."""
        return {
            "enabled": self.enabled,
            "adapters": {name: adapter.health_check() for name, adapter in self.adapters.items()},
            "routing_rules": len(self.routing),
            "checked_at": _utcnow_iso(),
        }

    def _audit_log(
        self,
        event_type: str,
        adapter: str,
        severity: str,
        title: str,
        delivered: bool,
        error: str = "",
    ):
        """Log delivery attempt to notification_log (append-only)."""
        try:
            conn = get_connection()
            import hashlib

            log_id = f"nlog-{hashlib.sha256(f'{_utcnow_iso()}{adapter}{event_type}'.encode()).hexdigest()[:12]}"
            conn.execute(
                """
                INSERT INTO notification_log
                    (id, event_type, adapter, severity, title, delivered, error, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    log_id,
                    event_type,
                    adapter,
                    severity,
                    title[:200],
                    delivered,
                    error[:500],
                    _utcnow_iso(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # Best-effort — never block on audit
            logger.warning("_audit_log: best-effort INSERT into notification_log failed (non-blocking): %s", exc)

    def _secure_log(
        self,
        event_type: str,
        severity: str,
        title: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Write PIR alert to the secure audit trail (audit_trail) atomically.

        Satisfies NIST 800-53 AU-2/AU-9 by persisting alert generation
        events to the immutable append-only audit store.
        atomic_log_event wraps each write in BEGIN IMMEDIATE (SQLite)
        or an explicit transaction block, and guarantees that if the DB
        write fails the payload is captured to a dead-letter JSONL file
        so alerts are never silently discarded.
        """
        last_result = None
        for attempt in range(3):
            result = atomic_log_event(
                event_type="pir_alert_generated",
                actor="notification_gateway",
                action=title,
                details={
                    "severity": severity,
                    "event_type": event_type,
                    **(metadata or {}),
                },
                classification="CUI",
            )
            last_result = result
            if result.get("status") == "persisted":
                return result
            logger.warning(
                "Secure log write attempt %d/%d failed: %s",
                attempt + 1,
                3,
                result.get("error", "unknown"),
            )
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
        logger.error(
            "Secure log write FAILED after 3 attempts for event_type=%s title=%s: %s",
            event_type,
            title,
            last_result.get("error", "unknown"),
        )
        return last_result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Notification Gateway")
    parser.add_argument("--health", action="store_true", help="Check adapter health")
    parser.add_argument("--test", type=str, help="Send test notification to adapter")
    parser.add_argument("--send", action="store_true", help="Send a notification")
    parser.add_argument("--event-type", type=str, default="test")
    parser.add_argument("--severity", type=str, default="info")
    parser.add_argument("--title", type=str, default="Test Notification")
    parser.add_argument("--body", type=str, default="This is a test from ICDEV™ Notification Gateway.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    gw = NotificationGateway()

    if args.health:
        result = gw.health()
    elif args.test:
        result = gw.send(
            event_type="test",
            severity="info",
            title="ICDEV™ Test Notification",
            body=f"Test delivery to {args.test} adapter at {_utcnow_iso()}.",
        )
    elif args.send:
        result = gw.send(
            event_type=args.event_type,
            severity=args.severity,
            title=args.title,
            body=args.body,
        )
    else:
        result = gw.health()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
