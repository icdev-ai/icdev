
from tools.logging.icdev_logger import get_logger
# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Collaborative Editing.

Polling-based real-time collaboration for security designs.
Clients poll for changes via /api/collab/poll endpoint.
No WebSocket dependency — works in air-gapped/CUI environments.
"""

import threading
import uuid as _uuid
from datetime import datetime, timezone

logger = get_logger("icdev.security_canvas.collab")

# In-memory session store (single-process; for multi-process use Redis)
_sessions = {}  # design_id -> CollabSession
_lock = threading.Lock()


class CollabSession:
    """Tracks participants and pending operations for a design."""

    def __init__(self, design_id: str):
        self.design_id = design_id
        self.participants = {}  # user_id -> {name, color, cursor, last_seen}
        self.operations = []  # list of {id, user_id, op_type, data, ts}
        self.seq = 0  # monotonic sequence number
        self._lock = threading.Lock()

    def join(self, user_id: str, user_name: str = "") -> dict:
        colors = ["#e74c3c", "#3498db", "#27ae60", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#2c3e50"]
        with self._lock:
            idx = len(self.participants) % len(colors)
            self.participants[user_id] = {
                "name": user_name or f"User-{user_id[:6]}",
                "color": colors[idx],
                "cursor": None,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }
        return {"session_id": self.design_id, "color": colors[idx], "participants": self.get_participants()}

    def leave(self, user_id: str):
        with self._lock:
            self.participants.pop(user_id, None)

    def get_participants(self) -> list:
        return [{"user_id": uid, **info} for uid, info in self.participants.items()]

    def push_operation(self, user_id: str, op_type: str, data: dict) -> int:
        with self._lock:
            self.seq += 1
            op = {
                "id": str(_uuid.uuid4())[:8],
                "seq": self.seq,
                "user_id": user_id,
                "op_type": op_type,  # "node_add", "node_move", "node_delete", "edge_add", "edge_delete", "cursor_move"
                "data": data,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            self.operations.append(op)
            # Keep only last 200 operations
            if len(self.operations) > 200:
                self.operations = self.operations[-200:]
            # Update last_seen
            if user_id in self.participants:
                self.participants[user_id]["last_seen"] = op["ts"]
            return self.seq

    def get_operations_since(self, since_seq: int) -> list:
        return [op for op in self.operations if op["seq"] > since_seq]

    def update_cursor(self, user_id: str, x: float, y: float):
        if user_id in self.participants:
            self.participants[user_id]["cursor"] = {"x": x, "y": y}
            self.participants[user_id]["last_seen"] = datetime.now(timezone.utc).isoformat()


def get_session(design_id: str) -> CollabSession:
    with _lock:
        if design_id not in _sessions:
            _sessions[design_id] = CollabSession(design_id)
        return _sessions[design_id]


def close_session(design_id: str):
    with _lock:
        _sessions.pop(design_id, None)
