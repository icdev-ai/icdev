# AGX Leaderboard + Evidence-Based Routing Recommendation (agx-bench-02)

Surfaces the [agx-bench-01](../../tools/llm/architectures/benchmark.py) benchmark
results as a leaderboard and turns them into an **evidence-based routing
recommendation** — replacing intuition-tuned YAML with measured selection.

> **Sequencing:** this is the LAST measurement task in the AGX card; it grades
> everything the card built.

## What ships

| Piece | File | Role |
|-------|------|------|
| `baseline` architecture | `tools/llm/architectures/baseline.py` | A single direct `router.invoke`, no reasoning wrapper — the honest reference the leaderboard grades everything against. Measurement-only; never a routing default. |
| `build_leaderboard` | `tools/llm/architectures/leaderboard.py` | Ranks architectures per (task-family × model-family) on quality, with cost + latency alongside. Per-family — never a single blended number. Low-sample groups → `unmeasured`. |
| `recommend_defaults` | same | Per-function recommendation with inline evidence. **Recommends only; never writes config.** |
| `is_config_noop` / `check_no_degradation` | same | Regression guard (below). |
| CLI | `python tools/llm/architectures/leaderboard.py --json` / `--markdown` / `--recommend` | Reads `data/agx/benchmark_latest.json`. |

## SAFETY GOVERNOR — recommend, do not flip

This task **does not autonomously flip any platform-wide LLM architecture-selection
default.** The committed `args/llm_config.yaml` `architectures:` block stays
all-null (`default: null`, `functions: {}`, `roles: {}`) — i.e. **current behavior,
a verified no-op** (established in agx-core-03). The recommendation is produced for a
human to apply deliberately via that config; nothing here edits it.

Two tests enforce this and act as the regression guard the task requires:

- `test_shipped_llm_config_architectures_block_is_noop` — the shipped block passes
  `is_config_noop`.
- `test_shipped_config_resolves_to_current_behavior_for_real_functions` —
  `resolve_architecture()` returns `None` (current behavior) for `narrative_generation`,
  `code_review`, `requirements_generation`, `recommendation`, `chat_response`,
  `code_generation`, and the `cot_reasoner` / `cod_debater_pool` roles.

`check_no_degradation` additionally flags — for any *future* config edit — an
architecture routed to a benchmarked function that the measurements show performs
below baseline on any model family. For the shipped (all-null) config it returns `[]`:
nothing is routed, so nothing can regress.

## Current evidence state → recommendation

As committed, **no live-model benchmark run is bundled** (a fresh CI worktree is
air-gapped with no `.env`/providers, and CUI-safety keeps model runs local). The
benchmark therefore reports `status: "unmeasured"`, and the evidence-based
recommendation for **every function is: KEEP CURRENT BEHAVIOR** (all-null config).
This is the correct, honest output given the evidence — the leaderboard refuses to
assert any improvement it did not measure.

### How the recommendation becomes actionable

Run the bench live where ≥2 model families are reachable (including a local Ollama
model, per the LLM-agnostic constraint):

```bash
# populate data/agx/benchmark_latest.json
python tools/llm/architectures/benchmark.py --run --json

# render the leaderboard + recommendations from it
python tools/llm/architectures/leaderboard.py --markdown
python tools/llm/architectures/leaderboard.py --recommend --json
```

A recommendation flips to `recommend_change` **only** when a single architecture
beats `baseline` by ≥ `min_margin` composite on **all** measured model families
(rejecting frontier-only wins) and stays within `max_cost_ratio` × baseline cost.
Otherwise the decision is `keep_current` (reported, never buried) or
`insufficient_evidence`. Applying a change is then a deliberate edit to
`args/llm_config.yaml` `architectures.functions` — see agx-core-03.

## Honesty guarantees

- **Per-model-family, always.** The leaderboard axis keeps model family; a win that
  only holds on a frontier model can never masquerade as a portable default.
- **"Nothing beat baseline" is a first-class result.** `keep_current` is surfaced
  with its evidence, not hidden.
- **Insufficient samples are `unmeasured`, not ranked on noise.** `min_samples`
  gates both the leaderboard and the recommendation.

## Deferred: dashboard page (documented follow-up)

The task allows shipping a CLI/JSON leaderboard plus a documented follow-up when the
full dashboard page gate cannot be satisfied cleanly. It cannot here: a fresh
worktree has no authenticated dashboard DB/session, `tools/dashboard/app.py` is off
limits this run (concurrent sessions), and there is no measured data to render yet.

**Follow-up (when a live benchmark run exists and the dashboard is available):** add
an "Architecture Leaderboard" panel *where ICDEV already shows evaluation results*
(the eval/skill-lab surface) rather than a brand-new standalone page. If a new page is
warranted instead, it MUST ship all 8 components of the CLAUDE.md page-completeness
gate — template + `icdev/` mirror + route + backing module + constants + migration
(if it persists to a table) + nav link + full IQE integration (adapter, `/api/iqe-query`
route, widget include, `_CANVAS_MAP` + `PATH_CANVAS` entries, ≥3 seed queries) — plus
Playwright V&V with a screenshot under `playwright/screenshots/`. Until then the
CLI/JSON leaderboard is the interface, and `build_leaderboard` already returns the
render-ready rows a page would consume.
