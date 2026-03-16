#!/usr/bin/env python3
"""Socratic dialogue routes — AI-guided critical thinking sessions."""
import logging

from fastapi import APIRouter, Request

from src.db import dynamo
from src.models import SocraticRequest
from src.services import socratic_engine

logger = logging.getLogger("intaas.routes.socratic")
router = APIRouter()


@router.post("/{topic_id}")
async def start_or_continue_socratic(topic_id: str, req: SocraticRequest, request: Request):
    """Start or continue a Socratic dialogue about a topic.

    The AI never gives direct answers. It asks progressively deeper questions
    to develop the user's critical thinking about the topic's perspectives.

    5-turn model (academically validated):
      1. Assumption challenge — "What assumptions does Source A make?"
      2. Evidence demand — "What evidence supports Source B's claim?"
      3. Contrarian probe — "What would someone who disagrees say?"
      4. Omission detection — "What facts are ALL sources ignoring?"
      5. Synthesis invitation — "Given all this, what's YOUR assessment?"
    """
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    session_id = req.session_id
    if not session_id:
        session = dynamo.create_socratic_session(topic_id)
        session_id = session["session_id"]

    # Get existing session
    session = dynamo.get_socratic_session(session_id)
    if not session:
        return {"error": "Session not found"}

    turns = session.get("turns", [])
    turn_count = len(turns)

    # Store user response if provided
    if req.user_response and turn_count > 0:
        turn_count += 1
        dynamo.add_socratic_turn(session_id, turn_count, "user", req.user_response)

    # Generate next AI question
    articles = dynamo.get_articles_for_topic(topic_id)
    perspectives = dynamo.get_perspectives(topic_id)

    ai_turn = socratic_engine.generate_next_question(
        topic=topic,
        articles=articles,
        perspectives=perspectives,
        previous_turns=turns,
        user_response=req.user_response,
    )

    turn_count += 1
    dynamo.add_socratic_turn(
        session_id, turn_count, "ai",
        ai_turn["content"],
        ai_turn.get("question_type", ""),
    )

    return {
        "session_id": session_id,
        "topic_id": topic_id,
        "turn_number": turn_count,
        "ai_question": ai_turn["content"],
        "question_type": ai_turn.get("question_type", ""),
        "turns_remaining": max(0, 5 - (turn_count // 2)),
        "status": "active" if turn_count < 10 else "complete",
    }


@router.get("/{session_id}")
async def get_socratic_session(session_id: str, request: Request):
    """Get full transcript of a Socratic dialogue session."""
    session = dynamo.get_socratic_session(session_id)
    if not session:
        return {"error": "Session not found"}

    return {
        "session_id": session_id,
        "topic_id": session.get("topic_id", ""),
        "turn_count": int(session.get("turn_count", 0)),
        "status": session.get("status", "active"),
        "turns": [
            {
                "turn_number": int(t.get("turn_number", 0)),
                "role": t.get("role", ""),
                "content": t.get("content", ""),
                "question_type": t.get("question_type", ""),
            }
            for t in session.get("turns", [])
        ],
    }
