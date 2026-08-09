# CUI // SP-CTI
"""Multi-step (sequence / chain) detection over the agent event stream (agov-det-04).

WHY THIS EXISTS. Every safety check ICDEV ships today is single-action: the
1250-line ``.claude/hooks/pre_tool_use.py`` decides on one tool call at a time,
and ``args/file_access_tiers.yaml`` classifies one path at a time. Neither can
express "read ``.env``, **then** POST to an external host" — the two halves are
individually unremarkable and the chain is the whole signal. This module is the
missing operator.

SCHEMA (the ``sequence:`` block of a rule file under ``args/agent_rules/``)::

    sequence:
      within: 30m            # wall-clock window, first step -> last step
      within_events: 40      # positional window, inclusive span in the partition
      steps:                 # 2..8 ordered matchers
        - {event_type: file.read, file_path_glob: "*/.env"}
        - {event_type: network.indicator, url_matches: "^https?://"}
      max_matches: 1         # findings per partition, default 1, max 16

At least one of ``within`` / ``within_events`` is required — an unbounded chain
would eventually match every session that ever did both things. Steps must
match **in order** but need not be adjacent, because a real chain is separated
by ordinary work.

PARTITIONING IS THE CORRECTNESS PROPERTY. Candidate events are bucketed by
``(session_id, agent, project_id, source)`` and a chain is only ever assembled
inside one bucket. ICDEV runs many concurrent sessions against one database, so
an evaluator that scanned the event stream globally would stitch session A's
``.env`` read to session B's outbound POST and report it as an attack. That is
not a detection, it is a false-positive generator, and at ICDEV's concurrency
it would fire constantly. Three exclusions follow from the same reasoning and
are all fail-CLOSED (see :func:`_candidates`):

- no ``session_id``  — cannot prove two events share a session, so never chain it
- no parseable ``ts`` — cannot order it, and "in order" is the entire semantics
- no ``event_id``    — cannot cite it, and an uncitable finding is unreviewable

EVERY CONTRIBUTING EVENT IS CITED, IN STEP ORDER. :attr:`SequenceMatch.event_ids`
is the ordered tuple of the ids that satisfied step 0, step 1, ... so a reviewer
reconstructs why a rule fired by pulling those rows — never by re-running the
engine against a database that has moved on.

MATCHING is greedy-earliest from each candidate start, which is exact rather
than merely cheap: both window constraints are monotone in the index of the last
event, so taking the earliest event that satisfies each subsequent step
minimizes the final index. If the greedy chain from a given start violates the
window, no assignment from that start can satisfy it. Successive matches within
a partition are non-overlapping (the next search resumes after the previous
chain's last event), so ``max_matches`` bounds distinct episodes rather than
counting the combinatorial re-slicings of one.

STEP MATCHING is delegated. :func:`default_step_matcher` prefers
``tools.agent_detect.rules.match_event`` (agov-det-03) and falls back to
:func:`builtin_match_event`, a stand-in with the same key vocabulary that exists
so this evaluator is usable and testable standalone. Both read STRUCTURED event
fields only. Neither substring-matches a flattened tool input — that is the
documented fail-open in ``args/agent_approval_policy.yaml:107-126`` where a
``git_push`` carrying ``{"note": "mkdir logs"}`` matched the ``mkdir`` pattern.

This module does not touch the database. Persisting findings is agov-det-05 and
wiring the evaluator into the hook is agov-det-06; a sequence evaluator that
also owned its storage could not be unit-tested without one.
"""
from __future__ import annotations

import fnmatch
import functools
import hashlib
import re
from collections.abc import Mapping, Sequence as _SequenceABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_detect.sequence")

#: Fields whose exact tuple defines a partition. A chain never spans two of them.
PARTITION_FIELDS = ("session_id", "agent", "project_id", "source")

MIN_STEPS = 2
MAX_STEPS = 8
DEFAULT_MAX_MATCHES = 1
MAX_MAX_MATCHES = 16

#: ``ts`` formats accepted in addition to ISO-8601 and epoch seconds.
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")

_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_DURATION_SEGMENT = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])")
_BARE_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")

#: An event carries one of these under either name; they mean the same actor.
_FIELD_ALIASES = {"actor": ("actor", "agent"), "agent": ("agent", "actor")}


class SequenceSpecError(ValueError):
    """A ``sequence:`` block is malformed.

    Raised only at parse time by :meth:`SequenceSpec.from_dict`, so a rule
    loader can skip the offending file and name it. :func:`evaluate_sequence`
    catches it and yields zero findings — a broken rule must never be treated
    as match-all and must never crash its caller.
    """


# ---------------------------------------------------------------------------
# duration
# ---------------------------------------------------------------------------


def parse_duration(value: Any) -> float:
    """Parse ``30m`` / ``1h30m`` / ``45`` into seconds.

    A bare number is seconds. Units are ``s``/``m``/``h``/``d`` and may be
    concatenated. Anything else — including a trailing unparsed remainder like
    ``30 minutes`` — raises, because silently reading ``30m`` out of
    ``30 months`` would widen a window by four orders of magnitude.
    """
    if isinstance(value, bool):
        raise SequenceSpecError("within must be a duration string or number, not a bool")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if not text:
            raise SequenceSpecError("within is empty")
        if _BARE_NUMBER.match(text):
            seconds = float(text)
        else:
            segments = list(_DURATION_SEGMENT.finditer(text))
            consumed = "".join(m.group(0) for m in segments).replace(" ", "")
            if not segments or consumed != text.replace(" ", ""):
                raise SequenceSpecError(f"unparseable duration: {value!r}")
            seconds = sum(float(m.group(1)) * _UNIT_SECONDS[m.group(2)] for m in segments)
    if seconds <= 0:
        raise SequenceSpecError(f"duration must be positive: {value!r}")
    return seconds


# ---------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceSpec:
    """A validated ``sequence:`` block."""

    steps: tuple[Mapping[str, Any], ...]
    within_seconds: Optional[float] = None
    within_events: Optional[int] = None
    max_matches: int = DEFAULT_MAX_MATCHES
    within_raw: Optional[Any] = None

    @classmethod
    def from_dict(cls, spec: Any) -> "SequenceSpec":
        """Validate a raw ``sequence`` mapping. Raises :class:`SequenceSpecError`."""
        if not isinstance(spec, Mapping):
            raise SequenceSpecError("sequence must be a mapping")

        unknown = set(spec) - {"within", "within_events", "steps", "max_matches"}
        if unknown:
            raise SequenceSpecError(f"unknown sequence key(s): {sorted(unknown)}")

        raw_steps = spec.get("steps")
        if not isinstance(raw_steps, (list, tuple)):
            raise SequenceSpecError("sequence.steps must be a list")
        if not MIN_STEPS <= len(raw_steps) <= MAX_STEPS:
            raise SequenceSpecError(
                f"sequence.steps must hold {MIN_STEPS}..{MAX_STEPS} steps, got {len(raw_steps)}"
            )
        steps: list[Mapping[str, Any]] = []
        for index, step in enumerate(raw_steps):
            if not isinstance(step, Mapping) or not step:
                raise SequenceSpecError(f"sequence.steps[{index}] must be a non-empty mapping")
            steps.append(dict(step))

        within_raw = spec.get("within")
        within_seconds = None if within_raw is None else parse_duration(within_raw)

        within_events = spec.get("within_events")
        if within_events is not None:
            if isinstance(within_events, bool) or not isinstance(within_events, int):
                raise SequenceSpecError("sequence.within_events must be an integer")
            if within_events < 1:
                raise SequenceSpecError("sequence.within_events must be >= 1")

        if within_seconds is None and within_events is None:
            raise SequenceSpecError("sequence requires at least one of within / within_events")

        max_matches = spec.get("max_matches", DEFAULT_MAX_MATCHES)
        if isinstance(max_matches, bool) or not isinstance(max_matches, int):
            raise SequenceSpecError("sequence.max_matches must be an integer")
        if not 1 <= max_matches <= MAX_MAX_MATCHES:
            raise SequenceSpecError(
                f"sequence.max_matches must be 1..{MAX_MAX_MATCHES}, got {max_matches}"
            )

        return cls(
            steps=tuple(steps),
            within_seconds=within_seconds,
            within_events=within_events,
            max_matches=max_matches,
            within_raw=within_raw,
        )


# ---------------------------------------------------------------------------
# event access
# ---------------------------------------------------------------------------


def _raw(event: Any, name: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def _field(event: Any, name: str) -> Any:
    """Read ``name`` off a mapping or an object, honouring the actor/agent alias."""
    for candidate in _FIELD_ALIASES.get(name, (name,)):
        value = _raw(event, candidate)
        if value is not None:
            return value
    return None


def coerce_timestamp(value: Any) -> Optional[datetime]:
    """Normalize a ``ts`` to an aware UTC datetime, or ``None`` if unusable.

    A naive datetime or string is read as UTC — every ICDEV writer stamps
    ``datetime.now(timezone.utc)``, and guessing local time would shift a
    window by the host's offset.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
        for fmt in _TS_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def partition_key(event: Any) -> tuple:
    """The ``(session_id, agent, project_id, source)`` bucket an event belongs to."""
    values = []
    for name in PARTITION_FIELDS:
        value = _field(event, name)
        values.append(None if value is None else str(value))
    return tuple(values)


@dataclass(frozen=True)
class _Candidate:
    index: int
    event_id: str
    ts: datetime
    event: Any


def _candidates(events: Iterable[Any]) -> dict[tuple, list[_Candidate]]:
    """Bucket events by partition, dropping any that cannot be chained safely."""
    buckets: dict[tuple, list[_Candidate]] = {}
    dropped = 0
    for index, event in enumerate(events):
        session_id = _field(event, "session_id")
        event_id = _field(event, "event_id")
        ts = coerce_timestamp(_field(event, "ts"))
        if session_id is None or str(session_id) == "" or event_id is None or ts is None:
            dropped += 1
            continue
        buckets.setdefault(partition_key(event), []).append(
            _Candidate(index=index, event_id=str(event_id), ts=ts, event=event)
        )
    if dropped:
        logger.debug(
            "sequence: dropped %d event(s) lacking session_id, event_id or a parseable ts", dropped
        )
    for candidates in buckets.values():
        candidates.sort(key=lambda c: (c.ts, c.index))
    return buckets


# ---------------------------------------------------------------------------
# step matching
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=512)
def _compiled(pattern: str) -> Optional[re.Pattern]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning("sequence: skipping uncompilable pattern %r (%s)", pattern, exc)
        return None


def _as_list(value: Any) -> list:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _h_equals(field_name: str) -> Callable[[Any, Any], bool]:
    def handler(event: Any, want: Any) -> bool:
        actual = _field(event, field_name)
        return actual is not None and str(actual) == str(want)

    return handler


def _h_glob(field_name: str) -> Callable[[Any, Any], bool]:
    def handler(event: Any, want: Any) -> bool:
        actual = _field(event, field_name)
        return actual is not None and fnmatch.fnmatch(str(actual), str(want))

    return handler


def _h_regex(field_name: str) -> Callable[[Any, Any], bool]:
    def handler(event: Any, want: Any) -> bool:
        actual = _field(event, field_name)
        if actual is None:
            return False
        pattern = _compiled(str(want))
        return pattern is not None and pattern.search(str(actual)) is not None

    return handler


def _h_contains(field_name: str) -> Callable[[Any, Any], bool]:
    def handler(event: Any, want: Any) -> bool:
        actual = _field(event, field_name)
        if isinstance(actual, str) or not isinstance(actual, _SequenceABC):
            return False
        return any(str(item) == str(want) for item in actual)

    return handler


#: The matcher vocabulary agov-det-03 defines. ``not_<key>`` negates each.
MATCHER_KEYS: dict[str, Callable[[Any, Any], bool]] = {
    "event_type": _h_equals("event_type"),
    "tool_name": _h_equals("tool_name"),
    "actor": _h_equals("actor"),
    "command_name": _h_equals("command_name"),
    "file_path_glob": _h_glob("file_path"),
    "command_matches": _h_regex("command"),
    "url_matches": _h_regex("url"),
    "argv_contains": _h_contains("argv"),
}


def builtin_match_event(matcher: Any, event: Any) -> bool:
    """Structured-field matcher used until agov-det-03's lands.

    Keys AND together; the list under a key ORs. An unknown key makes the step
    unmatchable rather than match-all — a typo must fail closed, and a matcher
    that can be made permissive by a bad key is an allowlist that fails open by
    accident.
    """
    if not isinstance(matcher, Mapping) or not matcher:
        return False
    for key, expected in matcher.items():
        name = str(key)
        negate = name.startswith("not_")
        base = name[4:] if negate else name
        handler = MATCHER_KEYS.get(base)
        if handler is None:
            logger.warning("sequence: unknown matcher key %r — step cannot match", key)
            return False
        hit = any(handler(event, want) for want in _as_list(expected))
        if hit == negate:
            return False
    return True


_MATCHER_UNRESOLVED = object()
_rule_matcher: Any = _MATCHER_UNRESOLVED


def _resolve_rule_matcher() -> Optional[Callable[[Any, Any], bool]]:
    """Bind ``rules.match_event`` if agov-det-03 has landed, else ``None``."""
    global _rule_matcher
    if _rule_matcher is _MATCHER_UNRESOLVED:
        try:
            from tools.agent_detect.rules import match_event  # type: ignore

            _rule_matcher = match_event if callable(match_event) else None
        except Exception:  # pragma: no cover - depends on sibling card landing
            _rule_matcher = None
    return _rule_matcher


def reset_matcher_cache() -> None:
    """Forget the resolved matcher. For tests that swap the implementation."""
    global _rule_matcher
    _rule_matcher = _MATCHER_UNRESOLVED
    _compiled.cache_clear()


def default_step_matcher(matcher: Any, event: Any) -> bool:
    """Prefer agov-det-03's single-event matcher; fall back to the built-in."""
    resolved = _resolve_rule_matcher()
    if resolved is not None:
        return bool(resolved(matcher, event))
    return builtin_match_event(matcher, event)


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceMatch:
    """One chain, wholly inside one partition."""

    partition: tuple
    event_ids: tuple[str, ...]
    events: tuple = field(repr=False, default=())
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    span_events: int = 0
    span_seconds: float = 0.0

    @property
    def session_id(self) -> Optional[str]:
        return self.partition[0]

    @property
    def actor(self) -> Optional[str]:
        return self.partition[1]

    @property
    def project_id(self) -> Optional[str]:
        return self.partition[2]

    @property
    def source(self) -> Optional[str]:
        return self.partition[3]


def _next_match(
    candidates: list[_Candidate],
    start: int,
    stop: int,
    step: Mapping[str, Any],
    match_fn: Callable[[Any, Any], bool],
) -> Optional[int]:
    for index in range(start, stop):
        if match_fn(step, candidates[index].event):
            return index
    return None


def _chain_from(
    spec: SequenceSpec,
    candidates: list[_Candidate],
    start: int,
    match_fn: Callable[[Any, Any], bool],
) -> Optional[list[int]]:
    """Earliest complete chain whose first event is at or after ``start``."""
    total = len(candidates)
    for first in range(start, total):
        if not match_fn(spec.steps[0], candidates[first].event):
            continue
        # within_events is an inclusive span, so it also bounds the search.
        stop = total if spec.within_events is None else min(total, first + spec.within_events)
        indexes = [first]
        cursor = first
        for step in spec.steps[1:]:
            found = _next_match(candidates, cursor + 1, stop, step, match_fn)
            if found is None:
                break
            indexes.append(found)
            cursor = found
        if len(indexes) != len(spec.steps):
            continue
        if spec.within_seconds is not None:
            elapsed = (candidates[indexes[-1]].ts - candidates[first].ts).total_seconds()
            if elapsed > spec.within_seconds:
                continue
        return indexes
    return None


def find_sequence_matches(
    spec: SequenceSpec,
    events: Iterable[Any],
    match_fn: Optional[Callable[[Any, Any], bool]] = None,
) -> list[SequenceMatch]:
    """Every chain satisfying ``spec``, capped at ``max_matches`` per partition."""
    matcher = match_fn or default_step_matcher
    results: list[SequenceMatch] = []
    for key, candidates in sorted(_candidates(events).items(), key=lambda kv: [
        "" if part is None else part for part in kv[0]
    ]):
        cursor = 0
        found_here = 0
        while found_here < spec.max_matches:
            indexes = _chain_from(spec, candidates, cursor, matcher)
            if indexes is None:
                break
            picked = [candidates[i] for i in indexes]
            results.append(
                SequenceMatch(
                    partition=key,
                    event_ids=tuple(c.event_id for c in picked),
                    events=tuple(c.event for c in picked),
                    first_ts=picked[0].ts,
                    last_ts=picked[-1].ts,
                    span_events=indexes[-1] - indexes[0] + 1,
                    span_seconds=(picked[-1].ts - picked[0].ts).total_seconds(),
                )
            )
            found_here += 1
            # Non-overlapping: the next chain starts after this one's last event.
            cursor = indexes[-1] + 1
    return results


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceFinding:
    """A match plus the rule metadata a reviewer needs, shaped for agent_findings."""

    finding_id: str
    rule_id: str
    rule_version: Optional[str]
    severity: str
    title: str
    tags: tuple[str, ...]
    enforced: bool
    decision: str
    session_id: Optional[str]
    actor: Optional[str]
    project_id: Optional[str]
    source: Optional[str]
    event_ids: tuple[str, ...]
    match: SequenceMatch = field(repr=False, default=None)  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "title": self.title,
            "tags": list(self.tags),
            "enforced": self.enforced,
            "decision": self.decision,
            "session_id": self.session_id,
            "actor": self.actor,
            "project_id": self.project_id,
            "source": self.source,
            "event_ids": list(self.event_ids),
            "span_events": self.match.span_events if self.match else 0,
            "span_seconds": self.match.span_seconds if self.match else 0.0,
        }


def _finding_id(rule_id: str, rule_version: Any, event_ids: tuple[str, ...]) -> str:
    """Deterministic id, so re-evaluating the same events never double-reports."""
    digest = hashlib.sha256(
        "|".join([str(rule_id), str(rule_version), *event_ids]).encode("utf-8")
    ).hexdigest()
    return digest[:32]


def evaluate_sequence(
    rule: Mapping[str, Any],
    events: Iterable[Any],
    match_fn: Optional[Callable[[Any, Any], bool]] = None,
) -> list[SequenceFinding]:
    """Evaluate one rule's ``sequence`` block against ``events``.

    Returns ``[]`` — never raises — when the rule is disabled, carries no
    sequence, or carries a malformed one. The offending spec is logged with its
    rule id so an operator can find it; a broken rule is inert, not match-all.
    """
    if not isinstance(rule, Mapping):
        logger.warning("sequence: rule must be a mapping, got %s", type(rule).__name__)
        return []
    rule_id = str(rule.get("id") or "<unnamed>")
    if rule.get("enabled", True) is False:
        return []
    raw = rule.get("sequence")
    if raw is None:
        return []
    try:
        spec = SequenceSpec.from_dict(raw)
    except SequenceSpecError as exc:
        logger.warning("sequence: skipping rule %s — %s", rule_id, exc)
        return []

    enforced = bool(rule.get("enforce", False))
    tags = tuple(str(tag) for tag in _as_list(rule.get("tags") or []))
    findings = []
    for match in find_sequence_matches(spec, events, match_fn=match_fn):
        findings.append(
            SequenceFinding(
                finding_id=_finding_id(rule_id, rule.get("version"), match.event_ids),
                rule_id=rule_id,
                rule_version=None if rule.get("version") is None else str(rule.get("version")),
                severity=str(rule.get("severity") or "medium"),
                title=str(rule.get("title") or rule_id),
                tags=tags,
                enforced=enforced,
                decision="denied" if enforced else "observed",
                session_id=match.session_id,
                actor=match.actor,
                project_id=match.project_id,
                source=match.source,
                event_ids=match.event_ids,
                match=match,
            )
        )
    return findings


__all__ = [
    "DEFAULT_MAX_MATCHES",
    "MATCHER_KEYS",
    "MAX_MAX_MATCHES",
    "MAX_STEPS",
    "MIN_STEPS",
    "PARTITION_FIELDS",
    "SequenceFinding",
    "SequenceMatch",
    "SequenceSpec",
    "SequenceSpecError",
    "builtin_match_event",
    "coerce_timestamp",
    "default_step_matcher",
    "evaluate_sequence",
    "find_sequence_matches",
    "parse_duration",
    "partition_key",
    "reset_matcher_cache",
]
