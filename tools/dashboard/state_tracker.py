#!/usr/bin/env python3
# CUI // SP-CTI
"""Dirty-tracking state push for ICDEV dashboard (Phase 44 — D268-D270).

Per-client dirty/pushed version counters with debounced coalescing.
Adapted from Agent Zero's StateMonitor pattern.

Clients send ?since_version=N, server returns only changes since that version.
SSE debounced at 25ms, HTTP polling preserved at 3s.
"""

import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from tools.dashboard.sse_manager import sse_manager
except ImportError:
    sse_manager = None


class ClientState:
    """Tracks what a specific client has seen."""

    __slots__ = ("client_id", "viewing_context", "pushed_version", "last_seen_at", "transport")

    def __init__(self, client_id: str, transport: str = "poll"):
        self.client_id = client_id
        self.viewing_context: Optional[str] = None
        self.pushed_version: int = 0
        self.last_seen_at: float = time.time()
        self.transport = transport  # poll or sse


class StateTracker:
    """Track per-context dirty versions and per-client pushed versions.

    Every context change (new message, status change, intervention) increments
    the context's dirty_version. Clients request updates since their last
    pushed_version and receive only incremental changes.
    """

    def __init__(self, debounce_ms: int = 25, max_changes_buffer: int = 200):
        self._clients: Dict[str, ClientState] = {}
        self._context_versions: Dict[str, int] = defaultdict(int)
        self._context_changes: Dict[str, List[dict]] = defaultdict(list)
        self._lock = threading.Lock()
        self._debounce_ms = debounce_ms
        self._max_changes_buffer = max_changes_buffer
        self._pending_timers: Dict[str, threading.Timer] = {}

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def register_client(self, client_id: str, transport: str = "poll") -> ClientState:
        """Register a new client connection."""
        with self._lock:
            client = ClientState(client_id, transport)
            self._clients[client_id] = client
            return client

    def unregister_client(self, client_id: str) -> None:
        """Remove a client connection."""
        with self._lock:
            self._clients.pop(client_id, None)

    def set_viewing_context(self, client_id: str, context_id: str) -> None:
        """Set which chat context a client is currently viewing."""
        with self._lock:
            client = self._clients.get(client_id)
            if client:
                client.viewing_context = context_id
                client.last_seen_at = time.time()

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def mark_dirty(
        self,
        context_id: str,
        change_type: str,
        data: Optional[dict] = None,
    ) -> int:
        """Mark a context as changed. Returns the new dirty version.

        Args:
            context_id: The chat context that changed.
            change_type: Type of change (new_message, status_change, intervention, etc.)
            data: Optional change payload.

        Returns:
            New dirty_version for the context.
        """
        with self._lock:
            self._context_versions[context_id] += 1
            version = self._context_versions[context_id]

            change = {
                "version": version,
                "type": change_type,
                "data": data or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            changes = self._context_changes[context_id]
            changes.append(change)

            # Trim old changes to prevent unbounded growth
            if len(changes) > self._max_changes_buffer:
                self._context_changes[context_id] = changes[-self._max_changes_buffer:]

        # Schedule debounced push for SSE clients
        self._schedule_push(context_id)
        return version

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_version(self, context_id: str) -> int:
        """Get current dirty version for a context."""
        with self._lock:
            return self._context_versions.get(context_id, 0)

    def get_updates(
        self,
        client_id: str,
        context_id: str,
        since_version: int = 0,
    ) -> dict:
        """Get incremental updates since a version.

        Returns:
            {context_id, dirty_version, since_version, changes: [...], up_to_date: bool}
        """
        with self._lock:
            current_version = self._context_versions.get(context_id, 0)

            if since_version >= current_version:
                return {
                    "context_id": context_id,
                    "dirty_version": current_version,
                    "since_version": since_version,
                    "changes": [],
                    "up_to_date": True,
                }

            changes = self._context_changes.get(context_id, [])
            new_changes = [c for c in changes if c["version"] > since_version]

            # Update client tracking
            client = self._clients.get(client_id)
            if client:
                client.pushed_version = current_version
                client.last_seen_at = time.time()

            return {
                "context_id": context_id,
                "dirty_version": current_version,
                "since_version": since_version,
                "changes": new_changes,
                "up_to_date": False,
            }

    def acknowledge(self, client_id: str, version: int) -> None:
        """Acknowledge that client has processed up to a version."""
        with self._lock:
            client = self._clients.get(client_id)
            if client:
                client.pushed_version = version
                client.last_seen_at = time.time()

    # ------------------------------------------------------------------
    # SSE push (debounced)
    # ------------------------------------------------------------------

    def _schedule_push(self, context_id: str) -> None:
        """Schedule a debounced push to SSE clients viewing this context."""
        if sse_manager is None:
            return

        timer_key = f"push_{context_id}"
        with self._lock:
            # Cancel any pending timer for this context
            existing = self._pending_timers.pop(timer_key, None)
            if existing:
                existing.cancel()

            # Schedule new push after debounce period
            timer = threading.Timer(
                self._debounce_ms / 1000.0,
                self._do_push,
                args=(context_id,),
            )
            timer.daemon = True
            self._pending_timers[timer_key] = timer
            timer.start()

    def _do_push(self, context_id: str) -> None:
        """Execute the debounced push to SSE clients."""
        if sse_manager is None:
            return

        with self._lock:
            self._pending_timers.pop(f"push_{context_id}", None)
            current_version = self._context_versions.get(context_id, 0)
            # Get clients viewing this context
            target_clients = [
                c for c in self._clients.values()
                if c.viewing_context == context_id
                and c.transport == "sse"
                and c.pushed_version < current_version
            ]
            if not target_clients:
                return

            changes = self._context_changes.get(context_id, [])
            min_version = min(c.pushed_version for c in target_clients)
            new_changes = [c for c in changes if c["version"] > min_version]

            # Update pushed versions
            for client in target_clients:
                client.pushed_version = current_version

        # Broadcast outside the lock
        if new_changes:
            sse_manager.broadcast(
                {
                    "type": "chat_update",
                    "context_id": context_id,
                    "version": current_version,
                    "changes": new_changes,
                },
                event_type="chat_state",
            )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def tracked_contexts(self) -> int:
        with self._lock:
            return len(self._context_versions)

    def get_diagnostics(self) -> dict:
        """Return diagnostic info for monitoring."""
        with self._lock:
            return {
                "clients": len(self._clients),
                "tracked_contexts": len(self._context_versions),
                "pending_pushes": len(self._pending_timers),
                "context_versions": dict(self._context_versions),
            }


# Singleton
state_tracker = StateTracker()
