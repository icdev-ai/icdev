---
ontology_id: icdev:mission:m-t3-02-write-your-first-tool:step:1
step_class: icdev:Lesson
---

# Write Your First ICDEV Tool

Every ICDEV capability starts as a tool: a Python module in `tools/` that does exactly one job. In this mission you'll write the core logic of a compliance evidence collector tool. (ICDEV already ships a production one — `tools/compliance/evidence_collector.py` — which you can read once you've finished yours.)

## The Tool Contract used in this exercise

FORGE's rule is that tools are deterministic and do one job; the LLM orchestrates, the tool executes. This exercise makes that concrete with a simple contract. A tool must:

1. **Do one job** — no orchestration, no LLM calls, no side-chain tools
2. **Accept structured input** — documented args, typed where possible
3. **Return structured output** — dict with `status`, `data`, and `error` keys
4. **Be deterministic** — same inputs → same output (no randomness)
5. **Handle its own errors** — return `{"status": "error", "error": "..."}` not raise

```python
# The return shape this exercise grades
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

## How a real ICDEV tool is shipped

The return shape above is this exercise's convention, not a schema the repo enforces. What a real
tool in `tools/` adds on top of the logic you write here:

- **A CLI** — an `argparse` entry point with a `--json` flag for machine-readable output. For example,
  `python tools/compliance/evidence_collector.py --project-id <id> --json`
  (other flags: `--framework`, `--freshness`, `--list-frameworks`).
- **A manifest entry** — one row appended to the matching topic shard under `tools/manifest/`
  (the real evidence collector is registered in `tools/manifest/compliance-evidence-auto-collection-lineage.md`).
  Grep those shards before writing a new tool — it may already exist.
- **Canonical imports** — new code imports `icdev.tools.*`; `tools.*` is a backward-compatibility shim.

## The evidence database (simulated)

The sandbox has no database, so you have an in-memory lookup table (`EVIDENCE_DB`) mapping `(system, control)` → evidence items. If no evidence exists, return `status: "partial"` with an explanation.

## Success criteria

- `collect_evidence()` returns the correct ICDEV tool shape
- `status` is always one of `"ok"`, `"error"`, `"partial"`
- Evidence items include `type` and `description` fields
- `compliance_status` is correctly derived from evidence count and type
- Calling with an unknown system returns `status: "partial"` with a helpful message
- `run_batch()` processes multiple (system, control) pairs and returns aggregated results
