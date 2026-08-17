# Entity Currency Store

Classification: CUI // SP-CTI

One domain-agnostic answer to "is this thing still current, who says so, and
when did they last look?" (cef-fnd-04). Currency evidence used to live in three
domain-narrow tables — a software-release feed, a hardware EOL feed and the
curated catalog — each readable only by the subsystem that owned it, none able
to describe an entity the others had never heard of, and no place at all for a
fourth provider to write.

`tools/currency/entity_currency.py` names no table, no column, no vendor, no
product and no domain: every source is declared in `args/entity_currency.yaml`,
so adding a provider is a config entry. Two sources that disagree keep two rows;
`resolve()` picks a winner at read time under the declared policy and hands back
the losers next to it. Curated sources are `authoritative` and win outright —
ahead of confidence, ahead of recency — because a tie-break that can be
overturned by bumping a prior is not authority.

`confidence` is a DECLARED PRIOR from the YAML, not a measurement.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Entity Currency Store | tools/currency/entity_currency.py | Source-agnostic currency assertions in `entity_currency`: `upsert()`, `query()`, `resolve()` (authority → confidence → as_of, reporting disagreement), `stats()`, `backfill()` from every declared source; `derive_verdict_from_dates()` | `--backfill` / `--stats` / `--resolve <entity>` | dict / JSON |
| Currency Package API | tools/currency/__init__.py | Re-exports VERDICTS, CurrencyAssertion, backfill, resolve, stats, upsert, normalize_key | import | Python API |
| Source declarations | args/entity_currency.yaml | Per-source table, column mapping, entity_type (literal or data-driven), kind, authoritative flag, declared confidence, verdict strategy (`dates` \| `value_map`), and the read-time resolution order | YAML | config |

## Wiring

- **Refreshed by** the nightly `doc_modernization_sweep` Genesis reflex, right
  after the EOL syncs it reads — a substrate nothing refreshes goes stale
  silently.
- **Consumed by** `tools/doc_modernization/packs/network_hardware.py`, which
  calls `resolve()` only when the curated catalog and the hardware EOL feed both
  come back empty. It is the seam for a provider neither of them knows about,
  and it can never overrule the catalog.
- **Declared** in `args/capability_consumption.yaml` `substrates:` so
  `capability_consumption.py --probe-substrate entity_currency` and
  `coherence_checker.py --check substrate_liveness` can see an empty one.
