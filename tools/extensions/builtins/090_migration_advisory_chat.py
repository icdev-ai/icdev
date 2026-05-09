#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration advisory chat extension (Phase CAM).

Hooks into ``chat_message_after`` to surface migration advisories in /chat:
  - Active migration phase status
  - Failed refactor jobs
  - AI opportunities not yet started

Throttled to every 10 turns to avoid noise. Only fires when:
  - context.project_id matches a mc_projects record, OR
  - user_query contains migration-related keywords

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("icdev.extensions.migration_advisory_chat")

EXTENSION_HOOKS = {
    "chat_message_after": {
        "name": "migration_advisory_chat",
        "priority": 90,
        "allow_modification": True,
        "handler": "handle",
    }
}


def handle(context: dict) -> dict:
    """Inject migration advisory into chat_message_after context."""
    try:
        from tools.migration_canvas.migration_chat_advisor import get_migration_advisory
        advisory = get_migration_advisory(context)
        if advisory:
            context["migration_advisory"] = advisory
    except (ImportError, Exception) as exc:
        logger.debug("Migration advisory extension error: %s", exc)
    return context
