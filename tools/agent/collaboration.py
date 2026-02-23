# [TEMPLATE: CUI // SP-CTI]
"""Collaboration patterns for multi-agent orchestration.

Implements five structured collaboration patterns used by the ICDEV
multi-agent system: reviewer, debate, consensus, veto, and escalation.
Each pattern orchestrates agent interactions using BedrockClient and logs
collaboration events to agent_collaboration_history and the audit trail.

Decision D36: ThreadPoolExecutor for parallelism (no asyncio).
Decision D41: SQLite-based mailbox for inter-agent messaging.
Decision D42: YAML-defined authority matrix for veto rights.
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = logging.getLogger("icdev.collaboration")

# Graceful LLM import (Enhancement #4 — Bedrock decoupling)
try:
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False
    LLMRouter = None
    LLMRequest = None

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit: %s", kwargs.get("action", ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None) -> sqlite3.Connection:
    """Open a DB connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_collaboration(project_id: str, agent_a_id: str, agent_b_id: str,
                       collaboration_type: str, task_id: str = None,
                       workflow_id: str = None, outcome: str = None,
                       lesson_learned: str = None, duration_ms: int = None,
                       db_path=None) -> int:
    """Record a collaboration event in agent_collaboration_history."""
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO agent_collaboration_history
               (project_id, agent_a_id, agent_b_id, collaboration_type,
                task_id, workflow_id, outcome, lesson_learned, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, agent_a_id, agent_b_id, collaboration_type,
             task_id, workflow_id, outcome, lesson_learned, duration_ms),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _load_prompt_template(template_name: str) -> str:
    """Load a hard prompt template from hardprompts/agent/."""
    path = BASE_DIR / "hardprompts" / "agent" / template_name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _invoke_llm(system_prompt: str, user_prompt: str,
                agent_id: str = "", project_id: str = "",
                output_schema: dict = None) -> dict:
    """Invoke LLM via router and parse JSON response. Returns parsed dict or fallback."""
    if not _LLM_AVAILABLE:
        logger.warning("LLM router unavailable — returning empty fallback")
        return {}

    router = LLMRouter()
    request = LLMRequest(
        messages=[{"role": "user", "content": [{"type": "text", "text": user_prompt}]}],
        system_prompt=system_prompt,
        agent_id=agent_id,
        project_id=project_id,
        effort="medium",
        max_tokens=4096,
        output_schema=output_schema,
        classification="CUI",
    )
    resp = router.invoke("collaboration", request)

    # Try structured output first, then parse content as JSON
    if resp.structured_output:
        return resp.structured_output

    content = resp.content.strip()
    # Handle markdown-wrapped JSON
    if content.startswith("```"):
        lines = content.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        content = "\n".join(json_lines)

    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse Bedrock response as JSON")
        return {"raw_content": resp.content}


def _load_schema(schema_name: str) -> Optional[dict]:
    """Load a JSON schema from context/agent/response_schemas/."""
    path = BASE_DIR / "context" / "agent" / "response_schemas" / schema_name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------------------
# Pattern 1: Reviewer Pattern
# ---------------------------------------------------------------------------

def reviewer_pattern(producer_output: dict, reviewer_agent_id: str,
                     skill_id: str, project_id: str, max_rounds: int = 3,
                     db_path=None) -> dict:
    """Producer generates -> Reviewer evaluates -> Approve or Reject with feedback -> Revise.

    The reviewer evaluates the producer's output and decides to approve or reject.
    If rejected, feedback is provided for revision. Repeats up to max_rounds.

    Args:
        producer_output: The output to review (dict with content).
        reviewer_agent_id: Agent performing the review.
        skill_id: Skill context for the review.
        project_id: Project scope.
        max_rounds: Maximum review rounds before forced decision.
        db_path: Optional database path override.

    Returns:
        {"approved": bool, "final_output": ..., "rounds": int, "feedback_history": [...]}
    """
    start_time = time.time()
    feedback_history = []
    current_output = producer_output
    approved = False
    rounds_used = 0

    # Audit: collaboration started
    audit_log_event(
        event_type="agent_collaboration_started",
        actor=reviewer_agent_id,
        action=f"Reviewer pattern started for skill '{skill_id}'",
        project_id=project_id,
        details={"pattern": "reviewer", "max_rounds": max_rounds, "skill_id": skill_id},
        classification="CUI",
    )

    template = _load_prompt_template("reviewer_prompt.md")
    schema = _load_schema("review_decision.json")

    for round_num in range(1, max_rounds + 1):
        rounds_used = round_num

        # Build review prompt
        system_prompt = template if template else (
            "You are a reviewing agent. Evaluate the output and respond with JSON: "
            '{"decision": "approve"|"reject", "confidence": 0-1, "feedback": "...", "issues": [...]}'
        )

        user_prompt = json.dumps({
            "round": round_num,
            "max_rounds": max_rounds,
            "producer_output": current_output,
            "previous_feedback": feedback_history,
            "skill_id": skill_id,
        }, indent=2)

        # Invoke Bedrock for review decision
        decision = _invoke_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            agent_id=reviewer_agent_id,
            project_id=project_id,
            output_schema=schema,
        )

        verdict = decision.get("decision", "reject").lower()
        confidence = decision.get("confidence", 0.0)
        feedback = decision.get("feedback", "")
        issues = decision.get("issues", [])

        feedback_history.append({
            "round": round_num,
            "decision": verdict,
            "confidence": confidence,
            "feedback": feedback,
            "issues": issues,
        })

        if verdict == "approve":
            approved = True
            break

        # If rejected and not last round, the output would be revised by producer
        # (caller handles revision; we just record the feedback)
        logger.info("Round %d/%d: rejected (confidence=%.2f)", round_num, max_rounds, confidence)

    duration_ms = int((time.time() - start_time) * 1000)

    # Log collaboration
    outcome = "agreement" if approved else "disagreement"
    _log_collaboration(
        project_id=project_id,
        agent_a_id="producer",
        agent_b_id=reviewer_agent_id,
        collaboration_type="review",
        outcome=outcome,
        duration_ms=duration_ms,
        db_path=db_path,
    )

    # Audit: collaboration completed
    audit_log_event(
        event_type="agent_collaboration_completed",
        actor=reviewer_agent_id,
        action=f"Reviewer pattern completed: {'approved' if approved else 'rejected'} after {rounds_used} rounds",
        project_id=project_id,
        details={"pattern": "reviewer", "approved": approved, "rounds": rounds_used,
                 "duration_ms": duration_ms},
        classification="CUI",
    )

    return {
        "approved": approved,
        "final_output": current_output,
        "rounds": rounds_used,
        "feedback_history": feedback_history,
        "duration_ms": duration_ms,
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# Pattern 2: Debate Pattern
# ---------------------------------------------------------------------------

def debate_pattern(topic: str, agent_ids: List[str], project_id: str,
                   rounds: int = 2, db_path=None) -> dict:
    """Multiple agents argue positions on a topic. Each sees others' arguments.

    Each agent presents their position, and in subsequent rounds they respond
    to each other's arguments. A synthesis is generated at the end.

    Args:
        topic: The topic to debate.
        agent_ids: List of agent IDs participating.
        project_id: Project scope.
        rounds: Number of debate rounds.
        db_path: Optional database path override.

    Returns:
        {"positions": [...], "synthesis": str, "consensus": bool}
    """
    start_time = time.time()
    all_positions = []

    audit_log_event(
        event_type="agent_collaboration_started",
        actor="orchestrator-agent",
        action=f"Debate pattern started on topic '{topic}' with {len(agent_ids)} agents",
        project_id=project_id,
        details={"pattern": "debate", "topic": topic, "agents": agent_ids, "rounds": rounds},
        classification="CUI",
    )

    template = _load_prompt_template("debate_prompt.md")
    schema = _load_schema("debate_position.json")

    for round_num in range(1, rounds + 1):
        round_positions = []

        # Collect previous positions for context
        previous_positions_text = json.dumps(all_positions, indent=2) if all_positions else "None yet."

        def _debate_turn(agent_id: str) -> dict:
            """Single agent's debate turn."""
            system_prompt = template if template else (
                "You are participating in a structured debate. Present your position as JSON: "
                '{"position": "support"|"oppose"|"neutral", "confidence": 0-1, '
                '"arguments": [...], "counterarguments": [...], "recommendation": "..."}'
            )
            user_prompt = json.dumps({
                "topic": topic,
                "agent_role": agent_id,
                "round": round_num,
                "total_rounds": rounds,
                "previous_positions": previous_positions_text,
            }, indent=2)

            result = _invoke_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                agent_id=agent_id,
                project_id=project_id,
                output_schema=schema,
            )
            result["agent_id"] = agent_id
            result["round"] = round_num
            return result

        # Execute debate turns in parallel (D36: ThreadPoolExecutor)
        with ThreadPoolExecutor(max_workers=min(len(agent_ids), 5)) as executor:
            futures = {executor.submit(_debate_turn, aid): aid for aid in agent_ids}
            for future in as_completed(futures):
                try:
                    position = future.result()
                    round_positions.append(position)
                except Exception as exc:
                    agent_id = futures[future]
                    logger.error("Debate turn failed for %s: %s", agent_id, exc)
                    round_positions.append({
                        "agent_id": agent_id,
                        "round": round_num,
                        "position": "neutral",
                        "confidence": 0.0,
                        "arguments": [f"Error: {exc}"],
                        "counterarguments": [],
                        "recommendation": "Unable to participate due to error",
                    })

        all_positions.extend(round_positions)

    # Determine consensus
    final_positions = [p for p in all_positions if p.get("round") == rounds]
    position_counts = {}
    for p in final_positions:
        pos = p.get("position", "neutral")
        position_counts[pos] = position_counts.get(pos, 0) + 1

    dominant_position = max(position_counts, key=position_counts.get) if position_counts else "neutral"
    consensus = position_counts.get(dominant_position, 0) >= len(agent_ids) * 0.67

    # Generate synthesis
    synthesis = (
        f"After {rounds} round(s) of debate among {len(agent_ids)} agents, "
        f"the dominant position is '{dominant_position}' "
        f"({'consensus reached' if consensus else 'no consensus'}). "
        f"Position distribution: {json.dumps(position_counts)}."
    )

    duration_ms = int((time.time() - start_time) * 1000)

    # Log pairwise collaborations
    for i, a in enumerate(agent_ids):
        for b in agent_ids[i + 1:]:
            _log_collaboration(
                project_id=project_id,
                agent_a_id=a,
                agent_b_id=b,
                collaboration_type="debate",
                outcome="agreement" if consensus else "disagreement",
                duration_ms=duration_ms,
                db_path=db_path,
            )

    audit_log_event(
        event_type="agent_collaboration_completed",
        actor="orchestrator-agent",
        action=f"Debate completed: {'consensus' if consensus else 'no consensus'} on '{topic}'",
        project_id=project_id,
        details={"pattern": "debate", "consensus": consensus, "positions": position_counts,
                 "duration_ms": duration_ms},
        classification="CUI",
    )

    return {
        "positions": all_positions,
        "synthesis": synthesis,
        "consensus": consensus,
        "dominant_position": dominant_position,
        "position_counts": position_counts,
        "duration_ms": duration_ms,
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# Pattern 3: Consensus Pattern
# ---------------------------------------------------------------------------

def consensus_pattern(proposal: dict, voter_agent_ids: List[str],
                      project_id: str, threshold: float = 0.67,
                      db_path=None) -> dict:
    """Agents vote on a proposal. Passes if vote ratio >= threshold.

    Each agent evaluates the proposal and votes approve/reject with rationale.
    The proposal passes if the approval ratio meets or exceeds the threshold.

    Args:
        proposal: The proposal to vote on.
        voter_agent_ids: List of voting agent IDs.
        project_id: Project scope.
        threshold: Approval ratio needed (0.0-1.0, default 0.67).
        db_path: Optional database path override.

    Returns:
        {"approved": bool, "votes": {...}, "ratio": float}
    """
    start_time = time.time()

    audit_log_event(
        event_type="agent_collaboration_started",
        actor="orchestrator-agent",
        action=f"Consensus pattern started with {len(voter_agent_ids)} voters (threshold={threshold})",
        project_id=project_id,
        details={"pattern": "consensus", "voter_count": len(voter_agent_ids),
                 "threshold": threshold},
        classification="CUI",
    )

    votes = {}

    def _cast_vote(agent_id: str) -> dict:
        """Single agent's vote."""
        system_prompt = (
            "You are voting on a proposal. Evaluate it and respond with JSON: "
            '{"vote": "approve" or "reject", "confidence": 0.0-1.0, '
            '"rationale": "brief explanation"}'
        )
        user_prompt = json.dumps({
            "proposal": proposal,
            "agent_role": agent_id,
            "threshold": threshold,
        }, indent=2)

        result = _invoke_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            agent_id=agent_id,
            project_id=project_id,
        )
        result["agent_id"] = agent_id
        return result

    # Parallel voting (D36: ThreadPoolExecutor)
    with ThreadPoolExecutor(max_workers=min(len(voter_agent_ids), 10)) as executor:
        futures = {executor.submit(_cast_vote, aid): aid for aid in voter_agent_ids}
        for future in as_completed(futures):
            agent_id = futures[future]
            try:
                result = future.result()
                votes[agent_id] = {
                    "vote": result.get("vote", "reject"),
                    "confidence": result.get("confidence", 0.0),
                    "rationale": result.get("rationale", ""),
                }
            except Exception as exc:
                logger.error("Vote failed for %s: %s", agent_id, exc)
                votes[agent_id] = {
                    "vote": "abstain",
                    "confidence": 0.0,
                    "rationale": f"Error: {exc}",
                }

    # Calculate ratio (abstentions don't count toward total)
    valid_votes = {k: v for k, v in votes.items() if v["vote"] in ("approve", "reject")}
    approve_count = sum(1 for v in valid_votes.values() if v["vote"] == "approve")
    total_valid = len(valid_votes)
    ratio = approve_count / total_valid if total_valid > 0 else 0.0
    approved = ratio >= threshold

    duration_ms = int((time.time() - start_time) * 1000)

    # Log collaborations
    _log_collaboration(
        project_id=project_id,
        agent_a_id="orchestrator-agent",
        agent_b_id=",".join(voter_agent_ids),
        collaboration_type="consensus",
        outcome="agreement" if approved else "disagreement",
        duration_ms=duration_ms,
        db_path=db_path,
    )

    audit_log_event(
        event_type="agent_collaboration_completed",
        actor="orchestrator-agent",
        action=f"Consensus {'reached' if approved else 'not reached'}: {approve_count}/{total_valid} approved (ratio={ratio:.2f}, threshold={threshold})",
        project_id=project_id,
        details={"pattern": "consensus", "approved": approved, "ratio": ratio,
                 "approve_count": approve_count, "total_valid": total_valid,
                 "duration_ms": duration_ms},
        classification="CUI",
    )

    return {
        "approved": approved,
        "votes": votes,
        "ratio": round(ratio, 4),
        "approve_count": approve_count,
        "total_valid": total_valid,
        "threshold": threshold,
        "duration_ms": duration_ms,
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# Pattern 4: Veto Pattern
# ---------------------------------------------------------------------------

def veto_pattern(output: dict, authority_agent_id: str, topic: str,
                 project_id: str, db_path=None) -> dict:
    """Domain authority reviews output for violations. Can hard/soft veto.

    The authority agent evaluates the output against its domain expertise.
    A hard veto blocks the output; a soft veto warns but allows override.

    Args:
        output: The output to evaluate.
        authority_agent_id: Agent with domain authority.
        topic: Domain topic being evaluated.
        project_id: Project scope.
        db_path: Optional database path override.

    Returns:
        {"vetoed": bool, "veto_type": str, "reason": str, "evidence": str}
    """
    start_time = time.time()

    audit_log_event(
        event_type="agent_collaboration_started",
        actor=authority_agent_id,
        action=f"Veto check started for topic '{topic}'",
        project_id=project_id,
        details={"pattern": "veto", "topic": topic, "authority": authority_agent_id},
        classification="CUI",
    )

    # Load authority info
    try:
        from tools.agent.authority import check_authority
        authority_info = check_authority(authority_agent_id, topic)
    except ImportError:
        authority_info = {"has_authority": True, "veto_type": "soft", "topics": [topic]}

    template = _load_prompt_template("veto_check_prompt.md")
    schema = _load_schema("veto_decision.json")

    system_prompt = template if template else (
        "You are a domain authority agent. Evaluate the output for violations. "
        'Respond with JSON: {"veto": true/false, "veto_type": "hard"|"soft"|null, '
        '"reason": "...", "evidence": "...", "recommendations": [...]}'
    )

    user_prompt = json.dumps({
        "authority_agent_id": authority_agent_id,
        "authority_topics": authority_info.get("topics", [topic]),
        "veto_type": authority_info.get("veto_type", "soft"),
        "topic": topic,
        "content": output,
    }, indent=2)

    decision = _invoke_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        agent_id=authority_agent_id,
        project_id=project_id,
        output_schema=schema,
    )

    vetoed = decision.get("veto", False)
    veto_type = decision.get("veto_type", None) if vetoed else None
    reason = decision.get("reason", "")
    evidence = decision.get("evidence", "")
    recommendations = decision.get("recommendations", [])

    duration_ms = int((time.time() - start_time) * 1000)

    # Record veto in DB if issued
    if vetoed:
        try:
            from tools.agent.authority import record_veto
            record_veto(
                authority_agent_id=authority_agent_id,
                vetoed_agent_id="producer",
                task_id=None,
                workflow_id=None,
                project_id=project_id,
                topic=topic,
                veto_type=veto_type or "soft",
                reason=reason,
                evidence=evidence,
                db_path=db_path,
            )
        except ImportError:
            logger.warning("authority module unavailable — veto not recorded in DB")

        audit_log_event(
            event_type="agent_veto_issued",
            actor=authority_agent_id,
            action=f"{veto_type} veto on topic '{topic}': {reason}",
            project_id=project_id,
            details={"veto_type": veto_type, "topic": topic, "reason": reason},
            classification="CUI",
        )

    # Log collaboration
    _log_collaboration(
        project_id=project_id,
        agent_a_id=authority_agent_id,
        agent_b_id="producer",
        collaboration_type="veto",
        outcome="veto" if vetoed else "agreement",
        duration_ms=duration_ms,
        db_path=db_path,
    )

    audit_log_event(
        event_type="agent_collaboration_completed",
        actor=authority_agent_id,
        action=f"Veto check completed: {'VETOED' if vetoed else 'PASSED'} for topic '{topic}'",
        project_id=project_id,
        details={"pattern": "veto", "vetoed": vetoed, "veto_type": veto_type,
                 "duration_ms": duration_ms},
        classification="CUI",
    )

    return {
        "vetoed": vetoed,
        "veto_type": veto_type,
        "reason": reason,
        "evidence": evidence,
        "recommendations": recommendations,
        "authority_agent_id": authority_agent_id,
        "topic": topic,
        "duration_ms": duration_ms,
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# Pattern 5: Escalation Pattern
# ---------------------------------------------------------------------------

def escalation_pattern(task_context: dict, escalation_reason: str,
                       project_id: str, db_path=None) -> dict:
    """Escalates a blocked task to human review. Creates approval_workflow entry.

    When an agent is blocked (e.g., hard veto, low confidence, conflicting
    directives), this pattern escalates to human review by creating an
    approval_workflow record.

    Args:
        task_context: Context about the blocked task.
        escalation_reason: Why escalation is needed.
        project_id: Project scope.
        db_path: Optional database path override.

    Returns:
        {"escalation_id": str, "status": "pending_human_review"}
    """
    start_time = time.time()
    escalation_id = str(uuid.uuid4())

    audit_log_event(
        event_type="agent_escalation_created",
        actor="orchestrator-agent",
        action=f"Task escalated to human review: {escalation_reason}",
        project_id=project_id,
        details={"escalation_id": escalation_id, "reason": escalation_reason,
                 "task_context": task_context},
        classification="CUI",
    )

    # Insert into approval_workflows if the table supports it
    conn = _get_db(db_path)
    try:
        # Check if approval_workflows table has session_id as required
        # Use a generic escalation record approach
        conn.execute(
            """INSERT OR IGNORE INTO approval_workflows
               (id, session_id, project_id, approval_type, status,
                submitted_by, reviewers, conditions, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                escalation_id,
                escalation_id,  # self-referencing session for escalations
                project_id,
                "pi_commitment",  # closest matching type
                "escalated",
                "orchestrator-agent",
                json.dumps(["human-reviewer"]),
                json.dumps({
                    "escalation_reason": escalation_reason,
                    "task_context": task_context,
                }),
                "CUI",
            ),
        )
        conn.commit()
        logger.info("Escalation %s created in approval_workflows", escalation_id)
    except sqlite3.Error as exc:
        logger.warning("Failed to create approval_workflow entry: %s", exc)
    finally:
        conn.close()

    # Log collaboration
    _log_collaboration(
        project_id=project_id,
        agent_a_id="orchestrator-agent",
        agent_b_id="human-reviewer",
        collaboration_type="escalation",
        outcome="escalation",
        duration_ms=int((time.time() - start_time) * 1000),
        db_path=db_path,
    )

    return {
        "escalation_id": escalation_id,
        "status": "pending_human_review",
        "reason": escalation_reason,
        "project_id": project_id,
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI for testing collaboration patterns."""
    parser = argparse.ArgumentParser(
        description="ICDEV Collaboration Patterns — multi-agent interaction orchestration"
    )
    sub = parser.add_subparsers(dest="pattern", help="Collaboration pattern to execute")

    # Reviewer
    p_review = sub.add_parser("reviewer", help="Producer-Reviewer pattern")
    p_review.add_argument("--output", required=True, help="JSON output to review")
    p_review.add_argument("--reviewer", required=True, help="Reviewer agent ID")
    p_review.add_argument("--skill", required=True, help="Skill context")
    p_review.add_argument("--project-id", required=True, help="Project ID")
    p_review.add_argument("--max-rounds", type=int, default=3)

    # Debate
    p_debate = sub.add_parser("debate", help="Multi-agent debate pattern")
    p_debate.add_argument("--topic", required=True, help="Debate topic")
    p_debate.add_argument("--agents", required=True, help="Comma-separated agent IDs")
    p_debate.add_argument("--project-id", required=True, help="Project ID")
    p_debate.add_argument("--rounds", type=int, default=2)

    # Consensus
    p_consensus = sub.add_parser("consensus", help="Voting consensus pattern")
    p_consensus.add_argument("--proposal", required=True, help="JSON proposal to vote on")
    p_consensus.add_argument("--voters", required=True, help="Comma-separated voter agent IDs")
    p_consensus.add_argument("--project-id", required=True, help="Project ID")
    p_consensus.add_argument("--threshold", type=float, default=0.67)

    # Veto
    p_veto = sub.add_parser("veto", help="Domain authority veto pattern")
    p_veto.add_argument("--output", required=True, help="JSON output to evaluate")
    p_veto.add_argument("--authority", required=True, help="Authority agent ID")
    p_veto.add_argument("--topic", required=True, help="Domain topic")
    p_veto.add_argument("--project-id", required=True, help="Project ID")

    # Escalation
    p_escalate = sub.add_parser("escalation", help="Escalation to human review")
    p_escalate.add_argument("--context", required=True, help="JSON task context")
    p_escalate.add_argument("--reason", required=True, help="Escalation reason")
    p_escalate.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not args.pattern:
        parser.print_help()
        sys.exit(1)

    if args.pattern == "reviewer":
        output_data = json.loads(args.output)
        result = reviewer_pattern(
            producer_output=output_data,
            reviewer_agent_id=args.reviewer,
            skill_id=args.skill,
            project_id=args.project_id,
            max_rounds=args.max_rounds,
        )

    elif args.pattern == "debate":
        agent_ids = [a.strip() for a in args.agents.split(",")]
        result = debate_pattern(
            topic=args.topic,
            agent_ids=agent_ids,
            project_id=args.project_id,
            rounds=args.rounds,
        )

    elif args.pattern == "consensus":
        proposal_data = json.loads(args.proposal)
        voter_ids = [v.strip() for v in args.voters.split(",")]
        result = consensus_pattern(
            proposal=proposal_data,
            voter_agent_ids=voter_ids,
            project_id=args.project_id,
            threshold=args.threshold,
        )

    elif args.pattern == "veto":
        output_data = json.loads(args.output)
        result = veto_pattern(
            output=output_data,
            authority_agent_id=args.authority,
            topic=args.topic,
            project_id=args.project_id,
        )

    elif args.pattern == "escalation":
        context_data = json.loads(args.context)
        result = escalation_pattern(
            task_context=context_data,
            escalation_reason=args.reason,
            project_id=args.project_id,
        )

    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
