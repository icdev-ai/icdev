# CUI // SP-CTI
"""Normalized, READ-ONLY agent event view (agov-det-01).

ICDEV already writes rich agent activity into five tables and never reads any
of it back for detection. This module is the missing read side: it projects
those rows into one :class:`AgentEvent` shape so a rule can be written once and
evaluated against every source.

Sources (all read-only — this module creates no table and issues no write)::

    hook_events       HMAC-signed, session_id-keyed, written by
                      .claude/hooks/send_event.py::store_event
    agent_executions  one row per agent run (model, status, tokens)
    ai_telemetry      one row per model call (provider, function, latency)
    audit_trail       append-only NIST AU trail (actor, action)
    ace_audit_log     ACE co-worker action log (instance, actor, action)

Event types are MUTUALLY EXCLUSIVE and one source row yields AT MOST ONE
event. A recognized shell request produces ``command.exec`` and NOT an
additional ``tool.call``; an unrecognized tool stays ``tool.call``.

Two invariants are enforced in code, not just documented:

(a) **Classification never reads free text.** :func:`classify` is a pure
    function of the structured fields ``tool_name`` and the operand keys in
    :data:`OPERAND_KEYS`. Command output, notification messages, prompt
    previews and audit ``details`` are listed in :data:`FREE_TEXT_KEYS` and
    :func:`_structured` refuses to read them — a key in that set raises rather
    than being quietly consulted. There is no regex over any payload string
    anywhere in this module.

(b) **Every mapping carries a confidence naming how directly the source
    supports it**, and an ambiguous payload stays ``tool.call``.
    :meth:`AgentEvent.__post_init__` rejects any non-``tool.call`` event whose
    operand is missing, so "promote first, hope the operand shows up" is not
    expressible. See :data:`CONFIDENCE_LEVELS`.

CLI::

    python tools/agent_detect/events.py --json --limit 20
    python tools/agent_detect/events.py --session <session_id> --json
    python -m tools.agent_detect.events --source hook_events --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

LOG = get_logger("icdev.agent_detect.events")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

COMMAND_EXEC = "command.exec"
FILE_READ = "file.read"
FILE_WRITE = "file.write"
FILE_DELETE = "file.delete"
NETWORK_INDICATOR = "network.indicator"
TOOL_CALL = "tool.call"

#: The closed, mutually exclusive event vocabulary. A source row maps to
#: exactly one of these.
EVENT_TYPES: Tuple[str, ...] = (
    COMMAND_EXEC,
    FILE_READ,
    FILE_WRITE,
    FILE_DELETE,
    NETWORK_INDICATOR,
    TOOL_CALL,
)

#: ``tool.call`` is the honest fallback: it asserts only that a tool ran.
FALLBACK_EVENT_TYPE = TOOL_CALL

SOURCE_HOOK_EVENTS = "hook_events"
SOURCE_AGENT_EXECUTIONS = "agent_executions"
SOURCE_AI_TELEMETRY = "ai_telemetry"
SOURCE_AUDIT_TRAIL = "audit_trail"
SOURCE_ACE_AUDIT_LOG = "ace_audit_log"

SOURCES: Tuple[str, ...] = (
    SOURCE_HOOK_EVENTS,
    SOURCE_AGENT_EXECUTIONS,
    SOURCE_AI_TELEMETRY,
    SOURCE_AUDIT_TRAIL,
    SOURCE_ACE_AUDIT_LOG,
)

# -- confidence -------------------------------------------------------------
#
# A confidence NAMES how directly the source row supports the mapping. It is
# not a probability and it is never computed from a score — three discrete
# levels, each with a checkable definition:

#: The tool is known by name and the operand came from that tool's own
#: documented input field (``Bash.command``, ``Read.file_path``,
#: ``WebFetch.url``). The row states the action outright.
CONFIDENCE_DIRECT = "direct"

#: The tool was recognized through a shared structured vocabulary rather than
#: an exact entry — the ``command_tools`` generic-executor list in
#: args/agent_approval_policy.yaml. Still a structured field, one hop removed.
CONFIDENCE_DERIVED = "derived"

#: The row names a tool, agent or audited action and nothing more. No operand,
#: so no promotion beyond ``tool.call``.
CONFIDENCE_DECLARED = "declared"

CONFIDENCE_LEVELS: Tuple[str, ...] = (
    CONFIDENCE_DIRECT,
    CONFIDENCE_DERIVED,
    CONFIDENCE_DECLARED,
)

#: Ordering for rules that want "at least this direct". Higher is stronger.
CONFIDENCE_RANK: Dict[str, int] = {
    CONFIDENCE_DIRECT: 3,
    CONFIDENCE_DERIVED: 2,
    CONFIDENCE_DECLARED: 1,
}

# ---------------------------------------------------------------------------
# Structured-field allowlist / free-text denylist  (invariant (a))
# ---------------------------------------------------------------------------

#: Operand kinds and the structured payload keys that may carry them. Claude
#: Code tools use ``file_path``/``command``/``url``; the ICDEV agent runtime
#: (tools/agent_runtime/builtin_tools.py, mutating_tools.py) uses ``path`` and
#: ``command``. Nothing outside these keys is ever read as an operand.
OPERAND_KEYS: Dict[str, Tuple[str, ...]] = {
    "command": ("command",),
    "file": ("file_path", "path", "notebook_path"),
    "url": ("url",),
}

#: Payload keys that carry free text — model prose, command output, user
#: prompts, audit narrative. Reading any of these to decide WHAT happened is
#: the failure mode this module exists to prevent, so :func:`_structured`
#: raises on them rather than trusting every future caller to remember.
#:
#: ``content`` is here on purpose: it is the body a Write tool is asked to
#: write, i.e. attacker-influenced text, not a statement about the action.
FREE_TEXT_KEYS = frozenset({
    "content",
    "detail",
    "details",
    "error_message",
    "input",
    "message",
    "new_string",
    "old_string",
    "output",
    "output_summary",
    "prompt",
    "prompt_preview",
    "response",
    "stderr",
    "stdout",
    "text",
    "tool_output",
})

# ---------------------------------------------------------------------------
# Tool → event mapping
# ---------------------------------------------------------------------------

#: Exact tool names whose input schema is known, keyed lowercase.
#: ``(event_type, operand_kind)``. The operand is REQUIRED: a row naming one of
#: these tools without its operand is ambiguous and falls back to ``tool.call``
#: (which is exactly the shape of a real ``post_tool_use`` payload, whose
#: ``tool_input_keys`` records that a command existed but not what it was).
TOOL_SPECS: Dict[str, Tuple[str, str]] = {
    # -- Claude Code / harness tools
    "bash": (COMMAND_EXEC, "command"),
    "read": (FILE_READ, "file"),
    "notebookread": (FILE_READ, "file"),
    "write": (FILE_WRITE, "file"),
    "edit": (FILE_WRITE, "file"),
    "multiedit": (FILE_WRITE, "file"),
    "notebookedit": (FILE_WRITE, "file"),
    "webfetch": (NETWORK_INDICATOR, "url"),
    # -- ICDEV agent runtime tools (builtin_tools.py / mutating_tools.py and
    #    the vocabulary enumerated in args/agent_approval_policy.yaml)
    "read_file": (FILE_READ, "file"),
    "write_file": (FILE_WRITE, "file"),
    "edit_file": (FILE_WRITE, "file"),
    "delete_file": (FILE_DELETE, "file"),
    "run_command": (COMMAND_EXEC, "command"),
    "http_post": (NETWORK_INDICATOR, "url"),
    "post_webhook": (NETWORK_INDICATOR, "url"),
}

#: Fallback generic-executor names, used only when
#: args/agent_approval_policy.yaml cannot be read. Kept deliberately identical
#: to that file's ``command_tools`` so the two cannot disagree silently.
_FALLBACK_COMMAND_TOOLS: Tuple[str, ...] = (
    "run_command", "bash", "shell", "sh", "execute_command",
    "exec", "powershell", "terminal",
)

_APPROVAL_POLICY_PATH = BASE_DIR / "args" / "agent_approval_policy.yaml"
_command_tools_cache: Optional[frozenset] = None

#: MCP tool names are ``mcp__<server>__<tool>``. The server and tool are
#: recovered from that structured name, but the tool's INPUT SCHEMA is not
#: known to ICDEV, so an MCP call is never promoted past ``tool.call`` here.
MCP_PREFIX = "mcp__"
MCP_SEPARATOR = "__"


def _command_tools() -> frozenset:
    """Generic-executor tool names from args/agent_approval_policy.yaml.

    Reused rather than re-listed: that file is the operator-controlled record
    of which tools are a shell rather than a verb, and duplicating it here is
    how the two drift.
    """
    global _command_tools_cache
    if _command_tools_cache is not None:
        return _command_tools_cache

    names: Optional[List[str]] = None
    try:
        import yaml  # noqa: PLC0415 — optional at import time, needed only here

        with open(_APPROVAL_POLICY_PATH, encoding="utf-8") as fh:
            policy = yaml.safe_load(fh) or {}
        raw = policy.get("command_tools")
        if isinstance(raw, list):
            names = [str(n).strip().lower() for n in raw if str(n).strip()]
    except Exception as exc:  # noqa: BLE001 — a missing policy must not break reads
        LOG.warning(
            "agent_detect: could not read command_tools from %s (%s: %s); "
            "using the built-in fallback list",
            _APPROVAL_POLICY_PATH, type(exc).__name__, exc,
        )

    if not names:
        names = list(_FALLBACK_COMMAND_TOOLS)
    _command_tools_cache = frozenset(names)
    return _command_tools_cache


# ---------------------------------------------------------------------------
# The normalized shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentEvent:
    """One normalized agent action, projected from one source row.

    ``__post_init__`` enforces invariant (b): the vocabulary is closed, the
    confidence is named, and a promoted event must carry the operand that
    justified the promotion. Constructing ``command.exec`` without a command
    raises — there is no path by which a guess becomes an event.
    """

    event_id: str
    session_id: Optional[str]
    ts: Optional[str]
    source: str
    event_type: str
    confidence: str
    actor: Optional[str] = None
    tool_name: Optional[str] = None
    command: Optional[str] = None
    file_path: Optional[str] = None
    url: Optional[str] = None
    model: Optional[str] = None
    exit_code: Optional[int] = None
    project_id: Optional[str] = None
    mcp_server: Optional[str] = None
    mcp_tool: Optional[str] = None
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown event_type {self.event_type!r}; "
                f"the vocabulary is closed: {EVENT_TYPES}"
            )
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"unknown confidence {self.confidence!r}; expected one of "
                f"{CONFIDENCE_LEVELS}"
            )
        if self.source not in SOURCES:
            raise ValueError(f"unknown source {self.source!r}; expected one of {SOURCES}")

        required = _REQUIRED_OPERAND_FIELD.get(self.event_type)
        if required is not None and not getattr(self, required):
            raise ValueError(
                f"{self.event_type} requires a {required}; a row without one is "
                f"ambiguous and must stay {FALLBACK_EVENT_TYPE}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict projection, suitable for JSON and for rule matchers."""
        return asdict(self)


#: event_type → the AgentEvent attribute that must be populated for it.
#: ``tool.call`` is absent on purpose — needing no operand is what makes it the
#: honest fallback.
_REQUIRED_OPERAND_FIELD: Dict[str, str] = {
    COMMAND_EXEC: "command",
    FILE_READ: "file_path",
    FILE_WRITE: "file_path",
    FILE_DELETE: "file_path",
    NETWORK_INDICATOR: "url",
}


# ---------------------------------------------------------------------------
# Structured field access  (invariant (a))
# ---------------------------------------------------------------------------


def _structured(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[Any]:
    """Read the first present of ``keys`` from a hook payload.

    Looks in ``payload[key]`` and in ``payload["tool_input"][key]`` — the two
    places a hook writes a tool's structured input — and NOWHERE else. Reading
    a free-text key is a programming error, not a fallback, so it raises.
    """
    for key in keys:
        if key in FREE_TEXT_KEYS:
            raise ValueError(
                f"{key!r} carries free text; classification must come from "
                f"structured fields only (agov-det-01 invariant (a))"
            )

    if not isinstance(payload, Mapping):
        return None

    tool_input = payload.get("tool_input")
    scopes: List[Mapping[str, Any]] = [payload]
    if isinstance(tool_input, Mapping):
        scopes.insert(0, tool_input)

    for scope in scopes:
        for key in keys:
            value = scope.get(key)
            if value is not None and value != "":
                return value
    return None


def _structured_str(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[str]:
    """As :func:`_structured`, accepting only a genuine string operand.

    A dict or list where a path was expected is ambiguous, not a path.
    """
    value = _structured(payload, keys)
    return value if isinstance(value, str) else None


def _structured_int(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    value = _structured(payload, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def split_mcp_tool(tool_name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split ``mcp__<server>__<tool>`` into ``(server, tool)``.

    Server names legitimately contain ``_`` (``claude_ai_Gmail``) and tool
    names can contain ``__``, so the split is on the FIRST two separators and
    the remainder is the tool.
    """
    if not tool_name or not tool_name.startswith(MCP_PREFIX):
        return None, None
    parts = tool_name.split(MCP_SEPARATOR)
    # ['mcp', '<server>', '<tool>', ...]
    if len(parts) < 3 or not parts[1] or not parts[2]:
        return None, None
    return parts[1], MCP_SEPARATOR.join(parts[2:])


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(
    tool_name: Optional[str], payload: Optional[Mapping[str, Any]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """Classify one tool invocation. Pure, structured-only, single-valued.

    Args:
        tool_name: the source row's ``tool_name`` column.
        payload: the parsed hook payload, or ``None``.

    Returns:
        ``(event_type, confidence, operands)`` where ``operands`` carries only
        the keys the mapping actually justified — ``command``, ``file_path``
        or ``url``. Exactly ONE event type is returned; a recognized shell is
        ``command.exec`` and never additionally ``tool.call``.
    """
    payload = payload if isinstance(payload, Mapping) else {}
    name = (tool_name or "").strip()
    lowered = name.lower()

    # MCP tools: the server and tool are recoverable from the structured name,
    # but their input schema is not ICDEV's, so no promotion is warranted.
    if lowered.startswith(MCP_PREFIX):
        return FALLBACK_EVENT_TYPE, CONFIDENCE_DECLARED, {}

    spec = TOOL_SPECS.get(lowered)
    if spec is not None:
        event_type, operand_kind = spec
        operands = _extract_operand(payload, operand_kind)
        if operands:
            return event_type, CONFIDENCE_DIRECT, operands
        # Known tool, missing operand → ambiguous. Do not promote.
        return FALLBACK_EVENT_TYPE, CONFIDENCE_DECLARED, {}

    if lowered in _command_tools():
        operands = _extract_operand(payload, "command")
        if operands:
            return COMMAND_EXEC, CONFIDENCE_DERIVED, operands
        return FALLBACK_EVENT_TYPE, CONFIDENCE_DECLARED, {}

    return FALLBACK_EVENT_TYPE, CONFIDENCE_DECLARED, {}


def _extract_operand(payload: Mapping[str, Any], operand_kind: str) -> Dict[str, Any]:
    """Pull one operand out of a payload's structured fields."""
    keys = OPERAND_KEYS.get(operand_kind)
    if not keys:
        return {}
    value = _structured_str(payload, keys)
    if value is None:
        return {}
    target = {"command": "command", "file": "file_path", "url": "url"}[operand_kind]
    return {target: value}


# ---------------------------------------------------------------------------
# Row → AgentEvent
# ---------------------------------------------------------------------------


def _as_mapping(row: Any, columns: Sequence[str]) -> Dict[str, Any]:
    """Accept a dict-ish row, a sqlite3.Row, or a positional tuple."""
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return {key: row[key] for key in columns}  # sqlite3.Row / DictRow
    except (TypeError, KeyError, IndexError):
        pass
    return dict(zip(columns, row))


def _iso(value: Any) -> Optional[str]:
    """Normalize a timestamp column to an ISO-8601 string."""
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_payload(raw: Any) -> Dict[str, Any]:
    """Parse the hook_events.payload JSON column in Python.

    Deliberately NOT ``json_extract``/``jsonb`` in SQL: runtime SQL here is
    authored for PostgreSQL and SQLite-dialect JSON functions do not survive
    the trip (see the runtime-SQL guardrail in CLAUDE.md). Reading the raw
    column and parsing here works identically on both backends.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


HOOK_EVENT_COLUMNS: Tuple[str, ...] = (
    "id", "session_id", "hook_type", "tool_name", "project_id", "payload", "created_at",
)


def normalize_hook_event(row: Any) -> AgentEvent:
    """Project one ``hook_events`` row.

    ``hook_events`` has no actor column — the session is the correlator — so
    ``actor`` is populated only when the payload carries a structured
    ``actor``/``agent_id``/``user_id``, and is otherwise ``None`` rather than
    an invented identity.
    """
    data = _as_mapping(row, HOOK_EVENT_COLUMNS)
    payload = _parse_payload(data.get("payload"))
    tool_name = data.get("tool_name") or None

    event_type, confidence, operands = classify(tool_name, payload)
    mcp_server, mcp_tool = split_mcp_tool(tool_name)

    return AgentEvent(
        event_id=f"{SOURCE_HOOK_EVENTS}:{data.get('id')}",
        session_id=data.get("session_id") or None,
        ts=_iso(data.get("created_at")),
        source=SOURCE_HOOK_EVENTS,
        event_type=event_type,
        confidence=confidence,
        actor=_structured_str(payload, ("actor", "agent_id", "user_id")),
        tool_name=tool_name,
        command=operands.get("command"),
        file_path=operands.get("file_path"),
        url=operands.get("url"),
        model=_structured_str(payload, ("model",)),
        exit_code=_structured_int(payload, ("exit_code", "returncode")),
        project_id=data.get("project_id") or None,
        mcp_server=mcp_server,
        mcp_tool=mcp_tool,
        tool_call_id=_structured_str(payload, ("tool_use_id", "tool_call_id")),
    )


AGENT_EXECUTION_COLUMNS: Tuple[str, ...] = (
    "id", "execution_id", "project_id", "agent_type", "model", "status", "created_at",
)

#: ``agent_executions.status`` → a synthetic exit code. Only the two terminal
#: states map; ``started``/``retried``/``timeout`` have no exit code yet and
#: get ``None`` rather than a stand-in.
_STATUS_EXIT_CODES: Dict[str, int] = {"completed": 0, "failed": 1}


def normalize_agent_execution(row: Any) -> AgentEvent:
    """Project one ``agent_executions`` row.

    An agent run is not a file or shell action, so it is always ``tool.call``:
    the row declares that an agent of some type ran under some model, and
    nothing about what it touched.
    """
    data = _as_mapping(row, AGENT_EXECUTION_COLUMNS)
    status = (data.get("status") or "").strip().lower()
    return AgentEvent(
        event_id=f"{SOURCE_AGENT_EXECUTIONS}:{data.get('execution_id') or data.get('id')}",
        session_id=data.get("execution_id") or None,
        ts=_iso(data.get("created_at")),
        source=SOURCE_AGENT_EXECUTIONS,
        event_type=FALLBACK_EVENT_TYPE,
        confidence=CONFIDENCE_DECLARED,
        actor=data.get("agent_type") or None,
        tool_name=data.get("agent_type") or None,
        model=data.get("model") or None,
        exit_code=_STATUS_EXIT_CODES.get(status),
        project_id=data.get("project_id") or None,
    )


AI_TELEMETRY_COLUMNS: Tuple[str, ...] = (
    "id", "model_id", "provider", "agent_id", "user_id", "project_id",
    "function", "created_at",
)


def normalize_ai_telemetry(row: Any) -> AgentEvent:
    """Project one ``ai_telemetry`` row.

    A model call declares a routed function and a model, never an action on
    the host, so it stays ``tool.call``.
    """
    data = _as_mapping(row, AI_TELEMETRY_COLUMNS)
    return AgentEvent(
        event_id=f"{SOURCE_AI_TELEMETRY}:{data.get('id')}",
        session_id=data.get("agent_id") or None,
        ts=_iso(data.get("created_at")),
        source=SOURCE_AI_TELEMETRY,
        event_type=FALLBACK_EVENT_TYPE,
        confidence=CONFIDENCE_DECLARED,
        actor=data.get("agent_id") or data.get("user_id") or None,
        tool_name=data.get("function") or None,
        model=data.get("model_id") or None,
        project_id=data.get("project_id") or None,
    )


AUDIT_TRAIL_COLUMNS: Tuple[str, ...] = (
    "id", "project_id", "event_type", "actor", "action", "session_id", "created_at",
)


def normalize_audit_trail(row: Any) -> AgentEvent:
    """Project one ``audit_trail`` row.

    ``action`` is a short structured verb and ``details`` is narrative. Only
    the former is read, and it is not matched against anything — the row
    declares that an actor did something auditable, so ``tool.call`` is the
    strongest honest claim.
    """
    data = _as_mapping(row, AUDIT_TRAIL_COLUMNS)
    return AgentEvent(
        event_id=f"{SOURCE_AUDIT_TRAIL}:{data.get('id')}",
        session_id=data.get("session_id") or None,
        ts=_iso(data.get("created_at")),
        source=SOURCE_AUDIT_TRAIL,
        event_type=FALLBACK_EVENT_TYPE,
        confidence=CONFIDENCE_DECLARED,
        actor=data.get("actor") or None,
        tool_name=data.get("action") or None,
        project_id=data.get("project_id") or None,
    )


ACE_AUDIT_LOG_COLUMNS: Tuple[str, ...] = (
    "id", "instance_id", "coworker_id", "action", "actor", "created_at",
)


def normalize_ace_audit_log(row: Any) -> AgentEvent:
    """Project one ``ace_audit_log`` row.

    ``instance_id`` is the ACE session correlator, so it lands in
    ``session_id``; ``ace_audit_log`` has no other session key.
    """
    data = _as_mapping(row, ACE_AUDIT_LOG_COLUMNS)
    return AgentEvent(
        event_id=f"{SOURCE_ACE_AUDIT_LOG}:{data.get('id')}",
        session_id=data.get("instance_id") or None,
        ts=_iso(data.get("created_at")),
        source=SOURCE_ACE_AUDIT_LOG,
        event_type=FALLBACK_EVENT_TYPE,
        confidence=CONFIDENCE_DECLARED,
        actor=data.get("actor") or data.get("coworker_id") or None,
        tool_name=data.get("action") or None,
    )


NORMALIZERS = {
    SOURCE_HOOK_EVENTS: normalize_hook_event,
    SOURCE_AGENT_EXECUTIONS: normalize_agent_execution,
    SOURCE_AI_TELEMETRY: normalize_ai_telemetry,
    SOURCE_AUDIT_TRAIL: normalize_audit_trail,
    SOURCE_ACE_AUDIT_LOG: normalize_ace_audit_log,
}


def normalize_row(source: str, row: Any) -> AgentEvent:
    """Dispatch one raw row to its source's normalizer."""
    normalizer = NORMALIZERS.get(source)
    if normalizer is None:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    return normalizer(row)


# ---------------------------------------------------------------------------
# Reading  (SELECT only — this module never writes)
# ---------------------------------------------------------------------------

#: SQL authored for PostgreSQL (``%s`` placeholders). ``StorageConnection``
#: rewrites them for the SQLite fallback. No JSON functions appear here: the
#: ``payload`` column is read raw and parsed in Python by :func:`_parse_payload`.
_SELECTS: Dict[str, Tuple[str, Tuple[str, ...], Optional[str]]] = {
    SOURCE_HOOK_EVENTS: (
        "SELECT id, session_id, hook_type, tool_name, project_id, payload, created_at "
        "FROM hook_events",
        HOOK_EVENT_COLUMNS,
        "session_id",
    ),
    SOURCE_AGENT_EXECUTIONS: (
        "SELECT id, execution_id, project_id, agent_type, model, status, created_at "
        "FROM agent_executions",
        AGENT_EXECUTION_COLUMNS,
        "execution_id",
    ),
    SOURCE_AI_TELEMETRY: (
        "SELECT id, model_id, provider, agent_id, user_id, project_id, function, "
        "created_at FROM ai_telemetry",
        AI_TELEMETRY_COLUMNS,
        "agent_id",
    ),
    SOURCE_AUDIT_TRAIL: (
        "SELECT id, project_id, event_type, actor, action, session_id, created_at "
        "FROM audit_trail",
        AUDIT_TRAIL_COLUMNS,
        "session_id",
    ),
    SOURCE_ACE_AUDIT_LOG: (
        "SELECT id, instance_id, coworker_id, action, actor, created_at "
        "FROM ace_audit_log",
        ACE_AUDIT_LOG_COLUMNS,
        "instance_id",
    ),
}

DEFAULT_LIMIT = 500


def iter_events(
    session_id: Optional[str] = None,
    sources: Optional[Iterable[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    event_types: Optional[Iterable[str]] = None,
) -> Iterator[AgentEvent]:
    """Yield normalized events from the configured backend.

    A source whose table does not exist yet is skipped with a warning rather
    than failing the whole read — the five tables are created by different
    migrations and a fresh checkout will not have all of them.

    Args:
        session_id: restrict to one session (matched against each source's own
            session-ish column; sources without one are skipped when set).
        sources: subset of :data:`SOURCES`. Default: all.
        since / until: inclusive ISO-8601 bounds on ``created_at``.
        limit: per-source row cap. ``0`` or less means no cap.
        event_types: keep only these normalized types.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415 — lazy: keeps import cheap

    wanted = tuple(sources) if sources else SOURCES
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise ValueError(f"unknown source(s) {unknown}; expected a subset of {SOURCES}")
    keep_types = set(event_types) if event_types else None

    conn = get_connection()
    try:
        for source in wanted:
            sql, columns, session_column = _SELECTS[source]
            if session_id and not session_column:
                continue

            clauses: List[str] = []
            params: List[Any] = []
            if session_id:
                clauses.append(f"{session_column} = %s")
                params.append(session_id)
            if since:
                clauses.append("created_at >= %s")
                params.append(since)
            if until:
                clauses.append("created_at <= %s")
                params.append(until)

            query = sql
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at ASC, id ASC"
            if limit and limit > 0:
                query += " LIMIT %s"
                params.append(int(limit))

            try:
                cur = conn.cursor()
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
            except Exception as exc:  # noqa: BLE001 — a missing table is not fatal
                LOG.warning(
                    "agent_detect: skipping source %s (%s: %s)",
                    source, type(exc).__name__, exc,
                )
                # PostgreSQL aborts the whole transaction on any failed
                # statement, so without this rollback the FIRST missing table
                # would make every LATER source raise "current transaction is
                # aborted" and be skipped too — one gap silently reported as
                # five. Harmless no-op on SQLite.
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001 — nothing to recover if this fails
                    pass
                continue

            for row in rows:
                try:
                    event = normalize_row(source, _as_mapping(row, columns))
                except Exception as exc:  # noqa: BLE001 — one bad row must not blind the rest
                    LOG.warning(
                        "agent_detect: could not normalize a %s row (%s: %s)",
                        source, type(exc).__name__, exc,
                    )
                    continue
                if keep_types and event.event_type not in keep_types:
                    continue
                yield event
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — a close failure must not mask results
            pass


def fetch_events(**kwargs: Any) -> List[AgentEvent]:
    """:func:`iter_events` as a list, ordered by timestamp across all sources."""
    events = list(iter_events(**kwargs))
    events.sort(key=lambda e: (e.ts or "", e.source, e.event_id))
    return events


def summarize(events: Sequence[AgentEvent]) -> Dict[str, Any]:
    """Counts by event type, source and confidence — for the CLI and for tests."""
    by_type: Dict[str, int] = {t: 0 for t in EVENT_TYPES}
    by_source: Dict[str, int] = {}
    by_confidence: Dict[str, int] = {c: 0 for c in CONFIDENCE_LEVELS}
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
        by_source[event.source] = by_source.get(event.source, 0) + 1
        by_confidence[event.confidence] = by_confidence.get(event.confidence, 0) + 1
    return {
        "total": len(events),
        "by_event_type": by_type,
        "by_source": by_source,
        "by_confidence": by_confidence,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only normalized agent event view (AGOV / agov-det-01).",
    )
    parser.add_argument("--session", help="restrict to one session id")
    parser.add_argument(
        "--source", action="append", choices=list(SOURCES),
        help="restrict to a source (repeatable)",
    )
    parser.add_argument(
        "--event-type", action="append", choices=list(EVENT_TYPES),
        help="restrict to an event type (repeatable)",
    )
    parser.add_argument("--since", help="inclusive ISO-8601 lower bound on created_at")
    parser.add_argument("--until", help="inclusive ISO-8601 upper bound on created_at")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="per-source row cap")
    parser.add_argument("--summary", action="store_true", help="counts only, no rows")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    events = fetch_events(
        session_id=args.session,
        sources=args.source,
        since=args.since,
        until=args.until,
        limit=args.limit,
        event_types=args.event_type,
    )
    stats = summarize(events)

    if args.json:
        payload: Dict[str, Any] = {"summary": stats}
        if not args.summary:
            payload["events"] = [e.to_dict() for e in events]
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0

    print(f"{stats['total']} event(s)")
    for key, value in stats["by_event_type"].items():
        if value:
            print(f"  {key:<20} {value}")
    if not args.summary:
        for event in events:
            detail = event.command or event.file_path or event.url or event.tool_name or ""
            print(
                f"  {event.ts or '-':<28} {event.event_type:<18} "
                f"{event.confidence:<9} {str(detail)[:80]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
