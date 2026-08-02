# CUI // SP-CTI
"""Reversibility classification + approval gate for the agent loop (ars-appr-01).

``icdev/tools/llm/agent_loop.py`` had no approval step: every tool call the model
emitted executed, and the only thing standing between a loop and an irreversible
act was whichever caller happened to pass an ``on_pre_tool_use`` hook. ICDEV has
real irreversible surfaces — ``git push`` and force-push, PR merge, branch and
worktree deletion, writes against append-only tables, marking a kanban task
``done``, and external posts — and CLAUDE.md already names one absolute
prohibition (never delete YouTube videos) precisely because irreversibility is
the risk.

This module classifies a tool call by **reversibility** and requires a human
decision for anything that is not provably reversible.

Design
------
**Default deny on unknown.** An allowlist that fails open is decoration: the
whole point is the call nobody enumerated. A tool that matches no rule is
classified :data:`UNKNOWN` and requires approval, exactly like an explicitly
irreversible one. Only a positive signal — a first-party ``is_read_only`` schema
flag, an explicit ``reversibility`` declaration, or a config allowlist entry —
yields :data:`REVERSIBLE`.

**Content beats the tool name.** ``run_command``/``run_tool`` can carry anything,
so the irreversible *pattern* rules are evaluated against the flattened tool
input **before** the reversible allowlist. ``run_command`` may be allowlisted and
still halt when its command is ``git push --force``. Otherwise an allowlisted
shell tool would launder every irreversible act through itself.

**Read-only is a first-party assertion.** ``is_read_only: true`` lives in the
tool *schema*, which is written in code by the caller — the model cannot set it.
A read-only tool cannot perform an irreversible act, so it short-circuits ahead
of the content patterns (otherwise ``grep_files("git push")`` would prompt).

Precedent reused rather than reinvented
---------------------------------------
- :mod:`tools.agent_runtime.safety` — the SAG command-approval layer: injectable
  :data:`Approver`, ``manual``/``off`` modes, audit on both outcomes. This module
  keeps that shape (and its ``run_pre_tool_check`` hard-block composition) and
  adds the reversibility taxonomy plus a dedicated append-only trail.
- ``tools/cortex/blueprint.py`` ``_agent_proposal`` / ``_launch_confirmed_agent``
  — the confirm-then-launch affordance: describe what WOULD happen and ask, and
  only act on an explicit confirm. A denial here returns the same kind of
  human-readable proposal text back to the model as the tool result.

Every decision — approved and denied alike — is recorded to the append-only
``agent_approval_log`` table with the actor and the reason.

Usage::

    from icdev.tools.llm.approval_gate import build_approval_hook

    hook = build_approval_hook(tools=tools, session_id=sid, actor="alice")
    run_agent_loop(router, ..., on_pre_tool_use=hook)

:func:`run_agent_loop` builds this hook itself by default — see its
``approval_gate`` / ``approver`` / ``approval_mode`` parameters.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.approval_gate")


# ---------------------------------------------------------------------------
# Reversibility taxonomy
# ---------------------------------------------------------------------------

#: The call can be undone (read-only, or an allowlisted local mutation covered by
#: git / checkpoints). Executes without a human in the loop.
REVERSIBLE = "reversible"

#: The call cannot be undone once it lands — a push, a merge, a deletion, an
#: external post, a row in an append-only table. Always requires approval.
IRREVERSIBLE = "irreversible"

#: No rule matched. Treated exactly like :data:`IRREVERSIBLE` for gating purposes;
#: recorded distinctly so the config gap is visible in the trail.
UNKNOWN = "unknown"

#: Approval modes.
MODE_MANUAL = "manual"
"""Seek a decision from the approver (default)."""
MODE_DENY = "deny"
"""Never approve — fail-closed. For unattended batch runs."""
MODE_OFF = "off"
"""Auto-approve, still recorded. The explicit, audited escape hatch."""

_VALID_MODES = (MODE_MANUAL, MODE_DENY, MODE_OFF)

# Env keys.
_MODE_ENV = "ICDEV_AGENT_APPROVAL_MODE"
_ENABLED_ENV = "ICDEV_AGENT_APPROVAL_GATE"
_ACTOR_ENV = "ICDEV_APPROVAL_ACTOR"

_CONFIG_FILENAME = "approval_gate.yaml"

#: Fallback rules used when ``args/approval_gate.yaml`` cannot be read. Deliberately
#: *narrower* than the shipped config: a missing config file must not widen the
#: allowlist. Only the loop-terminating ``done`` tool is auto-reversible here;
#: everything else falls through to UNKNOWN → approval.
_FALLBACK_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": MODE_MANUAL,
    "reversible": {"tools": ["done"]},
    "irreversible": {
        "tools": [],
        "patterns": [
            {
                "id": "fallback-git-push",
                "regex": r"\bgit\s+(?:.*\s)?push\b",
                "reason": "git push publishes commits to a remote — it cannot be un-published",
            },
            {
                "id": "fallback-destructive",
                "regex": r"\b(?:rm\s+-[a-z]*[rf]|drop\s+table|truncate\s+table|delete\s+from)\b",
                "reason": "destructive filesystem or SQL operation",
            },
        ],
    },
}


@dataclass(frozen=True)
class Classification:
    """The reversibility verdict for one tool call.

    Attributes:
        reversibility: :data:`REVERSIBLE`, :data:`IRREVERSIBLE` or :data:`UNKNOWN`.
        rule_id: Which rule decided it — e.g. ``read_only_schema``,
            ``irreversible_pattern:git-push``, ``no_rule_matched``. Recorded so a
            surprising verdict is traceable to a line of config.
        reason: Human-readable justification, shown to the approver and to the
            model when the call is denied.
    """

    reversibility: str
    rule_id: str
    reason: str

    @property
    def requires_approval(self) -> bool:
        """True unless the call is provably reversible — UNKNOWN requires approval."""
        return self.reversibility != REVERSIBLE


@dataclass
class ApprovalRequest:
    """Context handed to an :data:`Approver`."""

    tool_name: str
    tool_input: dict[str, Any]
    classification: Classification
    session_id: str = ""
    trace_id: str = ""
    #: Best-known actor before a decision is made (env / caller supplied). The
    #: approver may override it in its :class:`ApprovalDecision`.
    actor: str = ""

    def summary(self) -> str:
        """Confirm-card text: what WOULD run, and why it is being questioned."""
        return (
            f"[{self.classification.reversibility.upper()}] {self.tool_name}\n"
            f"  input: {preview_input(self.tool_input)}\n"
            f"  why:   {self.classification.reason}\n"
            f"  rule:  {self.classification.rule_id}"
        )


@dataclass
class ApprovalDecision:
    """An approver's answer. Both fields are required by the audit trail.

    Attributes:
        approved: Whether the call may execute.
        actor: WHO decided — a username, ``system:<mode>``, or an adapter id.
            Never empty in a recorded row; the recorder substitutes
            ``system:unattributed`` if an approver leaves it blank.
        reason: WHY. Free text from the operator, or the rule that auto-decided.
    """

    approved: bool
    actor: str = ""
    reason: str = ""


#: ``approver(request) -> ApprovalDecision``. A bare ``bool`` return is accepted
#: for compatibility with :mod:`tools.agent_runtime.safety` approvers.
Approver = Callable[[ApprovalRequest], "ApprovalDecision | bool"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    """Resolve the repo root from ``__file__`` — never from ``os.getcwd()``.

    CLAUDE.md: cwd points at the worktree root under ``git worktree`` and at a
    subdirectory under some test harnesses, so a cwd-relative config lookup reads
    the wrong file or none at all.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "args" / _CONFIG_FILENAME).exists():
            return parent
    return None


_CONFIG_CACHE: dict[str, Any] | None = None


def load_config(*, refresh: bool = False) -> dict[str, Any]:
    """Load ``args/approval_gate.yaml``, falling back to :data:`_FALLBACK_CONFIG`.

    A missing or malformed config file **narrows** the allowlist rather than
    disabling the gate — the fallback is fail-closed by construction.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not refresh:
        return _CONFIG_CACHE

    config = dict(_FALLBACK_CONFIG)
    root = _repo_root()
    if root is not None:
        # icdev/tools/llm/... resolves to the icdev/ package dir when installed as a
        # wheel; check the repo root and its parent so both layouts find args/.
        candidates = [root / "args" / _CONFIG_FILENAME, root.parent / "args" / _CONFIG_FILENAME]
        for cfg_path in candidates:
            if not cfg_path.exists():
                continue
            try:
                import yaml

                with open(cfg_path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                if isinstance(raw, dict):
                    config = raw
                break
            except Exception as exc:  # noqa: BLE001 — a broken config must not open the gate
                logger.warning(
                    "approval_gate: could not read %s (%s) — using fail-closed fallback rules",
                    cfg_path,
                    exc,
                )
                break

    _CONFIG_CACHE = config
    return config


def resolve_mode(mode: str | None = None, config: dict[str, Any] | None = None) -> str:
    """Resolve the approval mode: explicit arg → env → config → ``manual``."""
    config = config if config is not None else load_config()
    raw = mode or os.environ.get(_MODE_ENV) or config.get("mode") or MODE_MANUAL
    resolved = str(raw).strip().lower()
    if resolved not in _VALID_MODES:
        logger.warning("approval_gate: unknown mode %r — falling back to %s", raw, MODE_MANUAL)
        return MODE_MANUAL
    return resolved


def is_enabled(enabled: bool | None = None, config: dict[str, Any] | None = None) -> bool:
    """Resolve whether the gate runs at all: explicit arg → env → config → True."""
    if enabled is not None:
        return bool(enabled)
    env = os.environ.get(_ENABLED_ENV)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    config = config if config is not None else load_config()
    return bool(config.get("enabled", True))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def flatten_input(tool_input: Any) -> str:
    """Flatten a tool input to one scannable string for the pattern rules."""
    if isinstance(tool_input, str):
        return tool_input
    try:
        return json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return str(tool_input)


def preview_input(tool_input: Any, limit: int = 300) -> str:
    """Short, single-line preview of a tool input for prompts and audit rows."""
    text = " ".join(flatten_input(tool_input).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _schema_index(tools: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Map ``tool_name -> merged schema dict`` from an OpenAI function-schema list.

    Handles both the wrapped (``{"type":"function","function":{...}}``) and flat
    shapes, so ``is_read_only``/``reversibility`` are found wherever declared.
    """
    index: dict[str, dict[str, Any]] = {}
    for entry in tools or []:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        fn = fn if isinstance(fn, dict) else {}
        name = fn.get("name") or entry.get("name")
        if not name:
            continue
        merged = {k: v for k, v in entry.items() if k != "function"}
        merged.update(fn)
        index[str(name)] = merged
    return index


def _name_matches(name: str, patterns: Any) -> str | None:
    """Return the matching entry if *name* matches any exact name or glob."""
    if not isinstance(patterns, (list, tuple)):
        return None
    for candidate in patterns:
        candidate = str(candidate)
        if name == candidate or fnmatch(name, candidate):
            return candidate
    return None


def _compiled_patterns(config: dict[str, Any]) -> list[tuple[str, re.Pattern[str], str]]:
    """Compile the irreversible content patterns as ``(id, regex, reason)``."""
    compiled: list[tuple[str, re.Pattern[str], str]] = []
    for rule in config.get("irreversible", {}).get("patterns", []) or []:
        if not isinstance(rule, dict):
            continue
        regex = rule.get("regex")
        if not regex:
            continue
        try:
            compiled.append(
                (
                    str(rule.get("id") or regex),
                    re.compile(str(regex), re.IGNORECASE),
                    str(rule.get("reason") or "matches an irreversible-operation pattern"),
                )
            )
        except re.error as exc:
            # A pattern that will not compile is a hole in the gate, not a nuisance.
            logger.warning(
                "approval_gate: irreversible pattern %r failed to compile: %s",
                rule.get("id") or regex,
                exc,
            )
    return compiled


def classify(
    tool_name: str,
    tool_input: Any,
    *,
    tool_schema: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> Classification:
    """Classify one tool call by reversibility.

    Rule order — first match wins, and the order is the security property:

    1. An explicit ``reversibility`` declaration in the tool schema or in
       ``irreversible.declared`` / ``reversible.declared`` config.
    2. Tool-name match against ``irreversible.tools`` (exact or glob).
    3. ``is_read_only: true`` in the schema → :data:`REVERSIBLE`. First-party, and
       a read-only tool cannot act irreversibly, so it short-circuits the content
       patterns (``grep_files("git push")`` must not prompt).
    4. ``irreversible.patterns`` against the flattened input — **before** the
       reversible allowlist, so an allowlisted shell tool cannot launder
       ``git push --force`` through itself.
    5. Tool-name match against ``reversible.tools`` → :data:`REVERSIBLE`.
    6. Nothing matched → :data:`UNKNOWN`, which requires approval.

    Args:
        tool_name: The tool the model asked for.
        tool_input: Its arguments (any JSON-able value).
        tool_schema: That tool's schema entry, when available.
        config: Pre-loaded config; defaults to :func:`load_config`.
    """
    config = config if config is not None else load_config()
    name = str(tool_name or "")
    schema = tool_schema or {}

    # 1. Explicit declaration wins over everything.
    declared = schema.get("reversibility")
    if isinstance(declared, str) and declared.strip().lower() in (REVERSIBLE, IRREVERSIBLE):
        value = declared.strip().lower()
        return Classification(
            reversibility=value,
            rule_id="schema_declared_reversibility",
            reason=f"tool schema declares reversibility={value!r}",
        )

    # 2. Explicitly irreversible tool names.
    match = _name_matches(name, config.get("irreversible", {}).get("tools"))
    if match:
        return Classification(
            reversibility=IRREVERSIBLE,
            rule_id=f"irreversible_tool:{match}",
            reason=f"{name!r} is listed as an irreversible tool in args/{_CONFIG_FILENAME}",
        )

    # 3. First-party read-only assertion — cannot mutate, so cannot be irreversible.
    if schema.get("is_read_only") is True:
        return Classification(
            reversibility=REVERSIBLE,
            rule_id="read_only_schema",
            reason=f"{name!r} declares is_read_only=true in its tool schema",
        )

    # 4. Content patterns — evaluated before the allowlist on purpose.
    blob = flatten_input(tool_input)
    for rule_id, regex, reason in _compiled_patterns(config):
        if regex.search(blob):
            return Classification(
                reversibility=IRREVERSIBLE,
                rule_id=f"irreversible_pattern:{rule_id}",
                reason=reason,
            )

    # 5. Allowlisted reversible tool names.
    match = _name_matches(name, config.get("reversible", {}).get("tools"))
    if match:
        return Classification(
            reversibility=REVERSIBLE,
            rule_id=f"reversible_tool:{match}",
            reason=f"{name!r} is allowlisted as reversible in args/{_CONFIG_FILENAME}",
        )

    # 6. Default deny. An allowlist that fails open is decoration.
    return Classification(
        reversibility=UNKNOWN,
        rule_id="no_rule_matched",
        reason=(
            f"{name!r} matches no reversibility rule. Unknown tools require approval "
            f"by default — add it to args/{_CONFIG_FILENAME} or set is_read_only in "
            f"its schema once its blast radius is known."
        ),
    )


# ---------------------------------------------------------------------------
# Approvers
# ---------------------------------------------------------------------------


def _default_actor() -> str:
    """Best-effort operator identity for the audit trail."""
    return (
        os.environ.get(_ACTOR_ENV)
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "unknown"
    )


def console_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Prompt the operator on the console; denies on EOF / non-interactive stdin."""
    prompt = (
        f"\n=== APPROVAL REQUIRED — irreversible or unclassified action ===\n"
        f"{request.summary()}\n"
        f"Approve? [y/N]: "
    )
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return ApprovalDecision(
            approved=False,
            actor="system:console_unavailable",
            reason="no interactive console available to confirm — denied fail-closed",
        )
    if answer.strip().lower() not in ("y", "yes"):
        return ApprovalDecision(
            approved=False, actor=_default_actor(), reason="operator declined at console"
        )
    try:
        why = input("Reason for approval (recorded): ").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        why = ""
    return ApprovalDecision(
        approved=True,
        actor=_default_actor(),
        reason=why or "operator approved at console (no reason given)",
    )


def deny_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Fail-closed approver — the default when there is no interactive console."""
    return ApprovalDecision(
        approved=False,
        actor="system:no_approver",
        reason=(
            "no human approver is attached to this agent loop, and "
            f"{request.tool_name!r} is {request.classification.reversibility}"
        ),
    )


def default_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Console when stdin is a TTY, otherwise fail-closed.

    An unattended run (kanban runner, cron, CI) has no one to ask, so it denies
    rather than blocking forever on ``input()``.
    """
    try:
        interactive = bool(sys.stdin) and sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        interactive = False
    return console_approver(request) if interactive else deny_approver(request)


# ---------------------------------------------------------------------------
# Append-only recording
# ---------------------------------------------------------------------------

APPROVAL_LOG_TABLE = "agent_approval_log"

_INSERT_SQL = f"""
INSERT INTO {APPROVAL_LOG_TABLE} (
    id, created_at, session_id, trace_id, tool_name, tool_input_preview,
    reversibility, rule_id, decision, actor, reason, approval_mode, classification,
    metadata
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def record_decision(
    *,
    tool_name: str,
    tool_input: Any,
    classification: Classification,
    decision: ApprovalDecision,
    mode: str,
    session_id: str = "",
    trace_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Append one approval/denial row. Returns the row id, or None if it did not land.

    Failures are logged at WARNING with the exception — never swallowed. CLAUDE.md:
    a swallowed INSERT is how a feature reports success while persisting nothing.
    The gate's *decision* is still honoured when recording fails; the caller sees a
    log line rather than a silent hole in the trail.
    """
    row_id = str(_uuid.uuid4())
    actor = (decision.actor or "").strip() or "system:unattributed"
    reason = (decision.reason or "").strip() or "no reason recorded"
    payload = json.dumps(metadata or {}, default=str)
    try:
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            conn.execute(
                _INSERT_SQL,
                (
                    row_id,
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                    trace_id,
                    str(tool_name),
                    preview_input(tool_input),
                    classification.reversibility,
                    classification.rule_id,
                    "approved" if decision.approved else "denied",
                    actor,
                    reason,
                    mode,
                    "CUI",
                    payload,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return row_id
    except Exception as exc:  # noqa: BLE001 — recording must not break the gate,
        # but it must not be invisible either.
        logger.warning(
            "approval_gate: could not record %s decision for %r (actor=%s): %s",
            "approved" if decision.approved else "denied",
            tool_name,
            actor,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Hard block — compose the existing headless pre-tool-use check
# ---------------------------------------------------------------------------


def _hard_block_reason(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Ask the headless ``pre_tool_use`` replica whether this is outright forbidden.

    ``tools.airgap.hook_compat.run_pre_tool_check`` already implements the
    destructive-git blocklist and append-only-table protection. A refusal there is
    not approvable — it is denied outright, as in
    :mod:`tools.agent_runtime.safety`.
    """
    try:
        from icdev.tools.airgap.hook_compat import run_pre_tool_check

        check = run_pre_tool_check("Write", {"content": flatten_input(tool_input)})
    except Exception as exc:  # noqa: BLE001 — a broken checker must not open the gate;
        # the call still falls through to the approval path below.
        logger.debug("approval_gate: pre_tool_check unavailable: %s", exc)
        return None
    if not check.get("allowed", True):
        return str(check.get("reason") or "blocked by platform safety policy")
    return None


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


@dataclass
class GateOutcome:
    """Full result of one gate evaluation (used by tests and by the hook)."""

    allowed: bool
    classification: Classification
    decision: ApprovalDecision
    mode: str
    audit_id: str | None = None
    #: Message returned to the model as the tool result when the call is blocked.
    block_message: str = ""


def _block_message(request: ApprovalRequest, decision: ApprovalDecision) -> str:
    """Confirm-card text handed back to the model in place of the tool result.

    Mirrors ``tools/cortex/blueprint.py::_agent_proposal`` — say what WOULD have
    happened and that a human must confirm, so the model can propose it to the
    operator rather than retrying blindly.
    """
    return (
        f"[APPROVAL REQUIRED — NOT EXECUTED] {request.tool_name} was not run.\n"
        f"Classification: {request.classification.reversibility} "
        f"({request.classification.rule_id})\n"
        f"Reason: {request.classification.reason}\n"
        f"Decision: denied by {decision.actor} — {decision.reason}\n"
        "This action is irreversible or unclassified, so it needs explicit human "
        "confirmation. Do not retry it. Describe what you intended to do and why, "
        "and let the operator run it or approve it."
    )


def evaluate(
    tool_name: str,
    tool_input: Any,
    *,
    tool_schema: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    approver: Approver | None = None,
    mode: str | None = None,
    session_id: str = "",
    trace_id: str = "",
    actor: str = "",
    record: bool = True,
) -> GateOutcome:
    """Classify, seek approval if needed, record the decision, and return the outcome.

    A :data:`REVERSIBLE` call short-circuits: it is allowed without a prompt and
    without an audit row (the trail records *decisions*, and there is none to make
    — the loop's existing ``tool_call_log`` already covers ordinary execution).
    Every call that reaches the approval path produces exactly one row, approved
    or denied.
    """
    config = config if config is not None else load_config()
    resolved_mode = resolve_mode(mode, config)
    if not isinstance(tool_input, dict):
        tool_input = {"value": tool_input} if tool_input is not None else {}

    classification = classify(
        tool_name, tool_input, tool_schema=tool_schema, config=config
    )

    if not classification.requires_approval:
        return GateOutcome(
            allowed=True,
            classification=classification,
            decision=ApprovalDecision(True, "system:classifier", classification.reason),
            mode=resolved_mode,
        )

    request = ApprovalRequest(
        tool_name=str(tool_name),
        tool_input=tool_input,
        classification=classification,
        session_id=session_id,
        trace_id=trace_id,
        actor=actor or _default_actor(),
    )

    # Outright-forbidden actions are denied, not offered for approval.
    hard_block = _hard_block_reason(str(tool_name), tool_input)
    if hard_block:
        decision = ApprovalDecision(
            approved=False,
            actor="system:pre_tool_use_hook",
            reason=f"blocked by platform safety policy: {hard_block}",
        )
    elif resolved_mode == MODE_OFF:
        decision = ApprovalDecision(
            approved=True,
            actor=actor or _default_actor(),
            reason=(
                f"approval gate mode=off — auto-approved without human review "
                f"({classification.rule_id})"
            ),
        )
        logger.warning(
            "approval_gate: mode=off auto-approved %s (%s)",
            tool_name,
            classification.reversibility,
        )
    elif resolved_mode == MODE_DENY:
        decision = ApprovalDecision(
            approved=False,
            actor="system:deny_mode",
            reason="approval gate mode=deny — unattended run, nothing approvable",
        )
    else:
        approve = approver or default_approver
        try:
            raw = approve(request)
        except Exception as exc:  # noqa: BLE001 — a broken approver denies
            logger.warning("approval_gate: approver raised, denying: %s", exc)
            raw = ApprovalDecision(
                approved=False,
                actor="system:approver_error",
                reason=f"approver raised {type(exc).__name__}: {exc}",
            )
        if isinstance(raw, ApprovalDecision):
            decision = raw
        else:  # tools.agent_runtime.safety-style bool approver
            decision = ApprovalDecision(
                approved=bool(raw),
                actor=request.actor,
                reason="approver returned %s" % ("approve" if raw else "deny"),
            )

    audit_id = None
    if record:
        audit_id = record_decision(
            tool_name=str(tool_name),
            tool_input=tool_input,
            classification=classification,
            decision=decision,
            mode=resolved_mode,
            session_id=session_id,
            trace_id=trace_id,
            metadata={"hard_block": bool(hard_block)},
        )

    return GateOutcome(
        allowed=decision.approved,
        classification=classification,
        decision=decision,
        mode=resolved_mode,
        audit_id=audit_id,
        block_message="" if decision.approved else _block_message(request, decision),
    )


def build_approval_hook(
    *,
    tools: list[dict[str, Any]] | None = None,
    approver: Approver | None = None,
    mode: str | None = None,
    session_id: str = "",
    trace_id: str = "",
    actor: str = "",
    config: dict[str, Any] | None = None,
    chain: Callable[[str, dict[str, Any]], "str | None"] | None = None,
) -> Callable[[str, dict[str, Any]], "str | None"]:
    """Build an ``on_pre_tool_use`` hook for :func:`run_agent_loop`.

    The returned hook has the loop's blocking contract: return a non-empty string
    to block the call (the string becomes the error tool_result), or ``None`` to
    allow it.

    Args:
        tools: The loop's tool-schema list, used to read ``is_read_only`` /
            ``reversibility`` declarations.
        approver: Who decides. Defaults to :func:`default_approver` (console on a
            TTY, fail-closed otherwise).
        mode: ``manual`` | ``deny`` | ``off``. Defaults to env then config.
        session_id / trace_id: Correlation ids written to the audit row.
        actor: Operator identity to attribute a decision to when the approver
            does not supply one.
        chain: A caller-supplied ``on_pre_tool_use`` to compose with. It runs
            **first**; if it blocks, this gate does not run (the call is already
            refused) and its message is returned unchanged.
    """
    config = config if config is not None else load_config()
    schemas = _schema_index(tools)

    def hook(tool_name: str, tool_input: dict[str, Any]) -> "str | None":
        if chain is not None:
            try:
                chained = chain(tool_name, tool_input)
            except Exception as exc:  # noqa: BLE001 — a broken caller hook blocks
                logger.warning("approval_gate: chained on_pre_tool_use raised: %s", exc)
                return f"pre-tool-use hook failed: {type(exc).__name__}: {exc}"
            if chained:
                return chained

        outcome = evaluate(
            tool_name,
            tool_input,
            tool_schema=schemas.get(str(tool_name)),
            config=config,
            approver=approver,
            mode=mode,
            session_id=session_id,
            trace_id=trace_id,
            actor=actor,
        )
        return None if outcome.allowed else outcome.block_message

    return hook


__all__ = [
    "APPROVAL_LOG_TABLE",
    "IRREVERSIBLE",
    "MODE_DENY",
    "MODE_MANUAL",
    "MODE_OFF",
    "REVERSIBLE",
    "UNKNOWN",
    "ApprovalDecision",
    "ApprovalRequest",
    "Approver",
    "Classification",
    "GateOutcome",
    "build_approval_hook",
    "classify",
    "console_approver",
    "default_approver",
    "deny_approver",
    "evaluate",
    "flatten_input",
    "is_enabled",
    "load_config",
    "preview_input",
    "record_decision",
    "resolve_mode",
]
