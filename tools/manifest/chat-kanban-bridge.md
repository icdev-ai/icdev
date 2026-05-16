# Chat ↔ Kanban Bridge

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Chat-Kanban Integration

Links multi-stream chat contexts to kanban tasks for build visibility.
Tasks are tagged `dispatch_source='chat:{context_id}'` and appear in the
**Tasks** tab of the chat right panel, auto-polling every 8 seconds.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Kanban Bridge | tools/chat/kanban_bridge.py | Link/list/create Kanban tasks for a chat context; auto-create V&V chain (CodeLens + Coherence + E2E) | CLI: --list/--create/--vv-chain --context ctx-id | task dicts |
| Build Sync Extension | tools/extensions/builtins/081_build_kanban_sync.py | Extension hook: detects build-completion signals in assistant messages; auto-creates V&V chain tasks; throttled 20-turn cooldown | chat_message_after hook | V&V chain in kanban_tasks |
| Requirement Intake Hook | tools/chat/requirement_intake_hook.py | Auto-detect requirement-bearing messages (regex, no LLM); run intake engine + SAFe decomposition; route to HITL review queue instead of direct Kanban | context_id, user_message | {hitl_instance_id, session_id, requirements_found, review_url} |

### CLI
```bash
python tools/chat/kanban_bridge.py --list --context ctx-abc123 --json
python tools/chat/kanban_bridge.py --create --context ctx-abc --title "Task" --type build --json
python tools/chat/kanban_bridge.py --vv-chain --context ctx-abc --canvas govlift --json
```

### API Routes (chat blueprint additions)
| Route | Method | Description |
|-------|--------|-------------|
| /api/chat/{ctx_id}/tasks | GET | List tasks linked to context |
| /api/chat/{ctx_id}/tasks | POST | Create task linked to context |
| /api/chat/{ctx_id}/vv-chain | POST | Auto-create CodeLens + Coherence + E2E chain |

### UI (chat right panel — Tasks tab)
- 4th tab in the right panel, alongside RICOAS / Gov / Intel
- Shows task items with status badges (color-coded: green=done, blue=running, amber=queued, purple=suggested)
- "V&V Chain" button queues CodeLens + Coherence + E2E tasks
- Badge shows count of non-done tasks on the tab button
- Auto-polls every 8 seconds while context is active

### Build Completion Signals (extension 081)
The extension fires when assistant messages contain patterns like:
- "routes verified", "all N routes → 200 OK", "canvas build complete"
- "coherence gate … 0 failures", "implementation complete", "phase N complete"
- "tasks seeded", "tasks created"

Throttle: one V&V chain per context per 20 turns.
