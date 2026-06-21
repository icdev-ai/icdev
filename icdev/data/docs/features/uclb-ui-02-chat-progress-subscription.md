# UCLB-UI-02 — Chat UI subscribes to cli_synthesis progress events

CUI // SP-CTI

## Summary

When a chat send is deferred to a background CLI job (`status: 'pending'` +
`job_id`), the chat page now subscribes to the live `emit_progress`
`cli_synthesis` stream and updates the pending bubble's spinner label as the job
moves through its phases (`queued → calling CLI → synthesizing → done`). If the
SSE stream is unavailable, the UI falls back silently to the existing
`/<id>/messages` poll loop, which still renders the real answer when it lands.

## How it works

1. `tools/llm/cli_bridge/subprocess_backend.py::_emit` broadcasts structured
   progress events via `sse_manager.emit_progress(operation_type="cli_synthesis",
   operation_id=<job_id>, phase, completed, total, status, detail)`.
2. These events are **broadcast-only** (not persisted to `hook_events`), so the
   correct transport is the SSE endpoint `GET /api/events/progress`, not
   `/api/events/poll` (which reads the DB). The task's "or /api/events/poll"
   option does not apply because poll cannot see broadcast-only progress events.
3. `tools/dashboard/static/js/chat.js`:
   - The deferred-job pending bubble is tagged with `data-job-id`.
   - On a `pending` send response, `subscribeJobProgress(res.job_id)` opens an
     `EventSource('/api/events/progress')` and listens for `progress` events.
   - Events are filtered to `operation_type === 'cli_synthesis'` and
     `operation_id === job_id`; matching events update the `.pending-notice`
     text via `CLI_PHASE_LABELS` (with an optional percent suffix).
   - On terminal status (`completed` / `failed`) the stream is closed. On
     `failed` the bubble shows the failure detail and hides the spinner.
   - The subscription is torn down on context switch/close and on stream error.

## Graceful fallback

- No `EventSource` support → `subscribeJobProgress` returns immediately.
- SSE construction throws or stream errors → `closeJobProgress()` is called.
- In all fallback paths the existing message poll loop renders the answer.

## Files changed

- `tools/dashboard/static/js/chat.js`
- `icdev/tools/dashboard/static/js/chat.js` (mirror per dashboard completeness gate)

## Verification

- `node --check` passes on both chat.js copies.
- Phase labels keyed off the exact phase strings emitted by
  `subprocess_backend._emit` (`queued`, `running`, `done`) plus a `synthesizing`
  alias for forward compatibility.
