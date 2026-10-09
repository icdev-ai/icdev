# CUI // SP-CTI

# cch-obs-02 — Caching that stops working goes red, not quiet

## The defect

`cch-tel-01` made the per-call cache counts **exist**. Nothing watched them
**change**.

That gap is the whole failure mode this card exists to close, because the two
states are rendered identically: a provider that was serving cached tokens and
stops looks exactly like a provider that was never enabled. Both are zero. That
is how Azure served cached tokens and discarded the count for its entire life
with nothing going red (#1725), and how the savings ledger sat in an `UNLOGGED`
table where a restart zeroed a cumulative metric with no record it had ever been
anything else.

An aggregate cannot tell them apart. Only a **transition** can.

## What was built

| | |
|---|---|
| `tools/cache_savings/regression.py` | The detector. Read-only; files nothing. |
| `tools/genesis/reflexes/cache_regression_reflex.py` | Turns a finding into a kanban card. |
| `args/cache_regression.yaml` | Thresholds, mechanism map, instrumentation floor. |
| `tests/test_cache_regression.py` | 28 tests, gated in `args/ci_test_files/core.txt` in this PR. |

No new alert channel. A genesis reflex writing a kanban card is the established
way findings reach a human here, and it is what this reuses.

### Three rungs

| rung | fires when |
|---|---|
| `stopped` | a provider reported cache reads across the whole baseline window and **exactly zero** across the recent one, with real traffic in both |
| `collapsed` | its cache-read share fell by at least `collapse_drop_ratio` relative, from a baseline that was meaningfully non-zero |
| `never_cached` | a provider whose declared mechanism **bills** cached tokens has made enough instrumented calls to have had a real chance and has never reported one |

The two comparative rungs never consult the mechanism declaration. A provider
that *did* report cache reads was caching, whatever any config file claims — the
evidence outranks the declaration. Only `never_cached`, which is a claim about
**absence**, needs the declaration, and it is the only rung that can be wrong
about a provider nobody has classified.

## The thresholds are measured, not chosen

Per CLAUDE.md: a signal that fires on normal variation is muted within a week,
and a threshold nobody measured is a guess with a number on it.

It could not be fitted to cache data directly. Measured on the live board
2026-08-16 — `ai_telemetry`, 13,073 rows, 8 providers, 2026-06-11 to 2026-08-16
— **every cache-token value is 0**, because the columns landed the same day. So
the fit used a structurally identical quantity out of the *same* ledger: a
bounded per-provider token share, `output/(output+input)`, swept over every
historical 7d-recent / 21d-baseline window pair, counting how often the collapse
rule would have fired on traffic nobody considers regressed.

| relative drop | fires | evaluations | rate |
|---:|---:|---:|---:|
| ≥ 30% | 24 | 82 | 29.27% |
| ≥ 50% | 7 | 82 | 8.54% |
| ≥ 60% | 4 | 79 | 5.06% |
| **≥ 70%** | **0** | **79** | **0.00%** ← armed here |
| ≥ 80% | 0 | 79 | 0.00% |

`0.5` would have fired roughly weekly on this deployment's ordinary traffic —
that is the muted-within-a-week failure with a number on it. `0.7` is the
**smallest** swept value with a measured zero false-fire rate, i.e. the most
sensitive setting the data supports rather than the safest one available.

The `stopped` rung was swept the same way — recent window falling to exactly
zero — and fired **0/79, 0.00%**, at both `min_calls` 5 and 20.

`min_calls_per_window` is 20: raising it from 5 cost almost nothing on this
ledger (82 → 79 evaluable windows), so small-sample safety was essentially free.
`min_baseline_share: 0.05` is an unmeasured guard, and deliberately one that can
only ever **suppress** a finding — it cannot raise the fire rate above the
ceiling measured above.

### Measured fire rate on the live board

```
providers_evaluated : 3
findings            : 0
cards_filed         : 0
verdicts            : {'mechanism_no_billing': 3}
instrumented_since  : 2026-08-16T12:38:21+00:00 (config)
elapsed             : 0.21s
```

Zero cards. All three providers with traffic in the window (`ollama`,
`ollama_cloud`, `cli`) are correctly named `mechanism_no_billing` rather than
reported as 0% hit rate — their caching is local or absent and there is no billed
cache read to miss.

## The zeros that are not observations

A zero in this column has four meanings, and the detector keeps all four apart by
**name**. Collapsing any two is the defect, not a simplification.

* **Backfilled.** Rows written before the cache columns existed hold `0` because
  the cch-tel-01 migration back-filled them, not because a provider was asked and
  answered zero. Every one of the live board's 13,073 rows is in that state —
  counting them would file a `never_cached` finding against every provider on the
  first run, which is precisely how a reflex earns its own suppression. The
  `never_cached` rung therefore counts only rows at or after `instrumented_since`
  and reports `pre_instrumentation_unknown` when that instant cannot be
  established. The comparative rungs need no floor: both require a **non-zero
  baseline**, which a backfilled window cannot produce.
* **No billing.** Ollama reuses its KV cache server-side and bills nothing back;
  a permanent zero there is correct. It is `mechanism_no_billing`, never a
  finding, and never `$0.00` presented as failure.
* **Undeclared.** A provider absent from the mechanism map is
  `mechanism_unknown`. Guessing about a vendor is how a detector loses trust.
* **No history.** An empty ledger, one without the cch-tel-01 columns, or a
  missing table reports `status: unmeasurable` with a reason — never a clean bill
  and never a wall of findings. Same rule `check_capability_liveness` and
  `capability_consumption` already apply to a fresh worktree.

### Where the instrumentation floor comes from

`instrumented_since` resolves config → the cch-tel-01 migration's `applied_at` in
`schema_migrations` → unknown. The explicit config value exists because that
migration's row is **absent** from `schema_migrations` on the live PostgreSQL
board — the columns were applied there without a ledger entry — so auto-detection
alone would leave the rung permanently mute on the one deployment that matters.

### The share's denominator

`cache_read / (cache_read + input)`. Providers disagree on whether `input_tokens`
already includes cached reads (Anthropic excludes them, OpenAI includes them), so
this is a provider-comparable proxy rather than an exact hit rate. It does not
weaken a regression claim: both windows of a comparison are the same provider
under the same formula. Exact per-provider effectiveness reporting is cch-obs-01.

## Acceptance — both directions, or it is not a detector

Run end to end through the real detector and the real reflex against an isolated
`ai_telemetry`; only the board write is intercepted, so the card below is exactly
what would be seeded.

```
DIRECTION 1 - a provider stops caching          (anthropic 0.40 share -> 0.0)
  verdicts    : {'stopped': 1, 'mechanism_no_billing': 1}
  findings    : 1
  cards filed : 1 ['cache-regr-b75e3289bd']
  -> "anthropic stopped reporting cached tokens"  [fix/high]

DIRECTION 2 - normal variation                  (anthropic 0.40 share -> 0.30)
  verdicts    : {'healthy': 1, 'mechanism_no_billing': 1}
  findings    : 0
  cards filed : 0

CONTROL - 500 backfilled pre-instrumentation zeros on a BILLING provider
  verdicts    : {'insufficient_instrumented_calls': 1}
  findings    : 0
  cards filed : 0
```

In every run a high-traffic `ollama` with a permanent zero sits alongside as a
control and stays silent.

`tests/test_cache_regression.py` holds both directions, and the threshold
boundary is pinned on **both** sides (69% relative drop silent, 72.5% fires) —
a test that only proves the fire case passes just as happily against a detector
that fires on everything.

## Dedupe, and what it costs

Card ids are deterministic in `(rung, provider)`. A uuid would refile the same
finding every cycle; title-matching would collapse two genuinely distinct
findings into one. The accepted cost is the same one `ungated_test_drift` takes:
once a card for a given (rung, provider) has been filed, a later recurrence files
nothing. The run result always reports every finding, filed or not, so a
recurrence stays visible in the reflex log.

## Registration

`tools/genesis/daemon.py` `REFLEX_NAMES` and `args/genesis_config.yaml`
(`every 6h`, green tier, `enabled: true`), both mirrored into `icdev/`. Six hours
rather than hourly because the windows are 7d/21d — a shorter cadence re-asks a
question whose answer cannot have moved.

`test_the_reflex_is_actually_dispatched_not_merely_written` asserts both
registrations, because a reflex nobody dispatches is this platform's signature
bug wearing new clothes: enabled, importable, catalogued, never run, nothing red.

## Found, not fixed

* **`openai` is invisible to the `never_cached` rung on the live board.** It has
  6 lifetime calls, all from 2026-06-27, all predating the instrumentation, so it
  appears in no window and no instrumented total. That is correct — the rung has
  nothing to answer with — but it means the rung has not yet been exercised
  against a real billing provider anywhere, only in tests. It arms itself the
  first time 50 instrumented calls reach one.
* The mechanism map in `args/cache_regression.yaml` is an **interim** restatement
  of what the four populating adapters do. `cch-cap-01` makes each provider
  declare its own mechanism, and that block should be read from the declaration
  rather than restated once it lands.
