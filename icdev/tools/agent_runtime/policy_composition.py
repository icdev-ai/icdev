# CUI // SP-CTI
"""Three-level policy composition and session state (exa-policy-02).

:mod:`tools.agent_runtime.policy_engine` (exa-policy-01) made a policy a
function returning ALLOW / ASK / DENY plus a reason, and gave it a chain. That
chain is **process-wide** — its own config says so, in prose, next to the rules
it bounds. One chain cannot express the thing omnigent gets right: that three
different people are entitled to an opinion about one tool call, and that they
are not equally trusted.

    session   set by the END USER, for this session only
    agent     set by the AGENT AUTHOR, for this agent or profile
    server    set by the ADMIN, as the org baseline

Evaluated **session first, then agent, then server**. A DENY at any level
short-circuits the whole composition — later levels are not consulted, because
no later opinion makes a refusal not a refusal.

## Why session-first is the safe order, not the dangerous one

The reflex is that letting an end user's rules run first is how you get talked
out of a control. It is the opposite, and the reason is structural rather than a
matter of care:

**Levels are ADDITIVE. They do not override.** Each level contributes its own
chain, and the answer is the strictest opinion any level returned. So the only
thing a session-level policy can contribute is a *stricter* answer. There is no
syntax at the session level for removing a policy from the agent or server
chain, for lowering a floor, or for making a DENY into an ALLOW — not because
those are rejected by a check that could be forgotten, but because the
composition never reads the session level as an override of anything. A session
ALLOW is indistinguishable from a session abstention.

That is what makes running it first *safe*, and running it first is what makes
it *useful*: a user who wants to forbid something for the next hour gets the
cheapest possible evaluation and an immediate DENY, without the org baseline
having to be consulted to confirm a refusal.

Three narrower things are checked rather than structural, and each is
**reported** rather than silently dropped — see :attr:`Composition.relaxations`:

* ``on_policy_error: allow`` at any level (rejected everywhere, including
  server: a broken policy is an unanswered question);
* an ``audit:`` block below the server level (a user does not get to stop their
  own denials being logged);
* a floor lower than a stricter level's floor (ignored by ``strictest``, but
  worth saying out loud, because someone wrote it expecting it to work).

A session config also cannot introduce *code*: there is no path from YAML or a
dict to a callable. It can only name policies the process registry already
holds, and :func:`policy_engine.register_policy` refuses to shadow a name
without ``replace=True``. And the level ORDER is a module constant
(:data:`LEVELS`), not a config key.

## Session state

A stateful policy needs somewhere to count. A policy returns
``state_updates`` — omnigent's mechanism, ``{"key": "call_count", "action":
"increment", "value": 1}`` — and :class:`SessionState` applies them, keyed by
``session_id``, which ICDEV already threads through everything.

The updates are applied **as each policy returns**, before the next policy in
the chain runs, so a later policy sees the value an earlier one wrote. A policy
that was never reached (because something before it denied) never writes. An
unknown action raises, which the chain resolves to ``on_policy_error`` — a
counter that silently fails to increment is a limit that silently never fires,
which is the exact failure this card exists to stop.

State is persisted to ``agent_session_policy_state`` (migration
20260812054330) because ICDEV resumes sessions across process restarts on
purpose; an in-memory-only counter is bypassed by restarting and resuming.

Config: the server level is ``args/agent_policy_chain.yaml``, unchanged and
still the only file an admin edits. The agent level is that file's ``agent:``
block, or ``<profile_dir>/policy_chain.yaml`` for the active SAG profile, or
``$ICDEV_AGENT_POLICY_CHAIN_AGENT``. The session level is passed in
programmatically, or ``$ICDEV_AGENT_POLICY_CHAIN_SESSION``.

CLI::

    python tools/agent_runtime/policy_composition.py --levels --json
    python tools/agent_runtime/policy_composition.py --evaluate git_push --json
    python tools/agent_runtime/policy_composition.py --evaluate git_push \\
        --session-policy '{"chain": [{"name": "reversibility"}]}' --json
    python tools/agent_runtime/policy_composition.py --state sess-1 --json
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. parents[2] is whatever holds this file's `tools` package: the
# repo root in tools/, and <repo>/icdev in the icdev/ mirror.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime import approval_gate as gate
from tools.agent_runtime import policy_engine as pe
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.policy_composition")

# ---------------------------------------------------------------------------
# Levels — least trusted first. This ORDER IS A CONSTANT, not a config key: a
# config that could reorder the levels could put the session level last, and a
# session level evaluated last against an override-capable composer would be a
# session level that can loosen. Composition is additive so the order does not
# affect the ANSWER, only which level gets to short-circuit first; keeping it
# out of config keeps it out of reach.
# ---------------------------------------------------------------------------
LEVEL_SESSION = "session"
LEVEL_AGENT = "agent"
LEVEL_SERVER = "server"
LEVELS: tuple[str, ...] = (LEVEL_SESSION, LEVEL_AGENT, LEVEL_SERVER)

# Only the server level owns these keys. Present at a lower level, they are
# ignored and reported.
SERVER_ONLY_KEYS: tuple[str, ...] = ("audit",)

SESSION_CONFIG_ENV = "ICDEV_AGENT_POLICY_CHAIN_SESSION"
AGENT_CONFIG_ENV = "ICDEV_AGENT_POLICY_CHAIN_AGENT"
PROFILE_CONFIG_FILENAME = "policy_chain.yaml"

STATE_TABLE = "agent_session_policy_state"
STATE_MIGRATION = "20260812054330_agent_session_policy_state"

# --- state_update actions --------------------------------------------------
# The vocabulary lives HERE, once. policy_engine validates only the structure of
# a state update (a key and an action) precisely so this list is not duplicated
# there, where it would drift.
ACTION_INCREMENT = "increment"
ACTION_DECREMENT = "decrement"
ACTION_SET = "set"
ACTION_APPEND = "append"
ACTION_DELETE = "delete"
STATE_ACTIONS: tuple[str, ...] = (
    ACTION_INCREMENT,
    ACTION_DECREMENT,
    ACTION_SET,
    ACTION_APPEND,
    ACTION_DELETE,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
class SessionState:
    """Mutable, session-scoped state that stateful policies count against.

    Behaves as a ``dict`` for readers (policies receive :meth:`snapshot` through
    ``PolicyEvent.session_state``) and is written only through
    :meth:`apply_updates`, so every mutation goes through one validated path.

    Persistence is **best effort and never fail-open in the direction that
    matters**: if the table is missing, the in-process values are still the
    authority for this process, and a WARNING names the migration. What is lost
    is survival across a restart — which is why the warning names the migration
    instead of being a debug line.
    """

    def __init__(
        self,
        session_id: str,
        values: Optional[dict[str, Any]] = None,
        *,
        persist: bool = True,
    ) -> None:
        self.session_id = str(session_id or "")
        self._values: dict[str, Any] = dict(values or {})
        self.persist = bool(persist) and bool(self.session_id)
        self.dirty: set[str] = set()

    # --- reads -------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """A copy. Policies must not mutate state by writing to the event."""
        return dict(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(str(key), default)

    def __contains__(self, key: object) -> bool:
        return str(key) in self._values

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"SessionState(session_id={self.session_id!r}, "
            f"keys={sorted(self._values)!r})"
        )

    # --- writes ------------------------------------------------------------
    def apply_updates(self, updates: Sequence[dict[str, Any]]) -> list[str]:
        """Apply ``state_updates`` in order and return the keys that changed.

        Raises :class:`ValueError` on an unknown action or a type mismatch. The
        caller is :func:`_leveled`, running inside the chain, so the raise
        becomes ``on_policy_error`` — DENY by default. A policy that cannot
        express its own bookkeeping does not get to authorise anything.
        """
        changed: list[str] = []
        for update in updates or ():
            key = str(update.get("key") or "").strip()
            action = str(update.get("action") or "").strip().lower()
            value = update.get("value")
            if not key:
                raise ValueError("a state update needs a non-empty 'key'")
            if action not in STATE_ACTIONS:
                raise ValueError(
                    f"unknown state action {action!r} for {key!r}; "
                    f"known actions are {', '.join(STATE_ACTIONS)}"
                )
            if action == ACTION_DELETE:
                self._values.pop(key, None)
            elif action == ACTION_SET:
                self._values[key] = value
            elif action in (ACTION_INCREMENT, ACTION_DECREMENT):
                # value defaults to 1 so `{"key": "calls", "action":
                # "increment"}` — the common case — needs no value at all.
                step = 1 if value is None else value
                if isinstance(step, bool) or not isinstance(step, (int, float)):
                    raise ValueError(
                        f"{action} of {key!r} needs a numeric value, got {step!r}"
                    )
                current = self._values.get(key, 0)
                if isinstance(current, bool) or not isinstance(current, (int, float)):
                    raise ValueError(
                        f"cannot {action} {key!r}: it currently holds "
                        f"{type(current).__name__}, not a number"
                    )
                self._values[key] = (
                    current + step if action == ACTION_INCREMENT else current - step
                )
            elif action == ACTION_APPEND:
                current = self._values.get(key)
                if current is None:
                    current = []
                if not isinstance(current, list):
                    raise ValueError(
                        f"cannot append to {key!r}: it currently holds "
                        f"{type(current).__name__}, not a list"
                    )
                self._values[key] = [*current, value]
            changed.append(key)
            self.dirty.add(key)
        if changed and self.persist:
            self.flush()
        return changed

    def reset(self) -> None:
        """Drop every value for this session, in memory and in the table."""
        keys = list(self._values)
        self._values.clear()
        self.dirty.clear()
        if keys and self.persist:
            _delete_state(self.session_id)

    # --- persistence -------------------------------------------------------
    def flush(self) -> bool:
        """UPSERT the dirty keys. Returns True when the write landed."""
        if not self.dirty or not self.session_id:
            return False
        payload = {k: self._values.get(k) for k in self.dirty}
        deletions = [k for k in self.dirty if k not in self._values]
        ok = _write_state(self.session_id, payload, deletions)
        if ok:
            self.dirty.clear()
        return ok


_STATES: dict[str, SessionState] = {}


def get_session_state(
    session_id: str, *, refresh: bool = False, persist: bool = True
) -> SessionState:
    """The one :class:`SessionState` for ``session_id`` in this process.

    A per-process registry rather than a fresh object per call: two hooks built
    for the same session — the runtime rebuilds one per turn — must count against
    the same numbers, or ``max_tool_calls_per_session`` resets every turn.

    An empty ``session_id`` gets a throwaway, unpersisted state. Stateful
    policies then see an always-empty dict, which is the honest answer: without a
    session id there is no session to scope state to, and inventing one would
    make a per-session limit silently per-call.
    """
    key = str(session_id or "")
    if not key:
        return SessionState("", persist=False)
    existing = _STATES.get(key)
    if existing is not None and not refresh:
        return existing
    state = SessionState(key, _read_state(key) if persist else {}, persist=persist)
    _STATES[key] = state
    return state


def forget_session_state(session_id: str) -> None:
    """Drop the in-process cache entry (the table is untouched)."""
    _STATES.pop(str(session_id or ""), None)


_STATE_TABLE_WARNED = False


def _state_table_ready(conn) -> bool:
    global _STATE_TABLE_WARNED
    from tools.db.storage import table_exists

    if table_exists(conn, STATE_TABLE):
        return True
    if not _STATE_TABLE_WARNED:
        _STATE_TABLE_WARNED = True
        logger.warning(
            "policy_composition: %s is missing, so session policy state lives "
            "only in this process and a restart resets every counter. Run "
            "migration %s.", STATE_TABLE, STATE_MIGRATION,
        )
    return False


def _read_state(session_id: str) -> dict[str, Any]:
    """Load persisted state for a session. Never raises."""
    from tools.db.storage import get_connection

    conn = None
    try:
        conn = get_connection()
        if not _state_table_ready(conn):
            return {}
        cur = conn.execute(
            f"SELECT state_key, state_value FROM {STATE_TABLE} WHERE session_id = %s",
            (session_id,),
        )
        values: dict[str, Any] = {}
        for row in cur.fetchall() or ():
            key, raw = (row[0], row[1]) if not isinstance(row, dict) else (
                row["state_key"], row["state_value"]
            )
            try:
                values[str(key)] = json.loads(raw) if raw is not None else None
            except (TypeError, ValueError):
                # A value this module did not write. Skip the key rather than
                # guess: a policy reading a half-parsed counter is worse than a
                # policy reading a missing one, which it already handles.
                logger.warning(
                    "policy_composition: %s.%s is not JSON; ignoring the key",
                    session_id, key,
                )
        return values
    except Exception as exc:  # noqa: BLE001 — in-process state still works
        logger.warning("policy_composition: could not read session state: %s", exc)
        return {}
    finally:
        # CLAUDE.md / swallowed-write pooling: an un-closed connection leaks a
        # pooled PG connection on every failure, so close in `finally`.
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _write_state(
    session_id: str, values: dict[str, Any], deletions: Sequence[str] = ()
) -> bool:
    """UPSERT the given keys. Returns False when the write did not land."""
    from tools.db.storage import get_connection

    conn = None
    try:
        conn = get_connection()
        if not _state_table_ready(conn):
            return False
        now = _utcnow()
        for key in deletions:
            conn.execute(
                f"DELETE FROM {STATE_TABLE} WHERE session_id = %s AND state_key = %s",
                (session_id, key),
            )
        for key, value in values.items():
            if key in deletions:
                continue
            # ON CONFLICT ... DO UPDATE is spelled identically in PostgreSQL and
            # in SQLite >= 3.24, and the composite PK is what it conflicts on.
            conn.execute(
                f"INSERT INTO {STATE_TABLE} "
                "(session_id, state_key, state_value, updated_at, classification) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (session_id, state_key) DO UPDATE "
                "SET state_value = EXCLUDED.state_value, "
                "    updated_at = EXCLUDED.updated_at",
                (session_id, key, json.dumps(value), now, "CUI"),
            )
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — in-process state still works
        logger.warning("policy_composition: could not persist session state: %s", exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _delete_state(session_id: str) -> bool:
    from tools.db.storage import get_connection

    conn = None
    try:
        conn = get_connection()
        if not _state_table_ready(conn):
            return False
        conn.execute(
            f"DELETE FROM {STATE_TABLE} WHERE session_id = %s", (session_id,)
        )
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("policy_composition: could not clear session state: %s", exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Relaxation:
    """Something a level wrote that would have loosened a stricter level.

    Recorded rather than dropped. A key that is ignored in silence is a key
    somebody keeps writing.
    """

    level: str
    key: str
    attempted: str
    effective: str
    reason: str

    def summary(self) -> str:
        return (
            f"{self.level} level tried {self.key}={self.attempted!r}; "
            f"{self.effective!r} stands ({self.reason})"
        )


@dataclass(frozen=True)
class Level:
    """One level's resolved chain and its own view of the shared settings.

    ``on_policy_error`` is ``""`` when this level did not state one — "no
    opinion", which is distinct from ``deny``. Defaulting a silent level to
    ``deny`` would mean an empty session level silently overrode a server admin
    who chose ``ask``, i.e. a level that says nothing changing the answer.
    """

    level: str
    chain: tuple[tuple[str, pe.PolicyFunction], ...] = ()
    floors: dict[str, str] = field(default_factory=dict)
    on_policy_error: str = ""
    source: str = ""
    disabled: tuple[str, ...] = ()

    @property
    def policy_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.chain)


def _load_yaml(path: Path) -> Optional[dict[str, Any]]:
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        return loaded if isinstance(loaded, dict) else None
    except Exception as exc:  # noqa: BLE001 — an unreadable level is an empty one
        logger.warning("policy_composition: %s unreadable (%s)", path, exc)
        return None


def _env_config(var: str) -> Optional[dict[str, Any]]:
    raw = os.environ.get(var)
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError as exc:
            logger.warning("policy_composition: %s is not valid JSON (%s)", var, exc)
            return None
    path = Path(raw)
    return _load_yaml(path) if path.exists() else None


def _profile_config() -> Optional[dict[str, Any]]:
    """``<profile_dir>/policy_chain.yaml`` for the active SAG profile, if any.

    This is the agent AUTHOR's file: a profile is how ICDEV already scopes an
    agent's skills and env overlay, so it is where an agent-scoped policy
    belongs. Absent (the common case for the default profile) the agent level
    falls back to the in-repo ``agent:`` block.
    """
    try:
        from tools.agent_runtime import profiles

        path = profiles.profile_dir(profiles.active_profile()) / PROFILE_CONFIG_FILENAME
    except Exception as exc:  # noqa: BLE001 — no profile is not an error
        logger.debug("policy_composition: no profile policy file: %s", exc)
        return None
    return _load_yaml(path) if path.exists() else None


def resolve_level_configs(
    *,
    session: Optional[dict[str, Any]] = None,
    agent: Optional[dict[str, Any]] = None,
    server: Optional[dict[str, Any]] = None,
) -> dict[str, tuple[dict[str, Any], str]]:
    """The raw config plus a provenance string for each of the three levels.

    Anything passed explicitly wins — that is how the runtime hands in the
    session policy a user set this turn, and how the tests pin a level.
    """
    server_cfg = server if server is not None else pe.load_config()
    server_src = "explicit" if server is not None else str(
        pe._find_config_path() or "fallback"
    )

    if agent is not None:
        agent_cfg, agent_src = agent, "explicit"
    else:
        env_agent = _env_config(AGENT_CONFIG_ENV)
        if env_agent is not None:
            agent_cfg, agent_src = env_agent, f"${AGENT_CONFIG_ENV}"
        else:
            profile_cfg = _profile_config()
            if profile_cfg is not None:
                agent_cfg, agent_src = profile_cfg, f"profile/{PROFILE_CONFIG_FILENAME}"
            else:
                block = server_cfg.get(LEVEL_AGENT)
                agent_cfg = block if isinstance(block, dict) else {}
                agent_src = f"{pe.CONFIG_FILENAME}:agent" if agent_cfg else "empty"

    if session is not None:
        session_cfg, session_src = session, "explicit"
    else:
        env_session = _env_config(SESSION_CONFIG_ENV)
        if env_session is not None:
            session_cfg, session_src = env_session, f"${SESSION_CONFIG_ENV}"
        else:
            session_cfg, session_src = {}, "empty"

    return {
        LEVEL_SESSION: (session_cfg or {}, session_src),
        LEVEL_AGENT: (agent_cfg or {}, agent_src),
        LEVEL_SERVER: (server_cfg or {}, server_src),
    }


def _resolve_on_policy_error(
    level: str, raw_config: dict[str, Any], relaxations: list[Relaxation]
) -> str:
    """``deny`` unless the level said ``ask``. ``allow`` is refused everywhere.

    Refused at the SERVER level too. ``on_policy_error`` answers "what does a
    broken policy mean", and the answer cannot be "yes" at any level of trust —
    a config typo must never be the thing that authorises an irreversible action.
    """
    if "on_policy_error" not in raw_config:
        return ""  # no opinion — see the Level docstring
    raw = str(raw_config.get("on_policy_error") or "").strip().lower()
    if raw in (pe.DENY, pe.ASK):
        return raw
    relaxations.append(
        Relaxation(
            level=level,
            key="on_policy_error",
            attempted=raw,
            effective=pe.DENY,
            reason="only 'deny' or 'ask' is accepted; a broken policy is not a yes",
        )
    )
    return pe.DENY


def build_level(
    level: str,
    raw_config: dict[str, Any],
    *,
    source: str = "",
    relaxations: Optional[list[Relaxation]] = None,
) -> Level:
    """Resolve one level's chain and settings from its raw config.

    Reuses :func:`policy_engine.resolve_chain`, so a name this level lists that
    the registry does not know resolves to a DENY that names itself here too —
    per level. A level whose config quietly drops a policy is a level that stops
    enforcing what its own config says.
    """
    relaxations = relaxations if relaxations is not None else []
    if level != LEVEL_SERVER:
        for key in SERVER_ONLY_KEYS:
            if key in raw_config:
                relaxations.append(
                    Relaxation(
                        level=level,
                        key=key,
                        attempted="present",
                        effective="ignored",
                        reason=f"{key!r} is set by the admin at the server level only",
                    )
                )
    floors_raw = raw_config.get("floors") or {}
    floors = {
        str(k): str(v).strip().lower()
        for k, v in (floors_raw.items() if isinstance(floors_raw, dict) else ())
    }
    # Names this level switched off. resolve_chain drops them, which is correct
    # for THIS level; _check_shadowed uses them to notice someone expecting the
    # switch to reach a level they do not own.
    disabled = tuple(
        str(entry.get("name") or "").strip().lower()
        for entry in (raw_config.get("chain") or [])
        if isinstance(entry, dict)
        and entry.get("name")
        and not bool(entry.get("enabled", True))
    )
    return Level(
        level=level,
        chain=tuple(pe.resolve_chain(raw_config)),
        floors=floors,
        on_policy_error=_resolve_on_policy_error(level, raw_config, relaxations),
        source=source,
        disabled=disabled,
    )


def load_levels(
    *,
    session: Optional[dict[str, Any]] = None,
    agent: Optional[dict[str, Any]] = None,
    server: Optional[dict[str, Any]] = None,
) -> tuple[tuple[Level, ...], list[Relaxation]]:
    """The three levels in evaluation order, plus every relaxation refused."""
    raw = resolve_level_configs(session=session, agent=agent, server=server)
    relaxations: list[Relaxation] = []
    levels = tuple(
        build_level(name, raw[name][0], source=raw[name][1], relaxations=relaxations)
        for name in LEVELS
    )
    _check_shadowed(levels, relaxations)
    _check_floors(levels, relaxations)
    return levels, relaxations


def _check_shadowed(levels: Sequence[Level], relaxations: list[Relaxation]) -> None:
    """Report a lower level disabling a policy a stricter level enables.

    ``enabled: false`` in a session chain is legitimate — a user dropping their
    own policy. It is worth reporting only when the same policy is enabled at a
    more trusted level, because that is someone expecting an override, and
    composition has no overrides: the stricter level still runs it.
    """
    enabled_above: dict[str, str] = {}
    for level in reversed(list(levels)):  # server first
        for name in level.policy_names:
            enabled_above.setdefault(name, level.level)
    for index, level in enumerate(levels):
        stricter = {
            name
            for later in list(levels)[index + 1:]
            for name in later.policy_names
        }
        for entry in level.disabled:
            if entry in stricter:
                relaxations.append(
                    Relaxation(
                        level=level.level,
                        key=f"chain.{entry}.enabled",
                        attempted="false",
                        effective="still evaluated",
                        reason=(
                            f"{entry!r} is enabled at the "
                            f"{enabled_above.get(entry, 'server')} level; a level "
                            "cannot disable a policy it did not add"
                        ),
                    )
                )


def _check_floors(levels: Sequence[Level], relaxations: list[Relaxation]) -> None:
    """Report a floor that a stricter level's floor already overrides."""
    for index, level in enumerate(levels):
        for event_type, value in level.floors.items():
            if value not in pe._STRICTNESS:
                relaxations.append(
                    Relaxation(
                        level=level.level,
                        key=f"floors.{event_type}",
                        attempted=value,
                        effective=pe.ALLOW,
                        reason=f"not one of {', '.join(pe.EFFECTS)}",
                    )
                )
                continue
            for stricter in list(levels)[index + 1:]:
                other = stricter.floors.get(event_type)
                if other in pe._STRICTNESS and (
                    pe._STRICTNESS[other] > pe._STRICTNESS[value]
                ):
                    relaxations.append(
                        Relaxation(
                            level=level.level,
                            key=f"floors.{event_type}",
                            attempted=value,
                            effective=other,
                            reason=(
                                f"the {stricter.level} level floors "
                                f"{event_type!r} at {other!r}; a floor can only "
                                "be raised"
                            ),
                        )
                    )
                    break


def composed_floor(levels: Sequence[Level], event_type: str) -> tuple[str, str]:
    """The strictest floor any level set for ``event_type``, and which level.

    ``strictest`` is the whole enforcement of "a session cannot lower a floor":
    a lower value simply loses. There is no branch that could be written the
    other way round.
    """
    best, owner = pe.ALLOW, ""
    for level in levels:
        value = level.floors.get(event_type)
        if value not in pe._STRICTNESS:
            continue
        if pe._STRICTNESS[value] > pe._STRICTNESS[best]:
            best, owner = value, level.level
    return best, owner


def composed_on_policy_error(levels: Sequence[Level]) -> str:
    """The strictest ``on_policy_error`` any level asked for.

    Same rule: ``ask`` from a session cannot soften ``deny`` from the server.
    A level that stated nothing does not vote — and if NOBODY stated one the
    answer is ``deny``, because that is what a broken policy has to mean.
    """
    stated = [lv.on_policy_error for lv in levels if lv.on_policy_error]
    if not stated:
        return pe.DENY
    return pe.DENY if pe.DENY in stated else pe.ASK


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Composition:
    """The composed answer, and enough of the working to review it."""

    effect: str
    reason: str
    policy: str
    level: str = ""
    decisions: tuple[tuple[str, pe.PolicyDecision], ...] = ()
    short_circuited: bool = False
    floor_applied: str = ""
    floor_level: str = ""
    levels_consulted: tuple[str, ...] = ()
    relaxations: tuple[Relaxation, ...] = ()
    state_changed: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.effect == pe.ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.effect == pe.ASK

    def tier(self) -> str:
        for _level, decision in self.decisions:
            if decision.tier:
                return decision.tier
        return gate.UNKNOWN

    def summary(self) -> str:
        who = f"{self.level}:{self.policy}" if self.level else (
            self.policy or "policy_composition"
        )
        line = f"[{self.effect.upper()}] {who}: {self.reason}"
        if self.short_circuited:
            line += " (composition short-circuited on DENY)"
        if self.floor_applied:
            line += (
                f" (raised to the {self.floor_applied} floor set at the "
                f"{self.floor_level} level)"
            )
        return line

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe. Argument values never appear — there are none in here."""
        return {
            "effect": self.effect,
            "reason": self.reason,
            "policy": self.policy,
            "level": self.level,
            "tier": self.tier(),
            "short_circuited": self.short_circuited,
            "floor_applied": self.floor_applied,
            "floor_level": self.floor_level,
            "levels_consulted": list(self.levels_consulted),
            "state_changed": list(self.state_changed),
            "decisions": [
                {
                    "level": level,
                    "policy": d.policy,
                    "effect": d.effect,
                    "reason": d.reason,
                    "rule": d.rule,
                }
                for level, d in self.decisions
            ],
            "relaxations": [
                {
                    "level": r.level, "key": r.key, "attempted": r.attempted,
                    "effective": r.effective, "reason": r.reason,
                }
                for r in self.relaxations
            ],
        }


def _split_level(policy: str) -> tuple[str, str]:
    """``"session:max_calls"`` -> ``("session", "max_calls")``.

    Every decision that comes out of a composed chain carries this prefix, and
    that is not a coincidence to be relied on loosely: :func:`_leveled` tags the
    ones a policy returned, and the chain KEYS are level-prefixed too, so the
    decisions :func:`policy_engine.evaluate` synthesises for a policy that raised
    are prefixed as well. Reading the level back off the prefix therefore covers
    the error path, which a side-channel trace populated by the wrapper would
    miss — the wrapper never returns when the policy raises.
    """
    if ":" in policy:
        level, _, name = policy.partition(":")
        if level in LEVELS:
            return level, name
    return "", policy


def _with_state(event: pe.PolicyEvent, state: SessionState) -> pe.PolicyEvent:
    """``event`` with a fresh snapshot of ``state``. ``PolicyEvent`` is frozen."""
    return pe.PolicyEvent(
        event_type=event.event_type,
        target=event.target,
        arguments=event.arguments,
        actor=event.actor,
        usage=event.usage,
        session_state=state.snapshot(),
        session_id=event.session_id or state.session_id,
        metadata=event.metadata,
    )


def _leveled(
    level: str,
    name: str,
    fn: pe.PolicyFunction,
    state: SessionState,
    changed: list[str],
) -> pe.PolicyFunction:
    """Wrap one policy so its level is recorded and its state updates applied.

    Three things happen in here, and the order matters:

    1. the policy is called and its return normalised;
    2. its ``state_updates`` are applied to ``state`` **immediately**, so the
       next policy in the composition sees what this one wrote — a counter that
       only lands after the whole chain has run cannot be read by anything in
       that chain;
    3. the decision is re-tagged ``<level>:<policy>`` so the audit row says
       which level refused, not just that something did.

    A raise from step 2 is deliberately not caught here: it propagates into
    :func:`policy_engine.evaluate`, which resolves it to ``on_policy_error``.

    ``state_updates`` are dropped from the tagged decision because they have
    already been applied. Carrying them onward would invite a second caller to
    apply them again, and a counter incremented twice per call is a limit that
    fires at half the configured number.
    """

    def wrapper(event: pe.PolicyEvent) -> pe.PolicyDecision:
        # Re-snapshot per policy rather than once per composition. The state a
        # policy reads has to be the state as of ITS turn, or a counter written
        # by the session level is invisible to the server level in the same call
        # — and "the server level checks the total" is the main reason to have a
        # total. A snapshot (not the live dict) so a policy cannot write to it.
        decision = pe._normalize(fn(_with_state(event, state)), name)
        if decision.state_updates:
            changed.extend(state.apply_updates(decision.state_updates))
        return pe.PolicyDecision(
            effect=decision.effect,
            reason=decision.reason,
            policy=f"{level}:{_split_level(decision.policy or name)[1]}",
            tier=decision.tier,
            rule=decision.rule,
            detail=decision.detail,
        )

    wrapper.__name__ = f"{level}:{name}"
    return wrapper


def compose(
    event: pe.PolicyEvent,
    levels: Sequence[Level],
    *,
    state: Optional[SessionState] = None,
    relaxations: Sequence[Relaxation] = (),
) -> Composition:
    """Evaluate ``levels`` in order and return one answer.

    Reduces to :func:`policy_engine.evaluate` over the levels' chains
    concatenated in :data:`LEVELS` order, with a composed floor and a composed
    ``on_policy_error``. That reuse is the point rather than a shortcut: DENY
    short-circuit, strictest-wins, abstention-is-not-authorisation and
    fail-closed normalisation are already tested there, and a second
    implementation of them here would be a second one to keep correct.

    Composition adds exactly three things: level ORDER, per-level provenance on
    every decision, and session-state updates applied as the chain runs.
    """
    state = state if state is not None else get_session_state(event.session_id)
    changed: list[str] = []
    chain: list[tuple[str, pe.PolicyFunction]] = [
        (f"{level.level}:{name}", _leveled(level.level, name, fn, state, changed))
        for level in levels
        for name, fn in level.chain
    ]

    floor, floor_owner = composed_floor(levels, event.event_type)
    config = {
        "on_policy_error": composed_on_policy_error(levels),
        "floors": {event.event_type: floor},
    }
    # Policies read state through the event, and only through it. _leveled
    # re-snapshots per policy, so this baseline event is what a policy sees
    # before anything in the chain has written.
    result = pe.evaluate(_with_state(event, state), chain, config=config)

    decisions = tuple(
        (_split_level(d.policy)[0], d) for d in result.decisions
    )
    # Which levels actually got consulted. A DENY at the session level means the
    # server level was never asked, and the record should say so.
    consulted = tuple(
        dict.fromkeys(level for level, _d in decisions if level)
    )
    winning_level, winning_policy = _split_level(result.policy or "")

    return Composition(
        effect=result.effect,
        reason=result.reason,
        policy=winning_policy,
        level=winning_level,
        decisions=decisions,
        short_circuited=result.short_circuited,
        floor_applied=result.floor_applied,
        floor_level=floor_owner if result.floor_applied else "",
        levels_consulted=consulted,
        relaxations=tuple(relaxations),
        state_changed=tuple(dict.fromkeys(changed)),
    )


def evaluate_composed(
    event: pe.PolicyEvent,
    *,
    session: Optional[dict[str, Any]] = None,
    agent: Optional[dict[str, Any]] = None,
    server: Optional[dict[str, Any]] = None,
    state: Optional[SessionState] = None,
) -> Composition:
    """Load the three levels and :func:`compose` them over ``event``."""
    levels, relaxations = load_levels(session=session, agent=agent, server=server)
    return compose(event, levels, state=state, relaxations=relaxations)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _classification_for(result: Composition, target: str) -> gate.Classification:
    """Project a composition onto the shape ``record_decision`` already writes.

    ``rule`` carries the level, so an auditor reading ``agent_approval_log`` can
    tell an org baseline refusal from one the user set on themselves — which is
    the first question anyone asks of a composed decision.
    """
    who = f"{result.level}:{result.policy}" if result.level else (
        result.policy or "policy_composition"
    )
    return gate.Classification(
        tool_name=target,
        tier=result.tier(),
        rule=f"policy_composition:{who}:{result.effect}",
        detail="; ".join(
            f"[{level or 'composition'}] {d.summary()}" for level, d in result.decisions
        ),
        requires_approval=result.effect != pe.ALLOW,
    )


def record_composed_decision(
    *,
    event: pe.PolicyEvent,
    result: Composition,
    approved: bool,
    reason: str,
    actor: str,
    mode: str,
) -> bool:
    """Append one composed decision to the append-only ``agent_approval_log``.

    Delegates to :func:`approval_gate.record_decision` for the same reason
    :mod:`policy_engine` does: that function is the one place that knows the
    no-argument-values rule, and a second writer is a second chance to break it.
    """
    return gate.record_decision(
        tool_name=event.target,
        tool_input=event.arguments,
        classification=_classification_for(result, event.target),
        decision=gate.ApprovalDecision(approved, reason, actor),
        mode=mode,
        session_id=event.session_id,
    )


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------
def build_composed_policy_hook(
    *,
    session_id: str = "",
    session_policy: Optional[dict[str, Any]] = None,
    agent_policy: Optional[dict[str, Any]] = None,
    server_policy: Optional[dict[str, Any]] = None,
    approver: Optional[gate.Approver] = None,
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    usage: Optional[dict[str, Any]] = None,
    state: Optional[SessionState] = None,
    on_event: Optional[Callable[[gate.GateEvent], None]] = None,
    consult_pre_tool_check: bool = True,
) -> Callable[[str, Any], Optional[str]]:
    """A ``PreToolUseHook`` that composes session, agent and server policies.

    Same contract as :func:`approval_gate.build_approval_hook` and
    :func:`policy_engine.build_policy_hook` — return ``None`` to let the call
    run, a string to halt it — so it is a drop-in for
    ``run_agent_loop(approval_gate=...)``.

    Levels are resolved ONCE, when the hook is built, and the session state is
    fetched from the per-process registry so the counters survive the hook being
    rebuilt for the next turn.
    """
    resolved_mode = gate.resolve_mode(mode)
    who = gate.resolve_actor(actor)
    levels, relaxations = load_levels(
        session=session_policy, agent=agent_policy, server=server_policy
    )
    session_state = state if state is not None else get_session_state(session_id)
    counters = usage if usage is not None else {}
    approve = approver or gate.console_approver
    # Only the SERVER level decides whether allows are logged. A session that
    # could set log_allow: false could stop its own denials being reviewable.
    server_raw = resolve_level_configs(server=server_policy)[LEVEL_SERVER][0]
    log_allow = pe._log_allow(server_raw)

    for relaxation in relaxations:
        logger.warning("policy_composition: %s", relaxation.summary())

    def _emit(event: gate.GateEvent) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception as exc:  # noqa: BLE001 — an observer never blocks
                logger.debug("policy_composition: on_event raised: %s", exc)

    def _finish(
        pol_event: pe.PolicyEvent,
        result: Composition,
        approved: bool,
        reason: str,
        who_decided: str,
        *,
        record: bool = True,
    ) -> Optional[str]:
        recorded = False
        if record:
            recorded = record_composed_decision(
                event=pol_event,
                result=result,
                approved=approved,
                reason=reason,
                actor=who_decided,
                mode=resolved_mode,
            )
        _emit(
            gate.GateEvent(
                tool_name=pol_event.target,
                tier=result.tier(),
                requires_approval=result.effect != pe.ALLOW,
                allowed=approved,
                reason=reason,
                actor=who_decided,
                recorded=recorded,
                extra={
                    "effect": result.effect,
                    "policy": result.policy,
                    "level": result.level,
                    "rule": f"policy_composition:{result.level}:{result.policy}",
                    "mode": resolved_mode,
                    "short_circuited": result.short_circuited,
                    "levels_consulted": list(result.levels_consulted),
                    "state_changed": list(result.state_changed),
                },
            )
        )
        if approved:
            return None
        return (
            f"BLOCKED by the {result.level or 'composed'} policy level: "
            f"{pol_event.target} was refused by {result.policy} "
            f"({result.effect.upper()}). {reason}. Do not retry this call — ask "
            "the operator, or choose an allowed alternative."
        )

    def hook(tool_name: str, tool_input: Any) -> Optional[str]:
        if not isinstance(tool_input, dict):
            tool_input = {} if tool_input is None else {"value": tool_input}
        pol_event = pe.PolicyEvent(
            event_type=pe.EVENT_TOOL_CALL,
            target=tool_name,
            arguments=tool_input,
            actor=who,
            usage=counters,
            session_state=session_state.snapshot(),
            session_id=session_id,
        )
        result = compose(
            pol_event, levels, state=session_state, relaxations=relaxations
        )

        # 1. Hard blocks are not policy questions and not approval questions.
        if consult_pre_tool_check:
            blocked, why = gate._hard_block(tool_name, tool_input)
            if blocked:
                return _finish(pol_event, result, False, f"hard block: {why}", who)

        # 2. DENY. Never offered to the approver — that is the point of DENY.
        if result.effect == pe.DENY:
            return _finish(pol_event, result, False, result.reason, who)

        # 3. ALLOW.
        if result.effect == pe.ALLOW:
            return _finish(
                pol_event, result,
                True, result.reason or "allowed by the composed policy levels",
                who, record=log_allow,
            )

        # 4. ASK — the gate's existing escalation path, unchanged.
        if resolved_mode == gate.MODE_OFF:
            return _finish(
                pol_event, result, True,
                f"approval gate mode=off (escape hatch); composition said ask: "
                f"{result.reason}", who,
            )
        if resolved_mode == gate.MODE_DRY_RUN:
            return _finish(
                pol_event, result, True,
                f"dry_run: would have asked; composition said ask: {result.reason}",
                who,
            )

        request = gate.ApprovalRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            classification=_classification_for(result, tool_name),
            actor=who,
        )
        try:
            decision = gate._normalize(approve(request), who)
        except Exception as exc:  # noqa: BLE001 — a broken approver denies
            logger.warning("policy_composition: approver raised, denying: %s", exc)
            decision = gate.ApprovalDecision(False, f"approver error: {exc}", who)
        return _finish(
            pol_event, result, decision.approved,
            decision.reason or "approver returned no reason",
            decision.actor or who,
        )

    return hook


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_policy_arg(raw: Optional[str], label: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("{"):
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise ValueError(f"--{label}-policy must be a JSON object")
        return loaded
    path = Path(raw)
    if not path.exists():
        raise ValueError(f"--{label}-policy: {raw} does not exist")
    return _load_yaml(path) or {}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compose session/agent/server policies (exa-policy-02)."
    )
    parser.add_argument("--evaluate", metavar="TARGET", help="tool/target to evaluate")
    parser.add_argument("--input", default="{}", help="event arguments as JSON")
    parser.add_argument("--event-type", default=pe.EVENT_TOOL_CALL)
    parser.add_argument("--actor", default="")
    parser.add_argument("--session-id", default="", help="session to scope state to")
    parser.add_argument("--session-policy", help="JSON object or path to a YAML file")
    parser.add_argument("--agent-policy", help="JSON object or path to a YAML file")
    parser.add_argument("--server-policy", help="JSON object or path to a YAML file")
    parser.add_argument("--levels", action="store_true", help="show the resolved levels")
    parser.add_argument("--state", metavar="SESSION_ID", help="dump session state")
    parser.add_argument(
        "--reset-state", metavar="SESSION_ID", help="clear a session's state"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        session = _parse_policy_arg(args.session_policy, "session")
        agent = _parse_policy_arg(args.agent_policy, "agent")
        server = _parse_policy_arg(args.server_policy, "server")
    except (ValueError, json.JSONDecodeError) as exc:
        print(exc)
        return 2

    if args.reset_state:
        state = get_session_state(args.reset_state, refresh=True)
        state.reset()
        forget_session_state(args.reset_state)
        payload = {"session_id": args.reset_state, "reset": True}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if args.state:
        state = get_session_state(args.state, refresh=True)
        payload = {"session_id": args.state, "state": state.snapshot()}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if args.levels:
        pe.load_config(refresh=True)
        levels, relaxations = load_levels(
            session=session, agent=agent, server=server
        )
        payload = {
            "order": list(LEVELS),
            "levels": [
                {
                    "level": level.level,
                    "source": level.source,
                    "policies": list(level.policy_names),
                    "floors": level.floors,
                    "on_policy_error": level.on_policy_error,
                }
                for level in levels
            ],
            "composed_floor": dict(
                zip(("effect", "level"), composed_floor(levels, args.event_type))
            ),
            "composed_on_policy_error": composed_on_policy_error(levels),
            "registered": pe.list_policies(),
            "relaxations": [r.summary() for r in relaxations],
        }
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if not args.evaluate:
        parser.print_help()
        return 2

    try:
        arguments = json.loads(args.input)
    except json.JSONDecodeError as exc:
        print(f"--input is not valid JSON: {exc}")
        return 2
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}

    pe.load_config(refresh=True)
    result = evaluate_composed(
        pe.PolicyEvent(
            event_type=args.event_type,
            target=args.evaluate,
            arguments=arguments,
            actor=gate.resolve_actor(args.actor or None),
            session_id=args.session_id,
        ),
        session=session,
        agent=agent,
        server=server,
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(result.summary())
        for level, decision in result.decisions:
            print(f"  [{level or 'composition'}] {decision.summary()}")
        for relaxation in result.relaxations:
            print(f"  ! {relaxation.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
