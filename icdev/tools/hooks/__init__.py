# [TEMPLATE: CUI // SP-CTI]
"""Pre-tool-use safety checks shared by every orchestrator.

``shared_checks`` holds the one implementation of each blocking check.
``.claude/hooks/pre_tool_use.py`` (Claude Code) and
``tools/airgap/hook_compat.py`` (every non-Claude-Code orchestrator, incl. the
standalone agent runtime) both call it, so the two paths cannot drift apart.
"""
