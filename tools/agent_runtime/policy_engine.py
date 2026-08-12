# CUI // SP-CTI
"""Policy-function layer over the reversibility gate (exa-policy-01).

:mod:`tools.agent_runtime.approval_gate` answers exactly one question — *is this
tool call reversible?* — and it answers it well: ``default_tier: unknown`` with
``unknown`` in ``require_approval_tiers`` means a tool has to be **named** to be
automatic, content patterns escalate before the per-tool tier, and downgrade
patterns fire only for declared ``command_tools``. Nothing here replaces any of
that. :func:`~tools.agent_runtime.approval_gate.classify` is imported and called
unchanged.

What it cannot express is everything that is not a regex over one tool name:

* a question that depends on **who** is asking, or on **how much** they have
  already spent, or on **how many** calls this session has made;
* an outright **DENY** — the gate's vocabulary is "auto-allow" and "ask a
  human", so a rule that should never be answerable by a tired operator at 3am
  has nowhere to live;
* more than one opinion at a time.

So this module adds a layer **above** it. A *policy* is a plain function::

    def my_policy(event: PolicyEvent) -> PolicyDecision:
        return PolicyDecision(DENY, "branch is protected", policy="my_policy")

It receives a :class:`PolicyEvent` (type, target, arguments, actor, usage,
session state) and returns one of three effects plus a **reason**:

    ALLOW   proceed
    ASK     escalate to a human approver
    DENY    refuse — and stop evaluating

:func:`evaluate` runs the configured chain in order. **A DENY short-circuits**:
the remaining policies are never consulted, because there is no answer a later
policy could give that would make a refusal not a refusal. ASK does *not*
short-circuit — a later DENY must still be able to win — so the chain effect is
the strictest one any policy returned.

The reversibility gate becomes one policy among several
(:func:`reversibility_policy`), mapping ``requires_approval`` to ASK and
everything else to ALLOW. Its verdict is unchanged; it is just no longer the
only voice.

Failure is closed at every layer, which is the same posture the gate already
takes:

* a policy that **raises** resolves to ``on_policy_error`` — ``deny`` by
  default, and ``allow`` is not an accepted value;
* a policy that returns an **unrecognised effect** is a DENY;
* a **missing or unreadable config** falls back to a chain of exactly the
  reversibility policy, which is itself fail-closed;
* a **floor** in the config is a minimum strictness no policy chain can go
  below, so an operator can pin "every tool call asks" without editing Python.

Config: ``args/agent_policy_chain.yaml``, modelled on
``args/heal_constitution.yaml`` — a reviewed YAML whose scopes are stated in
prose and whose per-action floors sit beside the rules they bound.

Audit: every decision is appended to the existing append-only
``agent_approval_log`` through
:func:`~tools.agent_runtime.approval_gate.record_decision`. That function is
reused rather than reimplemented precisely because of the property it already
has — it stores argument **key names** and a SHA-256 of the flattened input, and
never argument values, which can carry CUI. A second INSERT written here would
be a second chance to get that wrong.

CLI::

    python tools/agent_runtime/policy_engine.py --list-policies --json
    python tools/agent_runtime/policy_engine.py --evaluate git_push --json
    python tools/agent_runtime/policy_engine.py --evaluate run_command \\
        --input '{"command": "git push --force"}' --json
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Run by path, this module is `__main__`, and `tools.agent_runtime.policy_engine`
# is a SECOND, distinct module object. That matters now that policies live in
# another module (exa-policy-03): `policy_builtins` imports the canonical copy,
# so it would build `PolicyDecision` instances of the canonical class while
# `_normalize` here checks `isinstance` against `__main__`'s class. The check
# fails, and a perfectly good decision is normalised to "returned something that
# is not a PolicyDecision" — a DENY. Silently, and only via the CLI.
#
# Publishing this module under its canonical name BEFORE the first-party imports
# below means `policy_builtins` binds to this object and there is only ever one
# set of dataclasses. `setdefault`, so an ordinary import never disturbs a real
# entry.
if __name__ == "__main__":  # pragma: no cover — the by-path CLI path
    sys.modules.setdefault("tools.agent_runtime.policy_engine", sys.modules[__name__])

from tools.agent_runtime import approval_gate as gate
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.policy_engine")

# --- Effects ---------------------------------------------------------------
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
EFFECTS = (ALLOW, ASK, DENY)

# Ordered least → most strict. `strictest()` is how a chain of opinions becomes
# one answer, and how a config floor raises a chain that came back too permissive.
_STRICTNESS = {ALLOW: 0, ASK: 1, DENY: 2}

CONFIG_FILENAME = "agent_policy_chain.yaml"
CONFIG_ENV = "ICDEV_AGENT_POLICY_CHAIN"

EVENT_TOOL_CALL = "tool_call"

# Used when a policy chain is evaluated without a config file. Exactly the
# reversibility policy, which is itself fail-closed (unknown tools ASK), and
# `on_policy_error: deny`. A missing config must never be the reason a policy
# stopped being consulted.
_FALLBACK_CONFIG: dict[str, Any] = {
    "version": 0,
    "on_policy_error": DENY,
    "audit": {"log_allow": True},
    "chain": [{"name": "reversibility", "enabled": True}],
    "floors": {},
}


# ---------------------------------------------------------------------------
# Event and decision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PolicyEvent:
    """One thing an agent is about to do, handed to every policy in the chain.

    ``arguments`` is model-authored and may carry CUI. It is passed to policies
    because a policy that cannot see the arguments cannot judge the call — but
    it is never persisted and never rendered: :meth:`__repr__` below elides the
    values so an exception traceback or a debug log cannot leak them, and the
    audit row stores only key names and a digest (see :func:`record_decision`).
    """

    event_type: str = EVENT_TOOL_CALL
    target: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    session_state: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def argument_keys(self) -> list[str]:
        """Key names only — the safe projection of :attr:`arguments`."""
        if not isinstance(self.arguments, dict):
            return []
        return sorted(str(k) for k in self.arguments)

    def __repr__(self) -> str:  # noqa: D105 — see the class docstring
        return (
            f"PolicyEvent(event_type={self.event_type!r}, target={self.target!r}, "
            f"argument_keys={self.argument_keys()!r}, actor={self.actor!r}, "
            f"session_id={self.session_id!r})"
        )


@dataclass(frozen=True)
class PolicyDecision:
    """What one policy said, and why.

    ``reason`` is required in spirit and enforced in practice: a decision with
    no reason is unreviewable, and a DENY nobody can explain is a bug report
    waiting to be filed against the wrong component.

    ``state_updates`` is how a *stateful* policy writes back — a tuple of
    ``{"key": ..., "action": ..., "value": ...}`` mappings, omnigent's
    mechanism. This module only carries them; applying them (and persisting the
    result for a session) is
    :func:`tools.agent_runtime.policy_composition.apply_state_updates`, because
    the state that a counter counts against is session-scoped and this layer is
    process-wide. A single-level chain with no session therefore ignores them,
    which is why the field is a plain default rather than a required argument.
    """

    effect: str
    reason: str = ""
    policy: str = ""
    tier: str = ""      # carried through from the reversibility gate, if any
    rule: str = ""      # which rule inside the policy decided
    detail: str = ""
    state_updates: tuple[dict[str, Any], ...] = ()

    def summary(self) -> str:
        head = f"[{self.effect.upper()}] {self.policy or 'policy'}"
        return f"{head}: {self.reason}" if self.reason else head


# A policy is any callable event -> PolicyDecision. Returning ``None`` means
# "no opinion" and is normalised to ALLOW with an abstain reason — an abstention
# is not an authorisation, it simply lets the next policy speak.
PolicyFunction = Callable[[PolicyEvent], Optional[PolicyDecision]]

# A policy FACTORY builds one configured policy instance from a chain entry's
# ``params`` (exa-policy-03; omnigent's ``factory_params`` shape). It is how a
# policy is *configured* rather than *copied*: two instances with different
# limits are two config entries, not two functions.
PolicyFactory = Callable[[dict[str, Any]], PolicyFunction]


@dataclass(frozen=True)
class ChainResult:
    """The chain's single answer, plus every opinion that produced it."""

    effect: str
    reason: str
    policy: str
    decisions: tuple[PolicyDecision, ...] = ()
    short_circuited: bool = False
    floor_applied: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect == ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.effect == ASK

    def summary(self) -> str:
        line = f"[{self.effect.upper()}] {self.policy or 'policy_chain'}: {self.reason}"
        if self.short_circuited:
            line += " (chain short-circuited on DENY)"
        if self.floor_applied:
            line += f" (raised to the {self.floor_applied} floor)"
        return line

    def tier(self) -> str:
        """The reversibility tier, if any policy in the chain established one."""
        for decision in self.decisions:
            if decision.tier:
                return decision.tier
        return gate.UNKNOWN


def strictest(*effects: str) -> str:
    """The most restrictive of the given effects. Unknown strings are DENY."""
    best = ALLOW
    for effect in effects:
        normalized = effect if effect in _STRICTNESS else DENY
        if _STRICTNESS[normalized] > _STRICTNESS[best]:
            best = normalized
    return best


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _find_config_path() -> Optional[Path]:
    """Locate ``args/agent_policy_chain.yaml``.

    Resolved from ``__file__``, never ``os.getcwd()`` — this module is imported
    from worktrees and from CI runners whose cwd is not the repo root (CLAUDE.md,
    "Notes for agents working from worktrees").
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        p = Path(override)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "args" / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


_CONFIG_CACHE: Optional[dict[str, Any]] = None


def load_config(*, refresh: bool = False) -> dict[str, Any]:
    """Load (and memoize) the policy-chain config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not refresh:
        return _CONFIG_CACHE
    config = dict(_FALLBACK_CONFIG)
    path = _find_config_path()
    if path is not None:
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                config = loaded
        except Exception as exc:  # noqa: BLE001 — fall back, never fail open
            logger.warning(
                "policy_engine: config %s unreadable (%s); using the "
                "reversibility-only fallback chain", path, exc,
            )
    else:
        logger.warning(
            "policy_engine: %s not found; using the reversibility-only "
            "fallback chain", CONFIG_FILENAME,
        )
    _CONFIG_CACHE = config
    return config


def _on_policy_error(config: dict[str, Any]) -> str:
    """How a raising policy resolves. ``deny`` unless told ``ask``; never allow.

    A broken policy is an unanswered question, not an answer. ``allow`` is
    rejected rather than honoured because a config typo must not be the thing
    that authorises an irreversible action.
    """
    raw = str(config.get("on_policy_error") or DENY).strip().lower()
    return raw if raw in (DENY, ASK) else DENY


def _floor_for(config: dict[str, Any], event_type: str) -> str:
    raw = (config.get("floors") or {}).get(event_type)
    value = str(raw or ALLOW).strip().lower()
    return value if value in _STRICTNESS else ALLOW


def _log_allow(config: dict[str, Any]) -> bool:
    audit = config.get("audit") or {}
    return bool(audit.get("log_allow", True))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, PolicyFunction] = {}
_FACTORIES: dict[str, PolicyFactory] = {}

# The builtins (exa-policy-03) live in a module that imports THIS one, so they
# cannot be imported at the top of this file. They are imported on first use
# instead, which is enough because every path that could ask for one —
# `resolve_chain`, `get_policy`, `get_policy_factory`, `list_policies` — goes
# through `_ensure_builtins()` below. Registering them lazily but *always* is
# what keeps "a name in the config always resolves to something" true; leaving
# it to the caller to import the module first would make a configured policy
# silently missing depending on import order.
_BUILTINS_LOADED = False


def _ensure_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True  # set first: a failed import must not retry per call
    try:
        from tools.agent_runtime import policy_builtins  # noqa: F401 — registers
    except Exception as exc:  # noqa: BLE001 — a chain naming one still DENYs
        logger.warning(
            "policy_engine: could not load the builtin policies (%s); a chain "
            "naming one will resolve to a DENY rather than be skipped", exc,
        )


def register_policy(name: str, fn: PolicyFunction, *, replace: bool = False) -> None:
    """Register a policy function under ``name``.

    Registering over an existing name needs ``replace=True``. Silently
    shadowing a policy is how a chain that looks configured stops enforcing
    what its name says it enforces.
    """
    key = str(name).strip().lower()
    if not key:
        raise ValueError("a policy needs a name")
    if key in _REGISTRY and not replace:
        raise ValueError(f"policy {key!r} is already registered (pass replace=True)")
    _REGISTRY[key] = fn


def register_policy_factory(
    name: str, factory: PolicyFactory, *, replace: bool = False
) -> None:
    """Register a policy *factory* under ``name`` (exa-policy-03).

    A factory is called once per chain entry, at resolution time, with that
    entry's ``params``. It returns the configured policy function. Same
    anti-shadowing rule as :func:`register_policy`, and for the same reason.
    """
    key = str(name).strip().lower()
    if not key:
        raise ValueError("a policy factory needs a name")
    if key in _FACTORIES and not replace:
        raise ValueError(
            f"policy factory {key!r} is already registered (pass replace=True)"
        )
    _FACTORIES[key] = factory


def get_policy(name: str) -> Optional[PolicyFunction]:
    _ensure_builtins()
    return _REGISTRY.get(str(name).strip().lower())


def get_policy_factory(name: str) -> Optional[PolicyFactory]:
    _ensure_builtins()
    return _FACTORIES.get(str(name).strip().lower())


def list_policies() -> list[str]:
    _ensure_builtins()
    return sorted(_REGISTRY)


def list_policy_factories() -> list[str]:
    _ensure_builtins()
    return sorted(_FACTORIES)


# ---------------------------------------------------------------------------
# The reversibility gate, as one policy among several
# ---------------------------------------------------------------------------
def reversibility_policy(event: PolicyEvent) -> PolicyDecision:
    """:func:`approval_gate.classify` expressed as a policy function.

    Behaviour is deliberately a pure translation of the gate's own verdict:

        ``requires_approval`` True   -> ASK
        ``requires_approval`` False  -> ALLOW

    and nothing else. The gate never returns DENY, so this policy never does
    either — a call it dislikes is one a human may still authorise. That is the
    existing contract and this layer does not tighten it; a rule that should be
    refusable without a human goes in its own policy.

    ``classify`` is called with the event's arguments exactly as
    ``build_approval_hook`` calls it, so the tier, the rule and the detail are
    the same strings the gate would have produced for the same call.
    """
    classification = gate.classify(event.target, event.arguments)
    return PolicyDecision(
        effect=ASK if classification.requires_approval else ALLOW,
        reason=(
            f"{classification.tool_name} classified {classification.tier} "
            f"by {classification.rule}"
        ),
        policy="reversibility",
        tier=classification.tier,
        rule=classification.rule,
        detail=classification.detail,
    )


register_policy("reversibility", reversibility_policy, replace=True)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _normalize_state_updates(raw: Any) -> tuple[dict[str, Any], ...]:
    """Structurally validate a policy's ``state_updates``, or raise.

    Raising is the point. A malformed update dropped quietly is a counter that
    never increments, which is a limit that never fires — the
    declared-but-unconsumed failure this card exists to stop, in miniature. The
    raise is caught by :func:`evaluate` and resolved to ``on_policy_error``
    (DENY by default), so a policy that cannot express its own state change does
    not get to authorise anything.

    Only *structure* is checked here — a non-empty ``key`` and a non-empty
    ``action``. The action VOCABULARY belongs to
    :mod:`tools.agent_runtime.policy_composition`, which owns the state, and a
    second copy of it here is a copy that drifts.
    """
    if not raw:
        return ()
    if isinstance(raw, dict):  # a single update, unwrapped — accept it
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"state_updates must be a sequence, got {type(raw).__name__}")
    updates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(
                f"each state update must be a mapping, got {type(item).__name__}"
            )
        key = str(item.get("key") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        if not key:
            raise ValueError("a state update needs a non-empty 'key'")
        if not action:
            raise ValueError(f"state update for {key!r} needs a non-empty 'action'")
        updates.append({"key": key, "action": action, "value": item.get("value")})
    return tuple(updates)


def _normalize(result: Any, name: str) -> PolicyDecision:
    """Coerce whatever a policy returned into a PolicyDecision. Fail closed."""
    if result is None:
        return PolicyDecision(ALLOW, "no opinion", policy=name, rule="abstain")
    if isinstance(result, PolicyDecision):
        effect = str(result.effect or "").strip().lower()
        if effect not in EFFECTS:
            return PolicyDecision(
                DENY,
                f"policy returned an unrecognised effect {result.effect!r}",
                policy=name or result.policy,
                rule="invalid_effect",
            )
        return PolicyDecision(
            effect=effect,
            reason=result.reason or "",
            policy=result.policy or name,
            tier=result.tier,
            rule=result.rule,
            detail=result.detail,
            state_updates=_normalize_state_updates(result.state_updates),
        )
    if isinstance(result, str):
        effect = result.strip().lower()
        if effect in EFFECTS:
            return PolicyDecision(effect, "", policy=name)
    if isinstance(result, tuple) and len(result) == 2:
        effect = str(result[0]).strip().lower()
        if effect in EFFECTS:
            return PolicyDecision(effect, str(result[1]), policy=name)
    return PolicyDecision(
        DENY,
        f"policy returned {type(result).__name__}, not a PolicyDecision",
        policy=name,
        rule="invalid_return",
    )


def resolve_chain(
    config: Optional[dict[str, Any]] = None,
) -> list[tuple[str, PolicyFunction]]:
    """The ordered, enabled policies named by the config.

    A name the registry does not know is **not** silently skipped — that is the
    declared-but-unconsumed failure this repo keeps shipping. It resolves to a
    policy that DENYs with a reason saying so, which is loud, safe, and fixed by
    either registering the policy or removing the line.

    An entry may carry ``params`` (exa-policy-03). If the name is a registered
    **factory**, the factory is called with those params and its return is the
    configured instance; a factory that raises resolves to a DENY naming the
    error, so a mistyped threshold refuses rather than defaulting. If the name
    is a plain policy function, ``params`` are meaningless to it — and that is
    also a DENY rather than a shrug, because parameters accepted and ignored are
    a limit the operator believes is in force and which is not.
    """
    config = config if config is not None else load_config()
    entries = config.get("chain") or []
    resolved: list[tuple[str, PolicyFunction]] = []
    for entry in entries:
        params: dict[str, Any] = {}
        if isinstance(entry, str):
            name, enabled = entry, True
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "")
            enabled = bool(entry.get("enabled", True))
            raw_params = entry.get("params")
            if raw_params is not None and not isinstance(raw_params, dict):
                resolved.append((
                    name or "?",
                    _config_error_policy(
                        name or "?",
                        f"params must be a mapping, got {type(raw_params).__name__}",
                    ),
                ))
                continue
            params = dict(raw_params or {})
        else:
            continue
        if not name or not enabled:
            continue

        key = name.strip().lower()
        factory = get_policy_factory(name)
        if factory is not None:
            try:
                resolved.append((key, factory(params)))
            except Exception as exc:  # noqa: BLE001 — a bad config is not a yes
                logger.error(
                    "policy_engine: policy %r could not be built from its "
                    "params: %s", name, exc,
                )
                resolved.append((key, _config_error_policy(name, str(exc))))
            continue

        fn = get_policy(name)
        if fn is None:
            logger.error("policy_engine: chain names unregistered policy %r", name)
            resolved.append((name, _missing_policy(name)))
            continue
        if params:
            logger.error(
                "policy_engine: policy %r takes no params but the chain gave it "
                "%s", name, ", ".join(sorted(params)),
            )
            resolved.append((key, _config_error_policy(
                name,
                f"it is not a configurable policy, but the chain passed params "
                f"({', '.join(sorted(params))}) which would be ignored",
            )))
            continue
        resolved.append((key, fn))
    return resolved


def _missing_policy(name: str) -> PolicyFunction:
    def _deny(_event: PolicyEvent) -> PolicyDecision:
        return PolicyDecision(
            DENY,
            f"policy {name!r} is named in {CONFIG_FILENAME} but is not registered",
            policy=name,
            rule="unregistered_policy",
        )

    return _deny


def _config_error_policy(name: str, error: str) -> PolicyFunction:
    """A policy that DENYs because its own configuration could not be used.

    The alternative — falling back to a default limit, or dropping the entry —
    is how a threshold nobody chose ends up in force, or how a rule the operator
    wrote ends up enforcing nothing. Neither is quiet in the way it needs to be
    loud, so a broken instance refuses and says why.
    """

    def _deny(_event: PolicyEvent) -> PolicyDecision:
        return PolicyDecision(
            DENY,
            f"policy {name!r} is misconfigured in {CONFIG_FILENAME}: {error}",
            policy=name,
            rule="policy_config_error",
            detail=error,
        )

    return _deny


def evaluate(
    event: PolicyEvent,
    policies: Optional[Sequence[tuple[str, PolicyFunction]] | Iterable[Any]] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> ChainResult:
    """Run the policy chain over ``event`` and return one answer.

    * Policies run in configured order.
    * **A DENY short-circuits** — the rest of the chain is not consulted.
    * ASK does not short-circuit, so a later DENY can still win.
    * The result is the strictest effect returned, then raised to the config
      floor for this event type if that floor is stricter.
    * A policy that raises resolves to ``on_policy_error`` (``deny`` by
      default) and, if that is DENY, short-circuits like any other DENY.
    """
    config = config if config is not None else load_config()
    chain = list(policies) if policies is not None else resolve_chain(config)
    on_error = _on_policy_error(config)

    decisions: list[PolicyDecision] = []
    winner: Optional[PolicyDecision] = None
    short_circuited = False

    for item in chain:
        if isinstance(item, tuple) and len(item) == 2:
            name, fn = str(item[0]), item[1]
        else:
            name, fn = getattr(item, "__name__", "policy"), item
        try:
            decision = _normalize(fn(event), name)
        except Exception as exc:  # noqa: BLE001 — a broken policy is not a yes
            logger.warning("policy_engine: policy %s raised, %s: %s", name, on_error, exc)
            decision = PolicyDecision(
                on_error, f"policy raised: {exc}", policy=name, rule="policy_error"
            )
        decisions.append(decision)
        if winner is None or (
            _STRICTNESS[decision.effect] > _STRICTNESS[winner.effect]
        ):
            winner = decision
        if decision.effect == DENY:
            short_circuited = True
            break

    if winner is None:
        # An empty chain authorises nothing. Same reasoning as the gate's empty
        # policy: nobody said yes, so nobody said yes.
        winner = PolicyDecision(
            ASK,
            f"no policy is enabled in {CONFIG_FILENAME}; nothing has vouched for "
            f"{event.target!r}",
            policy="policy_chain",
            rule="empty_chain",
        )
        decisions.append(winner)

    effect = winner.effect
    floor = _floor_for(config, event.event_type)
    floor_applied = ""
    reason = winner.reason
    policy_name = winner.policy or "policy_chain"
    if _STRICTNESS[floor] > _STRICTNESS[effect]:
        floor_applied = floor
        reason = (
            f"{reason} (raised to the {floor} floor configured for "
            f"{event.event_type!r})" if reason else
            f"raised to the {floor} floor configured for {event.event_type!r}"
        )
        effect = floor

    return ChainResult(
        effect=effect,
        reason=reason,
        policy=policy_name,
        decisions=tuple(decisions),
        short_circuited=short_circuited,
        floor_applied=floor_applied,
    )


# ---------------------------------------------------------------------------
# Audit — reuse the gate's writer, which already never stores argument values
# ---------------------------------------------------------------------------
def _classification_for(result: ChainResult, target: str) -> gate.Classification:
    """Project a chain result onto the shape ``record_decision`` already writes.

    ``rule`` is prefixed ``policy_chain:`` so a reader can tell a machine
    evaluation from a human approval in the same table, and the reversibility
    tier is carried through unchanged when the chain established one.
    """
    return gate.Classification(
        tool_name=target,
        tier=result.tier(),
        rule=f"policy_chain:{result.policy}:{result.effect}",
        detail="; ".join(d.summary() for d in result.decisions),
        requires_approval=result.effect != ALLOW,
    )


def record_chain_decision(
    *,
    event: PolicyEvent,
    result: ChainResult,
    approved: bool,
    reason: str,
    actor: str,
    mode: str,
) -> bool:
    """Append one policy-layer decision to the append-only ``agent_approval_log``.

    Deliberately delegates to :func:`approval_gate.record_decision` rather than
    issuing its own INSERT. That function is the one place that knows the
    no-argument-values rule — key names plus a SHA-256 of the flattened input —
    and a second writer here would be a second chance to break it.
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
def build_policy_hook(
    *,
    approver: Optional[gate.Approver] = None,
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    policies: Optional[Sequence[tuple[str, PolicyFunction]]] = None,
    session_id: str = "",
    session_state: Optional[dict[str, Any]] = None,
    usage: Optional[dict[str, Any]] = None,
    on_event: Optional[Callable[[gate.GateEvent], None]] = None,
    consult_pre_tool_check: bool = True,
) -> Callable[[str, Any], Optional[str]]:
    """Build a ``PreToolUseHook`` that runs the policy chain.

    Same contract as :func:`approval_gate.build_approval_hook` — return ``None``
    to let the call run, or a string to halt it — so it is a drop-in for
    ``run_agent_loop(approval_gate=...)``. The difference is what happens in
    between:

    * a **hard block** from ``.claude/hooks/pre_tool_use.py`` still wins first
      and is never escalated to a human;
    * a chain **DENY** blocks outright and is *never* offered to the approver.
      That is the capability the reversibility gate does not have: the gate's
      strongest verdict is "ask", and some calls should not be answerable;
    * a chain **ASK** goes to the approver exactly as before, honouring
      ``dry_run`` / ``off``;
    * a chain **ALLOW** proceeds.

    ``session_state`` and ``usage`` are passed through to every policy on every
    call. They are handed in by reference so a caller can keep counters across
    a session; this module reads them and does not write them.
    """
    resolved_mode = gate.resolve_mode(mode)
    who = gate.resolve_actor(actor)
    cfg = config if config is not None else load_config()
    chain = list(policies) if policies is not None else resolve_chain(cfg)
    approve = approver or gate.console_approver
    state = session_state if session_state is not None else {}
    counters = usage if usage is not None else {}
    log_allow = _log_allow(cfg)

    def _emit(event: gate.GateEvent) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception as exc:  # noqa: BLE001 — an observer never blocks
                logger.debug("policy_engine: on_event raised: %s", exc)

    def _finish(
        pol_event: PolicyEvent,
        result: ChainResult,
        approved: bool,
        reason: str,
        who_decided: str,
        *,
        record: bool = True,
    ) -> Optional[str]:
        recorded = False
        if record:
            recorded = record_chain_decision(
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
                requires_approval=result.effect != ALLOW,
                allowed=approved,
                reason=reason,
                actor=who_decided,
                recorded=recorded,
                extra={
                    "effect": result.effect,
                    "policy": result.policy,
                    "rule": f"policy_chain:{result.policy}",
                    "mode": resolved_mode,
                    "short_circuited": result.short_circuited,
                },
            )
        )
        if approved:
            return None
        return (
            f"BLOCKED by the policy chain: {pol_event.target} was refused by "
            f"{result.policy} ({result.effect.upper()}). {reason}. Do not retry "
            "this call — ask the operator, or choose an allowed alternative."
        )

    def hook(tool_name: str, tool_input: Any) -> Optional[str]:
        if not isinstance(tool_input, dict):
            tool_input = {} if tool_input is None else {"value": tool_input}
        pol_event = PolicyEvent(
            event_type=EVENT_TOOL_CALL,
            target=tool_name,
            arguments=tool_input,
            actor=who,
            usage=counters,
            session_state=state,
            session_id=session_id,
        )
        result = evaluate(pol_event, chain, config=cfg)

        # 1. Hard blocks are not policy questions and not approval questions.
        if consult_pre_tool_check:
            blocked, why = gate._hard_block(tool_name, tool_input)
            if blocked:
                return _finish(
                    pol_event, result, False, f"hard block: {why}", who
                )

        # 2. DENY. Never offered to the approver — that is the point of DENY.
        if result.effect == DENY:
            return _finish(pol_event, result, False, result.reason, who)

        # 3. ALLOW.
        if result.effect == ALLOW:
            return _finish(
                pol_event, result, True, result.reason or "allowed by policy chain",
                who, record=log_allow,
            )

        # 4. ASK — the gate's existing escalation path, unchanged.
        if resolved_mode == gate.MODE_OFF:
            return _finish(
                pol_event, result, True,
                f"approval gate mode=off (escape hatch); chain said ask: {result.reason}",
                who,
            )
        if resolved_mode == gate.MODE_DRY_RUN:
            return _finish(
                pol_event, result, True,
                f"dry_run: would have asked; chain said ask: {result.reason}", who,
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
            logger.warning("policy_engine: approver raised, denying: %s", exc)
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
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate the agent policy chain (exa-policy-01)."
    )
    parser.add_argument("--evaluate", metavar="TARGET", help="tool/target to evaluate")
    parser.add_argument("--input", default="{}", help="event arguments as JSON")
    parser.add_argument("--event-type", default=EVENT_TOOL_CALL)
    parser.add_argument("--actor", default="", help="who is asking")
    parser.add_argument("--list-policies", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.list_policies:
        config = load_config(refresh=True)
        payload = {
            "config_path": str(_find_config_path() or ""),
            "on_policy_error": _on_policy_error(config),
            "floors": config.get("floors") or {},
            "registered": list_policies(),
            "registered_factories": list_policy_factories(),
            "chain": [name for name, _ in resolve_chain(config)],
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

    result = evaluate(
        PolicyEvent(
            event_type=args.event_type,
            target=args.evaluate,
            arguments=arguments,
            actor=gate.resolve_actor(args.actor or None),
        ),
        config=load_config(refresh=True),
    )
    if args.json:
        print(json.dumps(
            {
                "effect": result.effect,
                "reason": result.reason,
                "policy": result.policy,
                "tier": result.tier(),
                "short_circuited": result.short_circuited,
                "floor_applied": result.floor_applied,
                # Key names only — argument values are never emitted.
                "decisions": [
                    {
                        "policy": d.policy, "effect": d.effect,
                        "reason": d.reason, "rule": d.rule,
                    }
                    for d in result.decisions
                ],
            },
            indent=2,
        ))
    else:
        print(result.summary())
        for decision in result.decisions:
            print(f"  {decision.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
