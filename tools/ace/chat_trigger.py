# CUI // SP-CTI
"""ACE chat trigger — detect trigger conditions in chat messages and launch ACE.

Provides:
    detect_ace_trigger(content) -> 'explicit' | 'implicit' | None
    maybe_launch_ace(context_id, content, user_id, project_id) -> instance_id | None

Trigger conditions
------------------
Explicit  "@team <problem>" at the start of the message.
Implicit  Message is 200+ characters AND matches 4+ distinct RICOAS signal patterns.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from icdev.tools.ace import controller as _ace_controller

_ORIGINAL_CONTROLLER_CLS = _ace_controller.ACEController

#: Module-level patch point, read by the resolution in ``maybe_launch_ace``.
#: It has to actually exist: ``monkeypatch.setattr(chat_trigger,
#: "ACEController", ...)`` raises AttributeError on a name the module never
#: defines, so the seam the code below documents was unusable — the whole
#: `getattr(sys.modules[__name__], "ACEController", ...)` branch could only
#: ever return its default.
ACEController = _ORIGINAL_CONTROLLER_CLS

# Explicit trigger: message starts with @team
_EXPLICIT_RE = re.compile(r"^\s*@team\b", re.IGNORECASE)

# RICOAS signal patterns (subset of problem_classifier._SIGNALS)
_RICOAS_SIGNAL_RES: list[re.Pattern] = [
    re.compile(r"\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bmust\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bshould\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bneeds? to\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bhas to\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\brequirement[s]?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\buser stor(?:y|ies)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bacceptance criteri(?:a|on)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bthe system\b.{0,60}\b(?:shall|should|must|will|needs?)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bfunctional requirement\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:create|build|develop|design|implement)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:deploy|integrate|generate)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:monitor|capture|track|detect|alert)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:analyze|analyse|process|correlate|aggregate)\b", re.IGNORECASE | re.DOTALL),
]

_IMPLICIT_MIN_LEN = 200
_IMPLICIT_MIN_SIGNALS = 4


def count_ricoas_signals(text: str) -> int:
    """Count distinct RICOAS signal patterns matched in text."""
    return sum(1 for p in _RICOAS_SIGNAL_RES if p.search(text))


def detect_ace_trigger(content: str) -> Optional[str]:
    """Classify the trigger type for a chat message.

    Returns:
        'explicit'  if the message starts with '@team'
        'implicit'  if 200+ chars AND 4+ RICOAS signals match
        None        otherwise
    """
    if _EXPLICIT_RE.match(content):
        return "explicit"
    if len(content) >= _IMPLICIT_MIN_LEN and count_ricoas_signals(content) >= _IMPLICIT_MIN_SIGNALS:
        return "implicit"
    return None


def maybe_launch_ace(
    context_id: str,
    content: str,
    user_id: str = "system",
    project_id: str = "",
) -> Optional[str]:
    """Detect trigger and launch ACE if appropriate.

    Returns:
        ACE instance_id if launched, None otherwise.
    """
    trigger_type = detect_ace_trigger(content)
    if trigger_type is None:
        return None

    if trigger_type == "explicit":
        problem_text = _EXPLICIT_RE.sub("", content).strip()
    else:
        problem_text = content

    # Tests patch either the controller class directly or the module-level alias.
    # Prefer a patched controller module, then the module-level alias, then default.
    if _ace_controller.ACEController is not _ORIGINAL_CONTROLLER_CLS:
        _ctrl = _ace_controller.ACEController
    else:
        _ctrl = getattr(sys.modules[__name__], "ACEController", _ORIGINAL_CONTROLLER_CLS)

    return _ctrl.get_instance().launch(
        problem_text=problem_text,
        trigger_source="chat",
        trigger_ref=context_id,
        user_id=user_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Suggest-then-confirm
# ---------------------------------------------------------------------------
#
# `maybe_launch_ace` above is deliberately left untouched: tests/
# test_ace_chat_trigger.py monkeypatches `chat_trigger.ACEController` and asserts
# an immediate launch with trigger_source == "chat". That is a regression
# contract, and "@team <problem>" should stay immediate anyway -- an explicit
# command IS the approval.
#
# What follows is the implicit path. A message that merely LOOKS like a
# multi-step goal now produces a proposal a human can approve, instead of
# silently spawning a team of agents with read/write/execute agency.

import json as _json  # noqa: E402
import uuid as _uuid  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from datetime import timezone as _tz  # noqa: E402

from icdev.tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

_DB_ENV = "ICDEV_ACE_DB_URL"

#: A proposal older than this is stale -- the conversation has moved on.
SUGGESTION_TTL_SECONDS = 60 * 60 * 6

#: Chat-spawned teams are capped tighter than the global MAX_TEAM_SIZE (8).
#: Lowering the global cap would regress other paths, e.g. the 5-role doc team.
MAX_CHAT_TEAM = 5


def _now() -> str:
    return _dt.now(_tz.utc).isoformat(timespec="seconds")


def _conn():
    from icdev.tools.ace.db.init_db import init as _init
    from icdev.tools.db.storage import get_canvas_connection

    _init()
    return get_canvas_connection(_DB_ENV)


def _audit(action: str, detail: str, instance_id: str = "") -> None:
    """Append a decision to the immutable trail.

    ace_team_suggestions itself transitions state, so the durable record of who
    approved what lives in ace_audit_log, which is append-only.
    """
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, "
                "actor, classification, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (instance_id, "", action, detail, "chat", "CUI", _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- auditing must not block the flow
        logger.warning("chat_trigger: audit write failed for %s: %s", action, exc)


def _classify_roles(content: str) -> list[dict]:
    """Best-effort roster preview. Never raises; an empty roster is valid."""
    try:
        from icdev.tools.ace.problem_classifier import ProblemClassifierLens

        lens = ProblemClassifierLens(content)
        manifest = lens.run()
        known = {r.role_id: r for r in lens._role_loader.list_roles()}
        out: list[dict] = []
        for slot in (getattr(manifest, "slots", None) or [])[:MAX_CHAT_TEAM]:
            role = known.get(slot.role_id)
            out.append({
                "role_id": slot.role_id,
                "display_name": getattr(role, "display_name", "") or slot.role_id,
                "count": int(getattr(slot, "count", 1)),
                "status": "existing" if role is not None else "to_generate",
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_trigger: role classification failed: %s", exc)
        return []


def build_team_proposal(
    context_id: str,
    content: str,
    user_id: str = "system",
    project_id: str = "",
) -> Optional[dict]:
    """Return a persisted team proposal for *content*, or None.

    Returns None when the message does not warrant a team, or when it is an
    explicit ``@team`` command (which the caller launches directly).

    The proposal is written to ``ace_team_suggestions`` in state ``proposed``
    and returned as a dict suitable for a ``content_type='action_card'`` chat
    message. Nothing is launched and no SME is generated here -- generation is
    deferred until approval, so a declined proposal costs no LLM spend.
    """
    if detect_ace_trigger(content) != "implicit":
        return None

    roles = _classify_roles(content)
    suggestion_id = "sug-" + _uuid.uuid4().hex[:12]

    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO ace_team_suggestions "
                "(id, context_id, user_id, project_id, problem_text, "
                " proposed_roles_json, state, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s)",
                (
                    suggestion_id, context_id, user_id, project_id, content,
                    _json.dumps(roles), _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_trigger: could not persist proposal: %s", exc)
        return None

    role_ids = [r["role_id"] for r in roles]
    _audit("team_proposed", "suggestion=%s roles=%s" % (suggestion_id, role_ids))

    return {
        "card": "sme_team_suggestion",
        "suggestion_id": suggestion_id,
        "roles": roles,
        "estimated_agents": sum(int(r.get("count", 1)) for r in roles),
        "rationale": (
            "This message reads as a multi-step goal spanning more than one "
            "specialty. A co-worker team could take it on."
        ),
        "actions": [
            {"id": "approve", "label": "Spin up the team"},
            {"id": "decline", "label": "No thanks"},
        ],
    }


def get_proposal(suggestion_id: str) -> Optional[dict]:
    """Return the stored proposal row, or None."""
    try:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT id, context_id, user_id, project_id, problem_text, "
                "proposed_roles_json, state, instance_id, created_at "
                "FROM ace_team_suggestions WHERE id = %s",
                (suggestion_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_trigger: proposal lookup failed: %s", exc)
        return None
    return dict(row) if row else None


def is_expired(proposal: dict) -> bool:
    """True when the proposal is older than the TTL.

    Evaluated at read time rather than by a reaper: a stale row is harmless
    until someone tries to act on it.
    """
    try:
        created = _dt.fromisoformat(str(proposal.get("created_at")))
        if created.tzinfo is None:
            created = created.replace(tzinfo=_tz.utc)
    except Exception:  # noqa: BLE001
        return False
    return (_dt.now(_tz.utc) - created).total_seconds() > SUGGESTION_TTL_SECONDS


def decline_proposal(suggestion_id: str, reason: str = "") -> bool:
    """Mark a proposal declined. Returns True when a row was updated."""
    proposal = get_proposal(suggestion_id)
    if not proposal or proposal["state"] != "proposed":
        return False
    try:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE ace_team_suggestions SET state='declined', decline_reason=%s, "
                "decided_at=%s WHERE id=%s AND state='proposed'",
                (reason[:400], _now(), suggestion_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_trigger: decline failed: %s", exc)
        return False
    _audit("team_declined", "suggestion=%s reason=%s" % (suggestion_id, reason[:120]))
    return True


def confirm_proposal(suggestion_id: str, user_id: str = "system") -> dict:
    """Approve a proposal and launch the team.

    The only path that spawns agents from an implicit chat trigger, and it runs
    exactly once per proposal -- the state guard makes a double-click idempotent
    rather than launching twice.

    Returns ``{"ok": bool, "instance_id": str, "error": str}``.
    """
    proposal = get_proposal(suggestion_id)
    if not proposal:
        return {"ok": False, "instance_id": "", "error": "unknown suggestion"}
    if proposal["state"] == "launched":
        return {"ok": True, "instance_id": proposal["instance_id"], "error": ""}
    if proposal["state"] != "proposed":
        return {"ok": False, "instance_id": "", "error": "already " + str(proposal["state"])}
    if is_expired(proposal):
        decline_proposal(suggestion_id, "expired")
        return {"ok": False, "instance_id": "", "error": "expired"}

    try:
        roles = _json.loads(proposal.get("proposed_roles_json") or "[]")
    except Exception:  # noqa: BLE001
        roles = []

    # Only roles that actually exist may be launch targets. An unresolvable id
    # becomes a ghost coworker that fails role_not_found, so passing one is
    # worse than passing none.
    role_ids = [r["role_id"] for r in roles if r.get("status") == "existing"]

    if _ace_controller.ACEController is not _ORIGINAL_CONTROLLER_CLS:
        _ctrl = _ace_controller.ACEController
    else:
        _ctrl = getattr(sys.modules[__name__], "ACEController", _ORIGINAL_CONTROLLER_CLS)

    try:
        instance_id = _ctrl.get_instance().launch(
            problem_text=proposal["problem_text"],
            trigger_source="chat_suggestion",
            trigger_ref=proposal["context_id"],
            user_id=user_id or proposal["user_id"],
            project_id=proposal["project_id"],
            role_ids=role_ids or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_trigger: launch from proposal failed: %s", exc)
        return {"ok": False, "instance_id": "", "error": str(exc)}

    try:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE ace_team_suggestions SET state='launched', instance_id=%s, "
                "decided_at=%s WHERE id=%s",
                (instance_id, _now(), suggestion_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- the team IS running; report success
        logger.warning("chat_trigger: could not mark proposal launched: %s", exc)

    _audit("team_approved", "suggestion=%s roles=%s" % (suggestion_id, role_ids), instance_id)
    return {"ok": True, "instance_id": instance_id, "error": ""}
