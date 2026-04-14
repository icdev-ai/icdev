# Observability Hooks (Phase 39)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Observability Hooks (Phase 39)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Send Event | .claude/hooks/send_event.py | Shared utility: HMAC-signed event storage + SSE forwarding | session_id, hook_type, payload | Event ID |
| Post-Tool-Use Hook | .claude/hooks/post_tool_use.py | Log tool results to hook_events table (always exits 0) | tool_name, tool_input, tool_output | — |
| Notification Hook | .claude/hooks/notification.py | Log user notifications (always exits 0) | message | — |
| Stop Hook | .claude/hooks/stop.py | Capture session completion event (always exits 0) | session_id, reason | — |
| Subagent Stop Hook | .claude/hooks/subagent_stop.py | Log subagent task completion (always exits 0) | subagent_id, result | — |

