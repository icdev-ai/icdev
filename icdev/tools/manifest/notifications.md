# Notifications

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Notifications
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Routing Rules Engine | tools/notifications/routing_rules.py | Evaluate (severity x component x tenant) -> channels routing rules at send time (args/notification_routing.yaml). Canonical layer = NotificationGateway. Consumed by crx-gen-02/DMX. | resolve_channels(severity, component, tenant_id, default) | list[str] channels |
| Escalation + Ack | tools/notifications/escalation.py | Track unacknowledged critical alerts; re-route via routing rules after a timeout. Synchronous process_escalations() sweep (reflex/scheduler hook, no daemon). notification_escalations table (tenant_id+classification, RLS). | register_alert / acknowledge / process_escalations | escalation dicts |
| User Preferences | tools/notifications/preferences.py | Per-user channels, quiet hours (midnight-wrap aware), digest opt-in. notification_preferences table (tenant_id+classification, RLS). Narrows routing to recipient prefs. | get/set_preferences / resolve_user_channels / in_quiet_hours / wants_digest | prefs dicts / list[str] |
| Base Adapter | tools/notifications/adapters/base.py | Abstract base class for all notification delivery adapters | (library) | NotificationAdapter ABC |
| Email Adapter | tools/notifications/adapters/email_adapter.py | Email notification delivery adapter | --json | Delivery status |
| Slack Adapter | tools/notifications/adapters/slack.py | Slack webhook notification adapter — delivers messages to Slack channels via incoming webhooks | --json | Delivery status |
| Teams Adapter | tools/notifications/adapters/teams.py | Microsoft Teams webhook notification adapter — delivers messages to Teams channels via connectors | --json | Delivery status |
| Telegram Adapter | tools/notifications/adapters/telegram.py | Telegram Bot API notification adapter — sends messages via bot token and chat ID | --json | Delivery status |
| Webhook Adapter | tools/notifications/adapters/webhook.py | Generic webhook notification adapter — HTTP POST to arbitrary endpoints | --json | Delivery status |
| Compliance Notifier | tools/notify/adapter.py | Write-once multi-channel compliance notifier (STIG, cATO, gate blocks) | --health/--send/--json | NotifyResult JSON |
| Compliance Cards | tools/notify/cards.py | Card abstractions: ComplianceCard, STIGCard, CATOCard, SecurityGateCard | (library) | Typed card objects |
| Slack Compliance Channel | tools/notify/channels/slack.py | Slack Block Kit delivery for compliance cards | (library) | bool |
| Teams Compliance Channel | tools/notify/channels/teams.py | MS Teams Connector Card delivery for compliance cards | (library) | bool |
| Discord Compliance Channel | tools/notify/channels/discord.py | Discord webhook embed delivery for compliance cards | (library) | bool |
| Email Compliance Channel | tools/notify/channels/email.py | SMTP HTML/plain-text delivery for compliance cards | (library) | bool |

