# [TEMPLATE: CUI // SP-CTI]
"""SSE (Server-Sent Events) connection manager for ICDEV™ dashboard.

Manages client connections, event broadcasting, and heartbeat.
Decision D29: SSE over WebSocket — Flask-native, simpler, unidirectional sufficient.
"""

import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class SSEManager:
    """Manages SSE client connections and event broadcasting."""

    def __init__(self, heartbeat_interval: int = 15):
        self._clients = []
        self._lock = threading.Lock()
        self._heartbeat_interval = heartbeat_interval
        self._event_queue = queue.Queue(maxsize=10000)
        self._running = False

    def add_client(self) -> queue.Queue:
        """Register a new SSE client. Returns a queue for the client."""
        client_queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._clients.append(client_queue)
        return client_queue

    def remove_client(self, client_queue: queue.Queue):
        """Unregister an SSE client."""
        with self._lock:
            if client_queue in self._clients:
                self._clients.remove(client_queue)

    def broadcast(self, event_data: dict, event_type: str = "message"):
        """Broadcast an event to all connected clients."""
        formatted = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
        dead_clients = []
        with self._lock:
            for client_queue in self._clients:
                try:
                    client_queue.put_nowait(formatted)
                except queue.Full:
                    dead_clients.append(client_queue)
            for dc in dead_clients:
                self._clients.remove(dc)

    def heartbeat(self):
        """Send heartbeat to all clients."""
        self.broadcast({"type": "heartbeat", "timestamp": datetime.now(timezone.utc).isoformat()}, "heartbeat")

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def generate_stream(self, client_queue: queue.Queue):
        """Generator that yields SSE-formatted events for a client."""
        try:
            while True:
                try:
                    data = client_queue.get(timeout=self._heartbeat_interval)
                    yield data
                except queue.Empty:
                    # Send heartbeat on timeout
                    yield f"event: heartbeat\ndata: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"  # noqa: E501
        except GeneratorExit:
            self.remove_client(client_queue)

    def broadcast_to_context(self, context_id: str, event_data: dict, event_type: str = "chat_update"):
        """Broadcast an event only to clients subscribed to a specific context.

        Phase 44 (D268-D270): Targeted push for chat stream state updates.
        Falls back to broadcast if no context filtering is set up.
        """
        event_data["context_id"] = context_id
        self.broadcast(event_data, event_type)

    # --- Progress streaming (adapted from Agent Harness SSE pattern) ---
    def broadcast_progress(
        self,
        operation_id: str,
        operation_type: str,
        phase: str,
        completed: int,
        total: int,
        status: str = "running",
        detail: str = None,
    ):
        """Broadcast a structured progress event for long-running operations.

        Provides real-time workflow status updates to the dashboard, similar
        to the Agent Harness ``harness_phase_start`` / ``harness_batch_complete``
        SSE events.

        Args:
            operation_id: Unique ID for this operation run.
            operation_type: Category (e.g. "genesis_reflex", "compliance_scan",
                            "batch_workflow", "marketplace_install",
                            "pulse_pipeline", "filesync").
            phase: Current phase/step label.
            completed: Number of completed steps/items.
            total: Total steps/items.
            status: One of "running", "completed", "failed", "paused".
            detail: Optional human-readable detail string.
        """
        event_data = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "phase": phase,
            "completed": completed,
            "total": total,
            "status": status,
            "percent": round((completed / total) * 100, 1) if total > 0 else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if detail:
            event_data["detail"] = detail
        self.broadcast(event_data, "progress")


# Singleton instance
sse_manager = SSEManager()


def emit_progress(
    operation_id: str,
    operation_type: str,
    phase: str,
    completed: int,
    total: int,
    status: str = "running",
    detail: str = None,
):
    """Module-level convenience for broadcasting progress events.

    Safe to call from any tool/module — no-op if no SSE clients connected.
    Import: ``from tools.dashboard.sse_manager import emit_progress``
    """
    sse_manager.broadcast_progress(
        operation_id,
        operation_type,
        phase,
        completed,
        total,
        status,
        detail,
    )
