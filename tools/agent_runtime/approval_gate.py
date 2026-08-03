# CUI // SP-CTI
"""Approval gate for irreversible agent actions (ars-appr-01).

MEASURED: ``grep -c approval icdev/tools/llm/agent_loop.py`` returned 0. The
agent-loop primitive would execute ``git push``, ``gh pr merge``, a worktree
delete or an append-only DB write with exactly the same ceremony as reading a
file. ICDEV has real irreversible surfaces and CLAUDE.md already names one
absolute prohibition (never delete YouTube videos) precisely because
irreversibility is the risk.

This module classifies a tool call by **reversibility** and halts the
irreversible ones for confirmation.

Three tiers plus the one that matters:

    reversible    reads/searches — allowed
    recoverable   local mutation git or a checkpoint can restore — allowed
    irreversible  publishes, destroys, or writes immutable evidence — HALTS
    unknown       not enumerated — HALTS

``unknown`` halting is the whole design. An allowlist that fails open is
decoration: the dangerous call is the one nobody thought to enumerate, so the
policy's ``default_tier`` is ``unknown`` and ``unknown`` is in
``require_approval_tiers``. A tool has to be *named* to be automatic.

Content patterns escalate **before** the per-tool tier and win, because a generic
shell tool is only as reversible as the string it is handed, and a tool schema's
own ``is_read_only`` flag is an assertion by the caller rather than a fact. They
only *downgrade* for a tool named in ``command_tools`` — a pattern that can make
any call look safe is an allowlist that fails open by accident.

The single exception is a tool the **policy** enumerates ``reversible`` that is
not a generic executor: it is exempt from escalation entirely, because a tool
that does not execute its arguments cannot be made irreversible by them. That
assertion lives in the operator's policy file rather than in developer-authored
schema, which is what makes it trustworthy where ``is_read_only`` was not. See
:func:`classify`.

Precedent reused rather than reinvented:
  - :mod:`tools.agent_runtime.safety` — the SAG ``SafetyGate``: injectable
    ``Approver``, console prompt default, deny-on-EOF, env-resolved mode.
    :func:`assess_risk` there now consults :func:`classify` here, so its
    ``smart`` mode can no longer auto-approve an unenumerated tool as low risk.
  - :func:`tools.airgap.hook_compat.run_pre_tool_check` — the headless
    ``.claude/hooks/pre_tool_use.py`` hard-block list. A hard block is not an
    approval question; this gate defers to it first and never prompts for
    something the hook already refuses.
  - The confirm-then-launch affordance in ``tools/cortex/blueprint.py``
    (proposal → explicit confirmed launch) is the same shape at the UI layer.

Every decision — approved *and* denied — is appended to ``agent_approval_log``
(migration 342, append-only, in ``APPEND_ONLY_TABLES``) with the actor and the
reason. Argument VALUES are never stored: only the argument key names and a
SHA-256 of the input, mirroring ``runtime_invocations`` (migration 341), because
tool arguments can carry CUI and trusting a redactor across every tool shape is
a fail-open bet.

CLI::

    python tools/agent_runtime/approval_gate.py --classify git_push --json
    python tools/agent_runtime/approval_gate.py --classify run_command \\
        --input '{"command": "git push --force"}' --json
    python tools/agent_runtime/approval_gate.py --list-policy --json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.approval_gate")

# --- Tiers -----------------------------------------------------------------
REVERSIBLE = "reversible"
RECOVERABLE = "recoverable"
IRREVERSIBLE = "irreversible"
UNKNOWN = "unknown"
TIERS = (REVERSIBLE, RECOVERABLE, IRREVERSIBLE, UNKNOWN)

# --- Modes -----------------------------------------------------------------
MODE_ENFORCE = "enforce"   # halt and ask (default)
MODE_DRY_RUN = "dry_run"   # record what would have halted, then allow
MODE_OFF = "off"           # allow, still audited — explicit escape hatch
MODES = (MODE_ENFORCE, MODE_DRY_RUN, MODE_OFF)

MODE_ENV = "ICDEV_AGENT_APPROVAL_MODE"
ACTOR_ENV = "ICDEV_APPROVAL_ACTOR"
POLICY_ENV = "ICDEV_AGENT_APPROVAL_POLICY"

AUDIT_TABLE = "agent_approval_log"
POLICY_FILENAME = "agent_approval_policy.yaml"

# Fallback policy used when args/agent_approval_policy.yaml cannot be read.
# Deliberately minimal and deliberately fail-closed: with no tool enumerated,
# every tool is UNKNOWN and every call needs a human. A missing config file must
# never be the reason an irreversible action ran unattended.
_FALLBACK_POLICY: dict[str, Any] = {
    "version": 0,
    "default_tier": UNKNOWN,
    "require_approval_tiers": [IRREVERSIBLE, UNKNOWN],
    "tools": {},
    "command_patterns": {},
    "command_tools": [],
}


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------
def _find_policy_path() -> Optional[Path]:
    """Locate args/agent_approval_policy.yaml.

    Resolved from ``__file__``, never ``os.getcwd()`` — this module is imported
    from worktrees and from CI runners whose cwd is not the repo root
    (CLAUDE.md, "Notes for agents working from worktrees").
    """
    override = os.environ.get(POLICY_ENV)
    if override:
        p = Path(override)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    # tools/agent_runtime/ -> repo root, and icdev/tools/agent_runtime/ -> repo
    # root: walk up rather than hardcoding a parent index for each copy.
    for parent in here.parents:
        candidate = parent / "args" / POLICY_FILENAME
        if candidate.exists():
            return candidate
    return None


_POLICY_CACHE: Optional[dict[str, Any]] = None


def load_policy(*, refresh: bool = False) -> dict[str, Any]:
    """Load (and memoize) the reversibility policy."""
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not refresh:
        return _POLICY_CACHE
    policy = dict(_FALLBACK_POLICY)
    path = _find_policy_path()
    if path is not None:
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                policy = loaded
        except Exception as exc:  # noqa: BLE001 — fail closed, never fail open
            logger.warning(
                "approval_gate: policy %s unreadable (%s); every tool is UNKNOWN",
                path, exc,
            )
    else:
        logger.warning(
            "approval_gate: %s not found; every tool is UNKNOWN", POLICY_FILENAME
        )
    _POLICY_CACHE = policy
    return policy


# Keyed on id(policy), but the entry holds a strong reference to the policy dict
# itself. Without it the dict could be garbage-collected and a later, unrelated
# policy could be allocated at the same address and silently inherit these
# compiled patterns.
_PATTERN_CACHE: dict[
    int, tuple[dict[str, Any], list[tuple[str, re.Pattern[str], str]]]
] = {}


def _compiled_patterns(policy: dict[str, Any]) -> list[tuple[str, re.Pattern[str], str]]:
    """Compile ``command_patterns`` once per policy object into (tier, rx, detail)."""
    key = id(policy)
    cached = _PATTERN_CACHE.get(key)
    if cached is not None and cached[0] is policy:
        return cached[1]
    out: list[tuple[str, re.Pattern[str], str]] = []
    raw = policy.get("command_patterns") or {}
    # Most-severe first so the first match is also the worst match.
    for tier in (IRREVERSIBLE, RECOVERABLE, REVERSIBLE):
        for entry in raw.get(tier) or []:
            if isinstance(entry, str):
                pattern, detail = entry, ""
            else:
                pattern = str(entry.get("pattern", ""))
                detail = str(entry.get("detail", ""))
            if not pattern:
                continue
            try:
                out.append((tier, re.compile(pattern, re.IGNORECASE), detail))
            except re.error as exc:
                logger.warning("approval_gate: bad pattern %r skipped: %s", pattern, exc)
    _PATTERN_CACHE[key] = (policy, out)
    return out


def _command_tools(policy: dict[str, Any]) -> set[str]:
    """Tools that are generic executors — a shell, not a verb.

    Only these may be *downgraded* by a content pattern. See :func:`classify`.
    """
    return {str(n).lower() for n in (policy.get("command_tools") or [])}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Classification:
    """The reversibility verdict for one tool call."""

    tool_name: str
    tier: str
    rule: str            # which policy rule decided this
    detail: str          # human-readable "what will happen", from the policy
    requires_approval: bool

    def summary(self) -> str:
        line = f"[{self.tier.upper()}] {self.tool_name} (rule: {self.rule})"
        return f"{line}\n{self.detail}" if self.detail else line


def flatten_input(tool_input: Any) -> str:
    """Flatten a tool input to one searchable string. Never persisted."""
    if isinstance(tool_input, dict):
        try:
            return " ".join(f"{k}={v}" for k, v in tool_input.items())
        except Exception:  # noqa: BLE001
            return str(tool_input)
    return str(tool_input)


def _tool_tiers(policy: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    tools = policy.get("tools") or {}
    for tier in TIERS:
        for name in tools.get(tier) or []:
            mapping[str(name).lower()] = tier
    return mapping


def classify(
    tool_name: str,
    tool_input: Any = None,
    *,
    policy: Optional[dict[str, Any]] = None,
) -> Classification:
    """Classify a tool call by reversibility.

    Order is deliberate, and the asymmetry between escalation and downgrade is
    the whole safety property:

    0. **A tool the policy enumerates ``reversible`` is exempt from escalation**,
       unless it is a generic executor. Its arguments are data it reads, not a
       command it runs, so scanning them for ``git push`` is a category error —
       and one that made ``read_file`` prompt for confirmation on a docstring.
    1. **Irreversible content patterns escalate anything else.** ``run_command``
       is not a tier; the string it is handed is. A shell tool the caller marked
       read-only can still carry ``git push``.
    2. Then the per-tool tier from the policy.
    3. **Downgrade patterns apply only to declared generic executors**
       (``command_tools``). A content pattern may always make a call *worse*,
       but may only make it *better* for a tool that is nothing but a shell.
       Otherwise incidental text decides: ``git_push`` with a ``{"note":
       "mkdir logs"}`` argument matched the ``mkdir`` recoverable pattern and
       was auto-allowed, and so was any unenumerated tool carrying the same
       word. A downgrade rule that fires on a tool it was never written for is
       an allowlist that fails open by accident.
    4. Then ``default_tier`` — ``unknown``, which requires approval.
    """
    policy = policy if policy is not None else load_policy()
    require = set(policy.get("require_approval_tiers") or [IRREVERSIBLE, UNKNOWN])
    blob = f"{tool_name} {flatten_input(tool_input)}"
    patterns = _compiled_patterns(policy)
    lname = (tool_name or "").lower()
    command_tools = _command_tools(policy)

    # 0. A tool the POLICY declares reversible is not escalated by its own
    #    arguments. For a tool that does not execute what it is handed, the
    #    input is data being read, not a command being run — matching an
    #    irreversible pattern against it is a category error. Without this,
    #    read_file("how do I git push safely") halted for human approval, and a
    #    gate that prompts on a read teaches operators to approve reflexively,
    #    which costs more safety than the escalation buys.
    #
    #    This is narrower than trusting a tool schema's own is_read_only flag
    #    (which the parallel ars-appr-01 implementation did, and which this
    #    module rejected as "an assertion by the caller rather than a fact").
    #    The assertion here comes from args/agent_approval_policy.yaml — the
    #    operator's file, already the source of truth for every other tier — not
    #    from developer-authored schema shipped alongside the tool.
    #
    #    A generic executor is excluded, always: for anything in command_tools
    #    the input IS the command, so the exemption would be exactly the
    #    smuggling hole rule 1 exists to close. Listing a shell as `reversible`
    #    is an incoherent claim and is ignored rather than honoured.
    if _tool_tiers(policy).get(lname) == REVERSIBLE and lname not in command_tools:
        return Classification(
            tool_name=tool_name,
            tier=REVERSIBLE,
            rule="reversible_tool",
            detail=(
                f"{tool_name} is enumerated reversible in {POLICY_FILENAME} and is "
                f"not a generic executor, so its arguments are data, not commands"
            ),
            requires_approval=REVERSIBLE in require,
        )

    # 1. Escalation — unconditional, for every tool.
    for tier, rx, detail in patterns:
        if tier == IRREVERSIBLE and rx.search(blob):
            return Classification(
                tool_name=tool_name,
                tier=tier,
                rule=f"pattern:{rx.pattern}",
                detail=detail,
                requires_approval=tier in require,
            )

    # 2. The tool's own enumerated tier.
    tier = _tool_tiers(policy).get((tool_name or "").lower())
    if tier:
        return Classification(
            tool_name=tool_name,
            tier=tier,
            rule="tool_list",
            detail=f"{tool_name} is enumerated as {tier} in {POLICY_FILENAME}",
            requires_approval=tier in require,
        )

    # 3. Downgrade — generic executors only. `run_command` running `git add .`
    #    is recoverable; an unenumerated tool that merely mentions `git add` is
    #    still unknown.
    if (tool_name or "").lower() in _command_tools(policy):
        for tier, rx, detail in patterns:
            if tier != IRREVERSIBLE and rx.search(blob):
                return Classification(
                    tool_name=tool_name,
                    tier=tier,
                    rule=f"pattern:{rx.pattern}",
                    detail=detail,
                    requires_approval=tier in require,
                )

    # 4. Not enumerated, nothing matched.
    default_tier = str(policy.get("default_tier") or UNKNOWN)
    return Classification(
        tool_name=tool_name,
        tier=default_tier,
        rule="default_tier",
        detail=(
            f"{tool_name!r} is not enumerated in {POLICY_FILENAME}. An allowlist "
            "that fails open is decoration — an unrecognised tool needs a human."
        ),
        requires_approval=default_tier in require,
    )


# ---------------------------------------------------------------------------
# Approvers
# ---------------------------------------------------------------------------
@dataclass
class ApprovalRequest:
    """Context handed to an :data:`Approver`."""

    tool_name: str
    tool_input: dict[str, Any]
    classification: Classification
    actor: str = ""

    def summary(self) -> str:
        preview = (
            self.tool_input.get("command")
            or self.tool_input.get("path")
            or self.tool_input.get("file_path")
            or ""
        )
        preview = str(preview)[:200]
        head = self.classification.summary()
        return f"{head}\n  {preview}" if preview else head


@dataclass
class ApprovalDecision:
    """What a human (or a policy) decided, and why."""

    approved: bool
    reason: str = ""
    actor: str = ""


# An approver may return a bool or a full ApprovalDecision.
Approver = Callable[[ApprovalRequest], Union[bool, ApprovalDecision]]


def console_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Prompt on the console. Denies on EOF — a non-interactive run fails closed."""
    prompt = (
        f"\n=== APPROVAL REQUIRED ===\n{request.summary()}\n"
        "Approve this irreversible action? [y/N]: "
    )
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return ApprovalDecision(False, "no interactive approver available", request.actor)
    if answer.strip().lower() not in ("y", "yes"):
        return ApprovalDecision(False, "operator denied", request.actor)
    try:
        reason = input("Reason (recorded, append-only): ").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        reason = ""
    return ApprovalDecision(True, reason or "operator approved", request.actor)


def deny_all_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Fail-closed approver for batch/CI runs."""
    return ApprovalDecision(False, "no approver configured (fail closed)", request.actor)


def _normalize(result: Any, actor: str) -> ApprovalDecision:
    if isinstance(result, ApprovalDecision):
        return ApprovalDecision(
            bool(result.approved), result.reason or "", result.actor or actor
        )
    if isinstance(result, tuple) and len(result) == 2:
        return ApprovalDecision(bool(result[0]), str(result[1]), actor)
    return ApprovalDecision(bool(result), "approver returned bool", actor)


# ---------------------------------------------------------------------------
# Audit — append-only, actor + reason, KEY NAMES ONLY
# ---------------------------------------------------------------------------
def resolve_actor(actor: Optional[str] = None) -> str:
    """Who is deciding. Explicit arg → env → OS user → ``unknown``."""
    for candidate in (
        actor,
        os.environ.get(ACTOR_ENV),
        os.environ.get("ICDEV_USER"),
        os.environ.get("USER"),
        os.environ.get("USERNAME"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "unknown"


def _arg_keys(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        return ",".join(sorted(str(k) for k in tool_input))
    return ""


def _input_digest(tool_input: Any) -> str:
    return hashlib.sha256(flatten_input(tool_input).encode("utf-8")).hexdigest()


def record_decision(
    *,
    tool_name: str,
    tool_input: Any,
    classification: Classification,
    decision: ApprovalDecision,
    mode: str,
    session_id: str = "",
) -> bool:
    """Append one decision to ``agent_approval_log``. Returns True if persisted.

    Falls back to the ``hook_events`` trail (also append-only, present in every
    environment) when the dedicated table is missing — a fresh worktree whose DB
    predates migration 342 must still leave a record.
    """
    row = {
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id or _session_id(),
        "actor": decision.actor or "unknown",
        "tool_name": tool_name,
        "tier": classification.tier,
        "rule": classification.rule,
        "decision": "approved" if decision.approved else "denied",
        "reason": decision.reason or "",
        "mode": mode,
        "arg_keys": _arg_keys(tool_input),
        "input_sha256": _input_digest(tool_input),
        "detail": classification.detail,
    }
    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
        try:
            if table_exists(conn, AUDIT_TABLE):
                cols = list(row)
                placeholders = ", ".join(["%s"] * len(cols))
                conn.execute(
                    f"INSERT INTO {AUDIT_TABLE} ({', '.join(cols)}) "
                    f"VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
                conn.commit()
                return True
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — fall through to hook_events
        logger.warning("approval_gate: %s write failed: %s", AUDIT_TABLE, exc)

    try:
        from tools.airgap.hook_compat import store_event

        store_event(row["session_id"], "agent_approval", tool_name, row)
        return True
    except Exception as exc:  # noqa: BLE001 — auditing must not break the gate
        logger.error("approval_gate: DECISION UNRECORDED for %s: %s", tool_name, exc)
    return False


def _session_id() -> str:
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            return val
    return "unknown"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def resolve_mode(mode: Optional[str] = None) -> str:
    """arg → ``ICDEV_AGENT_APPROVAL_MODE`` → ``enforce``."""
    m = (mode or os.environ.get(MODE_ENV) or MODE_ENFORCE).strip().lower()
    return m if m in MODES else MODE_ENFORCE


@dataclass
class GateEvent:
    """One gate evaluation, for callers that want to inspect what happened."""

    tool_name: str
    tier: str
    requires_approval: bool
    allowed: bool
    reason: str
    actor: str
    recorded: bool
    extra: dict[str, Any] = field(default_factory=dict)


def build_approval_hook(
    *,
    approver: Optional[Approver] = None,
    mode: Optional[str] = None,
    actor: Optional[str] = None,
    policy: Optional[dict[str, Any]] = None,
    session_id: str = "",
    on_event: Optional[Callable[[GateEvent], None]] = None,
    consult_pre_tool_check: bool = True,
) -> Callable[[str, dict[str, Any]], Optional[str]]:
    """Build a ``PreToolUseHook`` for :func:`icdev.tools.llm.agent_loop.run_agent_loop`.

    The returned hook has the loop's blocking contract: return ``None`` to let
    the call run, or a string to halt it (the string is fed back to the model as
    the tool result, so it says *why*).

    Args:
        approver: ``request -> bool | ApprovalDecision``. Defaults to the console
            prompt, which denies on EOF, so a non-interactive run fails closed.
        mode: ``enforce`` (default) | ``dry_run`` | ``off``. ``dry_run`` and
            ``off`` still write the audit row.
        actor: Who is deciding. Defaults to :func:`resolve_actor`.
        consult_pre_tool_check: Defer to the headless
            ``.claude/hooks/pre_tool_use.py`` hard-block list first. A hard block
            is not a question — it is never escalated to the approver.
    """
    resolved_mode = resolve_mode(mode)
    approve = approver or console_approver
    who = resolve_actor(actor)
    pol = policy if policy is not None else load_policy()

    def _emit(event: GateEvent) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception as exc:  # noqa: BLE001 — an observer never blocks
                logger.debug("approval_gate: on_event raised: %s", exc)

    def _finish(
        cls: Classification,
        tool_input: dict[str, Any],
        decision: ApprovalDecision,
    ) -> Optional[str]:
        recorded = record_decision(
            tool_name=cls.tool_name,
            tool_input=tool_input,
            classification=cls,
            decision=decision,
            mode=resolved_mode,
            session_id=session_id,
        )
        _emit(
            GateEvent(
                tool_name=cls.tool_name,
                tier=cls.tier,
                requires_approval=cls.requires_approval,
                allowed=decision.approved,
                reason=decision.reason,
                actor=decision.actor,
                recorded=recorded,
                extra={"rule": cls.rule, "mode": resolved_mode},
            )
        )
        if decision.approved:
            return None
        return (
            f"BLOCKED by the approval gate: {cls.tool_name} is {cls.tier} "
            f"({cls.detail}). {decision.reason}. Do not retry this call — ask the "
            "operator, or choose a reversible alternative."
        )

    def hook(tool_name: str, tool_input: Any) -> Optional[str]:
        if not isinstance(tool_input, dict):
            tool_input = {} if tool_input is None else {"value": tool_input}
        cls = classify(tool_name, tool_input, policy=pol)

        # 1. Hard blocks are not approval questions.
        if consult_pre_tool_check:
            blocked, why = _hard_block(tool_name, tool_input)
            if blocked:
                return _finish(
                    cls, tool_input, ApprovalDecision(False, f"hard block: {why}", who)
                )

        # 2. Reversible / recoverable: allowed without ceremony and without a
        #    row — the audit trail is for decisions, not for every read.
        if not cls.requires_approval:
            _emit(
                GateEvent(
                    tool_name=tool_name,
                    tier=cls.tier,
                    requires_approval=False,
                    allowed=True,
                    reason="auto-allowed",
                    actor=who,
                    recorded=False,
                    extra={"rule": cls.rule, "mode": resolved_mode},
                )
            )
            return None

        # 3. Irreversible or unknown.
        if resolved_mode == MODE_OFF:
            return _finish(
                cls, tool_input,
                ApprovalDecision(True, "approval gate mode=off (escape hatch)", who),
            )
        if resolved_mode == MODE_DRY_RUN:
            return _finish(
                cls, tool_input,
                ApprovalDecision(True, "dry_run: would have required approval", who),
            )

        request = ApprovalRequest(
            tool_name=tool_name, tool_input=tool_input, classification=cls, actor=who
        )
        try:
            decision = _normalize(approve(request), who)
        except Exception as exc:  # noqa: BLE001 — a broken approver denies
            logger.warning("approval_gate: approver raised, denying: %s", exc)
            decision = ApprovalDecision(False, f"approver error: {exc}", who)
        return _finish(cls, tool_input, decision)

    return hook


def _hard_block(tool_name: str, tool_input: dict[str, Any]) -> tuple[bool, str]:
    """Ask the headless pre_tool_use hook. Unavailable → not a hard block."""
    try:
        from tools.airgap.hook_compat import run_pre_tool_check

        if tool_name in ("run_command", "bash", "Bash"):
            hook_tool, hook_input = "Bash", {"command": str(tool_input.get("command", ""))}
        elif tool_name in ("write_file", "Write"):
            hook_tool, hook_input = "Write", {
                "content": str(tool_input.get("content", "")),
                "path": str(tool_input.get("path") or tool_input.get("file_path", "")),
            }
        else:
            hook_tool, hook_input = "Write", {"content": flatten_input(tool_input)}
        result = run_pre_tool_check(hook_tool, hook_input)
    except Exception as exc:  # noqa: BLE001 — the gate below still asks a human
        logger.debug("approval_gate: pre_tool_check unavailable: %s", exc)
        return False, ""
    if not result.get("allowed", True):
        return True, str(result.get("reason", "blocked by safety policy"))
    return False, ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Classify an agent tool call by reversibility (ars-appr-01)."
    )
    parser.add_argument("--classify", metavar="TOOL", help="tool name to classify")
    parser.add_argument("--input", default="{}", help="tool input as JSON")
    parser.add_argument("--list-policy", action="store_true", help="show loaded policy")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.list_policy:
        policy = load_policy(refresh=True)
        payload = {
            "policy_path": str(_find_policy_path() or ""),
            "default_tier": policy.get("default_tier"),
            "require_approval_tiers": policy.get("require_approval_tiers"),
            "tool_counts": {
                tier: len((policy.get("tools") or {}).get(tier) or []) for tier in TIERS
            },
            "pattern_count": len(_compiled_patterns(policy)),
        }
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0

    if not args.classify:
        parser.print_help()
        return 2

    try:
        tool_input = json.loads(args.input)
    except json.JSONDecodeError as exc:
        print(f"--input is not valid JSON: {exc}")
        return 2
    cls = classify(args.classify, tool_input)
    if args.json:
        print(json.dumps(asdict(cls), indent=2))
    else:
        print(cls.summary())
        print(f"requires_approval: {cls.requires_approval}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
