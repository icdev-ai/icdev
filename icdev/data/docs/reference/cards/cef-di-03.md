# DocDrift's SSP evidence comes from ONE governed seam (cef-di-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from icdev.tools.document_intelligence.ssp_evidence import resolve_evidence
  bundle = resolve_evidence("AC-2", frameworks=["fedramp_high"])
  bundle.texts[i] is cited by bundle.citations[i]   # INDEX-ALIGNED
Toggle: `cortex.enabled` in args/dic_acoic_config.yaml — DEFAULT OFF, and off
means the seam is NEVER consulted, so the rollback is a flag flip and not a
merge revert. What migrated is acoic._retrieve_evidence, a bare
RAGRetriever.search that returned chunk TEXTS AND NOTHING ELSE: the [SOURCE-N]
tags on a drafted fragment were positional indices into a list nobody
persisted, so after the call returned no reader could say what [SOURCE-1] had
been. Now each one resolves to a source id, a table and a provenance id, and
`citation_report.sources` on the fragment records them.
NOT migrated, on purpose: map_changed_controls (the deterministic RICOAS /
NIST 800-53 crosswalk, a pure JSON lookup that never went near retrieval) and
_draft_fragment_text's cited-template fallback (the no-LLM draft). Both are
the air-gap-safe paths and both are asserted with the router armed to raise.
`fallback_on_empty` (default TRUE) catches "nothing to draft from at all" — a
governance refusal, a fan-out where every rung failed. It does NOT catch a THIN
answer, and the difference is measured: on the live canvas 2026-08-18 a WARM
cortex.resolve("CM-12") answers in 4.8s with 5 cited texts from
rag_compliance_corpus + dic_documents (the same evidence the legacy path found,
now with source ids), while the same call on a COLD process spends 10.3s and
abandons rag/dic/graph at the 10.0/10.0/8.0s budgets in args/cortex_config.yaml,
answering from `currency` alone — thin, not empty, so the flag does not fire.
The abandoned backends are RECORDED on the fragment under
`citation_report.evidence_detail.backend_errors` instead, so a thin run reads as
an infrastructure event and not as a claim about the corpus. Do NOT "fix" the
cold case by raising the global Cortex timeouts — every Cortex consumer shares
them. (`kb` errors on every resolution with `column "use_count" does not exist`
— a pre-existing Cortex backend defect, reported rather than swallowed.)
Evidence text is SHORTER on the governed path: a citation snippet is capped at
200 chars by search_service.py where a raw chunk ran 112-667, so a cited draft
is ~970 chars against ~1400. Deliberate — the text must BE the citation's
snippet, or the persisted provenance summarises what was verified rather than
being it.
Which chain produced a fragment is recorded on it as
`citation_report.evidence_path`: cortex | cortex_empty_fallback | legacy | caller.
`pack_evidence` citations are dropped at the seam — a pack's own verdict
returning through the fan-out must never become the evidence for a control.
The module ships byte-identical in BOTH trees and they are separate module
objects with separate thread-local run state. Reach it through
acoic._ssp_evidence_module() (icdev first) — never by importing a namespace
and resetting it, and in a test patch the copy that function returns.
dic_ssp_fragments held 0 rows before this change and still does — the drafting
entry point (process_regen_item) has never been invoked; all 72 regen-queue
items are still `queued`. This card migrated the evidence chain, not the
trigger.
