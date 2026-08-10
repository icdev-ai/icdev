# CUI // SP-CTI
"""YAML rule loader + single-event evaluator for AGOV detection (agov-det-03).

One rule per file under ``args/agent_rules/**/*.yaml``. A rule declares its
identity (``id`` / ``version`` / ``title`` / ``severity`` / ``tags``), whether it
is live (``enabled``, default true), whether it may BLOCK (``enforce``, default
**false**), the message shown when it does (``deny_message``), and exactly one
condition — ``expr`` (single event, here) or ``sequence`` (a chain, agov-det-04).

Monitor-only by default is the whole safety story. A rule pack that blocked on
install would take down live sessions on its first false positive, so
``enforce`` defaults to False and the shipped pack sets it explicitly false;
opting a rule in is an operator action (agov-det-06).

CONDITIONS ARE STRUCTURED YAML MATCHERS, NOT AN EXPRESSION LANGUAGE
-------------------------------------------------------------------
Decided against CEL — a new third-party dependency is not acceptable in an
air-gap/DoD-oriented repo — and against a restricted-AST evaluator, which is a
security surface ICDEV would own and have to defend forever. What is left is the
shape ``args/agent_approval_policy.yaml`` and ``args/file_access_tiers.yaml``
already prove: named keys, literal or regex values, no evaluation of operator
text as code.

::

    id: secrets.env_read_by_bash
    version: 1
    title: Shell read of a dotenv file
    severity: high
    tags: [T1552.001]
    enforce: false
    deny_message: "Reading .env from a shell is blocked."
    expr:
      event_type: [command.exec]
      command_name: [cat, head, tail]
      argv_contains: [".env"]
      not_actor: [ci]

Semantics, stated once so a rule author cannot guess wrong:

  * Keys within ``expr`` **AND** together — every key must match.
  * The list under one key **OR**s — any element matching satisfies that key.
  * A scalar is sugar for a one-element list.
  * ``not_<key>`` is true exactly when the positive form of that key does not
    match. It is a separate key, so it ANDs with the rest like any other.

Matcher keys, and what each reads off the event:

  ``event_type``      exact, against :class:`AgentEvent.event_type` (agov-det-01)
  ``tool_name``       exact, case-insensitive
  ``actor``           exact, case-insensitive
  ``file_path_glob``  fnmatch against ``file_path`` and against its basename
  ``command_name``    the PARSED command name (agov-det-02), case-insensitive
  ``command_matches`` regex search against the raw ``command`` string
  ``url_matches``     regex search against ``url``
  ``argv_contains``   exact membership in the PARSED argv (agov-det-02)

FAIL SAFE, NOT OPEN
-------------------
An unknown matcher key, an uncompilable regex, a bad severity, both ``expr`` and
``sequence``, or an empty ``expr`` makes the WHOLE RULE invalid: it is skipped,
recorded in :attr:`RuleSet.errors` with its file path, and logged. It is never
degraded into a partial matcher and never treated as match-all — dropping only
the offending key would silently LOOSEN the surviving AND, which is precisely
how a detection rule becomes a match-everything rule. An empty ``expr`` is
rejected for the same reason. An absent or empty rules directory yields an empty
RuleSet, not an error.

``command_name`` and ``argv_contains`` require the parsed view and do NOT fall
back to substring matching on the raw command. That fallback is the documented
fail-open agov-det-02 exists to fix: ``args/agent_approval_policy.yaml``:107-126
records a ``git_push`` carrying ``{"note": "mkdir logs"}`` matching the ``mkdir``
downgrade pattern, because patterns ran against a flattened string. A rule that
needs parsed semantics simply does not fire on a command that did not parse.

The one asymmetry worth naming: ``not_command_name`` on an unparsed command is
TRUE, because the positive form did not match. That direction is safe — it can
only produce an extra finding for a human to read, never suppress one.

Usage::

    from tools.agent_detect.rules import load_rules, evaluate_event

    ruleset = load_rules()                       # args/agent_rules/, memoized
    for hit in evaluate_event(event, ruleset):   # never raises
        ...

The operator CLI (``--list`` / ``--check`` / ``--scan``) is agov-det-07;
persistence of findings to ``agent_findings`` is agov-det-05.
"""
from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_detect.rules")

# --- Rule vocabulary -------------------------------------------------------
SEVERITIES = ("info", "low", "medium", "high", "critical")

#: Matcher keys, in the order they are evaluated. Cheap exact-match keys first
#: so an expensive regex or a shell parse is only reached by a candidate that
#: already cleared the literals.
POSITIVE_KEYS = (
    "event_type",
    "tool_name",
    "actor",
    "file_path_glob",
    "command_matches",
    "url_matches",
    "command_name",
    "argv_contains",
)
NEGATED_KEYS = tuple("not_" + key for key in POSITIVE_KEYS)
MATCHER_KEYS = frozenset(POSITIVE_KEYS + NEGATED_KEYS)

#: Values under these keys are regular expressions and are compiled at load.
REGEX_KEYS = frozenset({"command_matches", "url_matches"})

#: These read the agov-det-02 parsed view and never fall back to the raw string.
PARSED_KEYS = frozenset({"command_name", "argv_contains"})

#: A deny message is rendered into a hook refusal; keep it a message, not a doc.
DENY_MESSAGE_MAX_BYTES = 512

#: ``secrets.env_read`` — a namespace and a name, so rules group and sort.
RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")

RULES_DIR_ENV = "ICDEV_AGENT_RULES_DIR"
RULES_DIRNAME = "agent_rules"
RULE_SUFFIXES = (".yaml", ".yml")


class RuleSpecError(ValueError):
    """A rule file is malformed. Raised internally; the loader turns it into a
    :class:`RuleError` so one bad file can never crash the caller."""


# --- Compiled shapes -------------------------------------------------------
@dataclass(frozen=True)
class Clause:
    """One matcher key from a rule, compiled.

    ``values`` are literals (or the regex source strings, kept for diagnostics);
    ``patterns`` is populated only for :data:`REGEX_KEYS`.
    """

    key: str                                    # positive base key
    negated: bool
    values: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...] = ()

    @property
    def declared_key(self) -> str:
        return ("not_" + self.key) if self.negated else self.key


@dataclass(frozen=True)
class Matcher:
    """A compiled ``expr``: clauses that AND together."""

    clauses: tuple[Clause, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(c.declared_key for c in self.clauses)

    @property
    def needs_parsed_command(self) -> bool:
        return any(c.key in PARSED_KEYS for c in self.clauses)


@dataclass(frozen=True)
class Rule:
    """One loaded, validated rule."""

    rule_id: str
    version: str
    title: str
    severity: str
    tags: tuple[str, ...] = ()
    enabled: bool = True
    enforce: bool = False
    deny_message: str = ""
    expr: Optional[Matcher] = None
    #: Raw ``sequence`` block, validated only for shape here. The chain
    #: evaluator that consumes it is agov-det-04.
    sequence: Optional[dict[str, Any]] = None
    source_path: str = ""

    @property
    def is_sequence(self) -> bool:
        return self.sequence is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "title": self.title,
            "severity": self.severity,
            "tags": list(self.tags),
            "enabled": self.enabled,
            "enforce": self.enforce,
            "deny_message": self.deny_message,
            "kind": "sequence" if self.is_sequence else "expr",
            "keys": list(self.expr.keys) if self.expr else [],
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class RuleError:
    """A rule file that was skipped, and why. Always carries its path."""

    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class RuleSet:
    """Everything one rules directory yielded — the good and the skipped."""

    rules: tuple[Rule, ...] = ()
    errors: tuple[RuleError, ...] = ()
    directory: Optional[str] = None

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def by_id(self) -> dict[str, Rule]:
        return {r.rule_id: r for r in self.rules}

    @property
    def enabled_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.enabled)

    @property
    def event_rules(self) -> tuple[Rule, ...]:
        """Enabled single-event rules — what :func:`evaluate_event` runs."""
        return tuple(r for r in self.enabled_rules if r.expr is not None)

    @property
    def sequence_rules(self) -> tuple[Rule, ...]:
        """Enabled chain rules. agov-det-04 consumes these; skip the whole
        chain pass when this is empty (the hook is latency-critical)."""
        return tuple(r for r in self.enabled_rules if r.is_sequence)


@dataclass(frozen=True)
class RuleMatch:
    """A rule matched one event. NOT proof the action executed."""

    rule_id: str
    rule_version: str
    severity: str
    title: str
    tags: tuple[str, ...] = ()
    enforce: bool = False
    deny_message: str = ""
    event_id: Optional[str] = None
    session_id: Optional[str] = None
    actor: Optional[str] = None
    matched_keys: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "title": self.title,
            "tags": list(self.tags),
            "enforce": self.enforce,
            "deny_message": self.deny_message,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "actor": self.actor,
            "matched_keys": list(self.matched_keys),
        }


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------
def _as_value_list(raw: Any, key: str) -> tuple[str, ...]:
    """Normalize a matcher value to a tuple of strings. Scalar is one element."""
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        items: list[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        items = list(raw)
    else:
        raise RuleSpecError(
            f"matcher key {key!r} takes a string or a list of strings, "
            f"got {type(raw).__name__}"
        )
    if not items:
        # An empty OR-list has no honest reading: vacuously false makes the rule
        # dead, vacuously true makes it match-all. Refuse rather than choose.
        raise RuleSpecError(f"matcher key {key!r} has an empty value list")
    out: list[str] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise RuleSpecError(
                f"matcher key {key!r} has a non-string value {item!r}"
            )
        text = str(item)
        if not text:
            raise RuleSpecError(f"matcher key {key!r} has an empty value")
        out.append(text)
    return tuple(out)


def compile_matcher(raw: Any) -> Matcher:
    """Compile an ``expr`` block. Raises :class:`RuleSpecError` if anything is
    off — the caller skips the whole rule rather than dropping a key."""
    if not isinstance(raw, Mapping):
        raise RuleSpecError(f"expr must be a mapping, got {type(raw).__name__}")
    if not raw:
        # Zero clauses AND together to True, i.e. match every event. That is the
        # one thing a detection rule must never do by accident.
        raise RuleSpecError("expr is empty; an empty matcher would match every event")

    clauses: list[Clause] = []
    for declared_key, value in raw.items():
        key = str(declared_key)
        if key not in MATCHER_KEYS:
            raise RuleSpecError(
                f"unknown matcher key {key!r}; known keys: "
                + ", ".join(sorted(MATCHER_KEYS))
            )
        negated = key.startswith("not_")
        base = key[4:] if negated else key
        values = _as_value_list(value, key)
        patterns: tuple[re.Pattern[str], ...] = ()
        if base in REGEX_KEYS:
            compiled: list[re.Pattern[str]] = []
            for pattern in values:
                try:
                    compiled.append(re.compile(pattern, re.IGNORECASE))
                except re.error as exc:
                    raise RuleSpecError(
                        f"matcher key {key!r} has an uncompilable regex "
                        f"{pattern!r}: {exc}"
                    ) from exc
            patterns = tuple(compiled)
        clauses.append(Clause(key=base, negated=negated, values=values, patterns=patterns))

    order = {name: i for i, name in enumerate(POSITIVE_KEYS)}
    clauses.sort(key=lambda c: (order[c.key], c.negated))
    return Matcher(clauses=tuple(clauses))


def _validate_sequence(raw: Any) -> dict[str, Any]:
    """Shape-check a ``sequence`` block without evaluating it.

    The chain evaluator, its window semantics and its (session_id, agent,
    project_id, source) partitioning are agov-det-04. This exists so a
    sequence rule is not silently dropped by the loader in the meantime, and so
    it can never be mistaken for a single-event rule.
    """
    if not isinstance(raw, Mapping):
        raise RuleSpecError(f"sequence must be a mapping, got {type(raw).__name__}")
    steps = raw.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        raise RuleSpecError("sequence.steps must be a non-empty list of matchers")
    if raw.get("within") in (None, "") and raw.get("within_events") in (None, ""):
        raise RuleSpecError("sequence requires at least one of within / within_events")
    return dict(raw)


def _require_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise RuleSpecError(f"missing required field {key!r}")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RuleSpecError(f"field {key!r} must be text, got {type(value).__name__}")
    return str(value).strip()


def _require_bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if not isinstance(value, bool):
        # "no"/"false"/0 are truthy or falsy by accident in Python; an operator
        # who wrote one of those meant a boolean and must be told, not guessed at.
        raise RuleSpecError(
            f"field {key!r} must be a YAML boolean, got {type(value).__name__} {value!r}"
        )
    return value


def compile_rule(data: Any, source_path: str = "") -> Rule:
    """Validate and compile one rule mapping. Raises :class:`RuleSpecError`."""
    if not isinstance(data, Mapping):
        raise RuleSpecError(
            f"rule file must contain a mapping, got {type(data).__name__}"
        )

    rule_id = _require_text(data, "id")
    if not RULE_ID_RE.match(rule_id):
        raise RuleSpecError(
            f"id {rule_id!r} must be lowercase dot-separated with at least two "
            "segments, e.g. 'secrets.env_read'"
        )
    version = _require_text(data, "version")
    title = _require_text(data, "title")

    severity = _require_text(data, "severity").lower()
    if severity not in SEVERITIES:
        raise RuleSpecError(
            f"severity {severity!r} is not one of {', '.join(SEVERITIES)}"
        )

    raw_tags = data.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, Sequence):
        raise RuleSpecError("tags must be a list of strings")
    tags = tuple(str(t) for t in raw_tags if str(t))

    enabled = _require_bool(data, "enabled", True)
    enforce = _require_bool(data, "enforce", False)

    deny_message = data.get("deny_message") or ""
    if not isinstance(deny_message, str):
        raise RuleSpecError("deny_message must be a string")
    deny_message = deny_message.strip()
    if len(deny_message.encode("utf-8")) > DENY_MESSAGE_MAX_BYTES:
        raise RuleSpecError(
            f"deny_message is {len(deny_message.encode('utf-8'))} bytes; "
            f"the limit is {DENY_MESSAGE_MAX_BYTES}"
        )

    has_expr = data.get("expr") is not None
    has_sequence = data.get("sequence") is not None
    if has_expr and has_sequence:
        raise RuleSpecError(
            "a rule declares exactly one of 'expr' or 'sequence', not both"
        )
    if not has_expr and not has_sequence:
        raise RuleSpecError("a rule must declare either 'expr' or 'sequence'")

    expr = compile_matcher(data["expr"]) if has_expr else None
    sequence = _validate_sequence(data["sequence"]) if has_sequence else None

    return Rule(
        rule_id=rule_id,
        version=version,
        title=title,
        severity=severity,
        tags=tags,
        enabled=enabled,
        enforce=enforce,
        deny_message=deny_message,
        expr=expr,
        sequence=sequence,
        source_path=source_path,
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def default_rules_dir() -> Optional[Path]:
    """Locate the rules directory.

    Resolved from ``__file__``, never ``os.getcwd()`` — this module is imported
    from worktrees and from CI runners whose cwd is not the repo root (CLAUDE.md,
    "Notes for agents working from worktrees").
    """
    override = os.environ.get(RULES_DIR_ENV)
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    here = Path(__file__).resolve()
    # tools/agent_detect/ -> repo root, and icdev/tools/agent_detect/ -> the
    # packaged root: walk up rather than hardcoding a parent index for each copy.
    for parent in here.parents:
        candidate = parent / "args" / RULES_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


def discover_rule_files(rules_dir: Path) -> list[Path]:
    """Every ``*.yaml`` / ``*.yml`` under ``rules_dir``, sorted.

    Sorted so a duplicate id resolves the same way on every machine: the first
    path wins and the later one is reported, rather than whichever the
    filesystem happened to hand back first.
    """
    found: list[Path] = []
    for suffix in RULE_SUFFIXES:
        found.extend(p for p in rules_dir.rglob("*" + suffix) if p.is_file())
    return sorted(set(found), key=lambda p: p.as_posix())


def _dir_signature(rules_dir: Path) -> tuple[Any, ...]:
    """Stat signature of the rules tree, for the compiled-rule cache.

    The hook this feeds runs before every tool call (agov-det-06), so re-parsing
    a dozen YAML files per call is not acceptable; re-reading their stat is.
    Any stat failure yields a unique-ish signature so the cache misses rather
    than serving rules that may have changed.
    """
    entries: list[Any] = []
    for path in discover_rule_files(rules_dir):
        try:
            st = path.stat()
            entries.append((path.as_posix(), st.st_mtime_ns, st.st_size))
        except OSError as exc:  # noqa: PERF203 — per-file, and the miss is the point
            entries.append((path.as_posix(), repr(exc)))
    return tuple(entries)


_RULESET_CACHE: dict[str, tuple[tuple[Any, ...], RuleSet]] = {}


def load_rules(
    rules_dir: Optional[os.PathLike[str] | str] = None,
    *,
    refresh: bool = False,
) -> RuleSet:
    """Load every rule under ``rules_dir`` (default: ``args/agent_rules``).

    Never raises. A missing directory, an unreadable file, unavailable PyYAML or
    a malformed rule all produce a :class:`RuleSet` — empty or partial, with the
    reason and the offending path in :attr:`RuleSet.errors`.
    """
    path = Path(rules_dir) if rules_dir is not None else default_rules_dir()
    if path is None or not path.is_dir():
        # An operator directory that ships empty is the designed default state,
        # not a fault. Zero rules means zero findings.
        return RuleSet(directory=str(path) if path is not None else None)

    key = str(path.resolve())
    signature = _dir_signature(path)
    if not refresh:
        cached = _RULESET_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]

    try:
        import yaml
    except ImportError as exc:
        logger.warning("agent_detect.rules: PyYAML unavailable (%s); no rules loaded", exc)
        return RuleSet(
            errors=(RuleError(str(path), f"PyYAML unavailable: {exc}"),),
            directory=key,
        )

    rules: list[Rule] = []
    errors: list[RuleError] = []
    seen: dict[str, str] = {}

    for rule_file in discover_rule_files(path):
        display = rule_file.as_posix()
        try:
            data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the rest
            errors.append(RuleError(display, f"unreadable: {exc}"))
            logger.warning("agent_detect.rules: %s skipped — unreadable: %s", display, exc)
            continue
        if data is None:
            errors.append(RuleError(display, "empty rule file"))
            logger.warning("agent_detect.rules: %s skipped — empty rule file", display)
            continue
        try:
            rule = compile_rule(data, source_path=display)
        except RuleSpecError as exc:
            errors.append(RuleError(display, str(exc)))
            logger.warning("agent_detect.rules: %s skipped — %s", display, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — defensive; still never fatal
            errors.append(RuleError(display, f"unexpected error: {exc}"))
            logger.warning("agent_detect.rules: %s skipped — unexpected: %s", display, exc)
            continue
        if rule.rule_id in seen:
            message = f"duplicate rule id {rule.rule_id!r}, first defined in {seen[rule.rule_id]}"
            errors.append(RuleError(display, message))
            logger.warning("agent_detect.rules: %s skipped — %s", display, message)
            continue
        seen[rule.rule_id] = display
        rules.append(rule)

    ruleset = RuleSet(rules=tuple(rules), errors=tuple(errors), directory=key)
    _RULESET_CACHE[key] = (signature, ruleset)
    return ruleset


def clear_cache() -> None:
    """Drop the compiled-rule cache. Tests and long-lived daemons only."""
    _RULESET_CACHE.clear()


# ---------------------------------------------------------------------------
# Event field access
# ---------------------------------------------------------------------------
def _get(event: Any, name: str) -> Any:
    """Read one field off an AgentEvent, a dict, or anything attribute-shaped.

    Deliberately duck-typed: agov-det-01 owns the :class:`AgentEvent` dataclass,
    and binding this evaluator to that import would make the rule engine
    unusable on a plain dict — which is what fixtures, the CLI and the headless
    hook seam all have in hand.
    """
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _statement_view(node: Any) -> Optional[tuple[str, tuple[str, ...]]]:
    """One parsed statement -> (name, argv), or None when it did not parse."""
    if node is None:
        return None
    parsed_flag = _get(node, "parsed")
    if parsed_flag is False:
        return None
    name = _get(node, "name")
    if name is None:
        return None
    raw_argv = _get(node, "argv") or ()
    if isinstance(raw_argv, (str, bytes)) or not isinstance(raw_argv, Sequence):
        raw_argv = ()
    return str(name), tuple(str(a) for a in raw_argv)


_SHELL_PARSE_UNRESOLVED = object()
_shell_parse_fn: Any = _SHELL_PARSE_UNRESOLVED


def _lazy_shell_parse(command: str) -> Any:
    """Parse ``command`` with agov-det-02's parser if it is importable.

    The contract is that a caller attaches the parsed view to the event as
    ``parsed``. This fallback only covers the case where it did not — and if
    ``shell_parse`` is not present (it lands in a sibling task), it stays None
    forever, which makes parsed-view matchers simply not fire.
    """
    global _shell_parse_fn
    if _shell_parse_fn is _SHELL_PARSE_UNRESOLVED:
        _shell_parse_fn = None
        try:
            from tools.agent_detect import shell_parse  # noqa: PLC0415 — optional
            for candidate in ("parse_command", "parse"):
                fn = getattr(shell_parse, candidate, None)
                if callable(fn):
                    _shell_parse_fn = fn
                    break
        except Exception as exc:  # noqa: BLE001 — absence is the expected state
            logger.debug("agent_detect.rules: shell_parse unavailable: %s", exc)
    if _shell_parse_fn is None:
        return None
    try:
        return _shell_parse_fn(command)
    except Exception as exc:  # noqa: BLE001 — a parser fault must not fire a rule
        logger.debug("agent_detect.rules: shell_parse raised on %r: %s", command, exc)
        return None


def parsed_statements(event: Any) -> Optional[tuple[tuple[str, tuple[str, ...]], ...]]:
    """The parsed statements for this event, or None when unavailable.

    None is the load-bearing return: every parsed-view matcher treats it as "no
    match" rather than falling back to the flattened command string. A pipeline
    yields one entry per statement, so ``command_name: [curl]`` fires on
    ``cat x | curl -T - https://h`` — which is the point of parsing at all.
    """
    raw = _get(event, "parsed")
    if raw is None:
        command = _text(_get(event, "command"))
        if not command:
            return None
        raw = _lazy_shell_parse(command)
    if raw is None or raw is False:
        return None
    if _get(raw, "parsed") is False:
        return None

    nodes: list[Any]
    container = _get(raw, "statements")
    if container is not None:
        nodes = list(container) if isinstance(container, Sequence) and not isinstance(
            container, (str, bytes)
        ) else []
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, Mapping)):
        nodes = list(raw)
    else:
        nodes = [raw]

    out = [view for view in (_statement_view(n) for n in nodes) if view is not None]
    return tuple(out) if out else None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _glob_match(file_path: str, pattern: str) -> bool:
    """fnmatch against the whole path and against its basename.

    Same normalization as ``tools/hooks/shared_checks.py::_matches_tier``, so a
    pattern an operator already knows from ``args/file_access_tiers.yaml``
    behaves identically here.
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
        posixpath.basename(normalized), pattern
    )


def _clause_matches(clause: Clause, event: Any) -> bool:
    """Evaluate the POSITIVE form of a clause. Negation is applied by the caller."""
    key = clause.key

    if key == "event_type":
        actual = _text(_get(event, "event_type"))
        return bool(actual) and any(actual == v for v in clause.values)

    if key in ("tool_name", "actor"):
        actual = _text(_get(event, key)).lower()
        return bool(actual) and any(actual == v.lower() for v in clause.values)

    if key == "file_path_glob":
        actual = _text(_get(event, "file_path"))
        return any(_glob_match(actual, v) for v in clause.values)

    if key == "command_matches":
        actual = _text(_get(event, "command"))
        return bool(actual) and any(rx.search(actual) for rx in clause.patterns)

    if key == "url_matches":
        actual = _text(_get(event, "url"))
        return bool(actual) and any(rx.search(actual) for rx in clause.patterns)

    if key == "command_name":
        statements = parsed_statements(event)
        if not statements:
            return False
        wanted = {v.lower() for v in clause.values}
        return any(name.lower() in wanted for name, _argv in statements)

    if key == "argv_contains":
        statements = parsed_statements(event)
        if not statements:
            return False
        return any(
            value in argv for _name, argv in statements for value in clause.values
        )

    # Unreachable: compile_matcher rejects any key not in MATCHER_KEYS. Fail to
    # NOT-matched rather than to True if a future key is added without a branch.
    logger.warning("agent_detect.rules: no evaluator for matcher key %r", key)
    return False


def matches(matcher: Matcher, event: Any) -> bool:
    """True when every clause holds. Keys AND, values within a key OR."""
    for clause in matcher.clauses:
        hit = _clause_matches(clause, event)
        if clause.negated:
            hit = not hit
        if not hit:
            return False
    return True


def match_rule(rule: Rule, event: Any) -> Optional[RuleMatch]:
    """Evaluate one single-event rule. Returns None when it does not fire."""
    if not rule.enabled or rule.expr is None:
        return None
    if not matches(rule.expr, event):
        return None
    return RuleMatch(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        severity=rule.severity,
        title=rule.title,
        tags=rule.tags,
        enforce=rule.enforce,
        deny_message=rule.deny_message,
        event_id=_get(event, "event_id"),
        session_id=_get(event, "session_id"),
        actor=_get(event, "actor"),
        matched_keys=rule.expr.keys,
    )


def evaluate_event(
    event: Any,
    ruleset: Optional[RuleSet] = None,
    *,
    rules_dir: Optional[os.PathLike[str] | str] = None,
) -> list[RuleMatch]:
    """Every enabled single-event rule that matches ``event``.

    Never raises. A rule whose evaluation faults is logged and skipped — one
    broken rule must not blind the engine to the other eleven, and it must
    certainly not propagate into the pre-tool-use hook.
    """
    if ruleset is None:
        ruleset = load_rules(rules_dir)
    hits: list[RuleMatch] = []
    for rule in ruleset.event_rules:
        try:
            hit = match_rule(rule, event)
        except Exception as exc:  # noqa: BLE001 — see docstring
            logger.warning(
                "agent_detect.rules: rule %s (%s) raised during evaluation: %s",
                rule.rule_id, rule.source_path, exc,
            )
            continue
        if hit is not None:
            hits.append(hit)
    return hits
