# Plan: Agent-Loop Context-Window + Cost Guardrails (Gap #3)

## Goal
Add hard and soft budget guardrails to `icdev.tools.llm.agent_loop.run_agent_loop` so an agentic co-worker cannot:
1. Run away and burn an unbounded token budget.
2. Overflow the target model's context window because tool results keep accumulating.

The primitive remains **tool-handler-agnostic** and **backward-compatible**. All new knobs are optional; existing callers behave exactly as before.

---

## Assumptions

1. The agent loop's message history (`messages`) is the only thing that grows unbounded.
2. We can estimate token counts cheaply with the same `len(text)//4` heuristic already used by `icdev.tools.llm.context_compressor`.
3. `LLMResponse` already carries `input_tokens`, `output_tokens`, and `cost_usd` (set by providers/router).
4. We will reuse the existing reversible compressor rather than invent a new one.
5. Hard budget violations terminate the loop and are reported to the caller; they are **not** surfaced to the LLM for a "please summarize cheaper" retry (that is a separate feature).
6. This is the primitive layer; `CoWorkerThread` will inherit the defaults via config and can later expose per-role YAML overrides.

---

## Proposed API Changes

### `AgentLoopResult` additions

Add optional telemetry fields:

```python
total_input_tokens: int = 0
total_output_tokens: int = 0
total_cost_usd: float = 0.0
compression_events: list[dict[str, Any]] = field(default_factory=list)
truncation_reason: str = ""  # "max_iterations" | "max_total_tokens" | "max_cost_usd" | "stop_event" | "completed"
```

### `run_agent_loop()` new optional parameters

```python
max_total_tokens: int | None = None      # hard cap on cumulative input+output tokens across all turns
max_cost_usd: float | None = None      # hard cap on cumulative cost_usd
context_window_tokens: int | None = None  # soft threshold: if messages exceed this, compress before next turn
compression_budget_tokens: int | None = None  # target budget used when compression is triggered (defaults to context_window_tokens * 0.75)
```

When any hard cap is omitted, the loop reads defaults from `args/llm_config.yaml`:

```yaml
agent_loop:
  budgets:
    max_total_tokens: 32000
    max_cost_usd: 5.0
    context_window_tokens: 16000
    compression_budget_tokens: 12000
```

Explicit call parameters override the config.

---

## Approach: Per-Turn Budget Check + Compression Trigger

1. **Before each LLM turn**
   - Estimate current message-token count.
   - If `context_window_tokens` is set and messages exceed it, run `compress_messages(messages, budget_tokens=compression_budget_tokens)` from `icdev.tools.llm.context_compressor` and replace `messages` with `result.messages`.
   - Log a compression event (`method`, `original_tokens`, `compressed_tokens`, `compression_ratio`) to `AgentLoopResult.compression_events`.

2. **Build and send the request**
   - Use the (possibly compressed) message history.

3. **After each response**
   - Accumulate `response.input_tokens` and `response.output_tokens` into `total_input_tokens` / `total_output_tokens`.
   - Accumulate `response.cost_usd` into `total_cost_usd`.
   - Check hard caps:
     - If `total_input_tokens + total_output_tokens > max_total_tokens`: set `truncated=True`, `truncation_reason="max_total_tokens"`, append a system note explaining the cap was hit, and break.
     - If `total_cost_usd > max_cost_usd`: set `truncated=True`, `truncation_reason="max_cost_usd"`, append a system note, and break.

4. **On normal termination**
   - `truncation_reason = "completed"` if `done=True` and not truncated.
   - `truncation_reason = "max_iterations"` if the for-loop exhausted.
   - `truncation_reason = "stop_event"` if `stop_event` aborted the loop.

### Why this approach?

- **Reuses proven code**: `context_compressor.py` already logs to `llm_context_compression_log`, has reversible compression, and handles tool-use message lists.
- **No provider changes**: the loop passes a (possibly compressed) `messages` list to `LLMRequest`; providers remain unaware.
- **Deterministic safety**: hard caps are checked after every response, not estimated before.
- **Backward compatible**: all new parameters default to `None`; when no config is present, no limits are enforced.

### Alternatives considered

| Approach | Verdict |
|---|---|
| A. Add a separate `AgentBudget` stateful class | Rejected — adds a new abstraction and file when the loop already has a natural place to track totals. |
| B. Delegate compression to `LLMRouter._compress_request_context()` | Rejected — that function lives in `tools.llm.compression.context_compressor` and uses `truncate_middle`, which is lossy and drops entire messages. The agent loop needs content-aware reversible compression that preserves tool blocks. |
| C. Request model-token limit from provider metadata | Rejected — model configs already have `max_output_tokens`, but input context limits vary by model and are not consistently stored. The caller/config will supply the limit instead. |
| D. Estimate cost from tokens when `cost_usd` is missing | Rejected — pricing is per-model and only the router has that map. Approximations would be unreliable and could silently block legitimate runs. |

---

## Files to Modify

1. **`icdev/tools/llm/agent_loop.py`**
   - Import `compress_messages` from `icdev.tools.llm.context_compressor` (lazy import to avoid cycles).
   - Add token-estimation helper consistent with `context_compressor.estimate_tokens`.
   - Add `_load_budget_defaults()` that reads `args/llm_config.yaml` `agent_loop.budgets`.
   - Add `_should_compress_messages()` and `_compress_messages()` helpers.
   - Extend `AgentLoopResult` with new fields.
   - Extend `run_agent_loop()` signature with new params.
   - In the loop, before building `LLMRequest`, check/compress messages.
   - After each response, accumulate token/cost totals and check hard caps.
   - Set `truncation_reason` on all exit paths.

2. **`args/llm_config.yaml`**
   - Add an `agent_loop:` section under the existing `settings:` block (or as a new top-level key) with conservative defaults.

3. **`icdev/tools/ace/coworker_thread.py`**
   - In `_run_agent_loop`, pass through `max_total_tokens`, `max_cost_usd`, `context_window_tokens` from the role/config or let `run_agent_loop` read config defaults.
   - For v1, no per-role YAML changes are required; the primitive will read global defaults.

4. **`tests/test_agent_loop.py`**
   - Add `ScriptedRouter` support for setting `input_tokens`, `output_tokens`, `cost_usd` on responses.
   - Test `max_total_tokens` truncation.
   - Test `max_cost_usd` truncation.
   - Test `context_window_tokens` triggers compression (mock `compress_messages`).
   - Test `truncation_reason` values for each path.
   - Test that compression preserves tool-use message structure.

5. **`tests/test_ace_agent_mode.py`** (optional)
   - Verify that `CoWorkerThread._run_agent_loop` still works and does not regress.

---

## Test Plan

### New unit tests in `tests/test_agent_loop.py`

1. **`test_max_total_tokens_truncates`**
   - Scripted LLM returns two tool-call turns with `input_tokens=1000, output_tokens=500` each.
   - `max_total_tokens=2500`.
   - Expect `turns=2`, `truncated=True`, `truncation_reason="max_total_tokens"`, `total_input_tokens=2000`, `total_output_tokens=1000`.

2. **`test_max_cost_usd_truncates`**
   - Scripted LLM returns tool-call turns with `cost_usd=1.0` each.
   - `max_cost_usd=2.5`.
   - Expect `turns=2`, `truncated=True`, `truncation_reason="max_cost_usd"`, `total_cost_usd=3.0`.

3. **`test_context_window_compression_triggered`**
   - Large accumulated message history; `context_window_tokens=100`.
   - Mock `compress_messages` to return compressed messages and record the call.
   - Expect at least one compression event in `result.compression_events`.

4. **`test_truncation_reason_completed`**
   - LLM returns end_turn on first call.
   - Expect `truncated=False`, `truncation_reason="completed"`.

5. **`test_truncation_reason_max_iterations`**
   - LLM always returns tool_calls.
   - `max_iterations=2`.
   - Expect `truncated=True`, `truncation_reason="max_iterations"`.

6. **`test_compression_preserves_tool_blocks`**
   - Messages contain `tool_use`/`tool_result` list content blocks.
   - Compression must not drop non-text blocks.

### Regression tests

- Run existing `tests/test_agent_loop.py` and `tests/test_ace_agent_mode.py`.
- No existing test should change behavior because all new params are optional.

---

## Success Criteria

1. `pytest tests/test_agent_loop.py -v` passes, including new budget-guardrail tests.
2. `pytest tests/test_ace_agent_mode.py -v` still passes.
3. `ruff check icdev/tools/llm/agent_loop.py tests/test_agent_loop.py` is clean.
4. An agent-mode role with `max_iterations=100` cannot exceed `max_total_tokens` or `max_cost_usd`.
5. A long tool-output history exceeding `context_window_tokens` is automatically compressed before the next LLM call.

---

## Open Questions (decide before implement)

1. Should `max_total_tokens` be checked **before** the next LLM call using the running total + a model-context allowance, or **after** each response?  
   *Recommendation:* After each response — simpler and deterministic.

2. Should compression be triggered by message-token count **or** by a percentage of `max_total_tokens`?  
   *Recommendation:* Keep `context_window_tokens` as an independent soft threshold so callers can reason about context-window pressure separately from total-spend cap.

3. Should the system note appended on hard-cap truncation be sent to the LLM as a final user message?  
   *Recommendation:* No — the loop is terminated; the note is only in `result.messages` for inspection.
