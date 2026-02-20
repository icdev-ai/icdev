#!/usr/bin/env python3
# CUI // SP-CTI
"""CommandEnvelope — Normalized command representation for all channels.

Every inbound message from any channel adapter is normalized into a
CommandEnvelope before entering the security chain.  This provides a
single, channel-agnostic data structure that the security chain,
command router, and audit logger can operate on.

Decision D133: Channel adapters are ABC + implementations.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class CommandEnvelope:
    """Channel-agnostic representation of an inbound command."""

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Source channel
    channel: str = ""              # telegram, slack, teams, mattermost, internal_chat
    channel_user_id: str = ""      # platform-specific user identifier
    channel_user_name: str = ""    # display name (for logging only, never used for auth)
    channel_message_id: str = ""   # platform message ID (for replies)
    channel_thread_id: str = ""    # thread/conversation ID if applicable

    # Command content
    raw_text: str = ""             # original message text
    command: str = ""              # parsed ICDEV command (e.g. "icdev-status")
    args: Dict[str, Any] = field(default_factory=dict)  # parsed arguments
    project_id: str = ""           # target project (if specified)

    # Security context (populated by security chain gates)
    binding_id: Optional[str] = None      # remote_user_bindings.id
    icdev_user_id: Optional[str] = None   # resolved ICDEV user
    tenant_id: Optional[str] = None       # resolved SaaS tenant
    user_role: Optional[str] = None       # RBAC role

    # Metadata
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    signature: str = ""            # webhook signature from channel
    is_bot: bool = False           # whether sender is a bot

    # Gate results (populated as envelope passes through security chain)
    gate_results: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for audit logging."""
        return {
            "id": self.id,
            "channel": self.channel,
            "channel_user_id": self.channel_user_id,
            "channel_user_name": self.channel_user_name,
            "raw_text": self.raw_text,
            "command": self.command,
            "args": self.args,
            "project_id": self.project_id,
            "binding_id": self.binding_id,
            "icdev_user_id": self.icdev_user_id,
            "tenant_id": self.tenant_id,
            "user_role": self.user_role,
            "timestamp": self.timestamp,
            "is_bot": self.is_bot,
            "gate_results": self.gate_results,
        }


def parse_command_text(text: str) -> tuple:
    """Parse raw message text into (command, args_dict).

    Supports formats:
        /icdev-status
        /icdev-status --project-id proj-123
        /icdev-test proj-123
        icdev-status proj-123
    """
    text = text.strip()

    # Strip leading slash
    if text.startswith("/"):
        text = text[1:]

    parts = text.split()
    if not parts:
        return ("", {})

    command = parts[0].lower()

    # Parse remaining as positional or --key value pairs
    args = {}
    i = 1
    positional_idx = 0
    while i < len(parts):
        token = parts[i]
        if token.startswith("--") and i + 1 < len(parts):
            key = token[2:].replace("-", "_")
            args[key] = parts[i + 1]
            i += 2
        else:
            # Positional argument — first positional is project_id by convention
            if positional_idx == 0:
                args["project_id"] = token
            else:
                args[f"arg_{positional_idx}"] = token
            positional_idx += 1
            i += 1

    return (command, args)
