# CUI // SP-CTI

# cef-di-01 — DocMod's evidence lookups on `cortex.resolve()`

**Status:** shipped, toggle **OFF** by default
**Surface:** `tools/doc_modernization/` (scanner + packs) — the first of six
**Toggle:** `cortex.enabled` in `args/docmod/docmod_config.yaml`

---

## The problem

`tools/doc_modernization/scanner.py` and the packs under
`tools/doc_modernization/packs/` read evidence by hand: `docmod_eol_products`,
`docmod_defacto_standards`, `docmod_nist_pubs`, `kg_nodes`, `mc_net_eol_data`,
`ni_devices` and the `dic_*` tables, over an RLS-free canvas connection because
those tables carry no `tenant_id`.

Every pack that wants to reach past its own domain has to learn how — which is
why only two of the seven ever do, and neither the same way:
`network_hardware` calls `tools.currency.entity_currency.resolve` directly,
`policy_refs` hand-writes a `SELECT id FROM kg_nodes ... LIMIT 1`. Neither
lookup produces an audit row, a registered evidence set, or reaches RAG, DIC,
the KB or an SME.

## What shipped

One seam — `tools/doc_modernization/evidence.py` — over the governed
`cortex.resolve(entity)` verb, and three consumers:

| Consumer | Was | Is (toggle on) |
|---|---|---|
| `packs/network_hardware.py::_currency_hit` | `entity_currency.resolve(label, ...)` | the seam's `currency` lane, same dict shape |
| `packs/policy_refs.py::_kg_corroboration` | `SELECT id FROM kg_nodes ... LIMIT 1` | the seam's `graph` lane |
| `scanner.py::_enrich_evidence` | — | the governed resolution's citations attached to every finding written |

The scanner consumer is the one that covers all seven packs at once: a pack
keeps whatever structured read its verdict needs, and the finding it produced
additionally carries the citations `cortex.resolve` gathered across the currency
store, RAG, DIC, the knowledge graph and the KB.

## TRUST rule 1 is unchanged, and three things keep it that way

`base_pack`'s rule is that `evaluate()` derives its verdict from deterministic
evidence, never from an LLM. `resolve()` supplies the EVIDENCE; the pack still
decides. Concretely:

1. **Typed fields only.** The seam returns `verdict` / `eol_date` / `eos_date` /
   `superseded_by` off the `currency` backend's `metadata`, resolved into
   `entity_resolution.claims`. No prose is parsed and no answer is generated.
   `currency_assertion()` deliberately returns `entity_currency.resolve`'s
   *exact* dict shape, so the pack's verdict derivation is not one line
   different from what it was.
2. **Only `extraction: structured` claims reach a pack.** `entity_resolution`
   also produces `text_pattern` claims — read off a retrieved DOCUMENT's prose —
   and `pack` claims, which are the packs' own verdicts coming back around.
   Either one reaching `evaluate()` would make a document's sentence, or a
   pack's earlier answer, the authority behind a deterministic verdict. The
   filter lives at the seam, not in each pack.
3. **No model call anywhere.** `resolve` passes `corrective=False`, so even the
   CRAG rewrite — the one LLM inside retrieval — does not run, and ADVISORY
   (`sme`) hits are dropped before they reach citations.
   `tests/docmod/test_cortex_evidence_seam.py` arms `LLMRouter.__init__` to
   raise and asserts every pack still evaluates to the same verdict.

## The circularity, and the guard

`cortex.resolve` gets its verdict by running `DomainPack.evaluate()` —
`resolver.assess()` loads the very packs that call this seam. A pack calling
`resolve` inside `evaluate` recurses without bound.

`evidence._STATE` is a **thread-local** flag held for the duration of the
outbound call, so a re-entrant ask returns `None` and the pack takes its legacy
read. Thread-local rather than global because the search fan-out runs backends
in a worker pool, and a global flag would suppress an unrelated concurrent
sweep's evidence.

## `None` is the legacy path

Every caller reads `None` as "the seam said nothing — do what you did before".
Five distinct causes, each logged with its own reason rather than merged:

| Cause | Result |
|---|---|
| `cortex.enabled: false` | `None` — the seam is not consulted at all |
| re-entrant (inside a live resolution) | `None` |
| `max_resolves_per_run` spent | `None`, and `run_stats()["capped"]` increments |
| Cortex not importable | `None` |
| governance / citation block | a bundle carrying `blocked` and no evidence |

No path can fail a document sweep.

## What was deliberately NOT migrated

The structured reads a verdict is *derived* from stay where they are:
`docmod_eol_products` (product+cycle rows), `docmod_nist_pubs` (`revision_num`
compared numerically), `dic_chunk_links`/`rag_chunks` (a hash equality),
`dic_documents` (a timestamp comparison). Those are exact values a ranked
retrieval seam cannot return — "is 4 < 5" is not a ranking question. Replacing
them would turn a proven verdict into an approximation.

## Before/after on the live corpus

`.tmp/compare_scan.py` (read-only: no scan run, no finding, no scan-state row)
ran the real pack pipeline over the live canvas twice, once per toggle state.

| | toggle off | toggle on |
|---|---|---|
| documents with an approved version | 18 | 18 |
| distinct findings | **20** | **20** |
| finding set (doc, pack, entity, type, verdict, severity, confidence) | — | **identical** — 0 added, 0 removed, 0 changed |
| evidence entries | 22 | **165** |
| findings that gained evidence | — | 20 of 20 |
| outbound resolutions | 0 | 12 (memoised; 0 refused by the cap) |
| pack errors | 0 | 0 |

The migration is behaviour-preserving on the finding set and additive on the
evidence, which is exactly the shape intended: `_enrich_evidence` runs *after*
the verdict is fixed and touches `evidence` only, so `dedupe_key`, severity,
confidence and finding type cannot move.

Two degradations observed with the toggle on, both **pre-existing and outside
this card**:

* the `rag`, `dic` and `graph` rungs time out (10s / 10s / 8s) on this
  deployment because the configured embedding provider answers 401, so the
  evidence that arrives comes from the `currency` rung — which is the lane the
  packs consume, so the migration delivers on the path it depends on;
* the `kb` rung errors with `column "use_count" does not exist` —
  `tools/mcp/knowledge_server.py` orders by a column the live
  `knowledge_patterns` schema does not have. The seam reports it under
  `backend_errors` rather than returning a silent empty, which is the intended
  behaviour.

## Rollback

Set `cortex.enabled: false` in `args/docmod/docmod_config.yaml`. The seam is
then never consulted, both migrated packs run their original query, the scanner
attaches nothing, and no outbound resolution happens. That is asserted by
`test_network_pack_uses_the_store_directly_when_the_toggle_is_off`,
`test_policy_pack_runs_the_kg_select_when_the_toggle_is_off` and
`test_seam_is_not_consulted_when_the_toggle_is_off`.

`enrich_findings` is a second, narrower switch: it takes the scanner half down
without disabling the pack-level lookups, because the two have different blast
radii — one costs a resolution per distinct entity, the other writes into every
`evidence_json` the corpus holds.

## Files

* `tools/doc_modernization/evidence.py` (new) + `icdev/` mirror
* `tools/doc_modernization/scanner.py` — `_reset_evidence_run`,
  `_enrich_findings_enabled`, `_enrich_evidence`, `findings_enriched` counter
* `tools/doc_modernization/packs/network_hardware.py` — `_currency_hit`
* `tools/doc_modernization/packs/policy_refs.py` — `_kg_corroboration`
* `args/docmod/docmod_config.yaml` — the `cortex:` block
* `tests/docmod/test_cortex_evidence_seam.py` + `args/ci_test_files/core.d/cef-di-01.txt`
