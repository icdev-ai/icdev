# CUI // SP-CTI
"""Shared pre/post/stop tool-use safety checks (hgx-guard-01, hgx-guard-02).

The modules here are the single source of truth for the guardrails that both
``.claude/hooks/`` (Claude Code) and ``tools/airgap/hook_compat.py`` (every
non-Claude-Code orchestrator) enforce, so the two paths cannot drift apart.

``shared_checks`` is deliberately stdlib-only at import time: the Claude Code
hook is a short-lived process spawned once per tool call and loads it straight
from disk, without paying the ~80 ms cost of importing the ``tools`` package.
"""
