# CUI // SP-CTI
"""Backward-compatibility shim for the agent-loop approval gate (ars-appr-01).

The canonical implementation lives in :mod:`icdev.tools.llm.approval_gate`. This
module is a pure re-export so ``from tools.llm.approval_gate import X`` yields the
*same object* as ``from icdev.tools.llm.approval_gate import X`` — object identity
matters here because tests monkeypatch the approver and the recorder.

Do not add logic here — edit the canonical module instead. A physically separate
copy is exactly how ``tools/llm/agent_loop.py`` drifted out of sync and silently
bound stale code at runtime.
"""
from __future__ import annotations

from icdev.tools.llm.approval_gate import *  # noqa: F401,F403

# Explicit re-exports: ``import *`` honours ``__all__``, and these underscore-
# prefixed helpers plus the module logger are imported by name elsewhere.
from icdev.tools.llm.approval_gate import (  # noqa: F401
    APPROVAL_LOG_TABLE,
    IRREVERSIBLE,
    MODE_DENY,
    MODE_MANUAL,
    MODE_OFF,
    REVERSIBLE,
    UNKNOWN,
    ApprovalDecision,
    ApprovalRequest,
    Approver,
    Classification,
    GateOutcome,
    _compiled_patterns,
    _default_actor,
    _hard_block_reason,
    _name_matches,
    _repo_root,
    _schema_index,
    build_approval_hook,
    classify,
    console_approver,
    default_approver,
    deny_approver,
    evaluate,
    flatten_input,
    is_enabled,
    load_config,
    logger,
    preview_input,
    record_decision,
    resolve_mode,
)
