# CUI // SP-CTI

# xrv-cost-03 — behavioural waste survey, live numbers

Re-derive every figure below with:

```bash
python -m tools.cost.waste_survey --since-days 30 --project ICDev --json
python -m tools.cost.waste_survey --since-days 30 --project ICDev          # human
```

**Measured 2026-09-12** against `~/.claude/projects`, 30-day window, project
filter `ICDev`. A live corpus moves under a reader: two runs eight minutes
apart returned **1600** and **1599** sessions (9181 / 9180 edits, 1659 / 1661
retries, 81.93% / 81.91%). Both are quoted; one figure off a moving corpus is
not a measurement. The table below is the second run.

`sessions` is the count of transcripts whose **mtime** falls in the window.
mtime is a transcript's LAST write, so this is sessions **active** in the
window, not sessions **started** — the report carries
`sessions_basis: transcript_mtime_in_window` rather than leaving that to be
guessed at.

---

## 1. One-shot rate

| | |
|---|---|
| edit calls | 9,180 |
| retries | 1,661 |
| one-shot edits | 7,519 |
| **one-shot rate** | **81.91%** |
| sessions that edited anything | 446 of 1,599 |

The rule, verbatim and unchanged from codeburn: **a RETRY is the same file
Edited again after a Bash call in between.** `Edit A, Bash, Edit A` is one
retry; `Edit A, Bash, Edit B` is none; `Edit A, Edit A` is none — two hunks,
not two attempts.

Most-retried paths in the window:

| retries | path |
|---:|---|
| 22 | `…/icdev-worktrees/cli/adhoc/fni-chain-01/CLAUDE.md` |
| 20 | `…/.claude/projects/C--ai-icdev/memory/project-icdev-ft-parent-split.md` |
| 18 | `…/icdev-worktrees/cli/adhoc/fni-chain-01/icdev_fin/bots/runtime.py` |
| 18 | `…/.claude/projects/c--AI-ICDev/memory/MEMORY.md` |
| 17 | `…/.claude/projects/c--AI-ICDev/memory/project-fdx-fathomdesk-migration.md` |

Three of the top five are **CLAUDE.md and memory files**, not source. Those
are append-shaped documents edited in a loop with a check between each edit,
so the rule counts each pass as a retry. That is the rule behaving correctly
and is worth knowing before anyone reads 1,661 as 1,661 failed code edits.

## 2. Re-read files — a LOWER BOUND, and the report says by how much

| | |
|---|---|
| offenders (same path Read ≥ 3× in one session) | 96 |
| `Read`-tool calls | 2,050 |
| shell (`Bash`/`PowerShell`) calls | 62,377 |

**This measure sees roughly one file read in thirty.** Only the `Read` tool
names a path; this deployment's own harness instruction steers sessions to
`cat` / `head` / `sed -n` through Bash, and a file read that way is a shell
command no path can be attributed to. `read_tool_calls` and `run_tool_calls`
ride on the report beside the offender count so nobody reads 96 as the whole
of the re-reading on this fleet.

Worst offenders: `tools/ci/pr_watcher.py` (9 reads in one session),
`tools/cortex/search_service.py` (7), `tools/genesis/reflexes/kanban.py` (7).

## 3. Definitions not invoked in the window

| kind | root | declared | not invoked | |
|---|---|---:|---:|---|
| command | `.claude/commands` | 70 | 68 | 97.14% |
| skill | `.agents/skills` | 23 | 23 | 100.0% |
| agent | `.claude/agents` | — | — | **absent** |
| **total** | | **93** | **91** | **97.85%** |

`.claude/agents` does not exist in this checkout. It reports `absent` with
`declared: None` — **never zero ghosts**, which would read as a clean bill of
health for a tree nobody looked at.

**All three detectors fire**, which is the positive control that makes the
97.85% a finding rather than a broken scanner: `tool_use` 166, `command_tag`
83, `slash_line` 32. Two of the 70 commands were invoked.

The verdict is `not_invoked_in_window` and **never `dead`**. A definition may
be reached from Cursor, Copilot, the headless `tools/skills/invoke.py` runner
or a cron job, none of which writes a Claude Code transcript, and this survey
cannot see any of them. The caveat ships on the report itself, not only here.

## 4. CLAUDE.md config bloat

| | |
|---|---|
| bytes | 357,077 |
| approx tokens | 89,078 (`chars_div_4`) |
| `@`-imports | 0 (none declared; nothing to expand) |
| sessions in window | 1,599 |
| tokens loaded in window | 142,435,722 |
| **tokens loaded per day** | **≈ 4,747,857** |

Token counts are `len(text) // 4` and are labelled `token_basis: chars_div_4`
on every field that carries one. **They are an approximation and must not be
quoted as billed tokens.** The card's 2026-09-11 reading of 352,915 bytes has
since grown to 357,077 — the file gains a block per card, so this number is a
moving one and is dated on every run.

## 5. MCP tools — the two counts, and how far they disagree

| | declared | reached | not reached |
|---|---:|---:|---:|
| **transcripts** (Claude Code callers) | 472 | **3** | 469 (99.36%) |
| **`studio_dispatch_only`** (Studio tool gate) | 472 | **1** | 471 |

The three reached from Claude Code are `kanban_get_task` (8 calls),
`kanban_update_task` (8) and `kanban_delete_task` (1) — 17 calls over 30 days
against a 472-entry registry.

**The two numbers measure different callers and are never merged.**
`studio_mcp_dispatch_audit` has exactly two writers, both Studio-internal
(`tools/studio/executors/mcp_executor.py` and `agent_tool_gate.py`). Nothing
under `tools/mcp/` — the servers Claude Code actually talks to — writes a
dispatch row, so **every MCP call made from a Claude Code session is invisible
to `capability_consumption --class mcp_dispatch_tool`**. On this host the
disagreement is 3 against 1: the Studio figure misses all 17 Claude Code
calls, and the transcript figure misses whatever Studio dispatched that no
session made. Neither is a correction of the other, and neither alone answers
"is this tool used".

This card changed **no** probe and **no** budget:
`args/liveness_gate.yaml: mcp_dispatch_tool: 467` is untouched. An
MCP-server-side audit write is a new writer to an append-only table and is its
own card.

### A defect found while measuring this, and fixed

The first implementation matched a transcript's tool name against
`TOOL_REGISTRY` **bare**, and reported **6** registry tools reached from
Claude Code. Three of those six were Playwright's. `TOOL_REGISTRY` declares
`browser_click`, `browser_navigate` and `browser_type`, and the Playwright MCP
server declares tools of exactly those names — so Playwright's 50 clicks and
176 navigations were credited to ICDEV's registry, doubling the one number
this section exists to produce.

Attribution is now **server-qualified**, and the serving servers are derived
from the checkout's own `.mcp.json` (a server whose command line names
`tools/mcp`), never from a hardcoded server name — so a deployment that
renames or adds an ICDEV server needs no edit. An unreadable config falls back
to bare matching and **says so** in `server_attribution:
bare_name_fallback`, rather than quietly reporting the inflated count.
Pinned by `test_another_servers_identically_named_tool_is_not_credited`.

---

## A pre-existing gate failure this card does NOT touch

`coherence_checker --check capability_liveness` fails on this branch, and it
fails identically on `main`:

```
mcp_dispatch_tool: 468 never-consumed unit(s) exceeds the grandfathered
budget of 467
```

It read **471** twenty minutes earlier in the same session. Both readings are
quoted, because the drift is itself the argument: this check reads a live
table over a rolling window, so its count moves under a reader without anyone
committing anything.

This branch touches **none** of that check's three inputs —
`tools/mcp/tool_registry.py`, `args/liveness_gate.yaml` and
`args/capability_consumption.yaml` are unchanged (`git diff --name-only
origin/main` names none of them). The check reads a live table, so the count
drifts with the board: CLAUDE.md records it at 468 for cef-rsv-01, which is
where it sits again. Raising the budget is forbidden by
CLAUDE.md and editing the probe is forbidden by this card; the repair is
routing the other MCP entry points through the same audit, which is a new
writer to an append-only table and its own card.

It is worth recording here because it is **the same defect this card
measures, seen from the other side**. The Studio probe says 468-471 of 472
registry tools are inert; the transcripts say 3 were reached from Claude Code
in 30 days. Both are true, of different callers, and the gap between the two
counts is exactly the blind spot the transcript measure exists to fill.

## What this survey is not

Report only, deliberately no `--gate` (kpr-fix-03). It measures the FLEET's
behaviour over a window, not a diff, so a gate would fail commits for a
condition the committer did not cause — and a survey with a `--gate` earns
itself a `|| true`.

Every rate is `None` — never `0.0` and never `100.0` — over an empty
denominator (`args/perfect_score_gate.yaml` is ratcheted to 0). A window
holding no transcript is `unmeasurable`, its own verdict, which never folds
into `clean`. Exit 2 = the survey could not be produced, which is never the
same as a survey that found no waste.
