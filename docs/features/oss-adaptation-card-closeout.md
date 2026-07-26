# CUI // SP-CTI

# OSS Adaptation Card — close-out & disposition record (oss-xcut-01)

Governance close-out for the OSS-adaptation card, which studied RAGFlow,
Crawl4AI, browser-use and STRIX and adapted their **goals** into ICDEV without
adopting their stacks. Source analysis:
[`docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md`](../spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md).

Every upstream is registered in `coherence_checker._ATTRIBUTION_REGISTRY` as a
concept-only, clean-room adoption. None is GPL/AGPL. No upstream code, model
weights, or runtime dependency was taken.

---

## What shipped

| Task | What | Upstream idea |
|---|---|---|
| `oss-meas-01` | benchmark the 5 OFF toggles; withdraw the void RAPTOR verdict | RAGFlow (measure before adopting) |
| `oss-filter-01/02/03` | two-pass content filter, retrofit, SSRF egress gate | Crawl4AI `fit_markdown` |
| `oss-cite-01` | web citation type + fetch provenance | — (TRUST prerequisite) |
| `oss-chunk-01/02/03` | template chunking, position breadcrumbs, acceptance | RAGFlow structural chunking |
| `oss-table-01/02` | pdfplumber table extraction + dependency-cliff fix | RAGFlow DeepDoc (goal only) |
| `oss-browse-01..04` | agent browser, scope, four seams, page V&V | browser-use page representation |
| `oss-poc-01` | reproduce-or-drop for dynamic findings | STRIX PoC discipline |
| `oss-redteam-01/02` | scope-locked app red team | STRIX self-test |
| `oss-hitl-01` | HITL chunk merge/split/re-chunk/re-embed | RAGFlow visibility/intervention |
| `oss-fix-01/02/03` | sandbox_execute handler, phantom docs, review_loop | truthfulness cleanups |

## The rejections — as much the deliverable as the adoptions

Recorded so the next person evaluating these projects does not redo the analysis.

### Python Playwright / a chromium download (from browser-use)
**Rejected.** ICDEV already has a vendored-Selenium driver (`driver_manager.py`,
msedge/chromedriver, no runtime downloads) that survives an air-gap. Adding
Playwright would introduce a chromium download and a second browser stack for no
capability the existing driver lacks. The `@playwright/test` setup in the repo is
npm-based E2E tooling for our own dashboard, not an agent path. What browser-use
was worth adopting is the *page representation*, not the driver.

### Elasticsearch / Infinity (from RAGFlow)
**Rejected.** ICDEV's retrieval is pgvector-primary with a SQLite fallback, both
already shipped and measured. RAGFlow's ES/Infinity backend is a heavy operational
dependency that buys nothing over pgvector at this corpus scale, and would break
the air-gap and SQLite-fallback stories the platform depends on.

### DeepDoc / VLM layout weights (from RAGFlow)
**Rejected.** DeepDoc recovers tables with a deep-learning layout model — model
weights, a GPU, and a runtime download. `oss-table-01` gets most of the value from
pdfplumber's ruling-line detection with none of that, which is the only version
that runs air-gapped. The *goal* (real table structure, not `extract_text()`
soup) was adopted; the *stack* was not.

### RAPTOR (already in-tree, re-measured)
**Kept OFF, but its DROP verdict was withdrawn.** RAPTOR shipped earlier and was
recorded as a measured regression. `oss-meas-01-d3` found that verdict was
produced on a golden set with four queries of headroom in 33 — an instrument that
could not have detected an improvement. Re-measured on the v2 set it shows
+0.0208 recall, and stays OFF on *cost* (p95 +392 ms plus a corpus-wide
summarisation build), not on a void quality number. This is the RAGFlow-has-it,
we-measured-it-here discipline working as intended.

### STRIX's Docker sandbox + Caido + nuclei (from STRIX)
**Rejected.** `oss-redteam-01/02` adopted STRIX's *discipline* — a finding needs a
discriminating reproduction, and a red team is only defensible scope-locked to
owned targets. It did **not** vendor STRIX's Docker sandbox image, the Caido proxy,
or nuclei. The scope-lock (loopback default, refuse-outright, written
authorization) is the whole control; a vendored scanner-in-a-box would be a larger
attack surface than the thing it tests, and undefensible in a public repo.

### A general web crawler (from Crawl4AI)
**Rejected.** `oss-filter-01` took Crawl4AI's `fit_markdown` two-pass idea and
nothing else. A general crawler is an unbounded egress surface; ICDEV fetches
specific URLs a user or canvas supplies, through the central hardened client
(`tools/http/fetch_extract.py`) behind the SSRF egress gate (`oss-filter-03`). The
extraction improvement was worth adopting; the crawler was not.

## Governance state at close-out

- **Attribution:** ragflow, crawl4ai, browser-use, strix registered
  (`_ATTRIBUTION_REGISTRY`); `check_attribution_claims` passes.
- **Sandbox coverage:** `tools/http/page_extract.py` recorded in
  [`docs/security/sandbox-coverage.md`](../security/sandbox-coverage.md) (Gap 37);
  the agent browser at Gaps 36/38; `fetch_extract` at Gap 39.
- **Manifests & docs:** each new module has a manifest-shard row and, where it
  has a CLI, a `docs/reference/commands.md` entry.
- **Mirrors:** every `tools/` module is mirrored to `icdev/`.

## One decision left open for a human

`tools/qdc/gate_checker.py` ships `dast_enabled: False`, emitting `dast_missing`
as a **warning**. `oss-redteam-02` recommends keeping it a warning until the red
team's live observer is wired and a CI target with recorded authorization exists —
blocking on a scanner that cannot yet run would fail every build. The decision is
recorded, not taken; revisit when the observer lands.
