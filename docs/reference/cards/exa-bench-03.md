# Agent adapter capability matrix — DECLARED vs ACTUAL per adapter (exa-bench-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/agents/capability_matrix.py --json          # 5 adapters x 7 capabilities
python tools/agents/capability_matrix.py --adapter claude_cli
python tools/agents/capability_matrix.py --capability sandbox_passthrough
python tools/agents/capability_matrix.py --gate          # exit 1 on a declared-but-absent capability
```

actual is present | absent | unconfirmed — and unconfirmed is NEVER folded into
either. Only behavioral (adapter code run) and interface (live object inspected)
probes may assert present/absent; a source_evidence probe may only say unconfirmed.
Routing consults it: pick_default("build", require=["sandbox_passthrough"]) skips
any adapter not MEASURED present. Claims: args/agent_capabilities.yaml
NOT a replacement for tools/workflow/executor_parity.py — that one replays a task
corpus in worktrees to measure OUTCOME parity. This measures CAPABILITY parity.
