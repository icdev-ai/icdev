# CUI // SP-CTI
"""Command-approval safety layer for the SAG runtime (sag-safe-01).

sag-reg-02 left a load-bearing seam: every *mutating* tool is routed through a
:data:`~tools.agent_runtime.dispatch.SafetyGate` before it executes. This module
supplies the real gate.

The gate composes the platform's existing safety logic — it does **not** reinvent
it. :func:`tools.airgap.hook_compat.run_pre_tool_check` already replicates
``.claude/hooks/pre_tool_use.py`` headlessly (destructive-git blocklist +
append-only-table protection), so the gate:

1. **Hard-blocks** anything ``run_pre_tool_check`` refuses — the call is denied
   with the hook's reason, no prompt.
2. For a risky-but-allowed mutation, seeks **approval** according to the mode:
   - ``manual`` — always prompt the operator (default).
   - ``smart``  — a cheap-tier LLM risk judgment; auto-approve *low* risk, prompt
     otherwise. Falls back to a keyword heuristic if the LLM is unavailable.
   - ``off``    — the ``--yolo`` escape hatch: auto-approve, but still audited.
3. **Audits** every decision (approved/denied) to the append-only ``hook_events``
   trail via ``store_event`` — no new table, so ``APPEND_ONLY_TABLES`` and the
   conftest schema are untouched.

The approver is injectable (:data:`Approver`): the console prompt is the default,
and gateway agent-mode (sag-gw-01) passes an approver that asks for confirmation
through its messaging adapter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.safety")

MODE_MANUAL = "manual"
MODE_SMART = "smart"
MODE_OFF = "off"
_VALID_MODES = (MODE_MANUAL, MODE_SMART, MODE_OFF)

# Env keys.
_MODE_ENV = "ICDEV_SAG_APPROVAL_MODE"
_RISK_FN_ENV = "ICDEV_SAG_RISK_FUNCTION"

# Keyword heuristic for risk when no LLM is available (fail toward "high").
_HIGH_RISK_KEYWORDS = (
    "rm ", "rmdir", "del ", "format", "mkfs", "dd ", "shutdown", "reboot",
    "drop table", "drop database", "truncate", "delete from", "git push",
    "git reset", "git clean", "chmod", "chown", "curl", "wget", "pip install",
    "> /", "sudo", "kill ", "taskkill",
)


@dataclass
class ApprovalRequest:
    """Context handed to an :data:`Approver` for a risky-but-allowed mutation."""

    tool_name: str
    tool_input: dict[str, Any]
    risk: str  # "low" | "medium" | "high"
    detail: str  # human-readable summary of what will happen

    def summary(self) -> str:
        preview = self.tool_input.get("command") or self.tool_input.get("path") or ""
        preview = str(preview)[:200]
        return (
            f"[{self.risk.upper()} RISK] {self.tool_name}: {preview}"
            + (f"\n{self.detail}" if self.detail else "")
        )


# approver(request) -> True to allow, False to deny.
Approver = Callable[[ApprovalRequest], bool]


def console_approver(request: ApprovalRequest) -> bool:
    """Default approver: prompt on the console. Denies on EOF/non-interactive."""
    prompt = f"\n{request.summary()}\nApprove this action? [y/N]: "
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def deny_all_approver(_request: ApprovalRequest) -> bool:
    """Non-interactive fail-closed approver (useful for tests / batch runs)."""
    return False


# ---------------------------------------------------------------------------
# Tool → hook mapping
# ---------------------------------------------------------------------------
def _map_to_hook(tool_name: str, tool_input: dict[str, Any]) -> "tuple[str, dict]":
    """Map a SAG tool call to a (hook_tool, hook_input) run_pre_tool_check accepts."""
    if tool_name == "run_command":
        return "Bash", {"command": str(tool_input.get("command", ""))}
    if tool_name == "write_file":
        return "Write", {
            "content": str(tool_input.get("content", "")),
            "path": str(tool_input.get("path", "")),
        }
    # Unknown mutating tool: present its serialised input as content so the
    # append-only-SQL / git-danger scanners still get a look.
    return "Write", {"content": _flatten(tool_input)}


def _flatten(d: dict[str, Any]) -> str:
    try:
        return " ".join(f"{k}={v}" for k, v in d.items())
    except Exception:  # noqa: BLE001
        return str(d)


# ---------------------------------------------------------------------------
# Risk judgment (smart mode)
# ---------------------------------------------------------------------------
def _heuristic_risk(tool_name: str, tool_input: dict[str, Any]) -> str:
    blob = f"{tool_name} {_flatten(tool_input)}".lower()
    if any(kw in blob for kw in _HIGH_RISK_KEYWORDS):
        return "high"

    # ars-appr-01: this used to end at `return "low"`, so in `smart` mode any
    # tool that was not run_command/write_file and carried none of the keywords
    # above was auto-approved — including every tool nobody had enumerated. That
    # is an allowlist that fails open. Defer to the reversibility classifier,
    # whose default tier for an unrecognised tool is `unknown` (needs approval).
    try:
        from tools.agent_runtime.approval_gate import IRREVERSIBLE, RECOVERABLE, classify

        cls = classify(tool_name, tool_input)
        if cls.tier == IRREVERSIBLE or cls.requires_approval:
            return "high"
        if cls.tier == RECOVERABLE:
            return "medium"
        return "low"
    except Exception as exc:  # noqa: BLE001 — classifier unavailable: fail toward high
        logger.debug("safety: reversibility classifier unavailable: %s", exc)

    # write_file within the repo is medium; anything unrecognised is high.
    if tool_name == "write_file":
        return "medium"
    return "high"


def _llm_risk(
    router: Any, tool_name: str, tool_input: dict[str, Any]
) -> Optional[str]:
    """Ask a cheap-tier LLM to classify risk. None on any failure."""
    try:
        from tools.llm.provider import LLMRequest

        function = resolve_risk_function()
        req = LLMRequest(
            system_prompt=(
                "You are a security gate. Classify the RISK of executing the "
                "described agent tool call as exactly one word: low, medium, or "
                "high. High = destructive/irreversible/exfiltration; medium = "
                "writes/modifies local state; low = reversible/no side effects. "
                "Reply with only the single word."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Tool: {tool_name}\nInput: {_flatten(tool_input)}",
                }
            ],
            max_tokens=8,
            effort="low",
            skip_injection_scan=True,
        )
        resp = router.invoke(function, req)
        word = (getattr(resp, "content", "") or "").strip().lower()
        for level in ("high", "medium", "low"):
            if level in word:
                return level
        return None
    except Exception as exc:  # noqa: BLE001 — degrade to heuristic
        logger.debug("safety: LLM risk judgment failed: %s", exc)
        return None


def assess_risk(
    tool_name: str, tool_input: dict[str, Any], *, router: Any = None
) -> str:
    """Return 'low' | 'medium' | 'high' — LLM if a router is given, else heuristic."""
    if router is not None:
        level = _llm_risk(router, tool_name, tool_input)
        if level is not None:
            return level
    return _heuristic_risk(tool_name, tool_input)


# ---------------------------------------------------------------------------
# Audit (reuses the append-only hook_events trail — no new table)
# ---------------------------------------------------------------------------
def _audit(
    decision: str,
    tool_name: str,
    tool_input: dict[str, Any],
    reason: str,
    mode: str,
    risk: str = "",
) -> None:
    try:
        from tools.airgap.hook_compat import get_session_id, store_event

        store_event(
            get_session_id(),
            "sag_approval",
            tool_name,
            {
                "decision": decision,  # "approved" | "denied"
                "mode": mode,
                "risk": risk,
                "reason": reason,
                "input_preview": _flatten(tool_input)[:200],
            },
        )
    except Exception as exc:  # noqa: BLE001 — auditing must never break the gate
        logger.debug("safety: audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Gate factory
# ---------------------------------------------------------------------------
def resolve_mode(mode: Optional[str] = None) -> str:
    """Resolve the approval mode: arg → env → ``args/agent_runtime.yaml`` → manual.

    The env var is still read before the config file and still wins
    (hgx-cfg-01). An unrecognised value at any layer resolves to ``manual``,
    never to ``off`` — a typo must not disable the approval gate.
    """
    if mode:
        m = mode.strip().lower()
        return m if m in _VALID_MODES else MODE_MANUAL
    try:
        from tools.agent_runtime.config import load_config

        return load_config().approval_mode
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("safety: config layer unavailable: %s", exc)
    m = (os.environ.get(_MODE_ENV) or MODE_MANUAL).strip().lower()
    return m if m in _VALID_MODES else MODE_MANUAL


def resolve_risk_function() -> str:
    """Routing function used for LLM risk judgment in ``smart`` mode.

    ``ICDEV_SAG_RISK_FUNCTION`` → ``args/agent_runtime.yaml`` → ``summarization``.
    A routing *function*, never a model id — the router picks the provider.
    """
    try:
        from tools.agent_runtime.config import load_config

        return load_config().risk_function
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("safety: config layer unavailable: %s", exc)
        return os.environ.get(_RISK_FN_ENV, "summarization")


def build_safety_gate(
    *,
    mode: Optional[str] = None,
    approver: Optional[Approver] = None,
    router: Any = None,
    checkpoint: bool = True,
):
    """Build the real :data:`~tools.agent_runtime.dispatch.SafetyGate`.

    Args:
        checkpoint: When True (default), an approved mutating call is snapshotted
            (sag-safe-02) before it is allowed to run, so it can be ``/rollback``\\ ed.
        mode: ``manual`` | ``smart`` | ``off`` (defaults to ``ICDEV_SAG_APPROVAL_MODE``
            then ``manual``).
        approver: Callable that grants/denies a risky mutation. Defaults to the
            console prompt; gateway agent-mode injects a messaging-adapter approver.
        router: Optional ``LLMRouter`` for ``smart`` risk judgment. When ``None``
            in smart mode, the keyword heuristic is used.

    Returns:
        ``gate(tool_name, tool_input, read_only) -> (allowed, reason)``.
    """
    resolved_mode = resolve_mode(mode)
    approve = approver or console_approver

    def _grant(tool_name: str, tool_input: dict[str, Any]) -> "tuple[bool, str]":
        """Common approved-path tail: snapshot the affected paths, then allow.

        The sag-safe-01 approval flow is the sag-safe-02 checkpoint trigger — an
        approved *mutating* command is snapshotted (best-effort) before it runs so
        the operator can ``/rollback``.
        """
        if checkpoint:
            try:
                from tools.agent_runtime.checkpoints import snapshot_for_tool

                snapshot_for_tool(tool_name, tool_input)
            except Exception as exc:  # noqa: BLE001 — snapshot must not block execution
                logger.warning("safety: checkpoint snapshot failed: %s", exc)
        return True, ""

    def gate(
        tool_name: str, tool_input: dict[str, Any], read_only: bool
    ) -> "tuple[bool, str]":
        if read_only:
            return True, ""
        if not isinstance(tool_input, dict):
            tool_input = {}

        # 1. Hard block via the existing headless pre-tool-use check.
        try:
            from tools.airgap.hook_compat import run_pre_tool_check

            hook_tool, hook_input = _map_to_hook(tool_name, tool_input)
            check = run_pre_tool_check(hook_tool, hook_input)
        except Exception as exc:  # noqa: BLE001 — if the checker itself breaks,
            # fail safe toward requiring approval rather than silently allowing.
            logger.warning("safety: pre_tool_check unavailable: %s", exc)
            check = {"allowed": True, "reason": "pre-check unavailable"}
        if not check.get("allowed", True):
            reason = check.get("reason", "blocked by safety policy")
            _audit("denied", tool_name, tool_input, reason, resolved_mode, "high")
            return False, reason

        # 2. Mode handling.
        if resolved_mode == MODE_OFF:
            _audit("approved", tool_name, tool_input, "yolo mode (off)", resolved_mode)
            logger.warning("safety: --yolo auto-approved %s", tool_name)
            return _grant(tool_name, tool_input)

        risk = "high"
        if resolved_mode == MODE_SMART:
            risk = assess_risk(tool_name, tool_input, router=router)
            if risk == "low":
                _audit(
                    "approved", tool_name, tool_input,
                    "smart auto-approve (low risk)", resolved_mode, risk,
                )
                return _grant(tool_name, tool_input)
        else:  # manual — still surface a heuristic risk label in the prompt
            risk = _heuristic_risk(tool_name, tool_input)

        # 3. Seek approval.
        request = ApprovalRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            risk=risk,
            detail=check.get("reason", ""),
        )
        try:
            allowed = bool(approve(request))
        except Exception as exc:  # noqa: BLE001 — a broken approver denies
            logger.warning("safety: approver raised, denying: %s", exc)
            allowed = False
        if allowed:
            _audit("approved", tool_name, tool_input, "operator approved", resolved_mode, risk)
            return _grant(tool_name, tool_input)
        _audit("denied", tool_name, tool_input, "operator denied", resolved_mode, risk)
        return False, "operator denied approval"

    return gate
