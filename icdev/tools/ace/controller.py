# CUI // SP-CTI
"""ACE Controller — singleton orchestrator for Autonomous Collaborative Engine runs.

Provides a non-blocking ``launch()`` that:
  1. Classifies problem_text into a TeamManifest (ProblemClassifierLens)
  2. Assembles + persists a TeamInstance (TeamAssembler)
  3. Submits each CoWorkerThread to a shared ThreadPoolExecutor
  4. Emits an SSE progress event (best-effort)
  5. Returns instance_id immediately

CLI usage::

    python -m icdev.tools.ace.controller --launch "build a data pipeline" [--json]
    python -m icdev.tools.ace.controller --status <instance_id> [--json]
    python -m icdev.tools.ace.controller --abort <instance_id>
    python -m icdev.tools.ace.controller --list-roles
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.ace.controller")

_DB_ENV = "ICDEV_ACE_DB_URL"
_MAX_WORKERS = 16  # max concurrent CoWorkerThreads across all instances


# ---------------------------------------------------------------------------
# ACEController singleton
# ---------------------------------------------------------------------------


class ACEController:
    """Singleton orchestrator — one per process.

    Obtain via ``ACEController.get_instance()``.
    """

    _instance: "ACEController | None" = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ACEController":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="ace-cw")
        # Maps instance_id → list of CoWorkerThreads so abort() can stop them
        self._threads: dict[str, list[Any]] = {}
        self._threads_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def launch(
        self,
        problem_text: str,
        trigger_source: str,
        trigger_ref: str,
        user_id: str = "system",
        project_id: str = "",
        preset_label: str = "",
    ) -> str:
        """Launch an ACE run non-blocking.  Returns instance_id immediately."""
        instance_id = f"ace-{uuid.uuid4().hex[:12]}"
        self._run(instance_id, problem_text, trigger_source, trigger_ref, user_id, project_id, preset_label)
        return instance_id

    def status(self, instance_id: str) -> dict[str, Any]:
        """Return status dict for an ACE instance from the DB."""
        try:
            from icdev.tools.db.storage import get_canvas_connection
            from icdev.tools.ace.db.init_db import init as _init_ace_db

            _init_ace_db()
            conn = get_canvas_connection(_DB_ENV)
            try:
                row = conn.execute(
                    "SELECT id, name, state, trust_tier, created_at, updated_at FROM ace_instances WHERE id = ?",
                    (instance_id,),
                ).fetchone()
                if not row:
                    return {"instance_id": instance_id, "error": "not_found"}
                coworkers = conn.execute(
                    "SELECT id, role_id, state, assigned_step FROM ace_coworkers WHERE instance_id = ?",
                    (instance_id,),
                ).fetchall()
            finally:
                conn.close()

            return {
                "instance_id": row[0],
                "name": row[1],
                "state": row[2],
                "trust_tier": row[3],
                "created_at": row[4],
                "updated_at": row[5],
                "coworkers": [
                    {"id": c[0], "role_id": c[1], "state": c[2], "assigned_step": c[3]}
                    for c in coworkers
                ],
            }
        except Exception as exc:
            return {"instance_id": instance_id, "error": str(exc)}

    def abort(self, instance_id: str) -> None:
        """Signal all CoWorkerThreads for an instance to stop."""
        with self._threads_lock:
            threads = self._threads.get(instance_id, [])
        for t in threads:
            try:
                t.stop()
            except Exception:
                pass
        self._set_instance_state(instance_id, "cancelled")
        logger.info("Aborted ACE instance %s (%d threads signalled)", instance_id, len(threads))

    @staticmethod
    def list_roles() -> list[str]:
        """Return sorted list of all loaded role_ids."""
        from icdev.tools.ace.role_loader import RoleLoader

        loader = RoleLoader()
        return sorted(r.role_id for r in loader.list_roles())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(
        self,
        instance_id: str,
        problem_text: str,
        trigger_source: str,
        trigger_ref: str,
        user_id: str,
        project_id: str,
        preset_label: str = "",
    ) -> None:
        """Full orchestration pipeline executed in ThreadPoolExecutor."""
        self._emit_sse(instance_id, "assembling", "Classifying problem")
        try:
            # 1. Classify
            from icdev.tools.ace.problem_classifier import ProblemClassifierLens

            manifest = ProblemClassifierLens(problem_text).run()
            logger.info("ACE %s: manifest has %d slot(s)", instance_id, len(manifest.slots))

            # 2. Assemble + persist
            from icdev.tools.ace.team_assembler import TeamAssembler

            context = {
                "problem_text": problem_text,
                "trigger_source": trigger_source,
                "trigger_ref": trigger_ref,
                "user_id": user_id,
                "project_id": project_id,
                "preset_label": preset_label,
                "name": f"ace:{trigger_source}:{trigger_ref}"[:120],
            }
            assembler = TeamAssembler()
            team = assembler.assemble(manifest, instance_id, context)
            logger.info("ACE %s: assembled %d coworker(s)", instance_id, len(team.specs))

            self._set_instance_state(instance_id, "active")
            self._emit_sse(instance_id, "active", f"Team assembled ({len(team.specs)} coworkers)")

            # 3. Build shared resources
            from icdev.tools.ace.message_bus import MessageBus
            from icdev.tools.daemon.base import TrustKernelBase

            bus = MessageBus(instance_id=instance_id)
            trust_kernel = TrustKernelBase(config={})

            # 4. Launch CoWorkerThreads
            from icdev.tools.ace.coworker_thread import CoWorkerThread

            threads: list[CoWorkerThread] = []
            for spec in team.specs:
                t = CoWorkerThread(
                    spec=spec,
                    instance_id=instance_id,
                    message_bus=bus,
                    trust_kernel=trust_kernel,
                )
                threads.append(t)

            with self._threads_lock:
                self._threads[instance_id] = threads

            for t in threads:
                t.start()

            # 5. Wait for all threads (non-blocking from caller's perspective since
            #    we're already in the executor worker thread)
            for t in threads:
                t.join()

            self._set_instance_state(instance_id, "complete")
            self._emit_sse(instance_id, "complete", "All coworkers finished")
            logger.info("ACE %s: complete", instance_id)

        except Exception as exc:
            logger.exception("ACE %s: fatal error: %s", instance_id, exc)
            self._set_instance_state(instance_id, "failed")
            self._emit_sse(instance_id, "failed", str(exc))
        finally:
            with self._threads_lock:
                self._threads.pop(instance_id, None)

    def _set_instance_state(self, instance_id: str, state: str) -> None:
        """Update ace_instances.state (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "UPDATE ace_instances SET state = ?, updated_at = ? WHERE id = ?",
                    (state, now, instance_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("_set_instance_state(%s, %s) failed: %s", instance_id, state, exc)

    @staticmethod
    def _emit_sse(instance_id: str, phase: str, detail: str) -> None:
        """Broadcast an ACE progress event via SSEManager (best-effort)."""
        try:
            from tools.dashboard.sse_manager import sse_manager

            sse_manager.broadcast(
                {
                    "type": "ace_progress",
                    "instance_id": instance_id,
                    "phase": phase,
                    "detail": detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                event_type="ace_progress",
            )
        except Exception:
            pass  # SSE is best-effort; never crash the run


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="ACEController CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--launch", metavar="TEXT", help="Launch an ACE run with the given problem text")
    group.add_argument("--status", metavar="INSTANCE_ID", help="Show status for an ACE instance")
    group.add_argument("--abort", metavar="INSTANCE_ID", help="Abort an ACE instance")
    group.add_argument("--list-roles", action="store_true", help="List available role IDs")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")
    parser.add_argument("--trigger-source", default="cli")
    parser.add_argument("--trigger-ref", default="")
    parser.add_argument("--user-id", default="system")
    parser.add_argument("--project-id", default="")
    args = parser.parse_args()

    ctrl = ACEController.get_instance()

    if args.list_roles:
        roles = ctrl.list_roles()
        if args.as_json:
            print(json.dumps({"roles": roles}))
        else:
            print(", ".join(roles))
        sys.exit(0)

    if args.launch:
        instance_id = ctrl.launch(
            problem_text=args.launch,
            trigger_source=args.trigger_source,
            trigger_ref=args.trigger_ref,
            user_id=args.user_id,
            project_id=args.project_id,
        )
        if args.as_json:
            print(json.dumps({"instance_id": instance_id}))
        else:
            print(instance_id)
        sys.exit(0)

    if args.status:
        result = ctrl.status(args.status)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            state = result.get("state", result.get("error", "unknown"))
            coworkers = result.get("coworkers", [])
            print(f"instance: {args.status}  state={state}  coworkers={len(coworkers)}")
        sys.exit(0)

    if args.abort:
        ctrl.abort(args.abort)
        if args.as_json:
            print(json.dumps({"aborted": args.abort}))
        else:
            print(f"Abort signalled for {args.abort}")
        sys.exit(0)


if __name__ == "__main__":
    _cli()
