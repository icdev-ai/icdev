# DocMod asks ONE governed seam instead of hand-querying tables (cef-di-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.doc_modernization.evidence import (
      resolve_evidence, currency_assertion, graph_citations, run_stats)
  bundle = resolve_evidence("Catalyst 6500", entity_type="hardware_model")
  hit = currency_assertion(bundle)   # entity_currency.resolve()'s exact dict shape
Toggle: `cortex.enabled` in args/docmod/docmod_config.yaml — DEFAULT OFF, and
off means the seam is NEVER consulted, so the rollback is a flag flip and not
a merge revert. TRUST rule 1 is unchanged: resolve() supplies the EVIDENCE and
DomainPack.evaluate() still derives the verdict, from TYPED fields, with no
model call anywhere (resolve passes corrective=False, so even the CRAG rewrite
does not run). Only `extraction: structured` claims are handed to a pack — a
claim read off a retrieved document's PROSE, or a pack's own verdict returning
through the fan-out, can never become a verdict.
cortex.resolve() RUNS the packs (resolver.assess), so a pack calling it inside
evaluate() recurses without bound: a thread-local guard returns None for a
re-entrant ask and the pack takes its legacy read. Same for a spent
`max_resolves_per_run` budget, an absent Cortex, or a governance refusal —
every one of them degrades to the legacy path and none can fail a sweep.
Bounds are REPORTED (`run_stats()` -> resolutions / capped), never silent.
Migrated: packs/network_hardware.py::_currency_hit (the entity-currency
lookup), packs/policy_refs.py::_kg_corroboration (the kg_nodes SELECT),
scanner.py::_enrich_evidence (governed citations on every finding written).
NOT migrated, on purpose: docmod_eol_products, docmod_nist_pubs (revision_num
compared numerically), dic_chunk_links/rag_chunks (a hash equality),
dic_documents (a timestamp comparison) — those are EXACT values a ranked
retrieval seam cannot return, and swapping them would turn a proven verdict
into an approximation.
