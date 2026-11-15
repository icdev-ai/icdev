# CUI // SP-CTI
"""ACE Event Dispatcher — turns ACE events into recommendations, or auto-launches under a grant.

Polls the event log (``event_log.py``: ``ace_events``), matches each event's
topic against the roles' ``listen_topics`` (exact match), and for each matched
role (icx-ace-01, owner 2026-11-15):

1. **Under a live auto-dispatch grant** (``auto_dispatch.live_grant``: declared
   eligible in the role YAML, trust >= trusted, granted by a person, not revoked,
   expired, over today's limit or stopped) it launches the role and records
   ``auto_launched`` with the grant id. A person is on the loop: the feed shows
   it and the run can be aborted at once.
2. **Otherwise** it RECOMMENDS: ``declared.propose`` records a pending step of
   ``decl-wft-ace-run-launch`` (surface ``ace_api.api_launch``) as the dispatcher's
   service actor, and the result row is ``recommended`` with the step id.
   Nothing launches.
3. Each cycle it sweeps its own ``recommended`` rows. A step a person decided
   (affirm) is SPENT once, for exactly the launch spec that was proposed, and
   only then launched (``launched``). A declined step is ``declined``; one
   withdrawn, or undecided past ``recommendation_expiry_hours``, is ``withdrawn``.

The dispatcher never decides: its actor is a service key, which ``declared``
refuses as a decider. Fanout cap and dedup window are kept, from
``args/ace/auto_dispatch.yaml``. One daemon thread, started by the dashboard
server (``start_if_enabled``) when ``dispatcher_enabled`` is true; tests call
``_process_pending()`` directly.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from tools.logging.icdev_logger import get_logger

if TYPE_CHECKING:
    from icdev.tools.ace.role_loader import RoleTemplate

logger = get_logger("icdev.ace.event_dispatcher")

LAUNCH_TEMPLATE = "decl-wft-ace-run-launch"
LAUNCH_SURFACE = "ace_api.api_launch"
#: The dispatcher's principal. A service key proposes and spends; it never decides.
DISPATCHER_ACTOR = {"id": "ace-dispatcher", "role": "service"}

_FIRST_CYCLE_DELAY = 8  # seconds: let the server finish starting


def launch_spec(role: "RoleTemplate", event: dict) -> dict[str, Any]:
    """The launch an event asks of a role. Deterministic: a decided step is spent
    for exactly this payload, rebuilt from the stored event and the role."""
    payload = event.get("payload") or {}
    content = str(payload.get("content") or "")
    canvas = event.get("source_canvas") or "ecosystem"
    topic = event["topic"]
    steps = ", ".join(getattr(s, "name", str(s)) for s in role.steps)
    task = (
        f"[ACE EVENT | topic={topic} | source={canvas}]\n\n"
        f"Role: {role.display_name}\n"
        f"Task: Apply your expertise to the following content from {canvas}.\n\n"
        f"--- CONTENT ---\n{content[:2500]}\n--- END ---\n\n"
        f"Steps to execute: {steps}"
    )
    return {"problem_text": task, "role_ids": [role.role_id],
            "trigger_source": f"ace_event:{topic}", "trigger_ref": str(event["id"]),
            "user_id": DISPATCHER_ACTOR["id"]}


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

    def __init__(self, roles: list | None = None) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        # Dedup cache: (role_id, topic) -> last time acted on
        self._recent: dict[tuple[str, str], float] = {}
        # Roles to use instead of RoleLoader (tests).
        self._roles_override = roles

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
        logger.info("ACE Event Dispatcher started (recommend by default; auto only under a grant)")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        from icdev.tools.ace.auto_dispatch import load_config

        time.sleep(_FIRST_CYCLE_DELAY)
        while self._running:
            try:
                self._process_pending()
            except Exception as exc:  # noqa: BLE001 — a cycle never kills the thread
                logger.warning("Dispatcher cycle error: %s", exc)
            time.sleep(max(1, int(load_config()["poll_seconds"])))

    def _process_pending(self) -> dict[str, int]:
        """One cycle: sweep recommendations, then act on new events. Returns counts."""
        from icdev.tools.ace import event_log
        from icdev.tools.ace.auto_dispatch import load_config

        cfg = load_config()
        counts = self._sweep(cfg)
        events = event_log.get_pending(limit=20)
        if not events:
            return counts
        roles = self._get_roles()
        if not roles:
            return counts  # leave events unprocessed until roles load

        topic_index: dict[str, list[RoleTemplate]] = {}
        for role in roles:
            for topic in (role.communication.get("listen_topics") or []):
                topic_index.setdefault(topic, []).append(role)

        for event in events:
            acted, failed = 0, False
            for role in topic_index.get(event["topic"], []):
                if acted >= int(cfg["max_roles_per_event"]):
                    break
                key = (role.role_id, event["topic"])
                now = time.monotonic()
                if now - self._recent.get(key, -1e9) < float(cfg["dedup_window_seconds"]):
                    continue  # too recent
                outcome = self._dispatch(role, event)
                if outcome is None:
                    failed = True
                    continue
                counts[outcome] = counts.get(outcome, 0) + 1
                self._recent[key] = now
                acted += 1
            if not failed:
                event_log.mark_processed(event["id"])
        return counts

    def _dispatch(self, role: "RoleTemplate", event: dict) -> str | None:
        """Auto-launch under a live grant, or recommend. Returns the status recorded,
        or None when nothing could be recorded (the event is retried)."""
        from icdev.tools.ace import auto_dispatch, event_log
        from icdev.tools.workflow_hitl import declared

        spec = launch_spec(role, event)
        grant, why = auto_dispatch.live_grant(role.role_id, event["topic"],
                                              roles=self._roles_override)
        if grant:
            try:
                instance_id = self._launch(spec)
            except Exception as exc:  # noqa: BLE001
                event_log.store_result(event["id"], role.role_id, "", "auto_refused",
                                       grant_id=grant["grant_id"],
                                       reason=f"launch failed: {exc}")
                return "auto_refused"
            event_log.store_result(event["id"], role.role_id, instance_id, "auto_launched",
                                   grant_id=grant["grant_id"],
                                   reason=f"auto, under grant {grant['grant_id']}")
            logger.info("Auto-launched %s -> role=%s instance=%s (grant %s)",
                        event["topic"], role.role_id, instance_id, grant["grant_id"])
            return "auto_launched"

        note = f"{event['topic']} from {event.get('source_canvas') or 'ecosystem'} " \
               f"(event {event['id']})"
        try:
            step = declared.propose(LAUNCH_TEMPLATE, surface=LAUNCH_SURFACE, subject=role.role_id,
                                    payload=spec, actor=dict(DISPATCHER_ACTOR), note=note)
        except declared.StepRefused as exc:
            logger.warning("Recommendation for %s not recorded: %s", role.role_id, exc)
            return None
        try:
            event_log.store_result(event["id"], role.role_id, "", "recommended",
                                   step_id=step["step_id"], reason=why)
        except Exception as exc:  # noqa: BLE001 — no feed row: nothing would spend it
            declared.withdraw(step["step_id"], f"the event feed could not record it ({exc})")
            return None
        return "recommended"

    def _sweep(self, cfg: dict) -> dict[str, int]:
        """Move this dispatcher's recommendations on, from what people decided."""
        from icdev.tools.ace import event_log
        from icdev.tools.workflow_hitl import declared

        counts: dict[str, int] = {}

        def _set(row: dict, status: str, reason: str, instance_id: str = "") -> None:
            event_log.update_result(row["id"], status, instance_id=instance_id, reason=reason)
            counts[status] = counts.get(status, 0) + 1

        expiry = timedelta(hours=float(cfg["recommendation_expiry_hours"]))
        roles = {r.role_id: r for r in self._get_roles()}
        for row in event_log.results_with_status("recommended"):
            state = declared.step_state(row["step_id"]) if row["step_id"] else None
            if state is None:
                _set(row, "withdrawn", "its step is not in the ledger")
                continue
            if state["status"] == "withdrawn":
                _set(row, "withdrawn", "its step was withdrawn")
            elif state["status"] == "spent":
                _set(row, "withdrawn", "its step was already spent")
            elif state["status"] == "pending":
                if _older_than(state["proposed_at"], expiry):
                    declared.withdraw(row["step_id"], "undecided past "
                                      f"{cfg['recommendation_expiry_hours']}h")
                    _set(row, "withdrawn", "undecided past "
                         f"{cfg['recommendation_expiry_hours']}h")
            elif not state["affirmative"]:
                _set(row, "declined", f"declined by {state['decided_by']}")
            else:
                event = event_log.get_event(row["event_id"])
                role = roles.get(row["role_id"])
                if event is None or role is None:
                    _set(row, "withdrawn", "its event or role no longer exists")
                    continue
                try:
                    declared.spend(row["step_id"], template_id=LAUNCH_TEMPLATE,
                                   surface=LAUNCH_SURFACE, payload=launch_spec(role, event),
                                   actor=dict(DISPATCHER_ACTOR))
                except declared.StepRefused as exc:
                    _set(row, "withdrawn", f"not launched: {exc}")
                    continue
                try:
                    instance_id = self._launch(launch_spec(role, event))
                except Exception as exc:  # noqa: BLE001
                    _set(row, "withdrawn", f"decided, but the launch failed: {exc}")
                    continue
                _set(row, "launched", f"decided by {state['decided_by']}", instance_id)
        return counts

    def _launch(self, spec: dict[str, Any]) -> str:
        """Launch a team for exactly this spec. Returns the instance id; raises on failure."""
        from icdev.tools.ace.controller import ACEController

        instance_id = ACEController.get_instance().launch(
            problem_text=spec["problem_text"],
            trigger_source=spec["trigger_source"],
            trigger_ref=spec["trigger_ref"],
            user_id=spec["user_id"],
            role_ids=list(spec["role_ids"]),
        )
        if not instance_id:
            raise RuntimeError("the controller returned no instance")
        return str(instance_id)

    def _get_roles(self) -> list["RoleTemplate"]:
        if self._roles_override is not None:
            return list(self._roles_override)
        try:
            from icdev.tools.ace.role_loader import RoleLoader
            return RoleLoader().list_roles()
        except Exception as exc:
            logger.debug("RoleLoader failed: %s", exc)
            return []


def _older_than(stamp: str, age: timedelta) -> bool:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > age


def start_if_enabled() -> bool:
    """Start the process's dispatcher when ``dispatcher_enabled`` (the dashboard server)."""
    from icdev.tools.ace.auto_dispatch import load_config

    if not load_config()["dispatcher_enabled"]:
        logger.info("ACE Event Dispatcher disabled (args/ace/auto_dispatch.yaml)")
        return False
    ACEEventDispatcher.get_instance().start()
    return True




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
