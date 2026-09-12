# Capability consumption — is a DECLARED capability actually being used? (exa-live-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/capability_consumption.py --json                  # all classes, 30d window
python tools/awareness/capability_consumption.py --window-days 7         # configurable window
python tools/awareness/capability_consumption.py --class reflex --json   # one class
python tools/awareness/capability_consumption.py --known-inert --json    # the 5 known-inert cases
python tools/awareness/capability_consumption.py --gate                  # exit 1 if a class is UNMEASURABLE
```

Reuses existing telemetry only (genesis_reflex_state, studio_mcp_dispatch_audit,
agent_approval_log, audit_platform, prompt_versions, audit_trail, agent_improvement_artifacts).
A missing table reports telemetry_available:false — never a misleading zero.
Config: args/capability_consumption.yaml
