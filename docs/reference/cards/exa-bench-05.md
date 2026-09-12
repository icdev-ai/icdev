# PreToolUse hook enforcement — the hook's exit 2 now reaches the caller (exa-bench-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/hooks/fire_rate_survey.py --json               # per-check fire rate over recent sessions
python tools/hooks/fire_rate_survey.py --check dangerous_rm --samples 25
python tools/hooks/fire_rate_survey.py --gate --max-fire-rate 0.01
```

`.claude/settings.json` no longer wraps the hook in `|| true`, so a BLOCKED
refusal actually blocks. Stand it down with ICDEV_PRETOOLUSE_ENFORCE=0 (every
check still runs and prints, prefixed ADVISORY:) or per check with
ICDEV_<CHECK>_GUARD=0 — see CHECK_KILL_SWITCHES in .claude/hooks/pre_tool_use.py.
NEVER re-add a shell neutraliser: run the survey, then narrow the check.
Corpus is the Claude Code transcripts; hook_events stores tool-input KEY NAMES
only, so it cannot drive a replay and reports itself unusable.
