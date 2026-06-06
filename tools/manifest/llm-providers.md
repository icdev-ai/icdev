# LLM Providers (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## LLM Providers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gemini Provider | tools/llm/gemini_provider.py | Google Vertex AI Gemini LLM provider | (library) | GeminiProvider |
| CLI Bridge Activate | tools/llm/cli_bridge/activate.py | Auto-enable Claude CLI provider — prepends `claude-cli` to routing chains when air-gapped or no cloud key; gated by `ICDEV_CLI_BRIDGE` env | config dict | rewritten config |

