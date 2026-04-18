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

## ODC Closed-Loop Hook (SDC Replay → ODC Verify)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SDC Replay Verifier | tools/observability_canvas/replay_verify.py | Verify TTP detection coverage for an SDC attack path; writes od_ttp_coverage + od_audit rows | ttp_ids: list[str], design_id: str | {path, results[{ttp_id, state, coverage_row_id}], summary{full,partial,none,total}} |

States: `full` = Sigma snippet + covered baseline; `partial` = one signal only; `none` = no coverage.
CLI: `python tools/observability_canvas/replay_verify.py T1059 T1078 --json`

