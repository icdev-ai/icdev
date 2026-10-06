---
ontology_id: icdev:mission:m-aie-01-coding-harnesses:step:2
step_class: icdev:Lesson
skill_tag: coding_harnesses
---

# Driving a Harness Well

A harness amplifies whatever you give it. Vague instructions, unlimited permissions and one long, cluttered session produce confident, unreviewable changes. This lesson covers the seven controls that separate the two outcomes. Examples use Claude Code's names where a concrete name helps; the other harnesses have equivalents (as of mid-2026).

## 1. Instruction files — standing orders, not a wiki

The instruction file (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`, `.github/copilot-instructions.md`) is loaded into context **every session**. Put in it what the agent cannot reliably infer and must never get wrong:

- **Project overview** — what this is, the stack, where the important code lives.
- **Commands** — the exact commands to build, test and lint. An agent that guesses `npm test` when you use `pytest` wastes a turn and may report a false pass.
- **Conventions** — naming, import style, the patterns a reviewer will insist on.
- **Boundaries** — what it must never do: commit to `main`, edit generated files, touch secrets.

Keep it short. Every byte is paid for on every session, whether or not the task needs it. ICDEV's own `CLAUDE.md` once carried 82 incident write-ups inline — about 297 KB of a 367 KB file — and they were moved out to `docs/reference/cards/` with a one-line pointer each. Detail belongs in linked docs the agent reads **on demand**.

Format differences matter. Cursor's `.mdc` rule files open with YAML frontmatter (`description`, `globs`, `alwaysApply`) that controls *when* the rule is attached; `alwaysApply: true` attaches it to every request. Plain-markdown files (`CLAUDE.md`, `AGENTS.md`, Copilot's file) have no such header.

## 2. MCP servers — tools by protocol

The **Model Context Protocol** lets a harness call tools served by a separate process: a database, a ticket system, a browser. In Claude Code a project declares them in `.mcp.json` (or adds one with `claude mcp add`). Every server you add is new reach for the agent — grant the narrowest one that does the job, and prefer read-only access where read-only is enough.

## 3. Hooks and permission modes — deterministic guardrails

The model is probabilistic; your guardrails should not be. **Hooks** are your own scripts that the harness runs at fixed points. In Claude Code a `PreToolUse` hook sees each tool call before it runs, and **exit code 2 blocks the call**. That makes a hook a real gate — unless something swallows the exit code. ICDEV once wired its hook as `python pre_tool_use.py || true`: the shell always returned 0, so every check printed `BLOCKED` and blocked nothing. Test that a gate can actually refuse.

**Permission modes** set the default posture. Claude Code's include `default` (ask before risky actions), `acceptEdits` (file edits auto-approved), `plan` (read and propose only, change nothing) and `bypassPermissions` (approve everything — for sandboxes only). Codex CLI pairs approval policies with a sandbox. Choose the least permissive mode that lets the task move.

## 4. Plan, then act

For anything beyond a one-line change, ask for a **plan first** — in Claude Code, plan mode does exactly that. Read the plan, correct a wrong assumption while it costs a sentence, and only then let the agent edit. A plan is cheap to change; a 40-file diff built on a misunderstanding is not.

## 5. Subagents — a separate context for side quests

A **subagent** is a second agent the main one launches with its own fresh context, for a bounded job such as "find every caller of this function". It returns a conclusion, not the 30 files it read, so the main conversation stays focused. Use them for wide searches and independent sub-tasks; don't use them for work that needs the main thread's context.

## 6. Git worktrees — one agent, one checkout

Two agents in one working directory will fight: one runs `git checkout` and the other's edits land on the wrong branch. Give each agent its own **git worktree** — a second checkout of the same repository on its own branch:

```bash
git worktree add -b feat/login-fix ../wt-login-fix origin/main
```

ICDEV makes this a hard rule after concurrent sessions sharing one checkout moved `HEAD` under each other. Remove the worktree when its branch has merged (`git worktree remove ../wt-login-fix`).

## 7. Context hygiene

The context window is a budget, and a polluted one degrades every answer after it:

- **One task per session.** Start fresh (`/clear` in Claude Code) when you switch tasks.
- **Compact long sessions** (`/compact`) instead of letting old tool output crowd out the current task.
- **Point, don't paste.** Name a file path and let the agent read it rather than pasting a 2,000-line file.
- **Persist what you learn** in the instruction file or a memory file, not in a chat that will be cleared.
