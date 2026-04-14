# AppForge — Autonomous Vertical App Builder

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AppForge — Autonomous Vertical App Builder

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/appforge/daemon.py` | Autonomous vertical discovery + app builder + Pulse writer: 5 Reflexes (discover, evaluate, architect, build, publish) on a daily cycle. Subclass of DaemonBase. Enabled via `ICDEV_APPFORGE_ENABLED`. |

### 5 Reflexes (tools/appforge/reflexes/)

| Reflex | Risk Tier | Purpose |
|--------|-----------|---------|
| discover | GREEN | Scan Innovation/Creative/Research engines to find high-value vertical challenges |
| evaluate | GREEN | Score and select the top challenge to build |
| architect | GREEN | Generate app blueprint and specification |
| build | GREEN | Create standalone child app (Flask + SQLite + professional UI) |
| publish | GREEN | Write and publish Pulse article about the build |

