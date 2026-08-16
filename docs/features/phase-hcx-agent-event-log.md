# CUI // SP-CTI

# The append-only agent event log (hcx-evt-01, hcx-evt-02, hcx-evt-05)

**Card:** `hcx` — see [docs/research/deepseek-harness-cordis-adaptation.md](../research/deepseek-harness-cordis-adaptation.md) §3.1.
**Table:** `agent_session_events`, migration `20260816122036`.
**Modules:** `tools/agent_runtime/event_log.py` (the store), `tools/agent_runtime/event_recorder.py` (the consumer), `tools/audit/row_hash.py` (the shared hashing recipe). All mirrored to `icdev/tools/`.

---

## The defect

`icdev/tools/llm/agent_loop_session.py::save_session` persists an agent run as
ONE `messages_json` blob, UPSERT-overwritten on every turn:

```sql
INSERT INTO agent_loop_sessions (... messages_json ...) VALUES (...)
ON CONFLICT (session_id) DO UPDATE SET messages_json = excluded.messages_json
```

That is enough to **resume** a session and nothing else. Once turn N+1 is
written, turn N no longer exists — so fork and replay are not merely
unimplemented, **the data they would need is already destroyed**. "What did the
model actually see, and when" is answerable only from a transcript that has been
overwritten.

Three things that look like they cover it and do not:

- **`llm_gateway_audit`** stores hashes only, deliberately, for privacy — and it
  is imported by `tools/cortex/*` and `tools/ops_hub/llmops_engine.py`, neither
  of which is on the agent-runtime path. SAG's own LLM calls were unaudited.
- **No context injection was recorded anywhere.** `project_context.py`,
  `goal_context.py` and `profile_memory.py` all inject into the prompt; a
  tree-wide grep for `context_injection|injected_context|prompt_snapshot|rendered_prompt`
  returned three unrelated files.
- **`harness_eval`** is populated and live (1,993 rows on the 2026-08-16 probe)
  but is a per-dispatch *outcome* table keyed by `task_id` — no `session_id`, no
  `seq`, no messages, no tool calls. It records *that the codegen reflex decided
  X and the dispatch ended Z*, never what the model saw. Different substrate,
  different grain; this was built alongside it rather than widening it.

The adapted commitment is DSH's runtime invariant **"model-visible means
logged"**: anything reaching a model request must be reconstructable from the
log. See the research note for why the harness itself was not adopted.

## What was built

One immutable row per model-visible event.

| Column | Note |
|---|---|
| `event_id` | PK |
| `session_id` | The **chat context id** — see the recorder section |
| `seq` | Monotonic per session from 1; UNIQUE over `(session_id, seq)` |
| `event_type` | Six values, below |
| `occurred_at` | Correlation only, **never ordering** |
| `payload_hash` | `NOT NULL`, always written |
| `payload_json` | NULL means WITHHELD BY POLICY and nothing else |
| `tenant_id`, `classification` | Makes the table RLS-eligible |
| `correlation_id` | The per-turn loop identity |

**The vocabulary is deliberately smaller than DSH's**: `turn_start`,
`request_context`, `assistant_message`, `tool_call`, `tool_result`, `turn_end`.
There is no per-chunk event — ICDEV's loop does not stream into the log, so a
chunk row would record the transport's framing rather than anything the model
saw, at one row per token.

## Five decisions worth re-reading before changing this

**Ordering is `seq`, not the clock.** Several events inside one turn routinely
share a millisecond, so a timestamp cannot totally order a session. The UNIQUE
index is what makes optimistic allocation safe: `next_seq()` reads the current
maximum and `append()` **retries the constraint violation a lost race produces**,
rather than doing the read-modify-write-with-no-constraint that gave this repo
three colliding migration numbers in a single session.

**The main DB, not the canvas DB** — stated explicitly because CLAUDE.md's
canvas rule cuts the other way and `agent_loop_sessions`, the table this one
complements, is canvas-resident. Two reasons override it: a canvas table has no
`tenant_id`/`classification`, so every read would be unfiltered, and these rows
can hold verbatim model input; and `tools/agent_case/session_timeline.py` joins
`hook_events` and `audit_trail`, both of which are in the main DB. A
canvas-resident log could be joined to neither.

**`payload_hash` always, `payload_json` conditionally.** The hash comes from
`tools/audit/row_hash.py::compute_payload_hash` — extracted there rather than
written in the caller so this codebase keeps **one** hashing recipe and not two;
the migration-149 audit-chain recipe is untouched and still pinned. Retention is
a classification decision in `args/agent_event_log.yaml`, and it **fails closed**
to hash-only on a config that exists but cannot be parsed. A retained `None` is
stored as the JSON literal `null`, so "suppressed" and "empty" never collapse
into one value a replay would read wrong.

**Append-only in the only sense that matters.** Registered in
`APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`, and the module exposes
`append()`, `read_session()`, `next_seq()` — no mutating verb exists to call. A
correction is a new event, the same rule `sbom_records.supersedes_sbom_id` and
`trust_deltas.supersedes_delta_id` follow.

**The INSERT is not wrapped in a bare `except`.** A swallowed INSERT is how
`module_budget_usage` held zero rows while reporting success. `append()` raises
by design; the swallow lives in exactly one place, in the recorder, with a count
you can read back.

## The consumer (hcx-evt-02)

A store with no writer is [this platform's signature
defect](../research/deepseek-harness-cordis-adaptation.md) with an extra step,
so `event_recorder.py` wires it to a real turn. `TurnRecorder`, one per
`AgentRuntime.run_turn`, plugged into the four lifecycle hooks
`run_agent_loop` **already exposed** — no new hook machinery was added to the
loop, because none was needed.

| Event | Emitted from |
|---|---|
| `turn_start` | `run_turn` itself — `on_turn` fires only *after* a model response, so it could never carry the user's own input |
| `assistant_message` | `on_turn` |
| `tool_call` | `on_pre_tool_use` |
| `tool_result` | `on_post_tool_use` |
| `turn_end` | `on_stop`, plus an idempotent `finally` backstop |

Three properties that are the threat model rather than coverage:

- **The recorder cannot allow a tool call.** `on_pre_tool_use` returns `None` on
  every path *including the failure paths*, and `_compose_pre_tool_hooks` places
  the caller's hook after the approval gate where the first non-empty block
  message wins. An audit outage must not silently become a refusal, and a
  logging hook must not be able to rescue a call the gate denied.
- **The recorder cannot kill a turn.** It catches `Exception`, never
  `BaseException`, so Ctrl-C still stops the run.
- **A gate-blocked or unregistered call still gets a `tool_call` event.**
  `on_post_tool_use` fires for every entry in `tool_calls`; `on_pre_tool_use`
  does not — it is skipped for an unregistered tool and short-circuited when the
  gate blocks — which would leave *exactly the denied calls* with no record of
  having been attempted. The missing event is reconstructed from the post-hook's
  own arguments and tagged `observed: post_tool_use`, so it is never mistaken
  for one the pre-hook saw dispatched.

**`session_id` is the chat context id**, not `AgentLoopResult.session_id`, which
is a fresh UUID on every call even when resuming and would make each user message
its own one-turn session. The per-turn loop identity moved to `correlation_id`,
minted by `for_turn()` and passed to `run_agent_loop(correlation_id=...)`.

Additive: `agent_loop_sessions.messages_json` stays the resume path. Kill switch
`ICDEV_AGENT_EVENT_RECORDING=0`; **default ON**, because shipping it off would be
the declared-but-unconsumed defect again.

## The fork (hcx-evt-05)

The reason the ordering was built. `tools/agent_runtime/fork.py` turns a prefix
of this log back into a message list and seeds a NEW session from it, which is
the branching primitive ICDEV did not have — `run_agent_loop(parent_session_id=…)`
records sub-agent *lineage*, not "this session is that one up to turn N".

```bash
icdev chat --fork <ctx-id>                    # survey the legal boundaries; creates nothing
icdev chat --fork <ctx-id> --at 12            # branch there and drop into the REPL
python -m tools.agent_runtime.fork --session <ctx-id> --at 12 --dry-run --json
```

**A boundary inside an open turn is REFUSED, not rounded** — the one refusal
borrowed from DSH rather than rediscovered. A prefix ending mid-turn is not a
shorter conversation, it is an illegal one: an assistant `tool_use` with no
matching `tool_result`, which the next provider call rejects. Refused likewise: a
`seq` that names no event (the log is the only place that number means anything,
so it is never clamped to a neighbour), an unanswered tool call or an orphaned
result — holes in the log rather than a boundary anyone chose — and a prefix
whose projected payloads are **withheld**, because a withheld payload is not an
empty one and projecting it would seed a fabrication carrying a correct-looking
digest. Every refusal names the legal boundaries either side.

**The event order is not the message order.** `on_turn` fires *after* the
post-tool hooks, so a tool-using iteration is recorded `tool_call, tool_result, …,
assistant_message` — the message announcing the calls arrives after the results
answering them. The projection buffers a result until its call lands, so both
that order and the reverse project to the same legal list.

A fork writes the projected messages to `agent_loop_sessions` (**read back before
it is linked** — a `resume_session_id` pointing at a row that was never written
produces a session that looks continued and remembers nothing), a `chat_contexts`
row whose `context_config.fork` holds the parent id, boundary seq, seed length
and a digest over the seeded events' hashes, one `session_fork` event at `seq` 1
of the new session's own log, and the projected turns replayed into
`chat_messages`. The prefix events are **not copied**: the digest identifies them
without duplicating a byte, and a copy would have needed a second write verb on a
module whose surface is deliberately `append` / `read_session` / `next_seq`.

`session_fork` is the second non-model-visible member of `EVENT_TYPES`, after
`permission_posture`, and adding it needed no migration — the payoff of holding
the vocabulary in Python rather than in a `CHECK` constraint.

One limitation, inherited and not introduced: the forked session's next turn
behaves exactly as `--resume`'s does, and `run_agent_loop` does not append a new
`user_prompt` to a transcript loaded from `resume_session_id`
(`tests/test_agent_loop.py::test_resume_loads_prior_messages` passes
`user_prompt="ignored"`). That is a property of the resume seam in
`AgentRuntime.run_turn`, not of forking.

## Using it

```bash
python tools/agent_runtime/event_log.py --session <ctx-id> --json
python tools/agent_runtime/event_log.py --session <ctx-id> --type tool_call --with-payload
python tools/agent_runtime/event_log.py --policy --json     # what retention resolved to
ICDEV_AGENT_EVENT_RECORDING=0 icdev chat                    # stand recording down for a run
```

`event_recorder.py` is a library with no CLI — import `TurnRecorder`. Full
reference: [docs/reference/commands.md](../reference/commands.md) → "Agent
Session Event Log".

## Verification

- `tests/test_agent_event_log.py` — 42 tests. The fixture builds the table from
  the migration's own `up.sql`, so a column added to one and not the other fails
  there rather than at runtime.
- `tests/agent_runtime/test_event_recorder.py` — 38 tests.
- `tests/agent_runtime/test_fork.py` — 21 tests, against the same
  migration-built table. Half of them are the refusals: a boundary mid-turn, a
  `seq` naming no event, an unanswered tool call, a withheld payload, and a
  refused fork leaving nothing behind.

Both gated in `args/ci_test_files/core.txt` in the PR that made them pass, per
the test-gating policy; `red_first_gate` recorded the RED for each.

**Registered with the substrate gate (hcx-evt-06).** `agent_session_events` is
declared in `args/capability_consumption.yaml` under `substrates:`, deliberately
*before* a writer existed — until hcx-evt-02 landed, this table was the textbook
shape the substrate probe exists to name (see the substrate-liveness rule in
[CLAUDE.md](../../CLAUDE.md)): a substrate something is designed against that
holds nothing.

```bash
python tools/awareness/capability_consumption.py --probe-substrate agent_session_events
```

## Related

- [docs/research/deepseek-harness-cordis-adaptation.md](../research/deepseek-harness-cordis-adaptation.md)
  — the source analysis; §2 is why DSH was not adopted, §3.1 is this adaptation,
  §4 is what was rejected.
- [phase-hcx-tool-execute-before-dispatch.md](phase-hcx-tool-execute-before-dispatch.md)
  — §3.3, the gating extension point (hcx-live-01).
- [phase-hcx-agent-lifecycle-extension-points.md](phase-hcx-agent-lifecycle-extension-points.md)
  — §3.3, `AGENT_START`/`AGENT_END` and the four points that still cannot fire
  (hcx-live-03).
- [hcx-post-01-permission-postures.md](hcx-post-01-permission-postures.md)
  — §3.2, named permission postures.
- `hcx-evt-03` (in flight at time of writing) records every context injection as
  a `request_context` event — the gap §3.1 names where nothing recorded what was
  injected into a prompt.
