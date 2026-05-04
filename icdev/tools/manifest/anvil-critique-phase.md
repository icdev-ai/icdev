# ANVIL Critique Phase (Phase 61 — Feature 3)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ANVIL Critique Phase (Phase 61 — Feature 3)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ANVIL Critique | tools/agent/anvil_critique.py | Adversarial multi-agent plan critique: parallel dispatch to security/compliance/knowledge agents, severity classification, GO/NOGO/CONDITIONAL consensus, revision loop (max 3 rounds). Append-only findings (NIST AU). | --project-id, --phase-output, --session-id, --status, --history, --max-rounds, --json | Critique session + findings JSON |
| ANVIL Critique Config | args/anvil_critique_config.yaml | Critique phase config: critic agent assignments, focus areas, consensus rules, revision prompt, max rounds | (data) | YAML config |

