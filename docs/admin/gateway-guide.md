# Remote Command Gateway Administration Guide

> CUI // SP-CTI

## Overview

The Remote Command Gateway (Phase 28) allows authorized users to send ICDEV commands from external messaging channels into the ICDEV platform. Commands are received via channel adapters, processed through an 8-gate security chain, and dispatched to the appropriate ICDEV agent for execution. The gateway runs on port 8458 as a standalone agent.

### Supported Channels

| Channel | IL Range | Connectivity | Notes |
|---------|----------|-------------|-------|
| Telegram | IL2-IL4 | Internet required | Bot API, public internet only |
| Slack | IL2-IL5 | Internet required | Slack GovCloud supported |
| Teams | IL2-IL5 | Internet required | Microsoft GCC/GCC-High supported |
| Mattermost | IL2-IL6 | Air-gapped supported | Self-hosted, REST API (no WebSocket) |
| internal_chat | IL2-IL6 | Always available | Built-in dashboard chat interface |

---

## Starting the Gateway

### Foreground Mode

```bash
python tools/gateway/gateway_agent.py
```

The gateway starts on port 8458 by default.

### Verify Gateway Status

Check the gateway agent card:

```
GET http://localhost:8458/.well-known/agent.json
```

---

## User Binding

User binding is **mandatory** before any command execution. No anonymous remote commands are permitted. Every command must trace to a verified identity.

### Provision a Binding (Pre-Provision for Air-Gapped)

```bash
python tools/gateway/user_binder.py \
  --provision \
  --channel mattermost \
  --channel-user-id "user123" \
  --icdev-user-id "admin@enclave.mil" \
  --json
```

### List All Bindings

```bash
python tools/gateway/user_binder.py --list --json
```

### Revoke a Binding

```bash
python tools/gateway/user_binder.py --revoke <binding-id>
```

### Binding Lifecycle

1. **Provision** -- Administrator creates a binding linking a channel user ID to an ICDEV user ID.
2. **Active** -- User can send commands through the bound channel.
3. **Expired** -- Binding TTL exceeded (configurable in `args/remote_gateway_config.yaml`).
4. **Revoked** -- Administrator explicitly revokes the binding.

Bindings are stored in the `remote_user_bindings` table with full audit trail.

---

## Channel Configuration

All channel configuration is managed in `args/remote_gateway_config.yaml`.

### Configuration Structure

```yaml
environment:
  mode: connected          # connected | air_gapped

channels:
  telegram:
    enabled: true
    max_il: IL4
    bot_token_secret: "telegram_bot_token"
    rate_limit:
      per_user_per_minute: 30
      per_channel_per_minute: 100

  slack:
    enabled: true
    max_il: IL5
    webhook_secret: "slack_webhook_secret"
    rate_limit:
      per_user_per_minute: 30
      per_channel_per_minute: 100

  teams:
    enabled: true
    max_il: IL5
    app_secret: "teams_app_secret"
    rate_limit:
      per_user_per_minute: 30
      per_channel_per_minute: 100

  mattermost:
    enabled: true
    max_il: IL6
    instance_url: "https://mattermost.enclave.mil"
    token_secret: "mattermost_token"
    rate_limit:
      per_user_per_minute: 30
      per_channel_per_minute: 100

  internal_chat:
    enabled: true
    max_il: IL6
    rate_limit:
      per_user_per_minute: 30
      per_channel_per_minute: 100

security:
  binding_ttl_days: 90
  signature_verification: true
  replay_window_seconds: 300
```

---

## Air-Gapped Mode

Setting `environment.mode: air_gapped` automatically disables all internet-dependent channels (Telegram, Slack, Teams). Only Mattermost (self-hosted) and internal_chat remain available.

```yaml
environment:
  mode: air_gapped
```

When air-gapped mode is active:
- Telegram, Slack, and Teams adapters are not initialized.
- No outbound internet connections are attempted.
- Mattermost must be self-hosted within the enclave.
- internal_chat is always available regardless of mode.

---

## 8-Gate Security Chain

Every incoming command passes through all 8 gates sequentially. Failure at any gate rejects the command.

| Gate | Check | Details |
|------|-------|---------|
| 1 | **User Binding** | Sender must have an active, non-expired binding |
| 2 | **Signature Verification** | Webhook signature validated (HMAC-SHA256) |
| 3 | **Replay Prevention** | Message timestamp within 300-second window |
| 4 | **Rate Limiting** | 30 commands/user/minute, 100 commands/channel/minute |
| 5 | **Command Allowlist** | Command must be in the allowlist for the channel |
| 6 | **IL Filtering** | Command output classification must not exceed channel max_il |
| 7 | **Confirmation Gate** | Certain commands require explicit confirmation |
| 8 | **Response Filtering** | Output stripped of content above channel max_il |

---

## Command Allowlist

The command allowlist is YAML-driven with per-channel overrides. Commands not in the allowlist are rejected.

### Default Allowlist

```yaml
command_allowlist:
  default:
    - icdev-status
    - icdev-test
    - icdev-secure
    - icdev-build
    - icdev-comply
    - icdev-review
    - icdev-knowledge
    - icdev-maintain
    - icdev-monitor

  per_channel:
    telegram:
      remove:
        - icdev-build    # Too resource-intensive for IL2-IL4 channel
    mattermost:
      add:
        - icdev-mbse     # Available on high-IL channels only
```

### Blocked Commands (All Channels)

The following commands are **permanently blocked** on all remote channels. These destructive operations require direct dashboard or CLI access:

| Command | Reason |
|---------|--------|
| `icdev-deploy` | Deployment changes require direct access and approval workflow |
| `icdev-init` | Project initialization modifies system state |

### Confirmation-Required Commands

The following commands require explicit user confirmation before execution:

| Command | Confirmation Prompt |
|---------|-------------------|
| `icdev-test` | "Run full test suite? Reply YES to confirm." |
| `icdev-secure` | "Run security scanning? Reply YES to confirm." |
| `icdev-build` | "Start build pipeline? Reply YES to confirm." |

---

## IL-Aware Response Filtering

The gateway never upgrades classification. Responses are filtered to ensure content above the channel's maximum IL is stripped before delivery.

### Filtering Rules

- If the command produces CUI output and the channel max_il is IL2, the CUI content is redacted.
- If the command produces SECRET output and the channel max_il is IL5, the SECRET content is stripped.
- Classification banners are adjusted to reflect the filtered content.
- The original unfiltered response is logged in the `remote_command_log` table for audit purposes.

---

## Dashboard Integration

The gateway administration page is available at `/gateway` on the ICDEV dashboard.

### Dashboard Features

| Section | Content |
|---------|---------|
| **Bindings** | Active user bindings with channel, user, status, expiry |
| **Command Log** | Recent commands with sender, channel, command, status, timestamp |
| **Channels** | Channel status (enabled/disabled, max_il, connection health) |

Access the dashboard:

```
http://localhost:5000/gateway
```

---

## Operational Procedures

### Adding a New User

1. Create the ICDEV user account (dashboard or `auth.py`).
2. Provision a binding for the user's messaging channel:
   ```bash
   python tools/gateway/user_binder.py \
     --provision \
     --channel slack \
     --channel-user-id "U12345678" \
     --icdev-user-id "jane.doe@agency.gov" \
     --json
   ```
3. Inform the user of the binding. They can now send commands via the bound channel.

### Rotating Bindings

Bindings have a configurable TTL (default 90 days). To rotate:

1. Revoke the existing binding:
   ```bash
   python tools/gateway/user_binder.py --revoke <old-binding-id>
   ```
2. Provision a new binding:
   ```bash
   python tools/gateway/user_binder.py \
     --provision \
     --channel slack \
     --channel-user-id "U12345678" \
     --icdev-user-id "jane.doe@agency.gov" \
     --json
   ```

### Adding a New Command to the Allowlist

1. Edit `args/remote_gateway_config.yaml`.
2. Add the command to `command_allowlist.default` or a `per_channel` section.
3. Restart the gateway agent.

### Investigating Failed Commands

1. Check the `remote_command_log` table:
   ```bash
   python tools/audit/audit_query.py --project "gateway" --format json
   ```
2. Look for the gate that rejected the command in the log entry's `rejection_gate` field.
3. Common failures:
   - Gate 1: User has no active binding -- provision or renew.
   - Gate 3: Message arrived outside replay window -- clock sync issue.
   - Gate 4: Rate limit exceeded -- user sending too many commands.
   - Gate 5: Command not in allowlist -- add to config if appropriate.

### Transitioning to Air-Gapped Mode

1. Update `args/remote_gateway_config.yaml`:
   ```yaml
   environment:
     mode: air_gapped
   ```
2. Ensure Mattermost is deployed within the enclave if needed.
3. Pre-provision all user bindings (no internet-based binding ceremony available).
4. Restart the gateway agent.

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `remote_user_bindings` | User-channel binding records with TTL and status |
| `remote_command_log` | Append-only log of all commands (accepted and rejected) |
| `remote_command_allowlist` | Runtime allowlist cache (source of truth is YAML) |

---

## Security Considerations

- All webhook payloads are verified via HMAC-SHA256 signature before processing.
- Replay attacks are mitigated by the 300-second timestamp window.
- Rate limiting prevents denial-of-service via command flooding.
- The append-only command log satisfies NIST AU-2 audit requirements.
- Mattermost uses REST API only (no WebSocket) per D140, consistent with air-gap compatibility.
- Channel adapters follow the ABC + implementations pattern (D133) for extensibility.
- Response filtering never upgrades classification (D135).
