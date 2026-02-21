# CUI // SP-CTI
"""SSE (Server-Sent Events) connection manager for ICDEV dashboard.

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
                    yield f"event: heartbeat\ndata: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"
        except GeneratorExit:
            self.remove_client(client_queue)


# Singleton instance
sse_manager = SSEManager()
