# AI Governance Integration (Phase 50)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AI Governance Integration (Phase 50)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AI Governance Scorer | tools/requirements/ai_governance_scorer.py | Score AI governance readiness (6 components) for 7th readiness dimension | project_id, conn/db_path | JSON score + gaps |
| AI Governance Chat Extension | tools/extensions/builtins/010_ai_governance_chat.py | Chat hook: detect AI keywords, check governance gaps, inject advisory messages | chat context dict | context + governance_advisory |
| AI Governance Config | args/ai_governance_config.yaml | Intake detection keywords, chat governance, readiness weights, auto-trigger rules | (config) | YAML config |

