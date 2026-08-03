# CUI // SP-CTI
"""Rule-level scorecard exemptions — request, approve, audit.

A scorecard that carries one unfair rule does not get that rule fixed. It gets
the whole scorecard ignored, and an ignored scorecard is worse than no
scorecard because it still reads as governance. An exemption is the pressure
valve: it takes ONE component out of ONE rule, and leaves everything else about
that component graded exactly as before.

What an exemption is *not*
--------------------------
It is not a pass. An exempt component is neither passing nor failing the rule —
the rule simply does not apply to it, the same way a rule's ``filter`` makes it
not-applicable. So an exempt rule leaves the score's denominator, and a
component with a 20-point rule exempted is scored out of the remaining weight
rather than being handed 20 free points. Crediting an exemption as a pass would
make waiving a rule the cheapest way to raise a score, which is precisely the
incentive a scorecard exists to remove.

It also does not stop the ladder. A rung whose only unmet rule is exempted for
this component is met, so progression continues — see
``tools/idp/scorecard.py::_assign_level``.

Approval and attribution
------------------------
Every exemption names **who approved it** and **why**. An unattributed
exemption is a silent failure with extra steps: six months later nobody can
tell whether the waiver was a considered decision or a way to make a red board
green. The rule is enforced at evaluation time, not just at write time — an
exemption missing an approver or a reason is reported as *inert* and does not
waive anything, so the failure mode is "the rule still fails" rather than "the
rule was quietly skipped by nobody in particular".

``auto_approve`` is OFF by default. With it off, a request sits at
``pending`` and waives nothing until somebody approves it.

Storage — the log IS the record
-------------------------------
``idp_rule_exemptions`` is an append-only event log (migration
20260803030514, registered in ``APPEND_ONLY_TABLES``). Every state change
appends a row; the current state of an exemption is its highest
``event_index`` row. There is no mutable state row to overwrite, so the
approver of a waiver cannot be edited out after the fact, and revoking is
itself an event rather than a deletion.

Each event is additionally mirrored, best-effort, to ``component_audit_log``
via ``log_component_audit()`` — the existing component-configuration audit
precedent — so exemptions show up on the same surface as enable/disable and
profile-apply events instead of inventing a second place to look.

CLI
---
    python tools/idp/exemptions.py --list
    python tools/idp/exemptions.py --request e2e-spec:my-canvas \\
        --scorecard component-readiness --reason "Headless, renders no page." \\
        --requested-by alice --expires 2026-12-31
    python tools/idp/exemptions.py --approve e2e-spec:my-canvas \\
        --scorecard component-readiness --by bob --decision-reason "Confirmed headless."
    python tools/idp/exemptions.py --history e2e-spec:my-canvas --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

TABLE = "idp_rule_exemptions"
CLASSIFICATION = "CUI // SP-CTI"

#: Lifecycle states. ``pending`` waives nothing — that is the whole point of
#: an approval step.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_REVOKED = "revoked"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED, STATUS_REVOKED)

#: Event vocabulary. One event per state change, appended.
EVENT_REQUESTED = "requested"
EVENT_APPROVED = "approved"
EVENT_DENIED = "denied"
EVENT_REVOKED = "revoked"
EVENTS = (EVENT_REQUESTED, EVENT_APPROVED, EVENT_DENIED, EVENT_REVOKED)

#: The one state in which an exemption actually waives a rule.
ACTIVE_STATUS = STATUS_APPROVED

#: Columns written by :func:`_append_event`. Asserted against the migration's
#: DDL by tests/test_idp_exemptions.py — an INSERT naming a column the live
#: schema lacks raises, is swallowed by the caller, and reports success while
#: persisting nothing (CLAUDE.md).
INSERT_COLUMNS = (
    "id",
    "exemption_key",
    "scorecard_key",
    "rule_identifier",
    "component_key",
    "event",
    "status",
    "event_index",
    "reason",
    "requested_by",
    "decided_by",
    "decision_reason",
    "auto_approved",
    "self_approved",
    "expires_on",
    "notified",
    "recorded_at",
    "tenant_id",
    "classification",
)

_READ_COLUMNS = ", ".join(c for c in INSERT_COLUMNS if c != "classification")


class ExemptionError(RuntimeError):
    """Raised when an exemption cannot be written or a transition is invalid."""


# ---------------------------------------------------------------------------
# Policy — the `exemptions:` config block on a scorecard
# ---------------------------------------------------------------------------


class ExemptionPolicy:
    """The three knobs a scorecard carries for exemptions.

    ``enabled``
        Turn exemptions off entirely for this scorecard. Every grant is then
        reported as inert and nothing is waived — useful for a scorecard whose
        rules are all statutory.
    ``auto_approve``
        Approve a request the moment it is filed. **Default off**: an
        exemption nobody looked at is a self-service way to delete a rule.
        When on, the approver is recorded as the requester with
        ``auto_approved`` set, so a later review can tell the two apart.
    ``user_specific_notifications``
        Notify the people attached to this exemption (requester, approver, and
        the component's owner/on-call from the registry) rather than
        broadcasting to a channel. Default off.
    """

    __slots__ = ("enabled", "auto_approve", "user_specific_notifications")

    def __init__(
        self,
        enabled: bool = True,
        auto_approve: bool = False,
        user_specific_notifications: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.auto_approve = bool(auto_approve)
        self.user_specific_notifications = bool(user_specific_notifications)

    @classmethod
    def from_config(cls, raw: Any) -> "ExemptionPolicy":
        """Build from a scorecard's ``exemptions:`` block.

        Accepts both spellings for each key (``autoApprove`` as cortex.io
        writes it, ``auto_approve`` as the rest of this repo's YAML does), and
        tolerates the legacy shape where ``exemptions:`` is a bare list of
        grants with no config at all.
        """
        if not isinstance(raw, dict):
            return cls()

        def flag(camel: str, snake: str, default: bool) -> bool:
            for name in (camel, snake):
                if name in raw:
                    return bool(raw[name])
            return default

        return cls(
            enabled=flag("enabled", "enabled", True),
            auto_approve=flag("autoApprove", "auto_approve", False),
            user_specific_notifications=flag(
                "userSpecificNotifications", "user_specific_notifications", False
            ),
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "auto_approve": self.auto_approve,
            "user_specific_notifications": self.user_specific_notifications,
        }

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"ExemptionPolicy({self.to_dict()})"


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def _meaningful(value: Any) -> str:
    """Normalize an actor/reason to a real value or ``""``.

    Reuses the registry's ``UNOWNED_SENTINELS`` scrub: a waiver approved by
    "TBD" is not approved by anybody, exactly as a component owned by "TBD" is
    not owned by anybody. Same failure, same list.
    """
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip()
    try:
        from tools.config.component_registry import UNOWNED_SENTINELS  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — attribution must not depend on the registry importing
        UNOWNED_SENTINELS = frozenset({"", "-", "n/a", "na", "none", "null", "tbd", "todo", "unassigned", "unknown"})
    return "" if text.lower() in UNOWNED_SENTINELS else text


def attribution_defect(
    *,
    status: str,
    approved_by: Any,
    reason: Any,
    expires: Any = "",
    today: str | None = None,
    policy: ExemptionPolicy | None = None,
) -> str:
    """Why this exemption does **not** apply, or ``""`` when it does.

    Returning the reason rather than a bare bool is deliberate: an exemption
    that silently fails to apply is as confusing as one that silently applies.
    The evaluator surfaces this string next to the grant.
    """
    if policy is not None and not policy.enabled:
        return "exemptions are disabled for this scorecard"
    if str(status or "") != ACTIVE_STATUS:
        return f"status is {status or 'unset'!s}, not {ACTIVE_STATUS}"
    if not _meaningful(approved_by):
        return "no approver recorded — an unattributed exemption does not apply"
    if not _meaningful(reason):
        return "no reason recorded — an unexplained exemption does not apply"
    expiry = str(expires or "").strip()
    if expiry and expiry < (today or _today()):
        return f"expired on {expiry}"
    return ""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def exemption_key(scorecard_key: str, rule_identifier: str, component_key: str) -> str:
    """Stable identity of one exemption across all of its events."""
    return f"{scorecard_key}|{rule_identifier}|{component_key}"


def parse_target(target: str) -> tuple[str, str]:
    """Split a CLI ``rule:component`` target."""
    if ":" not in target:
        raise ExemptionError(
            f"target {target!r} must be <rule-identifier>:<component-key>"
        )
    rule, component = target.split(":", 1)
    if not rule.strip() or not component.strip():
        raise ExemptionError(f"target {target!r} must name both a rule and a component")
    return rule.strip(), component.strip()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _open_connection() -> Any:
    from tools.db.storage import get_connection  # noqa: PLC0415

    return get_connection()


def _rows(cursor_result: Any) -> list[dict[str, Any]]:
    """Normalise fetchall() output to plain dicts across both backends."""
    out: list[dict[str, Any]] = []
    for row in cursor_result or []:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            try:
                out.append(dict(row))
            except (TypeError, ValueError):  # pragma: no cover — tuple cursor
                out.append({"_row": row})
    return out


def table_ready(conn: Any) -> bool:
    """Is the exemption log present? Readers degrade instead of raising.

    A platform whose migration has not run yet must still render its
    scorecards — with no exemptions honoured, which is the safe direction.
    """
    try:
        from tools.db.storage import table_exists  # noqa: PLC0415

        return bool(table_exists(conn, TABLE))
    except Exception:  # noqa: BLE001
        return False


def _events_for(conn: Any, key: str) -> list[dict[str, Any]]:
    """Every event for one exemption, oldest first."""
    return _rows(
        conn.execute(
            f"SELECT {_READ_COLUMNS} FROM {TABLE} WHERE exemption_key = %s "
            "ORDER BY event_index",
            (key,),
        ).fetchall()
    )


def _latest(conn: Any, key: str) -> dict[str, Any] | None:
    events = _events_for(conn, key)
    return events[-1] if events else None


def _append_event(
    conn: Any,
    *,
    key: str,
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    event: str,
    status: str,
    reason: str,
    requested_by: str,
    decided_by: str | None,
    decision_reason: str | None,
    auto_approved: bool,
    self_approved: bool,
    expires_on: str | None,
    notified: list[str],
    event_index: int,
    tenant_id: str | None,
) -> dict[str, Any]:
    """Append one immutable event row and return it."""
    recorded_at = _utcnow_iso()
    row = {
        "id": uuid.uuid4().hex,
        "exemption_key": key,
        "scorecard_key": scorecard_key,
        "rule_identifier": rule_identifier,
        "component_key": component_key,
        "event": event,
        "status": status,
        "event_index": event_index,
        "reason": reason,
        "requested_by": requested_by,
        "decided_by": decided_by,
        "decision_reason": decision_reason,
        "auto_approved": 1 if auto_approved else 0,
        "self_approved": 1 if self_approved else 0,
        "expires_on": expires_on,
        "notified": json.dumps(notified),
        "recorded_at": recorded_at,
        "tenant_id": tenant_id,
        "classification": CLASSIFICATION,
    }
    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
    conn.execute(
        f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) VALUES ({placeholders})",
        tuple(row[c] for c in INSERT_COLUMNS),
    )
    conn.commit()
    row.pop("classification", None)
    # Hand the caller the list it passed in rather than the JSON text the column
    # holds — the return value is a Python row, not a database row.
    row["notified"] = list(notified)
    return row


def _mirror_to_component_audit(row: dict[str, Any]) -> None:
    """Best-effort mirror to ``component_audit_log``.

    Uses the existing component-configuration audit helper rather than a new
    mechanism, so a waiver appears on the same surface as an enable/disable.
    Failures are swallowed there; the authoritative record is the event row
    that has already been written by the time this runs.
    """
    try:
        from tools.config.component_registry import log_component_audit  # noqa: PLC0415

        log_component_audit(
            event_type=f"scorecard_exemption_{row['event']}",
            actor=str(row.get("decided_by") or row.get("requested_by") or "system"),
            tenant_id=row.get("tenant_id"),
            component_key=row.get("component_key"),
            details={
                "scorecard": row.get("scorecard_key"),
                "rule": row.get("rule_identifier"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "decision_reason": row.get("decision_reason"),
                "requested_by": row.get("requested_by"),
                "decided_by": row.get("decided_by"),
                "auto_approved": bool(row.get("auto_approved")),
                "self_approved": bool(row.get("self_approved")),
                "expires_on": row.get("expires_on"),
                "event_index": row.get("event_index"),
            },
        )
    except Exception:  # noqa: BLE001 — audit mirroring must not break the action
        pass


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _recipients(row: dict[str, Any]) -> list[str]:
    """The specific people this exemption is about.

    Requester, decider, and whoever owns the component — an exemption granted
    on a component whose owner never hears about it is a governance change
    made behind the owner's back.
    """
    people = [row.get("requested_by"), row.get("decided_by")]
    try:
        from tools.config.component_registry import ComponentRegistry  # noqa: PLC0415

        registry = ComponentRegistry()
        component = registry.get(str(row.get("component_key") or ""))
        if component is not None:
            people.extend([getattr(component, "owner", None), getattr(component, "on_call", None)])
    except Exception:  # noqa: BLE001 — a missing registry costs recipients, not the event
        pass

    out: list[str] = []
    for person in people:
        cleaned = _meaningful(person)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def notify_targets(policy: ExemptionPolicy, row: dict[str, Any]) -> list[str]:
    """Who this event will be sent to — resolved **before** the row is written.

    Split from delivery deliberately. The recipient list is part of the record
    ("who was told"), and the record is append-only, so it cannot be patched in
    afterwards: an event row written with ``notified: []`` and a send that
    happens later leaves a log that permanently claims nobody was notified.
    Resolving first and inserting the answer keeps the row true on the first
    and only write.
    """
    return _recipients(row) if policy.user_specific_notifications else []


def _deliver(row: dict[str, Any], people: list[str]) -> None:
    """Send the notification. Entirely best-effort, and deliberately last.

    Runs *after* the event row is durable: a waiver that has been decided and
    recorded must not be rolled back by an unreachable notification gateway,
    and a notification claiming an approval that failed to persist would be
    worse than no notification.
    """
    if not people:
        return
    try:
        from tools.notifications.gateway import NotificationGateway  # noqa: PLC0415

        NotificationGateway().send(
            event_type="scorecard_exemption",
            severity="warning" if row.get("event") == EVENT_APPROVED else "info",
            title=(
                f"Exemption {row['event']}: {row['rule_identifier']} "
                f"on {row['component_key']}"
            ),
            body=(
                f"{row['scorecard_key']} / {row['rule_identifier']} — "
                f"{row.get('decision_reason') or row.get('reason') or ''}"
            ),
            metadata={"recipients": people, "exemption_key": row["exemption_key"]},
        )
    except Exception:  # noqa: BLE001 — delivery is not load-bearing
        pass


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _policy_for(scorecard_key: str, policy: ExemptionPolicy | None) -> ExemptionPolicy:
    """Resolve the policy — explicit argument wins, else read the scorecard."""
    if policy is not None:
        return policy
    try:
        from tools.idp.scorecard import load_scorecard  # noqa: PLC0415

        return load_scorecard(scorecard_key).exemption_policy
    except Exception:  # noqa: BLE001 — an unknown scorecard gets the safe default
        return ExemptionPolicy()


def request_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    reason: str,
    requested_by: str,
    expires: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """File an exemption request.

    Lands at ``pending`` and waives nothing unless the scorecard's policy sets
    ``auto_approve``, in which case an ``approved`` event is appended
    immediately after the request — two rows, not one, so the log still shows
    that nobody reviewed it.
    """
    reason = _meaningful(reason)
    requested_by = _meaningful(requested_by)
    if not reason:
        raise ExemptionError(
            "an exemption needs a reason — an unexplained waiver is a silent failure"
        )
    if not requested_by:
        raise ExemptionError("an exemption needs a requester")

    policy = _policy_for(scorecard_key, policy)
    if not policy.enabled:
        raise ExemptionError(
            f"scorecard {scorecard_key!r} has exemptions disabled (exemptions.enabled: false)"
        )

    own = conn is None
    conn = conn or _open_connection()
    key = exemption_key(scorecard_key, rule_identifier, component_key)
    try:
        if not table_ready(conn):
            raise ExemptionError(
                f"{TABLE} is missing — run migration 20260803030514_idp_rule_exemptions"
            )
        current = _latest(conn, key)
        if current and str(current.get("status")) in (STATUS_PENDING, STATUS_APPROVED):
            raise ExemptionError(
                f"{key} already has a {current['status']} exemption "
                f"(event {current['event_index']}); revoke it before re-requesting"
            )
        index = int(current["event_index"]) + 1 if current else 1
        notified = notify_targets(
            policy,
            {
                "requested_by": requested_by,
                "decided_by": None,
                "component_key": component_key,
            },
        )
        row = _append_event(
            conn,
            key=key,
            scorecard_key=scorecard_key,
            rule_identifier=rule_identifier,
            component_key=component_key,
            event=EVENT_REQUESTED,
            status=STATUS_PENDING,
            reason=reason,
            requested_by=requested_by,
            decided_by=None,
            decision_reason=None,
            auto_approved=False,
            self_approved=False,
            expires_on=(str(expires).strip() or None) if expires else None,
            notified=notified,
            event_index=index,
            tenant_id=tenant_id,
        )
        _deliver(row, notified)
        _mirror_to_component_audit(row)

        if policy.auto_approve:
            approved = _decide(
                conn,
                key=key,
                event=EVENT_APPROVED,
                status=STATUS_APPROVED,
                actor=requested_by,
                decision_reason="auto-approved by scorecard policy (exemptions.autoApprove)",
                policy=policy,
                auto_approved=True,
                tenant_id=tenant_id,
            )
            approved["auto_approved_from"] = row["id"]
            return approved
        return row
    finally:
        if own:
            conn.close()


def _decide(
    conn: Any,
    *,
    key: str,
    event: str,
    status: str,
    actor: str,
    decision_reason: str | None,
    policy: ExemptionPolicy,
    auto_approved: bool = False,
    tenant_id: str | None = None,
    allowed_from: tuple[str, ...] = (STATUS_PENDING,),
) -> dict[str, Any]:
    """Append a decision event on an existing exemption."""
    actor = _meaningful(actor)
    if not actor:
        raise ExemptionError(
            f"an exemption {event} must name who did it — "
            "an unattributed decision is not a decision"
        )
    current = _latest(conn, key)
    if current is None:
        raise ExemptionError(f"no exemption {key!r} to {event.rstrip('d')}")
    if str(current.get("status")) not in allowed_from:
        raise ExemptionError(
            f"{key} is {current['status']}; {event} requires "
            f"{' or '.join(allowed_from)}"
        )
    notified = notify_targets(
        policy,
        {
            "requested_by": current.get("requested_by"),
            "decided_by": actor,
            "component_key": current.get("component_key"),
        },
    )
    row = _append_event(
        conn,
        key=key,
        scorecard_key=str(current["scorecard_key"]),
        rule_identifier=str(current["rule_identifier"]),
        component_key=str(current["component_key"]),
        event=event,
        status=status,
        reason=str(current.get("reason") or ""),
        requested_by=str(current.get("requested_by") or ""),
        decided_by=actor,
        decision_reason=_meaningful(decision_reason) or None,
        auto_approved=auto_approved,
        # Recorded rather than blocked: a one-operator deployment must still be
        # able to grant a waiver, but "the requester approved themselves" is a
        # fact a reviewer needs to see rather than infer.
        self_approved=actor == _meaningful(current.get("requested_by")),
        expires_on=current.get("expires_on"),
        notified=notified,
        event_index=int(current["event_index"]) + 1,
        tenant_id=tenant_id if tenant_id is not None else current.get("tenant_id"),
    )
    _deliver(row, notified)
    _mirror_to_component_audit(row)
    return row


def approve_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    approved_by: str,
    decision_reason: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Approve a pending request. This is the event that makes it apply."""
    policy = _policy_for(scorecard_key, policy)
    own = conn is None
    conn = conn or _open_connection()
    try:
        return _decide(
            conn,
            key=exemption_key(scorecard_key, rule_identifier, component_key),
            event=EVENT_APPROVED,
            status=STATUS_APPROVED,
            actor=approved_by,
            decision_reason=decision_reason,
            policy=policy,
            tenant_id=tenant_id,
        )
    finally:
        if own:
            conn.close()


def deny_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    denied_by: str,
    decision_reason: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Deny a pending request. The rule keeps applying."""
    policy = _policy_for(scorecard_key, policy)
    own = conn is None
    conn = conn or _open_connection()
    try:
        return _decide(
            conn,
            key=exemption_key(scorecard_key, rule_identifier, component_key),
            event=EVENT_DENIED,
            status=STATUS_DENIED,
            actor=denied_by,
            decision_reason=decision_reason,
            policy=policy,
            tenant_id=tenant_id,
        )
    finally:
        if own:
            conn.close()


def revoke_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    revoked_by: str,
    decision_reason: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Withdraw a live exemption. The rule starts applying again.

    Revocation is an append, not a delete: the waiver still happened, and the
    window during which it applied is part of the record.
    """
    policy = _policy_for(scorecard_key, policy)
    own = conn is None
    conn = conn or _open_connection()
    try:
        return _decide(
            conn,
            key=exemption_key(scorecard_key, rule_identifier, component_key),
            event=EVENT_REVOKED,
            status=STATUS_REVOKED,
            actor=revoked_by,
            decision_reason=decision_reason,
            policy=policy,
            tenant_id=tenant_id,
            allowed_from=(STATUS_PENDING, STATUS_APPROVED),
        )
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def history(
    scorecard_key: str | None = None,
    rule_identifier: str | None = None,
    component_key: str | None = None,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Every event, oldest first — the audit read."""
    own = conn is None
    conn = conn or _open_connection()
    try:
        if not table_ready(conn):
            return []
        where: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("scorecard_key", scorecard_key),
            ("rule_identifier", rule_identifier),
            ("component_key", component_key),
        ):
            if value:
                where.append(f"{column} = %s")
                params.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return _rows(
            conn.execute(
                f"SELECT {_READ_COLUMNS} FROM {TABLE} {clause} "
                "ORDER BY exemption_key, event_index",
                tuple(params),
            ).fetchall()
        )
    finally:
        if own:
            conn.close()


def current_states(
    scorecard_key: str | None = None,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """The latest event per exemption — i.e. the current state of each one.

    Grouped in Python rather than with a windowed SQL query: the row count is
    exemptions × events, and CLAUDE.md pushes anything cleverer than a filtered
    SELECT out of the SQL layer for backend portability.
    """
    own = conn is None
    conn = conn or _open_connection()
    try:
        events = history(scorecard_key, conn=conn)
    finally:
        if own:
            conn.close()

    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        key = str(event.get("exemption_key"))
        previous = latest.get(key)
        if previous is None or int(event.get("event_index") or 0) >= int(
            previous.get("event_index") or 0
        ):
            latest[key] = event
    return [latest[k] for k in sorted(latest)]


def active_grants(
    scorecard_key: str,
    conn: Any = None,
    *,
    today: str | None = None,
    policy: ExemptionPolicy | None = None,
) -> list[dict[str, Any]]:
    """Approved, attributed, unexpired exemptions for one scorecard.

    This is what the evaluator consumes. Anything that fails
    :func:`attribution_defect` is left out here and reported separately as
    inert, so a waiver never applies without an approver and a reason.
    """
    today = today or _today()
    out: list[dict[str, Any]] = []
    for state in current_states(scorecard_key, conn=conn):
        defect = attribution_defect(
            status=str(state.get("status") or ""),
            approved_by=state.get("decided_by"),
            reason=state.get("reason"),
            expires=state.get("expires_on"),
            today=today,
            policy=policy,
        )
        state = dict(state)
        state["defect"] = defect
        if not defect:
            out.append(state)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(rows: Iterable[dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "(no exemptions)"
    lines = [
        f"{'rule':<24} {'component':<24} {'status':<10} {'approver':<16} "
        f"{'expires':<12} reason",
        "-" * 108,
    ]
    for row in rows:
        marks = []
        if row.get("auto_approved"):
            marks.append("auto")
        if row.get("self_approved"):
            marks.append("self")
        suffix = f"  [{'+'.join(marks)}]" if marks else ""
        lines.append(
            f"{str(row.get('rule_identifier') or ''):<24} "
            f"{str(row.get('component_key') or ''):<24} "
            f"{str(row.get('status') or ''):<10} "
            f"{str(row.get('decided_by') or '-'):<16} "
            f"{str(row.get('expires_on') or '-'):<12} "
            f"{str(row.get('reason') or '')}{suffix}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/idp/exemptions.py",
        description="Request, approve and audit rule-level scorecard exemptions.",
    )
    ap.add_argument("--scorecard", default="component-readiness", help="Scorecard key")
    ap.add_argument("--list", action="store_true", help="Show current state of each exemption")
    ap.add_argument("--history", metavar="RULE:COMPONENT", nargs="?", const="",
                    help="Show every event (optionally for one rule:component)")
    ap.add_argument("--request", metavar="RULE:COMPONENT", help="File a request")
    ap.add_argument("--approve", metavar="RULE:COMPONENT", help="Approve a pending request")
    ap.add_argument("--deny", metavar="RULE:COMPONENT", help="Deny a pending request")
    ap.add_argument("--revoke", metavar="RULE:COMPONENT", help="Withdraw a live exemption")
    ap.add_argument("--reason", help="Why the rule should not apply (required to request)")
    ap.add_argument("--requested-by", help="Who is asking")
    ap.add_argument("--by", help="Who is approving/denying/revoking")
    ap.add_argument("--decision-reason", help="Why the decision went that way")
    ap.add_argument("--expires", metavar="YYYY-MM-DD", help="Optional expiry date")
    ap.add_argument("--tenant", help="Tenant id to record")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    actions = [args.list, args.history is not None, args.request, args.approve,
               args.deny, args.revoke]
    if not any(actions):
        ap.error("one of --list, --history, --request, --approve, --deny or --revoke is required")

    try:
        conn = _open_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot open the database: {exc}", file=sys.stderr)
        return 2

    try:
        if args.list:
            rows = current_states(args.scorecard, conn=conn)
            print(json.dumps(rows, indent=2, default=str) if args.json else _render(rows))
            return 0

        if args.history is not None:
            rule = component = None
            if args.history:
                rule, component = parse_target(args.history)
            rows = history(args.scorecard, rule, component, conn=conn)
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                for row in rows:
                    print(
                        f"{row['recorded_at']}  #{row['event_index']}  "
                        f"{row['event']:<10} {row['rule_identifier']}:{row['component_key']}  "
                        f"by={row.get('decided_by') or row.get('requested_by')}  "
                        f"{row.get('decision_reason') or row.get('reason') or ''}"
                    )
                if not rows:
                    print("(no events)")
            return 0

        if args.request:
            rule, component = parse_target(args.request)
            row = request_exemption(
                args.scorecard, rule, component,
                reason=args.reason or "",
                requested_by=args.requested_by or "",
                expires=args.expires,
                conn=conn,
                tenant_id=args.tenant,
            )
        elif args.approve:
            rule, component = parse_target(args.approve)
            row = approve_exemption(
                args.scorecard, rule, component,
                approved_by=args.by or "",
                decision_reason=args.decision_reason,
                conn=conn,
                tenant_id=args.tenant,
            )
        elif args.deny:
            rule, component = parse_target(args.deny)
            row = deny_exemption(
                args.scorecard, rule, component,
                denied_by=args.by or "",
                decision_reason=args.decision_reason,
                conn=conn,
                tenant_id=args.tenant,
            )
        else:
            rule, component = parse_target(args.revoke)
            row = revoke_exemption(
                args.scorecard, rule, component,
                revoked_by=args.by or "",
                decision_reason=args.decision_reason,
                conn=conn,
                tenant_id=args.tenant,
            )
    except ExemptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass

    if args.json:
        print(json.dumps(row, indent=2, default=str))
    else:
        print(
            f"{row['event']} #{row['event_index']}: {row['rule_identifier']}:"
            f"{row['component_key']} is now {row['status']}"
            + (f" (approved by {row['decided_by']})" if row.get("decided_by") else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
