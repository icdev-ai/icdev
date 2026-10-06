---
ontology_id: icdev:mission:m-aie-04-capstone:step:3
step_class: icdev:Lab
skill_tag: ai_assisted_engineering
---

# Lab: Write the Instruction File the Next Agent Reads

*Facts in this lesson are current as of October 2026.*

You fixed the ledger. The next person to change it will probably be an AI agent, and it
will not have read this mission. What it *will* read is the project instruction file:
Claude Code loads `CLAUDE.md`, Codex CLI and several other harnesses read `AGENTS.md`, and
GitHub Copilot reads `.github/copilot-instructions.md` — at the start of every session.

The starter holds the instruction file the original author wrote. Every line of it is a
wish: "write good code" and "make sure the tests pass" cannot be checked, so they cannot
be followed or violated. Rewrite it so that each line is **checkable**.

## Your task

Replace the `INSTRUCTIONS` string with an instruction file that has these three sections,
as Markdown `##` headings:

| Section | Must contain |
|---|---|
| `## Commands` | The exact test command: `python -m pytest tests/test_ledger.py -q` |
| `## Rules` | At least three bullet rules (`- ...`). Each starts with **Never**, **Always**, **Do not** or **Must**. Together they cover the three defect classes you just fixed (see below). |
| `## Done means` | What evidence counts as done. It must name the test command (the grader looks for `pytest`), because "done" is the output of a run, not a sentence. |

The rules must name the three defect classes, one rule each. The grader looks for these
words in your rule bullets:

| Defect class | A rule must mention one of |
|---|---|
| Swallowed exception | `except` |
| Off-by-one boundary | `limit`, `boundary` |
| Partial write on failure | `unchanged`, `partial`, `rollback`, `before` |

## What fails the grader

- A missing section, or a test command that is not the exact one above.
- A rule that is not a bullet, or that does not start with an imperative.
- Vague, uncheckable phrases: "write good code", "best practices", "clean code",
  "be careful". If a reviewer cannot tell whether a line was obeyed, it is not a rule.
- A `|| true` after the test command. It makes the command succeed whatever the tests
  decide — the neutraliser from m-aie-03.
- More than **40** non-blank lines. The harness pays for this file in every session;
  ICDEV budgets its own `CLAUDE.md` with `args/claude_md_budget.yaml` for that reason.

The lab runs entirely offline: no model is called and nothing is written to disk.
