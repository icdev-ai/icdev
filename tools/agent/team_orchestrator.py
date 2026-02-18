# CUI // SP-CTI
"""DAG-based workflow engine for multi-agent task orchestration.

Decomposes high-level tasks into subtask DAGs using Bedrock LLM, then
executes them in parallel where dependencies allow using TopologicalSorter
and ThreadPoolExecutor.

Decision D36: ThreadPoolExecutor for parallel subtask dispatch.
Decision D40: graphlib.TopologicalSorter (Python 3.9+ stdlib) for DAG resolution.
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
HARDPROMPT_PATH = BASE_DIR / "hardprompts" / "agent" / "task_decomposition.md"
SCHEMA_PATH = BASE_DIR / "context" / "agent" / "response_schemas" / "task_decomposition.json"

logger = logging.getLogger("icdev.team_orchestrator")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Subtask:
    """A single unit of work assigned to one agent skill."""
    id: str
    agent_id: str
    skill_id: str
    description: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"  # pending, queued, working, completed, failed, canceled, blocked
    input_data: Optional[Dict] = None
    output_data: Optional[Dict] = None
    error_message: str = ""
    attempt_count: int = 0
    duration_ms: int = 0


@dataclass
class Workflow:
    """A collection of subtasks forming a directed acyclic graph."""
    id: str
    name: str
    project_id: str = ""
    subtasks: Dict[str, Subtask] = field(default_factory=dict)
    status: str = "pending"  # pending, running, completed, failed, partially_completed, canceled
    created_by: str = "orchestrator-agent"
    aggregated_result: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(db_path: Path = None):
    """Create agent_workflows and agent_subtasks tables if they do not exist."""
    conn = _get_db(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by TEXT DEFAULT 'orchestrator-agent',
                aggregated_result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_subtasks (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                description TEXT DEFAULT '',
                depends_on TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                input_data TEXT,
                output_data TEXT,
                error_message TEXT DEFAULT '',
                attempt_count INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workflow_id) REFERENCES agent_workflows(id)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _audit_log(event_type: str, actor: str, action: str,
               project_id: str = None, details: dict = None,
               db_path: Path = None):
    """Best-effort audit trail logging."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type=event_type,
            actor=actor,
            action=action,
            project_id=project_id,
            details=details,
            classification="CUI",
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("Audit logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# TeamOrchestrator
# ---------------------------------------------------------------------------
class TeamOrchestrator:
    """DAG-based workflow engine for multi-agent task orchestration.

    Usage::

        orchestrator = TeamOrchestrator(max_workers=5)
        workflow = orchestrator.decompose_task(
            "Build a REST API with auth and deploy to staging",
            project_id="proj-123",
        )
        workflow = orchestrator.execute_workflow(workflow, timeout=600)
        print(workflow.status, workflow.aggregated_result)
    """

    def __init__(self, max_workers: int = 5, db_path: Path = None):
        """Initialize the orchestrator.

        Args:
            max_workers: Maximum parallel subtask threads (Decision D36).
            db_path: Override database path (default: data/icdev.db).
        """
        self._max_workers = max_workers
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._bedrock_client = None
        self._agent_client = None
        _ensure_tables(self._db_path)

    # -------------------------------------------------------------------
    # Bedrock client (lazy init)
    # -------------------------------------------------------------------
    def _get_bedrock_client(self):
        """Return cached BedrockClient, creating if needed."""
        if self._bedrock_client is None:
            from tools.agent.bedrock_client import BedrockClient
            self._bedrock_client = BedrockClient(db_path=str(self._db_path))
        return self._bedrock_client

    # -------------------------------------------------------------------
    # A2A client (lazy init)
    # -------------------------------------------------------------------
    def _get_agent_client(self):
        """Return cached A2AAgentClient, creating if needed."""
        if self._agent_client is None:
            from tools.a2a.agent_client import A2AAgentClient
            self._agent_client = A2AAgentClient(verify_ssl=False)
        return self._agent_client

    # -------------------------------------------------------------------
    # Task decomposition
    # -------------------------------------------------------------------
    def decompose_task(
        self,
        task_description: str,
        project_id: str = "",
        agent_config: dict = None,
    ) -> Workflow:
        """Decompose a high-level task into a DAG of subtasks using Bedrock LLM.

        1. Loads the task_decomposition hardprompt and JSON schema.
        2. Invokes Bedrock with structured output for a DAG of subtasks.
        3. Falls back to a sequential single-subtask workflow if Bedrock
           is unavailable.

        Args:
            task_description: Natural language description of the task.
            project_id: Project identifier for tracking.
            agent_config: Optional override for available agents/skills.

        Returns:
            Workflow with populated subtask DAG.
        """
        workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
        workflow = Workflow(
            id=workflow_id,
            name=task_description[:120],
            project_id=project_id,
        )

        # Attempt LLM-based decomposition
        try:
            workflow = self._decompose_via_bedrock(
                workflow, task_description, project_id, agent_config,
            )
        except Exception as exc:
            logger.warning(
                "Bedrock decomposition failed (%s) — using fallback", exc,
            )
            workflow = self._decompose_fallback(
                workflow, task_description, project_id,
            )

        # Persist and audit
        self._persist_workflow(workflow)
        _audit_log(
            event_type="workflow_created",
            actor=workflow.created_by,
            action=f"Created workflow '{workflow.name}' with {len(workflow.subtasks)} subtask(s)",
            project_id=project_id,
            details={
                "workflow_id": workflow.id,
                "subtask_count": len(workflow.subtasks),
                "subtask_ids": list(workflow.subtasks.keys()),
            },
            db_path=self._db_path,
        )

        return workflow

    def _decompose_via_bedrock(
        self,
        workflow: Workflow,
        task_description: str,
        project_id: str,
        agent_config: dict = None,
    ) -> Workflow:
        """Use Bedrock to decompose the task into a subtask DAG."""
        from tools.agent.bedrock_client import BedrockClient, BedrockRequest

        client = self._get_bedrock_client()

        # Load hardprompt
        system_prompt = ""
        if HARDPROMPT_PATH.exists():
            system_prompt = HARDPROMPT_PATH.read_text(encoding="utf-8")
        else:
            system_prompt = (
                "You are a task decomposition engine for a multi-agent system. "
                "Given a high-level task, break it into subtasks that can be assigned "
                "to specialized agents. Each subtask must specify: id, agent_id, "
                "skill_id, description, and depends_on (list of subtask IDs that "
                "must complete first). Return valid JSON matching the provided schema."
            )

        # Load output schema
        output_schema = None
        if SCHEMA_PATH.exists():
            try:
                output_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError) as exc:
                logger.warning("Failed to load decomposition schema: %s", exc)

        if output_schema is None:
            output_schema = {
                "name": "task_decomposition",
                "schema": {
                    "type": "object",
                    "properties": {
                        "workflow_name": {"type": "string"},
                        "subtasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "agent_id": {"type": "string"},
                                    "skill_id": {"type": "string"},
                                    "description": {"type": "string"},
                                    "depends_on": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["id", "agent_id", "skill_id", "description"],
                            },
                        },
                    },
                    "required": ["workflow_name", "subtasks"],
                },
            }

        # Build context about available agents
        agent_context = ""
        if agent_config:
            agent_context = f"\n\nAvailable agents and skills:\n{json.dumps(agent_config, indent=2)}"
        else:
            try:
                from tools.a2a.agent_registry import discover_agents
                agents = discover_agents(db_path=self._db_path)
                if agents:
                    summary = []
                    for a in agents:
                        caps = a.get("capabilities", {})
                        skills = caps.get("skills", []) if isinstance(caps, dict) else []
                        skill_ids = [
                            s.get("id", s) if isinstance(s, dict) else s
                            for s in skills
                        ]
                        summary.append({
                            "agent_id": a["id"],
                            "name": a.get("name", ""),
                            "skills": skill_ids,
                        })
                    agent_context = (
                        f"\n\nAvailable agents and skills:\n"
                        f"{json.dumps(summary, indent=2)}"
                    )
            except Exception:
                pass

        user_message = (
            f"Decompose this task into subtasks for a multi-agent system.\n\n"
            f"Task: {task_description}\n"
            f"Project ID: {project_id}\n"
            f"{agent_context}"
        )

        request = BedrockRequest(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": user_message}]},
            ],
            system_prompt=system_prompt,
            agent_id="orchestrator-agent",
            project_id=project_id,
            model_preference="opus",
            effort="high",
            max_tokens=8192,
            output_schema=output_schema,
            classification="CUI",
        )

        response = client.invoke(request)

        # Parse structured output
        decomposition = response.structured_output
        if not decomposition and response.content:
            try:
                decomposition = json.loads(response.content)
            except json.JSONDecodeError:
                raise ValueError(
                    f"Bedrock returned non-JSON content: {response.content[:200]}"
                )

        if not decomposition or "subtasks" not in decomposition:
            raise ValueError("Bedrock decomposition missing 'subtasks' key")

        # Build subtask objects
        for st_data in decomposition["subtasks"]:
            st = Subtask(
                id=st_data.get("id", f"st-{uuid.uuid4().hex[:8]}"),
                agent_id=st_data.get("agent_id", "builder-agent"),
                skill_id=st_data.get("skill_id", "generate_code"),
                description=st_data.get("description", ""),
                depends_on=st_data.get("depends_on", []),
                input_data=st_data.get("input_data"),
            )
            workflow.subtasks[st.id] = st

        if decomposition.get("workflow_name"):
            workflow.name = decomposition["workflow_name"]

        return workflow

    def _decompose_fallback(
        self,
        workflow: Workflow,
        task_description: str,
        project_id: str,
    ) -> Workflow:
        """Fallback decomposition: create a single sequential subtask.

        Used when Bedrock is unavailable (air-gapped, rate-limited, etc.).
        """
        st = Subtask(
            id=f"st-{uuid.uuid4().hex[:8]}",
            agent_id="builder-agent",
            skill_id="generate_code",
            description=task_description,
            depends_on=[],
            input_data={"task": task_description, "project_id": project_id},
        )
        workflow.subtasks[st.id] = st
        logger.info(
            "Fallback decomposition: single subtask '%s' for workflow '%s'",
            st.id, workflow.id,
        )
        return workflow

    # -------------------------------------------------------------------
    # Workflow execution
    # -------------------------------------------------------------------
    def execute_workflow(self, workflow: Workflow, timeout: int = 600) -> Workflow:
        """Execute a workflow by resolving the DAG and running subtasks in parallel.

        Uses graphlib.TopologicalSorter (Decision D40) for dependency ordering
        and concurrent.futures.ThreadPoolExecutor (Decision D36) for parallelism.

        Args:
            workflow: Workflow with populated subtasks.
            timeout: Maximum seconds for the entire workflow.

        Returns:
            Updated workflow with subtask results and aggregated output.
        """
        workflow.status = "running"
        self._persist_workflow(workflow)
        start_time = time.time()

        # Build dependency graph for TopologicalSorter
        graph: Dict[str, set] = {}
        for st_id, st in workflow.subtasks.items():
            deps = set()
            for dep_id in st.depends_on:
                if dep_id in workflow.subtasks:
                    deps.add(dep_id)
                else:
                    logger.warning(
                        "Subtask '%s' depends on unknown '%s' — ignoring dependency",
                        st_id, dep_id,
                    )
            graph[st_id] = deps

        try:
            sorter = TopologicalSorter(graph)
            sorter.prepare()
        except Exception as exc:
            logger.error("DAG has a cycle or is invalid: %s", exc)
            workflow.status = "failed"
            self._persist_workflow(workflow)
            _audit_log(
                event_type="workflow_failed",
                actor=workflow.created_by,
                action=f"Workflow '{workflow.id}' failed: invalid DAG ({exc})",
                project_id=workflow.project_id,
                details={"workflow_id": workflow.id, "error": str(exc)},
                db_path=self._db_path,
            )
            return workflow

        completed_count = 0
        failed_count = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            while sorter.is_active():
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    logger.error(
                        "Workflow '%s' timed out after %.0fs", workflow.id, elapsed,
                    )
                    # Cancel remaining subtasks
                    for st_id, st in workflow.subtasks.items():
                        if st.status in ("pending", "queued"):
                            st.status = "canceled"
                            self._update_subtask_status(st, workflow.id)
                    workflow.status = "failed"
                    break

                # Get ready nodes (no unfinished dependencies)
                ready = sorter.get_ready()
                if not ready:
                    # All submitted, waiting for futures
                    time.sleep(0.1)
                    continue

                # Submit ready subtasks in parallel
                futures = {}
                for st_id in ready:
                    st = workflow.subtasks[st_id]
                    st.status = "queued"
                    self._update_subtask_status(st, workflow.id)

                    # Build context from completed dependencies
                    context = self._build_subtask_context(st, workflow)

                    _audit_log(
                        event_type="subtask_dispatched",
                        actor=workflow.created_by,
                        action=f"Dispatched subtask '{st_id}' to {st.agent_id}:{st.skill_id}",
                        project_id=workflow.project_id,
                        details={
                            "workflow_id": workflow.id,
                            "subtask_id": st_id,
                            "agent_id": st.agent_id,
                            "skill_id": st.skill_id,
                        },
                        db_path=self._db_path,
                    )

                    future = executor.submit(self._execute_subtask, st, context)
                    futures[future] = st_id

                # Wait for submitted subtasks to complete
                remaining_timeout = max(1, timeout - (time.time() - start_time))
                for future in as_completed(futures, timeout=remaining_timeout):
                    st_id = futures[future]
                    try:
                        completed_st = future.result()
                        workflow.subtasks[st_id] = completed_st

                        if completed_st.status == "completed":
                            completed_count += 1
                            sorter.done(st_id)
                            _audit_log(
                                event_type="subtask_completed",
                                actor=workflow.created_by,
                                action=f"Subtask '{st_id}' completed in {completed_st.duration_ms}ms",
                                project_id=workflow.project_id,
                                details={
                                    "workflow_id": workflow.id,
                                    "subtask_id": st_id,
                                    "duration_ms": completed_st.duration_ms,
                                },
                                db_path=self._db_path,
                            )
                        else:
                            failed_count += 1
                            sorter.done(st_id)
                            _audit_log(
                                event_type="subtask_failed",
                                actor=workflow.created_by,
                                action=f"Subtask '{st_id}' failed: {completed_st.error_message}",
                                project_id=workflow.project_id,
                                details={
                                    "workflow_id": workflow.id,
                                    "subtask_id": st_id,
                                    "error": completed_st.error_message,
                                    "attempt_count": completed_st.attempt_count,
                                },
                                db_path=self._db_path,
                            )
                            # Mark downstream subtasks as blocked
                            self._block_downstream(st_id, workflow)

                    except Exception as exc:
                        failed_count += 1
                        st = workflow.subtasks[st_id]
                        st.status = "failed"
                        st.error_message = str(exc)
                        self._update_subtask_status(st, workflow.id)
                        sorter.done(st_id)

                        _audit_log(
                            event_type="subtask_failed",
                            actor=workflow.created_by,
                            action=f"Subtask '{st_id}' raised exception: {exc}",
                            project_id=workflow.project_id,
                            details={
                                "workflow_id": workflow.id,
                                "subtask_id": st_id,
                                "error": str(exc),
                            },
                            db_path=self._db_path,
                        )
                        self._block_downstream(st_id, workflow)

        # Determine final workflow status
        total = len(workflow.subtasks)
        if failed_count == 0 and completed_count == total:
            workflow.status = "completed"
        elif completed_count > 0 and failed_count > 0:
            workflow.status = "partially_completed"
        elif failed_count > 0:
            workflow.status = "failed"
        else:
            # Some may be canceled/blocked
            workflow.status = "partially_completed" if completed_count > 0 else "failed"

        # Aggregate results
        workflow.aggregated_result = self._aggregate_results(workflow)
        self._persist_workflow(workflow)

        # Final audit
        _audit_log(
            event_type="workflow_completed" if workflow.status == "completed" else "workflow_failed",
            actor=workflow.created_by,
            action=(
                f"Workflow '{workflow.id}' {workflow.status}: "
                f"{completed_count}/{total} subtasks completed, {failed_count} failed"
            ),
            project_id=workflow.project_id,
            details={
                "workflow_id": workflow.id,
                "status": workflow.status,
                "completed": completed_count,
                "failed": failed_count,
                "total": total,
            },
            db_path=self._db_path,
        )

        return workflow

    def _build_subtask_context(self, subtask: Subtask, workflow: Workflow) -> Dict:
        """Build execution context from completed dependency outputs."""
        context = {
            "workflow_id": workflow.id,
            "project_id": workflow.project_id,
            "dependency_outputs": {},
        }
        for dep_id in subtask.depends_on:
            dep = workflow.subtasks.get(dep_id)
            if dep and dep.status == "completed" and dep.output_data:
                context["dependency_outputs"][dep_id] = dep.output_data
        return context

    def _execute_subtask(self, subtask: Subtask, context: Dict) -> Subtask:
        """Execute a single subtask by dispatching to the target agent.

        1. Looks up the target agent from agent_registry.
        2. Sends the task via A2AAgentClient.send_task().
        3. Waits for completion with timeout.
        4. Returns the updated subtask.

        Args:
            subtask: The subtask to execute.
            context: Execution context with dependency outputs.

        Returns:
            Updated Subtask with output_data, status, duration_ms.
        """
        subtask.status = "working"
        subtask.attempt_count += 1
        start_time = time.time()

        try:
            # Look up agent
            from tools.a2a.agent_registry import get_agent
            agent = get_agent(subtask.agent_id, db_path=self._db_path)

            if not agent:
                subtask.status = "failed"
                subtask.error_message = f"Agent '{subtask.agent_id}' not found in registry"
                subtask.duration_ms = int((time.time() - start_time) * 1000)
                self._update_subtask_status(subtask, context.get("workflow_id", ""))
                return subtask

            agent_url = agent.get("url", "")
            if not agent_url:
                subtask.status = "failed"
                subtask.error_message = f"Agent '{subtask.agent_id}' has no URL configured"
                subtask.duration_ms = int((time.time() - start_time) * 1000)
                self._update_subtask_status(subtask, context.get("workflow_id", ""))
                return subtask

            # Build input data
            input_data = subtask.input_data or {}
            input_data["description"] = subtask.description
            input_data["context"] = context

            # Send task via A2A
            client = self._get_agent_client()
            result = client.send_task(
                agent_url=agent_url,
                skill_id=subtask.skill_id,
                input_data=input_data,
                project_id=context.get("project_id", ""),
            )

            # If the task is async, wait for completion
            task_status = result.get("status", "")
            if task_status not in ("completed", "failed", "canceled"):
                task_id = result.get("id", "")
                if task_id:
                    result = client.wait_for_completion(
                        agent_url=agent_url,
                        task_id=task_id,
                        timeout=300,
                        poll_interval=2.0,
                    )

            # Map result to subtask
            final_status = result.get("status", "completed")
            if final_status == "completed":
                subtask.status = "completed"
                subtask.output_data = result.get("artifacts", result.get("output", result))
            elif final_status == "failed":
                subtask.status = "failed"
                subtask.error_message = result.get("error", "Agent returned failure")
            else:
                subtask.status = "failed"
                subtask.error_message = f"Unexpected agent status: {final_status}"

        except TimeoutError as exc:
            subtask.status = "failed"
            subtask.error_message = f"Timeout waiting for agent: {exc}"
        except ConnectionError as exc:
            subtask.status = "failed"
            subtask.error_message = f"Cannot reach agent '{subtask.agent_id}': {exc}"
        except Exception as exc:
            subtask.status = "failed"
            subtask.error_message = f"Subtask execution error: {exc}"
            logger.error(
                "Subtask '%s' failed with exception: %s", subtask.id, exc, exc_info=True,
            )

        subtask.duration_ms = int((time.time() - start_time) * 1000)
        self._update_subtask_status(subtask, context.get("workflow_id", ""))
        return subtask

    def _block_downstream(self, failed_id: str, workflow: Workflow):
        """Mark all subtasks that depend on the failed subtask as blocked."""
        for st_id, st in workflow.subtasks.items():
            if failed_id in st.depends_on and st.status in ("pending", "queued"):
                st.status = "blocked"
                self._update_subtask_status(st, workflow.id)
                logger.info(
                    "Subtask '%s' blocked due to failed dependency '%s'",
                    st_id, failed_id,
                )

    # -------------------------------------------------------------------
    # Result aggregation
    # -------------------------------------------------------------------
    def _aggregate_results(self, workflow: Workflow) -> Dict:
        """Aggregate outputs from all completed subtasks.

        Returns a summary dict with per-subtask results and overall stats.
        """
        results = {
            "workflow_id": workflow.id,
            "workflow_status": workflow.status,
            "subtask_results": {},
            "summary": {
                "total": len(workflow.subtasks),
                "completed": 0,
                "failed": 0,
                "blocked": 0,
                "canceled": 0,
                "total_duration_ms": 0,
            },
        }

        for st_id, st in workflow.subtasks.items():
            results["subtask_results"][st_id] = {
                "agent_id": st.agent_id,
                "skill_id": st.skill_id,
                "status": st.status,
                "duration_ms": st.duration_ms,
                "output_data": st.output_data,
                "error_message": st.error_message if st.error_message else None,
            }

            if st.status == "completed":
                results["summary"]["completed"] += 1
            elif st.status == "failed":
                results["summary"]["failed"] += 1
            elif st.status == "blocked":
                results["summary"]["blocked"] += 1
            elif st.status == "canceled":
                results["summary"]["canceled"] += 1

            results["summary"]["total_duration_ms"] += st.duration_ms

        return results

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------
    def _persist_workflow(self, workflow: Workflow):
        """Persist workflow and all subtasks to the database."""
        conn = _get_db(self._db_path)
        try:
            agg_json = json.dumps(workflow.aggregated_result) if workflow.aggregated_result else None

            conn.execute(
                """INSERT INTO agent_workflows (id, name, project_id, status, created_by, aggregated_result)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   status = excluded.status,
                   aggregated_result = excluded.aggregated_result,
                   updated_at = CURRENT_TIMESTAMP""",
                (
                    workflow.id,
                    workflow.name,
                    workflow.project_id,
                    workflow.status,
                    workflow.created_by,
                    agg_json,
                ),
            )

            for st_id, st in workflow.subtasks.items():
                conn.execute(
                    """INSERT INTO agent_subtasks
                       (id, workflow_id, agent_id, skill_id, description,
                        depends_on, status, input_data, output_data,
                        error_message, attempt_count, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status,
                       output_data = excluded.output_data,
                       error_message = excluded.error_message,
                       attempt_count = excluded.attempt_count,
                       duration_ms = excluded.duration_ms,
                       updated_at = CURRENT_TIMESTAMP""",
                    (
                        st.id,
                        workflow.id,
                        st.agent_id,
                        st.skill_id,
                        st.description,
                        json.dumps(st.depends_on),
                        st.status,
                        json.dumps(st.input_data) if st.input_data else None,
                        json.dumps(st.output_data) if st.output_data else None,
                        st.error_message,
                        st.attempt_count,
                        st.duration_ms,
                    ),
                )

            conn.commit()
        except Exception as exc:
            logger.error("Failed to persist workflow '%s': %s", workflow.id, exc)
        finally:
            conn.close()

    def _update_subtask_status(self, subtask: Subtask, workflow_id: str):
        """Update a single subtask's status in the database."""
        conn = _get_db(self._db_path)
        try:
            conn.execute(
                """UPDATE agent_subtasks
                   SET status = ?, output_data = ?, error_message = ?,
                       attempt_count = ?, duration_ms = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND workflow_id = ?""",
                (
                    subtask.status,
                    json.dumps(subtask.output_data) if subtask.output_data else None,
                    subtask.error_message,
                    subtask.attempt_count,
                    subtask.duration_ms,
                    subtask.id,
                    workflow_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Failed to update subtask status: %s", exc)
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Status query
    # -------------------------------------------------------------------
    def get_workflow_status(self, workflow_id: str) -> dict:
        """Retrieve workflow status and subtask details from the database.

        Args:
            workflow_id: The workflow identifier to look up.

        Returns:
            Dict with workflow metadata and subtask statuses,
            or empty dict if not found.
        """
        conn = _get_db(self._db_path)
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM agent_workflows WHERE id = ?", (workflow_id,))
            wf_row = c.fetchone()
            if not wf_row:
                return {}

            wf = dict(wf_row)
            if wf.get("aggregated_result"):
                try:
                    wf["aggregated_result"] = json.loads(wf["aggregated_result"])
                except json.JSONDecodeError:
                    pass

            c.execute(
                "SELECT * FROM agent_subtasks WHERE workflow_id = ? ORDER BY created_at",
                (workflow_id,),
            )
            subtasks = []
            for row in c.fetchall():
                st = dict(row)
                for json_field in ("depends_on", "input_data", "output_data"):
                    if st.get(json_field) and isinstance(st[json_field], str):
                        try:
                            st[json_field] = json.loads(st[json_field])
                        except json.JSONDecodeError:
                            pass
                subtasks.append(st)

            wf["subtasks"] = subtasks
            return wf
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI for workflow decomposition and execution."""
    parser = argparse.ArgumentParser(
        description="ICDEV Team Orchestrator — DAG-based multi-agent workflow engine"
    )
    parser.add_argument(
        "--decompose",
        help="Task description to decompose into subtasks",
    )
    parser.add_argument(
        "--execute",
        help="Workflow ID to execute (must have been previously decomposed)",
    )
    parser.add_argument(
        "--status",
        help="Get status of a workflow by ID",
    )
    parser.add_argument("--project-id", default="", help="Project ID for tracking")
    parser.add_argument(
        "--max-workers", type=int, default=5,
        help="Max parallel subtask threads (default: 5)",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Workflow execution timeout in seconds (default: 600)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", help="Override database path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = Path(args.db_path) if args.db_path else None
    orchestrator = TeamOrchestrator(
        max_workers=args.max_workers,
        db_path=db_path,
    )

    if args.decompose:
        workflow = orchestrator.decompose_task(
            task_description=args.decompose,
            project_id=args.project_id,
        )

        if args.json:
            output = {
                "workflow_id": workflow.id,
                "name": workflow.name,
                "project_id": workflow.project_id,
                "status": workflow.status,
                "subtask_count": len(workflow.subtasks),
                "subtasks": {
                    st_id: asdict(st)
                    for st_id, st in workflow.subtasks.items()
                },
                "classification": "CUI",
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            print(f"Workflow: {workflow.id}")
            print(f"Name: {workflow.name}")
            print(f"Project: {workflow.project_id}")
            print(f"Subtasks: {len(workflow.subtasks)}")
            print(f"Classification: CUI // SP-CTI")
            print()
            for st_id, st in workflow.subtasks.items():
                deps = ", ".join(st.depends_on) if st.depends_on else "(none)"
                print(f"  [{st_id}] {st.agent_id}:{st.skill_id}")
                print(f"    Description: {st.description}")
                print(f"    Depends on: {deps}")
                print()

    elif args.execute:
        # Load workflow from DB
        status = orchestrator.get_workflow_status(args.execute)
        if not status:
            print(f"Workflow not found: {args.execute}", file=sys.stderr)
            sys.exit(1)

        # Reconstruct Workflow object from DB
        workflow = Workflow(
            id=status["id"],
            name=status["name"],
            project_id=status.get("project_id", ""),
            status=status.get("status", "pending"),
            created_by=status.get("created_by", "orchestrator-agent"),
        )
        for st_data in status.get("subtasks", []):
            st = Subtask(
                id=st_data["id"],
                agent_id=st_data["agent_id"],
                skill_id=st_data["skill_id"],
                description=st_data.get("description", ""),
                depends_on=st_data.get("depends_on", []),
                status="pending",  # Reset for re-execution
                input_data=st_data.get("input_data"),
            )
            workflow.subtasks[st.id] = st

        workflow = orchestrator.execute_workflow(workflow, timeout=args.timeout)

        if args.json:
            print(json.dumps(workflow.aggregated_result or {}, indent=2, default=str))
        else:
            print(f"Workflow: {workflow.id}")
            print(f"Status: {workflow.status}")
            print(f"Classification: CUI // SP-CTI")
            if workflow.aggregated_result:
                summary = workflow.aggregated_result.get("summary", {})
                print(f"Completed: {summary.get('completed', 0)}/{summary.get('total', 0)}")
                print(f"Failed: {summary.get('failed', 0)}")
                print(f"Duration: {summary.get('total_duration_ms', 0)}ms")

    elif args.status:
        status = orchestrator.get_workflow_status(args.status)
        if not status:
            print(f"Workflow not found: {args.status}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(status, indent=2, default=str))
        else:
            print(f"Workflow: {status['id']}")
            print(f"Name: {status['name']}")
            print(f"Status: {status['status']}")
            print(f"Project: {status.get('project_id', 'N/A')}")
            print(f"Classification: CUI // SP-CTI")
            print()
            for st in status.get("subtasks", []):
                print(f"  [{st['id']}] {st['agent_id']}:{st['skill_id']} -> {st['status']}")
                if st.get("error_message"):
                    print(f"    Error: {st['error_message']}")
                if st.get("duration_ms"):
                    print(f"    Duration: {st['duration_ms']}ms")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
