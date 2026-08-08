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
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.controller")

_DB_ENV = "ICDEV_ACE_DB_URL"
_MAX_WORKERS = 16  # max concurrent CoWorkerThreads across all instances
_MEMORY_CAP = 50   # max ace_coworker_memory rows per role

_REQUIRED_ACE_TABLES = (
    "ace_instances",
    "ace_coworkers",
    "ace_messages",
    "ace_artifacts",
    "ace_agent_workflows",
    "ace_audit_log",
    "ace_webhook_log",
    "ace_sessions",
)

# Patterns that identify decision/outcome/lesson sentences worth persisting
_FACT_RE = re.compile(
    r"\b(decided|chose|selected|will use|going with|adopted|implemented|"
    r"completed|finished|produced|output|result|learned|lesson|should|avoid|"
    r"fixed|resolved|issue|error|note:|important:|key:)\b",
    re.IGNORECASE,
)


def _extract_facts(content_md: str, max_facts: int = 10) -> list[str]:
    """Return up to *max_facts* key sentences from *content_md* via pattern match.

    Prefers lines that contain decision/outcome/lesson keywords; falls back to
    the first substantial sentences if none match.
    """
    candidates: list[str] = []
    for line in content_md.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("|"):
            continue
        clean = re.sub(r"^[-*+]\s+", "", line)
        clean = re.sub(r"^\d+\.\s+", "", clean)
        if 20 <= len(clean) <= 500:
            candidates.append(clean)

    keyword_hits = [c for c in candidates if _FACT_RE.search(c)]
    selected = keyword_hits if keyword_hits else candidates
    return selected[:max_facts]


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
        # Maps instance_id → threading.Event set by abort() so _run's join loop
        # stops waiting promptly instead of blocking on thread.join().
        self._abort_events: dict[str, threading.Event] = {}
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
        webhook_url: str = "",
        role_ids: list[str] | None = None,
    ) -> str:
        """Launch an ACE run non-blocking.  Returns instance_id immediately.

        Writes a ``pending`` stub row synchronously before handing off to
        the background thread so that ``/coworker/<id>`` never 404s right
        after launch.  The thread upgrades the row to ``assembling`` once
        TeamAssembler.assemble() runs.
        """
        instance_id = f"ace-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        config = {
            "problem_text": problem_text,
            "trigger_source": trigger_source,
            "trigger_ref": trigger_ref,
            "user_id": user_id,
            "project_id": project_id,
        }
        name = f"ace:{trigger_source}:{trigger_ref}"[:120]

        try:
            from icdev.tools.db.storage import get_canvas_connection
            from icdev.tools.ace.db.init_db import init as _init_ace_db

            _init_ace_db()
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_instances "
                    "(id, name, state, config_json, created_at, updated_at) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s)",
                    (instance_id, name, json.dumps(config), now, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("ace launch: pre-insert failed for %s: %s", instance_id, exc)

        # webhook_url must be passed through as a keyword: the positional args
        # stopped at project_id, so _run always received the "" default and
        # _persist_webhook_url / _deliver_webhook were unreachable from launch().
        # cortex.api.agent() forwards a webhook_url that was being dropped here.
        self._executor.submit(
            self._run, instance_id, problem_text, trigger_source, trigger_ref,
            user_id, project_id, webhook_url=webhook_url, role_ids=role_ids,
        )
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
                    "SELECT id, name, state, trust_tier, created_at, updated_at FROM ace_instances WHERE id = %s",
                    (instance_id,),
                ).fetchone()
                if not row:
                    return {"instance_id": instance_id, "error": "not_found"}
                coworkers = conn.execute(
                    "SELECT id, role_id, state, assigned_step FROM ace_coworkers WHERE instance_id = %s",
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
        """Signal all CoWorkerThreads for an instance to stop.

        Raises LookupError if *instance_id* does not exist.
        """
        from icdev.tools.db.storage import get_canvas_connection
        from icdev.tools.ace.db.init_db import init as _init_ace_db

        _init_ace_db()
        conn = get_canvas_connection(_DB_ENV)
        try:
            row = conn.execute(
                "SELECT id FROM ace_instances WHERE id = %s", (instance_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise LookupError(f"ACE instance {instance_id} not found")

        with self._threads_lock:
            threads = self._threads.get(instance_id, [])
            abort_event = self._abort_events.get(instance_id)
        if abort_event is not None:
            abort_event.set()  # wake _run's join loop
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

    # ---- Karpathy wiki integration (Items 2 & 5) -------------------------

    @staticmethod
    def _query_role_wiki(role_ids: list[str], problem_text: str) -> str:
        """Query the memory wiki for knowledge relevant to these roles and problem.

        Returns a brief wiki context string (top-3 snippets) or empty string.
        Best-effort: never raises.
        """
        try:
            import re

            from tools.memory.claude_memory_path import claude_memory_dir

            auto_dir = claude_memory_dir()
            if not auto_dir.is_dir():
                return ""

            # BM25-style file search — no external DB needed
            query = " ".join(role_ids) + " " + problem_text[:120]
            stop = frozenset({"the", "a", "an", "in", "of", "to", "is", "it", "for", "on", "and", "or"})
            terms = [t for t in re.findall(r"[a-z]{4,}", query.lower()) if t not in stop]
            if not terms:
                return ""

            scored: list[tuple[float, str]] = []
            for fpath in auto_dir.glob("*.md"):
                if fpath.name == "MEMORY.md":
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8")
                    cl = content.lower()
                    hits = sum(cl.count(t) for t in terms)
                    if hits > 0:
                        score = hits / (len(content) / 400 + 1)
                        # Pull first non-frontmatter paragraph as snippet
                        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
                        snippet = body.strip()[:200]
                        scored.append((score, snippet))
                except Exception:
                    continue

            scored.sort(key=lambda x: x[0], reverse=True)
            snippets = [f"- {s}" for _, s in scored[:3] if s]
            return "\n".join(snippets)

        except Exception:
            return ""

    @staticmethod
    def _file_session_to_wiki(
        instance_id: str,
        problem_text: str,
        role_ids: list[str],
    ) -> None:
        """File a brief ACE session summary to the memory wiki after completion.

        Enables the cross-role wiki link pass (Item 5) and builds up an
        institutional knowledge base of past coworker sessions.
        Best-effort: never raises.
        """
        try:
            import hashlib
            from datetime import datetime, timezone

            from tools.memory.claude_memory_path import claude_memory_dir
            from tools.memory.memory_write import update_crossrefs

            auto_dir = claude_memory_dir()
            if not auto_dir.is_dir():
                return

            slug = "ace-session-" + hashlib.sha256(
                instance_id.encode()
            ).hexdigest()[:10]
            topic_file = auto_dir / f"{slug}.md"
            if topic_file.exists():
                return

            roles_str = ", ".join(role_ids) if role_ids else "unknown"
            date_str = datetime.now(timezone.utc).date().isoformat()
            body = (
                f"---\n"
                f"name: {slug}\n"
                f"description: ACE session {instance_id[:12]} — {roles_str}\n"
                f"metadata:\n"
                f"  type: project\n"
                f"---\n\n"
                f"**ACE session:** `{instance_id}`  \n"
                f"**Date:** {date_str}  \n"
                f"**Roles:** {roles_str}  \n\n"
                f"**Problem:** {problem_text[:400]}\n"
            )
            topic_file.write_text(body, encoding="utf-8", newline="")

            mem_index = auto_dir / "MEMORY.md"
            if mem_index.exists():
                existing = mem_index.read_text(encoding="utf-8")
                entry = (
                    f"- [ACE session {instance_id[:12]}]({slug}.md)"
                    f" — coworker run: {roles_str}\n"
                )
                if slug not in existing:
                    with open(mem_index, "a", encoding="utf-8") as fh:
                        fh.write(entry)

            # Item 5: cross-link related wiki entries
            update_crossrefs(slug, f"{roles_str} {problem_text}", memory_dir=auto_dir)

        except Exception:
            pass

    def _run(
        self,
        instance_id: str,
        problem_text: str,
        trigger_source: str,
        trigger_ref: str,
        user_id: str,
        project_id: str,
        webhook_url: str = "",
        role_ids: list[str] | None = None,
    ) -> None:
        """Full orchestration pipeline executed in ThreadPoolExecutor."""
        self._emit_sse(instance_id, "assembling", "Classifying problem")
        try:
            # 0. Wiki context (Item 2): enrich problem_text with relevant wiki knowledge
            wiki_ctx = self._query_role_wiki(role_ids or [], problem_text)
            enriched_problem = (
                f"{problem_text}\n\n[Wiki context]\n{wiki_ctx}"
                if wiki_ctx
                else problem_text
            )

            # 1. Classify — or use explicit role_ids if provided
            if role_ids:
                from icdev.tools.ace.problem_classifier import TeamManifest, RoleSlot
                manifest = TeamManifest(
                    slots=[RoleSlot(role_id=r, count=1, priority="high") for r in role_ids]
                )
                logger.info(
                    "ACE %s: using explicit roles: %s", instance_id, role_ids
                )
            else:
                from icdev.tools.ace.problem_classifier import ProblemClassifierLens
                manifest = ProblemClassifierLens(enriched_problem).run()
            logger.info("ACE %s: manifest has %d slot(s)", instance_id, len(manifest.slots))

            # 2. Assemble + persist
            from icdev.tools.ace.team_assembler import TeamAssembler

            context = {
                "problem_text": problem_text,
                "trigger_source": trigger_source,
                "trigger_ref": trigger_ref,
                "user_id": user_id,
                "project_id": project_id,
                "name": f"ace:{trigger_source}:{trigger_ref}"[:120],
            }
            assembler = TeamAssembler()
            team = assembler.assemble(manifest, instance_id, context)
            logger.info("ACE %s: assembled %d coworker(s)", instance_id, len(team.specs))

            if webhook_url:
                self._persist_webhook_url(instance_id, webhook_url)

            self._set_instance_state(instance_id, "active")
            self._emit_sse(instance_id, "active", f"Team assembled ({len(team.specs)} coworkers)")

            # 3. Build shared resources
            from icdev.tools.ace.message_bus import MessageBus
            from icdev.tools.daemon.base import TrustKernelBase

            bus = MessageBus(instance_id=instance_id)

            # NOVA TRUST: resolve per-role dispatch config (trust band, HITL mode,
            # max_parallel) before spawning threads.  Probationary roles are
            # excluded from dispatch entirely — a HITL review card is created.
            try:
                from icdev.tools.ace.trust_calibrator import get_dispatch_config
                _trust_fn = get_dispatch_config
            except Exception:
                _trust_fn = None  # trust_calibrator unavailable; proceed without

            # 4. Launch CoWorkerThreads
            from icdev.tools.ace.coworker_thread import CoWorkerThread

            threads: list[CoWorkerThread] = []
            skipped_probationary: list[str] = []
            # Keyed by role_id so we can compute effective_max_parallel after the loop.
            trust_cfgs: dict[str, dict] = {}

            for spec in team.specs:
                trust_cfg: dict = {}
                if _trust_fn:
                    try:
                        trust_cfg = _trust_fn(spec.role_id)
                    except Exception as exc:
                        logger.debug("[TRUST] get_dispatch_config failed for %s: %s", spec.role_id, exc)

                # Block probationary roles (trust < 0.3); create a review card instead
                if trust_cfg.get("max_parallel", 1) == 0:
                    logger.warning(
                        "[TRUST] role=%s is PROBATIONARY (score=%.3f); skipping dispatch",
                        spec.role_id,
                        trust_cfg.get("trust_score", 0.0),
                    )
                    skipped_probationary.append(spec.role_id)
                    self._create_probationary_card(spec.role_id, trust_cfg, instance_id)
                    continue

                trust_cfgs[spec.role_id] = trust_cfg

                # Build per-role trust_kernel encoding the HITL mode from trust band
                tk_config = {"nova_trust": trust_cfg}
                trust_kernel = TrustKernelBase(config=tk_config)

                t = CoWorkerThread(
                    spec=spec,
                    instance_id=instance_id,
                    message_bus=bus,
                    trust_kernel=trust_kernel,
                )
                threads.append(t)

            if skipped_probationary:
                logger.info(
                    "ACE %s: %d probationary role(s) excluded: %s",
                    instance_id, len(skipped_probationary), skipped_probationary,
                )

            # Register threads + an abort event so abort() can both signal each
            # CoWorkerThread to stop AND wake this _run's join loop promptly.
            abort_event = threading.Event()
            with self._threads_lock:
                self._threads[instance_id] = threads
                self._abort_events[instance_id] = abort_event

            # Per-role trust semaphores: each role gets its OWN BoundedSemaphore
            # sized to that role's own max_parallel.  A single supervised role
            # (max_parallel=1) no longer serializes the whole team — a trusted
            # role's coworkers still run concurrently alongside it.
            #   supervised (band) → max_parallel=1 → that role runs sequentially
            #   trusted           → max_parallel=2 → that role runs parallel(2)
            #   autonomous        → max_parallel=4 → that role runs parallel(4)
            role_semaphores: dict[str, threading.BoundedSemaphore] = {
                role_id: threading.BoundedSemaphore(max(1, cfg.get("max_parallel", 1)))
                for role_id, cfg in trust_cfgs.items()
            }

            logger.info(
                "ACE %s: dispatching %d coworker(s) with per-role parallelism "
                "(max_parallel by role: %s)",
                instance_id,
                len(threads),
                {r: max(1, c.get("max_parallel", 1)) for r, c in trust_cfgs.items()},
            )

            # Start each CoWorkerThread directly — NOT via executor.submit()+join,
            # which would pin a ThreadPoolExecutor worker for the coworker's whole
            # lifetime and starve the shared pool.  Each thread's run() is wrapped
            # to acquire its role's semaphore before doing work and release it on
            # completion, so per-role concurrency is enforced while extra coworkers
            # of the same role simply block on the semaphore until a sibling frees
            # a slot.  If a thread was told to stop while blocked, it exits at once.
            for t in threads:
                sem = role_semaphores.get(t.spec.role_id)
                if sem is None:
                    sem = threading.BoundedSemaphore(1)
                    role_semaphores[t.spec.role_id] = sem
                t.run = self._make_guarded_run(t, sem)  # type: ignore[method-assign]
                t.start()

            # Await completion at the controller level with a bounded join loop
            # that honors abort.  abort() sets abort_event and signals each thread
            # to stop; semaphore-blocked threads drain quickly because the guarded
            # run checks the stop event immediately after acquiring.
            pending: list[CoWorkerThread] = list(threads)
            while pending:
                if abort_event.is_set():
                    for t in pending:
                        t.join(timeout=5.0)
                    break
                still_running: list[CoWorkerThread] = []
                for t in pending:
                    t.join(timeout=0.5)
                    if t.is_alive():
                        still_running.append(t)
                pending = still_running

            # 5. Record trust events based on each coworker's final DB state.
            # All futures have completed so all thread states are final in the DB.
            if _trust_fn:
                try:
                    from icdev.tools.db.storage import get_canvas_connection
                    conn = get_canvas_connection(_DB_ENV)
                    try:
                        for t in threads:
                            row = conn.execute(
                                "SELECT state FROM ace_coworkers WHERE id = %s",
                                (t.spec.coworker_id,),
                            ).fetchone()
                            state = (row[0] if isinstance(row, (list, tuple)) else row.get("state", "")) if row else ""
                            event = "success" if state == "done" else "failure"
                            self._record_trust_outcome(t.spec.role_id, event, instance_id)
                    finally:
                        conn.close()
                except Exception as exc:
                    logger.debug("[TRUST] post-dispatch trust recording failed: %s", exc)

            # Items 2+5: file session to wiki + cross-link role entries
            role_ids = [s.role_id for s in team.specs]
            self._file_session_to_wiki(instance_id, problem_text, role_ids)

            self._set_instance_state(instance_id, "complete")
            self._finalize_instance(instance_id)
            self._emit_completion_event(instance_id)
            self._emit_sse(instance_id, "complete", "All coworkers finished")
            self._emit_task_completed(instance_id)
            self._deliver_chat_result(instance_id, "complete")
            if webhook_url:
                self._deliver_webhook(instance_id, webhook_url)
            logger.info("ACE %s: complete", instance_id)

        except Exception as exc:
            logger.exception("ACE %s: fatal error: %s", instance_id, exc)
            self._set_instance_state(instance_id, "failed")
            self._emit_sse(instance_id, "failed", str(exc))
            # Tell the conversation. Silence after "spinning up a team" is the
            # worst outcome: the user cannot distinguish a crash from slow work.
            self._deliver_chat_result(instance_id, "failed")
        finally:
            with self._threads_lock:
                self._threads.pop(instance_id, None)
                self._abort_events.pop(instance_id, None)

    @staticmethod
    def _make_guarded_run(thread: Any, sem: "threading.BoundedSemaphore"):
        """Wrap ``thread.run`` so the coworker acquires *sem* before doing work
        and releases it on completion.

        This enforces per-role parallelism without pinning a ThreadPoolExecutor
        worker: extra coworkers of the same role block on the semaphore inside
        their own thread rather than occupying a shared pool slot.  A thread that
        was signalled to stop while blocked exits immediately after acquiring so
        aborts drain quickly.
        """
        orig_run = thread.run
        stop_event = getattr(thread, "_stop_event", None)

        def _guarded() -> None:
            sem.acquire()
            try:
                if stop_event is not None and stop_event.is_set():
                    return
                orig_run()
            finally:
                sem.release()

        return _guarded

    @staticmethod
    def _deliver_chat_result(instance_id: str, state: str) -> None:
        """Post the run's outcome back to the chat that started it.

        In-process rather than via the webhook: an outbound POST would mean the
        dashboard calling itself over loopback, which needs a reachable base URL
        and breaks in air-gapped and odd-port deployments. Webhooks remain for
        external consumers.

        Best-effort by design — a delivery failure must never change the outcome
        of a run that already finished.
        """
        try:
            from icdev.tools.ace.chat_result import deliver

            deliver(instance_id, state=state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chat result delivery skipped for %s: %s", instance_id, exc)

    def _finalize_instance(self, instance_id: str) -> None:
        """Extract facts from the final artifact and persist to ace_coworker_memory (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection, get_connection

            # --- read artifact + role from ACE canvas DB ---
            ace_conn = get_canvas_connection(_DB_ENV)
            try:
                inst_row = ace_conn.execute(
                    "SELECT role_id FROM ace_instances WHERE id = %s", (instance_id,)
                ).fetchone()
                if not inst_row:
                    return
                role_id = inst_row[0] or "unknown"

                art_row = ace_conn.execute(
                    "SELECT content_md FROM ace_artifacts"
                    " WHERE instance_id = %s ORDER BY created_at DESC LIMIT 1",
                    (instance_id,),
                ).fetchone()
            finally:
                ace_conn.close()

            if not art_row or not art_row[0]:
                return

            facts = _extract_facts(art_row[0])
            if not facts:
                return

            # --- write to NOVA DB (ace_coworker_memory) ---
            nova_conn = get_connection()
            try:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for fact in facts:
                    nova_conn.execute(
                        "INSERT INTO ace_coworker_memory"
                        " (id, role_id, fact_type, content, confidence, source_task_id, created_at)"
                        " VALUES (%s, %s, 'outcome', %s, 0.8, %s, %s)",
                        (uuid.uuid4().hex, role_id, fact, instance_id, now),
                    )
                # Cap at _MEMORY_CAP rows per role — delete oldest by created_at
                nova_conn.execute(
                    "DELETE FROM ace_coworker_memory"
                    " WHERE role_id = %s AND id NOT IN ("
                    "   SELECT id FROM ace_coworker_memory"
                    "   WHERE role_id = %s ORDER BY created_at DESC LIMIT %s"
                    ")",
                    (role_id, role_id, _MEMORY_CAP),
                )
                nova_conn.commit()
            finally:
                nova_conn.close()

            logger.info(
                "ACE %s: persisted %d memory fact(s) for role '%s'",
                instance_id, len(facts), role_id,
            )
        except Exception as exc:
            logger.debug("_finalize_instance(%s) failed: %s", instance_id, exc)

    def _set_instance_state(self, instance_id: str, state: str) -> None:
        """Update ace_instances.state (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "UPDATE ace_instances SET state = %s, updated_at = %s WHERE id = %s",
                    (state, now, instance_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("_set_instance_state(%s, %s) failed: %s", instance_id, state, exc)

    def _create_probationary_card(self, role_id: str, trust_cfg: dict, instance_id: str) -> None:
        """Create a HITL review card for a probationary coworker blocked from dispatch."""
        try:
            import uuid as _uuid
            from icdev.tools.db.storage import get_connection
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            task_id = f"ace-trust-review-{_uuid.uuid4().hex[:8]}"
            score = trust_cfg.get("trust_score", 0.0)
            conn = get_connection()
            conn.execute(
                """
                -- The column is dispatch_source; `source` has never existed on
                -- kanban_tasks, so this card was never created (swp-scan-01).
                INSERT INTO kanban_tasks
                    (id, title, description, status, priority, dispatch_source, created_at, updated_at)
                VALUES (%s, %s, %s, 'backlog', 'high', 'ace_trust', %s, %s)
                """,
                (
                    task_id,
                    f"[TRUST] Probationary coworker blocked: {role_id}",
                    (
                        f"**NOVA TRUST: Probationary Role Blocked from Dispatch**\n\n"
                        f"- Role: `{role_id}`\n"
                        f"- Trust score: `{score:.3f}` (threshold: 0.300)\n"
                        f"- Instance: `{instance_id}`\n\n"
                        f"This coworker has insufficient trust to dispatch autonomously. "
                        f"Review past task failures for this role, then manually record "
                        f"positive trust events to raise the score above 0.3:\n\n"
                        f"```python\n"
                        f"from tools.ace.trust_calibrator import record_trust_event\n"
                        f"record_trust_event('{role_id}', 'success')\n"
                        f"```"
                    ),
                    now,
                    now,
                ),
            )
            conn.commit()
            logger.info("[TRUST] Probationary card created for %s (score=%.3f)", role_id, score)
        except Exception as exc:
            logger.debug("[TRUST] _create_probationary_card failed: %s", exc)

    @staticmethod
    def _record_trust_outcome(role_id: str, event_type: str, instance_id: str) -> None:
        """Record a trust event for a completed/failed coworker dispatch (best-effort)."""
        try:
            from icdev.tools.ace.trust_calibrator import record_trust_event
            record_trust_event(role_id, event_type, source_task_id=instance_id)
        except Exception as exc:
            logger.debug("[TRUST] _record_trust_outcome failed for %s: %s", role_id, exc)

    @staticmethod
    def _emit_completion_event(instance_id: str) -> None:
        """Emit task.completed to ace_events so reflexion_loop reflex fires (best-effort)."""
        try:
            from icdev.tools.ace.event_bus import emit as ace_emit
            ace_emit(
                "task.completed",
                {"trigger": "instance_complete", "instance_id": instance_id},
                source_canvas="ace",
                source_id=instance_id,
            )
        except Exception:
            pass

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

    @staticmethod
    def _emit_task_completed(instance_id: str) -> None:
        """Publish task.completed to nova canvas so reflexion_loop can react (best-effort)."""
        try:
            from icdev.tools.canvas.event_bus import publish

            publish(
                source_canvas="ace",
                event_type="task.completed",
                payload_dict={"trigger": "instance_complete", "instance_id": instance_id},
                target_canvas="nova",
            )
        except Exception:
            pass  # event publish is best-effort; never crash the run

    @staticmethod
    def _persist_webhook_url(instance_id: str, webhook_url: str) -> None:
        """Store webhook_url on the ace_instances row (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "UPDATE ace_instances SET webhook_url = %s, updated_at = %s WHERE id = %s",
                    (webhook_url, now, instance_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("_persist_webhook_url(%s) failed: %s", instance_id, exc)

    @staticmethod
    def _deliver_webhook(instance_id: str, webhook_url: str) -> None:
        """POST completion payload to webhook_url (best-effort)."""
        try:
            from icdev.tools.ace.webhook import deliver

            payload = {
                "event": "ace.instance.complete",
                "instance_id": instance_id,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            result = deliver(instance_id, webhook_url, payload)
            logger.info(
                "ACE %s: webhook delivered status=%s attempts=%d",
                instance_id, result.get("status_code"), result.get("attempt_count"),
            )
        except Exception as exc:
            logger.debug("_deliver_webhook(%s) failed: %s", instance_id, exc)


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
