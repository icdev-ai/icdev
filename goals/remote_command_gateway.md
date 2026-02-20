# CUI // SP-CTI
# Goal: Remote Command Gateway (Phase 28)

## Purpose
Enable users to issue ICDEV commands from messaging channels (Telegram, Slack, Teams, Mattermost, internal chat) with full security validation, classification filtering, and audit trail.

## When to Use
- User wants to interact with ICDEV from a messaging platform
- Field users need lightweight command access without VPN/SSH
- PMs/ISSOs need quick status checks from mobile devices
- Air-gapped environments need internal chat command support

## Prerequisites
- ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- Gateway config: `args/remote_gateway_config.yaml`
- Channel credentials set via environment variables (connected mode)
- User binding established (via ceremony or admin provisioning)

## Workflow

### 1. Gateway Startup
```bash
python tools/gateway/gateway_agent.py
```
- Loads config from `args/remote_gateway_config.yaml`
- Detects environment mode (connected vs air_gapped)
- Loads available channel adapters
- Starts Flask app on port 8458

### 2. User Binding (Required Before Commands)

**Connected environment:**
1. User sends `/bind` in messaging channel
2. Gateway returns one-time challenge code (8-char hex, 10-min TTL)
3. User enters code in ICDEV dashboard or provides API key
4. Binding created: channel user ID ↔ ICDEV user

**Air-gapped environment:**
```bash
python tools/gateway/user_binder.py --provision \
  --channel mattermost --channel-user-id "user123" \
  --icdev-user-id "analyst@enclave.mil" --json
```

### 3. Command Execution Flow
```
[User Message] → [Channel Webhook] → [8-Gate Security Chain] → [Command Router] → [Tool Execution] → [Response Filter] → [Channel Reply]
```

### 4. 8-Gate Security Chain
Every command must pass all 8 gates:
1. **Signature** — HMAC verification of webhook payload
2. **Bot/Replay** — Reject bots, reject timestamps >5min old
3. **Identity** — Resolve channel user → ICDEV user binding
4. **Authentication** — Validate user is active
5. **Classification** — Reject commands above channel's max_il
6. **RBAC** — Check role permissions for command category
7. **Rate Limit** — Per-user (30/min) and per-channel (100/min)
8. **Domain Authority** — Check agent veto rights

### 5. Response Filtering (D135)
- Detect classification level of response content
- If response IL > channel max_il → redact, provide dashboard link
- Log filtering action to audit trail

## Available Commands (Default Allowlist)

| Command | Category | Channels | Confirmation |
|---------|----------|----------|-------------|
| icdev-status | Read | All | No |
| icdev-monitor | Read | All | No |
| icdev-knowledge | Read | All | No |
| icdev-comply | Read | Slack, Teams, MM, Internal | No |
| icdev-query | Read | Slack, Teams, MM, Internal | No |
| icdev-test | Execute | Slack, Teams, MM, Internal | Yes |
| icdev-secure | Execute | Slack, Teams, MM, Internal | Yes |
| icdev-intake | Write | Internal only | Yes |
| icdev-build | Execute | Internal only | Yes |
| icdev-deploy | Execute | **Disabled** | Yes |

## Air-Gapped vs Connected

| Aspect | Connected | Air-Gapped |
|--------|-----------|------------|
| Channels | Telegram, Slack, Teams | Internal chat, Mattermost |
| Identity | Binding ceremony (HTTPS) | Admin pre-provision / CAC-PIV |
| LLM | Bedrock GovCloud | Ollama local |
| Config | `environment.mode: connected` | `environment.mode: air_gapped` |

## Tools
- `tools/gateway/gateway_agent.py` — Flask app, webhook routes
- `tools/gateway/security_chain.py` — 8-gate validation
- `tools/gateway/command_router.py` — Command dispatch
- `tools/gateway/response_filter.py` — IL-aware redaction
- `tools/gateway/user_binder.py` — Binding management
- `tools/gateway/adapters/` — Channel adapters (ABC pattern)

## Database Tables
- `remote_user_bindings` — Channel user ↔ ICDEV user mappings
- `remote_command_log` — Append-only command execution log (NIST AU)
- `remote_command_allowlist` — Per-channel command permissions

## Security Gate
See `args/security_gates.yaml` → `remote_command` section.

## Edge Cases
- **Unbound user sends command** → Rejected at Gate 3, prompted to /bind
- **CUI content on Telegram (IL4 max)** → Response redacted, dashboard link provided
- **Rate limit exceeded** → 429-equivalent, user notified, event logged
- **Deploy command on any remote channel** → Blocked (D138)
- **Air-gapped mode with Telegram enabled in config** → Auto-disabled (D139)
- **Challenge code expired** → User must re-initiate /bind
