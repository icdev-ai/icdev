# Remote Command Gateway (Phase 28 — D133-D140)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Remote Command Gateway (Phase 28 — D133-D140)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gateway Agent | tools/gateway/gateway_agent.py | Remote command reception from 5 channels (Telegram, Slack, Teams, Mattermost, internal chat), 8-gate security chain, IL-aware response filtering | --port 8458 | Flask server |
| User Binder | tools/gateway/user_binder.py | Pre-provision user bindings (air-gapped mode), binding ceremony, revocation | --provision, --list, --revoke, --json | Binding records |


## Remote Command Gateway (Phase 28)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Command Router | tools/gateway/command_router.py | Route remote commands to ICDEV™ tools | (library) | Routed results |
| Event Envelope | tools/gateway/event_envelope.py | HMAC-signed event envelope (D31) | (library) | Signed events |
| Response Filter | tools/gateway/response_filter.py | IL-aware response classification filter (D135) | (library) | Filtered responses |
| Security Chain | tools/gateway/security_chain.py | 8-gate security chain for remote commands | (library) | Chain results |

