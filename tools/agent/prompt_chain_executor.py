#!/usr/bin/env python3
# CUI // SP-CTI
"""Declarative prompt-chain executor for LLM-to-LLM sequential reasoning.

Loads chain definitions from args/prompt_chains.yaml, executes steps
sequentially with variable substitution, and records results in the
prompt_chain_executions DB table.

Decision D-PC-1: YAML-driven prompt chains (D26 pattern) — add new chains without code changes.
Decision D-PC-2: LLM reasoning chains via LLMRouter.invoke(), not A2A tool dispatch.
Decision D-PC-3: Sequential execution only — no DAG parallelism (that is team_orchestrator.py).
"""

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
CHAINS_PATH = BASE_DIR / "args" / "prompt_chains.yaml"

logger = logging.getLogger("icdev.prompt_chain_executor")

# ---------------------------------------------------------------------------
# Agent-to-function mapping for LLMRouter
# ---------------------------------------------------------------------------
# Maps the short agent name used in prompt_chains.yaml to the ICDEV
# LLMRouter function name that governs model selection for that agent.
AGENT_FUNCTION_MAP = {
    "orchestrator": "task_decomposition",
    "architect": "agent_architect",
    "builder": "code_generation",
    "compliance": "compliance_export",
    "security": "code_review",
    "knowledge": "code_review",
    "monitor": "agent_monitor",
    "infra": "code_generation",
    "mbse": "code_generation",
    "modernization": "code_generation",
    "requirements_analyst": "intake_persona_response",
    "supply_chain": "code_review",
    "simulation": "code_generation",
    "devsecops": "code_review",
}

# Valid agent names (from args/agent_config.yaml)
VALID_AGENTS = set(AGENT_FUNCTION_MAP.keys())


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ChainStep:
    """A single step in a prompt chain."""
    step_id: str
    agent: str
    prompt: str
    timeout_s: int = 120


@dataclass
class ChainDefinition:
    """A prompt chain template loaded from YAML."""
    name: str
    description: str = ""
    max_iterations: int = 1
    steps: List[ChainStep] = field(default_factory=list)


@dataclass
class StepResult:
    """Result of executing a single chain step."""
    step_id: str
    agent: str
    output: str = ""
    output_hash: str = ""
    duration_ms: int = 0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    status: str = "pending"  # pending, running, completed, failed


@dataclass
class ChainExecution:
    """Full execution state of a prompt chain."""
    id: str
    project_id: str
    chain_name: str
    original_input: str
    original_input_hash: str
    status: str = "running"
    steps_completed: int = 0
    steps_total: int = 0
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    final_output: str = ""
    final_output_hash: str = ""
    total_duration_ms: int = 0
    total_tokens_used: int = 0
    error_message: str = ""
    executed_by: str = ""
    created_at: str = ""
    completed_at: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    """Current UTC timestamp as ISO string."""
    from tools.compat.datetime_utils import utc_now_iso
    try:
        return utc_now_iso()
    except Exception:
        import datetime
        return datetime.datetime.utcnow().isoformat() + "Z"


def _sha256(text: str) -> str:
    """SHA-256 hash of text for privacy-preserving audit (D216 pattern)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit(event_type: str, actor: str, action: str,
           project_id: str = None, details: dict = None,
           db_path: Path = None):
    """Best-effort audit trail logging (D6 append-only)."""
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


def _ensure_table(db_path: Path = None):
    """Create prompt_chain_executions table if it does not exist."""
    conn = _get_db(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_chain_executions (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                chain_name TEXT NOT NULL,
                original_input TEXT NOT NULL,
                original_input_hash TEXT NOT NULL,
                status TEXT DEFAULT 'running'
                    CHECK(status IN ('running','completed','failed','cancelled')),
                steps_completed INTEGER DEFAULT 0,
                steps_total INTEGER NOT NULL,
                step_results TEXT DEFAULT '{}',
                final_output TEXT,
                final_output_hash TEXT,
                total_duration_ms INTEGER,
                total_tokens_used INTEGER DEFAULT 0,
                error_message TEXT,
                executed_by TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chain_exec_project
            ON prompt_chain_executions(project_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chain_exec_chain
            ON prompt_chain_executions(chain_name)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chain_exec_status
            ON prompt_chain_executions(status)
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------
def substitute_variables(
    template: str,
    current_input: str,
    original_input: str,
    step_outputs: Dict[str, str],
) -> str:
    """Replace $INPUT, $ORIGINAL, and $STEP{step_id} in a prompt template.

    Args:
        template: The prompt template with variable placeholders.
        current_input: Output from the previous step ($INPUT).
        original_input: The initial user input ($ORIGINAL).
        step_outputs: Dict mapping step_id -> output text for $STEP{x} refs.

    Returns:
        Prompt with all variables substituted.
    """
    result = template.replace("$INPUT", current_input)
    result = result.replace("$ORIGINAL", original_input)

    # Replace $STEP{step_id} references
    def _step_replacer(match):
        step_id = match.group(1)
        return step_outputs.get(step_id, f"[MISSING: step '{step_id}' not found]")

    result = re.sub(r'\$STEP\{([^}]+)\}', _step_replacer, result)
    return result


# ---------------------------------------------------------------------------
# Chain loader
# ---------------------------------------------------------------------------
def load_chains(config_path: Path = None) -> Dict[str, ChainDefinition]:
    """Load prompt chain definitions from YAML config.

    Args:
        config_path: Path to prompt_chains.yaml. Defaults to args/prompt_chains.yaml.

    Returns:
        Dict mapping chain name to ChainDefinition.
    """
    path = config_path or CHAINS_PATH
    if yaml is None:
        logger.warning("PyYAML not available — cannot load prompt chains")
        return {}
    if not path.exists():
        logger.warning("Prompt chains config not found at %s", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to load prompt chains config: %s", exc)
        return {}

    defaults = raw.get("defaults", {})
    default_timeout = defaults.get("timeout_s", 120)

    chains = {}
    for name, chain_data in raw.get("prompt_chains", {}).items():
        steps = []
        for step_data in chain_data.get("steps", []):
            step = ChainStep(
                step_id=step_data.get("step_id", f"step-{len(steps)}"),
                agent=step_data.get("agent", "builder"),
                prompt=step_data.get("prompt", ""),
                timeout_s=step_data.get("timeout_s", default_timeout),
            )
            steps.append(step)

        chain = ChainDefinition(
            name=name,
            description=chain_data.get("description", ""),
            max_iterations=chain_data.get("max_iterations", 1),
            steps=steps,
        )
        chains[name] = chain

    logger.info("Loaded %d prompt chain definitions", len(chains))
    return chains


# ---------------------------------------------------------------------------
# Chain validation
# ---------------------------------------------------------------------------
def validate_chain(chain: ChainDefinition) -> List[str]:
    """Validate a chain definition for structural correctness.

    Returns:
        List of error messages (empty = valid).
    """
    errors = []

    if not chain.steps:
        errors.append(f"Chain '{chain.name}' has no steps")
        return errors

    step_ids = set()
    for i, step in enumerate(chain.steps):
        # Duplicate step_id check
        if step.step_id in step_ids:
            errors.append(
                f"Chain '{chain.name}': duplicate step_id '{step.step_id}'"
            )
        step_ids.add(step.step_id)

        # Valid agent check
        if step.agent not in VALID_AGENTS:
            errors.append(
                f"Chain '{chain.name}', step '{step.step_id}': "
                f"unknown agent '{step.agent}' "
                f"(valid: {', '.join(sorted(VALID_AGENTS))})"
            )

        # Empty prompt check
        if not step.prompt.strip():
            errors.append(
                f"Chain '{chain.name}', step '{step.step_id}': empty prompt"
            )

        # $STEP{x} references must point to earlier steps
        refs = re.findall(r'\$STEP\{([^}]+)\}', step.prompt)
        earlier_ids = {s.step_id for s in chain.steps[:i]}
        for ref in refs:
            if ref not in earlier_ids:
                errors.append(
                    f"Chain '{chain.name}', step '{step.step_id}': "
                    f"$STEP{{{ref}}} references unknown or later step"
                )

        # Timeout sanity
        if step.timeout_s <= 0:
            errors.append(
                f"Chain '{chain.name}', step '{step.step_id}': "
                f"timeout_s must be positive, got {step.timeout_s}"
            )

    return errors


# ---------------------------------------------------------------------------
# PromptChainExecutor
# ---------------------------------------------------------------------------
class PromptChainExecutor:
    """Execute declarative prompt chains using LLMRouter.

    Usage::

        executor = PromptChainExecutor()
        result = executor.execute_chain(
            chain_name="plan_critique_refine",
            user_input="Add user auth module",
            project_id="proj-123",
        )
        print(result.status, result.final_output)
    """

    def __init__(self, db_path: Path = None, config_path: Path = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._config_path = Path(config_path) if config_path else CHAINS_PATH
        self._chains: Dict[str, ChainDefinition] = {}
        self._llm_router = None
        _ensure_table(self._db_path)
        self._load_chains()

    def _load_chains(self):
        """Load chain definitions from YAML."""
        self._chains = load_chains(self._config_path)

    def _get_llm_router(self):
        """Lazy-init LLMRouter (D-PC-2: LLM reasoning, not tool dispatch)."""
        if self._llm_router is None:
            from tools.llm.router import LLMRouter
            self._llm_router = LLMRouter()
        return self._llm_router

    def list_chains(self) -> List[Dict[str, Any]]:
        """List all available prompt chain definitions.

        Returns:
            List of chain summary dicts.
        """
        result = []
        for name, chain in self._chains.items():
            errors = validate_chain(chain)
            result.append({
                "name": name,
                "description": chain.description,
                "steps": len(chain.steps),
                "max_iterations": chain.max_iterations,
                "step_ids": [s.step_id for s in chain.steps],
                "agents": [s.agent for s in chain.steps],
                "valid": len(errors) == 0,
                "errors": errors,
            })
        return result

    def dry_run(
        self,
        chain_name: str,
        user_input: str,
    ) -> Dict[str, Any]:
        """Show what would execute without calling LLM.

        Args:
            chain_name: Name of the chain to dry-run.
            user_input: The initial user input.

        Returns:
            Dict with chain metadata and resolved prompts.
        """
        chain = self._chains.get(chain_name)
        if not chain:
            return {"error": f"Chain '{chain_name}' not found"}

        errors = validate_chain(chain)
        if errors:
            return {"error": "Chain validation failed", "validation_errors": errors}

        steps_preview = []
        prev_output = user_input
        step_outputs: Dict[str, str] = {}

        for step in chain.steps:
            resolved_prompt = substitute_variables(
                step.prompt, prev_output, user_input, step_outputs,
            )
            function = AGENT_FUNCTION_MAP.get(step.agent, "default")
            steps_preview.append({
                "step_id": step.step_id,
                "agent": step.agent,
                "llm_function": function,
                "timeout_s": step.timeout_s,
                "resolved_prompt_preview": resolved_prompt[:500] + (
                    "..." if len(resolved_prompt) > 500 else ""
                ),
            })
            # For dry-run, simulate output as placeholder
            placeholder = f"[DRY RUN: output of step '{step.step_id}' by agent '{step.agent}']"
            step_outputs[step.step_id] = placeholder
            prev_output = placeholder

        return {
            "chain_name": chain_name,
            "description": chain.description,
            "dry_run": True,
            "steps_count": len(chain.steps),
            "steps": steps_preview,
            "original_input_hash": _sha256(user_input),
        }

    def execute_chain(
        self,
        chain_name: str,
        user_input: str,
        project_id: str = "",
        executed_by: str = "orchestrator-agent",
    ) -> ChainExecution:
        """Execute a prompt chain end-to-end.

        Each step calls LLMRouter.invoke() with the agent's function mapping.
        The output of each step is substituted into the next step's prompt.

        Args:
            chain_name: Name of the chain from prompt_chains.yaml.
            user_input: The initial user input to process.
            project_id: Project identifier for tracking.
            executed_by: Actor identifier for audit trail.

        Returns:
            ChainExecution with all step results and final output.

        Raises:
            ValueError: If chain not found or validation fails.
        """
        chain = self._chains.get(chain_name)
        if not chain:
            raise ValueError(f"Chain '{chain_name}' not found in config")

        errors = validate_chain(chain)
        if errors:
            raise ValueError(
                f"Chain '{chain_name}' validation failed: {'; '.join(errors)}"
            )

        # Initialize execution record
        exec_id = f"pce-{uuid.uuid4().hex[:12]}"
        now = _now()
        execution = ChainExecution(
            id=exec_id,
            project_id=project_id,
            chain_name=chain_name,
            original_input=user_input,
            original_input_hash=_sha256(user_input),
            status="running",
            steps_total=len(chain.steps),
            executed_by=executed_by,
            created_at=now,
        )

        # Persist initial state
        self._persist_execution(execution)

        _audit(
            event_type="prompt_chain.started",
            actor=executed_by,
            action=f"Started prompt chain '{chain_name}' with {len(chain.steps)} steps",
            project_id=project_id,
            details={
                "execution_id": exec_id,
                "chain_name": chain_name,
                "input_hash": execution.original_input_hash,
                "steps_total": len(chain.steps),
            },
            db_path=self._db_path,
        )

        start_time = time.time()
        prev_output = user_input
        step_outputs: Dict[str, str] = {}

        for step in chain.steps:
            step_result = self._execute_step(
                step=step,
                current_input=prev_output,
                original_input=user_input,
                step_outputs=step_outputs,
                project_id=project_id,
                executed_by=executed_by,
            )

            execution.step_results[step.step_id] = step_result
            execution.total_tokens_used += (
                step_result.input_tokens + step_result.output_tokens
            )

            if step_result.status == "completed":
                execution.steps_completed += 1
                step_outputs[step.step_id] = step_result.output
                prev_output = step_result.output
            else:
                # Step failed — abort chain
                execution.status = "failed"
                execution.error_message = (
                    f"Step '{step.step_id}' failed: {step_result.error}"
                )
                break

            # Update intermediate state
            self._persist_execution(execution)

        # Finalize
        execution.total_duration_ms = int((time.time() - start_time) * 1000)
        execution.completed_at = _now()

        if execution.status != "failed":
            execution.status = "completed"
            execution.final_output = prev_output
            execution.final_output_hash = _sha256(prev_output)

        self._persist_execution(execution)

        _audit(
            event_type=(
                "prompt_chain.completed"
                if execution.status == "completed"
                else "prompt_chain.failed"
            ),
            actor=executed_by,
            action=(
                f"Prompt chain '{chain_name}' {execution.status}: "
                f"{execution.steps_completed}/{execution.steps_total} steps, "
                f"{execution.total_duration_ms}ms"
            ),
            project_id=project_id,
            details={
                "execution_id": exec_id,
                "chain_name": chain_name,
                "status": execution.status,
                "steps_completed": execution.steps_completed,
                "steps_total": execution.steps_total,
                "total_duration_ms": execution.total_duration_ms,
                "total_tokens_used": execution.total_tokens_used,
                "final_output_hash": execution.final_output_hash,
                "error": execution.error_message or None,
            },
            db_path=self._db_path,
        )

        return execution

    def _execute_step(
        self,
        step: ChainStep,
        current_input: str,
        original_input: str,
        step_outputs: Dict[str, str],
        project_id: str,
        executed_by: str,
    ) -> StepResult:
        """Execute a single chain step via LLMRouter.

        Args:
            step: The chain step definition.
            current_input: Output from the previous step.
            original_input: The initial user input.
            step_outputs: All completed step outputs so far.
            project_id: Project identifier.
            executed_by: Actor identifier.

        Returns:
            StepResult with output and metadata.
        """
        result = StepResult(
            step_id=step.step_id,
            agent=step.agent,
            status="running",
        )

        # Resolve prompt variables
        resolved_prompt = substitute_variables(
            step.prompt, current_input, original_input, step_outputs,
        )

        # Map agent to LLM function
        function = AGENT_FUNCTION_MAP.get(step.agent, "default")

        start_time = time.time()

        try:
            from tools.llm.provider import LLMRequest

            router = self._get_llm_router()

            llm_request = LLMRequest(
                messages=[
                    {"role": "user", "content": resolved_prompt},
                ],
                system_prompt=(
                    f"You are the {step.agent} agent in an ICDEV multi-agent system. "
                    f"Provide your expert analysis from the {step.agent} perspective."
                ),
                agent_id=f"{step.agent}-agent",
                project_id=project_id,
                effort="high",
                max_tokens=8192,
                classification="CUI",
            )

            response = router.invoke(function, llm_request)

            result.output = response.content or ""
            result.output_hash = _sha256(result.output)
            result.model = response.model_id or ""
            result.input_tokens = response.input_tokens
            result.output_tokens = response.output_tokens
            result.status = "completed"

        except Exception as exc:
            result.error = str(exc)
            result.status = "failed"
            logger.error(
                "Chain step '%s' (agent=%s) failed: %s",
                step.step_id, step.agent, exc,
            )

        result.duration_ms = int((time.time() - start_time) * 1000)

        # Audit each step
        _audit(
            event_type=f"prompt_chain.step.{result.status}",
            actor=executed_by,
            action=(
                f"Step '{step.step_id}' ({step.agent}): {result.status} "
                f"in {result.duration_ms}ms"
            ),
            project_id=project_id,
            details={
                "step_id": step.step_id,
                "agent": step.agent,
                "function": function,
                "status": result.status,
                "duration_ms": result.duration_ms,
                "model": result.model,
                "output_hash": result.output_hash,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "error": result.error or None,
            },
            db_path=self._db_path,
        )

        return result

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------
    def _persist_execution(self, execution: ChainExecution):
        """Persist or update execution state in the database."""
        conn = _get_db(self._db_path)
        try:
            # Serialize step_results
            step_results_json = {}
            for sid, sr in execution.step_results.items():
                step_results_json[sid] = {
                    "output_hash": sr.output_hash,
                    "agent": sr.agent,
                    "duration_ms": sr.duration_ms,
                    "model": sr.model,
                    "input_tokens": sr.input_tokens,
                    "output_tokens": sr.output_tokens,
                    "status": sr.status,
                    "error": sr.error or None,
                }

            conn.execute(
                """INSERT INTO prompt_chain_executions
                   (id, project_id, chain_name, original_input,
                    original_input_hash, status, steps_completed,
                    steps_total, step_results, final_output,
                    final_output_hash, total_duration_ms,
                    total_tokens_used, error_message, executed_by,
                    created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   status = excluded.status,
                   steps_completed = excluded.steps_completed,
                   step_results = excluded.step_results,
                   final_output = excluded.final_output,
                   final_output_hash = excluded.final_output_hash,
                   total_duration_ms = excluded.total_duration_ms,
                   total_tokens_used = excluded.total_tokens_used,
                   error_message = excluded.error_message,
                   completed_at = excluded.completed_at""",
                (
                    execution.id,
                    execution.project_id,
                    execution.chain_name,
                    execution.original_input,
                    execution.original_input_hash,
                    execution.status,
                    execution.steps_completed,
                    execution.steps_total,
                    json.dumps(step_results_json),
                    execution.final_output or None,
                    execution.final_output_hash or None,
                    execution.total_duration_ms,
                    execution.total_tokens_used,
                    execution.error_message or None,
                    execution.executed_by,
                    execution.created_at,
                    execution.completed_at or None,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.error("Failed to persist chain execution '%s': %s", execution.id, exc)
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # History query
    # -------------------------------------------------------------------
    def get_history(
        self,
        project_id: str = None,
        chain_name: str = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query execution history from the database.

        Args:
            project_id: Filter by project (optional).
            chain_name: Filter by chain name (optional).
            limit: Max results to return.

        Returns:
            List of execution summary dicts.
        """
        conn = _get_db(self._db_path)
        try:
            query = "SELECT * FROM prompt_chain_executions WHERE 1=1"
            params: List[Any] = []

            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if chain_name:
                query += " AND chain_name = ?"
                params.append(chain_name)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                entry = dict(row)
                # Parse step_results JSON
                if entry.get("step_results") and isinstance(entry["step_results"], str):
                    try:
                        entry["step_results"] = json.loads(entry["step_results"])
                    except json.JSONDecodeError:
                        pass
                results.append(entry)
            return results
        finally:
            conn.close()

    def get_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get a single execution by ID.

        Args:
            execution_id: The execution ID to look up.

        Returns:
            Execution dict or None if not found.
        """
        conn = _get_db(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM prompt_chain_executions WHERE id = ?",
                (execution_id,),
            ).fetchone()
            if not row:
                return None
            entry = dict(row)
            if entry.get("step_results") and isinstance(entry["step_results"], str):
                try:
                    entry["step_results"] = json.loads(entry["step_results"])
                except json.JSONDecodeError:
                    pass
            return entry
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI for prompt chain execution."""
    parser = argparse.ArgumentParser(
        description="ICDEV Prompt Chain Executor — declarative LLM-to-LLM reasoning chains"
    )
    parser.add_argument(
        "--chain",
        help="Name of the prompt chain to execute",
    )
    parser.add_argument(
        "--input",
        help="User input to process through the chain",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available prompt chains",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would execute without calling LLM",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show execution history",
    )
    parser.add_argument(
        "--execution-id",
        help="Get details for a specific execution",
    )
    parser.add_argument("--project-id", default="", help="Project ID for tracking")
    parser.add_argument("--executed-by", default="orchestrator-agent", help="Actor ID")
    parser.add_argument("--limit", type=int, default=20, help="History result limit")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--human", action="store_true", help="Output as formatted text")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--config-path", help="Override prompt chains config path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = Path(args.db_path) if args.db_path else None
    config_path = Path(args.config_path) if args.config_path else None

    executor = PromptChainExecutor(db_path=db_path, config_path=config_path)

    if args.list:
        chains = executor.list_chains()
        if args.json:
            print(json.dumps({
                "chains": chains,
                "count": len(chains),
                "classification": "CUI",
            }, indent=2))
        else:
            print("Available Prompt Chains")
            print("Classification: CUI // SP-CTI")
            print("=" * 60)
            for c in chains:
                status = "VALID" if c["valid"] else "INVALID"
                print(f"\n  {c['name']} [{status}]")
                print(f"    {c['description']}")
                print(f"    Steps: {c['steps']} | Agents: {', '.join(c['agents'])}")
                if c["errors"]:
                    for err in c["errors"]:
                        print(f"    ERROR: {err}")

    elif args.chain and args.dry_run:
        if not args.input:
            print("Error: --input required for --dry-run", file=sys.stderr)
            sys.exit(1)
        result = executor.dry_run(args.chain, args.input)
        if args.json:
            result["classification"] = "CUI"
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(f"Error: {result['error']}", file=sys.stderr)
                if "validation_errors" in result:
                    for err in result["validation_errors"]:
                        print(f"  - {err}", file=sys.stderr)
                sys.exit(1)
            print(f"Dry Run: {result['chain_name']}")
            print(f"Description: {result['description']}")
            print(f"Steps: {result['steps_count']}")
            print("Classification: CUI // SP-CTI")
            print()
            for step in result["steps"]:
                print(f"  Step: {step['step_id']}")
                print(f"    Agent: {step['agent']} -> {step['llm_function']}")
                print(f"    Timeout: {step['timeout_s']}s")
                print(f"    Prompt preview: {step['resolved_prompt_preview'][:200]}")
                print()

    elif args.chain and args.input:
        try:
            execution = executor.execute_chain(
                chain_name=args.chain,
                user_input=args.input,
                project_id=args.project_id,
                executed_by=args.executed_by,
            )
            if args.json:
                output = {
                    "execution_id": execution.id,
                    "chain_name": execution.chain_name,
                    "status": execution.status,
                    "steps_completed": execution.steps_completed,
                    "steps_total": execution.steps_total,
                    "total_duration_ms": execution.total_duration_ms,
                    "total_tokens_used": execution.total_tokens_used,
                    "final_output": execution.final_output,
                    "final_output_hash": execution.final_output_hash,
                    "error_message": execution.error_message or None,
                    "step_results": {
                        sid: {
                            "agent": sr.agent,
                            "status": sr.status,
                            "duration_ms": sr.duration_ms,
                            "model": sr.model,
                            "output_hash": sr.output_hash,
                            "input_tokens": sr.input_tokens,
                            "output_tokens": sr.output_tokens,
                            "error": sr.error or None,
                        }
                        for sid, sr in execution.step_results.items()
                    },
                    "classification": "CUI",
                }
                print(json.dumps(output, indent=2))
            else:
                print(f"Execution: {execution.id}")
                print(f"Chain: {execution.chain_name}")
                print(f"Status: {execution.status}")
                print(f"Steps: {execution.steps_completed}/{execution.steps_total}")
                print(f"Duration: {execution.total_duration_ms}ms")
                print(f"Tokens: {execution.total_tokens_used}")
                print("Classification: CUI // SP-CTI")
                if execution.error_message:
                    print(f"Error: {execution.error_message}")
                print()
                for sid, sr in execution.step_results.items():
                    print(f"  [{sid}] {sr.agent} -> {sr.status} ({sr.duration_ms}ms)")
                    if sr.error:
                        print(f"    Error: {sr.error}")
                if execution.final_output:
                    print(f"\nFinal Output:\n{execution.final_output[:2000]}")
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif args.history:
        results = executor.get_history(
            project_id=args.project_id or None,
            chain_name=args.chain or None,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps({
                "executions": results,
                "count": len(results),
                "classification": "CUI",
            }, indent=2, default=str))
        else:
            print("Prompt Chain Execution History")
            print("Classification: CUI // SP-CTI")
            print("=" * 60)
            for entry in results:
                print(f"\n  {entry['id']} | {entry['chain_name']} | {entry['status']}")
                print(f"    Steps: {entry['steps_completed']}/{entry['steps_total']}")
                print(f"    Duration: {entry.get('total_duration_ms', 0)}ms")
                print(f"    Created: {entry['created_at']}")

    elif args.execution_id:
        entry = executor.get_execution(args.execution_id)
        if not entry:
            print(f"Execution not found: {args.execution_id}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            entry["classification"] = "CUI"
            print(json.dumps(entry, indent=2, default=str))
        else:
            print(f"Execution: {entry['id']}")
            print(f"Chain: {entry['chain_name']}")
            print(f"Status: {entry['status']}")
            print(f"Steps: {entry['steps_completed']}/{entry['steps_total']}")
            print(f"Duration: {entry.get('total_duration_ms', 0)}ms")
            print(f"Tokens: {entry.get('total_tokens_used', 0)}")
            print("Classification: CUI // SP-CTI")
            if entry.get("error_message"):
                print(f"Error: {entry['error_message']}")
            if isinstance(entry.get("step_results"), dict):
                print("\nStep Results:")
                for sid, sr in entry["step_results"].items():
                    print(f"  [{sid}] {sr.get('agent', '?')} -> {sr.get('status', '?')} ({sr.get('duration_ms', 0)}ms)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
