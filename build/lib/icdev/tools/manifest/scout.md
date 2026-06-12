# Scout

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Scout
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Daemon | tools/scout/daemon.py | Daily autonomous self-improvement scanner (introspect/trending/competitive pillars) | --once --json, --status --json, --pillar <name> --json | Scan results / digest |
| Config Updater | tools/scout/config_updater.py | Scout configuration auto-updater | --json | Updated config |
| Genesis Trigger | tools/scout/genesis_trigger.py | Trigger Genesis from Scout findings | --json | Trigger results |
| Install Scheduler | tools/scout/install_scheduler.py | Scout installation scheduler | --json | Schedule status |
| LLM Summarizer | tools/scout/llm_summarizer.py | LLM-powered Scout finding summarizer | --json | Summaries |
| Trending Pillar | tools/scout/pillars/trending.py | Trending topic detection pillar | --json | Trending topics |
| Introspect Pillar | tools/scout/pillars/introspect.py | Scout Pillar 1 — self-introspection; analyzes ICDEV codebase for test coverage, dead code, and configuration drift | --scan --json | Introspection findings |
| Competitive Pillar | tools/scout/pillars/competitive.py | Scout Pillar 3 — monitors competitors and identifies new ones by delegating to competitive intelligence scanner | --json | Competitive findings |
| Preflight | tools/scout/preflight.py | Scout preflight validation | --json | Preflight results |
| Daily Digest | tools/scout/digest.py | Scout daily digest generator — produces Markdown reports of Scout findings with LLM synthesis and recommended actions | --generate --date YYYY-MM-DD --json, --view --date YYYY-MM-DD, --list --json | Digest reports |

