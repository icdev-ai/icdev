# Semantic Loop Detection (ars-loop-01)

CUI // SP-CTI

## The gap this closes

`icdev/tools/llm/agent_loop.py` already enforces every ceiling that matters:
`max_total_tokens`, `max_cost_usd`, `max_iterations`, `tool_timeout_seconds`,
`llm_call_timeout_seconds`, plus two loop-shaped controls —

* **duplicate guard (control 3)** — warns at the 3rd and blocks the 5th call with
  *byte-identical* inputs;
* **stall detector (control 5, `stall_threshold`)** — aborts after N turns with no
  *novel* successful tool call.

Neither catches an agent that is making steady progress through semantically
equivalent actions:

```
read {"path": "tools/foo.py"}      →  def foo(): ...
read {"path": "./tools/foo.py"}    →  def foo(): ...
read {"path": "tools\\foo.py"}     →  def foo(): ...
```

Every call is novel by exact match, so the duplicate guard never counts past one
and the stall tracker records progress on every turn. The run continues until it
hits the token ceiling and reports `error_max_budget_tokens` — which reads as
*"task too big"*, when what actually happened is *"agent was stuck"*. Those two
need different responses (raise the budget vs. change the prompt/tooling), and
the telemetry could not tell them apart.

## What was added

**`tools/llm/loop_detector.py`** (mirrored to `icdev/tools/llm/`) — a pure,
stdlib-only detector. `detect_semantic_loop(records, config=)` clusters the most
recent `window` tool calls and returns a `LoopDetection`.

Two calls are equivalent when they share a tool name **and** both their arguments
and their results score at or above `similarity_threshold`.

* **Similarity** is the mean of `difflib.SequenceMatcher` ratio and token-set
  Jaccard over normalised text. Neither works alone at these string lengths:
  `SequenceMatcher` scores `file1.txt` vs `file2.txt` at ~0.98 (one character
  apart), while Jaccard scores cosmetically reordered commands too low to ever
  match. Their mean separates the two (0.49 vs 1.0).
* **Normalisation** removes spellings that do not change what an action does:
  case, surrounding quotes, `\` vs `/`, repeated separators, `./` segments, and
  token order. **Digits are deliberately preserved** — scrubbing numbers would
  make `5 failed` and `2 failed` identical, which is exactly the signal that
  separates progress from a loop.
* **Results are compared, not just arguments.** This is the main
  false-positive guard: an agent fixing a failing test re-runs the same command
  every turn, but its output changes each time. Only when *both* the action and
  its outcome stop changing is it a loop.
* **Results are sampled head + tail** (`result_sample_chars`, 400). Head-only
  sampling compares banner text (`platform win32 -- Python 3.12 ...`) and would
  call two very different pytest runs identical; the summary line is at the end.

A cluster is only flagged when all four hold:

| Guard | Default | Rejects |
|-------|---------|---------|
| `min_cluster_size` | 3 | a single coincidence |
| `min_distinct_turns` | 3 | one turn fanning out several similar read-only calls |
| `min_distinct_variants` | 2 | byte-identical repetition — that is the duplicate guard's job, and it blocks the call and demands a new approach rather than ending the run |
| `coverage_ratio` | 0.6 | re-listing a directory *between* genuine edits |

**Wiring (`run_agent_loop`, control 6).** The check runs at the top of each turn,
before the LLM call, so the run stops within a few turns instead of at the
ceiling. On detection:

* `result.result_subtype = ResultSubtype.error_semantic_loop`
* `result.truncation_reason = "semantic_loop"` — distinct from `max_total_tokens`
  and `max_cost_usd`, which is the point
* `result.loop_detection` carries the evidence (tool, cluster size, window size,
  distinct turns, distinct variants, mean similarity, human-readable reason)

Two adjacent fixes came with it: `error_stalled` now sets
`truncation_reason="stalled"` (it previously fell through to `"max_iterations"`,
the same class of mislabelling), and `loop_detection={...}` is accepted per call
to override or disable the control.

**Config** — `args/llm_config.yaml` → `agent_loop.loop_detection` (mirrored to
`icdev/args/` and `icdev/data/args/`). Every key is tunable; `enabled: false`
turns the control off without a code change.

## Tuning against real transcripts

A detector tuned only against synthetic loops has never been shown *not* to kill
legitimate iterative work, and a false positive is worse than the loop it
prevents. `tools/llm/loop_detector_tune.py` replays recorded transcripts —
Claude Code `*.jsonl` sessions, or an `AgentLoopResult.tool_call_log` `*.json` —
through the detector after every tool call, exactly as the live loop evaluates
it, and reports every window that would have been flagged.

```bash
python tools/llm/loop_detector_tune.py --transcripts ~/.claude/projects/<proj>
python tools/llm/loop_detector_tune.py --transcripts <dir> --threshold 0.75 --json
python tools/llm/loop_detector_tune.py --transcripts <dir> --max-flag-rate 0.0   # regression gate
```

The shipped thresholds were chosen by replaying this repository's own recorded
agent sessions (302 transcripts, ~130k tool calls) at both the production
threshold and a deliberately aggressive one, and inspecting what the aggressive
setting flagged. `--max-flag-rate` turns a pinned corpus into a gate, so the
false-positive question stays answerable after future threshold changes.

## Verification

`tests/test_agent_loop_semantic_loop.py` (19 tests):

* **Caught** — same file re-read under different path spellings; a failing
  command re-run with cosmetic variation; a loop that survives one unrelated call
  landing in the window.
* **Not caught** — iterative test-fixing (same command, changing output);
  reading four different files; a parallel fan-out inside one turn; a repeated
  `list` interleaved with real edits; byte-identical repetition.
* **End to end** — a synthetic loop through `run_agent_loop` ends with
  `error_semantic_loop` / `truncation_reason="semantic_loop"` having spent under
  25% of the token budget, while a legitimate iterative task *of the same length*
  runs to `success` / `"completed"`; the stall guard is shown not to be what
  fires; and `loop_detection={"enabled": False}` restores the old behaviour.

`tests/test_agent_loop.py` and `tests/test_agent_loop_wiring.py` (103 tests) pass
unchanged.
