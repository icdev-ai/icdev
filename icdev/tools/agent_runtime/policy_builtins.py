# CUI // SP-CTI
"""The three builtin policies ICDEV could not express before (exa-policy-03).

:mod:`tools.agent_runtime.policy_engine` (exa-policy-01) gave a policy a
vocabulary — ALLOW / ASK / DENY plus a reason — and
:mod:`tools.agent_runtime.policy_composition` (exa-policy-02) gave it three
levels and somewhere to count. Neither shipped a policy that *uses* either. This
module is the three that do, ported from omnigent:

``max_tool_calls_per_session``
    A hard cap on how many tool calls one session may make. Needs the session
    state from exa-policy-02, because a per-session count is the one thing a
    per-call regex definitionally cannot hold.

``git_write_allowlist``
    A git write allowlist parameterised **by repo and by branch**. ICDEV's
    existing regex tiers in ``args/agent_approval_policy.yaml`` match a tool
    name and a content pattern, so they can say "``git push`` is irreversible";
    they cannot say "``git push`` is fine to ``feat/*`` but not to ``main``",
    because that is a statement about an *argument*, not about the tool.

``risk_score``
    A per-session risk score accrued across tool calls, escalating the required
    approval level as it crosses configured thresholds. Fifty individually
    benign calls and one benign call are the same event to a stateless gate;
    they are not the same event.

## Instances, not copies

These are registered as **factories**, not as policy functions. A chain entry
carries ``params:`` and the factory builds one configured *instance*::

    chain:
      - name: git_write_allowlist
        params:
          repos: ["*"]
          deny_branches: ["main", "master"]

so two instances with different parameters are two config entries, not two
copies of a Python function. That is omnigent's ``factory_params`` shape, and it
is the reason :func:`policy_engine.resolve_chain` grew factory support rather
than this module growing a second registry.

Because ``params`` are read per chain ENTRY and every level resolves its own
chain, an instance is configurable per level for free: the server can cap a
session at 500 calls and a user can cap themselves at 20, and — composition
being additive — the strictest of the two is what happens.

## No threshold has a Python default

A required threshold that is *missing* from the config is a
:class:`PolicyConfigError`, which :func:`policy_engine.resolve_chain` turns into
a DENY that names the error. It is deliberately **not** a default.

A default limit in Python is precisely how a "configured" limit becomes a number
nobody chose and nobody reviewed: the YAML looks authoritative, the number in
force came from a source file, and the two disagree silently. So ``limit``,
``ask_at`` and ``deny_at`` have no defaults. Structural parameters that are not
thresholds (which state key to count in, which argument keys carry a branch
name) do have defaults, because they change nothing about how much is permitted.

## Switching one off

``enabled: false`` on the chain entry, per level — the mechanism
:func:`policy_engine.resolve_chain` already had. Each of the three is a separate
entry, so each is independently switchable without touching the other two.

## Where the counters actually land

The stateful two (``max_tool_calls_per_session`` and ``risk_score``) accrue by
returning ``state_updates``, which are applied by
:func:`policy_composition._leveled` as the chain runs. That is the *composed*
hook — :func:`policy_composition.build_composed_policy_hook`. The single-level
:func:`policy_engine.build_policy_hook` carries state updates but does not apply
them, as its own docstring says.

Rather than trust that distinction to documentation, both stateful policies
**self-check**: :func:`_check_liveness` notices that a policy has emitted an
increment for a session and that the value it reads back is still unset, and
warns once naming the cause. A cap whose counter never increments is a cap that
never fires while reporting itself enabled — the exact defect this card exists
to stop, and it would otherwise be invisible.

CLI::

    python tools/agent_runtime/policy_builtins.py --list --json
    python tools/agent_runtime/policy_builtins.py --describe risk_score --json
"""
from __future__ import annotations

import fnmatch
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. parents[2] is whatever holds this file's `tools` package: the
# repo root in tools/, and <repo>/icdev in the icdev/ mirror.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime import approval_gate as gate
from tools.agent_runtime import policy_engine as pe
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.policy_builtins")

POLICY_MAX_TOOL_CALLS = "max_tool_calls_per_session"
POLICY_GIT_WRITE_ALLOWLIST = "git_write_allowlist"
POLICY_RISK_SCORE = "risk_score"

BUILTIN_POLICIES: tuple[str, ...] = (
    POLICY_MAX_TOOL_CALLS,
    POLICY_GIT_WRITE_ALLOWLIST,
    POLICY_RISK_SCORE,
)

# Effects a policy instance may be configured to return when its rule fires.
# `allow` is absent on purpose and is rejected by _require_effect: a policy
# configured to allow when it fires is a policy that does nothing, spelled in a
# way that reads like enforcement.
CONFIGURABLE_EFFECTS: tuple[str, ...] = (pe.DENY, pe.ASK)


class PolicyConfigError(ValueError):
    """A policy instance could not be built from its ``params``.

    Raised by a factory at chain-resolution time, caught by
    :func:`policy_engine.resolve_chain`, and turned into a DENY naming this
    error. It is never swallowed: a mistyped threshold must not resolve to "no
    limit".
    """


# ---------------------------------------------------------------------------
# Param helpers — every one of them fails closed
# ---------------------------------------------------------------------------
def _require_number(
    params: dict[str, Any], key: str, policy: str, *, minimum: Optional[float] = None
) -> float:
    """A required numeric parameter. No default, by design — see the module docs."""
    if key not in params or params.get(key) is None:
        raise PolicyConfigError(
            f"{policy}: params.{key} is required and has no default; state the "
            f"threshold in the config rather than relying on a value in Python"
        )
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyConfigError(
            f"{policy}: params.{key} must be a number, got {value!r}"
        )
    if minimum is not None and value < minimum:
        raise PolicyConfigError(
            f"{policy}: params.{key} must be >= {minimum}, got {value!r}"
        )
    return float(value)


def _optional_number(
    params: dict[str, Any], key: str, policy: str, default: float
) -> float:
    value = params.get(key)
    if value is None:
        return float(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyConfigError(
            f"{policy}: params.{key} must be a number, got {value!r}"
        )
    return float(value)


def _require_effect(params: dict[str, Any], key: str, policy: str, default: str) -> str:
    """An effect this instance returns when its rule fires. Never ``allow``."""
    raw = params.get(key)
    value = str(default if raw is None else raw).strip().lower()
    if value not in CONFIGURABLE_EFFECTS:
        raise PolicyConfigError(
            f"{policy}: params.{key} must be one of "
            f"{', '.join(CONFIGURABLE_EFFECTS)}, got {raw!r}. A policy that "
            f"allows when its rule fires is not a policy."
        )
    return value


def _string_list(params: dict[str, Any], key: str, policy: str, default: Any) -> tuple[str, ...]:
    raw = params.get(key, default)
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise PolicyConfigError(
            f"{policy}: params.{key} must be a list of strings, got {type(raw).__name__}"
        )
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _weight_map(params: dict[str, Any], key: str, policy: str) -> dict[str, float]:
    raw = params.get(key) or {}
    if not isinstance(raw, dict):
        raise PolicyConfigError(
            f"{policy}: params.{key} must be a mapping of pattern -> number, "
            f"got {type(raw).__name__}"
        )
    weights: dict[str, float] = {}
    for pattern, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PolicyConfigError(
                f"{policy}: params.{key}[{pattern!r}] must be a number, got {value!r}"
            )
        weights[str(pattern)] = float(value)
    return weights


def _bool(params: dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key)
    return default if value is None else bool(value)


def _unknown_params(params: dict[str, Any], known: tuple[str, ...], policy: str) -> None:
    """Reject a param this instance does not understand.

    A typo'd key (``dey_branches``) that is quietly ignored is a rule the
    operator believes is in force and which is not — the same class of failure
    as a chain naming an unregistered policy, and it gets the same treatment.
    """
    unknown = sorted(set(params) - set(known))
    if unknown:
        raise PolicyConfigError(
            f"{policy}: unknown params {', '.join(unknown)}; known params are "
            f"{', '.join(sorted(known))}"
        )


# ---------------------------------------------------------------------------
# Liveness self-check — a counter that never increments is a limit that never
# fires, and it fires nothing silently
# ---------------------------------------------------------------------------
_EMITTED: dict[tuple[str, str], int] = {}
_LIVENESS_WARNED: set[tuple[str, str]] = set()


def _note_emitted(session_id: str, state_key: str) -> None:
    if not session_id:
        return
    key = (session_id, state_key)
    _EMITTED[key] = _EMITTED.get(key, 0) + 1


def _check_liveness(session_id: str, state_key: str, observed: Any, policy: str) -> None:
    """Warn once if this policy's own increments are not coming back.

    Called with what the policy READ this call, having previously emitted an
    increment for the same session and key. If the runtime applies
    ``state_updates`` (the composed hook does) the value is back by now. If it
    does not, the counter is stuck at zero forever and the limit can never be
    reached — while ``--list-policies`` still reports the policy enabled.
    """
    if not session_id:
        return
    key = (session_id, state_key)
    if _EMITTED.get(key, 0) < 1 or key in _LIVENESS_WARNED:
        return
    if isinstance(observed, (int, float)) and not isinstance(observed, bool) and observed > 0:
        return
    _LIVENESS_WARNED.add(key)
    logger.warning(
        "policy_builtins: %s emitted an increment for session %s key %r but reads "
        "back %r, so the counter is not being applied and this limit can never "
        "fire. state_updates are applied by policy_composition (the composed "
        "hook); policy_engine.build_policy_hook carries them without applying "
        "them.", policy, session_id, state_key, observed,
    )


def reset_liveness_tracking() -> None:
    """Clear the self-check bookkeeping (tests, and a deliberate session reset)."""
    _EMITTED.clear()
    _LIVENESS_WARNED.clear()


def _no_session(policy: str, require_session: bool, event: pe.PolicyEvent) -> Optional[pe.PolicyDecision]:
    """The decision for an event with no session id, or ``None`` to continue.

    Without a session id :func:`policy_composition.get_session_state` hands out
    a throwaway, unpersisted state, so a per-session counter never accumulates
    and a per-session limit silently becomes no limit at all. ``require_session``
    decides whether that is a refusal or an accepted gap; it defaults to
    refusing, because "the limit is not enforceable here" is a fact the caller
    should have to acknowledge in config rather than discover in an incident.
    """
    if event.session_id:
        return None
    if not require_session:
        logger.warning(
            "policy_builtins: %s cannot enforce a per-session limit for an event "
            "with no session_id; require_session is false, so this call is not "
            "counted", policy,
        )
        return pe.PolicyDecision(
            pe.ALLOW,
            "no session id, so there is no session to count against "
            "(require_session: false)",
            policy=policy,
            rule="no_session_id",
        )
    return pe.PolicyDecision(
        pe.DENY,
        f"{policy} needs a session id to count against and this event has none, "
        f"so the limit cannot be enforced",
        policy=policy,
        rule="no_session_id",
    )


# ---------------------------------------------------------------------------
# 1. max_tool_calls_per_session
# ---------------------------------------------------------------------------
_MAX_CALLS_PARAMS = (
    "limit", "state_key", "on_exceed", "event_types", "require_session",
)


def max_tool_calls_policy(params: dict[str, Any]) -> pe.PolicyFunction:
    """Build a per-session hard cap on total tool invocations.

    ``limit`` is required and has no default. ``on_exceed`` is ``deny`` or
    ``ask`` — a cap that asks is a legitimate configuration (a human may raise
    it), a cap that allows is not a cap.

    The counter is incremented only on a call this policy did **not** refuse: a
    refused call never runs, so counting it would charge the session for work it
    was not allowed to do. It is still an over-count in one direction — a call
    this policy allows and a *later* policy denies is counted — and that is the
    safe direction, because it makes the cap fire sooner rather than later. Said
    plainly rather than papered over: this counts calls *evaluated and not
    refused here*, which is the closest thing to "calls made" available at the
    point the decision has to be taken.
    """
    policy = POLICY_MAX_TOOL_CALLS
    _unknown_params(params, _MAX_CALLS_PARAMS, policy)
    limit = _require_number(params, "limit", policy, minimum=1)
    state_key = str(params.get("state_key") or "tool_calls").strip() or "tool_calls"
    on_exceed = _require_effect(params, "on_exceed", policy, pe.DENY)
    event_types = _string_list(params, "event_types", policy, None)
    require_session = _bool(params, "require_session", True)

    def fn(event: pe.PolicyEvent) -> Optional[pe.PolicyDecision]:
        if event_types and event.event_type not in event_types:
            return None  # not this instance's business — abstain, do not count
        gap = _no_session(policy, require_session, event)
        if gap is not None:
            return gap

        used = event.session_state.get(state_key, 0)
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            used = 0
        _check_liveness(event.session_id, state_key, used, policy)

        if used >= limit:
            return pe.PolicyDecision(
                on_exceed,
                f"session {event.session_id} has made {int(used)} tool calls and "
                f"the cap is {int(limit)}",
                policy=policy,
                rule="max_tool_calls_per_session",
                detail=f"{state_key}={int(used)} limit={int(limit)}",
            )

        _note_emitted(event.session_id, state_key)
        return pe.PolicyDecision(
            pe.ALLOW,
            f"{int(used) + 1} of {int(limit)} tool calls used this session",
            policy=policy,
            rule="under_cap",
            state_updates=({"key": state_key, "action": "increment", "value": 1},),
        )

    fn.__name__ = policy
    return fn


# ---------------------------------------------------------------------------
# 2. git_write_allowlist
# ---------------------------------------------------------------------------
_GIT_PARAMS = (
    "repos", "allow_branches", "deny_branches", "operations", "command_keys",
    "branch_keys", "repo_keys", "on_violation", "on_unknown",
)

# Shell separators. A branch is extracted per segment because `git checkout main
# && git push` is one command string carrying two git invocations, and reading
# only the first would miss the one that writes.
_SEPARATORS = ("&&", "||", ";", "|", "&")


def _segments(command: str) -> list[list[str]]:
    """Split a command string into shell segments of tokens. Never raises."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # An unbalanced quote. Fall back to a whitespace split rather than give
        # up: a malformed command is exactly when an allowlist should still get
        # a look at what it can see.
        tokens = command.split()
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_ref(ref: str) -> str:
    ref = ref.strip()
    if ref.startswith("+"):
        ref = ref[1:]
    if ":" in ref:                       # src:dst — the DESTINATION is what is written
        ref = ref.partition(":")[2]
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    return ref.strip()


def _looks_like_url(token: str) -> bool:
    return "://" in token or (":" in token and "@" in token.split(":", 1)[0])


def _parse_git_writes(command: str, operations: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every governed git write in ``command``, as ``{operation, branch, repo}``.

    ``branch``/``repo`` are ``None`` when the command does not say — ``git push``
    with no refspec writes the current branch, whose name is simply not in the
    string. That is reported as unknown rather than guessed, because guessing
    "probably a feature branch" is how an allowlist lets a push to ``main``
    through.
    """
    found: list[dict[str, Any]] = []
    for tokens in _segments(command):
        if "git" not in [t.lower() for t in tokens]:
            continue
        lowered = [t.lower() for t in tokens]
        git_at = lowered.index("git")
        operation = None
        op_at = -1
        for index in range(git_at + 1, len(tokens)):
            if lowered[index] in operations:
                operation, op_at = lowered[index], index
                break
        if operation is None:
            continue

        positionals: list[str] = []
        skip_next = False
        for token in tokens[op_at + 1:]:
            if skip_next:
                skip_next = False
                continue
            if token.startswith("-"):
                # Options that take a separate value; everything else is a flag.
                if token in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
                    skip_next = True
                continue
            positionals.append(token)

        repo = None
        refspecs = list(positionals)
        if refspecs:
            first = refspecs[0]
            if _looks_like_url(first):
                repo, refspecs = first, refspecs[1:]
            elif len(refspecs) > 1 or "/" not in first or first in ("origin", "upstream"):
                # A bare remote NAME (`origin`) identifies no repo on its own.
                refspecs = refspecs[1:]

        branches = [_strip_ref(ref) for ref in refspecs]
        branches = [b for b in branches if b and not b.startswith("refs/tags/")]
        if branches:
            for branch in branches:
                found.append({"operation": operation, "branch": branch, "repo": repo})
        else:
            found.append({"operation": operation, "branch": None, "repo": repo})
    return found


def _first_value(arguments: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _matches_any(value: str, patterns: tuple[str, ...], *, ignore_case: bool) -> bool:
    subject = value.lower() if ignore_case else value
    for pattern in patterns:
        candidate = pattern.lower() if ignore_case else pattern
        if fnmatch.fnmatchcase(subject, candidate):
            return True
    return False


def git_write_allowlist_policy(params: dict[str, Any]) -> pe.PolicyFunction:
    """Build a git write allowlist scoped by repo and by branch.

    Order of decision, per git write found in the call:

    1. ``deny_branches`` — checked **first** and it wins. Matched
       case-INSENSITIVELY, because a deny list that misses ``Main`` is a deny
       list with a hole in it.
    2. ``allow_branches`` — allowlist semantics: a branch that matches nothing
       here is not allowed. Matched case-SENSITIVELY, because an allow list that
       accepts ``Feat/x`` for ``feat/*`` is an allow list with a hole in it.
       The two directions are deliberately not symmetric; each is the
       fail-closed reading of its own list.
    3. Anything the command does not state — the branch of a bare ``git push``,
       or the repo when this instance governs specific ``repos`` — resolves to
       ``on_unknown``, never to allow.

    A call carrying no governed git write **abstains** (returns ``None``), which
    the engine normalises to an ALLOW that authorises nothing and lets the rest
    of the chain speak. This policy has no opinion about ``read_file``.
    """
    policy = POLICY_GIT_WRITE_ALLOWLIST
    _unknown_params(params, _GIT_PARAMS, policy)
    repos = _string_list(params, "repos", policy, ["*"])
    allow_branches = _string_list(params, "allow_branches", policy, None)
    deny_branches = _string_list(params, "deny_branches", policy, None)
    operations = tuple(
        op.lower() for op in _string_list(params, "operations", policy, ["push"])
    )
    command_keys = _string_list(
        params, "command_keys", policy, ["command", "cmd", "script"]
    )
    branch_keys = _string_list(
        params, "branch_keys", policy, ["branch", "ref", "target_branch"]
    )
    repo_keys = _string_list(
        params, "repo_keys", policy, ["repo", "repository", "remote_url"]
    )
    on_violation = _require_effect(params, "on_violation", policy, pe.DENY)
    on_unknown = _require_effect(params, "on_unknown", policy, pe.ASK)

    if not operations:
        raise PolicyConfigError(
            f"{policy}: params.operations is empty, so this instance governs "
            f"nothing; remove the entry or name the git operations to govern"
        )
    if not allow_branches and not deny_branches:
        raise PolicyConfigError(
            f"{policy}: neither params.allow_branches nor params.deny_branches "
            f"is set, so this instance would refuse every git write it sees; "
            f"state at least one list"
        )

    governs_all_repos = not repos or set(repos) == {"*"}

    def _judge_repo(repo: Optional[str]) -> Optional[pe.PolicyDecision]:
        if governs_all_repos:
            return None
        if not repo:
            return pe.PolicyDecision(
                on_unknown,
                f"cannot tell which repo this git write targets, and "
                f"{policy} governs only {', '.join(repos)}",
                policy=policy,
                rule="unknown_repo",
            )
        if not _matches_any(repo, repos, ignore_case=True):
            return pe.PolicyDecision(
                pe.ALLOW, f"repo {repo} is outside this instance's scope",
                policy=policy, rule="out_of_scope",
            )
        return None

    def _judge_branch(
        operation: str, branch: Optional[str], repo: Optional[str]
    ) -> pe.PolicyDecision:
        where = f" in {repo}" if repo else ""
        if branch is None:
            return pe.PolicyDecision(
                on_unknown,
                f"git {operation} does not name the branch it writes{where}, so "
                f"it cannot be matched against the allowlist",
                policy=policy,
                rule="unknown_branch",
            )
        if deny_branches and _matches_any(branch, deny_branches, ignore_case=True):
            return pe.PolicyDecision(
                on_violation,
                f"git {operation} to {branch}{where} is denied "
                f"(deny_branches: {', '.join(deny_branches)})",
                policy=policy,
                rule="denied_branch",
                detail=f"branch={branch}",
            )
        if allow_branches:
            if _matches_any(branch, allow_branches, ignore_case=False):
                return pe.PolicyDecision(
                    pe.ALLOW,
                    f"git {operation} to {branch}{where} is allowed "
                    f"(allow_branches: {', '.join(allow_branches)})",
                    policy=policy,
                    rule="allowed_branch",
                )
            return pe.PolicyDecision(
                on_violation,
                f"git {operation} to {branch}{where} matches no entry in "
                f"allow_branches ({', '.join(allow_branches)})",
                policy=policy,
                rule="not_allowlisted",
                detail=f"branch={branch}",
            )
        return pe.PolicyDecision(
            pe.ALLOW,
            f"git {operation} to {branch}{where} is not on the deny list",
            policy=policy,
            rule="not_denied",
        )

    def fn(event: pe.PolicyEvent) -> Optional[pe.PolicyDecision]:
        arguments = event.arguments if isinstance(event.arguments, dict) else {}
        writes: list[dict[str, Any]] = []

        # A structured tool that names its branch outright — no parsing needed,
        # and checked first because it is unambiguous where a string is not.
        explicit_branch = _first_value(arguments, branch_keys)
        explicit_repo = _first_value(arguments, repo_keys)
        if explicit_branch:
            writes.append({
                "operation": operations[0],
                "branch": explicit_branch,
                "repo": explicit_repo,
            })

        for key in command_keys:
            command = arguments.get(key)
            if isinstance(command, (list, tuple)):
                command = " ".join(str(part) for part in command)
            if isinstance(command, str) and command.strip():
                for write in _parse_git_writes(command, operations):
                    if write["repo"] is None:
                        write["repo"] = explicit_repo
                    writes.append(write)

        if not writes:
            return None  # no governed git write here — no opinion

        # The strictest judgement over every write in the call. One denied push
        # in a chained command denies the command: the segments run together.
        verdict: Optional[pe.PolicyDecision] = None
        for write in writes:
            decision = _judge_repo(write["repo"]) or _judge_branch(
                write["operation"], write["branch"], write["repo"]
            )
            if verdict is None or (
                pe._STRICTNESS[decision.effect] > pe._STRICTNESS[verdict.effect]
            ):
                verdict = decision
        return verdict

    fn.__name__ = policy
    return fn


# ---------------------------------------------------------------------------
# 3. risk_score
# ---------------------------------------------------------------------------
_RISK_PARAMS = (
    "ask_at", "deny_at", "weights", "tier_weights", "default_weight",
    "state_key", "require_session", "event_types",
)


def risk_score_policy(params: dict[str, Any]) -> pe.PolicyFunction:
    """Build a per-session risk accrual that escalates as the score climbs.

    Each call is worth points: an explicit ``weights`` pattern on the tool name
    if one matches, else the ``tier_weights`` entry for the reversibility tier
    :func:`approval_gate.classify` gives the call, else ``default_weight``.
    Weighting by tier is why this reuses ``classify`` rather than re-listing
    every tool — the tiers are already maintained in
    ``args/agent_approval_policy.yaml``, and a second list of dangerous tools
    here is a second list to keep current.

    The proposed call's own weight is added **before** the comparison, so the
    decision is about the state the session would be in if the call ran, not the
    state it is in now. ``ask_at`` and ``deny_at`` are both required and
    ``deny_at`` must not be below ``ask_at``.

    Below ``ask_at`` the policy abstains rather than returning a cheerful ALLOW:
    a low risk score is not a reason to run something the reversibility policy
    would have stopped, and in a chain that takes the strictest answer an
    abstention is exactly the right weight to carry.

    As with the cap, a call this policy refuses does not accrue — the tool never
    ran, so no risk was taken. The consequence is intended: once the score is
    high enough that a given weight would cross ``deny_at``, calls of that weight
    keep being refused, which is what a ceiling is. Cheaper calls can still
    proceed, because the ceiling is on total risk including the proposed action.
    """
    policy = POLICY_RISK_SCORE
    _unknown_params(params, _RISK_PARAMS, policy)
    ask_at = _require_number(params, "ask_at", policy, minimum=0)
    deny_at = _require_number(params, "deny_at", policy, minimum=0)
    if deny_at < ask_at:
        raise PolicyConfigError(
            f"{policy}: params.deny_at ({deny_at}) is below params.ask_at "
            f"({ask_at}), so the deny threshold could never be reached without "
            f"the ask threshold already having fired; did they get swapped?"
        )
    weights = _weight_map(params, "weights", policy)
    tier_weights = _weight_map(params, "tier_weights", policy)
    default_weight = _optional_number(params, "default_weight", policy, 1)
    state_key = str(params.get("state_key") or "risk_score").strip() or "risk_score"
    require_session = _bool(params, "require_session", True)
    event_types = _string_list(params, "event_types", policy, None)

    unknown_tiers = sorted(set(tier_weights) - set(gate.TIERS))
    if unknown_tiers:
        raise PolicyConfigError(
            f"{policy}: params.tier_weights names tiers that do not exist: "
            f"{', '.join(unknown_tiers)}; known tiers are {', '.join(gate.TIERS)}"
        )

    def _weight_for(event: pe.PolicyEvent) -> tuple[float, str]:
        for pattern, value in weights.items():
            if fnmatch.fnmatchcase(event.target, pattern):
                return value, f"weights[{pattern}]"
        if tier_weights:
            try:
                tier = gate.classify(event.target, event.arguments).tier
            except Exception as exc:  # noqa: BLE001 — an unclassifiable call is
                # not a free one. Fall through to the default weight and say so.
                logger.warning(
                    "policy_builtins: %s could not classify %s (%s); using "
                    "default_weight", policy, event.target, exc,
                )
            else:
                if tier in tier_weights:
                    return tier_weights[tier], f"tier_weights[{tier}]"
        return default_weight, "default_weight"

    def fn(event: pe.PolicyEvent) -> Optional[pe.PolicyDecision]:
        if event_types and event.event_type not in event_types:
            return None
        gap = _no_session(policy, require_session, event)
        if gap is not None:
            return gap

        accrued = event.session_state.get(state_key, 0)
        if isinstance(accrued, bool) or not isinstance(accrued, (int, float)):
            accrued = 0
        _check_liveness(event.session_id, state_key, accrued, policy)

        weight, source = _weight_for(event)
        projected = accrued + weight
        detail = (
            f"{state_key}={accrued:g} + {weight:g} ({source}) = {projected:g}; "
            f"ask_at={ask_at:g} deny_at={deny_at:g}"
        )

        if projected >= deny_at:
            return pe.PolicyDecision(
                pe.DENY,
                f"session risk would reach {projected:g}, at or above the "
                f"deny threshold of {deny_at:g}",
                policy=policy,
                rule="risk_deny_threshold",
                detail=detail,
            )

        _note_emitted(event.session_id, state_key)
        updates = ({"key": state_key, "action": "increment", "value": weight},)
        if projected >= ask_at:
            return pe.PolicyDecision(
                pe.ASK,
                f"session risk has reached {projected:g}, at or above the ask "
                f"threshold of {ask_at:g}; approval is escalated even though "
                f"this call on its own is worth {weight:g}",
                policy=policy,
                rule="risk_ask_threshold",
                detail=detail,
                state_updates=updates,
            )
        return pe.PolicyDecision(
            pe.ALLOW,
            f"session risk {projected:g} is below the ask threshold of {ask_at:g}",
            policy=policy,
            rule="under_threshold",
            detail=detail,
            state_updates=updates,
        )

    fn.__name__ = policy
    return fn


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
FACTORIES = {
    POLICY_MAX_TOOL_CALLS: max_tool_calls_policy,
    POLICY_GIT_WRITE_ALLOWLIST: git_write_allowlist_policy,
    POLICY_RISK_SCORE: risk_score_policy,
}


def register_builtins(*, replace: bool = True) -> tuple[str, ...]:
    """Register all three factories with the engine. Idempotent."""
    for name, factory in FACTORIES.items():
        pe.register_policy_factory(name, factory, replace=replace)
    return BUILTIN_POLICIES


register_builtins()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
_DESCRIPTIONS = {
    POLICY_MAX_TOOL_CALLS: {
        "summary": "Hard cap on total tool invocations in one session.",
        "required": ["limit"],
        "optional": [
            "state_key", "on_exceed", "event_types", "require_session",
        ],
    },
    POLICY_GIT_WRITE_ALLOWLIST: {
        "summary": "Git write allowlist scoped by repo and branch.",
        "required": ["allow_branches and/or deny_branches"],
        "optional": [
            "repos", "operations", "command_keys", "branch_keys", "repo_keys",
            "on_violation", "on_unknown",
        ],
    },
    POLICY_RISK_SCORE: {
        "summary": "Per-session risk accrual escalating the approval level.",
        "required": ["ask_at", "deny_at"],
        "optional": [
            "weights", "tier_weights", "default_weight", "state_key",
            "require_session", "event_types",
        ],
    },
}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="The builtin agent policies (exa-policy-03)."
    )
    parser.add_argument("--list", action="store_true", help="list the builtins")
    parser.add_argument("--describe", metavar="NAME", help="describe one builtin")
    parser.add_argument(
        "--check", metavar="NAME",
        help="build NAME from --params and report whether the config is valid",
    )
    parser.add_argument("--params", default="{}", help="instance params as JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.describe:
        payload = _DESCRIPTIONS.get(args.describe)
        if payload is None:
            print(f"unknown builtin {args.describe!r}; known: {', '.join(BUILTIN_POLICIES)}")
            return 2
        payload = {"name": args.describe, **payload}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if args.check:
        factory = FACTORIES.get(args.check)
        if factory is None:
            print(f"unknown builtin {args.check!r}; known: {', '.join(BUILTIN_POLICIES)}")
            return 2
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            print(f"--params is not valid JSON: {exc}")
            return 2
        try:
            factory(params if isinstance(params, dict) else {})
        except PolicyConfigError as exc:
            payload = {"name": args.check, "valid": False, "error": str(exc)}
            print(json.dumps(payload, indent=2) if args.json else payload)
            return 1
        payload = {"name": args.check, "valid": True}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if args.list:
        payload = {
            "builtins": [
                {"name": name, **_DESCRIPTIONS[name]} for name in BUILTIN_POLICIES
            ],
            "registered_factories": pe.list_policy_factories(),
        }
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
