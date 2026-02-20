#!/usr/bin/env python3
# CUI // SP-CTI
"""Response Filter — IL-aware response redaction for the Remote Command Gateway.

Before sending any response back to a messaging channel:
1. Check response content against classification markings
2. Compare response IL against channel's max_il
3. If response IL > channel max_il → redact, return dashboard link
4. Log whether filtering was applied (audit trail)

Decision D135: Response filter strips content above channel max_il, never upgrades.
"""

import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.gateway.response_filter")

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping: %s", kwargs.get("action", ""))

# IL ordering: higher number = more sensitive
IL_ORDER = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}

# Classification markers that indicate content sensitivity
_CLASSIFICATION_PATTERNS = [
    (re.compile(r"SECRET\s*//", re.IGNORECASE), "IL6"),
    (re.compile(r"TOP\s+SECRET", re.IGNORECASE), "IL6"),
    (re.compile(r"CUI\s*//\s*SP-CTI", re.IGNORECASE), "IL5"),
    (re.compile(r"CUI\s*//", re.IGNORECASE), "IL4"),
    (re.compile(r"\bCUI\b", re.IGNORECASE), "IL4"),
]

# Default redaction message template
DEFAULT_REDACTION_MSG = (
    "[REDACTED] Response contains {classification} content that cannot be "
    "displayed on this channel (max: {channel_il}). "
    "View full response in the ICDEV dashboard."
)


def detect_response_il(response_text: str) -> str:
    """Detect the classification level of response content.

    Scans for classification markings and returns the highest IL detected.

    Args:
        response_text: The tool output text to scan.

    Returns:
        Detected IL level string (e.g., "IL2", "IL4", "IL5", "IL6")
    """
    highest_il = "IL2"  # default — public/unclassified

    for pattern, il_level in _CLASSIFICATION_PATTERNS:
        if pattern.search(response_text):
            if IL_ORDER.get(il_level, 0) > IL_ORDER.get(highest_il, 0):
                highest_il = il_level

    return highest_il


def filter_response(response_text: str, channel_max_il: str,
                    envelope_id: str = "",
                    dashboard_url: str = "",
                    redaction_template: str = "") -> Tuple[str, bool, str]:
    """Filter a response based on channel classification limits.

    Args:
        response_text: Raw tool output text.
        channel_max_il: Maximum IL the channel can display.
        envelope_id: Command envelope ID for audit reference.
        dashboard_url: URL to include in redaction message.
        redaction_template: Custom redaction message template.

    Returns:
        (filtered_text, was_filtered, detected_il)
    """
    detected_il = detect_response_il(response_text)

    # Check if response exceeds channel limit
    if IL_ORDER.get(detected_il, 0) > IL_ORDER.get(channel_max_il, 0):
        # Redact
        classification = _il_to_classification(detected_il)
        template = redaction_template or DEFAULT_REDACTION_MSG
        redacted = template.format(
            classification=classification,
            channel_il=channel_max_il,
            dashboard_url=dashboard_url or "/dashboard",
        )

        logger.warning(
            "Response FILTERED: detected %s (%s) > channel max %s [envelope=%s]",
            detected_il, classification, channel_max_il, envelope_id
        )

        try:
            audit_log_event(
                event_type="remote_response_filtered",
                actor="gateway",
                action=f"Response redacted: {detected_il} content on {channel_max_il} channel",
                details=str({
                    "envelope_id": envelope_id,
                    "detected_il": detected_il,
                    "channel_max_il": channel_max_il,
                    "response_length": len(response_text),
                }),
            )
        except Exception:
            pass  # Audit logging is best-effort

        return (redacted, True, detected_il)

    # No filtering needed
    return (response_text, False, detected_il)


def truncate_response(text: str, max_length: int = 4000) -> str:
    """Truncate response to fit channel message limits.

    Most messaging platforms have character limits (Telegram: 4096,
    Slack: 40000, Teams: 28000). We use a conservative default.

    Args:
        text: Response text to truncate.
        max_length: Maximum allowed characters.

    Returns:
        Truncated text with indicator if cut.
    """
    if len(text) <= max_length:
        return text

    # Cut and add truncation notice
    cut_point = max_length - 80  # leave room for notice
    return text[:cut_point] + "\n\n... [truncated — view full response in dashboard]"


def format_response(response_text: str, command: str,
                    execution_time_ms: int = 0,
                    audit_id: str = "",
                    include_timing: bool = True,
                    include_audit_id: bool = True) -> str:
    """Format a tool response for channel display.

    Adds metadata footer with timing and audit reference.

    Args:
        response_text: The tool output.
        command: The command that produced this output.
        execution_time_ms: How long execution took.
        audit_id: Audit trail entry ID.
        include_timing: Whether to show execution time.
        include_audit_id: Whether to show audit reference.

    Returns:
        Formatted response string.
    """
    parts = [response_text]

    footer_parts = []
    if include_timing and execution_time_ms > 0:
        if execution_time_ms < 1000:
            footer_parts.append(f"{execution_time_ms}ms")
        else:
            footer_parts.append(f"{execution_time_ms / 1000:.1f}s")

    if include_audit_id and audit_id:
        footer_parts.append(f"audit:{audit_id[:8]}")

    if footer_parts:
        parts.append(f"_[{' | '.join(footer_parts)}]_")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _il_to_classification(il: str) -> str:
    """Map IL level to classification name."""
    mapping = {
        "IL2": "PUBLIC",
        "IL4": "CUI",
        "IL5": "CUI",
        "IL6": "SECRET",
    }
    return mapping.get(il, "UNKNOWN")
