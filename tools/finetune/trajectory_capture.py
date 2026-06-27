#!/usr/bin/env python3
# CUI // SP-CTI
"""Trajectory-to-Training Pipeline — auto-capture successful agent tool-call
traces as ShareGPT-format JSONL training data (D-FT-TRAJ).

Successful compliance/build/intake workflows become RL trajectories stored in:
  - ft_trajectories     — metadata + pre-built ShareGPT JSON
  - ft_trajectory_steps — individual tool-call steps (APPEND-ONLY, D6)

Finalized successful trajectories are exported as ShareGPT JSONL and/or
ingested directly into ft_dataset_examples for fine-tuning.

DB schema is defined in init_icdev_db.py.  Existing PostgreSQL check constraints:
  workflow_type  IN ('compliance','build','proposal','test','general')
  source         IN ('otel_spans','a2a_tasks','manual')
  outcome        IN ('success','partial','failed')
  steps.status   IN ('success','error','skipped')

ShareGPT format per trajectory:
{
  "conversations": [
    {"from": "system", "value": "<system prompt>"},
    {"from": "human",  "value": "<initial task>"},
    {"from": "gpt",    "value": "<tool_call>\\n{...}\\n</tool_call>"},
    {"from": "human",  "value": "<tool_result>\\n{...}\\n</tool_result>"},
    ...
    {"from": "gpt",    "value": "<final response>"}
  ],
  "metadata": {
    "trajectory_id": "traj-xxx",
    "workflow_type": "compliance",
    "reward": 0.95,
    "step_count": 4
  }
}

Usage:
    # Start a new capture session
    python tools/finetune/trajectory_capture.py --start \\
        --workflow-type compliance \\
        --task "Generate SSP for IL4 system" --json

    # Record a tool call step
    python tools/finetune/trajectory_capture.py --record \\
        --session-id traj-xxx --tool compliance_export \\
        --input '{"project_id":"p1"}' --output '{"status":"ok"}' --json

    # Finalize (mark outcome + reward)
    python tools/finetune/trajectory_capture.py --finalize --session-id traj-xxx \\
        --outcome success --reward 0.95 --response "SSP generated." --json

    # Export successful trajectories as ShareGPT JSONL
    python tools/finetune/trajectory_capture.py --export \\
        --output-path data/finetune/trajectories.jsonl --min-reward 0.7 --json

    # Ingest a trajectory into an existing ft_dataset
    python tools/finetune/trajectory_capture.py --ingest \\
        --session-id traj-xxx --dataset-id ds-xxx --json

    # Statistics
    python tools/finetune/trajectory_capture.py --stats --json

    # Health gate (exit 1 if tables are not accessible)
    python tools/finetune/trajectory_capture.py --health --gate --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Values must match existing PostgreSQL CHECK constraints in ft_trajectories
VALID_WORKFLOW_TYPES = ("compliance", "build", "proposal", "test", "general")
VALID_SOURCES = ("otel_spans", "a2a_tasks", "manual")
VALID_OUTCOMES = ("success", "partial", "failed")
VALID_STEP_STATUSES = ("success", "error", "skipped")

DEFAULT_SYSTEM_PROMPT = (
    "You are ICDEV\u2122, an Intelligent Certified Development agent specialized in "
    "government and DoD application development, ATO compliance, and DevSecOps. "
    "You use deterministic tools to accomplish tasks and produce audit-ready artifacts."
)

# Minimum reward for JSONL export (CLI default; can be overridden)
DEFAULT_MIN_REWARD = 0.7
# Gate thresholds (informational, non-blocking)
GATE_MIN_TRAJECTORIES = 5
GATE_MIN_SUCCESS_RATE = 0.5


# ── DB helpers ────────────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rget(row, key: str, idx: int):
    """Safely fetch from a tuple or dict-like row."""
    try:
        return row[key]
    except (TypeError, KeyError):
        return row[idx]


# ── ShareGPT builder ──────────────────────────────────────────────────────────


def _build_sharegpt(
    system_prompt: str,
    initial_task: str,
    steps: List,
    final_response: str,
    traj_id: str,
    workflow_type: str,
    reward: float,
    step_count: int,
) -> Dict[str, Any]:
    """Build a ShareGPT-format conversation dict from trajectory components."""
    conversations = []

    if system_prompt:
        conversations.append({"from": "system", "value": system_prompt})
    conversations.append({"from": "human", "value": initial_task})

    for step in steps:
        tool_name = _rget(step, "tool_name", 3)
        tool_input_raw = _rget(step, "tool_input", 4)
        tool_output_raw = _rget(step, "tool_output", 5)
        status = _rget(step, "status", 6)

        try:
            input_obj = json.loads(tool_input_raw) if isinstance(tool_input_raw, str) else tool_input_raw
        except (json.JSONDecodeError, TypeError):
            input_obj = {"raw": str(tool_input_raw)}

        try:
            output_obj = json.loads(tool_output_raw) if isinstance(tool_output_raw, str) else tool_output_raw
        except (json.JSONDecodeError, TypeError):
            output_obj = {"raw": str(tool_output_raw)}

        tool_call_content = (
            f"<tool_call>\n"
            f'{{"tool": {json.dumps(tool_name)}, "args": {json.dumps(input_obj, ensure_ascii=False)}}}'
            f"\n</tool_call>"
        )
        conversations.append({"from": "gpt", "value": tool_call_content})

        tool_result_content = (
            f"<tool_result>\n"
            f'{json.dumps({"status": status, "output": output_obj}, ensure_ascii=False)}'
            f"\n</tool_result>"
        )
        conversations.append({"from": "human", "value": tool_result_content})

    if final_response and final_response.strip():
        conversations.append({"from": "gpt", "value": final_response})

    return {
        "conversations": conversations,
        "metadata": {
            "trajectory_id": traj_id,
            "workflow_type": workflow_type,
            "reward": reward,
            "step_count": step_count,
        },
    }


def _extract_from_sharegpt(sharegpt: Dict[str, Any]):
    """Extract system_prompt and initial_task from a ShareGPT dict."""
    convs = sharegpt.get("conversations", [])
    system_prompt = ""
    initial_task = ""
    for turn in convs:
        frm = turn.get("from", "")
        val = turn.get("value", "")
        if frm == "system" and not system_prompt:
            system_prompt = val
        elif frm == "human" and not initial_task:
            initial_task = val
    return system_prompt, initial_task


# ── Core operations ───────────────────────────────────────────────────────────


def start_trajectory(
    workflow_type: str = "general",
    task: str = "",
    system_prompt: str = "",
    trace_id: str = "",
    source: str = "manual",
    classification: str = "CUI",
    project_id: str = "",
) -> Dict[str, Any]:
    """Open a new trajectory capture session.

    Inserts a row in ft_trajectories with outcome='partial' and an initial
    ShareGPT JSON containing only the system + task turns.  Records
    subsequent --record calls to ft_trajectory_steps, then --finalize
    rebuilds the full ShareGPT JSON and sets the final outcome.
    """
    if workflow_type not in VALID_WORKFLOW_TYPES:
        return {
            "success": False,
            "error": f"Invalid workflow_type '{workflow_type}'. Valid: {VALID_WORKFLOW_TYPES}",
        }
    if source not in VALID_SOURCES:
        return {
            "success": False,
            "error": f"Invalid source '{source}'. Valid: {VALID_SOURCES}",
        }
    if not task.strip():
        return {"success": False, "error": "--task description is required"}

    traj_id = f"traj-{uuid.uuid4().hex[:12]}"
    prompt = system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
    now = _now()

    # Build initial ShareGPT JSON (task + system only — steps added later)
    initial_sgpt = _build_sharegpt(
        system_prompt=prompt,
        initial_task=task,
        steps=[],
        final_response="",
        traj_id=traj_id,
        workflow_type=workflow_type,
        reward=0.0,
        step_count=0,
    )

    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO ft_trajectories
                (id, trace_id, workflow_type, source, outcome, reward,
                 step_count, dataset_id, sharegpt_json,
                 project_id, classification, created_at)
            VALUES (%s, %s, %s, %s, 'partial', 0.0, 0, '', %s, %s, %s, %s)
            """,
            (
                traj_id,
                trace_id,
                workflow_type,
                source,
                json.dumps(initial_sgpt, ensure_ascii=False),
                project_id,
                classification,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "session_id": traj_id,
        "workflow_type": workflow_type,
        "source": source,
        "created_at": now,
    }


def record_step(
    session_id: str,
    tool_name: str = "",
    tool_input: Optional[Dict] = None,
    tool_output: Optional[Dict] = None,
    status: str = "success",
    duration_ms: int = 0,
    span_id: str = "",
    classification: str = "CUI",
) -> Dict[str, Any]:
    """Append a tool-call step to an open trajectory (APPEND-ONLY)."""
    if status not in VALID_STEP_STATUSES:
        return {
            "success": False,
            "error": f"Invalid status '{status}'. Valid: {VALID_STEP_STATUSES}",
        }

    tool_input_json = json.dumps(tool_input or {}, ensure_ascii=False)
    tool_output_json = json.dumps(tool_output or {}, ensure_ascii=False)
    now = _now()

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT step_count FROM ft_trajectories WHERE id = %s",
            (session_id,),
        ).fetchone()
        if row is None:
            return {"success": False, "error": f"session_id '{session_id}' not found"}

        step_idx = _rget(row, "step_count", 0)  # current count = next 0-based index

        conn.execute(
            """
            INSERT INTO ft_trajectory_steps
                (trajectory_id, step_index, tool_name, tool_input, tool_output,
                 status, duration_ms, span_id, classification, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                step_idx,
                tool_name,
                tool_input_json,
                tool_output_json,
                status,
                duration_ms,
                span_id,
                classification,
                now,
            ),
        )
        conn.execute(
            "UPDATE ft_trajectories SET step_count = step_count + 1 WHERE id = %s",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "session_id": session_id,
        "step_index": step_idx,
        "tool_name": tool_name,
        "status": status,
    }


def finalize_trajectory(
    session_id: str,
    outcome: str = "success",
    reward: float = 1.0,
    final_response: str = "",
) -> Dict[str, Any]:
    """Close a trajectory session.

    Rebuilds the full ShareGPT JSON from stored steps + final_response,
    persists outcome + reward, and stamps captured_at.
    """
    if outcome not in VALID_OUTCOMES:
        return {
            "success": False,
            "error": f"Invalid outcome '{outcome}'. Valid: {VALID_OUTCOMES}",
        }
    reward = max(0.0, min(1.0, reward))
    now = _now()

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, workflow_type, sharegpt_json, step_count FROM ft_trajectories WHERE id = %s",
            (session_id,),
        ).fetchone()
        if row is None:
            return {"success": False, "error": f"session_id '{session_id}' not found"}

        workflow_type = _rget(row, "workflow_type", 1)
        sharegpt_raw = _rget(row, "sharegpt_json", 2)
        step_count = _rget(row, "step_count", 3)

        # Recover system_prompt + initial_task from existing ShareGPT JSON
        try:
            existing_sgpt = json.loads(sharegpt_raw) if isinstance(sharegpt_raw, str) else sharegpt_raw
        except (json.JSONDecodeError, TypeError):
            existing_sgpt = {}
        system_prompt, initial_task = _extract_from_sharegpt(existing_sgpt)

        # Fetch steps in insertion order
        steps = conn.execute(
            """
            SELECT trajectory_id, step_index, step_index,
                   tool_name, tool_input, tool_output, status, duration_ms, span_id
            FROM ft_trajectory_steps
            WHERE trajectory_id = %s
            ORDER BY step_index ASC
            """,
            (session_id,),
        ).fetchall()

        # Rebuild full ShareGPT JSON
        sgpt = _build_sharegpt(
            system_prompt=system_prompt,
            initial_task=initial_task,
            steps=steps,
            final_response=final_response,
            traj_id=session_id,
            workflow_type=workflow_type,
            reward=reward,
            step_count=step_count,
        )

        conn.execute(
            """
            UPDATE ft_trajectories
            SET outcome = %s, reward = %s, sharegpt_json = %s, captured_at = %s
            WHERE id = %s
            """,
            (outcome, reward, json.dumps(sgpt, ensure_ascii=False), now, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "session_id": session_id,
        "workflow_type": workflow_type,
        "outcome": outcome,
        "reward": reward,
        "step_count": step_count,
        "finalized_at": now,
    }


# ── Export ────────────────────────────────────────────────────────────────────


def export_jsonl(
    output_path: str = "data/finetune/trajectories.jsonl",
    min_reward: float = DEFAULT_MIN_REWARD,
    workflow_types: Optional[List[str]] = None,
    include_failed: bool = False,
) -> Dict[str, Any]:
    """Export finalized trajectories as ShareGPT JSONL.

    Only exports rows where:
      - captured_at IS NOT NULL (finalized)
      - outcome = 'success' (unless include_failed=True, which adds 'partial')
      - reward >= min_reward
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    conn = _get_db()
    try:
        where_clauses = ["captured_at IS NOT NULL", "reward >= ?"]
        params: List[Any] = [min_reward]
        if not include_failed:
            where_clauses.append("outcome = 'success'")
        if workflow_types:
            placeholders = ",".join("?" * len(workflow_types))
            where_clauses.append(f"workflow_type IN ({placeholders})")
            params.extend(workflow_types)

        where_sql = " AND ".join(where_clauses)
        trajs = conn.execute(
            f"""
            SELECT id, workflow_type, sharegpt_json, reward, step_count
            FROM ft_trajectories
            WHERE {where_sql}
            ORDER BY reward DESC, captured_at DESC
            """,
            params,
        ).fetchall()

        exported = 0
        skipped = 0
        with out.open("w", encoding="utf-8") as fh:
            for traj in trajs:
                sharegpt_raw = _rget(traj, "sharegpt_json", 2)
                try:
                    record = json.loads(sharegpt_raw) if isinstance(sharegpt_raw, str) else sharegpt_raw
                except (json.JSONDecodeError, TypeError):
                    skipped += 1
                    continue

                convs = record.get("conversations", [])
                if len(convs) < 2:
                    skipped += 1
                    continue

                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                exported += 1
    finally:
        conn.close()

    return {
        "success": True,
        "output_path": str(out.resolve()),
        "exported": exported,
        "skipped": skipped,
        "min_reward": min_reward,
        "workflow_types": workflow_types or "all",
    }


# ── Ingest into ft_dataset_examples ──────────────────────────────────────────


def ingest_to_dataset(
    session_id: str,
    dataset_id: str,
    classification: str = "CUI",
    project_id: str = "",
) -> Dict[str, Any]:
    """Ingest a finalized successful trajectory into ft_dataset_examples.

    Condenses the ShareGPT conversation into:
      system turn   → system_prompt column
      first human   → user_input (tool_result turns appended as context)
      last gpt turn → expected_output column
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, workflow_type, outcome, reward, sharegpt_json, captured_at FROM ft_trajectories WHERE id = %s",
            (session_id,),
        ).fetchone()

        if row is None:
            return {"success": False, "error": f"session_id '{session_id}' not found"}

        outcome = _rget(row, "outcome", 2)
        captured_at = _rget(row, "captured_at", 5)
        sharegpt_raw = _rget(row, "sharegpt_json", 4)
        reward = _rget(row, "reward", 3)

        if outcome != "success":
            return {
                "success": False,
                "error": f"trajectory outcome is '{outcome}' — only 'success' trajectories can be ingested",
            }
        if not captured_at:
            return {"success": False, "error": "trajectory not yet finalized — call --finalize first"}

        try:
            sgpt = json.loads(sharegpt_raw) if isinstance(sharegpt_raw, str) else sharegpt_raw
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": "sharegpt_json is not valid JSON"}

        convs = sgpt.get("conversations", [])
        if len(convs) < 2:
            return {"success": False, "error": "trajectory has fewer than 2 conversation turns"}

        # Extract turns
        system_prompt = ""
        user_input = ""
        expected_output = ""
        extra_human_turns = []

        for turn in convs:
            frm = turn.get("from", "")
            val = turn.get("value", "")
            if frm == "system" and not system_prompt:
                system_prompt = val
            elif frm == "human" and not user_input:
                user_input = val
            elif frm == "human":
                extra_human_turns.append(val)  # tool_result turns
            elif frm == "gpt":
                expected_output = val  # last gpt turn wins

        if not expected_output:
            return {"success": False, "error": "no gpt response found in trajectory"}

        # Append tool context to user_input
        if extra_human_turns:
            tool_ctx = "\n".join(extra_human_turns)
            user_input = f"{user_input}\n\n<tool_context>\n{tool_ctx}\n</tool_context>"

        content_hash = _content_hash(system_prompt + user_input + expected_output)

        existing = conn.execute(
            "SELECT id FROM ft_dataset_examples WHERE content_hash = %s AND dataset_id = %s",
            (content_hash, dataset_id),
        ).fetchone()
        if existing:
            return {
                "success": True,
                "duplicate": True,
                "content_hash": content_hash,
                "message": "example already exists in dataset (dedup)",
            }

        now = _now()
        conn.execute(
            """
            INSERT INTO ft_dataset_examples
                (dataset_id, system_prompt, user_input, expected_output,
                 source, content_hash, classification, project_id,
                 approved, quality_score, created_at)
            VALUES (%s, %s, %s, %s, 'imported', %s, %s, %s, 1, %s, %s)
            """,
            (
                dataset_id,
                system_prompt,
                user_input,
                expected_output,
                content_hash,
                classification,
                project_id,
                reward,
                now,
            ),
        )
        conn.execute(
            "UPDATE ft_trajectories SET dataset_id = %s WHERE id = %s",
            (dataset_id, session_id),
        )
        conn.execute(
            "UPDATE ft_datasets SET example_count = example_count + 1, updated_at = %s WHERE id = %s",
            (now, dataset_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "session_id": session_id,
        "dataset_id": dataset_id,
        "content_hash": content_hash,
        "user_input_length": len(user_input),
        "ingested_at": now,
    }


# ── Statistics ────────────────────────────────────────────────────────────────


def get_stats() -> Dict[str, Any]:
    """Return aggregate statistics over all captured trajectories."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM ft_trajectories").fetchone()[0]
        successful = conn.execute(
            "SELECT COUNT(*) FROM ft_trajectories WHERE outcome = 'success'"
        ).fetchone()[0]
        finalized = conn.execute(
            "SELECT COUNT(*) FROM ft_trajectories WHERE captured_at IS NOT NULL"
        ).fetchone()[0]
        avg_reward_row = conn.execute(
            "SELECT AVG(reward) FROM ft_trajectories WHERE outcome = 'success' AND captured_at IS NOT NULL"
        ).fetchone()
        avg_reward = round(avg_reward_row[0] or 0.0, 4)
        total_steps = conn.execute("SELECT COUNT(*) FROM ft_trajectory_steps").fetchone()[0]

        by_type_rows = conn.execute(
            """
            SELECT workflow_type,
                   COUNT(*) AS n,
                   SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS ok,
                   AVG(reward) AS avg_r
            FROM ft_trajectories
            WHERE captured_at IS NOT NULL
            GROUP BY workflow_type
            ORDER BY n DESC
            """
        ).fetchall()

        by_type: Dict[str, Any] = {}
        for r in by_type_rows:
            wtype = _rget(r, "workflow_type", 0)
            by_type[wtype] = {
                "count": _rget(r, "n", 1),
                "successful": _rget(r, "ok", 2),
                "avg_reward": round((_rget(r, "avg_r", 3)) or 0.0, 4),
            }

        exportable = conn.execute(
            """
            SELECT COUNT(*) FROM ft_trajectories
            WHERE outcome = 'success' AND captured_at IS NOT NULL AND reward >= %s
            """,
            (DEFAULT_MIN_REWARD,),
        ).fetchone()[0]
    finally:
        conn.close()

    success_rate = round(successful / total, 4) if total > 0 else 0.0

    return {
        "success": True,
        "total": total,
        "finalized": finalized,
        "successful": successful,
        "success_rate": success_rate,
        "avg_reward": avg_reward,
        "total_steps": total_steps,
        "exportable": exportable,
        "by_workflow_type": by_type,
    }


# ── Health gate ────────────────────────────────────────────────────────────────


def health_check(gate: bool = False) -> Dict[str, Any]:
    """Validate trajectory system health.

    Gate check: both ft_trajectories and ft_trajectory_steps must be accessible.
    Warnings (non-blocking): low count, low success rate.
    """
    issues: List[str] = []
    total = 0
    successful = 0
    total_steps = 0
    tables_ok = True

    conn = _get_db()
    try:
        try:
            total = conn.execute("SELECT COUNT(*) FROM ft_trajectories").fetchone()[0]
            total_steps = conn.execute("SELECT COUNT(*) FROM ft_trajectory_steps").fetchone()[0]
            successful = conn.execute(
                "SELECT COUNT(*) FROM ft_trajectories WHERE outcome = 'success'"
            ).fetchone()[0]
        except Exception as exc:
            tables_ok = False
            issues.append(f"Table access failed: {exc}")
    finally:
        conn.close()

    success_rate = round(successful / total, 4) if total > 0 else 0.0

    warnings: List[str] = []
    if total < GATE_MIN_TRAJECTORIES:
        warnings.append(f"Only {total} trajectories captured (recommend >= {GATE_MIN_TRAJECTORIES})")
    if total >= 10 and success_rate < GATE_MIN_SUCCESS_RATE:
        warnings.append(f"Success rate {success_rate:.0%} below threshold {GATE_MIN_SUCCESS_RATE:.0%}")

    return {
        "success": True,
        "status": "pass" if not issues else "fail",
        "tables_ok": tables_ok,
        "total_trajectories": total,
        "successful_trajectories": successful,
        "total_steps": total_steps,
        "success_rate": success_rate,
        "issues": issues,
        "warnings": warnings,
        "gate_blocked": bool(issues),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(
        description="Trajectory-to-Training Pipeline — capture agent tool-call traces as RL training data"
    )
    p.add_argument("--start", action="store_true", help="Start a new trajectory session")
    p.add_argument("--record", action="store_true", help="Record a tool-call step")
    p.add_argument("--finalize", action="store_true", help="Finalize an open session")
    p.add_argument("--export", action="store_true", help="Export trajectories as ShareGPT JSONL")
    p.add_argument("--ingest", action="store_true", help="Ingest trajectory into ft_dataset_examples")
    p.add_argument("--stats", action="store_true", help="Print aggregate statistics")
    p.add_argument("--health", action="store_true", help="Run health / gate check")

    # --start options
    p.add_argument(
        "--workflow-type", default="general", choices=VALID_WORKFLOW_TYPES,
        help="Workflow type label"
    )
    p.add_argument(
        "--source", default="manual", choices=VALID_SOURCES,
        help="Trajectory source (manual/otel_spans/a2a_tasks)"
    )
    p.add_argument("--task", default="", help="Initial task description (required for --start)")
    p.add_argument("--system-prompt", default="", help="Override default system prompt")
    p.add_argument("--trace-id", default="", help="OpenTelemetry trace ID (optional)")

    # --record / --finalize session
    p.add_argument("--session-id", default="", help="Trajectory session ID (traj-xxx)")

    # --record options
    p.add_argument("--tool", default="", help="Tool name")
    p.add_argument("--input", default="{}", help="Tool input JSON string")
    p.add_argument("--output", default="{}", help="Tool output JSON string")
    p.add_argument(
        "--status", default="success", choices=VALID_STEP_STATUSES,
        help="Step execution status"
    )
    p.add_argument("--duration-ms", type=int, default=0, help="Step duration in ms")
    p.add_argument("--span-id", default="", help="OpenTelemetry span ID (optional)")

    # --finalize options
    p.add_argument(
        "--outcome", default="success", choices=VALID_OUTCOMES,
        help="Trajectory outcome (success/partial/failed)"
    )
    p.add_argument("--reward", type=float, default=1.0, help="Reward signal [0.0-1.0]")
    p.add_argument("--response", default="", help="Final agent response text")

    # --export options
    p.add_argument(
        "--output-path",
        default="data/finetune/trajectories.jsonl",
        help="Output JSONL file path",
    )
    p.add_argument("--min-reward", type=float, default=DEFAULT_MIN_REWARD)
    p.add_argument(
        "--workflow-types",
        nargs="*",
        default=None,
        help="Filter by workflow type(s) e.g. --workflow-types compliance build",
    )
    p.add_argument("--include-failed", action="store_true", help="Include partial/failed trajectories")

    # --ingest options
    p.add_argument("--dataset-id", default="", help="ft_dataset ID to ingest into")

    # Global
    p.add_argument("--classification", default="CUI")
    p.add_argument("--project-id", default="")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--gate", action="store_true", help="Exit 1 on gate failure or error")

    return p.parse_args()


def main() -> int:
    args = _parse_args()
    result: Dict[str, Any] = {}

    if args.start:
        result = start_trajectory(
            workflow_type=args.workflow_type,
            task=args.task,
            system_prompt=args.system_prompt,
            trace_id=args.trace_id,
            source=args.source,
            classification=args.classification,
            project_id=args.project_id,
        )

    elif args.record:
        try:
            tool_input = json.loads(args.input)
        except json.JSONDecodeError:
            tool_input = {"raw": args.input}
        try:
            tool_output = json.loads(args.output)
        except json.JSONDecodeError:
            tool_output = {"raw": args.output}

        result = record_step(
            session_id=args.session_id,
            tool_name=args.tool,
            tool_input=tool_input,
            tool_output=tool_output,
            status=args.status,
            duration_ms=args.duration_ms,
            span_id=args.span_id,
            classification=args.classification,
        )

    elif args.finalize:
        result = finalize_trajectory(
            session_id=args.session_id,
            outcome=args.outcome,
            reward=args.reward,
            final_response=args.response,
        )

    elif args.export:
        result = export_jsonl(
            output_path=args.output_path,
            min_reward=args.min_reward,
            workflow_types=args.workflow_types,
            include_failed=args.include_failed,
        )

    elif args.ingest:
        result = ingest_to_dataset(
            session_id=args.session_id,
            dataset_id=args.dataset_id,
            classification=args.classification,
            project_id=args.project_id,
        )

    elif args.stats:
        result = get_stats()

    elif args.health:
        result = health_check(gate=args.gate)

    else:
        result = {
            "success": False,
            "error": "No action specified. Use --start, --record, --finalize, --export, --ingest, --stats, or --health",
        }

    if args.json_output:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    else:
        if result.get("success"):
            for k, v in result.items():
                if k != "success":
                    print(f"  {k}: {v}")
        else:
            print(f"ERROR: {result.get('error', 'unknown error')}", file=sys.stderr)

    blocked = result.get("gate_blocked", False)
    failed = not result.get("success", True)
    if (blocked or failed) and args.gate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
