# xrv-mem-02 — PostToolUse observation capture: the measured rate

**Measured 2026-09-11 on this host (Windows 11, PostgreSQL board `icdev`).**
Report only. Every number below is re-derivable with the command beside it.

## What was measured

`tools/memory/auto_capture.py::capture()` had no hook caller: the buffer it fills
was flushed by `memory_maintenance_reflex` / `heartbeat_daemon` and filled by
nothing (`SELECT COUNT(*) FROM memory_buffer` = 0 on the live board before this
card). `.claude/hooks/post_tool_use.py` now derives a DETERMINISTIC observation
per event and captures it through that seam; `user_prompt_submit.py` captures a
`decision:` turn. Four kinds, closed: `edit`, `commit`, `test_summary`,
`decision`.

Two measurements, two bases, never merged:

1. **Replay of the shipped predicate over one working day of Claude Code
   transcripts** (`~/.claude/projects/**/*.jsonl`, which carry the full tool
   input AND `toolUseResult`, unlike `hook_events`, which persists key names
   only). This is `observe_tool` itself over real events — one computation,
   never a second copy of the rule — deduplicated by content hash across the
   window exactly as the buffer deduplicates.
2. **The live buffer during the session that built this card**, read back
   from PostgreSQL.

## 1. Transcript replay

```
python -m tools.hooks.observation_capture --survey --since-days 7 --json
```

`--since-days` filters transcripts by file MTIME (a session file written in the
last day can carry events from earlier days), so per-day counts are reported by
each event's own timestamp and the headline is the per-day series, not the
window.

| | value |
|---|---|
| transcripts read (7 days, project `ICDev`) | 197 |
| sessions with at least one observation | 148 |
| tool_use events replayed | 20,258 |
| observations, by kind (before dedup) | edit 2,098 · commit 116 · test_summary 1,140 · decision 0 |
| distinct observations (content-hash) | 2,325 |
| `<private>` spans stripped / wholly private skipped | 0 / 0 |
| days with an observation | 10 |

Distinct rows per UTC day:

| day | rows | | day | rows |
|---|---|---|---|---|
| 2026-09-02 | 38 | | 2026-09-07 | 104 |
| 2026-09-03 | 39 | | 2026-09-08 | 462 |
| 2026-09-04 | 7 | | 2026-09-09 | 168 |
| 2026-09-05 | 563 | | 2026-09-10 | 144 |
| 2026-09-06 | 250 | | 2026-09-11 (partial, to 23:12Z) | 550 |

**The number the card asked for — rows captured per working day on this host:**

| statistic | rows/day |
|---|---|
| last full UTC day (2026-09-10) | **144** |
| median over the 10 measured days | **156** |
| mean over the 10 measured days | 232.5 |

The spread (7 to 563) is the spread of the operator's day, not of the
predicate: 09-04 was a near-idle day and 09-05 / 09-11 were multi-session
build days. Quote the median, not the mean; the mean is dragged by the two
busiest days.

`decision` is 0 because the `decision:` tag is a convention this card
introduces — unmeasured, not zero-valued. `<private>` is 0 for the same reason.

### A defect the survey found before it shipped

The first replay read **4** test summaries against 116 commits over 7 days.
The pytest regex demanded the `===== N passed ... =====` bars; this repo runs
every gated suite `-q`, which prints the bare `N passed in 1.2s`. Both shapes
are read now (1,140 summaries), and the DURATION is stripped from the stored
observation — the outcome is the fact, and `24 passed in 1.13s` versus
`24 passed in 1.19s` was every re-run of the same suite as a fresh row.

## 2. The live buffer, this session

Board `icdev` (PostgreSQL), session `924aff08-dbe5-4d1e-b0be-eb3912170d8a`,
the kanban worker that built this card in the worktree
`.tmp/worktrees/xrv-mem-02`:

```
python -m tools.hooks.observation_capture --status --json
```

| instant (UTC) | `COUNT(*) FROM memory_buffer` |
|---|---|
| before the hook edit | 0 |
| 23:11Z, after five edits | 5 (one `edited <relpath>` row per Edit/Write, session id carried) |
| 23:16Z | 9 |

The per-session counter file `.tmp/memory_capture/<session>.json` read
`{"captured": 5}` at the first reading — the cap is counted there because
`memory_buffer` auto-flushes at 100 rows and a count over a table that empties
itself can never reach 200.

A worktree session reaches the PostgreSQL board because the worker inherits
the scheduler's environment (the DSN). Its `hook_events` rows, by contrast,
go nowhere: `send_event.py` writes only `<checkout>/data/icdev.db`, which a
worktree does not have — a pre-existing gap this card does not touch, and why
the `memory_capture` verdict the hook now puts on the event payload is
measurable only from a main-checkout session.

## 3. Cost per tool call

Five subprocess runs of the hook per shape, this host, median / min / max ms,
measured with a scheduler cycle and two genesis daemons running:

| event | ms | what it pays |
|---|---|---|
| Read (not a capture tool) | 193 / 189 / 201 | the hook as it was: `send_event` + extension dispatch |
| Bash `ls` (observed, yields nothing) | 203 / 199 / 206 | +10: the library import (`re`, `json`, `icdev.core.paths`); no storage import |
| Edit (capture, a duplicate) | 474 / 464 / 548 | +280: `tools.db.storage` import + one PostgreSQL round trip |

The storage import is paid only when an event yields an observation, which
the replay above puts at 3,354 of 20,258 events (16.6%) over 7 days — the
Read/Grep/WebFetch majority pays ~10 ms.

## What this does NOT measure, named

* `heartbeat_daemon.check_memory_maintenance` flushes `data/memory.db`, a
  file this board does not use; delivery to `memory_entries` here is
  `memory_maintenance_reflex` (`flush_buffer()` with no `db_path`, the same
  board `capture` writes to). Not repaired by this card.
* `flush_buffer` carries `type` and not `tier`, so a `procedural` chain row
  lands with `type='procedural'` and the column's default
  `tier='episodic'`. Carrying `tier` needs the column on SQLite's DDL too.
* `memory_entries` has a UNIQUE `(content_hash, user_id)`: `edited tools/x.py`
  lands ONCE, ever. The capture is of distinct facts, not a log — which is
  what the dedup seam was built to do.
* The second sink was dry-run against the live detector (20 patterns seen,
  20 at or above `min_pattern_frequency` 3, 0 written) and NOT dispatched
  through the daemon: the synthesize reflex runs weekly and its goal-draft
  step calls the LLM polisher, which is not a cost this measurement should
  spend.
