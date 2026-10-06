---
ontology_id: icdev:mission:m-t3-01-forge-deep-dive:step:1
step_class: icdev:Lesson
---

# FORGE Framework Deep Dive — Trace a Live Tool Call

You've used ICDEV. Now you'll understand it. In this mission you'll trace a real goal execution from trigger to output — reading actual ICDEV source files and identifying where each layer hands off to the next.

## The FORGE Architecture (real paths)

FORGE has six layers. Five of them are directories at the repo root; the sixth,
**Orchestration**, is the AI agent (Claude Code or another harness) reading a goal and
deciding the tool order — it has no directory.

```
goals/           <- What to achieve, which tools, in what order (index: goals/manifest.md)
tools/           <- Python scripts, one job each, deterministic (index: tools/manifest.md
                    + per-topic shards in tools/manifest/<topic>.md)
args/            <- YAML behavior settings
context/         <- Static reference material
hardprompts/     <- Reusable LLM instruction templates
```

## Your mission

Pick a real goal from `goals/manifest.md` — for example `goals/compliance_workflow.md`
(ATO artifacts: SSP, POAM, STIG, SBOM...) or `goals/security_scan.md` — and trace it by reading:

1. **The goal file** — What tools does it invoke? In what order? What does it expect as input/output?
   Real goal files are prose with `## Process` / `### Step N` headings; the tool calls appear
   inline as commands such as `python tools/compliance/stig_checker.py ...`.
2. **The primary tool** — Find the main Python module the goal calls (e.g.
   `tools/compliance/stig_checker.py`). What does it import? What does it touch?
3. **The args file** — Does this goal have an args YAML (e.g. `args/compliance_config.yaml`)?
   What behavior can you change without editing code?
4. **The integration point** — Where does this tool write its output? DB table (e.g.
   `stig_findings`)? File? API?

> **Sandbox note:** the exercise runs in an isolated sandbox with no copy of the repo, so
> `goals/` does not exist there. `GoalTracer` falls back to `SAMPLE_GOALS` — two condensed
> goal summaries (`compliance_scan`, `memory_write`) written in a compact
> `# Tools:` / `# Args:` / `# Output:` header form so the parser has something
> regular to read. Those two names are exercise fixtures, not files in `goals/`; the tool,
> args and table names inside them are real. Run your finished tracer against your own
> checkout of `goals/` to see it work on the real files.

## What you'll implement

Write a `GoalTracer` class that reads ICDEV source files and extracts the execution graph:

```python
tracer = GoalTracer(goals_dir="goals/", tools_dir="tools/")
graph = tracer.trace("compliance_scan")   # a SAMPLE_GOALS fixture in the sandbox
# → {"goal": "compliance_scan", "tools": [...], "args_file": "...", "output_type": "db", "tool_count": N}
```

## Why this matters

You cannot extend ICDEV until you can read it. Every tool you write must fit into this architecture. Every goal you create must follow this pattern. This mission is the prerequisite to writing your first tool (T3-02).

## Success criteria

- `GoalTracer.trace()` reads a goal file and extracts tool references
- Tool references are resolved to actual file paths in `tools/`
- The trace result includes: goal name, tool list, args_file (if any), estimated output type
- At least 2 goals can be traced successfully
