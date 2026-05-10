# FORGE Framework Deep Dive — Trace a Live Tool Call

You've used ICDEV. Now you'll understand it. In this mission you'll trace a real goal execution from trigger to output — reading actual ICDEV source files and identifying where each layer hands off to the next.

## The FORGE Architecture (real paths)

```
goals/           ← What to achieve, which tools, in what order
tools/           ← Python scripts, one job each, deterministic
args/            ← YAML behavior settings
context/         ← Static reference material
hardprompts/     ← Reusable LLM instruction templates
```

## Your mission

Trace the execution of `goals/compliance_scan.md` (or the nearest equivalent in your instance) by reading:

1. **The goal file** — What tools does it invoke? In what order? What does it expect as input/output?
2. **The primary tool** — Find the main Python module the goal calls. What does it import? What external system does it touch?
3. **The args file** — Does this goal have an args YAML? What behavior can you change without editing code?
4. **The integration point** — Where does this tool write its output? DB table? File? API?

## What you'll implement

Write a `GoalTracer` class that reads ICDEV source files and extracts the execution graph:

```python
tracer = GoalTracer(goals_dir="goals/", tools_dir="tools/")
graph = tracer.trace("compliance_scan")
# → {"goal": "compliance_scan", "tools": [...], "args_file": "...", "output_type": "db"}
```

## Why this matters

You cannot extend ICDEV until you can read it. Every tool you write must fit into this architecture. Every goal you create must follow this pattern. This mission is the prerequisite to writing your first tool (T3-02).

## Success criteria

- `GoalTracer.trace()` reads a goal file and extracts tool references
- Tool references are resolved to actual file paths in `tools/`
- The trace result includes: goal name, tool list, args_file (if any), estimated output type
- At least 2 goals can be traced successfully
