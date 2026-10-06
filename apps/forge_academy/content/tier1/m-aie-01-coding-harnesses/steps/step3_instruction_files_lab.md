---
ontology_id: icdev:mission:m-aie-01-coding-harnesses:step:3
step_class: icdev:Lab
skill_tag: coding_harnesses
---

# Lab: One Project, Three Instruction Files

Your team uses three harnesses — Claude Code, Codex CLI and Cursor — and you need all three to follow the same rules. Writing three files by hand guarantees they drift apart. Instead, write **one function** that renders all three from a single project description, the same pattern ICDEV uses in `tools/dx/instruction_generator.py` over the platform registry in `tools/dx/ai_platforms.py`.

## Your task

Implement `build_instruction_files(project)` in the starter. It receives a dict like:

```python
{
    "name": "ledger-api",
    "summary": "Internal REST API for the finance team's ledger.",
    "stack": ["Python 3.12", "FastAPI", "PostgreSQL"],
    "commands": {"test": "pytest -q", "lint": "ruff check .", "run": "uvicorn app.main:app"},
    "conventions": ["Type-hint every public function", ...],
    "boundaries": ["Never commit directly to main", ...],
}
```

and returns a dict mapping **file path → file content** with exactly these three keys:

| Harness | Path |
|---|---|
| Claude Code | `CLAUDE.md` |
| Codex CLI | `AGENTS.md` |
| Cursor | `.cursor/rules/project.mdc` |

## What the grader checks

Each of the three files must:

1. Contain the four sections from step 2 as `## ` headings, spelled exactly: `## Project Overview`, `## Commands`, `## Conventions`, `## Boundaries`.
2. Mention the project `name` and `summary`.
3. Include **every** command string verbatim, every convention and every boundary.

In addition, the Cursor file must start with YAML frontmatter — a first line of `---`, a `description:` line, `alwaysApply: true`, and a closing `---` — before any markdown.

The grader also runs your function on a **second, different project** it does not show you. Hardcoding `ledger-api` text will fail: the files must be rendered from the input.

## Hints

- Render the shared markdown body once, then wrap it per harness. Only the Cursor file needs a header.
- `"\n".join(...)` and a bullet per list item (`- item`) keep it readable.
- Put commands in backticks so an agent can copy them exactly.

The lab runs entirely offline: no model is called, and nothing is written to disk.
