# CUI // SP-CTI
"""Return a finished co-worker team's work to the conversation that asked for it.

``goals/ace_coworker.md`` has claimed since it was written that the "final
summary is injected as an assistant message on the originating session". No such
code existed. A team launched from chat did its work, wrote artifacts, and the
conversation never heard back — the user had to know to go and look at
``/coworker``.

Why not the webhook
-------------------
``ACEController.launch`` takes a ``webhook_url`` and this module deliberately
does not use it. A webhook is an outbound HTTP POST, so delivering an internal
result that way means the dashboard calling *itself* over loopback: it needs a
reachable base URL, an inbound endpoint, and it breaks in air-gapped and
odd-port deployments. Webhooks stay for genuinely external consumers (compass,
idea_lab). Internal delivery rides the in-process completion path that
``controller._run`` already executes.

LLM-agnostic: synthesis goes through ``LLMRouter`` by logical function name, so
the deployment decides the model. No model IDs appear here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DB_ENV = "ICDEV_ACE_DB_URL"

#: Trigger sources whose results belong back in a conversation.
_CHAT_TRIGGERS = ("chat", "chat_suggestion")

#: Logical LLM function for the summariser. Routed via args/llm_config.yaml, so
#: an operator can point it at a stronger model than the workers use without any
#: code change.
SYNTHESIS_FUNCTION = "ace_result_synthesis"

_MAX_ARTIFACT_CHARS = 6000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ace_conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection(_DB_ENV)


def _instance_context(instance_id: str) -> dict[str, Any]:
    """Return the launch config for *instance_id*, or {}."""
    try:
        conn = _ace_conn()
        try:
            row = conn.execute(
                "SELECT config_json, state FROM ace_instances WHERE id = %s",
                (instance_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("chat_result: instance lookup failed: %s", exc)
        return {}
    if not row:
        return {}
    data = dict(row)
    try:
        cfg = json.loads(data.get("config_json") or "{}")
    except Exception:  # noqa: BLE001
        cfg = {}
    cfg["_state"] = data.get("state") or ""
    return cfg


def _collect_output(instance_id: str) -> str:
    """Concatenate the team's artifacts, newest first, bounded."""
    try:
        conn = _ace_conn()
        try:
            rows = conn.execute(
                "SELECT title, content_md FROM ace_artifacts WHERE instance_id = %s "
                "ORDER BY created_at DESC",
                (instance_id,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("chat_result: artifact read failed: %s", exc)
        return ""

    parts: list[str] = []
    total = 0
    for row in rows:
        data = dict(row)
        body = (data.get("content_md") or "").strip()
        if not body:
            continue
        chunk = f"### {data.get('title') or 'Untitled'}\n{body}"
        if total + len(chunk) > _MAX_ARTIFACT_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n".join(parts)


def _synthesize(problem_text: str, output: str) -> str:
    """Summarise the team's output for a chat reader.

    Falls back to the raw artifacts when no provider is reachable — a degraded
    answer is far better than silence, which is what this whole module exists to
    fix.
    """
    if not output:
        return ""
    try:
        from icdev.tools.llm.provider import LLMRequest
        from icdev.tools.llm.router import LLMRouter

        response = LLMRouter().invoke(
            SYNTHESIS_FUNCTION,
            LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        "A team of specialists worked on the request below. "
                        "Summarise what they found for the person who asked, in "
                        "under 200 words. Lead with the answer, not the process. "
                        "Do not invent anything that is not in their output.\n\n"
                        f"Request:\n{problem_text[:1500]}\n\n"
                        f"Their output:\n{output}"
                    ),
                }],
                max_tokens=500,
                temperature=0.3,
            ),
        )
        text = (getattr(response, "content", "") or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — degrade, never drop the result
        logger.warning("chat_result: synthesis unavailable (%s); posting raw", exc)
    return output[:2000]


def _post_to_chat(context_id: str, content: str, content_type: str, meta: dict) -> bool:
    """Insert a message into the originating chat context."""
    try:
        # Module-level singleton, not a factory.
        from icdev.tools.dashboard.chat_manager import chat_manager as manager
    except Exception as exc:  # noqa: BLE001
        logger.debug("chat_result: chat manager unavailable: %s", exc)
        return False

    try:
        ctx = manager._contexts.get(context_id)  # noqa: SLF001 — same subsystem
        with manager._lock:  # noqa: SLF001
            if ctx is None:
                return False
            ctx.turn_number += 1
            turn = ctx.turn_number
        manager._db_insert_message(  # noqa: SLF001
            context_id, turn, "assistant", content,
            content_type=content_type, metadata=meta,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_result: could not post to chat %s: %s", context_id, exc)
        return False


def deliver(instance_id: str, *, state: str = "complete") -> bool:
    """Post a finished team's result back into its originating conversation.

    Returns True when a message was posted. Safe to call for any instance: work
    not launched from chat is ignored.

    ``state`` distinguishes completion from failure. A failed or aborted run
    still posts — silence after "spinning up a team" is the worst outcome,
    because the user has no way to tell a crash from slow progress.
    """
    cfg = _instance_context(instance_id)
    if not cfg:
        return False
    if cfg.get("trigger_source") not in _CHAT_TRIGGERS:
        return False

    context_id = str(cfg.get("trigger_ref") or "")
    if not context_id:
        return False

    deep_link = f"/coworker/{instance_id}"
    meta = {"ace_instance_id": instance_id, "deep_link": deep_link, "state": state}

    if state != "complete":
        body = (
            f"The co-worker team stopped without finishing (`{state}`). "
            f"Nothing has been applied.\n\n[Inspect the run]({deep_link})"
        )
        return _post_to_chat(context_id, body, "error", meta)

    output = _collect_output(instance_id)
    if not output:
        # A 'complete' run with no artifacts is exactly the failure mode that
        # made ACE look healthy while producing nothing. Say so plainly rather
        # than posting an empty summary.
        body = (
            "The co-worker team finished but produced no output. "
            f"This usually means its steps could not run.\n\n[Inspect the run]({deep_link})"
        )
        return _post_to_chat(context_id, body, "error", meta)

    summary = _synthesize(str(cfg.get("problem_text") or ""), output)
    body = f"{summary}\n\n[View the team's full work]({deep_link})"
    return _post_to_chat(context_id, body, "markdown", meta)
