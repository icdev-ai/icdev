# Bedrock Client (Opus 4.6 Multi-Agent — Phase A)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Bedrock Client (Opus 4.6 Multi-Agent — Phase A)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Bedrock Client | tools/agent/bedrock_client.py | Bedrock-specific wrapper: invoke, streaming, tool loops, model fallback chain (Opus→Sonnet 4.5→Sonnet 3.5), adaptive thinking, effort parameter, structured outputs. For vendor-agnostic access use tools.llm instead. | --prompt, --model, --effort, --probe, --stream, --json | BedrockResponse |
| Token Tracker | tools/agent/token_tracker.py | Token usage/cost tracking per agent/project/task with multi-provider pricing from llm_config.yaml (falls back to bedrock_models.yaml) | --action summary/cost, --project-id, --agent-id, --json | Usage summary |

