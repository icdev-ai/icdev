# DIC document generation asks ONE governed seam, and screens what it wrote (cef-di-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from icdev.tools.document_intelligence.docgen_evidence import (
      resolve_evidence, screen_draft)
  bundle = resolve_evidence("network transport SOP", collection_id="default")
  screen = screen_draft(drafted_prose)   # None means NOT SCREENED, not clean
Toggle: `cortex.enabled` in args/dic_docgen_config.yaml -- DEFAULT OFF, and off
means the seam is NEVER consulted, so the rollback is a flag flip and not a
merge revert. What migrated is the one retrieval line in each of
doc_generator.generate_document and .regenerate_section -- what the routes
POST /api/generate and POST /api/generate/section call. The seam hands back
real `DICSearchResult` objects, so EVERYTHING downstream of retrieval (the
evidence blocks, the verifier replay, the persisted citations, the quality
gate's `allowed_source_ids`) is untouched, and `chunk_id` is the Cortex
citation's `source_id`, so the existing `[source: chunk N]` contract holds
without touching a prompt. `GovernedCitation` SUBCLASSES the DIC `Citation`
and its `to_dict()` is a SUPERSET, so `citations_json` GAINS `source_type` /
`source_table` / `provenance_id` / `evidence_path` and loses nothing.

THE CURRENCY GUARD IS THE POINT, and it catches what nothing else can.
`verifier.verify` asks whether a claim is SUPPORTED by the retrieved
evidence -- and a 2019 runbook fully supports "configure the enclave to use
TLS 1.1". The verifier passes it, the attribution score passes it, the
confabulation detector passes it, and the document ships reintroducing a
protocol the estate spent two years removing. `screen_draft` passes the
DRAFTED PROSE back through `cortex.resolve`, whose pack assessments are
DETERMINISTIC (TRUST rule 1 -- no LLM decides what is deprecated). Measured on
the live canvas 2026-08-18, real packs, real prose: `TLS 1.1` -> superseded
(crypto_protocols, rule:crypto-tls-02, successor "TLS 1.2 or higher (prefer
TLS 1.3)") and `Catalyst 6500` -> deprecated (network_hardware,
mc_net_eol:cisco:Catalyst 6500). A flagged section is annotated with a cited
advisory, has `verified` CLEARED whatever the verifier said, and carries a
`currency_verdict` citation. `on_deprecated: abstain` drops the prose instead.
`unknown` NEVER trips it -- that means no pack RECOGNISED the entity, which is
a gap, and treating it as a finding would flag every draft on the board.
`screen_draft` returning None means the screen did NOT RUN; `citation_report.
currency.screened` is what tells that apart from "ran, found nothing", and the
two must never be read as one.

THE MEASURED CAVEAT -- the governed path is FASTER and THINNER here.
Same query ("network transport security SOP"), live canvas 2026-08-18:
  legacy DICSearchEngine  10 hits, 17.5s, chunk bodies 627-8021 chars
  cortex.resolve (cold)    3 hits, 10.3s, snippets capped at 200 chars,
                           all from dic_documents; `graph` and `kb` errored
  cortex.resolve (warm)    3 hits, 10.6s; `rag` errored too
So enabling the toggle on THIS deployment today hands the drafter ~600 chars
of evidence where the legacy chain gave ~11k. That is not a fallback case --
3 hits is thin, NOT empty, so `fallback_on_empty` (default true) does not
fire. The abandoned backends are RECORDED on every section under
`citation_report.evidence_detail.backend_errors` instead, so a thin run reads
as an infrastructure event and not as a claim about the corpus. The 200-char
cap is search_service.py's citation snippet, deliberate for the same reason as
cef-di-03: the text must BE the citation's snippet. Do NOT "fix" the timeouts
by raising the global budgets in args/cortex_config.yaml -- every Cortex
consumer shares them. The card's value here is the currency guard and the
governed provenance, NOT evidence volume; weigh that before flipping the flag.
Budget the screen too: it costs ~12s per section on this deployment, so a
6-section document pays ~75s. `max_resolves_per_run` (default 50) bounds it
and refused asks are COUNTED in `run_stats()`, never silent.

Which chain produced a section is recorded on it as
`citation_report.evidence_path`: cortex | cortex_empty_fallback | legacy.
`pack_evidence` citations are dropped at the seam. The advisory carries NO
`[source: chunk ...]` tag -- a pack's evidence ref is a synthetic key
(`entity_currency:nist`), not a retrievable chunk id, so tagging it would be a
hallucinated citation manufactured by the trust guard itself; the verdict is
cited STRUCTURALLY instead.
The module ships byte-identical in BOTH trees and they are separate module
objects with separate thread-local run state. Reach it through
doc_generator._evidence_module() (icdev first) -- in a test, patch the copy
that function returns, and patch EVERY alias of tools.cortex.api.
NOT migrated, on purpose: the Chain-of-Debate paths (ChainOrchestrator in
_cot_generate / _cod_compress -- they consume an evidence STRING and work
identically either way, still behind ICDEV_DIC_COT_ENABLED), the
"Source document content:" query-scrape fallback (it never went near
retrieval), and regenerate_section's dic_sections / dic_versions row reads --
EXACT primary-key lookups, and a ranked seam cannot return "the section
before this one".
