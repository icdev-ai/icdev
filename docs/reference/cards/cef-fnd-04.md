# Is this entity still current? ONE store, any source, any domain (cef-fnd-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.currency.entity_currency --backfill --json
python -m tools.currency.entity_currency --stats --json
python -m tools.currency.entity_currency --resolve "<entity>" --entity-type hardware_model
```

One row per (source, entity, version) ASSERTION in `entity_currency`. Sources
are declared in args/entity_currency.yaml — tools/currency/ names no table,
column, vendor, product or domain, so a fourth provider is a config entry.
Disagreement is PRESERVED: two sources that disagree keep two rows, resolve()
picks a winner at READ time and returns the losers under `others` with
conflict:true. Curated sources are `authoritative` and win ahead of confidence
AND recency — a tie-break that a bumped prior can overturn is not authority.
`confidence` is a DECLARED PRIOR, not a measurement. `as_of` (the source's
clock) is kept apart from `observed_at` (ours) so stale evidence stays
distinguishable from fresh. Refreshed by the doc_modernization_sweep reflex.
The de-facto learner's input is now a DECLARATION too
(args/docmod/inventory_feeds.yaml): docmod_defacto_standards held 0 rows for
months not because the writer never ran but because its only input, ni_devices,
held 0 rows. Each learned row records source_feed + evidence_kind and share_pct
is a share WITHIN one feed — an observed estate beats a drawing of one, and no
quantity of drawings adds up to an observation.
