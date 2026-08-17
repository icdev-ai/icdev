# CUI // SP-CTI

# Entity Currency Store — and why `docmod_defacto_standards` was empty (cef-fnd-04)

## The two findings

**One.** Currency evidence existed and was scattered. Three tables, three
incompatible shapes, each readable only by the subsystem that owned it:

| Table | Rows | Shape | Knows about |
|-------|-----:|-------|-------------|
| `docmod_eol_products` | 110 (105 synced) | product + release cycle + EOL date | software release cycles |
| `mc_net_eol_data` | 101 | naming authority + match PATTERN + EOL date | network hardware models |
| `docmod_catalog_entries` | 19 | a curated STATUS WORD, usually no date | whatever the team curated |

Nothing could ask all three at once, and a fourth provider — an OS vendor feed,
an internal deprecation register, a standards body — had nowhere to write. The
software feed is a genuinely working external path, and it will never know a
hardware chassis or a protocol version; that is not a defect in the feed, it is
the reason a feed cannot be the store.

**Two.** `docmod_defacto_standards` held **0 rows**, and the usual diagnosis was
wrong. `defacto_learner.recompute()` ran on the nightly docmod sweep. It read
`ni_devices`. `ni_devices` holds 0 rows, because it is populated from a NetBox
instance or a CSV export and neither is reachable from this deployment. **The
writer ran and had nothing to learn from** — the `empty` substrate shape, whose
fix is data or an input, never a migration.

## What shipped

### 1. `entity_currency` — one row per assertion

Migration `20260817010533_entity_currency`. One row per **(source, entity,
version)** assertion: entity type, namespace, key, version, verdict,
`superseded_by`, source, `as_of`, `observed_at`, confidence, EOL/EOS dates, and a
provenance pointer back to the exact origin row.

Deliberately **not** a resolved per-entity answer. Two sources that disagree keep
two rows; `resolve()` picks a winner at READ time and hands back the losers under
`others` with `conflict: true`. Squashing disagreement at write time destroys the
one thing a caller most needs to see.

**Domain-agnostic means the columns name no domain.** `entity_type` and
`namespace` are open vocabularies supplied by the source. The only closed
vocabulary is `verdict` (`current`, `scheduled_end_of_life`, `deprecated`,
`end_of_support`, `end_of_life`, `unknown`), validated in Python and deliberately
not by a CHECK constraint — a CHECK is a second copy that drifts.

**Source-agnostic means the module names no source.** Every table, column
mapping, entity type and verdict rule lives in `args/entity_currency.yaml`.
`tools/currency/entity_currency.py` contains no table name, no column name, no
vendor, no product and no domain; a test asserts it (`test_the_module_hardcodes_
no_source_table`).

Backfilled: **230 rows** from the 110 + 101 + 19 that already existed, idempotent
on re-run (a UNIQUE index over the identity tuple, `ON CONFLICT DO UPDATE`).

### 2. Honesty properties, made structural

- **Curated evidence is `authoritative`** and wins ahead of confidence AND
  recency. A tie-break that can be overturned by bumping a prior is not
  authority. The catalog's authority, asserted in the learner's docstring, is now
  enforced by the resolution policy.
- **`confidence` is a declared prior, not a measurement.** Stated in the
  migration, in the YAML, in the module docstring and in `CLAUDE.md`, so nothing
  downstream mistakes it for a calibrated probability.
- **`as_of` (the source's clock) is kept apart from `observed_at` (ours).** A
  feed synced today can be asserting a fact it last reviewed a year ago;
  collapsing them makes stale evidence indistinguishable from fresh.
- **"Recorded and knows nothing" is written down.** A source with no currency
  signal produces a row with verdict `unknown` at a clamped confidence — a
  different answer from "no source has heard of it", which `resolve()` reports as
  `None`. Never guessed upward to `current`.
- **A failed source reports why.** Per-source isolation in `backfill()`: one
  unreadable table costs that source its rows and nothing else, and the result
  carries the error rather than a zero.

### 3. The learner's input became a declaration

`args/docmod/inventory_feeds.yaml`. `recompute()` reads whatever feeds are
declared instead of one hardcoded table.

| Feed | Kind | Evidence | Precedence | Records here |
|------|------|----------|-----------:|-------------:|
| `ni_devices` | table | `inventory` | 10 | 0 |
| `topology_nodes` | json_nodes over `topologies.graph_json` | `design` | 20 | 48 |

`ni_devices` stays **enabled** on purpose: the day a NetBox or CSV import is
connected it starts winning with no config change, and an enabled feed reporting
0 records is visible in the sweep result where a commented-out one would not be.

`docmod_defacto_standards` now holds **32 rows**.

**Evidence classes are never blended.** Migration
`20260817011242_docmod_defacto_evidence_provenance` adds `source_feed` and
`evidence_kind`. `share_pct` is computed WITHIN a feed; `get_recommended()` and
`cross_check()` answer from the best-precedence feed that has data and never from
a pool of two. 16 design topologies must not read as deployment reality — that
laundering is the exact thing this engine exists to catch, and pooling the
percentages would have committed it.

### 4. Wiring, so the store is not another declared-but-unconsumed capability

- **Refreshed** by the nightly `doc_modernization_sweep` reflex, immediately
  after the EOL syncs it reads.
- **Consumed** by `tools/doc_modernization/packs/network_hardware.py`, which
  calls `resolve()` only when the curated catalog and the hardware EOL feed both
  come back empty. It is the extension seam — a provider declared in the YAML
  starts answering there with no code change — and it can never overrule the
  catalog, which is consulted first and returns before it.
- **Declared** in `args/capability_consumption.yaml` `substrates:`, both the new
  table and `docmod_defacto_standards`, with the note that an empty
  `docmod_defacto_standards` means "check whether every feed is empty" and not
  "the writer is broken".

## Verification

```
python -m tools.currency.entity_currency --backfill    # 230 written, 0 errors
python -m tools.currency.entity_currency --stats       # per-source rows, verdict mix
python tools/awareness/capability_consumption.py --probe-substrate entity_currency
                                                       # populated, 230
python -m pytest tests/currency/test_entity_currency.py # 12 passed
python tools/ci/red_first_gate.py --gate                # discriminating; RED recorded
```

## What was NOT done, and why

`ni_devices` was left empty. Ingesting an inventory into it needs a NetBox
instance or a device CSV export, and this deployment has neither — inventing rows
to fill it would have manufactured exactly the false "deployed estate" signal the
`evidence_kind` column exists to prevent. The feed that would consume such an
import is declared, enabled and reporting 0 records, so connecting one later is a
data step and not a code change.
