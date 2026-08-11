# CUI // SP-CTI
"""The pre-tool-use decision seam for AGOV detection (agov-det-06).

Wires the rule engine (agov-det-03/04) and the findings store (agov-det-05) into
the two places a tool call is checked: ``.claude/hooks/pre_tool_use.py`` and
``tools/airgap/hook_compat.py::run_pre_tool_check``. Both reach it through
``tools/hooks/shared_checks.py::check_agent_rules``, so the interactive and the
headless path cannot drift (hgx-guard-01).

This is an ADDITIONAL check. Every hardcoded block that shipped before AGOV —
``.env`` access, ``rm -rf``, append-only table modification, direct ``sqlite3``,
the file access tiers, branch deletion, the worktree path — runs FIRST and is
untouched. Those are the load-bearing protections; this gate can only add a
refusal, never remove one, and it fails OPEN on any internal error because a
detection engine that can wedge the hook is worse than the detections it makes.

ENFORCEMENT AUTHORITY IS A DIRECTORY, NOT A FLAG
------------------------------------------------
A rule blocks only when it sets ``enforce: true`` AND its file lives in the
operator-controlled directory (``ICDEV_AGENT_ENFORCE_RULES_DIR``, defaulting to
``args/agent_rules_enforce/``, which ships with no rule files in it). Rules from
the shipped pack under ``args/agent_rules/`` are loaded into a separate ruleset
whose matches are forced monitor-only, so flipping ``enforce: true`` on a shipped
file — by a bad merge, a rogue edit or a well-meaning PR — cannot block anything.
Authority is therefore a property of WHERE the file is, which an operator
controls, not of a field inside it, which anyone editing the repo controls.

Detection and enforcement evaluate the SAME matcher: both directories go through
``rules.evaluate_event`` / ``sequence.evaluate_sequence``. There is no second,
looser expression path that enforcement could disagree with.

LATENCY IS A HARD CONSTRAINT
----------------------------
This runs before every tool call, in a fresh interpreter each time, and the hook
was already the slowest thing in a build (see the circuit breaker in
``.claude/hooks/send_event.py``). Three things keep it cheap:

1. **Nothing first-party is imported at module scope.** ``import tools`` executes
   the compatibility shim, which pulls in ``icdev.tools.llm.router`` — 92ms
   measured — and ``tools.logging.icdev_logger`` costs a further 43ms. The hook
   already loads ``shared_checks`` by path for exactly this reason. When this
   module is loaded by path into a bare interpreter, :func:`_engine` registers a
   plain namespace package for ``tools`` and a stdlib-backed ``get_logger``
   before importing the engine, so neither cost is paid. In a process that has
   already imported ``tools`` (tests, the dashboard, any CLI) the bootstrap is a
   no-op and the real modules are used.
2. **The zero-rule fast path never imports the engine at all.** Counting rule
   files is one ``os.walk`` over two small directories. With no rules configured
   the gate returns in well under a millisecond.
3. **The sequence pass is skipped entirely unless a sequence rule is loaded.**
   Only then is the session trail written or read, and the trail is a bounded
   local JSONL tail — never a database scan.

Findings persistence is the one unbounded-ish cost, and it only happens on a
MATCH, which is the rare path. The decision is computed before the write, so a
slow database can delay a tool call but can never change whether it is allowed.
"""
from __future__ import annotations

# Module scope is kept to imports the hook process has ALREADY paid for. The
# three that are not on this list are the reason: `logging` costs 19ms cold,
# `tempfile` 10ms (it pulls in shutil), and both would be paid before every tool
# call for a module that logs almost nothing and touches the temp directory only
# when a chain rule is loaded. They are imported inside the functions that need
# them; `_HookLogger` below replaces the first outright.
import json
import os
import re
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --- Configuration ---------------------------------------------------------
#: Master switch. Set to 0/false/no to take the gate out of the path entirely.
ENABLE_ENV = "ICDEV_AGENT_DETECT"

#: The operator-controlled directory. Rules HERE may block; rules anywhere else
#: are monitor-only no matter what their `enforce` field says.
ENFORCE_DIR_ENV = "ICDEV_AGENT_ENFORCE_RULES_DIR"
ENFORCE_DIRNAME = "agent_rules_enforce"

#: Where the session event trail lives. Defaults under the OS temp dir; it is
#: scratch, is keyed by session, and losing it only costs chain detection.
TRAIL_DIR_ENV = "ICDEV_AGENT_DETECT_TRAIL_DIR"
TRAIL_DIRNAME = "icdev-agent-detect"

#: Trail bounds. `within_events: 200` is the largest window the shipped pack
#: asks for, so 256 lines covers it with headroom; the byte cap bounds the tail
#: read regardless of how long any single command was.
TRAIL_MAX_EVENTS = 256
TRAIL_TAIL_BYTES = 256 * 1024
TRAIL_MAX_BYTES = 1024 * 1024

#: Recorded on every finding so a reviewer can tell a hook observation from an
#: offline scan of the same events.
DEFAULT_SOURCE = "pre_tool_use"

_FALSE = {"0", "false", "no", "off"}

#: Tool name (lowercased) -> normalized event_type. Unlisted tools still produce
#: an event, typed `tool.call`, so a rule can be written against a tool this map
#: has never heard of rather than the call being invisible.
_TOOL_EVENT_TYPES = {
    "read": "file.read",
    "notebookread": "file.read",
    "write": "file.write",
    "edit": "file.write",
    "multiedit": "file.write",
    "notebookedit": "file.write",
    "bash": "command.exec",
    "powershell": "command.exec",
    "shell": "command.exec",
    "sql": "command.exec",
    "webfetch": "network.fetch",
    "websearch": "network.fetch",
}

#: First URL in a shell command, so `url_matches` works on `curl https://…`
#: without a shell parser. Stops at whitespace and at the shell metacharacters
#: that cannot appear inside a bare URL argument.
_URL_RE = re.compile(r"https?://[^\s'\"<>|;)]+")

#: Keys a tool payload may carry a path under, most specific first.
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")
_COMMAND_KEYS = ("command", "query", "sql")


class _Unset:
    pass


_UNSET = _Unset()
_ENGINE: Any = None


# --- Result ----------------------------------------------------------------
@dataclass(frozen=True)
class GateDecision:
    """What the gate concluded about one tool call.

    ``allowed`` is the only field the hook acts on. Everything else exists so a
    test, the CLI (agov-det-07) and a reviewer can see WHY, including in the
    overwhelmingly common case where the answer is "nothing matched".
    """

    allowed: bool = True
    reason: str = ""
    rule_id: str = ""
    #: Every rule that matched, monitor-only ones included, as plain dicts.
    matches: tuple = ()
    #: The result of each :func:`findings.record_finding` call.
    findings: tuple = ()
    #: Set when the gate short-circuited: ``disabled``, ``no rules``, ``error``.
    skipped: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "matches": list(self.matches),
            "findings": list(self.findings),
            "skipped": self.skipped,
        }


ALLOW = GateDecision()


# --- Environment -----------------------------------------------------------
def _off(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _FALSE


def repo_root() -> Path:
    """The repository root, from ``__file__`` and never ``os.getcwd()``.

    ``tools/agent_detect/gate.py`` -> the repo root; the same file under
    ``icdev/`` -> the packaged root. Walk up looking for ``args`` rather than
    hardcoding a parent index for each copy (CLAUDE.md, worktree notes).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "args").is_dir():
            return parent
    return here.parents[2]


def enforce_rules_dir(root: Optional[Path] = None) -> Optional[Path]:
    """The operator-controlled directory, or None when it does not exist."""
    override = os.environ.get(ENFORCE_DIR_ENV)
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    path = (root or repo_root()) / "args" / ENFORCE_DIRNAME
    return path if path.is_dir() else None


def _probable_detect_dir(root: Path) -> Optional[Path]:
    """Where the detect pack most likely is, WITHOUT importing the engine.

    Mirrors ``rules.default_rules_dir`` for the only question the fast path asks
    — "are there any rule files at all". The engine re-resolves it authoritatively
    a few lines later, so a disagreement can only cost a needless engine import,
    never a missed rule.
    """
    override = os.environ.get("ICDEV_AGENT_RULES_DIR")
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    path = root / "args" / "agent_rules"
    return path if path.is_dir() else None


def _count_rule_files(directory: Optional[Path]) -> int:
    """How many ``*.yaml``/``*.yml`` files live under ``directory``.

    The zero-rule fast path: stdlib only, no engine import, no YAML parse. An
    unreadable directory counts as zero — the loader reports the real error on
    the paths where it matters, and the hook must not stall on a bad mount.
    """
    if directory is None:
        return 0
    count = 0
    try:
        for _dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                if name.endswith((".yaml", ".yml")):
                    count += 1
    except OSError:
        return 0
    return count


# --- Engine loading --------------------------------------------------------
class _HookLogger:
    """The logger the engine gets inside the pre-tool-use hook.

    Stands in for ``tools.logging.icdev_logger.get_logger``, which costs 43ms of
    rotating file handlers, in a process that lives for one tool call — and for
    ``logging`` itself, which costs 19ms cold.

    Not a null logger. ``warning``/``error`` still reach stderr, because the
    messages that come through here are "your rule file is broken" and "this
    finding was not recorded", and losing those to a latency optimisation is how
    a detection engine quietly stops detecting. ``debug``/``info`` are dropped:
    the hook has no log file to put them in and stderr is read by a human.

    ONE LINE, CAPPED. In the hook, stderr is the same channel the deny message
    goes out on and is read by an agent. A PostgreSQL ``CheckViolation`` arrives
    here as six lines including the failing row, and a warning that buries the
    refusal underneath a wall of database internals trains its readers to skip
    the channel — which costs more than the detail was worth.
    """

    __slots__ = ("name",)

    #: Enough for the message and the first clause of an exception repr.
    MAX_CHARS = 220

    def __init__(self, name: str = "") -> None:
        self.name = name

    def _emit(self, level: str, message: str, args: tuple) -> None:
        try:
            text = message % args if args else message
        except Exception:  # noqa: BLE001 — a bad format string is not worth a crash
            text = f"{message} {args!r}"
        text = " ".join(str(text).split())
        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS - 1] + "…"
        try:
            sys.stderr.write(f"[agent_detect:{level}] {text}\n")
        except Exception:  # noqa: BLE001 — a closed stderr must not break the hook
            pass

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    info = debug

    def warning(self, message: str = "", *args: Any, **_kwargs: Any) -> None:
        self._emit("warning", message, args)

    def error(self, message: str = "", *args: Any, **_kwargs: Any) -> None:
        self._emit("error", message, args)

    exception = error
    critical = error


def _temp_root() -> Path:
    """The system temp directory, preferring the environment over ``tempfile``.

    A deliberate, measured deviation from the CLAUDE.md "use
    ``tempfile.gettempdir()``" guardrail on this one path: importing ``tempfile``
    costs 10ms (it pulls in ``shutil``) before every tool call. The env lookup is
    the same lookup ``gettempdir`` does first, and ``tempfile`` is still imported
    as the fallback when none of the variables are set, so the answer is
    identical — only the cost differs.
    """
    for name in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    import tempfile  # noqa: PLC0415 — see docstring

    return Path(tempfile.gettempdir())


def _bootstrap_tools_namespace() -> None:
    """Make ``tools.*`` importable without executing the compatibility shim.

    A no-op unless ``tools`` is absent from ``sys.modules`` — i.e. unless this
    module was loaded by path into a bare interpreter, which is what the Claude
    Code hook does. There it registers ``tools`` as a plain namespace package
    over the real directory, so ``tools.agent_detect.rules`` and
    ``tools.db.storage`` resolve to the real files, and pre-registers
    ``tools.logging.icdev_logger`` with the stdlib ``logging.getLogger``.

    Both substitutions are cost, not behaviour: the shim only exists to redirect
    ``tools.x`` to ``icdev.tools.x`` (92ms, via ``icdev.tools.llm.router``), and
    the NDJSON logger's rotating file handlers (43ms) have nothing to do in a
    process that lives for one tool call and writes only to stderr.
    """
    if "tools" in sys.modules:
        return
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(root / "tools")]  # type: ignore[attr-defined]
    pkg.__package__ = "tools"
    sys.modules["tools"] = pkg

    log_pkg = types.ModuleType("tools.logging")
    log_pkg.__path__ = [str(root / "tools" / "logging")]  # type: ignore[attr-defined]
    log_pkg.__package__ = "tools.logging"
    log_mod = types.ModuleType("tools.logging.icdev_logger")
    log_mod.get_logger = _HookLogger  # type: ignore[attr-defined]
    log_pkg.get_logger = _HookLogger  # type: ignore[attr-defined]
    log_pkg.icdev_logger = log_mod  # type: ignore[attr-defined]
    sys.modules["tools.logging"] = log_pkg
    sys.modules["tools.logging.icdev_logger"] = log_mod


def _engine():
    """``(rules, sequence_module_or_None)``, imported once per process.

    ``sequence`` is imported lazily by :func:`_sequence_module` — 8ms measured —
    because a deployment with no chain rule must not pay for the chain evaluator.
    """
    global _ENGINE
    if _ENGINE is None:
        _bootstrap_tools_namespace()
        from tools.agent_detect import rules as _rules

        _ENGINE = _rules
    return _ENGINE


_SEQUENCE: Any = _UNSET


def _sequence_module():
    global _SEQUENCE
    if isinstance(_SEQUENCE, _Unset):
        _SEQUENCE = None
        try:
            _bootstrap_tools_namespace()
            from tools.agent_detect import sequence as _sequence

            _SEQUENCE = _sequence
        except Exception:  # noqa: BLE001 — chain detection is optional, the gate is not
            _SEQUENCE = None
    return _SEQUENCE


def _findings_module():
    try:
        _bootstrap_tools_namespace()
        from tools.agent_detect import findings as _findings

        return _findings
    except Exception:  # noqa: BLE001
        return None


def reset() -> None:
    """Drop cached module handles. Tests only."""
    global _ENGINE, _SEQUENCE
    _ENGINE = None
    _SEQUENCE = _UNSET


# --- Event normalization ---------------------------------------------------
def _first(payload: Any, keys) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _new_event_id() -> str:
    """A random id. ``os.urandom`` rather than ``uuid`` — one fewer import on a
    path that runs before every tool call."""
    return os.urandom(8).hex()


def current_session_id() -> str:
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return "unknown"


def normalize_tool_call(
    tool_name: str,
    tool_input: Any,
    *,
    session_id: Optional[str] = None,
    actor: Optional[str] = None,
    project_id: Optional[str] = None,
    source: str = DEFAULT_SOURCE,
) -> dict:
    """One tool call as the event shape the rule engine reads.

    A stand-in for agov-det-01's ``AgentEvent``, which normalizes the same
    vocabulary out of the tables ICDEV already writes. The evaluator is
    duck-typed on purpose (``rules._get`` reads a Mapping or an object), so when
    ``events.py`` lands this function becomes a thin adapter to it and the rules
    do not change.

    ``file_path`` is left exactly as the tool gave it. The globs in the pack are
    written against a full path (``**/.env``), and the hook receives absolute
    paths; rewriting to a repo-relative form here would silently stop those
    patterns matching.
    """
    payload = tool_input if isinstance(tool_input, dict) else {}
    name = str(tool_name or "")
    command = _first(payload, _COMMAND_KEYS)
    url = _first(payload, ("url",))
    if not url and command:
        found = _URL_RE.search(command)
        if found:
            url = found.group(0)

    return {
        "event_id": _new_event_id(),
        "event_type": _TOOL_EVENT_TYPES.get(name.lower(), "tool.call"),
        "tool_name": name,
        "actor": actor or os.environ.get("ICDEV_AGENT_ACTOR") or "agent",
        "session_id": session_id or current_session_id(),
        "project_id": project_id or os.environ.get("ICDEV_PROJECT_ID") or "",
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
        "file_path": _first(payload, _PATH_KEYS),
        "command": command,
        "url": url,
    }


# --- Session trail ---------------------------------------------------------
def trail_dir() -> Path:
    override = os.environ.get(TRAIL_DIR_ENV)
    return Path(override) if override else _temp_root() / TRAIL_DIRNAME


_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _trail_path(session_id: str) -> Path:
    safe = _UNSAFE_SESSION_CHARS.sub("_", str(session_id or "unknown"))[:120]
    return trail_dir() / (safe + ".jsonl")


def append_trail(event: dict) -> None:
    """Append one event to its session trail. Never raises.

    Only called when a sequence rule is loaded — with no chain rule there is no
    consumer, and writing a file nobody reads on every tool call is exactly the
    kind of cost this hook cannot afford.
    """
    path = _trail_path(event.get("session_id", ""))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate-and-keep-the-tail rather than grow without bound. Rare: one
        # rewrite per megabyte of trail, and the tail is all any window reads.
        try:
            if path.stat().st_size > TRAIL_MAX_BYTES:
                kept = read_trail(event.get("session_id", ""), TRAIL_MAX_EVENTS)
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    for prior in kept:
                        handle.write(json.dumps(prior, separators=(",", ":")) + "\n")
        except OSError:
            pass
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        return


def read_trail(session_id: str, limit: int = TRAIL_MAX_EVENTS) -> list:
    """The last ``limit`` events of this session's trail, oldest first.

    Bounded twice: it seeks to the last :data:`TRAIL_TAIL_BYTES` rather than
    reading the file, then keeps at most ``limit`` lines. Returns ``[]`` on any
    error — a missing trail means no chain, not a broken gate.
    """
    path = _trail_path(session_id)
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TRAIL_TAIL_BYTES))
            chunk = handle.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if lines and size > TRAIL_TAIL_BYTES:
        # The first line of a mid-file seek is a fragment.
        lines = lines[1:]
    events = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


# --- Evaluation ------------------------------------------------------------
def _rule_mapping(rule: Any) -> dict:
    """A compiled :class:`rules.Rule` back in the raw shape ``evaluate_sequence``
    reads. It takes the mapping, not the compiled object, so that an offline
    scan can hand it YAML straight from disk."""
    return {
        "id": rule.rule_id,
        "version": rule.version,
        "title": rule.title,
        "severity": rule.severity,
        "tags": list(rule.tags),
        "enabled": rule.enabled,
        "enforce": rule.enforce,
        "sequence": rule.sequence,
    }


#: Most severe first, so the message a blocked caller sees is the worst finding
#: rather than whichever rule id sorted first.
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _candidate_key(candidate: dict) -> tuple:
    return (_SEVERITY_ORDER.get(candidate.get("severity", ""), 9), candidate.get("rule_id", ""))


def _collect_event_matches(engine, ruleset, event: dict, enforceable: bool) -> list:
    out = []
    for hit in engine.evaluate_event(event, ruleset):
        row = hit.to_dict()
        row["kind"] = "expr"
        row["event_ids"] = [event.get("event_id")]
        # AUTHORITY IS THE DIRECTORY. A shipped-pack rule carrying
        # `enforce: true` arrives here with enforceable=False and is recorded
        # monitor-only; it never reaches the blocking branch below.
        row["enforce"] = bool(row.get("enforce")) and enforceable
        out.append(row)
    return out


def _completes_a_chain(engine, rule, event: dict) -> bool:
    """Could THIS event be the final step of ``rule``'s chain?

    The prefilter that keeps the chain pass affordable. Only chains containing
    the current event are ever reported (see the filter below), and the current
    event is the newest in the trail, so it sorts last inside its partition and
    can only ever be the chain's LAST element. Therefore a rule whose final step
    does not match this event cannot newly complete on this call, and the whole
    O(history x steps) search for it can be skipped — one matcher evaluation
    instead of a walk over a 256-event trail, per rule.

    Uses ``rules.compile_matcher``/``rules.matches``, which is the same matcher
    ``sequence.default_step_matcher`` resolves to, so the prefilter and the
    search cannot disagree about what a step matches.

    The one assumption: trail order tracks time. A clock that jumps backwards
    mid-session can make an older event sort after this one, and the chain is
    then missed. That is the same assumption the "report it once" filter below
    already makes, and a missed monitor-only finding is the safe direction.
    """
    try:
        steps = (rule.sequence or {}).get("steps") or []
        if not steps:
            return False
        return engine.matches(engine.compile_matcher(steps[-1]), event)
    except Exception:  # noqa: BLE001 — an unusable spec is the evaluator's to report
        return True


def _collect_sequence_matches(engine, ruleset, event: dict, enforceable: bool) -> list:
    candidates_rules = [r for r in ruleset.sequence_rules if _completes_a_chain(engine, r, event)]
    if not candidates_rules:
        return []

    # Trail second, chain evaluator third. Every chain is at least two steps, so
    # a session whose trail holds fewer than two events cannot complete one — and
    # must not pay the 5ms to import the evaluator to find that out.
    history = read_trail(event.get("session_id", ""), TRAIL_MAX_EVENTS)
    if len(history) < 2:
        return []
    sequence = _sequence_module()
    if sequence is None:
        return []

    out = []
    for rule in candidates_rules:
        try:
            found = sequence.evaluate_sequence(_rule_mapping(rule), history)
        except Exception:  # noqa: BLE001 — one bad chain must not blind the rest
            continue
        for finding in found:
            if event.get("event_id") not in finding.event_ids:
                # The chain completed on an earlier call and was already
                # recorded then. Re-reporting it on every subsequent tool call
                # would bury the live signal.
                continue
            row = finding.to_dict()
            row["kind"] = "sequence"
            row["rule_version"] = row.get("rule_version") or rule.version
            row["deny_message"] = rule.deny_message
            row["enforce"] = bool(rule.enforce) and enforceable
            out.append(row)
    return out


def _record(candidates: list, blocking_id: str) -> tuple:
    """Append every match to ``agent_findings``. Never raises.

    Runs AFTER the decision is made, so a slow or unreachable database delays
    the tool call but cannot change whether it was allowed.
    """
    findings = _findings_module()
    if findings is None:
        return ()
    results = []
    for candidate in candidates:
        denied = candidate.get("rule_id") == blocking_id
        try:
            results.append(
                findings.record(
                    rule_id=candidate.get("rule_id", ""),
                    rule_version=candidate.get("rule_version") or "1",
                    severity=candidate.get("severity") or "info",
                    title=candidate.get("title") or "",
                    session_id=candidate.get("session_id") or "",
                    actor=candidate.get("actor") or "",
                    project_id=candidate.get("project_id") or "",
                    event_ids=candidate.get("event_ids") or [],
                    tags=candidate.get("tags") or [],
                    enforced=denied,
                    decision="denied" if denied else "observed",
                )
            )
        except Exception:  # noqa: BLE001 — an unrecorded finding is logged inside
            continue
    return tuple(results)


def evaluate_tool_call(
    tool_name: str,
    tool_input: Any,
    *,
    session_id: Optional[str] = None,
    actor: Optional[str] = None,
    project_id: Optional[str] = None,
    source: str = DEFAULT_SOURCE,
    root: Optional[Path] = None,
    record: bool = True,
) -> GateDecision:
    """Evaluate one tool call against the loaded rules.

    Never raises: every failure path returns an ALLOW carrying ``skipped``. This
    check is additive, so failing open leaves the caller exactly as protected as
    it was before AGOV — whereas failing closed would let a malformed rule file
    stop an agent from doing anything at all.
    """
    if _off(ENABLE_ENV):
        return GateDecision(skipped="disabled")

    try:
        base = root or repo_root()
        enforce_dir = enforce_rules_dir(base)

        # ZERO-RULE FAST PATH. Two os.walks over two small directories decide it:
        # no rule files anywhere means no possible finding and no possible block,
        # so the engine is never imported and no YAML is ever parsed. This is the
        # state a default install is in for the enforce directory, and the state
        # an operator who deletes the seed pack is in for both.
        if _count_rule_files(_probable_detect_dir(base)) == 0 and _count_rule_files(enforce_dir) == 0:
            return GateDecision(skipped="no rules")

        engine = _engine()
        # Resolved by the loader, which owns ICDEV_AGENT_RULES_DIR. The cheap
        # guess above only answers "is there anything at all"; this is the
        # authoritative answer, so the two cannot disagree about what is loaded.
        detect_dir = engine.default_rules_dir()
        # The monitor pack goes through the JSON side-cache (~25ms saved per
        # tool call: no PyYAML import, no YAML parse). The operator directory
        # below deliberately does NOT — no decision to BLOCK is ever taken from
        # a cached document. See the cache's own note in rules.py.
        detect_set = engine.load_rules_fast(detect_dir) if detect_dir else engine.RuleSet()
        # One directory serving both roles is an operator pointing the enforce
        # env at their detect pack; evaluate it once, with authority.
        same_dir = bool(
            enforce_dir and detect_dir and Path(enforce_dir).resolve() == Path(detect_dir).resolve()
        )
        enforce_set = (
            engine.load_rules(enforce_dir) if (enforce_dir and not same_dir) else None
        )

        event = normalize_tool_call(
            tool_name,
            tool_input,
            session_id=session_id,
            actor=actor,
            project_id=project_id,
            source=source,
        )

        candidates: list = []
        candidates += _collect_event_matches(engine, detect_set, event, same_dir)
        if enforce_set is not None:
            candidates += _collect_event_matches(engine, enforce_set, event, True)

        # THE SEQUENCE PASS IS SKIPPED ENTIRELY WHEN NO CHAIN RULE IS LOADED.
        # No trail write, no trail read, no `sequence` import — the whole 8ms
        # module plus its file I/O simply does not happen.
        has_sequence = bool(detect_set.sequence_rules) or bool(
            enforce_set.sequence_rules if enforce_set is not None else ()
        )
        if has_sequence:
            append_trail(event)
            candidates += _collect_sequence_matches(engine, detect_set, event, same_dir)
            if enforce_set is not None:
                candidates += _collect_sequence_matches(engine, enforce_set, event, True)

        if not candidates:
            return ALLOW

        candidates.sort(key=_candidate_key)
        blocker = next((c for c in candidates if c.get("enforce")), None)
        reason = ""
        blocking_id = ""
        if blocker is not None:
            blocking_id = blocker.get("rule_id", "")
            message = (blocker.get("deny_message") or "").strip()
            reason = "BLOCKED: " + (
                message or f"agent rule {blocking_id} denied this action."
            )

        recorded = _record(candidates, blocking_id) if record else ()
        return GateDecision(
            allowed=blocker is None,
            reason=reason,
            rule_id=blocking_id,
            matches=tuple(candidates),
            findings=recorded,
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        return GateDecision(skipped=f"error: {type(exc).__name__}: {exc}")


def check_tool_call(tool_name: str, tool_input: Any, **kwargs: Any) -> Optional[str]:
    """The shape ``shared_checks`` speaks: a deny reason, or None to allow."""
    decision = evaluate_tool_call(tool_name, tool_input, **kwargs)
    return decision.reason if not decision.allowed else None


__all__ = [
    "ALLOW",
    "ENABLE_ENV",
    "ENFORCE_DIRNAME",
    "ENFORCE_DIR_ENV",
    "GateDecision",
    "TRAIL_DIR_ENV",
    "append_trail",
    "check_tool_call",
    "enforce_rules_dir",
    "evaluate_tool_call",
    "normalize_tool_call",
    "read_trail",
    "repo_root",
    "reset",
    "trail_dir",
]
