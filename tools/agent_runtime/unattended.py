# CUI // SP-CTI
"""Per-session unattended flag — ROUTING ONLY (agov-inbox-04).

``tools/agent_runtime/safety.py`` ``console_approver`` denies on EOF and
``approval_gate.py`` falls back to ``deny_all_approver``, so a headless
overnight run refuses every irreversible action today. The only workaround in
the tree is to inject an auto-approver, which discards the gate entirely.

This module is the third option, and its entire contract is one sentence:

    **Unattended does not change the autonomy ceiling. It changes the
    destination of the question.**

Concretely, and each of these is asserted by ``tests/test_unattended_flag.py``:

- it does not widen what the agent may do — no tool moves tier, no tier leaves
  ``require_approval_tiers``, ``default_tier`` stays ``unknown``;
- it does not approve anything — the only approvers it can return are
  :func:`tools.agent_runtime.approval_gate.console_approver` and the
  agov-inbox-02 inbox approver, both of which halt the call until a *human*
  answers;
- it does not change the gate's mode — ``enforce`` / ``dry_run`` / ``off`` is
  resolved exactly as before, from :func:`approval_gate.resolve_mode`.

The only thing that moves is where the ask is delivered: to a durable
``approval_items`` row somebody will answer, instead of to a console nobody is
watching. The session then *suspends* on that item (the inbox approver blocks)
rather than being told "no" by a prompt that could not be shown.

If this module ever grows a branch that allows a call because ``unattended`` is
set, the feature has silently become an auto-approver — strictly worse than the
deny-on-EOF behaviour it replaces. :func:`approval_surface` exists to make that
regression a red test rather than a discovery.

## Never inferred from a missing TTY

Enabling unattended is an **explicit human act**: ``icdev chat --unattended``,
``icdev cron create --unattended``, ``ICDEV_UNATTENDED=1`` exported by an
operator, or a UI confirm. It is never derived from ``sys.stdin.isatty()``.

That restraint is the point. "No TTY" is true of a CI runner, a cron tick, a
subprocess with a redirected stdin, a Docker ``exec`` and a pytest run — an
inference that fired on it would silently enrol every one of those in a
different approval path than the one their operator chose, and would do it most
reliably in exactly the automated contexts where nobody is reading the logs.
The word "isatty" does not appear anywhere below, and a test asserts that about
this file's source.

## Durable, because a flag that dies with the process is not a flag

State lives in ``agent_unattended_sessions`` (migration 20260809213046), keyed
by session id, and cron jobs carry their own ``agent_cron_jobs.unattended``
column. An overnight run that restarts re-reads the flag and keeps routing to
the inbox; without persistence it would fall back to a console approver mid-run
and start denying, which reads as "the agent randomly broke".

The table is MUTABLE on purpose — an operator turns the flag on and off — and is
deliberately not in ``APPEND_ONLY_TABLES``. The history of *who flipped it* goes
to the append-only ``hook_events`` trail via :func:`tools.airgap.hook_compat.
store_event`, the same trail ``safety.py`` audits approvals to, so no new
append-only table is introduced.

## Reads fail safe, writes fail loud

A missing table, an unreachable DB, an unreadable row: every read resolves to
"not unattended", which routes to the console approver, which denies on EOF.
Degrading toward the *stricter* path is the only safe direction.

:func:`set_unattended` raises :class:`UnattendedStoreUnavailable` instead,
because a flag the operator asked for and that was not persisted is a session
that will deny everything with no explanation. That has to surface at the CLI,
not two hours later.

CLI::

    python tools/agent_runtime/unattended.py --list --json
    python tools/agent_runtime/unattended.py --show <session_id> --json
    python tools/agent_runtime/unattended.py --set <session_id> --on \\
        --reason "overnight backlog run" --json
    python tools/agent_runtime/unattended.py --set <session_id> --off --json
    python tools/agent_runtime/unattended.py --surface --json
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.approval_gate import (  # noqa: E402
    IRREVERSIBLE,
    TIERS,
    UNKNOWN,
    ApprovalDecision,
    ApprovalRequest,
    Approver,
    classify,
    console_approver,
    load_policy,
    resolve_actor,
    resolve_mode,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.agent_runtime.unattended")

TABLE = "agent_unattended_sessions"

#: Operator-set env var. Read only as a *fallback* behind the stored per-session
#: row, and only because exporting it in a systemd unit or a cron environment is
#: as explicit an act as typing the flag. It is not an inference.
ENV_UNATTENDED = "ICDEV_UNATTENDED"

#: Where the flag came from. Recorded so "why is this session unattended?" has an
#: answer, and so an operator can tell a CLI flag from an inherited cron setting.
SOURCE_CLI = "cli"
SOURCE_CRON = "cron"
SOURCE_API = "api"
SOURCE_ENV = "env"
SOURCE_STORE = "store"
SOURCE_DEFAULT = "default"
SOURCES = (SOURCE_CLI, SOURCE_CRON, SOURCE_API, SOURCE_ENV, SOURCE_STORE, SOURCE_DEFAULT)

DEFAULT_CLASSIFICATION = "CUI"

# Column order is the live schema's order (migration 20260809213046). The INSERT
# names every column explicitly, so this tuple and the migration must agree —
# asserted by tests/test_unattended_flag.py against the migration's own DDL.
COLUMNS = (
    "session_id",
    "unattended",
    "source",
    "actor",
    "reason",
    "inbox",
    "tenant_id",
    "classification",
    "set_at",
    "updated_at",
)

_TRUTHY = ("1", "true", "yes", "on")


class UnattendedStoreUnavailable(RuntimeError):
    """The flag could not be persisted.

    Raised by :func:`set_unattended` rather than swallowed. An operator who
    asked for unattended and did not get it would otherwise watch a session
    refuse every irreversible action with no visible cause. Reads never raise
    this — they degrade to "attended", which is the stricter path.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_flag() -> Optional[bool]:
    """``ICDEV_UNATTENDED`` as a tri-state: True / False / unset.

    Deliberately not ``bool(os.environ.get(...))``: an operator who exported
    ``ICDEV_UNATTENDED=0`` is stating a preference, and it must not read the
    same as never having set it.
    """
    raw = os.environ.get(ENV_UNATTENDED)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower() in _TRUTHY


def current_session_id(explicit: str = "") -> str:
    """Resolve the session id a flag is keyed on.

    Same precedence the rest of ``agent_runtime`` uses, so a flag set against a
    chat context id, a cron job id or ``$ICDEV_SESSION_ID`` all land in one
    place.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return "unknown"


@dataclass(frozen=True)
class UnattendedState:
    """The resolved unattended setting for one session, and where it came from."""

    session_id: str
    unattended: bool
    source: str = SOURCE_DEFAULT
    actor: str = ""
    reason: str = ""
    inbox: str = ""
    tenant_id: str = ""
    classification: str = DEFAULT_CLASSIFICATION
    set_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------
def _connect():
    """Open a connection, or raise :class:`UnattendedStoreUnavailable`.

    ``get_connection`` (never a raw driver) so the RLS predicate and the
    ``%s`` → ``?`` translation both apply — the table carries ``tenant_id`` and
    ``classification`` precisely so it is RLS-eligible.

    Never runs ``CREATE TABLE IF NOT EXISTS``: that statement never ALTERs an
    existing table, so self-creating is how a schema silently drifts away from
    its migration until an INSERT starts failing inside a swallowed exception
    (CLAUDE.md).
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise UnattendedStoreUnavailable(f"storage layer unavailable: {exc}") from exc
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        raise UnattendedStoreUnavailable(f"cannot open a connection: {exc}") from exc
    try:
        if not table_exists(conn, TABLE):
            raise UnattendedStoreUnavailable(
                f"{TABLE} is missing — run `python tools/db/migrate.py --up` "
                "(migration 20260809213046_agov_unattended_sessions)"
            )
    except UnattendedStoreUnavailable:
        _close(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        raise UnattendedStoreUnavailable(f"cannot inspect {TABLE}: {exc}") from exc
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _row_to_state(row: Any) -> UnattendedState:
    values = (
        {c: row.get(c) for c in COLUMNS}
        if isinstance(row, dict)
        else dict(zip(COLUMNS, row))
    )
    return UnattendedState(
        session_id=str(values.get("session_id") or ""),
        unattended=bool(int(values.get("unattended") or 0)),
        source=str(values.get("source") or SOURCE_STORE),
        actor=str(values.get("actor") or ""),
        reason=str(values.get("reason") or ""),
        inbox=str(values.get("inbox") or ""),
        tenant_id=str(values.get("tenant_id") or ""),
        classification=str(values.get("classification") or DEFAULT_CLASSIFICATION),
        set_at=str(values.get("set_at") or ""),
        updated_at=str(values.get("updated_at") or ""),
    )


_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


# ---------------------------------------------------------------------------
# Audit — the append-only trail safety.py already writes to
# ---------------------------------------------------------------------------
def _audit(state: UnattendedState, previous: Optional[bool]) -> None:
    """Append the flip to ``hook_events``. Never raises.

    The state table is mutable, so it holds only the current answer. Who turned
    unattended on, when, and why is evidence, and evidence belongs in the
    append-only trail — the same one ``safety.py`` audits every approval to, so
    no new append-only table (and no ``APPEND_ONLY_TABLES`` entry) is needed.
    """
    try:
        from tools.airgap.hook_compat import store_event

        store_event(
            state.session_id,
            "agent_unattended",
            "unattended",
            {
                "session_id": state.session_id,
                "unattended": state.unattended,
                "previous": previous,
                "source": state.source,
                "actor": state.actor,
                "reason": state.reason,
                "set_at": state.set_at,
                # Stated on every row so a reader of the trail alone cannot
                # mistake this for an autonomy change.
                "note": "routing only — the autonomy ceiling is unchanged",
            },
        )
    except Exception as exc:  # noqa: BLE001 — auditing must never break the flag
        logger.debug("unattended: audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def set_unattended(
    session_id: str,
    enabled: bool,
    *,
    actor: str = "",
    reason: str = "",
    source: str = SOURCE_CLI,
    inbox: str = "",
    tenant_id: str = "",
    classification: str = DEFAULT_CLASSIFICATION,
) -> UnattendedState:
    """Record an EXPLICIT decision to route this session's asks to the inbox.

    Args:
        session_id: Chat context id, cron job id, or ``$ICDEV_SESSION_ID``.
        enabled: The operator's answer. ``False`` is stored, not deleted, so
            "explicitly attended" is distinguishable from "never asked".
        source: Which explicit act set it — :data:`SOURCE_CLI`,
            :data:`SOURCE_CRON`, :data:`SOURCE_API`. An unrecognised source is
            refused rather than recorded, because the provenance of this flag is
            the thing that makes it auditable.

    Raises:
        UnattendedStoreUnavailable: if the flag could not be persisted.
    """
    sid = current_session_id(session_id)
    if sid == "unknown":
        raise ValueError("a session id is required to set the unattended flag")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")

    previous_state = get_unattended(sid)
    previous = previous_state.unattended if previous_state is not None else None
    now = _now()
    state = UnattendedState(
        session_id=sid,
        unattended=bool(enabled),
        source=source,
        actor=resolve_actor(actor or None),
        reason=reason,
        inbox=inbox,
        tenant_id=tenant_id,
        classification=classification or DEFAULT_CLASSIFICATION,
        set_at=(previous_state.set_at if previous_state is not None else now) or now,
        updated_at=now,
    )

    conn = _connect()
    try:
        # UPDATE-then-INSERT rather than a dialect-specific UPSERT: ON CONFLICT
        # and ON DUPLICATE KEY differ between PG and SQLite, and this table has
        # exactly one writer path, so the two-statement form is the portable one.
        cur = conn.execute(
            f"UPDATE {TABLE} SET unattended = %s, source = %s, actor = %s, "
            f"reason = %s, inbox = %s, tenant_id = %s, classification = %s, "
            f"updated_at = %s WHERE session_id = %s",
            (
                1 if state.unattended else 0,
                state.source,
                state.actor,
                state.reason,
                state.inbox,
                state.tenant_id,
                state.classification,
                state.updated_at,
                state.session_id,
            ),
        )
        if (getattr(cur, "rowcount", 0) or 0) < 1:
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            conn.execute(
                f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                (
                    state.session_id,
                    1 if state.unattended else 0,
                    state.source,
                    state.actor,
                    state.reason,
                    state.inbox,
                    state.tenant_id,
                    state.classification,
                    state.set_at,
                    state.updated_at,
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
        raise UnattendedStoreUnavailable(
            f"could not persist the unattended flag for {sid}: {exc}"
        ) from exc
    finally:
        _close(conn)

    _audit(state, previous)
    logger.info(
        "unattended: session %s set unattended=%s by %s (source=%s) — routing only, "
        "the autonomy ceiling is unchanged",
        sid, state.unattended, state.actor, state.source,
    )
    return state


def clear_unattended(session_id: str) -> bool:
    """Forget the stored row entirely. ``True`` if a row was removed.

    Distinct from ``set_unattended(sid, False)``, which records an explicit
    "attended". Clearing means the session falls back to the env/default
    resolution — useful when a context id is retired.
    """
    sid = current_session_id(session_id)
    try:
        conn = _connect()
    except UnattendedStoreUnavailable as exc:
        logger.debug("unattended: clear(%s) unavailable: %s", sid, exc)
        return False
    try:
        cur = conn.execute(f"DELETE FROM {TABLE} WHERE session_id = %s", (sid,))
        removed = (getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("unattended: clear(%s) failed: %s", sid, exc)
        return False
    finally:
        _close(conn)
    return removed


# ---------------------------------------------------------------------------
# Reads — degrade to "attended", never raise
# ---------------------------------------------------------------------------
def get_unattended(session_id: str) -> Optional[UnattendedState]:
    """The stored state for one session, or ``None`` when nothing is stored."""
    sid = current_session_id(session_id)
    try:
        conn = _connect()
    except UnattendedStoreUnavailable as exc:
        logger.debug("unattended: get(%s) unavailable: %s", sid, exc)
        return None
    try:
        row = conn.execute(f"{_SELECT} WHERE session_id = %s", (sid,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("unattended: get(%s) failed: %s", sid, exc)
        return None
    finally:
        _close(conn)
    return _row_to_state(row) if row is not None else None


def list_unattended(*, only_enabled: bool = True, limit: int = 200) -> list[UnattendedState]:
    """Stored flags, newest first. Empty on any failure."""
    where = " WHERE unattended = 1" if only_enabled else ""
    # LIMIT is interpolated as an int rather than bound: the RLS layer prepends
    # its own predicate params to a SELECT, and a trailing bound LIMIT is the one
    # slot that reordering would silently shift. int() makes it non-injectable.
    sql = f"{_SELECT}{where} ORDER BY updated_at DESC LIMIT {int(limit)}"
    try:
        conn = _connect()
    except UnattendedStoreUnavailable as exc:
        logger.debug("unattended: list unavailable: %s", exc)
        return []
    try:
        rows = conn.execute(sql, ()).fetchall() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("unattended: list failed: %s", exc)
        return []
    finally:
        _close(conn)
    return [_row_to_state(r) for r in rows]


def resolve(session_id: str = "", *, unattended: Optional[bool] = None) -> UnattendedState:
    """Resolve the effective state: explicit arg → stored row → env → attended.

    ``unattended`` is the caller's explicit act this run (``--unattended`` on the
    command line, the job's own column). It wins because it is the most recent
    human statement, and it is what :func:`set_unattended` will have persisted.

    There is no TTY branch here and there never will be — see the module
    docstring. The last resort is ``False``, which routes to the console
    approver, which denies on EOF: the strict path.
    """
    sid = current_session_id(session_id)
    if unattended is not None:
        return UnattendedState(
            session_id=sid,
            unattended=bool(unattended),
            source=SOURCE_CLI,
            actor=resolve_actor(),
        )
    stored = get_unattended(sid)
    if stored is not None:
        return stored
    env = _env_flag()
    if env is not None:
        return UnattendedState(
            session_id=sid, unattended=env, source=SOURCE_ENV, actor=resolve_actor()
        )
    return UnattendedState(session_id=sid, unattended=False, source=SOURCE_DEFAULT)


def is_unattended(session_id: str = "", *, unattended: Optional[bool] = None) -> bool:
    """Convenience wrapper over :func:`resolve`."""
    return resolve(session_id, unattended=unattended).unattended


# ---------------------------------------------------------------------------
# Routing — the ONLY thing this flag changes
# ---------------------------------------------------------------------------
def approver_for(
    session_id: str = "",
    *,
    unattended: Optional[bool] = None,
    inbox: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    poll_seconds: Optional[float] = None,
    deliver: Optional[Callable[[Any], Any]] = None,
) -> Approver:
    """Pick the :data:`Approver` for this session. **Delivery, not authority.**

    Returns exactly one of two things, and both of them halt the call until a
    human answers:

    - attended → :func:`approval_gate.console_approver` (denies on EOF);
    - unattended → the agov-inbox-02 inbox approver, which enqueues a *pending*
      ``approval_items`` row and blocks until it is resolved, expired or
      cancelled — and treats an expiry as a denial.

    There is deliberately no third branch. Nothing here can return an approver
    that allows on its own; adding one is what would turn this flag into the
    auto-approver the whole epic exists to avoid.

    If the inbox approver cannot be imported, the console approver is returned —
    degrading toward deny-on-EOF, never toward allow.
    """
    state = resolve(session_id, unattended=unattended)
    if not state.unattended:
        return console_approver

    try:
        from tools.agent_runtime.inbox_approver import make_inbox_approver
    except Exception as exc:  # noqa: BLE001 — degrade to the STRICTER approver
        logger.error(
            "unattended: inbox approver unavailable (%s); falling back to the "
            "console approver, which denies on EOF", exc,
        )
        return console_approver

    return make_inbox_approver(
        inbox=inbox if inbox is not None else (state.inbox or None),
        session_id=state.session_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        deliver=deliver,
        classification=state.classification,
    )


def safety_approver_for(
    session_id: str = "",
    *,
    unattended: Optional[bool] = None,
    **kwargs: Any,
) -> Callable[[Any], bool]:
    """The same routing, adapted to the ``safety.py`` SafetyGate approver shape.

    ``safety.Approver`` takes a ``safety.ApprovalRequest`` (tool, input, risk,
    detail) and returns a bool, while ``approval_gate.Approver`` takes a
    classification and may return an :class:`ApprovalDecision`. The SAG toolset
    path (``toolsets.build_toolset`` → ``safety.build_safety_gate``) uses the
    former, so unattended routing needs this adapter to reach it.

    The adapter classifies with the SAME :func:`approval_gate.classify` the
    gate would use, so the tier the inbox item records is the tier the policy
    assigns — the bridge translates a shape, never a verdict.
    """
    approve = approver_for(session_id, unattended=unattended, **kwargs)

    def _approve(request: Any) -> bool:
        tool_name = getattr(request, "tool_name", "")
        tool_input = getattr(request, "tool_input", {}) or {}
        gate_request = ApprovalRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            classification=classify(tool_name, tool_input),
            actor=resolve_actor(),
        )
        result = approve(gate_request)
        if isinstance(result, ApprovalDecision):
            return bool(result.approved)
        return bool(result)

    return _approve


# ---------------------------------------------------------------------------
# The invariant, made checkable
# ---------------------------------------------------------------------------
#: Tool names probed by :func:`approval_surface` on top of every tool the policy
#: enumerates. One per interesting shape: an enumerated irreversible verb, a
#: generic executor carrying an irreversible string, a generic executor carrying
#: a recoverable one, an enumerated read, and a name nobody has ever enumerated
#: (which must stay `unknown`, and so must still require approval).
_PROBES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("git_push", {"remote": "origin", "branch": "main"}),
    ("run_command", {"command": "git push --force origin main"}),
    ("run_command", {"command": "git add ."}),
    ("read_file", {"path": "README.md"}),
    ("a_tool_nobody_enumerated", {"anything": "at all"}),
)


def approval_surface(
    session_id: str = "", *, unattended: Optional[bool] = None
) -> dict[str, Any]:
    """A canonical, comparable description of WHAT currently requires approval.

    This is the executable form of the invariant. Serialise it with
    ``json.dumps(..., sort_keys=True)`` under ``unattended=True`` and under
    ``unattended=False`` and the two strings must be byte-identical: the flag
    changes the delivery channel, so a difference here means it has started
    changing the ceiling.

    It deliberately spans all three ways that could regress:

    - **policy** — ``default_tier`` and ``require_approval_tiers``;
    - **classification** — the tier and ``requires_approval`` of every
      enumerated tool plus :data:`_PROBES`, so a per-tool downgrade shows up;
    - **mode** — the resolved ``enforce`` / ``dry_run`` / ``off``, because
      "unattended implies mode=off" is the most tempting wrong shortcut and
      would not show up in the policy at all.

    The resolved state is reported alongside under ``"_state"``, which callers
    comparing surfaces must exclude — it is the input being varied, not part of
    the surface. :func:`surface_digest` does that for you.
    """
    state = resolve(session_id, unattended=unattended)
    policy = load_policy()

    names: set[str] = set()
    for tier in TIERS:
        for name in (policy.get("tools") or {}).get(tier) or []:
            names.add(str(name))

    tools: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        cls = classify(name, {}, policy=policy)
        tools[name] = {"tier": cls.tier, "requires_approval": cls.requires_approval}

    probes: dict[str, dict[str, Any]] = {}
    for name, tool_input in _PROBES:
        cls = classify(name, tool_input, policy=policy)
        key = f"{name}:{json.dumps(tool_input, sort_keys=True)}"
        probes[key] = {
            "tier": cls.tier,
            "rule": cls.rule,
            "requires_approval": cls.requires_approval,
        }

    return {
        "default_tier": str(policy.get("default_tier") or UNKNOWN),
        "require_approval_tiers": sorted(
            str(t) for t in (policy.get("require_approval_tiers") or [IRREVERSIBLE, UNKNOWN])
        ),
        "gate_mode": resolve_mode(),
        "tools": tools,
        "probes": probes,
        "_state": {
            "session_id": state.session_id,
            "unattended": state.unattended,
            "source": state.source,
        },
    }


def surface_digest(session_id: str = "", *, unattended: Optional[bool] = None) -> str:
    """:func:`approval_surface` minus ``_state``, as sorted JSON.

    The string two callers compare. ``_state`` is dropped because it is the
    variable being changed, not part of what the surface asserts.
    """
    surface = approval_surface(session_id, unattended=unattended)
    surface.pop("_state", None)
    return json.dumps(surface, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Inspect and set the per-session unattended flag (agov-inbox-04). "
            "Routing only: it changes where an approval ask is delivered, never "
            "what the agent is allowed to do."
        )
    )
    parser.add_argument("--list", action="store_true", help="list stored flags")
    parser.add_argument("--all", action="store_true", help="with --list: include attended")
    parser.add_argument("--show", metavar="SESSION_ID", help="resolve one session")
    parser.add_argument("--set", metavar="SESSION_ID", help="set one session's flag")
    parser.add_argument("--on", action="store_true", help="with --set: enable")
    parser.add_argument("--off", action="store_true", help="with --set: disable")
    parser.add_argument("--clear", metavar="SESSION_ID", help="forget a stored flag")
    parser.add_argument("--reason", default="", help="with --set: recorded reason")
    parser.add_argument("--actor", default="", help="with --set: who is deciding")
    parser.add_argument("--inbox", default="", help="with --set: destination inbox")
    parser.add_argument(
        "--surface",
        action="store_true",
        help="print what currently requires approval (the invariance probe)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str, sort_keys=True))
        else:
            print(payload)

    if args.surface:
        emit(approval_surface(args.show or ""))
        return 0

    if args.set:
        if args.on == args.off:
            # Neither or both. Enabling unattended is an explicit act, so the
            # command that does it may not be ambiguous.
            parser.error("--set requires exactly one of --on / --off")
        try:
            state = set_unattended(
                args.set,
                bool(args.on),
                actor=args.actor,
                reason=args.reason,
                inbox=args.inbox,
                source=SOURCE_CLI,
            )
        except (UnattendedStoreUnavailable, ValueError) as exc:
            print(f"unattended: {exc}", file=sys.stderr)
            return 1
        emit(state.to_dict())
        return 0

    if args.clear:
        removed = clear_unattended(args.clear)
        emit({"session_id": args.clear, "removed": removed})
        return 0 if removed else 1

    if args.show:
        emit(resolve(args.show).to_dict())
        return 0

    if args.list:
        states = list_unattended(only_enabled=not args.all)
        if args.json:
            emit([s.to_dict() for s in states])
        else:
            for s in states:
                print(
                    f"{s.session_id:<28} unattended={str(s.unattended):<5} "
                    f"source={s.source:<8} {s.reason}"
                )
            print(f"({len(states)} session(s))")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
