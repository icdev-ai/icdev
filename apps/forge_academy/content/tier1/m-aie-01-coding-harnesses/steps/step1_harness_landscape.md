---
ontology_id: icdev:mission:m-aie-01-coding-harnesses:step:1
step_class: icdev:Lesson
skill_tag: coding_harnesses
---

# The Coding-Harness Landscape

In this mission you will learn what an AI coding harness is, how the major ones differ, and how to drive one well enough that its output is something you would merge.

> **Landscape snapshot: as of mid-2026.** This market moves monthly. Product names, modes and file conventions below were current when this lesson was authored; check each vendor's documentation before you rely on a detail. Nothing here quotes a price or a benchmark score, because both go stale faster than the lesson.

## A model is not a harness

A raw model takes text in and gives text out. It cannot open your repository, run your tests, or remember yesterday's conversation. A **harness** is the software wrapped around the model that turns it into something that can do engineering work. Every harness adds the same four things, in different proportions:

| What the harness adds | What it means in practice |
|---|---|
| **Tools** | Read files, search the repo, edit code, run shell commands, call MCP servers. The model *asks* for a tool call; the harness *executes* it. |
| **Context** | Decides what the model sees each turn: your instruction files, the open files, search results, tool output, the conversation so far. |
| **Permissions** | Decides which tool calls run automatically, which need your approval, and which are refused. |
| **Memory** | Carries knowledge across sessions — usually as plain files (an instruction file, a memory directory) re-read at the start of every session. |

Around those four sits the **agent loop**: the model proposes an action, the harness runs it, the result goes back into context, and the loop repeats until the task is done or the model stops. The same model can be mediocre in one harness and excellent in another, because the harness decides what it sees and what it is allowed to do.

## Three shapes of harness

### 1. Autocomplete (inline completion)

The original form: **GitHub Copilot** inline suggestions predict the next few lines as you type. The context is mostly the current file and nearby open tabs; there is no agent loop, no shell, and no multi-file plan. You stay in control of every keystroke. Low risk, low leverage — good for boilerplate, weak for anything that spans files.

### 2. IDE agents

An agent loop inside the editor, able to edit many files and run commands with your approval:

- **Cursor** — a VS Code-based editor with an agent mode; project rules live in `.cursor/rules/`.
- **Windsurf** — a VS Code-based editor with an agent ("Cascade"); rules live in `.windsurf/rules/`.
- **GitHub Copilot agent mode** — the agent mode of Copilot inside VS Code and other IDEs; repository instructions live in `.github/copilot-instructions.md`.

IDE agents show you diffs inline, which makes review easy, and they are bound to an open editor session.

### 3. Terminal agents

An agent loop in your shell. No editor is required, so they also run headless — in CI, in a cron job, or launched by another program:

- **Claude Code** (Anthropic) — reads `CLAUDE.md`; supports MCP servers, hooks, permission modes, subagents and skills.
- **Codex CLI** (OpenAI) — reads `AGENTS.md`; runs commands under configurable approval and sandbox settings.
- **Aider** (open source) — pair-programs in the terminal and makes a git commit for each change it applies; conventions are commonly kept in a file such as `CONVENTIONS.md` loaded with `--read`.
- **Gemini CLI** (Google, open source) — reads `GEMINI.md`.

Because terminal agents can run unattended, they are the ones that need the strongest permission and isolation discipline (step 2).

## Why the instruction file matters

Every agentic harness reads a **project instruction file** at the start of a session and treats it as standing context: `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex CLI, `.cursor/rules/*.mdc` for Cursor, `.github/copilot-instructions.md` for Copilot, `GEMINI.md` for Gemini CLI. **`AGENTS.md`** is the closest thing to a vendor-neutral convention — several harnesses besides Codex read it.

ICDEV publishes the same guardrails to ten such platforms. The canonical list is `tools/dx/ai_platforms.py` (`AI_PLATFORM_FILES`), and `tools/dx/instruction_generator.py` renders each platform's file from one source. One source, many renderings — that is the pattern you will build yourself in the lab.

## Choosing a shape

| If you need… | Reach for… |
|---|---|
| Faster typing inside a file you already understand | Autocomplete |
| Multi-file changes you want to review as inline diffs | An IDE agent |
| Unattended or scripted work (CI, batch refactors, many tasks in parallel) | A terminal agent |

Most teams use more than one. That is exactly why the instruction files must agree with each other: two harnesses reading two different sets of rules will produce two different codebases.
