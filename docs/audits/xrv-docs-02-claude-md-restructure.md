# CLAUDE.md restructure — 82 card essays moved to `docs/reference/cards/` (xrv-docs-02)

**Measured 2026-09-12 on `kanban/xrv-docs-02` off `7ac421e49`.**

CLAUDE.md is loaded into EVERY Claude Code session, and `icdev init` copies the
packaged twin `icdev/data/claude_bootstrap/CLAUDE.md` into every scaffolded
project, so a byte here is paid for by every session on every deployment whether
or not the session touches the subject.

The essays are **not waste in themselves** — each is a hard-won incident record
that a card working *that* subsystem needs. The waste is loading all 82 into a
session that touches one.

---

## 1. Before and after

| | Before | After | Δ |
|---|---|---|---|
| `CLAUDE.md` bytes | 367,462 | **79,700** | −287,762 (−78.3%) |
| `CLAUDE.md` lines | 4,542 | 474 | −4,068 |
| approx tokens (`len/4`) | 91,674 | **19,737** | −71,937 (−78.5%) |
| card essays inline | 82 | 0 | — |
| `docs/reference/cards/` | — | 82 files, 306,365 bytes | +82 |

Where the bytes were, before:

| Region | Bytes | Share |
|---|---|---|
| the 82 card essays, inline in `### Essential Commands` | 297,635 | 81.0% |
| `### Development Rules` (part of the tail below) | 43,754 | 11.9% |
| everything after the essays (architecture → end) | 64,562 | 17.6% |
| the genuine essential-commands prelude | 4,502 | 1.2% |

> The card's own WHY paragraph said 352,915 bytes / 4,354 lines / 61 essays.
> Re-measured at the head this branch forked from the file is **367,462 bytes /
> 4,542 lines / 82 essays** — it grew 14,547 bytes and gained 21 essays between
> the card being written and being dispatched, which is itself the growth rate
> the card is about. Every number in this document is the re-measured one.

### The real token cost, and what is NOT measured

`len/4` is an approximation. The measurement is
`cache_creation_input_tokens` on the **first assistant turn** of a session —
the actual prompt-cache write, of which CLAUDE.md is one component.

Sampled over the 174 sessions in `~/.claude/projects/C--AI-ICDev*` whose
transcript was touched in the 7 days to 2026-09-12:

```
sessions sampled  174
min               38,788
p50              143,764
p90              174,464
max              189,787
```

**The AFTER of that measurement is UNMEASURABLE today, and is not reported as a
number.** A session's first-turn cache write can only be measured by a session
that starts *after* this lands; quoting a projection as a measurement is the
defect half this repo's audit docs exist to refuse. What *is* projectable, and
labelled as a projection: if the real tokeniser tracks `len/4` for this text,
the p50 first-turn cache write falls from 143,764 to roughly **71,800**.

The file-size side of it IS measured, by the committed tool,

```bash
python -m tools.cost.waste_survey --since-days 7 --project ICDev --json
```

whose `config_bloat` block reads, on the post-move tree:

```json
{"state": "measured", "path": "CLAUDE.md", "bytes": 79700,
 "approx_tokens": 19737, "token_basis": "chars_div_4",
 "sessions": 268, "window_days": 7.0,
 "tokens_loaded_in_window": 5289516, "tokens_loaded_per_day": 755645.1}
```

At the same 268-sessions-per-7-days rate the pre-move file was loading
**3,509,447 approximate tokens a day**; it now loads 755,645. Both figures share
the `chars_div_4` basis and the same session count, so the *ratio* is sound even
though neither side is a tokeniser measurement.

---

## 2. The survey: what asserts on CLAUDE.md's CONTENT

`grep -rln 'CLAUDE\.md' tests/ tools/ .githooks/ .github/` returns **396 files**.
Almost all of them use `CLAUDE.md` as a *repo-root sentinel*
(`any((parent / s).exists() for s in ("pyproject.toml", ".git", "CLAUDE.md"))`)
and never read a byte of it. The ones that assert on its **content**, and what
each asserts, are below. **Every one is green and NO test was edited.**

| Site | What it asserts | Survives the move because |
|---|---|---|
| `tests/ci/test_precommit_bootstrap_parity.py::test_the_live_tree_is_in_parity_so_this_commit_would_pass_its_own_hook` | repo `CLAUDE.md` bytes == `icdev/data/claude_bootstrap/CLAUDE.md` bytes | `prebuild_bootstrap.py` was re-run and the payload `git add`ed (mfx-ci-04) |
| `tests/test_bootstrap_parity.py` (2 tests) | same pair, via `check_bootstrap_parity` | same |
| `tests/ci/test_test_gating_census.py::test_policy_is_documented_and_reachable` | `"test-gating-policy.md"` appears in CLAUDE.md | that line is in `### Development Rules`, byte-identical |
| `tests/test_sbom_revision_2026.py::test_claude_md_no_longer_claims_a_gate_it_does_not_have` | `sbom_max_age_days`, `backstop`, `supersedes_sbom_id`, `source_revision` present; one retired sentence absent | all in `### Compliance & Security Rules`, byte-identical |
| `tests/cloud/test_flx_docs.py::test_the_performance_claim_guard_reaches_the_capability_itself[CLAUDE.md]` | the phrase `"performance, cost or capacity"` is in CLAUDE.md | **this one needed a decision — see §4** |
| `tests/test_coherence_doc_command_paths.py` (2 tests) | `check_doc_command_paths` does not fail; three phantom showcase paths absent | the doc set was **extended** to follow the commands — see §5 |
| `tools/workflow/coherence_checker.py::check_karpathy_sync` | 5 canonical headings in the 10 companion files | `## Karpathy Principles` is byte-identical; companion sync re-run |
| `tools/workflow/coherence_checker.py::check_sandbox_coverage` | reads `docs/security/sandbox-coverage.md`, **not** CLAUDE.md | unaffected (the card's survey listed it defensively) |
| `tools/kanban/union_resolver.py` / `tests/kanban/test_union_derived_from.py` | `CLAUDE.md` is union-declared and its bootstrap copy `derived_from` it | neither declaration changed (kpr-watch-14) |
| `tests/test_ahx_harness_truth.py::…_expand_doc_entries` | `"CLAUDE.md"` passes through the doc-set expander | unaffected |
| `tests/test_documented_clis_have_a_bootstrap.py` | every `python tools/x.py` documented under `DOC_ROOTS` (which **includes `docs`**) has a sys.path bootstrap | the command set is unchanged — it moved from CLAUDE.md to `docs/`, both in `DOC_ROOTS` |
| `tools/mcp/context_indexer.py::ClaudeMdIndexer` | parses `##`/`###` headings into sections | `### Essential Commands` keeps its name |
| `tools/cost/waste_survey.py` | measures CLAUDE.md size / tokens per day | this is the measuring instrument, not an assertion |
| `tools/genesis/reflexes/docs.py`, `tools/review_board/{fixers/qa_fixes,reflexes/docs}.py` | advisory reflexes: route drift, broken `` `tools/…py` `` refs, size/health | report-only, no gate |

Two that look like they assert and do not: `tests/hooks/test_shared_checks.py:854`
refers to `~/.claude/CLAUDE.md` (the harness's own config, not this repo's), and
`tests/agent_runtime/test_project_context.py` writes its own `CLAUDE.md` into
`tmp_path`.

Command run for the whole enumerated set:

```bash
python -m pytest tests/ci/test_precommit_bootstrap_parity.py tests/ci/test_test_gating_census.py \
  tests/cloud/test_flx_docs.py tests/test_coherence_doc_command_paths.py \
  tests/test_sbom_revision_2026.py tests/test_bootstrap_parity.py \
  tests/kanban/test_union_derived_from.py tests/test_ahx_harness_truth.py -q
```

---

## 3. What moved, and the proof it is verbatim

The essay region was lines **86–4160** of the pre-move file: everything after the
genuine essential-commands prelude (lines 11–84) and before
`### Python Dependencies`. It was split at every `# <title> (<card-id>)` header,
giving **82 essays**.

Three header lines matched that shape and are **not** headers — they are
mid-paragraph prose that happens to end in parentheses. Each was checked by hand
and excluded:

| Line | Text | Why not a header |
|---|---|---|
| 2764 | `# …the raw-INSERT (219) and undeclared-import (210)` | `210` is a number, not a card id; inside `rem-hyg-13` |
| 3172 | `# …sibling #2159 (rmf-rail-02, 2026-09-07)` | second element is a date; inside `mfx-ci-04` |
| 3981 | `# TWO COMPLIANCE AUDIT EVENTS WERE WRITTEN AND NEVER ADMITTED (same card).` | says "same card"; belongs to `rmf-wp-01` |

Two headers carry no usable card id and were named by hand:

* `# GEPA Optimizer — … (MCP tool: gepa_optimizer)` → `gepa-optimizer.md`
  (its body cites `rem-cap-01`; the header names an MCP tool, not a card).
* `# Does an epic CLAIM this task id? … (#rem-hyg-03/04)` → `rem-hyg-03-04.md`
  (a `/` is not a filename character; the index line keeps `rem-hyg-03/04`).

### Verbatim proof

Each record was rebuilt from its `.md` back into the `#`-prefixed block it came
from and compared to the pre-move file line by line: **82 of 82 match.**

Two whitespace-only caveats, both inherent to the transform the card specifies
("the leading `#` comment markers removed"), neither losing a character of content:

1. A `#`-only line and an empty line both become a blank line; the two cannot be
   told apart afterwards. This is what stripping `# ` *means*, not a defect.
2. `rmf-cyc-01` ended on a `#`-only line, which became a trailing blank and was
   trimmed.

The commands inside each record stay in fenced `bash` blocks and are
byte-identical, **including their inline `# …` notes** — only the *index* line in
CLAUDE.md drops the note (it is worth 59 bytes a card, and the record one hop
away carries it).

Re-derive the reference set at any time:

```bash
python tools/workflow/coherence_checker.py --check doc_command_paths --json
```

Distinct `python tools/…` references before the move (old CLAUDE.md alone): 106.
After (new CLAUDE.md + the 82 records + the runbook): **111, with 0 lost.**

---

## 4. Decisions, stated rather than implied

### 4.1 The index line carries no per-card path

The card specified
`<card-id> — <title> — <command> — docs/reference/cards/<id>.md`. The trailing
path costs 3,526 bytes across 82 cards and is **fully derivable** — the id *is*
the filename stem. So the rule is stated once, in the index heading
(`the essay is docs/reference/cards/<id>.md`), and
`tests/docs/test_claude_md_index.py` asserts the bijection in both directions
plus the presence of that sentence. One hop, mechanically, without a search.

### 4.2 One `flx-twin-01` sentence was carried UP into the index

`tests/cloud/test_flx_docs.py` requires the phrase *"performance, cost or
capacity"* to appear in CLAUDE.md — the standing guard that no performance claim
may be sourced from the floci emulator twin. That is a **rule**, not a record, so
it stays in CLAUDE.md, in the index heading, under a sentence saying exactly why.
The test passes untouched. The full sentence also remains verbatim in
`docs/reference/cards/flx-twin-01.md`.

### 4.3 `## Running ICDEV Outside Claude Code` also moved — beyond the essays

**This is the one change beyond the 82 essays, and it is here because the size
criterion is otherwise unreachable.** The arithmetic:

```
  4,502   the essential-commands prelude          (must stay)
 64,562   everything after the essays             (must stay — Development Rules,
                                                   Guardrails, Security, Karpathy,
                                                   Reference, Architecture)
 ------
 69,064   floor
+12,194   the leanest honest index for 82 cards (id + title + one command)
+   600   the index heading
 ------
 81,858   > 80,000
```

The acceptance figure of 80,000 was computed against **61 essays and a
352,915-byte file**; at 82 essays and 367,462 bytes it cannot be met by moving
the essays alone.

`## Running ICDEV Outside Claude Code` (3,771 bytes) is:

* **not** in the list of sections the card requires to stay byte-identical
  (Development Rules, Guardrails, Security, Karpathy, Reference — all verified
  byte-identical, see §6);
* already duplicated by a document it names in its own last line —
  *"**Long-form reference:** docs/ops/airgap-runbook.md"*.

It moved **verbatim** into that runbook as `## 14. Headless operation outside
Claude Code`, and CLAUDE.md keeps a pointer naming the wrappers
(`tools/anvil/<name>.py`, `tools/skills/invoke.py`) so the capability is still
discoverable from CLAUDE.md. Nothing was deleted. `docs/ops/airgap-runbook.md`
was added to the documented-command gate in the same change so its commands did
not leave the gate by being relocated.

**If the operator would rather have this section back inline, restore it and
raise `max_bytes` to 87,670 — it already has the headroom.** That is the one
place in this change where a reasonable person could decide differently.

### 4.4 Nothing was kept inline by exception

The card allows keeping specific essays inline. **None were.** Every one of the
82 moved; the only thing promoted upward is the single `flx-twin-01` guard
sentence in §4.2, which is a rule.

### 4.5 A scaffolded project gets the rules, not the records

`prebuild_bootstrap.py::SOURCES` copies `CLAUDE.md` and does **not** copy
`docs/`, so a project scaffolded by `icdev init` gets an index whose
`docs/reference/cards/<id>.md` targets are not present in that project. That is
a **net improvement**: those 82 essays are ICDEV's own incident history and a
customer project previously inherited all 287 KB of them for nothing. The
scaffolded project now inherits the rules. Shipping the records into the wheel is
a separate decision and was not taken here.

---

## 5. The gate that keeps the commands under gate

`args/doc_command_gate.yaml` gained two `docs:` entries:

```yaml
  - docs/reference/cards/*.md     # the 82 records (a card added later is covered by the glob)
  - docs/ops/airgap-runbook.md    # the section moved in §4.3
```

Result: **776 distinct references checked, 0 new breakage**, 51 pre-existing
grandfathered entries (unchanged), 0 stale.

Without these entries the move would have *silently removed* ~500 commands from
`check_doc_command_paths` — the gate would have gone green by having less to
check, which is the failure mode this repo names most often.

---

## 6. The sections that had to stay byte-identical, verified

Each section was hashed before and after and compared:

```
IDENTICAL  ### Development Rules
IDENTICAL  ## Guardrails
IDENTICAL  ### RLS Bypass Annotations for cwd-Sensitive Hook Logic
IDENTICAL  ### Compliance & Security Rules
IDENTICAL  ## Security Gates (Summary)
IDENTICAL  ## Karpathy Principles — Pre-Design Engineering Gate
IDENTICAL  ## Reference Documentation
IDENTICAL  ## Continuous Improvement
IDENTICAL  ### Python Dependencies
IDENTICAL  ## Architecture
IDENTICAL  ## How to Operate
```

Sections present before and absent after: the seven `###` children of
`## Running ICDEV Outside Claude Code` only (§4.3). Sections added: the `####
Card records` index heading. **No other section changed by a byte.**

---

## 7. The budget, and why it is WARN

`args/claude_md_budget.yaml` declares `max_bytes: 87670` — the post-move 79,700
plus 10% headroom, so an ordinary rule addition does not warn on the day it
lands. Consumed by
`tools/workflow/coherence_checker.py::check_claude_md_budget`.

**WARN, never fail.** A size budget that blocks a commit is a gate people learn
to route around, and the repair — "is this prose a RULE or a RECORD?" — is a
judgement call, not a mechanism. It is registered `skip` in the autofix map for
the same reason.

**The budget may only go DOWN.** Raising it to land a commit is the
census-ceiling move CLAUDE.md already forbids for `args/ci_test_backlog.txt`,
`args/undeclared_import_census.txt` and `args/perfect_score_gate.yaml`.

Three states, and `unmeasurable` is never folded into `pass`:

| Condition | Status | Reported |
|---|---|---|
| within budget | `pass` | bytes, budget, % used |
| over budget | `warn` | bytes, % used, **and the configured sections by name with their sizes** |
| no usable `max_bytes` | `warn` | `unmeasurable`; the percentage is **None**, never `0.0` and never `100.0` |
| `CLAUDE.md` absent or unreadable | `warn` | `unmeasurable` — a missing file is not a small one |

```bash
python tools/workflow/coherence_checker.py --check claude_md_budget --json
```

On this tree: `pass`, `CLAUDE.md is 79,700 bytes … 90.9% used`.

---

## 8. Not done, and named rather than implied

* **The SessionStart hook does not yet surface the record for a card's ids.**
  `tools/hooks/session_context.py` (xrv-mem-01) is the seam: a session whose task
  names `dwr-fid-02` could have `docs/reference/cards/dwr-fid-02.md` injected.
  The card explicitly says *do not build that here*; it is named, not dropped.
  Until it exists, the cost of the move is that a session must follow the index
  line to the record.
* **The after-side `cache_creation_input_tokens` is unmeasurable until this
  lands** (§1). Re-run the probe on a session started after the merge.
* **The records are not shipped to scaffolded projects** (§4.5).
* **`docs/reference/cards/` is not in `prebuild_bootstrap.py::SOURCES`**, so the
  packaged CLAUDE.md's index points outside the wheel. Deliberate, per §4.5.
* **The one-shot splitter lived under `.tmp/`** and is not committed: `.tmp` is
  disposable by design, and a tool whose only job was to run once against a file
  that no longer exists in that shape cannot be re-run meaningfully. The durable
  artifacts are the 82 records, the index, and
  `tests/docs/test_claude_md_index.py`, which re-derives the bijection from the
  tree on every gated run.

---

## 9. Reproduce

```bash
python -m pytest tests/docs/test_claude_md_index.py -q
python tools/workflow/coherence_checker.py --check claude_md_budget --json
python tools/workflow/coherence_checker.py --check doc_command_paths --json
python -m tools.cost.waste_survey --since-days 7 --project ICDev --json
python -m pytest tests/ci/test_precommit_bootstrap_parity.py tests/test_bootstrap_parity.py \
  tests/ci/test_test_gating_census.py tests/cloud/test_flx_docs.py \
  tests/test_coherence_doc_command_paths.py tests/test_sbom_revision_2026.py \
  tests/kanban/test_union_derived_from.py tests/test_ahx_harness_truth.py -q
```
