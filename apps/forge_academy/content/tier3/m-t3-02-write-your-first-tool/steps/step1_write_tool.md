# Write Your First ICDEV Tool

Every ICDEV capability starts as a tool: a Python module in `tools/` that does exactly one job. In this mission you'll write a compliance evidence collector tool — a real addition to the ICDEV toolchain.

## The ICDEV Tool Contract

An ICDEV tool must:
1. **Do one job** — no orchestration, no LLM calls, no side-chain tools
2. **Accept structured input** — documented args, typed where possible
3. **Return structured output** — dict with `status`, `data`, and `error` keys
4. **Be deterministic** — same inputs → same output (no randomness)
5. **Handle its own errors** — return `{"status": "error", "error": "..."}` not raise

```python
# Standard tool return shape
{
    "status": "ok" | "error" | "partial",
    "data": {...},          # the result
    "error": None | "msg",  # error message if status != "ok"
    "meta": {               # optional metadata
        "tool": "tool_name",
        "version": "1.0",
    }
}
```

## What you'll build

A **compliance evidence collector** that, given a system name and control ID, returns structured evidence for that control:

```python
collect_evidence(system_name="ICDEV-Prod", control_id="IA-2") → {
    "status": "ok",
    "data": {
        "control": "IA-2",
        "system": "ICDEV-Prod",
        "evidence": [...],
        "compliance_status": "compliant" | "non-compliant" | "partial",
        "evidence_date": "2026-05-02",
    },
    ...
}
```

## The evidence database (simulated)

You have a lookup table (`EVIDENCE_DB`) mapping `(system, control)` → evidence items. If no evidence exists, return `status: "partial"` with an explanation.

## Success criteria

- `collect_evidence()` returns the correct ICDEV tool shape
- `status` is always one of `"ok"`, `"error"`, `"partial"`
- Evidence items include `type` and `description` fields
- `compliance_status` is correctly derived from evidence count and type
- Calling with an unknown system returns `status: "partial"` with a helpful message
- `run_batch()` processes multiple (system, control) pairs and returns aggregated results
