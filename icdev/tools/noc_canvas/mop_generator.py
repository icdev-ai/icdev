# CUI // SP-CTI
"""NOCC MOP generator — LLM-backed Method of Procedure generation."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.noc_canvas.mop_generator")

_MOP_STEP_KEYS = {"step", "action", "rollback", "timeout_min", "verification"}

_TEMPLATE_STEPS = [
    {
        "step": 1,
        "action": "Pre-maintenance notification — notify NOC and affected customers",
        "rollback": "Cancel notification; reschedule window",
        "timeout_min": 15,
        "verification": "Confirm notification acknowledgment from stakeholders",
    },
    {
        "step": 2,
        "action": "Verify current state — capture interface/BGP/circuit status before change",
        "rollback": "Document baseline; do not proceed if unexpected state detected",
        "timeout_min": 10,
        "verification": "Baseline captured and stored",
    },
    {
        "step": 3,
        "action": "Execute change per RFC instructions",
        "rollback": "Revert configuration to baseline state",
        "timeout_min": 30,
        "verification": "Change applied without errors",
    },
    {
        "step": 4,
        "action": "Verify post-change state — confirm service restoration",
        "rollback": "Escalate to senior NOC engineer; engage vendor TAC",
        "timeout_min": 15,
        "verification": "All affected circuits/BGP sessions operational",
    },
    {
        "step": 5,
        "action": "Close maintenance window — notify stakeholders of completion",
        "rollback": "N/A",
        "timeout_min": 5,
        "verification": "Maintenance window closed in ticketing system",
    },
]


#: Seconds a REQUEST may spend waiting on the model before falling back.
#:
#: `generate_mop` runs inside `POST /api/noc/mops/generate`, and `router.invoke`
#: has no timeout of its own: `LLMRequest` carries max_tokens and effort but no
#: deadline. An unreachable provider therefore does not fail fast — it blocks on
#: the network. That costs ~0.2s on a developer machine, where the connection is
#: refused immediately, and MINUTES on a CI runner or an air-gapped host, where
#: it is dropped and the client waits out its own socket timeout.
#:
#: The `except Exception` below was always correct and never reached, because a
#: hang is not an exception. The fallback needs a clock, not a broader catch.
#: Override with ICDEV_NOC_MOP_LLM_TIMEOUT.
MOP_LLM_TIMEOUT_SECONDS = float(
    os.environ.get("ICDEV_NOC_MOP_LLM_TIMEOUT", "8") or 8
)


def _invoke_bounded(prompt: str, timeout: float):
    """Call the model, or give up. Returns the raw text, or None.

    The worker is a DAEMON thread and is deliberately abandoned on timeout:
    Python cannot kill a thread, so the request stops WAITING while the call
    finishes in the background and its result is discarded. That is the same
    cooperative bound the SAG dispatch layer documents for `stop_event`, and it
    is what makes this safe to put in a request path — the handler returns on
    the clock whatever the provider does.
    """
    import concurrent.futures

    def _call():
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        resp = router.invoke(
            "narrative_generation",
            LLMRequest(messages=[{"role": "user", "content": prompt}],
                       max_tokens=1500),
        )
        return resp.content or ""

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="noc-mop-llm")
    try:
        future = pool.submit(_call)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "noc mop: model did not answer within %.1fs — falling back to the "
            "template. The request is bounded on purpose; see "
            "MOP_LLM_TIMEOUT_SECONDS.", timeout)
        return None
    except Exception as exc:  # noqa: BLE001 — a model is a layer, not a dep
        logger.info("noc mop: model unavailable (%s) — using the template",
                    type(exc).__name__)
        return None
    finally:
        # Do NOT wait: shutdown(wait=True) would re-introduce the hang this
        # function exists to remove.
        pool.shutdown(wait=False)


def generate_mop(rfc: dict, context: str = "") -> dict:
    """Generate MOP steps from RFC metadata.

    Attempts LLM generation under a BOUNDED wait; falls back to the template
    skeleton on failure OR on timeout. Returns a dict with keys: mop_id,
    mop_number, steps, generated_by, ai_prompt.
    """
    mop_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mop_number = f"MOP-{now.strftime('%Y-%m%d')}-{mop_id[:6].upper()}"

    prompt = (
        f"Generate a detailed Method of Procedure (MOP) for the following network change.\n"
        f"RFC Title: {rfc.get('title', 'Unnamed change')}\n"
        f"Change Type: {rfc.get('change_type', 'standard')}\n"
        f"Risk Level: {rfc.get('risk_level', 'medium')}\n"
        f"Additional Context: {context}\n\n"
        f"Return a JSON array of steps. Each step must have: "
        f"step (int), action (str), rollback (str), timeout_min (int), verification (str)."
    )

    steps = None
    raw = _invoke_bounded(prompt, MOP_LLM_TIMEOUT_SECONDS)
    if raw:
        try:
            # Extract JSON array from response
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                steps = json.loads(raw[start:end])
        except Exception:  # noqa: BLE001 — a malformed answer is a template case
            steps = None

    if not steps:
        steps = _TEMPLATE_STEPS[:]
        generated_by = "ai_template"
    else:
        generated_by = "ai"

    return {
        "mop_id": mop_id,
        "mop_number": mop_number,
        "steps": steps,
        "generated_by": generated_by,
        "ai_prompt": prompt,
    }


def validate_mop_steps(steps: list[dict]) -> list[str]:
    """Return list of validation errors; empty list means valid."""
    errors = []
    required = {"step", "action", "rollback", "timeout_min", "verification"}
    for i, step in enumerate(steps):
        missing = required - set(step.keys())
        if missing:
            errors.append(f"Step {i + 1}: missing keys {missing}")
        if not isinstance(step.get("timeout_min"), (int, float)):
            errors.append(f"Step {i + 1}: timeout_min must be numeric")
    return errors


def mop_to_markdown(mop: dict) -> str:
    """Format a MOP as a structured Markdown document for NOC handoff."""
    lines = [
        f"# {mop.get('title', mop.get('mop_number', 'MOP'))}",
        f"**MOP Number:** {mop.get('mop_number', '')}",
        f"**Generated by:** {mop.get('generated_by', 'manual')}",
        "",
        "## Steps",
        "",
    ]
    steps = mop.get("steps", [])
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            steps = []
    for step in steps:
        lines.append(f"### Step {step.get('step', '?')}: {step.get('action', '')}")
        lines.append(f"- **Timeout:** {step.get('timeout_min', '?')} min")
        lines.append(f"- **Rollback:** {step.get('rollback', 'N/A')}")
        lines.append(f"- **Verification:** {step.get('verification', 'N/A')}")
        lines.append("")
    return "\n".join(lines)


def save_mop(conn: Any, rfc_id: str, mop: dict) -> str:
    """Persist a generated MOP to the DB and return mop_id."""
    steps_str = json.dumps(mop.get("steps", []))
    mop_id = mop["mop_id"]
    mop_number = mop["mop_number"]

    for sql, params in [
        (
            "INSERT INTO noc_mops (id, mop_number, title, rfc_id, steps_json, generated_by, ai_prompt) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (mop_id, mop_number, mop.get("title", mop_number),
             rfc_id, steps_str, mop.get("generated_by", "ai"), mop.get("ai_prompt", "")),
        ),
        (
            "INSERT INTO noc_mops (id, mop_number, title, rfc_id, steps_json, generated_by, ai_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mop_id, mop_number, mop.get("title", mop_number),
             rfc_id, steps_str, mop.get("generated_by", "ai"), mop.get("ai_prompt", "")),
        ),
    ]:
        try:
            conn.execute(sql, params)
            conn.commit()
            return mop_id
        except Exception:
            continue
    raise RuntimeError("Failed to save MOP")
