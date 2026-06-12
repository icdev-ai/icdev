# ICDEV™ Skills — SKILL.md Authoring Standard

Adapted from [mattpocock/skills](https://github.com/mattpocock/skills/tree/main/write-a-skill) (MIT).

Each skill directory contains a `SKILL.md` that acts as the contract between the skill and any orchestrating agent (Claude Code, Codex, Cursor, etc.). The description field is the **only** thing an agent sees when deciding which skill to load — everything else is loaded on demand.

---

## Required Structure

```
.agents/skills/<skill-name>/
  SKILL.md        # Primary skill file (≤ 100 lines)
  REFERENCE.md    # Detailed step procedures (split here when body > 100 lines)
  EXAMPLES.md     # Extended examples (optional)
  scripts/        # Deterministic helper scripts (optional)
```

---

## SKILL.md Format

```markdown
---
name: <skill-name>
description: "<First sentence: what it does in third person.> Use when <specific triggers: keywords, contexts, file types>."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $<skill-name>

## What This Does
Brief bullet list of capabilities (3-9 items).

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.  ← only if body is split

## Example
\`\`\`
$<skill-name> <typical invocation>
\`\`\`

## Error Handling
- Specific error conditions and responses
```

---

## Description Rules

| Rule | Requirement |
|------|-------------|
| Length | ≤ 1024 characters |
| Voice | Third person (e.g., "Runs...", "Generates...", "Displays...") |
| First sentence | What the skill does |
| Second sentence | Must start with **"Use when"** followed by specific triggers |
| Triggers | Keywords, contexts, file types, user intents that activate the skill |

**Good example:**
```
"Runs the full TDD cycle (RED → GREEN → REFACTOR) to generate tests, write minimal
implementation, lint, and map NIST 800-53 controls for a feature. Use when implementing
a new feature, fixing a bug with test-first discipline, or when generating code for an
ICDEV™ project from a feature description."
```

**Bad examples:**
- `"Build code with TDD"` — imperative, not third person, no 'Use when'
- `"This skill runs TDD..."` — informal, no 'Use when'
- (empty) — agent cannot make routing decisions

---

## Body Length Rule

**SKILL.md body must be ≤ 100 non-blank lines.**

When the body exceeds this limit, split it:
- Keep in `SKILL.md`: "What This Does" summary, Example, Error Handling, link to REFERENCE.md
- Move to `REFERENCE.md`: step-by-step procedures, code blocks, detailed output formats

---

## Deterministic Operations

If a skill requires deterministic operations (file transforms, DB queries, report generation), place the logic in `scripts/` as standalone Python scripts rather than embedding shell commands inline. This makes the skill testable and keeps SKILL.md concise.

---

## Enforcement

The coherence checker enforces this standard automatically:

```bash
python tools/workflow/coherence_checker.py --check skill_standard --gate
```

This check (`skill_standard`, OPT-56) runs as part of `--all` and fails the gate if:
- Any `description` is empty
- Any `description` exceeds 1024 characters
- Any `description` is missing the `Use when` sentence
- Any `SKILL.md` body exceeds 100 non-blank lines

---

## Skills Index

| Skill | Description |
|-------|-------------|
| `icdev-boundary` | Assesses ATO boundary impact and supply chain risk |
| `icdev-build` | TDD cycle: RED → GREEN → REFACTOR with NIST control mapping |
| `icdev-comply` | Generates ATO artifacts: SSP, POAM, STIG, SBOM, CSSP, SbD, IV&V |
| `icdev-deploy` | Generates IaC (Terraform/Ansible/K8s) and GitLab CI/CD pipeline |
| `icdev-init` | Initializes a new ICDEV™ project with compliance scaffolding |
| `icdev-innovate` | Runs autonomous self-improvement through web and codebase analysis |
| `icdev-intake` | RICOAS Phase 1: conversational requirements intake and SAFe decomposition |
| `icdev-integrate` | Syncs requirements with Jira, ServiceNow, GitLab, DOORS NG |
| `icdev-knowledge` | Queries and updates the self-learning knowledge base |
| `icdev-maintain` | Dependency audit: CVE scanning, maintenance score, auto-remediation |
| `icdev-market` | FORGE Marketplace: publish, install, search, review assets |
| `icdev-mbse` | MBSE integration: SysML/DOORS import, digital thread, DES compliance |
| `icdev-modernize` | 7Rs application modernization strategy and migration roadmap |
| `icdev-monitor` | Production monitoring with health checks, metrics, and self-healing |
| `icdev-query` | Queries ICDEV™ database for project metrics and compliance status |
| `icdev-review` | Pre-merge gate: tests, security, STIG, CUI, SBOM, lint |
| `icdev-secure` | Security scanning: SAST, dependency audit, secrets, container scan |
| `icdev-simulate` | Digital Program Twin simulations and COA generation |
| `icdev-status` | Comprehensive project status dashboard |
| `icdev-test` | Runs pytest + behave BDD with coverage and NIST control mapping |
| `icdev-worktree` | Creates and manages isolated git worktrees for ICDEV™ tasks |
