# CUI // SP-CTI
"""Outbound consult: ICDEV -> idea_lab's Specialist Council pressure-test.

This is the reverse of the existing idea_lab -> ICDEV bridge
(tools/ace/persona_query.py, tools/llm/chain_orchestrator.py::invoke_council,
exposed as the ace_persona_query/council_query MCP tools). Deliberately
scoped to idea_lab's Council pressure-test only, NOT generic SME Q&A:
idea_lab's own Specialist advisor logic (tools/qa/it_advisor.py in that
project) is itself mostly a pass-through to ICDEV's own
ace_persona_query/council_query -- calling it from here for plain Q&A
would be an ICDEV -> idea_lab -> ICDEV round trip for something ICDEV can
already do in-process. Only idea_lab's Council (which also runs its own
local-fallback + case-history grounding idea_lab uniquely owns) is worth
crossing the process boundary for.

RFI/Proposal content is CUI and today's rfi_*/proposal_drafting LLM
functions are LOCAL-ONLY (args/llm_config.yaml routes them to
qwen3-local/llama-local). idea_lab has no local/air-gapped LLM path -- it
is cloud-only. So `question`/`context_summary` here are ALWAYS sanitized
via tools.redaction.govcon_sanitizer.GovConSanitizer before leaving this
process (is_local_only=False -- this call genuinely leaves the network
boundary, unlike the LOCAL-ONLY rfi_*/proposal_drafting functions
themselves, so the sanitizer's "skip for local-only routing" exemption
must never apply here). Callers are still responsible for building
`question`/`context_summary` from an ABSTRACTED summary, never verbatim
section/requirement text -- redaction is defense-in-depth, not a license
to pass raw content.

idea_lab's /api/specialist/consult/council is an async job (submit -> poll)
since a real Council pass takes ~90-150s (see idea_lab's
backend/routers/specialist.py). request_council_consult() encapsulates the
whole submit-then-poll cycle into one blocking call -- only call this from
a background/reflex context, never inline in a page-rendering request.

Degrades to None on ANY failure (idea_lab unreachable, IDEA_LAB_BASE_URL/
SPECIALIST_API_KEY not configured, HTTP error, job failure, timeout) --
never raises. Callers must treat None identically to "consult unavailable,
proceed without it" and never block their own pipeline on this.
"""
from __future__ import annotations

import os
import time
from typing import Any

_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_POLL_INTERVAL_SECONDS = 3.0
_DEFAULT_MAX_WAIT_SECONDS = 170.0  # idea_lab's council_timeout_seconds (150s) + margin
_SUBMIT_TIMEOUT_SECONDS = 10.0
_POLL_TIMEOUT_SECONDS = 10.0


def _base_url() -> str:
    return os.environ.get("IDEA_LAB_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return os.environ.get("SPECIALIST_API_KEY", "").strip()


def _redact(question: str, context_summary: str) -> tuple[str, str] | None:
    """Sanitize both strings via GovConSanitizer before they leave this process.

    FAILS CLOSED. Returns None if the sanitizer cannot run, and the consult is then
    abandoned — nothing is sent.

    This used to `return question, context_summary` on any exception: if the redaction
    module failed to import, or the sanitizer raised for any reason, the RAW text went
    to idea_lab anyway. idea_lab is cloud-only and this call genuinely leaves the network
    boundary, so that fallback was an unsanitized CUI egress path that opened precisely
    when redaction was broken — the one moment it must not open.

    It also silently contradicted `redaction.fail_closed: true` in
    args/redaction_config.yaml. A fail-closed policy with a fail-open bypass in the one
    module that crosses the boundary is not a policy.

    Degrading to "no consult" costs an advisory third opinion. Degrading to "send it raw"
    costs a spill, and nothing in the pipeline depends on this call.
    """
    try:
        from tools.redaction.govcon_sanitizer import GovConSanitizer

        sanitizer = GovConSanitizer()
        safe_question, _ = sanitizer.sanitize_for_llm(
            question, function_name="specialist_consult", impact_level="IL4", is_local_only=False,
        )
        safe_context, _ = sanitizer.sanitize_for_llm(
            context_summary, function_name="specialist_consult", impact_level="IL4", is_local_only=False,
        ) if context_summary else (context_summary, {})
        return safe_question, safe_context
    except Exception:
        return None


def request_council_consult(
    topic_domain: str,
    question: str,
    context_summary: str = "",
    max_wait_seconds: float = _DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any] | None:
    """Submit `question` to idea_lab's ad-hoc Council consult
    (POST /api/specialist/consult/council), then poll for its result
    (GET .../consult/council/{job_id}) until it completes, fails, or
    `max_wait_seconds` elapses.

    Returns {"verdict": str, "stop_reason": str | None, "source": str}
    (source is "icdev_council" or "local_fallback", per idea_lab's own
    ICDEV-unreachable degrade path), or None on any failure -- including
    SPECIALIST_API_KEY not configured, idea_lab unreachable, a failed job,
    or a timeout."""
    if not (question or "").strip():
        return None

    api_key = _api_key()
    if not api_key:
        return None

    try:
        import requests
    except ImportError:
        return None

    redacted = _redact(question, context_summary)
    if redacted is None:
        # The sanitizer could not run. Send NOTHING. A third opinion is worth less than
        # not spilling CUI to a cloud-only service, and no caller depends on this.
        return None
    safe_question, safe_context = redacted

    base_url = _base_url()
    headers = {"X-Specialist-Api-Key": api_key}

    try:
        submit_resp = requests.post(
            f"{base_url}/api/specialist/consult/council",
            json={"topic_domain": topic_domain, "question": safe_question, "context_summary": safe_context},
            headers=headers,
            timeout=_SUBMIT_TIMEOUT_SECONDS,
        )
        if submit_resp.status_code != 200:
            return None
        job_id = submit_resp.json().get("job_id")
        if not job_id:
            return None
    except Exception:
        return None

    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            poll_resp = requests.get(
                f"{base_url}/api/specialist/consult/council/{job_id}", headers=headers, timeout=_POLL_TIMEOUT_SECONDS,
            )
            if poll_resp.status_code != 200:
                return None
            body = poll_resp.json()
        except Exception:
            return None

        status = body.get("status")
        if status == "completed":
            return {
                "verdict": body.get("verdict"),
                "stop_reason": body.get("stop_reason"),
                "source": body.get("source"),
            }
        if status == "failed":
            return None

        time.sleep(poll_interval_seconds)

    return None
