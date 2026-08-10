# CUI // SP-CTI
"""Thin adapters routing ACE and workflow_hitl into the unified inbox (agov-inbox-05).

ICDEV has four approval gates. Three of them ask a human the same question —
"may this proceed?" — through three unrelated stores, so an operator watching one
never sees the other two. This module gives those three ONE queue without
rewriting any of them.

## Adapt, do not rewrite

Each gate keeps its own store as the source of truth for its own waiter:

  ACE            ``ace_audit_log`` rows. A pause is an ``action='hitl_pending'``
                 row; it is released by INSERTing a matching ``'hitl_resolved'``
                 row (same ``coworker_id`` + same ``detail``), which is what
                 ``CoWorkerThread._wait_for_hitl_resolution`` polls for.
  workflow_hitl  ``wf_approvals`` rows. A pause is ``status='pending'``; it is
                 released by ``feedback.submit_feedback``, which advances the
                 stage.

``approval_items`` is a MIRROR of those, never a replacement. Two consequences
that are the whole point of the design:

  1. **The mirror is best-effort in the pending direction.** If the inbox is
     unmigrated, unreachable, or raises, the originating gate still holds exactly
     as it does today — an operator just has one fewer place to answer from.
     Failing the ACE gate closed because a mirror row could not be written would
     make an optional delivery channel load-bearing, and failing it OPEN would
     turn a missing table into an approval. Neither: the gate is untouched.
  2. **A resolution from either side settles the other.** Answer in the ACE UI
     and the mirrored item goes ``resolved``; answer in the inbox and the ACE
     audit row is INSERTed and the waiting ``CoWorkerThread`` wakes. The inbox
     never becomes a second, divergent opinion about whether the ask is open.

``ace_audit_log`` stays APPEND-ONLY throughout. Resolving from the inbox INSERTs
a ``hitl_resolved`` row through the existing :meth:`HITLGate.resolve`; nothing
here UPDATEs an ACE row. The mutable state lives only in ``approval_items``,
which is deliberately not append-only (see migration ``20260809203855``).

## Explicitly out of scope: ``tools/integration/approval_manager.py``

``submit_for_approval`` / ``review_approval`` is document-, COA- and
boundary-level approval with multi-reviewer lists, a different lifetime and a
different audience from a mid-run gate. Its reviewer semantics do not survive
being flattened into one item with one ``resolved_by``. It is untouched.

## Where the correlation key lives

``approval_items`` has no metadata column and must not grow one for this, so the
key each origin needs to find its own record is carried in columns it already
has:

  ACE            ``item_id`` is a digest of ``coworker_id`` + ``detail`` (so the
                 mirror is idempotent — re-mirroring the same pause is a no-op,
                 not a duplicate), ``tool_name`` is ``ace:<coworker_id>``,
                 ``session_id`` is the ACE ``instance_id``. The exact ``detail``
                 string is NOT stored: it is recovered from ACE's own pending
                 list at resolve time by matching the digest. That keeps ACE the
                 single source of truth for what is open, and makes resolving
                 something ACE no longer considers pending impossible.
  workflow_hitl  ``item_id`` is ``wfh-<approval_id>``. ``wf_approvals.id`` is an
                 opaque ``wfa-<hex>`` synthetic key, so embedding it carries no
                 CUI and needs no lookup table.

CLI::

    python tools/agent_runtime/inbox_adapters.py --list --json
    python tools/agent_runtime/inbox_adapters.py --resolve <item_id> --approve
    python tools/agent_runtime/inbox_adapters.py --resolve <item_id> --deny \\
        --reason "not authorised" --json

Use THIS ``--resolve``, not ``approval_inbox.py --resolve``, for a mirrored item:
the store settles the row but knows nothing about releasing an ACE thread or
advancing a workflow stage.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime import approval_inbox  # noqa: E402
from tools.agent_runtime.approval_inbox import (  # noqa: E402
    ORIGIN_ACE,
    ORIGIN_WORKFLOW_HITL,
    ApprovalInboxUnavailable,
    ApprovalItem,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.agent_runtime.inbox_adapters")

# --- ACE -------------------------------------------------------------------
ACE_ITEM_PREFIX = "ace-"
ACE_TOOL_PREFIX = "ace:"
ACE_RULE = "ace.hitl_gate"

# --- workflow_hitl ---------------------------------------------------------
WFH_ITEM_PREFIX = "wfh-"
WFH_TOOL_PREFIX = "workflow_hitl:"
WFH_RULE = "workflow_hitl.stage_gate"

# Neither gate classifies reversibility — an ACE confidence gate and a workflow
# stage gate both mean "a human must decide", with no verdict about what the
# action can undo. `unknown` is that, and it is the tier the approval policy
# already treats as fail-closed, so a mirrored item can never read as safer than
# an inline classification would have made it.
HITL_TIER = "unknown"


class InboxAdapterError(RuntimeError):
    """A settled item could not be released on its originating gate.

    Raised by :func:`resolve` only AFTER the item has been settled, so the
    caller knows the decision is recorded but the waiter is still parked and the
    origin's own UI is the way to clear it.
    """


# ---------------------------------------------------------------------------
# ACE
# ---------------------------------------------------------------------------
def ace_item_id(coworker_id: str, detail: str) -> str:
    """The deterministic mirror id for one ACE HITL pause.

    Derived from the pair that identifies the pause on the ACE side, so
    mirroring the same pause twice produces the same row rather than a second
    one, and so :func:`_ace_pending_detail` can recover ``detail`` by matching
    rather than by storing it.
    """
    digest = hashlib.sha256(
        f"{coworker_id}\x00{detail}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{ACE_ITEM_PREFIX}{digest}"


def _ace_coworker_of(item: ApprovalItem) -> str:
    name = item.tool_name or ""
    return name[len(ACE_TOOL_PREFIX):] if name.startswith(ACE_TOOL_PREFIX) else ""


def _ace_hitl_gate() -> Any:
    """The ``HITLGate`` the running ACE threads actually use.

    ``icdev.tools.ace.coworker_thread`` and ``tools.ace.coworker_thread`` are two
    distinct module objects in a checkout, each holding its OWN in-process
    ``_hitl_events`` wake registry. ``ACEController`` builds threads from the
    ``icdev`` copy, so that is the one whose ``threading.Event`` a resolution has
    to set. The fallback is not cosmetic either — resolving through the wrong
    copy still INSERTs the audit row, so the waiter clears on its 30 s
    cross-process re-check instead of immediately.
    """
    try:
        from icdev.tools.ace.coworker_thread import HITLGate
    except Exception:  # noqa: BLE001 — a wheel may ship only one of the two
        from tools.ace.coworker_thread import HITLGate
    return HITLGate


def mirror_ace_pending(
    *,
    coworker_id: str,
    detail: str,
    instance_id: str = "",
    role_id: str = "",
    inbox: str = approval_inbox.DEFAULT_INBOX,
) -> Optional[ApprovalItem]:
    """Mirror one ACE ``hitl_pending`` into the unified inbox.

    Idempotent: an already-mirrored pause returns the existing item. Returns
    ``None`` when the inbox is unavailable — the ACE gate is unaffected either
    way, because ``ace_audit_log`` remains the thing its waiter polls.
    """
    if not coworker_id:
        return None
    item_id = ace_item_id(coworker_id, detail)
    existing = approval_inbox.get(item_id)
    if existing is not None:
        return existing

    title = f"[ACE] {role_id or coworker_id} needs approval to continue"
    body = "\n".join(
        [
            "Origin: ACE co-worker HITL gate",
            f"Instance: {instance_id or '(unknown)'}",
            f"Co-worker: {coworker_id}",
            f"Role: {role_id or '(unknown)'}",
            f"Why: {detail}",
            "",
            "Approving releases the paused co-worker thread. It INSERTs a "
            "hitl_resolved row into the append-only ace_audit_log — the same "
            "row the ACE UI's Approve button writes.",
        ]
    )
    try:
        return approval_inbox.enqueue(
            item_id=item_id,
            tool_name=f"{ACE_TOOL_PREFIX}{coworker_id}",
            tier=HITL_TIER,
            title=title,
            body=body,
            origin=ORIGIN_ACE,
            session_id=instance_id,
            inbox=inbox,
            rule=ACE_RULE,
        )
    except (ApprovalInboxUnavailable, ValueError) as exc:
        # Best-effort BY DESIGN: see the module docstring. The ACE gate holds
        # regardless, so a mirror that cannot be written costs an operator one
        # delivery channel, never an unapproved action.
        logger.warning(
            "inbox_adapters: could not mirror ACE pause for %s: %s", coworker_id, exc
        )
        return None


def settle_ace(
    coworker_id: str,
    detail: str,
    *,
    approved: bool,
    resolved_by: str = "",
    reason: str = "",
) -> Optional[ApprovalItem]:
    """Settle the mirrored item after ACE resolved the pause on its own side.

    Called from :meth:`HITLGate.resolve` and :meth:`HITLGate.reject`, so an
    operator clicking Approve in the ACE UI (``POST /api/ace/<id>/hitl``) closes
    the inbox item too. Returns ``None`` if there is nothing mirrored or it is
    already settled — both are ordinary, not errors.
    """
    try:
        return approval_inbox.resolve(
            ace_item_id(coworker_id, detail),
            approved=approved,
            resolved_by=resolved_by or "ace-ui",
            reason=reason or ("approved in ACE" if approved else "rejected in ACE"),
        )
    except Exception as exc:  # noqa: BLE001 — never break the ACE resolution path
        logger.warning(
            "inbox_adapters: could not settle mirrored ACE item for %s: %s",
            coworker_id,
            exc,
        )
        return None


def _ace_pending_detail(coworker_id: str, item_id: str) -> Optional[str]:
    """Recover the exact ``detail`` of the pause this item mirrors, from ACE.

    ACE is the source of truth for what is open, so the detail is matched out of
    its live pending list rather than stored here. A pause ACE has already
    resolved is simply not found, which is what stops the inbox INSERTing a
    ``hitl_resolved`` row for something nobody is waiting on.
    """
    for row in _ace_hitl_gate().get_pending(coworker_id) or []:
        detail = str(row.get("detail") or "")
        if ace_item_id(coworker_id, detail) == item_id:
            return detail
    return None


def release_ace(item: ApprovalItem, *, approved: bool) -> str:
    """Release (or reject) the ACE pause an already-settled item mirrors.

    INSERTs the ``hitl_resolved`` / ``hitl_rejected`` row and wakes the parked
    ``CoWorkerThread``. Never UPDATEs an ``ace_audit_log`` row: that table is
    append-only evidence and a resolution is a new fact about it, not an edit to
    the old one.
    """
    coworker_id = _ace_coworker_of(item)
    if not coworker_id:
        raise InboxAdapterError(f"{item.item_id} carries no ACE co-worker id")
    detail = _ace_pending_detail(coworker_id, item.item_id)
    if detail is None:
        raise InboxAdapterError(
            f"ACE no longer lists a pending gate for {coworker_id} matching "
            f"{item.item_id} — it may already have been resolved in the ACE UI"
        )

    gate = _ace_hitl_gate()
    if approved:
        gate.resolve(coworker_id, detail, item.session_id)
    else:
        gate.reject(coworker_id, detail, item.session_id)
    return detail


# ---------------------------------------------------------------------------
# workflow_hitl
# ---------------------------------------------------------------------------
def wfh_item_id(approval_id: str) -> str:
    """The mirror id for one ``wf_approvals`` gate."""
    return f"{WFH_ITEM_PREFIX}{approval_id}"


def _wfh_approval_of(item: ApprovalItem) -> str:
    return (
        item.item_id[len(WFH_ITEM_PREFIX):]
        if item.item_id.startswith(WFH_ITEM_PREFIX)
        else ""
    )


def mirror_workflow_pending(
    *,
    approval_id: str,
    instance_id: str = "",
    stage: str = "",
    task_id: str = "",
    inbox: str = approval_inbox.DEFAULT_INBOX,
) -> Optional[ApprovalItem]:
    """Mirror one pending ``wf_approvals`` gate into the unified inbox.

    Same posture as :func:`mirror_ace_pending`: idempotent, and a failure leaves
    the workflow gate exactly as it is today.
    """
    if not approval_id:
        return None
    item_id = wfh_item_id(approval_id)
    existing = approval_inbox.get(item_id)
    if existing is not None:
        return existing

    title = f"[HITL] {stage or 'stage'} awaiting review"
    body = "\n".join(
        [
            "Origin: workflow_hitl stage gate",
            f"Instance: {instance_id or '(unknown)'}",
            f"Stage: {stage or '(unknown)'}",
            f"Task: {task_id or '(none)'}",
            f"Approval: {approval_id}",
            "",
            "Approving advances the workflow to its next stage through the same "
            "submit_feedback() path the review UI uses.",
        ]
    )
    try:
        return approval_inbox.enqueue(
            item_id=item_id,
            tool_name=f"{WFH_TOOL_PREFIX}{stage or 'stage'}",
            tier=HITL_TIER,
            title=title,
            body=body,
            origin=ORIGIN_WORKFLOW_HITL,
            session_id=instance_id,
            inbox=inbox,
            rule=WFH_RULE,
        )
    except (ApprovalInboxUnavailable, ValueError) as exc:
        logger.warning(
            "inbox_adapters: could not mirror workflow gate %s: %s", approval_id, exc
        )
        return None


def settle_workflow(
    approval_id: str,
    *,
    approved: bool,
    resolved_by: str = "",
    reason: str = "",
) -> Optional[ApprovalItem]:
    """Settle the mirrored item after the workflow gate was decided elsewhere."""
    try:
        return approval_inbox.resolve(
            wfh_item_id(approval_id),
            approved=approved,
            resolved_by=resolved_by or "workflow-hitl-ui",
            reason=reason
            or ("approved in the review UI" if approved else "kicked back in the review UI"),
        )
    except Exception as exc:  # noqa: BLE001 — never break the review path
        logger.warning(
            "inbox_adapters: could not settle mirrored workflow item %s: %s",
            approval_id,
            exc,
        )
        return None


def release_workflow(
    item: ApprovalItem, *, approved: bool, resolved_by: str = "", reason: str = ""
) -> str:
    """Advance (or kick back) the workflow gate an already-settled item mirrors."""
    approval_id = _wfh_approval_of(item)
    if not approval_id:
        raise InboxAdapterError(f"{item.item_id} carries no wf_approvals id")

    from tools.workflow_hitl.feedback import submit_feedback

    who = resolved_by or "approval-inbox"
    if approved:
        submit_feedback(approval_id, "approve", who, comments=reason or None)
    else:
        # kickback_reason is mandatory when ICDEV_HITL_REQUIRE_FEEDBACK is on,
        # and a denial with no stated reason is not worth recording anyway.
        submit_feedback(
            approval_id,
            "kickback",
            who,
            kickback_reason=reason or "denied from the unified approval inbox",
        )
    return approval_id


# ---------------------------------------------------------------------------
# One queue, one answer
# ---------------------------------------------------------------------------
_RELEASERS = {
    ORIGIN_ACE: lambda item, approved, who, why: release_ace(item, approved=approved),
    ORIGIN_WORKFLOW_HITL: lambda item, approved, who, why: release_workflow(
        item, approved=approved, resolved_by=who, reason=why
    ),
}


def resolve(
    item_id: str,
    *,
    approved: bool,
    resolved_by: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Answer one inbox item and release whatever is waiting on it.

    Ordering is deliberate: the item is SETTLED FIRST. ``approval_inbox._settle``
    is a conditional UPDATE that accepts the transition only if it changed
    exactly one row, so settling first is what makes exactly one caller go on to
    release the origin — a Slack reply racing the ACE UI cannot both release.
    The reverse order has no such guard and would allow a double release.

    If the release then fails, the decision is already recorded and
    :class:`InboxAdapterError` says so: the answer is not lost, but the waiter
    must be cleared from its own UI.
    """
    item = approval_inbox.get(item_id)
    if item is None:
        return {"ok": False, "error": f"no item {item_id}", "item_id": item_id}
    if not item.is_pending:
        return {
            "ok": False,
            "error": f"{item_id} is already {item.state}",
            "item_id": item_id,
            "state": item.state,
        }

    settled = approval_inbox.resolve(
        item_id, approved=approved, resolved_by=resolved_by, reason=reason
    )
    if settled is None:
        # Lost the race — somebody else answered between the read and the UPDATE.
        current = approval_inbox.get(item_id)
        return {
            "ok": False,
            "error": f"{item_id} was settled concurrently",
            "item_id": item_id,
            "state": current.state if current else "",
        }

    releaser = _RELEASERS.get(settled.origin)
    if releaser is None:
        # origin='sag' is answered by the inbox approver (agov-inbox-02), which
        # polls the item itself; there is nothing to push.
        return {"ok": True, "item_id": item_id, "origin": settled.origin,
                "released": False, "item": settled.to_dict()}

    try:
        key = releaser(settled, approved, resolved_by, reason)
    except InboxAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InboxAdapterError(
            f"{item_id} was settled but its origin could not be released: {exc}"
        ) from exc
    return {
        "ok": True,
        "item_id": item_id,
        "origin": settled.origin,
        "released": True,
        "origin_key": key,
        "item": settled.to_dict(),
    }


def pending(*, origin: Optional[str] = None, inbox: Optional[str] = None,
            limit: int = 200) -> list[ApprovalItem]:
    """Everything still waiting for a human, across every mirrored origin."""
    return approval_inbox.list_items(
        state=approval_inbox.STATE_PENDING, origin=origin, inbox=inbox, limit=limit
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Answer ACE and workflow_hitl approvals from one queue (agov-inbox-05)."
        )
    )
    parser.add_argument("--list", action="store_true", help="list pending items")
    parser.add_argument("--origin", help="with --list: ace | workflow_hitl | sag")
    parser.add_argument("--inbox", help="with --list: filter by inbox")
    parser.add_argument("--limit", type=int, default=50, help="with --list: max rows")
    parser.add_argument("--resolve", metavar="ITEM_ID", help="answer one item")
    parser.add_argument("--approve", action="store_true", help="with --resolve")
    parser.add_argument("--deny", action="store_true", help="with --resolve")
    parser.add_argument("--reason", default="", help="with --resolve: recorded reason")
    parser.add_argument("--actor", default="", help="with --resolve: who decided")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(payload)

    if args.resolve:
        if args.approve == args.deny:
            parser.error("--resolve requires exactly one of --approve / --deny")
        try:
            result = resolve(
                args.resolve,
                approved=bool(args.approve),
                resolved_by=args.actor,
                reason=args.reason,
            )
        except InboxAdapterError as exc:
            emit({"ok": False, "error": str(exc), "item_id": args.resolve})
            return 1
        emit(result)
        return 0 if result.get("ok") else 1

    if args.list:
        items = pending(origin=args.origin, inbox=args.inbox, limit=args.limit)
        if args.json:
            emit([x.to_dict() for x in items])
        else:
            for x in items:
                print(f"{x.item_id}  {x.origin:<14}  {x.title}")
            print(f"({len(items)} pending)")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
