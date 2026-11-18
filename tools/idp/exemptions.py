# CUI // SP-CTI
"""Scorecard exemptions that expire — request, a person's grant, renewal, audit.

A scorecard that carries one unfair rule does not get that rule fixed. It gets
the whole scorecard ignored, and an ignored scorecard is worse than no
scorecard because it still reads as governance. An exemption is the pressure
valve: it takes a component out of a rule, and leaves everything else about
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

Scope (icx-coe-02)
------------------
Every exemption names a scorecard and ONE scope (``args/coe_records.yaml``):

``rule``      one rule on one component (the original shape);
``service``   every rule of the scorecard on one component;
``standard``  the rule an engineering standard's check names (icx-coe-01's
              register), on one component;
``repo``      one rule on every component of a repo.

Each exemption links to the engineering standards it waives: the standards whose
check is the waived rule, plus any named, each one checked against the register.

Every exemption expires (icx-coe-02)
------------------------------------
A request with no expiry is refused, and so is one not after today or further
out than ``max_expiry_days``. An exemption past its expiry no longer exempts,
and it is not merely inert: it FAILS the scorecard (``evaluate`` reports
``exemptions.gate: fail`` and the rule fails for that component) and it fails
the CI check (``--check --gate``) until a person renews it or revokes it.
Nothing renews by itself and no timer decides anything: expiry only removes the
exemption.

Who decides
-----------
Requesting decides nothing: anyone signed in may ask, and an agent may draft a
request. **Granting** (``decl-wft-coe-exemption-grant``) and **renewing**
(``decl-wft-coe-exemption-renew``) are a person's declared steps, with a reason,
bound to the exemption's digest (``exemption_digest``: what the approver read),
and the requester is never the approver (``separate_from: builders``). The old
``autoApprove`` switch is no longer honoured: a request with it on is refused.
Revoking is a stop: immediate, with a stated reason, and it only narrows.

A grant written into a scorecard's YAML is not a person's declared step, so it is
reported as inert and fails the CI check; request it on /idp/exemptions instead.

Storage — the log IS the record
-------------------------------
``idp_rule_exemptions`` is an append-only event log (migration 20260803030514,
extended by 20261118010237 with triggers on UPDATE and DELETE). Every state
change appends a row; the current state of an exemption is its highest
``event_index`` row, and each new row carries the hash of the previous one
(``prev_hash`` / ``row_hash``, ``UNIQUE(exemption_key, prev_hash)``). Rows
written before the chain began are reported as legacy, never as verified.

Exemptions approved before expiry became mandatory are given one by
:func:`migrate_legacy`, written by the labelled bootstrap identity, never a
person, and the twin reports each until a person renews it or lets it lapse.

Each event is also mirrored, best-effort, to ``component_audit_log`` via
``log_component_audit()``.

CLI
---
    python tools/idp/exemptions.py --list [--json]
    python tools/idp/exemptions.py --request e2e-spec:my-canvas \\
        --scorecard component-readiness --reason "Headless, renders no page." \\
        --requested-by alice --expires 2026-12-31 [--scope rule|service|standard|repo]
    python tools/idp/exemptions.py --revoke e2e-spec:my-canvas --by bob --decision-reason "Fixed."
    python tools/idp/exemptions.py --history e2e-spec:my-canvas --json
    python tools/idp/exemptions.py --check [--gate] [--json]
    python tools/idp/exemptions.py --migrate-legacy [--json]

Granting and renewing are not CLI actions: they are a signed-in person's
declared step on /idp/exemptions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

#: The repo root for args/ (icx-coe-02), from either tree (tools/ or icdev/tools/).
BASE_DIR = repo_root(__file__)

TABLE = "idp_rule_exemptions"
CLASSIFICATION = "CUI // SP-CTI"
CONFIG_RELPATH = "args/coe_records.yaml"
GENESIS = "0" * 64

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
EVENT_RENEWAL_REQUESTED = "renewal_requested"
EVENT_RENEWED = "renewed"
EVENT_RENEWAL_REFUSED = "renewal_refused"
EVENT_MIGRATED = "migrated"
EVENTS = (EVENT_REQUESTED, EVENT_APPROVED, EVENT_DENIED, EVENT_REVOKED,
          EVENT_RENEWAL_REQUESTED, EVENT_RENEWED, EVENT_RENEWAL_REFUSED, EVENT_MIGRATED)

#: The one state in which an exemption actually waives a rule.
ACTIVE_STATUS = STATUS_APPROVED

SCOPES = ("rule", "service", "standard", "repo")
ACTOR_KINDS = ("person", "agent", "bootstrap")
#: The wildcard a ``service`` scope stores as its rule and a ``repo`` scope as its component.
ANY = "*"

#: The declared steps, and the route each one decides.
TEMPLATES = {
    "grant": "decl-wft-coe-exemption-grant",
    "renew": "decl-wft-coe-exemption-renew",
}
SURFACES = {
    "grant": "idp.idp_api_exemption_decide",
    "renew": "idp.idp_api_exemption_renew",
}

#: What a page shows for each exemption (:func:`classify`).
STATES = ("pending", "active", "expiring", "expired", "renewal_pending", "denied", "revoked",
          "inert")

#: Columns written by :func:`_append_event`. Asserted against the migrations'
#: DDL by tests — an INSERT naming a column the live schema lacks raises, is
#: swallowed by the caller, and reports success while persisting nothing
#: (CLAUDE.md).
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
    # icx-coe-02 (migration 20261118010237)
    "scope_kind",
    "scope_ref",
    "standard_ids",
    "requested_expires_on",
    "exemption_digest",
    "step_id",
    "actor",
    "actor_kind",
    "prev_hash",
    "row_hash",
)

#: Read back: every inserted column, the label included (later rows carry it forward).
_READ = INSERT_COLUMNS
_READ_COLUMNS = ", ".join(_READ)
#: What a row's hash covers: everything but the hash itself.
_HASHED = tuple(c for c in _READ if c != "row_hash")


class ExemptionError(RuntimeError):
    """Raised when an exemption cannot be written or a transition is refused.

    ``status`` is the HTTP status a route returns and ``code`` names the refusal.
    """

    def __init__(self, message: str, status: int = 400, code: str = "refused"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


# ---------------------------------------------------------------------------
# Configuration and the clock
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> dict:
    """The ``exemptions:`` block of ``args/coe_records.yaml``."""
    import yaml  # noqa: PLC0415

    p = Path(path or BASE_DIR / CONFIG_RELPATH)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except OSError:
        data = {}
    out = dict(data.get("exemptions") or {})
    out.setdefault("scopes", list(SCOPES))
    out.setdefault("repos", ["icdev"])
    out.setdefault("max_expiry_days", 365)
    out.setdefault("expiring_warn_days", 14)
    out.setdefault("migration_grace_days", 30)
    out.setdefault("migration_identity", "bootstrap:icx-coe-02-exemption-migration")
    if not str(out["migration_identity"]).startswith("bootstrap:"):
        raise ValueError("migration_identity must be a labelled bootstrap identity ('bootstrap:...')")
    return out


Clock = Callable[[], datetime]


def _clock(clock: Optional[Clock]) -> datetime:
    """Now, from the injected clock or the system's. Always timezone-aware UTC."""
    now = clock() if clock is not None else datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _today(clock: Optional[Clock] = None) -> str:
    return _clock(clock).date().isoformat()


def _utcnow_iso(clock: Optional[Clock] = None) -> str:
    return _clock(clock).isoformat()


def _iso_date(value: Any) -> str:
    """``YYYY-MM-DD`` or ``""`` when *value* is not a date."""
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def validate_expiry(expires: Any, *, today: str, config: Optional[dict] = None) -> str:
    """The expiry as ``YYYY-MM-DD``, or ExemptionError: none, not a date, not after
    today, or further out than ``max_expiry_days``."""
    cfg = config or load_config()
    raw = str(expires or "").strip()
    if not raw:
        raise ExemptionError("an exemption needs an expiry: one that never expires is refused",
                             code="no_expiry")
    iso = _iso_date(raw)
    if not iso or len(raw) != 10:
        raise ExemptionError(f"expiry {raw!r} must be a date, YYYY-MM-DD", code="bad_expiry")
    if iso <= today:
        raise ExemptionError(f"expiry {iso} must be after today ({today})", code="bad_expiry")
    latest = (date.fromisoformat(today) + timedelta(days=int(cfg["max_expiry_days"]))).isoformat()
    if iso > latest:
        raise ExemptionError(f"expiry {iso} is further out than {cfg['max_expiry_days']} days "
                             f"(the latest is {latest})", code="bad_expiry")
    return iso


# ---------------------------------------------------------------------------
# Policy — the `exemptions:` config block on a scorecard
# ---------------------------------------------------------------------------


class ExemptionPolicy:
    """The knobs a scorecard carries for exemptions.

    ``enabled``
        Turn exemptions off entirely for this scorecard. Every grant is then
        reported as inert and nothing is waived — useful for a scorecard whose
        rules are all statutory.
    ``auto_approve``
        No longer honoured (icx-coe-02): granting is a person's declared step.
        It is still read so a scorecard that sets it is refused loudly, by
        :func:`request_exemption` and by :func:`check`, rather than ignored.
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
# Attribution and expiry
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
    The evaluator surfaces this string next to the grant. An exemption with no
    expiry does not apply (expiry is mandatory); one past its expiry says
    ``expired on <date>``, and :func:`is_expired_defect` tells the two apart.
    """
    if policy is not None and not policy.enabled:
        return "exemptions are disabled for this scorecard"
    if str(status or "") != ACTIVE_STATUS:
        return f"status is {status or 'unset'!s}, not {ACTIVE_STATUS}"
    if not _meaningful(approved_by):
        return "no approver recorded — an unattributed exemption does not apply"
    if not _meaningful(reason):
        return "no reason recorded — an unexplained exemption does not apply"
    expiry = _iso_date(expires)
    if not expiry:
        return "no expiry recorded — an exemption that never expires does not apply"
    if expiry < (today or _today()):
        return f"expired on {expiry}"
    return ""


def is_expired_defect(defect: str) -> bool:
    """True when :func:`attribution_defect` said the exemption has expired."""
    return str(defect or "").startswith("expired on ")


def exemption_key(scorecard_key: str, rule_identifier: str, component_key: str) -> str:
    """Stable identity of one exemption across all of its events."""
    return f"{scorecard_key}|{rule_identifier}|{component_key}"


def ref_of(key: str) -> str:
    """A URL-safe reference to one exemption (its key carries ``|`` and ``*``)."""
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]


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
# Canonical form, digest and chain
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _ids(value: Any) -> list[str]:
    """``standard_ids`` as a sorted list, from a JSON column or a list."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    return sorted({str(v).strip().upper() for v in value or [] if str(v).strip()})


def exemption_record(row: dict) -> dict:
    """What a decision on this exemption is about, in a fixed shape.

    The expiry asked for is the requested one while a renewal waits, else the
    current one. ``event_index`` binds the record to the exact state read.
    """
    asks = row.get("requested_expires_on") or row.get("expires_on") or ""
    return {
        "exemption_key": str(row.get("exemption_key") or ""),
        "scorecard": str(row.get("scorecard_key") or ""),
        "rule": str(row.get("rule_identifier") or ""),
        "component": str(row.get("component_key") or ""),
        "scope": str(row.get("scope_kind") or "rule"),
        "scope_ref": str(row.get("scope_ref") or ""),
        "standard_ids": _ids(row.get("standard_ids")),
        "reason": str(row.get("reason") or ""),
        "requested_by": str(row.get("requested_by") or ""),
        "expires_on": _iso_date(asks),
        "event_index": int(row.get("event_index") or 0),
    }


def exemption_digest(row: dict) -> str:
    """The digest a person's grant or renewal is bound to."""
    return _digest(exemption_record(row))


def _row_hash(row: dict) -> str:
    return _digest({c: "" if row.get(c) is None else str(row.get(c)) for c in _HASHED})


def label_for(classification: Any = None) -> str:
    """The label an exemption's rows carry (the ``classification`` column).

    Named explicitly, it must be a label on the ladder (``security_context``'s labels,
    or this module's own ``CLASSIFICATION``), or it is refused, never guessed at.
    Otherwise it is the writer's own
    session level (Bell-LaPadula: a subject writes at its level, so it can read back
    what it wrote through the row-security predicate), and with no session (the CLI,
    the migration) the module's ``CLASSIFICATION``. Every later row of an exemption
    carries the label of its first.
    """
    from tools.security.security_context import _CLASSIFICATION_LABELS  # noqa: PLC0415

    want = str(classification or "").strip()
    if want:
        if want.upper() not in set(_CLASSIFICATION_LABELS) | {CLASSIFICATION}:
            raise ExemptionError(f"{want!r} is not a classification on the ladder", code="label")
        return want
    session = _session_label()
    return session or CLASSIFICATION


def _session_label() -> str:
    """The signed-in session's classification, or ``""`` outside a request."""
    try:
        from flask import g, has_request_context  # noqa: PLC0415

        if has_request_context():
            ctx = getattr(g, "security_context", None)
            value = ctx.get("classification") if isinstance(ctx, dict) else \
                getattr(ctx, "classification", None)
            return str(value or "").strip()
    except Exception:  # noqa: BLE001 — no session: the module's label
        pass
    return ""


def _legacy_anchor(row: dict) -> str:
    """What the first chained row after a pre-chain row points at."""
    return _digest({"legacy": {c: "" if row.get(c) is None else str(row.get(c))
                               for c in _READ if c not in ("prev_hash", "row_hash")}})


def _prev_for(current: Optional[dict]) -> str:
    if current is None:
        return GENESIS
    return str(current.get("row_hash") or "") or _legacy_anchor(current)


def verify_chain(events: list[dict]) -> dict:
    """Verify one exemption's rows, oldest first.

    ``{"legacy": n, "chained": n, "problems": [...]}``. A row written before the
    chain began (no ``row_hash``) is legacy: counted, never called verified. A
    row whose hash does not match its content, whose ``prev_hash`` is not the
    previous row's, or an unchained row after the chain began, is a problem.
    """
    problems: list[dict] = []
    legacy = chained = 0
    prev: Optional[dict] = None
    for row in sorted(events, key=lambda r: int(r.get("event_index") or 0)):
        if not row.get("row_hash"):
            if chained:
                problems.append({"id": row.get("id"), "problem": "an unchained row after the chain began"})
            legacy += 1
        else:
            chained += 1
            if _row_hash(row) != row["row_hash"]:
                problems.append({"id": row.get("id"), "problem": "row hash does not match its content"})
            if str(row.get("prev_hash") or "") != _prev_for(prev):
                problems.append({"id": row.get("id"), "problem": "not linked to the row before it"})
        prev = row
    return {"legacy": legacy, "chained": chained, "problems": problems}


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
            except (TypeError, ValueError):
                out.append(dict(zip(_READ, tuple(row))))
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
            f"SELECT {_READ_COLUMNS} FROM {TABLE} WHERE exemption_key = %s "  # nosec B608 — fixed table and columns
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
    current: Optional[dict],
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
    expires_on: str | None,
    notified: list[str],
    tenant_id: str | None,
    scope_kind: str,
    scope_ref: str,
    standard_ids: list[str],
    requested_expires_on: str | None = None,
    step_id: str = "",
    actor: str = "",
    actor_kind: str = "person",
    self_approved: bool = False,
    classification: str = CLASSIFICATION,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Append one immutable, chained event row and return it."""
    if event not in EVENTS or actor_kind not in ACTOR_KINDS or scope_kind not in SCOPES:
        raise ExemptionError(f"bad event {event!r}, actor kind {actor_kind!r} or scope {scope_kind!r}")
    row: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "exemption_key": key,
        "scorecard_key": scorecard_key,
        "rule_identifier": rule_identifier,
        "component_key": component_key,
        "event": event,
        "status": status,
        "event_index": int(current["event_index"]) + 1 if current else 1,
        "reason": reason,
        "requested_by": requested_by,
        "decided_by": decided_by,
        "decision_reason": decision_reason,
        # The switch is gone (icx-coe-02); the column stays so old rows still read.
        "auto_approved": 0,
        "self_approved": 1 if self_approved else 0,
        "expires_on": expires_on,
        "notified": json.dumps(notified),
        "recorded_at": _utcnow_iso(clock),
        "tenant_id": tenant_id,
        "classification": classification or CLASSIFICATION,
        "scope_kind": scope_kind,
        "scope_ref": scope_ref or "",
        "standard_ids": json.dumps(_ids(standard_ids)),
        "requested_expires_on": requested_expires_on,
        "exemption_digest": "",
        "step_id": step_id or "",
        "actor": actor or "",
        "actor_kind": actor_kind,
        "prev_hash": _prev_for(current),
    }
    row["exemption_digest"] = exemption_digest(row)
    row["row_hash"] = _row_hash(row)
    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
    try:
        conn.execute(
            f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) VALUES ({placeholders})",  # nosec B608
            tuple(row[c] for c in INSERT_COLUMNS),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise ExemptionError(f"{key} changed while you were deciding: reload it",
                                 status=409, code="concurrent") from exc
        raise ExemptionError(f"the exemption log could not be written, so nothing changed ({exc})",
                             status=503, code="store_unavailable") from exc
    # Hand the caller the list it passed in rather than the JSON text the column
    # holds — the return value is a Python row, not a database row.
    row["notified"] = list(notified)
    row["ref"] = ref_of(key)
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
                "scope": row.get("scope_kind"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "decision_reason": row.get("decision_reason"),
                "requested_by": row.get("requested_by"),
                "decided_by": row.get("decided_by"),
                "expires_on": row.get("expires_on"),
                "step_id": row.get("step_id"),
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
            severity="warning" if row.get("event") in (EVENT_APPROVED, EVENT_RENEWED) else "info",
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


def _after(row: dict, policy: ExemptionPolicy) -> dict:
    _deliver(row, row.get("notified") or [])
    _mirror_to_component_audit(row)
    return row


# ---------------------------------------------------------------------------
# Scope and the standards an exemption waives
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


def _scorecard_rules(scorecard_key: str) -> Optional[set[str]]:
    """The scorecard's rule identifiers, or None when it is not on disk."""
    try:
        from tools.idp.scorecard import load_scorecard  # noqa: PLC0415

        return {r.identifier for r in load_scorecard(scorecard_key).rules}
    except Exception:  # noqa: BLE001
        return None


def standard_entries(conn: Any = None) -> dict[str, dict]:
    """Every engineering standard known to the register: id -> latest entry.

    The declared seeds (``args/engineering_standards.yaml``), overlaid by the
    ledger's latest entry per standard when the ledger can be read. The ledger is
    read on the register's own connection (``standards._connection``), never on
    *conn*: inside a signed-in request the exemption log's connection carries the
    row-security predicate, which ``coe_standard_events`` (no classification
    column) would fail, and on PostgreSQL that failure aborts *conn*'s
    transaction. *conn* is accepted for the callers' symmetry and not used.
    """
    from tools.idp import standards  # noqa: PLC0415

    out: dict[str, dict] = {}
    try:
        for entry in standards.load_register().get("standards") or []:
            norm = standards.normalise(entry)
            out[norm["id"]] = norm
    except Exception:  # noqa: BLE001 — an unreadable register links nothing
        pass
    try:
        ch = standards.chain(standards.read_events())
        for sid, ordered in ch.items():
            if sid == "_problems" or not ordered:
                continue
            latest = standards.replay(ordered)["latest"]
            if latest:
                out[sid] = latest["entry"]
    except Exception:  # noqa: BLE001 — no ledger: the seeds are what is known
        pass
    return out


def standards_for_rule(scorecard_key: str, rule_identifier: str,
                       entries: Optional[dict[str, dict]] = None) -> list[str]:
    """The standards whose check is this scorecard rule (every rule of it for ``*``)."""
    entries = standard_entries() if entries is None else entries
    out = []
    for sid, entry in entries.items():
        check = (entry.get("check") or {}).get("scorecard_rule") or {}
        if check.get("scorecard") == scorecard_key and (
                rule_identifier == ANY or check.get("rule") == rule_identifier):
            out.append(sid)
    return sorted(out)


def resolve_scope(scorecard_key: str, rule_identifier: str, component_key: str, *,
                  scope: str = "rule", scope_ref: str = "", standards: Iterable[str] = (),
                  entries: Optional[dict[str, dict]] = None,
                  config: Optional[dict] = None) -> dict:
    """The stored shape of a request's target, and the standards it waives.

    Raises ExemptionError for a scope that is not declared, a target the scope
    does not allow, a rule the scorecard does not have, a standard not in the
    register, or a ``standard`` scope whose standard is not checked by a rule of
    this scorecard.
    """
    cfg = config or load_config()
    scope = str(scope or "rule").strip().lower()
    if scope not in cfg["scopes"]:
        raise ExemptionError(f"scope must be one of {', '.join(cfg['scopes'])}", code="scope")
    rule, component, ref = str(rule_identifier or "").strip(), str(component_key or "").strip(), \
        str(scope_ref or "").strip()
    entries = standard_entries() if entries is None else entries
    if scope == "service":
        if rule not in ("", ANY):
            raise ExemptionError("a service scope waives every rule: name no rule", code="scope")
        rule, ref = ANY, ref or component
    elif scope == "repo":
        if component not in ("", ANY):
            raise ExemptionError("a repo scope waives the rule on every component: name no "
                                 "component", code="scope")
        if ref not in cfg["repos"]:
            raise ExemptionError(f"repo must be one of {', '.join(cfg['repos'])}", code="scope")
        component = ANY
    elif scope == "standard":
        ref = ref.upper()
        entry = entries.get(ref)
        if entry is None:
            raise ExemptionError(f"no standard {ref or '(none)'} in the register", code="standard")
        check = (entry.get("check") or {}).get("scorecard_rule") or {}
        if check.get("scorecard") != scorecard_key or not check.get("rule"):
            raise ExemptionError(f"{ref} is not checked by a rule of scorecard {scorecard_key}: "
                                 "waive the rule itself, or the standard's evidence step is "
                                 "not an exemption", code="standard")
        if rule and rule != check["rule"]:
            raise ExemptionError(f"{ref} is checked by {check['rule']}, not {rule}", code="standard")
        rule = check["rule"]
    if not rule:
        raise ExemptionError("an exemption names the rule it waives", code="scope")
    if not component:
        raise ExemptionError("an exemption names the component it waives", code="scope")
    known = _scorecard_rules(scorecard_key)
    if known is not None and rule != ANY and rule not in known:
        raise ExemptionError(f"scorecard {scorecard_key} has no rule {rule!r}", code="scope")
    named = _ids(list(standards or []))
    unknown = [s for s in named if s not in entries]
    if unknown:
        raise ExemptionError(f"not in the standards register: {', '.join(unknown)}", code="standard")
    linked = sorted(set(named) | set(standards_for_rule(scorecard_key, rule, entries))
                    | ({ref} if scope == "standard" else set()))
    return {"rule": rule, "component": component, "scope": scope, "scope_ref": ref,
            "standard_ids": linked}


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _actor_id(actor: Any) -> str:
    return _meaningful(actor.get("id") if isinstance(actor, dict) else actor)


def request_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    reason: str,
    requested_by: str,
    expires: str | None = None,
    scope: str = "rule",
    scope_ref: str = "",
    standards: Iterable[str] = (),
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
    actor_kind: str = "person",
    classification: str | None = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """File an exemption request. Lands at ``pending`` and waives nothing.

    Its rows carry :func:`label_for` *classification*: the requester's session
    level when none is named.

    Refused: no reason, no requester, no expiry (or one not after today, or too
    far out), an undeclared scope, an unknown rule or standard, exemptions
    disabled or ``autoApprove`` set on the scorecard, or a live (pending or
    approved) exemption on the same target.
    """
    reason = _meaningful(reason)
    requested_by = _meaningful(requested_by)
    if not reason:
        raise ExemptionError(
            "an exemption needs a reason — an unexplained waiver is a silent failure",
            code="reason")
    if not requested_by:
        raise ExemptionError("an exemption needs a requester", status=401, code="no_actor")
    cfg = load_config()
    today = _today(clock)
    expiry = validate_expiry(expires, today=today, config=cfg)
    label = label_for(classification)

    policy = _policy_for(scorecard_key, policy)
    if not policy.enabled:
        raise ExemptionError(
            f"scorecard {scorecard_key!r} has exemptions disabled (exemptions.enabled: false)",
            code="disabled")
    if policy.auto_approve:
        raise ExemptionError(
            f"scorecard {scorecard_key!r} sets autoApprove, which is no longer honoured: "
            "granting is a person's declared step (icx-coe-02). Remove the switch.",
            status=409, code="auto_approve")

    own = conn is None
    conn = conn or _open_connection()
    try:
        target = resolve_scope(scorecard_key, rule_identifier, component_key, scope=scope,
                               scope_ref=scope_ref, standards=standards,
                               entries=standard_entries(conn), config=cfg)
        if not table_ready(conn):
            raise ExemptionError(
                f"{TABLE} is missing — run migration 20260803030514_idp_rule_exemptions",
                status=503, code="store_unavailable")
        key = exemption_key(scorecard_key, target["rule"], target["component"])
        current = _latest(conn, key)
        if current and str(current.get("status")) in (STATUS_PENDING, STATUS_APPROVED):
            raise ExemptionError(
                f"{key} already has a {current['status']} exemption "
                f"(event {current['event_index']}): revoke it, or ask for a renewal",
                status=409, code="exists")
        notified = notify_targets(policy, {"requested_by": requested_by, "decided_by": None,
                                           "component_key": target["component"]})
        row = _append_event(
            conn, current=current, key=key, scorecard_key=scorecard_key,
            rule_identifier=target["rule"], component_key=target["component"],
            event=EVENT_REQUESTED, status=STATUS_PENDING, reason=reason,
            requested_by=requested_by, decided_by=None, decision_reason=None,
            expires_on=expiry, notified=notified, tenant_id=tenant_id,
            scope_kind=target["scope"], scope_ref=target["scope_ref"],
            standard_ids=target["standard_ids"], actor=requested_by, actor_kind=actor_kind,
            classification=label, clock=clock,
        )
        return _after(row, policy)
    finally:
        if own:
            conn.close()


def builders(events: list[dict]) -> dict[str, str]:
    """Who may not grant or renew this exemption (``separate_from: builders``).

    Its requester, and whoever asked for a renewal of it.
    """
    out: dict[str, str] = {}
    for row in events:
        who = _meaningful(row.get("requested_by"))
        if who:
            out.setdefault(who, "requested this exemption")
    for row in events:
        if row.get("event") == EVENT_RENEWAL_REQUESTED:
            asker = _meaningful(row.get("actor"))
            if asker:
                out.setdefault(asker, "asked for a renewal of this exemption")
    return out


def _take(step: str, *, key: str, current: dict, decision: str, reason: str, actor: Any,
          separated: dict[str, str]) -> dict:
    """Take the declared step, or raise ExemptionError. Nothing is written here."""
    from tools.workflow_hitl import declared  # noqa: PLC0415

    if not isinstance(actor, dict):
        raise ExemptionError("granting and renewing are a signed-in person's declared step "
                             "on /idp/exemptions", status=401, code="no_person")
    record = exemption_record(current)
    try:
        return declared.take_step(
            TEMPLATES[step], surface=SURFACES[step], decision=str(decision or "").strip().lower(),
            subject=f"exemption:{key}", reason=str(reason or "").strip(), actor=actor,
            payload={"exemption_key": key, "digest": exemption_digest(current),
                     "expires_on": record["expires_on"], "scope": record["scope"],
                     "standard_ids": record["standard_ids"]},
            separated_from=separated)
    except declared.StepRefused as exc:
        raise ExemptionError(exc.message, status=exc.status, code=exc.code) from exc


def _state_for(conn: Any, key: str) -> tuple[dict, list[dict]]:
    events = _events_for(conn, key)
    if not events:
        raise ExemptionError(f"no exemption {key!r}", status=404, code="unknown_exemption")
    problems = verify_chain(events)["problems"]
    if problems:
        raise ExemptionError(f"{key}'s history does not verify: {problems[0]['problem']}",
                             status=409, code="chain_broken")
    return events[-1], events


def _carry(current: dict) -> dict:
    """The fields every later row carries forward from the one before it."""
    return {
        "key": current["exemption_key"], "scorecard_key": str(current["scorecard_key"]),
        "rule_identifier": str(current["rule_identifier"]),
        "component_key": str(current["component_key"]), "reason": str(current.get("reason") or ""),
        "requested_by": str(current.get("requested_by") or ""),
        "tenant_id": current.get("tenant_id"),
        "scope_kind": str(current.get("scope_kind") or "rule"),
        "scope_ref": str(current.get("scope_ref") or ""),
        "standard_ids": _ids(current.get("standard_ids")),
        "classification": str(current.get("classification") or CLASSIFICATION),
    }


def decide_exemption(
    key: str,
    *,
    decision: str,
    reason: str,
    read_digest: str,
    actor: Any,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """A person (never the requester) grants or denies a pending request.

    The person's step of ``decl-wft-coe-exemption-grant``, with a reason, bound
    to the digest of the request they read. A request whose expiry is no longer
    after today cannot be granted.
    """
    own = conn is None
    conn = conn or _open_connection()
    try:
        current, events = _state_for(conn, key)
        if str(current.get("status")) != STATUS_PENDING:
            raise ExemptionError(f"{key} is {current['status']}: only a pending request is "
                                 "granted or denied", status=409, code="out_of_order")
        if str(read_digest or "").strip() != exemption_digest(current):
            raise ExemptionError(f"{key} is not what you read: its digest is "
                                 f"{exemption_digest(current)[:12]} (reload it)",
                                 status=409, code="digest")
        today = _today(clock)
        expiry = _iso_date(current.get("expires_on"))
        if str(decision or "").strip().lower() == "grant":
            if not expiry:
                raise ExemptionError(f"{key} has no expiry: it cannot be granted; revoke the "
                                     "request and ask again with one", status=409, code="no_expiry")
            if expiry <= today:
                raise ExemptionError(f"{key} would expire on {expiry}, not after today: it "
                                     "cannot be granted", status=409, code="bad_expiry")
        policy = _policy_for(str(current["scorecard_key"]), policy)
        taken = _take("grant", key=key, current=current, decision=decision, reason=reason,
                      actor=actor, separated=builders(events))
        granted = taken["affirmative"]
        carry = _carry(current)
        notified = notify_targets(policy, {"requested_by": carry["requested_by"],
                                           "decided_by": taken["decided_by"],
                                           "component_key": carry["component_key"]})
        row = _append_event(
            conn, current=current, **carry,
            event=EVENT_APPROVED if granted else EVENT_DENIED,
            status=STATUS_APPROVED if granted else STATUS_DENIED,
            decided_by=taken["decided_by"], decision_reason=str(reason or "").strip(),
            expires_on=expiry or None, notified=notified, step_id=taken["step_id"],
            actor=taken["decided_by"], actor_kind="person", clock=clock,
        )
        row["decision_step"] = taken
        return _after(row, policy)
    finally:
        if own:
            conn.close()


def approve_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    actor: Any,
    decision_reason: str,
    read_digest: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Grant a pending request: the event that makes it apply.

    A person's declared step (:func:`decide_exemption`). ``read_digest`` is the
    digest of the request the person read; omitted, it is read now, which is
    what a caller holding the state in hand (a test, the MCP read) does.
    """
    key = exemption_key(scorecard_key, rule_identifier, component_key)
    own = conn is None
    conn = conn or _open_connection()
    try:
        if read_digest is None:
            current = _latest(conn, key)
            read_digest = exemption_digest(current) if current else ""
        return decide_exemption(key, decision="grant", reason=decision_reason,
                                read_digest=read_digest, actor=actor, policy=policy, conn=conn,
                                clock=clock)
    finally:
        if own:
            conn.close()


def deny_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    actor: Any,
    decision_reason: str,
    read_digest: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Deny a pending request. The rule keeps applying. A person's declared step."""
    key = exemption_key(scorecard_key, rule_identifier, component_key)
    own = conn is None
    conn = conn or _open_connection()
    try:
        if read_digest is None:
            current = _latest(conn, key)
            read_digest = exemption_digest(current) if current else ""
        return decide_exemption(key, decision="deny", reason=decision_reason,
                                read_digest=read_digest, actor=actor, policy=policy, conn=conn,
                                clock=clock)
    finally:
        if own:
            conn.close()


def request_renewal(
    key: str,
    *,
    expires: str,
    reason: str,
    requested_by: Any,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Ask for an approved exemption (live or expired) to run to a new expiry.

    Decides nothing: the exemption keeps its current expiry, and lapses on it,
    until a person renews it (:func:`renew_exemption`). Refused while another
    renewal waits.
    """
    asker = _actor_id(requested_by)
    reason = _meaningful(reason)
    if not asker:
        raise ExemptionError("a renewal needs a requester", status=401, code="no_actor")
    if not reason:
        raise ExemptionError("a renewal needs a reason", code="reason")
    today = _today(clock)
    expiry = validate_expiry(expires, today=today)
    own = conn is None
    conn = conn or _open_connection()
    try:
        current, _ = _state_for(conn, key)
        if str(current.get("status")) != STATUS_APPROVED:
            raise ExemptionError(f"{key} is {current['status']}: only a granted exemption is "
                                 "renewed", status=409, code="out_of_order")
        if current.get("event") == EVENT_RENEWAL_REQUESTED:
            raise ExemptionError(f"{key} already has a renewal waiting", status=409, code="exists")
        if expiry <= _iso_date(current.get("expires_on")):
            raise ExemptionError(f"a renewal runs past the current expiry "
                                 f"({current.get('expires_on')})", code="bad_expiry")
        policy = _policy_for(str(current["scorecard_key"]), policy)
        carry = _carry(current)
        row = _append_event(
            conn, current=current, **carry, event=EVENT_RENEWAL_REQUESTED, status=STATUS_APPROVED,
            decided_by=current.get("decided_by"),
            decision_reason=reason,
            expires_on=current.get("expires_on"), requested_expires_on=expiry,
            notified=notify_targets(policy, {"requested_by": asker, "decided_by": None,
                                             "component_key": carry["component_key"]}),
            actor=asker, actor_kind="agent" if asker.startswith("agent:") else "person",
            clock=clock,
        )
        return _after(row, policy)
    finally:
        if own:
            conn.close()


def renew_exemption(
    key: str,
    *,
    decision: str,
    reason: str,
    read_digest: str,
    actor: Any,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """A person (never the requester or the asker) renews it, or refuses.

    The person's step of ``decl-wft-coe-exemption-renew``, bound to the digest
    of the renewal they read. Refused, the exemption keeps its expiry and lapses
    on it. There is no automatic renewal.
    """
    own = conn is None
    conn = conn or _open_connection()
    try:
        current, events = _state_for(conn, key)
        if current.get("event") != EVENT_RENEWAL_REQUESTED:
            raise ExemptionError(f"{key} has no renewal waiting", status=409, code="out_of_order")
        if str(read_digest or "").strip() != exemption_digest(current):
            raise ExemptionError(f"{key} is not what you read: its digest is "
                                 f"{exemption_digest(current)[:12]} (reload it)",
                                 status=409, code="digest")
        policy = _policy_for(str(current["scorecard_key"]), policy)
        taken = _take("renew", key=key, current=current, decision=decision, reason=reason,
                      actor=actor, separated=builders(events))
        renewed = taken["affirmative"]
        carry = _carry(current)
        row = _append_event(
            conn, current=current, **carry,
            event=EVENT_RENEWED if renewed else EVENT_RENEWAL_REFUSED, status=STATUS_APPROVED,
            decided_by=taken["decided_by"] if renewed else current.get("decided_by"),
            decision_reason=str(reason or "").strip(),
            expires_on=current.get("requested_expires_on") if renewed else current.get("expires_on"),
            notified=notify_targets(policy, {"requested_by": carry["requested_by"],
                                             "decided_by": taken["decided_by"],
                                             "component_key": carry["component_key"]}),
            step_id=taken["step_id"], actor=taken["decided_by"], actor_kind="person", clock=clock,
        )
        row["decision_step"] = taken
        return _after(row, policy)
    finally:
        if own:
            conn.close()


def revoke_exemption(
    scorecard_key: str,
    rule_identifier: str,
    component_key: str,
    *,
    revoked_by: Any,
    decision_reason: str | None = None,
    policy: ExemptionPolicy | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Withdraw a live exemption or a pending request. The rule applies again.

    A stop: immediate, with a stated reason, by the person who says so. It only
    narrows, so it takes no declared step. Revocation is an append, not a
    delete: the waiver still happened, and the window during which it applied
    is part of the record.
    """
    return revoke_by_key(exemption_key(scorecard_key, rule_identifier, component_key),
                         revoked_by=revoked_by, reason=decision_reason or "", policy=policy,
                         conn=conn, clock=clock)


def revoke_by_key(key: str, *, revoked_by: Any, reason: str,
                  policy: ExemptionPolicy | None = None, conn: Any = None,
                  clock: Optional[Clock] = None) -> dict[str, Any]:
    """:func:`revoke_exemption` by the exemption's key."""
    actor = _actor_id(revoked_by)
    if not actor:
        raise ExemptionError("an exemption revoked must name who did it — an unattributed "
                             "decision is not a decision", status=401, code="no_actor")
    if not _meaningful(reason):
        raise ExemptionError("a revocation states its reason", code="reason")
    own = conn is None
    conn = conn or _open_connection()
    try:
        current, _ = _state_for(conn, key)
        if str(current.get("status")) not in (STATUS_PENDING, STATUS_APPROVED):
            raise ExemptionError(f"{key} is {current['status']}; revoked requires "
                                 f"{STATUS_PENDING} or {STATUS_APPROVED}",
                                 status=409, code="out_of_order")
        policy = _policy_for(str(current["scorecard_key"]), policy)
        carry = _carry(current)
        row = _append_event(
            conn, current=current, **carry, event=EVENT_REVOKED, status=STATUS_REVOKED,
            decided_by=actor, decision_reason=_meaningful(reason),
            expires_on=current.get("expires_on"),
            notified=notify_targets(policy, {"requested_by": carry["requested_by"],
                                             "decided_by": actor,
                                             "component_key": carry["component_key"]}),
            actor=actor, actor_kind="agent" if actor.startswith("agent:") else "person",
            clock=clock,
        )
        return _after(row, policy)
    finally:
        if own:
            conn.close()


def migrate_legacy(conn: Any = None, *, clock: Optional[Clock] = None) -> dict[str, Any]:
    """Give every approved exemption with no expiry one, by the labelled bootstrap identity.

    Expiry became mandatory with icx-coe-02. An exemption approved before then
    with none would otherwise apply for ever, or (read by the new rule) stop
    applying silently. Neither is a person's decision, so this appends one
    ``migrated`` row per such exemption: still approved by whoever approved it,
    now expiring ``migration_grace_days`` from today, written by
    ``migration_identity`` (never a person). The twin reports each as
    ``coe.exemption.migrated`` until a person renews it or lets it lapse.
    Idempotent: a migrated exemption has an expiry.
    """
    cfg = load_config()
    ident = str(cfg["migration_identity"])
    expiry = (date.fromisoformat(_today(clock))
              + timedelta(days=int(cfg["migration_grace_days"]))).isoformat()
    own = conn is None
    conn = conn or _open_connection()
    migrated = []
    try:
        if not table_ready(conn):
            return {"identity": ident, "migrated": [], "expires_on": expiry}
        for state in current_states(conn=conn):
            if str(state.get("status")) != STATUS_APPROVED or _iso_date(state.get("expires_on")):
                continue
            carry = _carry(state)
            row = _append_event(
                conn, current=state, **carry, event=EVENT_MIGRATED, status=STATUS_APPROVED,
                decided_by=state.get("decided_by"),
                decision_reason=(f"expiry became mandatory (icx-coe-02). Given one "
                                 f"{cfg['migration_grace_days']} days out by the migration, never "
                                 "granted again: a person renews it through the declared step, "
                                 "or it lapses."),
                expires_on=expiry, notified=[], actor=ident, actor_kind="bootstrap", clock=clock,
            )
            migrated.append({"exemption_key": row["exemption_key"], "event_id": row["id"],
                             "expires_on": expiry})
    finally:
        if own:
            conn.close()
    return {"identity": ident, "migrated": migrated, "expires_on": expiry}


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
                f"SELECT {_READ_COLUMNS} FROM {TABLE} {clause} "  # nosec B608 — fixed columns
                "ORDER BY exemption_key, event_index",
                tuple(params),
            ).fetchall()
        )
    finally:
        if own:
            conn.close()


def _latest_of(events: Iterable[dict]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        key = str(event.get("exemption_key"))
        previous = latest.get(key)
        if previous is None or int(event.get("event_index") or 0) >= int(
            previous.get("event_index") or 0
        ):
            latest[key] = event
    return latest


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
    latest = _latest_of(events)
    return [latest[k] for k in sorted(latest)]


def classify(state: dict, today: str, *, warn_days: int = 14,
             policy: ExemptionPolicy | None = None) -> str:
    """What one exemption is now: one of :data:`STATES`."""
    status = str(state.get("status") or "")
    if status == STATUS_PENDING:
        return "pending"
    if status == STATUS_DENIED:
        return "denied"
    if status == STATUS_REVOKED:
        return "revoked"
    defect = attribution_defect(status=status, approved_by=state.get("decided_by"),
                                reason=state.get("reason"), expires=state.get("expires_on"),
                                today=today, policy=policy)
    if is_expired_defect(defect):
        return "expired"
    if defect:
        return "inert"
    if state.get("event") == EVENT_RENEWAL_REQUESTED:
        return "renewal_pending"
    soon = (date.fromisoformat(today) + timedelta(days=int(warn_days))).isoformat()
    return "expiring" if _iso_date(state.get("expires_on")) <= soon else "active"


def active_grants(
    scorecard_key: str,
    conn: Any = None,
    *,
    today: str | None = None,
    policy: ExemptionPolicy | None = None,
) -> list[dict[str, Any]]:
    """Approved, attributed, unexpired exemptions for one scorecard.

    This is what the evaluator consumes. Anything that fails
    :func:`attribution_defect` is left out here and reported separately, so a
    waiver never applies without an approver, a reason and an expiry ahead.
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


def next_steps(state: dict, today: str) -> list[str]:
    """The steps that may be taken next (what the page offers)."""
    status = str(state.get("status") or "")
    if status == STATUS_PENDING:
        return ["grant", "revoke"]
    if status == STATUS_APPROVED:
        if state.get("event") == EVENT_RENEWAL_REQUESTED:
            return ["renew", "revoke"]
        return ["request_renewal", "revoke"]
    return []


def _summary(state: dict, events: list[dict], today: str, cfg: dict) -> dict:
    key = str(state["exemption_key"])
    chain = verify_chain(events)
    return {
        "ref": ref_of(key), "exemption_key": key, "scorecard": state.get("scorecard_key"),
        "rule": state.get("rule_identifier"), "component": state.get("component_key"),
        "scope": state.get("scope_kind") or "rule", "scope_ref": state.get("scope_ref") or "",
        "standard_ids": _ids(state.get("standard_ids")),
        "status": state.get("status"), "event": state.get("event"),
        "state": classify(state, today, warn_days=int(cfg["expiring_warn_days"])),
        "expires_on": _iso_date(state.get("expires_on")) or None,
        "requested_expires_on": _iso_date(state.get("requested_expires_on")) or None,
        "reason": state.get("reason"), "requested_by": state.get("requested_by"),
        "approved_by": state.get("decided_by") if state.get("status") == STATUS_APPROVED else None,
        "digest": exemption_digest(state), "event_index": state.get("event_index"),
        "migrated": any(e.get("event") == EVENT_MIGRATED for e in events),
        "self_approved": bool(state.get("self_approved")) or any(
            e.get("event") == EVENT_APPROVED and int(e.get("self_approved") or 0) for e in events),
        "chain": {"legacy": chain["legacy"], "chained": chain["chained"],
                  "intact": not chain["problems"], "problems": chain["problems"]},
        "updated_at": state.get("recorded_at"),
    }


def exemptions_view(conn: Any = None, *, today: str | None = None,
                    as_of: str | None = None) -> dict:
    """Every exemption with its state now (or ``as_of`` a later date), and the gate.

    ``as_of`` is a look-ahead: the same exemptions judged on another day, so a
    person can see what will have expired by then. It changes nothing.
    """
    cfg = load_config()
    today = today or _today()
    when = _iso_date(as_of) or today
    own = conn is None
    conn = conn or _open_connection()
    try:
        events = history(conn=conn) if table_ready(conn) else []
    finally:
        if own:
            conn.close()
    by_key: dict[str, list[dict]] = {}
    for e in events:
        by_key.setdefault(str(e["exemption_key"]), []).append(e)
    latest = _latest_of(events)
    rows = [_summary(latest[k], sorted(by_key[k], key=lambda r: int(r["event_index"])), when, cfg)
            for k in sorted(latest)]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    failed = [r for r in rows if r["state"] in ("expired",)]
    return {"today": today, "as_of": when, "look_ahead": when != today, "exemptions": rows,
            "counts": counts, "gate": "fail" if failed else "pass",
            "failed": [r["exemption_key"] for r in failed],
            "scopes": list(cfg["scopes"]), "repos": list(cfg["repos"]),
            "max_expiry_days": cfg["max_expiry_days"], "templates": dict(TEMPLATES),
            "warn_days": cfg["expiring_warn_days"]}


def find_key(ref: str, conn: Any = None) -> str:
    """The exemption key for a page's ``ref``."""
    for state in current_states(conn=conn):
        if ref_of(str(state["exemption_key"])) == str(ref or "").strip():
            return str(state["exemption_key"])
    raise ExemptionError(f"no exemption {ref}", status=404, code="unknown_exemption")


def exemption_view(ref: str, conn: Any = None, *, today: str | None = None) -> dict:
    """One exemption: its state, the full history, the chain, the digest and the step it waits for."""
    cfg = load_config()
    today = today or _today()
    own = conn is None
    conn = conn or _open_connection()
    try:
        key = find_key(ref, conn)
        events = _events_for(conn, key)
    finally:
        if own:
            conn.close()
    state = events[-1]
    return {**_summary(state, events, today, cfg),
            "history": [{k: e.get(k) for k in ("id", "event", "status", "event_index",
                                                "requested_by", "decided_by", "decision_reason",
                                                "expires_on", "requested_expires_on", "step_id",
                                                "actor", "actor_kind", "recorded_at", "row_hash")}
                        for e in events],
            "builders": builders(events), "next": next_steps(state, today)}


# ---------------------------------------------------------------------------
# The check: CI and the scorecard
# ---------------------------------------------------------------------------


def config_grants(directory: Any = None) -> list[dict]:
    """Grants written into scorecard YAML: not a person's declared step, so never a grant."""
    from tools.idp.scorecard import _relative_source, load_scorecards  # noqa: PLC0415

    out = []
    for card in load_scorecards(directory):
        for g in card.exemptions:
            out.append({"scorecard": card.key, "rule": g.identifier, "component": g.entity,
                        "expires": g.expires or None,
                        "source": _relative_source(card.source_path)})
    return out


def check(conn: Any = None, *, today: str | None = None, directory: Any = None) -> dict:
    """Fail on what may not stand: an exemption past its expiry that nobody renewed or
    revoked, an approved one with no expiry, a grant written into scorecard YAML, a
    scorecard that sets ``autoApprove``, or a log row that does not verify.

    The log is read when there is one; with no database (a CI checkout) the result
    says it was not read, and the file checks still run.
    """
    from tools.idp.scorecard import load_scorecards  # noqa: PLC0415

    today = today or _today()
    out: dict[str, Any] = {"today": today, "expired": [], "no_expiry": [], "pending_no_expiry": [],
                           "chain_problems": [], "config_grants": config_grants(directory),
                           "auto_approve": [c.key for c in load_scorecards(directory)
                                            if c.exemption_policy.auto_approve],
                           "log": "read"}
    try:
        own = conn is None
        conn = conn or _open_connection()
        try:
            events = history(conn=conn) if table_ready(conn) else None
        finally:
            if own:
                conn.close()
    except Exception as exc:  # noqa: BLE001 — no database: say so, never pass it
        events, out["log"] = None, f"not read ({exc})"
    if events is None and out["log"] == "read":
        out["log"] = f"not read ({TABLE} is absent)"
    by_key: dict[str, list[dict]] = {}
    for e in events or []:
        by_key.setdefault(str(e["exemption_key"]), []).append(e)
    for key, rows in sorted(by_key.items()):
        rows = sorted(rows, key=lambda r: int(r["event_index"]))
        state = rows[-1]
        for p in verify_chain(rows)["problems"]:
            out["chain_problems"].append({"exemption_key": key, **p})
        if str(state.get("status")) == STATUS_PENDING and not _iso_date(state.get("expires_on")):
            out["pending_no_expiry"].append(key)
        if str(state.get("status")) != STATUS_APPROVED:
            continue
        expiry = _iso_date(state.get("expires_on"))
        if not expiry:
            out["no_expiry"].append(key)
        elif expiry < today:
            out["expired"].append({"exemption_key": key, "ref": ref_of(key), "expires_on": expiry,
                                   "event_id": state.get("id")})
    out["ok"] = not (out["expired"] or out["no_expiry"] or out["config_grants"]
                     or out["auto_approve"] or out["chain_problems"])
    return out


# ---------------------------------------------------------------------------
# The twin's evidence and judgement (canvas idp,
# tools/twin_core/adapters/coe_standards.py)
# ---------------------------------------------------------------------------


def twin_evidence() -> dict:
    """What the governance twin reads about the exemptions. Never raises."""
    ev: dict[str, Any] = {"log": None, "config_grants": None, "errors": {}}
    try:
        ev["config_grants"] = config_grants()
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["scorecards"] = str(exc)
    try:
        conn = _open_connection()
        try:
            if not table_ready(conn):
                raise RuntimeError(f"{TABLE} is absent")
            events = history(conn=conn)
            known = set(standard_entries(conn))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        ev["errors"]["log"] = str(exc)
        return ev
    by_key: dict[str, list[dict]] = {}
    for e in events:
        by_key.setdefault(str(e["exemption_key"]), []).append(e)
    items = []
    for key, rows in sorted(by_key.items()):
        rows = sorted(rows, key=lambda r: int(r["event_index"]))
        state = rows[-1]
        migrated = [r for r in rows if r.get("event") == EVENT_MIGRATED]
        renewed_after = migrated and any(
            r.get("event") == EVENT_RENEWED and int(r["event_index"]) > int(migrated[-1]["event_index"])
            for r in rows)
        items.append({
            "exemption_key": key, "ref": ref_of(key), "status": state.get("status"),
            "event": state.get("event"), "last_row": state.get("id"),
            "expires_on": _iso_date(state.get("expires_on")) or None,
            "standard_ids": _ids(state.get("standard_ids")),
            "unknown_standards": [s for s in _ids(state.get("standard_ids")) if s not in known],
            "migrated_row": migrated[-1]["id"] if migrated and not renewed_after else None,
            "self_approved_row": next((r["id"] for r in rows if r.get("event") == EVENT_APPROVED
                                       and int(r.get("self_approved") or 0)), None),
            "chain": verify_chain(rows),
        })
    ev["log"] = {"exemptions": items, "rows": len(events)}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the exemptions. Pure: replays from the evidence.

    Expiry is judged on the observation's own date, so a replay judges the same.
    """
    from tools.twin_core.governance import check as twin_check  # noqa: PLC0415
    from tools.twin_core.governance import finding  # noqa: PLC0415

    errors = ev.get("errors") or {}
    log, grants = ev.get("log"), ev.get("config_grants")
    today = str(observed_at or "")[:10] or _today()
    warn = int(policy.get("exemption_expiring_warn_days") or 14)
    soon = (date.fromisoformat(today) + timedelta(days=warn)).isoformat()
    checks = [
        twin_check("coe.exemptions.log", "Exemption log (idp_rule_exemptions)", log is not None,
                   errors.get("log") or "log not read"),
        twin_check("coe.exemptions.config", "Scorecard exemption blocks (args/scorecards/)",
                   grants is not None, errors.get("scorecards") or "scorecards not read"),
    ]
    findings: list[dict] = []
    expired = active = 0
    for x in (log or {}).get("exemptions") or []:
        thread = f"idp_exemption:{x['last_row']}"
        for p in x["chain"]["problems"]:
            findings.append(finding(
                "coe.exemption.chain_broken", "critical", f"{x['exemption_key']}: {p['problem']}",
                thread_id=f"idp_exemption:{p['id']}",
                recommendation="The exemption's history cannot be trusted; the log is append-only, "
                               "so find who wrote the row and treat it as an incident."))
        if x["status"] != STATUS_APPROVED:
            continue
        if not x["expires_on"]:
            findings.append(finding(
                "coe.exemption.no_expiry", "high",
                f"{x['exemption_key']} is approved with no expiry: it does not apply",
                thread_id=thread,
                recommendation="Run python tools/idp/exemptions.py --migrate-legacy, then a person "
                               "renews it or lets it lapse."))
            continue
        if x["expires_on"] < today:
            expired += 1
            findings.append(finding(
                "coe.exemption.expired", "high",
                f"{x['exemption_key']} expired on {x['expires_on']}: it fails the scorecard",
                thread_id=thread,
                recommendation="A person renews it (decl-wft-coe-exemption-renew) or revokes it on "
                               "/idp/exemptions; until then the scorecard and the CI check fail."))
        else:
            active += 1
            if x["expires_on"] <= soon:
                findings.append(finding(
                    "coe.exemption.expiring", "low",
                    f"{x['exemption_key']} expires on {x['expires_on']}",
                    thread_id=thread,
                    recommendation="Ask for a renewal now, or fix the rule: nothing renews by itself."))
        if x.get("migrated_row"):
            findings.append(finding(
                "coe.exemption.migrated", "medium",
                f"{x['exemption_key']} was given an expiry ({x['expires_on']}) by the migration, "
                "not by a person",
                thread_id=f"idp_exemption:{x['migrated_row']}",
                recommendation="The owner reviews it: a person renews it through the declared "
                               "step, or it lapses on its date."))
        if x.get("self_approved_row"):
            findings.append(finding(
                "coe.exemption.self_approved", "medium",
                f"{x['exemption_key']} was approved by its own requester (before icx-coe-02)",
                thread_id=f"idp_exemption:{x['self_approved_row']}",
                recommendation="Revoke it, or let it lapse and request it again for someone "
                               "else's step."))
        if x.get("unknown_standards"):
            findings.append(finding(
                "coe.exemption.unknown_standard", "medium",
                f"{x['exemption_key']} waives a standard the register no longer has: "
                f"{', '.join(x['unknown_standards'])}",
                thread_id=thread,
                recommendation="Revoke it, or request it again against the register as it is."))
    for g in grants or []:
        findings.append(finding(
            "coe.exemption.config_grant", "high",
            f"{g['scorecard']}: {g['rule']}:{g['component']} is granted in the scorecard file, "
            "not by a person's step",
            thread_id=f"config:{g.get('source') or 'args/scorecards'}@",
            recommendation="Remove it from the YAML and request it on /idp/exemptions."))
    items = (log or {}).get("exemptions") or []
    summary = {"exemptions": len(items), "exemptions_active": active,
               "exemptions_expired": expired,
               "exemptions_pending": sum(1 for x in items if x["status"] == STATUS_PENDING),
               "exemption_gate": ("fail" if expired else "pass") if log is not None else "unknown"}
    return checks, findings, summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(rows: Iterable[dict[str, Any]], today: str) -> str:
    rows = list(rows)
    if not rows:
        return "(no exemptions)"
    lines = [
        f"{'rule':<20} {'component':<22} {'scope':<8} {'state':<15} {'approver':<16} "
        f"{'expires':<12} reason",
        "-" * 116,
    ]
    for row in rows:
        lines.append(
            f"{str(row.get('rule_identifier') or ''):<20} "
            f"{str(row.get('component_key') or ''):<22} "
            f"{str(row.get('scope_kind') or 'rule'):<8} "
            f"{classify(row, today):<15} "
            f"{str(row.get('decided_by') or '-'):<16} "
            f"{str(row.get('expires_on') or '-'):<12} "
            f"{str(row.get('reason') or '')}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/idp/exemptions.py",
        description="Request, audit and check scorecard exemptions that expire (icx-coe-02). "
                    "Granting and renewing are a person's declared step on /idp/exemptions.",
    )
    ap.add_argument("--scorecard", default="component-readiness", help="Scorecard key")
    ap.add_argument("--list", action="store_true", help="Show current state of each exemption")
    ap.add_argument("--history", metavar="RULE:COMPONENT", nargs="?", const="",
                    help="Show every event (optionally for one rule:component)")
    ap.add_argument("--request", metavar="RULE:COMPONENT", help="File a request")
    ap.add_argument("--revoke", metavar="RULE:COMPONENT", help="Withdraw a live exemption (a stop)")
    ap.add_argument("--check", action="store_true",
                    help="Expired, expiry-less, config-declared or auto-approved exemptions")
    ap.add_argument("--gate", action="store_true", help="With --check: exit 1 on a finding")
    ap.add_argument("--migrate-legacy", action="store_true",
                    help="Give approved exemptions with no expiry one (bootstrap identity)")
    ap.add_argument("--reason", help="Why the rule should not apply (required to request)")
    ap.add_argument("--requested-by", help="Who is asking")
    ap.add_argument("--by", help="Who is revoking")
    ap.add_argument("--decision-reason", help="Why (required to revoke)")
    ap.add_argument("--expires", metavar="YYYY-MM-DD", help="Expiry date (required to request)")
    ap.add_argument("--scope", default="rule", choices=SCOPES, help="Breadth of the waiver")
    ap.add_argument("--scope-ref", default="", help="The repo (repo scope) or standard id (standard scope)")
    ap.add_argument("--tenant", help="Tenant id to record")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    if args.check:
        out = check()
        print(json.dumps(out, indent=2, default=str) if args.json else
              f"exemption check: {'ok' if out['ok'] else 'FAILED'} (log {out['log']}); "
              f"expired {len(out['expired'])}, no expiry {len(out['no_expiry'])}, "
              f"config grants {len(out['config_grants'])}, autoApprove {len(out['auto_approve'])}, "
              f"chain problems {len(out['chain_problems'])}")
        return 1 if (args.gate and not out["ok"]) else 0

    actions = [args.list, args.history is not None, args.request, args.revoke, args.migrate_legacy]
    if not any(actions):
        ap.error("one of --list, --history, --request, --revoke, --check or --migrate-legacy "
                 "is required (granting and renewing are a person's step on /idp/exemptions)")

    try:
        conn = _open_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot open the database: {exc}", file=sys.stderr)
        return 2

    try:
        if args.migrate_legacy:
            print(json.dumps(migrate_legacy(conn), indent=2, default=str))
            return 0

        if args.list:
            rows = current_states(args.scorecard, conn=conn)
            print(json.dumps(rows, indent=2, default=str) if args.json else _render(rows, _today()))
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
                        f"{row['event']:<17} {row['rule_identifier']}:{row['component_key']}  "
                        f"by={row.get('decided_by') or row.get('requested_by')}  "
                        f"{row.get('decision_reason') or row.get('reason') or ''}"
                    )
                if not rows:
                    print("(no events)")
            return 0

        if args.request:
            rule, component = parse_target(args.request) if ":" in args.request else (args.request, "")
            row = request_exemption(
                args.scorecard, rule, component,
                reason=args.reason or "",
                requested_by=args.requested_by or "",
                expires=args.expires,
                scope=args.scope, scope_ref=args.scope_ref,
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
            f"{row['component_key']} is now {row['status']} (expires {row['expires_on']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
