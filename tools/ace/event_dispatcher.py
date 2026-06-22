# CUI // SP-CTI
"""ACE Event Dispatcher — background thread that auto-routes events to coworker roles.

Polls ace_events every few seconds, matches each event's topic against all loaded
roles' listen_topics, and launches an ACE session per matched role.

Design
------
- Single daemon thread, started once in create_app()
- Reads role listen_topics from the RoleLoader (hot-reloads every 60 s)
- Uses ACEController.launch() — fully non-blocking, returns instance_id
- Writes results to ace_event_results for the event feed UI
- Rate-limits: max 1 dispatch per role per topic per 30 s (dedup window)
- Caps auto-dispatches: max 3 roles per event (prevent fanout storms)

Supported topic → role topic mapping is declared in each role's YAML:
    communication:
      listen_topics: ["text.produced", "document.analyzed"]
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icdev.tools.ace.role_loader import RoleTemplate

logger = get_logger("icdev.ace.event_dispatcher")

_POLL_INTERVAL = 5        # seconds between DB polls
_MAX_ROLES_PER_EVENT = 3  # fanout cap
_DEDUP_WINDOW = 30        # seconds — skip if same role+topic dispatched recently


class ACEEventDispatcher:
    """Background dispatcher — one singleton per process."""

    _instance: "ACEEventDispatcher | None" = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ACEEventDispatcher":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        # Dedup cache: (role_id, topic) → last_dispatch_time
        self._recent: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="ace-event-dispatcher", daemon=True
        )
        self._thread.start()
        logger.info("ACE Event Dispatcher started (poll every %ds)", _POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        # Stagger startup to let Flask finish initializing
        time.sleep(8)
        while self._running:
            try:
                self._process_pending()
            except Exception as exc:
                logger.warning("Dispatcher cycle error: %s", exc)
            time.sleep(_POLL_INTERVAL)

    def _process_pending(self) -> None:
        from icdev.tools.ace.event_bus import get_pending, mark_processed, store_result
        events = get_pending(limit=20)
        if not events:
            return

        # Refresh roles (hot-reload built into RoleLoader)
        roles = self._get_roles()
        if not roles:
            return

        # Build topic → [role] index
        topic_index: dict[str, list[RoleTemplate]] = {}
        for role in roles:
            for topic in (role.communication.get("listen_topics") or []):
                topic_index.setdefault(topic, []).append(role)

        for event in events:
            matched = topic_index.get(event["topic"], [])
            dispatched = 0
            for role in matched:
                if dispatched >= _MAX_ROLES_PER_EVENT:
                    break
                key = (role.role_id, event["topic"])
                now = time.monotonic()
                if now - self._recent.get(key, 0) < _DEDUP_WINDOW:
                    continue  # too recent
                instance_id = self._launch(role, event)
                if instance_id:
                    store_result(event["id"], role.role_id, instance_id)
                    self._recent[key] = now
                    dispatched += 1
                    logger.info(
                        "Dispatched %s → role=%s instance=%s",
                        event["topic"], role.role_id, instance_id,
                    )
            mark_processed(event["id"])

    def _launch(self, role: "RoleTemplate", event: dict) -> str | None:
        """Launch an ACE session for the given role + event. Returns instance_id."""
        try:
            from icdev.tools.ace.controller import ACEController
            payload = event.get("payload") or {}
            content = payload.get("content", "")
            canvas = event.get("source_canvas", "ecosystem")
            topic = event["topic"]

            task = (
                f"[ACE AUTO-DISPATCH | topic={topic} | source={canvas}]\n\n"
                f"Role: {role.display_name}\n"
                f"Task: Apply your expertise to the following content from {canvas}.\n\n"
                f"--- CONTENT ---\n{content[:2500]}\n--- END ---\n\n"
                f"Steps to execute: {', '.join(role.steps)}"
            )

            return ACEController.get_instance().launch(
                problem_text=task,
                trigger_source=f"ace_event:{topic}",
                trigger_ref=str(event["id"]),
                user_id="system",
            )
        except Exception as exc:
            logger.warning("Launch failed for role %s: %s", role.role_id, exc)
            return None

    def _get_roles(self) -> list["RoleTemplate"]:
        try:
            from icdev.tools.ace.role_loader import RoleLoader
            return RoleLoader().list_roles()
        except Exception as exc:
            logger.debug("RoleLoader failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Canvas helper — call from any blueprint/route
# ---------------------------------------------------------------------------

def infer_canvas_from_path(path: str) -> str:
    """Derive a canvas slug from a request path for event attribution."""
    _CANVAS_PREFIXES = [
        ("/document-intelligence", "dic"),
        ("/writeguard", "writeguard"),
        ("/security", "security"),
        ("/network", "network"),
        ("/compliance", "compliance"),
        ("/kanban", "kanban"),
        ("/proposals", "govcon"),
        ("/cpmp", "cpmp"),
        ("/research", "research"),
        ("/innovation", "innovation"),
        ("/strategos", "strategos"),
        ("/dai", "dat"),
        ("/foundry", "foundry"),
        ("/integrity", "integrity"),
        ("/finetune", "finetune"),
        ("/studio", "studio"),
        ("/slides", "slides"),
        ("/skillhub", "skillhub"),
        ("/coworker", "ace"),
        ("/chat", "chat"),
    ]
    for prefix, slug in _CANVAS_PREFIXES:
        if path.startswith(prefix):
            return slug
    return "unknown"


def infer_topic_from_path(path: str, response_keys: set[str]) -> str:
    """Derive an event topic from route path + response shape."""
    p = path.lower()
    if any(k in p for k in ("ingest", "upload", "import")):
        return "document.ingested"
    if any(k in p for k in ("document", "analyze", "notebook", "study", "faq")):
        return "document.analyzed"
    if any(k in p for k in ("security", "scan", "stig", "vuln", "cve")):
        return "security.scan.completed"
    if any(k in p for k in ("network", "topology", "migration")):
        return "network.analyzed"
    if any(k in p for k in ("compliance", "poam", "gap", "rmf")):
        return "compliance.gap.found"
    if any(k in p for k in ("report", "generate", "brief", "summary")):
        return "report.generated"
    if any(k in p for k in ("proposal", "writeguard", "write")):
        return "text.produced"
    return "text.produced"
