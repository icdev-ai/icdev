# CUI // SP-CTI

# Explorer renders cross-source conflicts and gaps (cef-ui-02)

**Surface:** `GET /document-intelligence/explorer` (`tools/document_intelligence/blueprint.py::explorer`)
**Store:** `tools/cortex/finding_store.py` (mirrored to `icdev/tools/cortex/`)
**Migration:** `20260819030255_cortex_entity_findings_store`
**API:** `GET /document-intelligence/api/explorer/cortex-findings`
**Toggle:** `resolve.persist_findings` in `args/cortex_config.yaml` (default **true**)
**Tests:** `tests/cortex/test_finding_store.py`, `tests/cortex/test_explorer_conflict_render.py`
(gated via `args/ci_test_files/core.d/cef-ui-02.txt`)

## The defect

cef-rsv-02 made a cross-source disagreement **computable** and cef-rsv-03 gave
each finding **citations**. Both then travelled on the `CortexResolution` the
caller already held — and nowhere else.

So the only reader of a finding was whatever code happened to trigger the
resolution: a docmod sweep, a DocDrift draft screen, an MCP verb. A conflict is
a finding a **human adjudicates**; a gap is a **data-quality ticket**. Neither
is actionable if it dies with the request that produced it, and nothing in the
platform could show a person that two of its own sources disagreed.

The Explorer already existed as the KG "buried bodies" surface for orphans and
contradictions. A contradiction between two of the platform's **own evidence
sources** is the same kind of finding, so that is where it belongs.

## What was built

### 1. A durable projection, not an audit table

`record_findings(result, ctx)` runs in `resolver.resolve` immediately after
`register_resolution`, and upserts one `cortex_entity_findings` row per
`(tenant, entity, finding)`.

Upsert, not append: a conflict observed on forty resolutions is **one**
disagreement, and forty rows would render as forty findings. `seen_count`
carries the recurrence instead. A conflict whose *claimed values* change is a
**new** finding — what a human adjudicated is no longer what is on the table.

### 2. It stores no winner

There is no `resolved_value`, no `winning_side`, no `consensus` and no score in
`FINDING_COLUMNS`. `TestNoSilentWinner` asserts that against the **column list**
and against the round-tripped row, not against one hand-built payload, so a
field that merely happened to be unset in a fixture cannot ship.

Authority is **recorded on the sides** (`authoritative`, `confidence`, `as_of`,
`extraction`) and never applied. `entity_currency.resolve()` resolves authority
at read time to answer *"what is the best available answer"*; that is a
different question from *"do my sources agree"*, and answering the second with
the first deletes the finding.

### 3. Both claims, side by side, with their provenance

Each conflict renders one panel per side carrying that side's own **status**,
its source's own word (`raw_status`), **source**, **as-of date** (labelled *the
source's clock, not ours*), backends, the `table#row_id` it came from, its
extraction lane, its declared-prior confidence and its snippet. The panel
carries a standing note that the disagreement is unresolved *by design*.

The as-of dates are what make the motivating case legible: a 2019 runbook
asserting `current` against a 2026 catalog asserting `deprecated` reads as
staleness, not as an unexplained contradiction.

A side that names an authority and no row id is listed separately with its
reason — never dropped, and never lent a neighbour's citation.

### 4. A browsable, filterable gap list

Filter by entity (substring), reason and backend. Filtering goes through
`GET /document-intelligence/api/explorer/cortex-findings`, so the filter and
the stored payloads cannot disagree about what a reason or a backend means.
The filter *vocabulary* is derived from the rows on screen (`filter_options`),
never from the constants, so a chip can never offer a value matching nothing.
The server-rendered list is present on first paint, so the page is browsable
before any JS runs.

`reason` and `backend` live inside JSON payloads and are matched **in Python**,
per the repository rule against SQLite-dialect JSON SQL at a runtime call site.

### 5. An outage is not a statement about the corpus

A gap's `backends_failed` is its own column and never becomes a reason. On
screen it renders as a red `outage:` badge, visually distinct from the blue
reason badges — a partial outage is *context* for a gap, not the gap's cause,
and the two have different fixes. Filtering by reason cannot match a failed
backend.

### 6. Four causes of an empty list, and only one is "no conflicts"

`finding_stats` reports which, structurally:

| state | meaning | counts |
|---|---|---|
| `disabled` | `persist_findings` is off; nothing was recorded | `None` |
| `unmeasured` | recording on, no resolution recorded here yet | `None` |
| `clean` | resolutions ran and every claim was compatible | `0` |
| `findings` | rows exist | `n` |

`conflicts`/`gaps` are `None` — never `0` — for the two states that are not
measurements, so the template physically cannot print a reassuring zero for a
surface that never looked. The badge and the explanatory panel change with the
state, and only `clean` says anything about the data.

The same shape is returned when the store is unreachable or unmigrated: an
outage degrades to UNMEASURED, never to "your sources agree".

### 7. Actionable on DocDrift

Every conflict and gap links to `/document-intelligence/docdrift?entity=<label>`
and, where `docmod_findings` already holds rows for that entity, shows the open
count. This page says the sources disagree; DocDrift is the queue a redline is
drafted from. Counting the handoff is what keeps the disagreement actionable
rather than terminal. The count is best-effort — its absence never blocks the
render.

## Bounds and non-goals

* **The projection can never fail a resolution.** Every write path is
  exception-isolated and reports `status`/`detail`; the record lands on
  `result.metadata["finding_store"]` so a caller can see whether it wrote.
* **Nothing triggers a resolution from this page.** The Explorer renders what
  `cortex.resolve()` has already produced from its existing callers. A
  browse surface that fanned out across backends on page load would put a
  multi-second, multi-backend retrieval on every render.
* **Not added to `APPEND_ONLY_TABLES`.** This is a projection that upserts by
  design; the immutable record of a resolution is the `source_citation_registry`
  row cef-rsv-03 already writes, and `audit_trail` is untouched.
* **`resolver._gaps` and `entity_resolution`'s gaps keep their own
  vocabularies.** The store carries both shapes without merging them and
  derives a missing `entity_key` through the resolver's own `entity_ident`, so
  the two are browsable in one list without either one's reasons being
  rewritten.

## Measured

On the live canvas 2026-08-19, against PostgreSQL: a resolution carrying one
conflict and one gap writes two rows plus one denominator row; three
re-observations of the same disagreement leave **one** row at `seen_count = 3`;
filtering by `reason=no_evidence`, `backend=graph` and `entity=nexus` each
return the expected subset, and `reason=backends_failed` matches nothing.

Eight real `cortex.resolve()` calls through the governed facade
(`tools/cortex/api.py`) exercised the hook end to end at ~5–11s each. Four were
CLEAN — `Catalyst 6500` → `deprecated`, `TLS 1.1` → `superseded`, `Nexus 7000`
→ `deprecated`, `Windows Server 2012 R2` → `superseded`, each with zero
conflicts and zero gaps — and each still bumped the denominator, which is the
whole reason the denominator exists. Two produced real gaps
(`Zenith Fabric Controller 9000`, `OSPFv2`, both `no_pack_matched`, both
carrying genuine `backends_failed` outage badges from rungs that died during the
fan-out).

**No real conflict has been produced on this deployment.** The corpus has not
yet yielded two sources making incompatible claims about one entity, so
`conflicts` reads a MEASURED `0` — the `clean` half of the state table, not the
`unmeasured` half. The TLS 1.1 conflict in
`playwright/screenshots/cef-ui-02-explorer-conflicts-and-gaps.png` is a **seeded
reproduction** of the motivating case documented in cef-rsv-02 (the curated
`entity_currency` row against a 2019 runbook chunk), written directly to the
store to verify the rendering and **deleted from the shared database
immediately afterwards** — leaving only the rows real resolutions produced.

### Playwright verification

25 assertions against a second dashboard on port 5071 (5060/5061 are on
Chromium's blocked-port list), all passing: two claim panels rendered and both
visible; both statuses, both sources, both as-of dates and both `table#row_id`
provenance strings on screen; the extraction lane shown per side; the
"unresolved by design" note present; no winner vocabulary anywhere in the body;
the state badge reading `FINDINGS`; three gaps browsable; the outage badge
distinct from the reason badges; filter-by-reason narrowing 3 → 2 with the count
label following; entity search narrowing to exactly `OSPFv2`; backend filter
returning results; and four DocDrift handoff links.
