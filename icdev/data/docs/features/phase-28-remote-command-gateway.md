# Phase 28 — Remote Command Gateway

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 28 |
| Title | Remote Command Gateway |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 24 (DevSecOps Pipeline Security) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Field users, PMs, and ISSOs often need to issue ICDEV commands without access to a full development environment. A PM checking project status from a mobile device, an ISSO reviewing compliance posture from a tablet, or a field analyst requesting a quick security scan should not need VPN access, SSH tunnels, or the VSCode extension installed. Messaging platforms (Slack, Teams, Mattermost) are already the primary communication channels for most government and defense teams, making them a natural interface for lightweight command access.

However, exposing a powerful agentic system to messaging channels introduces significant security risks. Without proper identity verification, an attacker could impersonate a legitimate user and execute commands. Without classification filtering, CUI or SECRET content could leak to channels not authorized for that classification level. Without command allowlists, destructive operations (deploy, delete) could be triggered from low-security channels. Without rate limiting, a compromised account could flood the system with automated requests. The challenge is enabling convenience without compromising the security posture that ICDEV's entire architecture is designed to protect.

Phase 28 implements a Remote Command Gateway (port 8458) that receives commands from 5 messaging channels (Telegram, Slack, Teams, Mattermost, internal chat) and validates every request through an 8-gate security chain (signature, bot/replay, identity, authentication, classification, RBAC, rate limit, domain authority). Responses are filtered by Impact Level so content above a channel's maximum classification is redacted with a dashboard link. User binding is mandatory before any command execution, with a challenge-response ceremony for connected environments and admin pre-provisioning for air-gapped deployments. Air-gapped mode (`environment.mode: air_gapped`) auto-disables internet-dependent channels (Telegram, Slack, Teams), leaving only Mattermost and internal chat available.

---

## 2. Goals

1. Enable users to **issue ICDEV commands from messaging channels** (Telegram, Slack, Teams, Mattermost, internal chat) with full security validation and audit trail
2. Implement an **8-gate security chain** that validates every command: signature verification, bot/replay rejection, identity resolution, authentication, classification check, RBAC, rate limiting, and domain authority
3. Enforce **IL-aware response filtering** so content above a channel's maximum classification level is redacted and replaced with a dashboard link
4. Require **mandatory user binding** before command execution, with challenge-response ceremony (connected mode) and admin pre-provisioning (air-gapped mode)
5. Support **air-gapped mode** (`environment.mode: air_gapped`) that auto-disables internet-dependent channels and restricts to Mattermost + internal chat
6. Maintain a **YAML-driven command allowlist** with per-channel overrides, blocking destructive operations (deploy, init) on all remote channels by default
7. Implement channel adapters using the **ABC pattern** (D66) so new messaging channels can be added without modifying gateway core logic
8. Log all command executions to the **append-only audit trail** with full identity chain and classification filtering actions

---

## 3. Architecture

### 3.1 Command Execution Flow

```
[User Message in Slack/Teams/Mattermost/Telegram/Internal Chat]
  |
  v
[Channel Webhook / Adapter]
  |
  v
[8-Gate Security Chain]
  |-- Gate 1: Signature (HMAC verification of webhook payload)
  |-- Gate 2: Bot/Replay (reject bots, reject timestamps >5min old)
  |-- Gate 3: Identity (resolve channel user -> ICDEV user binding)
  |-- Gate 4: Authentication (validate user is active)
  |-- Gate 5: Classification (reject commands above channel max_il)
  |-- Gate 6: RBAC (check role permissions for command category)
  |-- Gate 7: Rate Limit (30/user/min, 100/channel/min)
  |-- Gate 8: Domain Authority (check agent veto rights)
  |
  v
[Command Router] -> [Tool Execution]
  |
  v
[Response Filter (IL-aware redaction)]
  |
  v
[Channel Reply]
```

### 3.2 Channel Support Matrix

| Channel | IL Range | Environment | Identity |
|---------|----------|-------------|----------|
| Telegram | IL2-IL4 | Connected only | Binding ceremony (HTTPS) |
| Slack | IL2-IL5 | Connected only | Binding ceremony (HTTPS) |
| Teams | IL2-IL5 | Connected only | Binding ceremony (HTTPS) |
| Mattermost | IL2-IL6 | Connected + Air-gapped | Admin pre-provision or binding |
| Internal Chat | IL2-IL6 | Always available | Admin pre-provision / CAC-PIV |

### 3.3 Air-Gapped vs Connected

```
Connected Mode:                    Air-Gapped Mode:
+-------------+                    +-------------+
| Telegram    | <-- auto-disabled  | Telegram    | X
| Slack       | <-- auto-disabled  | Slack       | X
| Teams       | <-- auto-disabled  | Teams       | X
| Mattermost  | <-- available      | Mattermost  | <-- available (REST API)
| Internal    | <-- available      | Internal    | <-- available
+-------------+                    +-------------+
    Config: environment.mode:          Config: environment.mode:
            connected                          air_gapped
```

---

## 4. Requirements

### 4.1 Security Chain

#### REQ-28-001: 8-Gate Validation
The system SHALL validate every incoming command through an 8-gate security chain: signature verification, bot/replay rejection, identity resolution, authentication, classification check, RBAC, rate limiting, and domain authority.

#### REQ-28-002: HMAC Signature Verification
Gate 1 SHALL verify HMAC-SHA256 signatures on webhook payloads to prevent tampering.

#### REQ-28-003: Replay Prevention
Gate 2 SHALL reject commands with timestamps older than 300 seconds (5 minutes) to prevent replay attacks.

#### REQ-28-004: Rate Limiting
Gate 7 SHALL enforce per-user rate limits (30 requests/minute) and per-channel rate limits (100 requests/minute).

### 4.2 Identity and Binding

#### REQ-28-005: Mandatory User Binding
The system SHALL require a verified user binding (channel user ID to ICDEV user) before any command execution. Unbound users are rejected at Gate 3 with instructions to initiate binding.

#### REQ-28-006: Binding Ceremony (Connected)
In connected environments, user binding SHALL use a challenge-response ceremony: user sends `/bind`, gateway returns an 8-character hex challenge code with 10-minute TTL, user enters the code in the ICDEV dashboard.

#### REQ-28-007: Admin Pre-Provisioning (Air-Gapped)
In air-gapped environments, the system SHALL support admin pre-provisioning of user bindings via CLI (`user_binder.py --provision`) without requiring internet connectivity.

### 4.3 Classification Filtering

#### REQ-28-008: IL-Aware Response Filtering
The system SHALL detect the classification level of response content and redact any content above the channel's maximum IL, replacing it with a dashboard link.

#### REQ-28-009: Never Upgrade Classification
Response filtering SHALL never upgrade content classification. If response IL exceeds channel max_il, the content is redacted, never transmitted.

### 4.4 Command Control

#### REQ-28-010: YAML-Driven Allowlist
The system SHALL maintain a YAML-driven command allowlist (`args/remote_gateway_config.yaml`) with per-channel overrides, enabling command permission changes without code modifications.

#### REQ-28-011: Deploy Disabled
The `icdev-deploy` and `icdev-init` commands SHALL be disabled on all remote channels by default. Destructive operations require dashboard or CLI access.

#### REQ-28-012: Confirmation for Execute Commands
Commands in the Execute category (icdev-test, icdev-secure, icdev-build) SHALL require user confirmation before execution on remote channels.

### 4.5 Air-Gapped Mode

#### REQ-28-013: Auto-Disable Internet Channels
When `environment.mode: air_gapped`, the system SHALL auto-disable internet-dependent channels (Telegram, Slack, Teams) without requiring manual per-channel configuration.

#### REQ-28-014: Mattermost REST API
The Mattermost adapter SHALL use REST API (not WebSocket) for compatibility with proxied and air-gapped deployments (D140).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `remote_user_bindings` | Channel user ID to ICDEV user mappings with TTL and status |
| `remote_command_log` | Append-only command execution log (NIST AU compliant) |
| `remote_command_allowlist` | Per-channel command permissions and restrictions |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/gateway/gateway_agent.py` | Flask gateway app on port 8458 with webhook routes |
| `tools/gateway/security_chain.py` | 8-gate security validation pipeline |
| `tools/gateway/command_router.py` | Command dispatch to ICDEV tools |
| `tools/gateway/response_filter.py` | IL-aware content redaction |
| `tools/gateway/user_binder.py` | User binding management (ceremony + pre-provision) |
| `tools/gateway/adapters/` | Channel adapters (ABC pattern: Telegram, Slack, Teams, Mattermost, Internal) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D133 | Channel adapters are ABC + implementations (D66 pattern) | Add new channels without modifying gateway core |
| D134 | Air-gapped environments use internal chat + Mattermost only | IL6/SIPR cannot reach Telegram/Slack/Teams APIs |
| D135 | Response filter strips content above channel max_il, never upgrades | Prevents CUI/SECRET leaking to unauthorized channels |
| D136 | User binding mandatory before any command execution | Full identity chain; no anonymous remote commands |
| D137 | Command allowlist is YAML-driven with per-channel overrides | Add/remove commands without code changes (D26 pattern) |
| D138 | Deploy commands disabled by default on all remote channels | Destructive operations require dashboard/CLI access |
| D139 | `environment.mode: air_gapped` auto-disables internet channels | Single config toggle; no per-channel manual disable needed |
| D140 | Mattermost adapter uses REST API (no WebSocket) | Consistent with D20; simpler; works behind proxies |

---

## 8. Security Gate

**Remote Command Gate:**
- User binding required (no anonymous commands)
- Signature verification on all webhooks (HMAC-SHA256)
- Replay window 300 seconds maximum
- Rate limit: 30/user/min + 100/channel/min
- `icdev-deploy` and `icdev-init` blocked on all remote channels
- `icdev-test`, `icdev-secure`, `icdev-build` require user confirmation
- Response content never exceeds channel maximum IL
- All commands logged to append-only audit trail

---

## 9. Commands

```bash
# Start the gateway
python tools/gateway/gateway_agent.py

# User binding management
python tools/gateway/user_binder.py --provision \
  --channel mattermost --channel-user-id "user123" \
  --icdev-user-id "analyst@enclave.mil" --json
python tools/gateway/user_binder.py --list --json
python tools/gateway/user_binder.py --revoke <binding-id>

# Gateway status
# GET http://localhost:8458/.well-known/agent.json

# Available commands from messaging channels:
#   icdev-status     (Read, all channels, no confirmation)
#   icdev-monitor    (Read, all channels, no confirmation)
#   icdev-knowledge  (Read, all channels, no confirmation)
#   icdev-comply     (Read, Slack/Teams/MM/Internal, no confirmation)
#   icdev-query      (Read, Slack/Teams/MM/Internal, no confirmation)
#   icdev-test       (Execute, Slack/Teams/MM/Internal, confirmation required)
#   icdev-secure     (Execute, Slack/Teams/MM/Internal, confirmation required)
#   icdev-intake     (Write, Internal only, confirmation required)
#   icdev-build      (Execute, Internal only, confirmation required)
#   icdev-deploy     (Execute, DISABLED on all remote channels)
```
