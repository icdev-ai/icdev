# DocDrift SHOWS the verdict — and shows an unknown as a finding (cef-ui-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from icdev.tools.document_intelligence.docdrift_evidence import (
      attach_resolutions, resolve_finding, resolve_findings, run_stats)
  page = attach_resolutions(acoic.list_drift_events())   # one view per finding
  view = resolve_finding("TLS 1.1", advisory=True)       # governed cortex.resolve
Toggle: `cortex.enabled` in args/dic_docdrift_config.yaml — DEFAULT **ON**,
and that is the one place this seam differs from cef-di-01/03/04/05. Those
four MIGRATED a working retrieval path, so `false` restores a chain that
already existed. This one has no legacy path: nothing on
/document-intelligence/docdrift had ever shown a verdict, a citation or a
reason, so shipping it off would declare a capability nothing consumes.
Off does NOT hide the panel — it makes every finding read `not_resolved`.

THREE AXES, AND KEEPING THEM APART IS THE ENTIRE FEATURE.
 * `state` reads the DETERMINISTIC verdict and NOTHING else:
   current | deprecated | superseded | unknown | not_resolved | refused.
   `finding_state(verdict, verdict_source)` has no `backend_errors`
   parameter, so an outage has no route by which to become a verdict.
 * `evidence_health` reads `backend_errors` and NOTHING else:
   ok | degraded | failed | unmeasured. It is a SEPARATE field because the
   live board proves the two move independently — measured 2026-08-18,
   `TLS 1.1` resolves **superseded** (crypto_protocols, rule:crypto-tls-02,
   successor "TLS 1.2 or higher (prefer TLS 1.3)") while FOUR of five
   backends time out. The verdict came from a pack reading a rulebook, so it
   is exactly as good as it looks, and the sweep is exactly as degraded as it
   looks. One field cannot say both.
 * `advisory` carries the `sme` rung's OPINION, last, indented, dashed and
   muted under a header that says it is not evidence. FOUR states, and the
   first three are all an empty list: `not_consulted` (nobody asked — `sme`
   is deliberately absent from `resolve.backends`, so a default deployment's
   advisory list is structurally always empty and rendering it as "no
   concerns" would be a fabrication), `unavailable` (asked, the rung ERRORED
   — what THIS deployment returns today, `generative_intelligence` monthly
   budget spent at 420,375/400,000), `no_opinion`, `opinion`.

`not_resolved` and `unmeasured` are the two values that had to exist. Without
them a finding nobody has checked renders identically to one checked and
found current — the defect the card was written for. The page states both in
words ("Nobody has asked yet. This is not a clean bill of health").
An `unknown` NEVER renders as a shrug: `unknown_reasons()` carries
no_pack_matched / no_evidence / backends_failed / packs_failed through with
labels, because those are four different fixes. Live proof that this is not
theoretical: `TLS 1.3` returns unknown with TEN corpus citations and no pack
assessment — a naive page would draw "10 citations, no problems found".

Resolutions are ON DEMAND and PERSISTED (`dic_docdrift_resolutions`,
migration 20260819020723): one costs 10-12s against five backends and the
board holds 72 findings, so resolving on render would take minutes.
`max_resolves_per_batch` (default 8) bounds "resolve all" and the deferred
entities come back NAMED in `skipped` — a truncated sweep reporting only its
successes reads as full coverage.
`advisory.enabled` is a DEFAULT (off), overridable per request; set
`advisory.allow_request_override: false` on a deployment that must guarantee
no model call reaches this page whatever a request body says.
API: POST /document-intelligence/api/docdrift/resolve{,-batch}
UI:  /document-intelligence/docdrift
