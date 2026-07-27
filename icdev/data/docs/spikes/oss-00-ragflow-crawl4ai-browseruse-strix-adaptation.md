# CUI // SP-CTI

# OSS-00 — RAGFlow / Crawl4AI / browser-use / STRIX adaptation analysis

**Date:** 2026-07-25
**Sources:**
- RAGFlow — https://github.com/infiniflow/ragflow (Apache-2.0)
- Crawl4AI — https://github.com/unclecode/crawl4ai (Apache-2.0)
- browser-use — https://github.com/browser-use/browser-use (MIT)
- STRIX — https://github.com/usestrix/strix (Apache-2.0)

All four are permissively licensed, so none is blocked by
`coherence_checker.py::check_attribution_claims` (which fails the gate on GPL/AGPL
upstreams). All four would need an entry in `_ATTRIBUTION_REGISTRY`
(`tools/workflow/coherence_checker.py:1629`) if any concept is cited in code.

**Headline verdict: reject all four runtimes; adopt one load-bearing idea from each.**
Every one of these projects ships a deployment model ICDEV has already
deliberately rejected (Docker-required, Playwright/Chromium, Elasticsearch,
downloaded model weights), and three of the four substantially duplicate
subsystems we already have. The adaptable material is much narrower than the
projects' surface area suggests — but it is real:

| Project | The one idea worth taking |
|---|---|
| Crawl4AI | **`fit_markdown`** — density-pruning + BM25 relevance filtering instead of tag-strip + positional truncation. |
| RAGFlow | **Chunking is a per-document-type decision a human can see and correct** — not one global window. |
| browser-use | **Index the interactive elements** so a model can act on a page at all. |
| STRIX | **A finding without a reproduction is not a finding.** |

The rest is either already built here (often *disabled* — see A0), or a
dependency we should not take.

**Method:** every ICDEV claim below was verified against the tree at
`C:\ai\icdev` on 2026-07-25 (file:line where cited); upstream claims come from the
projects' own READMEs/docs, fetched the same day.

---

## 1. Priority ranking (the actual recommendation)

| # | Adaptation | From | Size | Why first |
|---|---|---|---|---|
| 0 | **Re-measure the retrieval toggles we already ship OFF** | (none — internal) | XS | `rag.rerank`, `reflective_rerank`, `adaptive_routing`, `binary_prefilter` are all built and **disabled by default**. Cross-encoder reranking is RAGFlow's "fused re-ranking" and we already have two providers. Cheapest possible win, and it establishes the baseline every item below is measured against. |
| 1 | **`fit_markdown` two-pass HTML content filter** | Crawl4AI | S | Deterministic, stdlib-only, improves *every* LLM call over web content, cuts tokens. Replaces regex-strip + blind truncation in ≥3 live call sites. |
| 1b | **Make a fetched page citeable** (`web` citation type + fetch provenance) | — | S | Rides with #1; without it nothing fetched can satisfy the TRUST citation invariants. |
| 2 | **Template chunking** + **section/page columns on `rag_chunks`** | RAGFlow | M | Retrieval precision; activates a config hook that already exists but is dead. Deterministic, air-gap safe, no new deps. |
| 2b | **Real table extraction via pdfplumber** | RAGFlow (DeepDoc's goal, not its stack) | S–M | Recovers most of DeepDoc's value with a library already in the repo — no torch, no Paddle, no downloaded weights. |
| 3 | **Indexed-element browser primitive for agents** | browser-use | M | Unlocks agent-driven V&V (a known, repeatedly-hit pain) *and* is a prerequisite for #4/#6. |
| 4 | **Reproduce-or-drop finding discipline (PoC validation)** | STRIX | S | Attacks a documented ICDEV pathology: findings that don't survive verification. |
| 5 | **HITL chunk inspect/repair UI in DIC** | RAGFlow | M | DIC's TRUST story is incomplete without it — chunks are currently read-only. |
| 6 | **Dynamic self-test ("app red team") against our own dashboard** | STRIX | L | Highest security value, but only sane after #3 and #4 exist. |

**Defer:** declarative ingestion pipeline (RAGFlow), CSS/XPath selector schemas
(Crawl4AI) — do these when a specific recurring source actually hurts.
**Do not build:** a general web crawler (Crawl4AI) unless a real crawl
requirement appears. See §6.

---

## 2. What ICDEV already has (do NOT rebuild)

| Upstream capability | ICDEV equivalent | Assessment |
|---|---|---|
| RAGFlow: hybrid vector + keyword retrieval | `tools/rag/retriever.py` (embed → vector top-50 → RRF fusion → time decay → rerank → top-5), `vector_store_factory.py` (pgvector/sqlite/chroma/faiss), `rank_bm25` in `requirements.txt:27` | Built — but BM25 is a **re-scorer over the vector top-50**, not an index. See gap R4. |
| RAGFlow: reranking | `tools/rag/reranker.py`, `reranker_provider.py` (BGE via Ollama / LLM), `reflective_reranker.py` | Built — **`rag.rerank.enabled: false` by default.** |
| RAGFlow: RAPTOR | `tools/rag/raptor.py` (693 ln), merge in `retriever.py:206` | Built, kept OFF. ~~Recorded as a *regression* (0.0 recall@5, 0.0 MRR, −0.0005 nDCG@5); do not resurrect on RAGFlow's say-so.~~ **That regression is withdrawn (oss-meas-01-d3):** it was measured on a golden set with 4 queries of headroom in 33. Re-measured on the v2 48-query set, same corpus, `raptor` ON gives **+0.0208 recall@5 / +0.0093 MRR**. Still OFF, but now *undecided* rather than settled — needs a latency number. |
| RAGFlow: GraphRAG | `tools/knowledge_graph/graph_rag.py` (1874 ln, 5 scoring profiles), `tools/rag/rag_to_kg_ingester.py`, DIC KG bridge | **Already built, and well past RAGFlow.** |
| RAGFlow: agentic workflows / self-correction | `corrective_rag.py`, `crag_evaluator.py` (1119 ln), `adaptive_router.py`, `query_classifier.py`, `evaluator.py`, `quality_feedback_loop.py`, and `tools/mcp/rag_server.py`'s 14 tools incl. `rag_decompose` + `rag_evaluate(self_eval_retrieve)` | **Already built, ahead of RAGFlow.** Several toggles default OFF. |
| RAGFlow: traceable citations | `tools/quality/citation_grounding.py` (`citation_gate`, `classify_confidence`), `content_grounding.py`, `cove_guard.py`, + `dic_chunk_links.chunk_hash` evidence baseline (`ingest_orchestrator.py:214`) | Already built; ours is stronger (hash-at-link-time drift detection). |
| RAGFlow: multi-format parsing | `tools/document_intelligence/extractors.py` (1426 ln, 30+ extensions, 4-pass PDF chain, OCR confidence scoring, extraction-quality gates) | Built — but layout is stubbed and tables are flattened. See gaps R5/R6. |
| RAGFlow: LLM enrichment at ingest ("transformer") | `ingest_orchestrator.py`: `_ai_ocr_cleanup`, `_ai_document_summary`, `_ai_metadata_extraction`, `_ai_extract_identifiers`, `_ai_classify_into_taxonomy`, `_ground_token` | **Already built**, including grounding of extracted values against source text. |
| RAGFlow: per-chunk context enrichment | `tools/rag/contextual_retrieval.py` + `reindex_contextual.py` — Anthropic contextual-retrieval prefix, embedding-only, provenance-stamped | **Built and shipped ON**, with measured gains (+0.0151 recall@5, +0.0202 MRR). |
| RAGFlow: ingest progress visibility | DIC drag-and-drop ingest UI + SSE progress (`blueprint.py:1246 /api/ingest/<job_id>/stream`, `_emit(stage, detail, pct)`) | Already built. |
| Crawl4AI: retry/backoff, proxy, mTLS, timeouts | `tools/http/client.py::get_session/request` + `args/http_client.yaml` | Already built — and is the mandated chokepoint. |
| Crawl4AI: LLM extraction from a page | `tools/chat_router/url_analyzer.py`, `document_intelligence/extractors.py:1180 extract_url`, research/creative/news scanners | Built, but see gap C1. |
| Crawl4AI: SSRF-safe fetching | **`tools/doc_modernization/link_check.py:206 egress_guard`** — HTTPS-only, suffix allow/denylist (deny wins), `getaddrinfo` resolve-then-reject-any-private/loopback/link-local/metadata IP, paired `_NoRedirect` opener so every hop is re-validated | **Already built, and good.** Default `enabled: false`, and **no other fetcher calls it** — see gap C3. |
| Crawl4AI: CSS-selector extraction schema | `tools/sharepoint/browser_fallback.py:60 fetch_classic_page(url, selectors)` | Exists, but SharePoint-scoped, default-disabled, positional zip, no XPath/nesting. |
| Crawl4AI: per-source politeness delay from config | `tools/research/source_scanner.py:231 _rate_delay` (`args/research_config.yaml`), DDG circuit breaker at `tools/pulse/engine/researcher.py:252` | Partially built, per-scanner and ad hoc. |
| (no upstream analogue) prompt-injection scan of fetched bytes | `tools/security/injection_scanner.py:139 scan_text`, called at `tools/genesis/reflexes/research.py:375` | Built, but used by **one** reflex — see gap C4. |
| browser-use: browser driver lifecycle | `tools/browser/driver_manager.py::get_driver` — Selenium, **vendored** msedgedriver/chromedriver, no runtime downloads | Already built and air-gap correct. |
| browser-use: agent loop, tool registry, subagents, planning | `tools/agent_toolkit/` (`_fs`, `_shell`, `_planning`, `_subagent`, `_composer.create_agent`), `run_agent_loop`, ACE | **Already built.** Do not import a second agent framework. |
| browser-use: vision judgment of a page | MCP `validate_screenshot` (`gap_handlers.py:596`, vision-LLM assertion), `run_e2e_tests` | Built — the *read* half. The *act* half is missing entirely (gap B1). |
| STRIX: agent graph / specialist collaboration | `invoke_council`, `invoke_chain_of_debate`, ACE roles, workflow orchestration | Already built. |
| STRIX: active attack execution against a model | `tools/security/llm_red_team.py` — catalog in `args/`, detectors, OWASP LLM Top-10 grouping, `--gate` non-zero exit | **Already built** — and it is the exact template for adaptation #6. |
| STRIX: SAST / deps / secrets / container / CVE | `tools/security/sast_runner.py`, `dependency_auditor.py`, `secret_detector.py`, `container_scanner.py`, `osv_scanner.py`, `args/security_gates.yaml` | Already built. |
| STRIX: sandboxed command execution | `tools/security/sandbox_executor.py` (803 ln, network off by default, 30 s/512 MB defaults, audited), `agent_toolkit/_shell.py(sandboxed=True)`, ACE `tool_runner.py` 5-guard allowlist, SAG `agent_runtime/safety.py` approval modes + fail-closed mutation, `docs/security/sandbox-coverage.md` ledger | **Already built, and more thoroughly than STRIX.** But see gap S3 — the `sandbox_execute` MCP tool has no handler. |
| STRIX: CI gate with non-zero exit | `args/security_gates.yaml` (~59 KB) + `--gate` flags; CI `security` job runs bandit at high/high | Already built (statically). |
| STRIX: DAST findings store + gate scorer | `tools/security_canvas/dast_runtime_gates.py` (+ `zig_dast_scans`, `zig_dast_gate_results`) | Shell exists — **but it self-certifies pass. See gap S1.** |
| STRIX: live-target request harness | `tools/testing/route_smoke.py`, `api_contract_tester.py`, `acceptance_validator.py`, `a11y_sweep.py` | Built, non-security-oriented. Reuse for A6. |
| STRIX: adversary emulation | `tools/security_canvas/caldera_adapter.py` | Read-only metadata fetch; launches nothing. |

**Governing prior art — this analysis restates existing policy, it does not invent it:**
- `docs/features/phase-rce-rag-context-engineering.md` states the thesis explicitly:
  *"The next gains come from evolving the existing pipeline … not from swapping
  backends."* Every RAGFlow recommendation below is pipeline evolution.
- `docs/reference/adrs.md:822` and `docs/spikes/agx-00-agentic-architectures-adaptation.md`
  already reject the LangChain/LangGraph stack on "adopt patterns, not the stack" grounds.
- `docs/reference/agx-degradation-contract.md` bans `import anthropic|openai|langchain*`
  in architecture code — all inference through `LLMRouter`. RAGFlow-as-a-service
  would violate this by construction.
- The only prior mention of RAGFlow in the repo is one competitive-scan bullet
  (`docs/features/dic-discovery.md:100-115`), framed as *validation* of DIC's
  no-LLM/no-vector grounded default. There has been **no feature-gap comparison or
  adopt/reject decision until this doc.**

**Rejected wholesale:**
- RAGFlow's runtime — Docker ≥24, Compose, **Elasticsearch or Infinity**, ≥16 GB RAM, ≥4 cores, Python ≥3.13, gVisor for its code executor. ICDEV is a pip-installable, air-gap-first platform on PostgreSQL/SQLite. Also rejected: **DeepDoc/VLM parsing** — pulls torch/Paddle and downloads weights on first use, which `extractors.py` already documents as not air-gap safe and degrades away from.
- Crawl4AI's runtime — requires Playwright + `playwright install chromium`. Playwright *does* exist in this repo (`@playwright/test` in `package.json`, `playwright.config.ts`, the Playwright MCP) but strictly as **npm-based E2E tooling against our own dashboard**; the Python browser path is **Selenium with vendored drivers** (53 call sites, `tools/browser/README.md`, air-gap vendoring via `tools/airgap/driver_vendor.py`). Adding a *Python* Playwright + chromium download to the air-gap vendoring surface buys no capability we can't get from `driver_manager`, and cuts against the standing preference for pure-Python/offline tooling over npm.
- browser-use's runtime — its own agent loop, its own model (`ChatBrowserUse`), cloud CAPTCHA service, Playwright. Chrome memory cost at scale is a stated upstream limitation.
- STRIX's runtime — auto-pulls a sandbox Docker image, bundles Caido + nuclei, and installs via `curl | bash`. Not air-gap installable, and it is an autonomous attacker we would have to govern from the outside rather than from within our own gates.

---

## 3. The real gaps

### Gap C1 — HTML→text is a regex strip, then blind truncation *(highest value)*
The entire HTML→text implementation is
`tools/document_intelligence/extractors.py:487 _strip_html` — five `re.sub` calls
(drop `<script>`/`<style>`, drop all tags, `&nbsp;`, collapse whitespace).
`extract_url` (same file, :1180) is fetch + `<title>` regex + `_strip_html` with a
2 MB read cap. `tools/chat_router/url_analyzer.py` reimplements the same strip and
then hard-truncates to `_MAX_CONTENT = 7000` characters before the LLM call.
Consequences:

- Nav bars, cookie banners, footers, and link farms consume the token budget.
- Truncation is **positional, not relevance-based** — the answer is often past
  char 7000, and the model never sees it.
- No structure survives: headings, lists, and tables all flatten.

There is **no** markdown generation anywhere (no `html2text`, `markdownify`), and
no readability/boilerplate removal (no `trafilatura`, `readability-lxml`) —
`extractors.py:1306` has a *stale docstring* claiming a trafilatura fallback that
the code never imports. `bs4` is not a declared dependency; its two call sites
(`trading/news/rss_ingestor.py:87`, `workflow_hitl/document_ingestion.py:253`) are
soft imports with fallbacks.

Crawl4AI's answer is a two-pass filter producing `fit_markdown`:
`PruningContentFilter` scores blocks on text/link density and drops boilerplate,
then `BM25ContentFilter` keeps blocks relevant to the query. We already ship
`rank_bm25`. Nothing here needs an LLM, a browser, or a new dependency.

**This gap is already acknowledged internally:** `tools/kanban/seed_dic_kanban.py:163,496`
seeds tasks proposing a DIC `html_provider.py` built on `html2text` — and that
file does not exist. The adaptation below is the same work, scoped better (shared
module, no new dep).

### Gap C2 — no crawling, and no politeness primitives if we ever add it
Verified by scan: **zero** outbound `robots.txt` handling (no `RobotFileParser`;
the `robots` hits are `<meta name="robots">` in our own templates), **zero**
sitemap parsing, **zero** HTTP-level cache (no ETag/`If-Modified-Since`/body
store — the three "cache" mechanisms found are DB TTL/dedupe, e.g.
`pulse_research_cache`), and no frontier/visited-set/depth concept.

The one near-miss: `tools/genesis/reflexes/research.py` (L326–363,
`feed_type == "html_scrape"`) extracts links from a page — via LLM
(`_extract_links_nlp`) or an `href=` regex — records them as signals, and
**never fetches them**. So we have depth-1 link *listing*, not crawling.

Concurrency is fully synchronous: no `aiohttp`, no runtime `httpx`, serial
`for url in urls:` with blocking `time.sleep`. `ThreadPoolExecutor` is used in
~40 modules but never for HTTP; `tools/llm/rate_gate.py` gates **LLM** calls only.

That is a defensible design choice — but "crawl the vendor's docs site" is not a
capability today, and if someone adds it ad hoc we get an impolite, uncached,
unbounded crawler with no per-domain state and no `Retry-After` honoring.

### Gap C3 — one good egress gate, used by one feature *(governance; corrected)*
ICDEV **does** have a proper SSRF-safe fetch gate:
`tools/doc_modernization/link_check.py:206 egress_guard` — HTTPS-only, suffix
allow/denylist with deny-wins, `getaddrinfo` then reject if *any* answer is
private/loopback/link-local/metadata (169.254.169.254), paired with a
`_NoRedirect` opener so each redirect hop is re-validated. It is exactly the
primitive a fetcher should use.

**Nothing else calls it.** `scan_web`, `research/source_scanner.py`,
`osint/osint_ingestor.py`, `pulse/engine/researcher.py`,
`creative/competitor_discoverer.py`, `extract_url`, and
`genesis/reflexes/research.py` all reach the network with no host validation, no
scheme check, and no metadata-IP protection. It also ships `enabled: false` with
empty allow/denylist (`args/docmod/docmod_config.yaml:70-77`).

The two MCP-visible "egress" tools are not runtime request gates:
`tools/security/egress_policy_manager.py` generates **K8s NetworkPolicy YAML**
(and its `orchestrator`/`infrastructure` presets allow `*:443`), which has no
effect on a `python tools/...` run or the Flask process;
`tools/registry/egress_monitor.py` is **post-hoc, self-reported** child-app
telemetry.

Volume of the bypass: **104** modules under `tools/` use `urllib.request` *and*
reference a non-local `http(s)` URL, and **46** import `requests` directly, versus
**38** importing `tools.http.client`. Air-gap detection is reimplemented at least
four times across two different env vars (`ICDEV_AIRGAP` vs
`ICDEV_ENVIRONMENT=air-gapped`), and in three scanners "air-gap enforcement" is
merely `requests` failing to import.

### Gap C4 — fetched bytes are largely untrusted-but-unscanned, and unciteable
- `tools/security/injection_scanner.py:139 scan_text` is called on fetched content
  by exactly **one** caller (`genesis/reflexes/research.py:375`, critical findings
  block ingestion). Every other fetcher feeds unscanned third-party bytes toward
  an LLM. Worse, `_extract_links_nlp` passes raw HTML to the model with
  `skip_injection_scan=True`.
- `source_citation_registry`'s `citation_type` CHECK has no `web`/`url`/`crawl`
  value, so **a fetched page cannot be registered as a first-class citation**.
  Per-signal provenance is just `url` + `content_hash` + a metadata JSON blob — no
  fetch timestamp, HTTP status, ETag, redirect chain, or raw-body archive.
- There is no URL→RAG path at all: `tools/rag/ingestion_manager.py::ingest_source`
  is **table-driven** via `source_registry.py`; nothing ingests a live fetch. And
  no KG ingestion of `research_signals`/`innovation_signals` (zero references in
  `tools/kg/`, `tools/knowledge/`).

### Gap R1 — chunking is size-based only, and the template hook is dead config
`tools/rag/chunker.py` (251 ln) is one strategy: token estimate `len/4`, short
content → single chunk, long → sliding window with 10% overlap snapped to the
nearest sentence boundary (`_find_sentence_boundary`, ±200 chars), adaptive size
targeting ~70 chunks/doc clamped to `[150, 2000]`. DIC reuses it directly
(`ingest_orchestrator.py:53`). No recursive/structural splitter, no
markdown/header splitter, no semantic (embedding-boundary) splitter.

**A per-doc-type hook already exists and is dead:** 19 entries in
`tools/rag/source_registry.py` carry a `"chunking"` key (`canvas_graph`,
`canvas_assessment`), but it is read at exactly one place (`source_registry.py:822`,
a listing filter). `ingestion_manager.py` never consults it — everything funnels
through the single `chunk_content()`. So the config surface for template chunking
is already declared; only dispatch and the templates are missing.

RAGFlow's differentiator is exactly this — chunk templates (General, Q&A, table,
paper, book, laws, presentation, picture, email, resume, one) selected per file,
with the reasoning visible.

ICDEV's high-value document types are more structured than RAGFlow's, not less:
NIST/OSCAL control catalogs, STIG checklists, RFP/SOW sections (L/M),
CDRLs, contracts with numbered clauses, SOPs/runbooks with numbered steps.
Sliding-window chunking actively destroys `AC-2(3)`-style control boundaries and
splits STIG rules mid-check.

### Gap R2 — chunks carry no positional grounding at the schema level
RAGFlow 0.21's "long-context RAG" attaches chapter/TOC information to each chunk
so a retrieved fragment knows where it came from, and retrieval can expand to the
enclosing section.

In ICDEV, `rag_chunks` (DDL at `tools/rag/sqlite_vector_store.py:362`) has
**no page, no bbox, no section/heading, and no doc_id column**. Page and section
exist only in the DIC-side join table `dic_chunk_links` — so **every non-DIC
ingestion path loses them entirely**, and a chunk reading "shall be documented in
the SSP" is positionally and semantically orphaned.

`tools/rag/contextual_retrieval.py` is shipped ON and *does* help, but it is the
LLM-generated prefix approach (one call per chunk, embedding-only). A deterministic
`doc → section → subsection` breadcrumb persisted as real columns is the cheap
complement, not a duplicate — and unlike the LLM prefix it is also usable for
filtering, citation display, and section expansion at retrieval time.

### Gap R3 — no human intervention in chunking
RAGFlow's stated core value is "visibility and explainability — view the chunking
results and intervene where necessary." In ICDEV, chunks surface **read-only**
(`document_intelligence/doc_detail.html:144,414`, provenance display only). There is
no merge/split/re-chunk/re-embed path. DIC *does* have a rich HITL surface — but it
is **section/version** review (`blueprint.py:455 /review`: assign, revise, approve,
reject, locks, annotations, presence), one level up the hierarchy. For a canvas whose
entire premise is grounded citations, an operator who can see a bad chunk but not fix
it is a dead end.

### Gap R4 — BM25 is a re-scorer, not an index
`retriever.py::_compute_bm25_scores` runs `BM25Okapi` over **only the ~50 candidates
vector search already returned**. A chunk that matches lexically but is missed by
the embedding is unreachable — which is the exact failure mode hybrid retrieval is
supposed to eliminate, and it hits hardest on the identifier-heavy queries we care
about most (`AC-2(3)`, a CVE id, a CDRL number). True lexical recall exists only on
the pgvector path (`pg_vector_store.py` `tsvector` + SQL-level RRF), so the
SQLite/air-gap deployment is the weaker one. RAGFlow's "multiple recall methods with
fused re-ranking" is genuinely ahead here.

### Gap R5 — layout is stubbed and tables are flattened *(the real DeepDoc gap)*
- `extractors.py` ships the mode plumbing (`LAYOUT_MODE_AWARE`, `_probe_layout_libs`,
  `layout_mode()`) but both backing libraries are commented out of
  `requirements.txt:48-56` as not air-gap safe. **Runtime is therefore permanently
  `flat-ocr`** — no region, column, or table segmentation, ever.
- There is **no table object model anywhere**: no cells, bboxes, or rowspans; no
  markdown/HTML table reconstruction. DOCX tables get dumped under a
  `--- Tables ---` heading with tab-joined rows; XLSX rows flatten to `a | b | c`;
  PDF tables are whatever `extract_text()` happens to emit. Notably
  **`pdfplumber.extract_tables()` is never called in the DIC path**, even though
  pdfplumber is already used with word-coordinate precision elsewhere in-repo
  (`tools/network/pdf_import.py`, `tools/govcon/solicitation_parser.py` for CLIN tables).

That last point matters: the highest-value part of DeepDoc is table fidelity, and we
can get a large fraction of it from a library we already use, with no torch, no
Paddle, and no downloaded weights.

### Gap R6 — fragmentation and an optional-dependency cliff
- **Two independent PDF chains**: `tools/rag/pdf_provider.py` (Anthropic → Google →
  LLaVA → pypdf) and `extractors.py::_extract_pdf` (pymupdf → pdfplumber → pypdf →
  OCR), with no shared abstraction. Plus ≥5 separate document-parsing entry points
  (`requirements/document_extractor.py`, `govcon/rfi_document_parser.py`,
  `govcon/solicitation_parser.py`, `finetune/doc_extractor.py`, `genesis/reflexes/docs.py`).
- **Two `validate_citations` implementations** (`tools/quality/citation_grounding.py`
  and `retriever.py:241`).
- A clean `pip install -r requirements.txt` yields pypdf + python-docx + python-pptx
  + rank_bm25 + numpy. pymupdf, pdfplumber, **openpyxl**, easyocr, pytesseract,
  markitdown, chromadb, and faiss are all optional imports **absent from
  requirements.txt** — so on a clean install XLSX extraction, image OCR, and the two
  best PDF passes all silently degrade. "Silently" is the problem, not "degrade".

### Gap B1 — no agent can use a browser
`get_driver()` has exactly 8 consumers: the driver manager itself, its `__init__`,
the air-gap vendoring tool, one scheduling script, `sharepoint/browser_fallback.py`,
and three `tools/testing/e2e_*` modules. Everything browser-shaped in ICDEV is a
**hand-written Selenium script**. `tools/agent_toolkit/` gives agents filesystem,
shell, planning, and subagent spawning — **no browser**.

Checked all three agent execution surfaces — none exposes navigate/click/type:
ACE agent mode declares exactly 12 tools (`tools/ace/agent_tools.py:50 _SCHEMAS`:
read/write/patch/list/search/grep files, post/read result, run_tool, done,
spawn_agent, parallel_agents); the canonical loop
(`icdev/tools/llm/agent_loop.py`) greps clean for
`navigate|click|screenshot|selenium|playwright|webdriver`; and the standalone agent
runtime's bundles in `args/agent_toolsets.yaml` contain nothing browser-shaped.

What *does* exist is the **assertion half**: MCP `validate_screenshot`
(`tools/mcp/gap_handlers.py:596`) + `tools/testing/screenshot_validator.py` let an
agent make a vision-LLM claim about an image, and `run_e2e_tests` can trigger
Playwright runs. So an agent can *look* and *judge* — it cannot *act*. There is no
`click`, no `type`, and no machine-readable representation of what is clickable
(`grep set-of-marks|accessibility tree|interactive element|dom.tree` → one unrelated hit).

The one place an LLM *does* drive a browser today is
`tools/testing/e2e_runner.py:548 _execute_via_claude()`, which shells out to the
external `claude` CLI **with `--dangerously-skip-permissions`** and a prompt telling
it to navigate a markdown test spec via the Playwright MCP (gated on
`ANTHROPIC_API_KEY`, capped at 120 s). That is an external subprocess with its
guardrails disabled standing in for a capability we don't have — which is a second
reason to build the primitive properly in-process, under our own audit and scope
controls.

browser-use's load-bearing idea is not the agent loop (we have one); it is the
**page representation**: interactive elements are extracted and assigned stable
integer indexes, so the model acts via `click(14)` rather than by inventing a CSS
selector. Plus `use_vision`/`vision_detail_level` for screenshots,
`include_attributes` to control DOM verbosity, and `sensitive_data` so
credentials never enter the prompt.

Why this matters here specifically: our own retro notes say visual bugs need
screenshot+DOM V&V, and the dashboard-page completeness gate has 8 mandatory
components (nav reachability, IQE widget presence) that are checked by grep today.
Agent-driven verification is the missing V&V tier — and it's cheaper than
maintaining 50+ bespoke e2e scripts.

### Gap S1 — security testing is static assertion; nothing exercises the running app
`grep -rl "import requests|import httpx|urllib.request" tools/security/` returns
**zero files**. Every one of the 48 modules in `tools/security/` operates on files,
config, and DB rows.

- `atlas_red_team.py` — its own docstring: "Static checks only — no actual LLM
  invocations. Verifies defensive tooling and configuration exist."
- `llm_red_team.py` — genuinely active, but against a **model**, not the app.
- `nuclei`/`scan-dast`/`scan-zap`/Burp appear only as **node types in
  `tools/pipeline/constants.py`** — i.e. we *generate CI YAML that would run DAST*,
  we never run it.
- `tools/security_canvas/caldera_adapter.py` is a **read-only** metadata fetch from
  an external Caldera instance; it launches no operations.
  `attack_path_twin.py` enumerates paths over a *modeled* graph and sends nothing.
- Fuzzing exists but is **CLI-argument fuzzing only** (`tools/testing/fuzz_cli.py`) —
  no network, protocol, or grammar fuzzing.
- Zero hits for proxy interception (mitmproxy/Caido), zero for sqlmap, no
  request/response replay, no auth-flow/session/IDOR/priv-esc testing.

**⚠️ Worse than absent — `tools/security_canvas/dast_runtime_gates.py` is a DAST
gate that certifies itself.** `run_dast_scan(application, target_url="", findings=None)`
**accepts `target_url` and never uses it**. All ten OWASP checks default to `True`
("baseline-clean app") unless a caller passes a `findings` dict, and
`run_runtime_check` likewise defaults WAF/RASP/TLS/rate-limit/secrets to pass.
`deploy_dast_gates()` calls it with no findings, scores 100%, and writes ZIG
activity `zig-act-p2-21` as **"complete"**. `evaluate_gate` blocks below a 0.85
combined score — unreachable, since an empty scan scores 1.0. Related:
`tools/qdc/gate_checker.py` carries `dast_enabled: False` and emits `dast_missing`
as a mere *warning*.

This should be treated as a defect independent of anything in this document: a gate
that returns `pass` when no scan ran is worse than no gate, because it produces
compliance evidence for work that did not happen. **Fixed in PR #790** — fail-closed
semantics: no observation → `unknown`, never `pass`; `zig_dast_scans` records only
observed checks; p2-21 reports `in_progress` while no scanner is wired; and the cATO
signal gives no credit for an evidence-free gate. The shell that remains — findings
tables plus gate scorer — is the natural landing place for adaptation A6.

Meanwhile the recurring real defects in this codebase are precisely the dynamic
kind: an authz endpoint failing open to admin, an ABAC deny-case that matched
everything, RLS classification read-down. Static checks did not catch those.

### Gap S2 — findings are reported without being reproduced
STRIX's discipline is that a finding ships with a working PoC or it isn't a finding.
`grep -rniE "false.positive|proof.of.concept|\bpoc\b|exploitab" tools/security/ tools/quality/`
yields four hits, **none of them a validator** (two are `POC:` in CUI banner
boilerplate, one a Luhn check, one a comment). No module attempts to prove a finding
is real before reporting it; severity comes from static taxonomy (bandit severity,
CVSS lookup, STIG CAT) and gates fire on counts.

**Correction to a plausible assumption:** `tools/quality/review_loop.py` is **not in
the working tree or the git index** — it exists only as quarantined copies under
`.tmp/integrity_quarantine/`. So the "review-until-green" loop is *not* an
abstraction that can be reused as-is; it would have to be productized first (and it
scores ruff/coherence/PR gates — static checks, not exploit proofs, either way).

What *does* exist and is closer to the right shape: the **`adversarial_verifier` ACE
role** (`args/ace/roles/adversarial_verifier.yaml`) — an independent LLM re-check of
a builder's work against acceptance criteria, emitting `task.approved`/`task.rejected`
with feedback re-injected, and a standing rule never to approve after finding a
security regression. Plus `run_agent_loop_with_rubric` / `_grade_against_rubric`
(`icdev/tools/llm/agent_loop.py:1503-1719`). Both verify *correctness*, not
*exploitability* — but they are the seam to extend.

### Gap S3 — advertised-but-missing implementations (found while mapping)
Two concrete defects surfaced during this review; both are independent of any
adaptation and worth fixing on their own:
- **`sandbox_execute` has no handler.** `tools/mcp/tool_registry.py:5061` points at
  `tools.mcp.gap_handlers.handle_sandbox_execute`, which does not exist (only
  `handle_sandbox_score`). `unified_server.py:75-95` therefore substitutes a `_stub`
  returning `{"error": "Module not available"}` — and `sandbox_execute` is listed in
  the `security` bundle of `args/agent_toolsets.yaml:65`, so the standalone agent's
  advertised sandbox tool is non-functional.
- ~~**`tools/showcase/validator.py` does not exist** despite being documented in
  `CLAUDE.md`'s Quick Reference (`python tools/showcase/validator.py --app <slug> --json`).~~
  **FIXED — oss-fix-02.** The phantom showcase commands were removed from the docs
  (the capability was never built, and `synthetic_data_engine.py` is a library with no
  CLI), and the class of defect is now gated: `coherence_checker.py:check_doc_command_paths`
  resolves every `python tools/...` invocation in `CLAUDE.md` and `docs/reference/commands.md`
  against the filesystem. It found **55 more** broken references, enumerated in
  `args/doc_command_gate.yaml` as a visible backlog; any NEW broken reference fails the gate.

---

## 4. Recommended adaptations

### A0 — measure what is already built and switched off *(do this before anything else)*
The single most surprising finding in this review is that a meaningful slice of what
RAGFlow is admired for is **already implemented here and disabled**:

| Toggle | Config | State | RAGFlow analogue |
|---|---|---|---|
| Cross-encoder rerank | `rag.rerank.enabled` | **false** | "fused re-ranking" |
| Self-RAG reflective rerank | `rag.reflective_rerank.enabled` | **false** | — (we're ahead) |
| Adaptive complexity routing | `rag.adaptive_routing.enabled` | **false** | — (we're ahead) |
| Binary Hamming pre-filter | `rag.quantization.binary_prefilter` | **false** | — (perf) |
| RAPTOR | `rag.raptor.enabled` | **false** | RAPTOR toggle |
| Filesystem auto-index | `rag.auto_indexer.enabled` | **false** | — |

Run `tools/rag/rag_benchmark.py` over `args/rag/golden_query_set.yaml` with each
toggle on, against the committed baselines. Two outcomes, both valuable: either we
gain retrieval quality for the price of a config change, or we learn these were
correctly disabled and can say so with numbers. **RAPTOR is the cautionary case —
it was already measured as a regression, so "RAGFlow has it" is not an argument.**
Nothing else in this document should be built before this baseline exists.

### A1 — `fit_markdown` page extractor *(from Crawl4AI; first build item)*
New module, e.g. `tools/http/page_extract.py`:

```python
extract(html, *, query=None) -> {
    "title": str, "raw_text": str, "fit_markdown": str,
    "links": [...], "dropped_blocks": int, "reason": {...},
}
```
- Pass 1 — pruning: block-level scoring on text density, link density, and word
  count (`threshold`, `threshold_type: fixed|dynamic`, `min_word_threshold` all in
  `args/`, per FORGE layer separation). stdlib `html.parser` only — no bs4/lxml,
  which are absent from `requirements.txt` by design.
- Pass 2 — BM25 relevance filter over surviving blocks using the caller's query,
  via the `rank_bm25` we already ship. This replaces positional truncation.
- Preserve headings/lists/tables as markdown; emit reference-style citations so
  link targets survive without inlining URLs into the LLM context.
- Retrofit call sites: `chat_router/url_analyzer.py` (drop `_strip_html` +
  `_MAX_CONTENT` truncation), `document_intelligence/extractors.py::extract_url` /
  `_strip_html`, and the research/creative/news scanners. This also satisfies the
  already-seeded DIC `html_provider` tasks without adding `html2text`.
- **Fetch path, not just parse path.** Every retrofitted site must go through
  `tools/http/client.py::get_session` *and* call the existing
  `link_check.egress_guard` (promote it out of `doc_modernization/` into
  `tools/http/` so it is reusable, keeping its default-off config semantics).
  That converts Gap C3 from "one gate nobody calls" into a real chokepoint instead
  of adding a 105th bypass.
- **Scan before you trust:** run `injection_scanner.scan_text` on extracted content
  in the shared path so every fetcher inherits it, and drop the
  `skip_injection_scan=True` in `_extract_links_nlp`.
- Success criteria: on a fixed corpus of ~20 saved pages, ≥50% token reduction at
  equal or better answer quality, measured with the existing RAG benchmark harness
  (`tools/rag/rag_benchmark.py`). Deterministic output for identical input. Zero new
  runtime dependencies.

### A1b — make a fetched page citeable *(small, rides along with A1)*
Add a `web` value to `source_citation_registry.citation_type` and persist real
fetch provenance (URL, final URL after redirects, HTTP status, fetch timestamp,
content hash, ETag when present). Without this, anything A1 improves still cannot
be cited under the TRUST invariants — inline `[source: …]` citations must validate
against a persisted provenance record, and today a web page has no valid type.

### A2 — template chunking + section breadcrumbs *(from RAGFlow)*
- `args/chunking_templates.yaml` — versioned templates keyed by document type,
  with the *ICDEV* types that matter: OSCAL/NIST control catalog (one chunk per
  control, never split a control), STIG checklist (one per rule), RFP/SOW
  (Section L/M boundaries), numbered-clause contract, numbered-step SOP/runbook,
  slide deck (one per slide), spreadsheet (row-group with header repetition),
  plus a `general` fallback that is today's sliding window.
- Dispatch in `tools/rag/chunker.py` on an explicit `template=` argument, and
  **activate the dead `"chunking"` key already present on 19 `source_registry.py`
  entries** rather than inventing a parallel config surface. Auto-detection is a
  *suggestion* surfaced to the operator, never a silent choice; record the template
  used on the chunk so it is auditable.
- Breadcrumb: add real `page` / `section` / `doc_id` columns to `rag_chunks`
  (migration) so non-DIC paths stop losing them, prepend the deterministic
  `doc → section → subsection` header to the embedded text, and allow section-level
  expansion of a hit at retrieval.
- Success criteria: a control-catalog fixture chunks 1:1 with controls (zero split
  controls); `tools/rag/rag_benchmark.py` against `args/rag/golden_query_set.yaml`
  shows no regression in recall@k / MRR / nDCG@5 / citation_hit_rate versus the
  committed baselines in `data/rag/rce_*_compliance.json` — the same measurement
  discipline that produced the KEEP(contextual_retrieval)/DROP(raptor) decisions.

  > **Do not benchmark against `rce_*_compliance.json` alone (oss-meas-01-d3).**
  > Those baselines were taken on the v1 33-query set, where 29 of 33 queries sat
  > at both perfect recall and perfect MRR. A change measured only against them
  > can show "no regression" while being invisible either way — which is exactly
  > how the withdrawn RAPTOR DROP was produced. Use the v2 48-query set
  > (`args/rag/golden_query_set.yaml`, control recall@5 0.7431) so an improvement
  > has somewhere to register.

### A2b — real table extraction *(from RAGFlow's DeepDoc, minus DeepDoc)*
Call `pdfplumber.extract_tables()` in the DIC PDF path and emit **markdown tables**
into the extracted text, with a table-aware chunk rule (never split a table; repeat
the header row when a large table must span chunks). Same for DOCX tables (drop the
tab-joined `--- Tables ---` dump) and XLSX row groups. pdfplumber is already used
in-repo with word-coordinate precision, so this is air-gap safe and weight-free —
it recovers most of DeepDoc's actual value without any of its cost. Add pdfplumber
and openpyxl to `requirements.txt` (or make the degrade loud rather than silent).

### A3 — `browse()` agent primitive *(from browser-use)*
Implement the primitive **once** — `tools/browser/agent_browser.py`, built on
`driver_manager.get_driver()`, **no Playwright** — then expose it through the seams
that already exist, because there are four parallel agent-tool registries and
picking the wrong one strands the capability:
1. `tools/agent_toolkit/__init__.py` — export alongside `read_file`/`execute_shell`
   for in-process use.
2. `TOOL_REGISTRY` in `tools/mcp/tool_registry.py` + a handler in `gap_handlers.py` —
   `tools/agent_runtime/discovery.py` then picks it up automatically.
3. A new `browser` bundle in `args/agent_toolsets.yaml` so the standalone agent can
   reach it, marked `mutating: true`.
4. `tools/ace/agent_tools.py` — add to `_SCHEMAS` (12 tools today) and
   `_make_handler` if ACE co-workers should have it.
Enforce through `default_safety_gate` in `tools/agent_runtime/dispatch.py` and
`tools/security/mcp_tool_authorizer.py` (deny-first RBAC), and heed the S3 lesson:
a registry entry without a working handler silently becomes a stub.

Surface:
- `read_state()` → indexed interactive elements (`[14] button "Promote"`, role +
  visible text + selected attributes), page title/URL, and an optional screenshot;
  DOM verbosity governed by an `include_attributes`-style allowlist in `args/`.
- `navigate(url)`, `click(index)`, `type(index, text)`, `select(index, value)`,
  `press(key)`, `screenshot()`.
- **Scope controls are not optional, ship them with v1:**
  - `allowed_domains` — default `localhost`/`127.0.0.1` only; anything else requires
    explicit config and passes `egress_guard`. Note that
    `grep -r "allowed_domains|domain_allowlist" tools/` currently returns **zero
    matches** — no domain-restriction concept exists anywhere in the Python tree
    today; the only network restriction is K8s NetworkPolicy generation, which does
    nothing for a `python tools/...` run.
  - `sensitive_data` placeholder substitution — credentials resolved at the driver,
    never rendered into the prompt or the transcript.
  - per-run action cap and step timeout (mirroring `max_actions_per_step`/`max_failures`).
  - every action audited to `audit_trail`, following `_shell.py`'s `audit=True` pattern.
- First consumer is **V&V, not browsing.** Two existing gates are currently
  evaluated statically and would be strictly better evaluated by driving the UI:
  `new_page_completeness` in `args/security_gates.yaml:97` (detected by
  `coherence_checker.py::check_new_page_completeness` — i.e. by grepping for a
  template include), and `acceptance_validation` (:109), whose blocking conditions
  literally include `ui_page_renders_with_error` and warning `page_content_empty`.
  Those are runtime facts being inferred from source text.
- Success criteria: the agent reproduces one existing hand-written e2e script's
  assertions from a goal statement alone, on the live dashboard, with an audit row
  per action and zero navigation outside the allowlist.

### A4 — reproduce-or-drop rule for findings *(from STRIX)*
Bind every dynamic finding to a stored, replayable reproduction (request/response
pair, or agent action trace from A3). Unreproducible → `unconfirmed`, never
`finding`, and never a gate block.

Build it on the `adversarial_verifier` ACE role + `run_agent_loop_with_rubric`
(gap S2), **not** on `tools/quality/review_loop.py` — that file is quarantined, not
live. Findings already have somewhere to land (`failure_log`, `poam_items`,
`finding_approvals`, and the kanban-filing precedent in
`tools/testing/api_contract_tester.py`, which files `[API-CONTRACT]` bug tasks with
evidence); this adds the evidence requirement in front of them.

Pair it with the fail-closed fix to `dast_runtime_gates.py`: an empty scan must
score `unknown`, not 1.0.

Success criteria: a seeded, deliberately-fixed vulnerability is confirmed by a
replay that then *fails* once the fix is applied — i.e. the reproduction is proven
to discriminate.

### A5 — DIC chunk inspect/repair *(from RAGFlow)*
Extend `document_intelligence/doc_detail.html` from read-only chunk display
(`:144`, `:414`) to inspect + merge/split/re-chunk/re-embed. **Reuse the DIC
section-review HITL machinery that already exists** — `blueprint.py:455 /review`
plus the `/api/sections/<id>/{lock,annotations,approve,reject,revise,history}`
surface, presence registry, and SSE — since it is the same
assign/revise/approve/lock shape one level down the hierarchy. Every mutation
audited; `dic_chunk_links.chunk_hash` re-baselined so citation evidence stays
honest. The 8-component dashboard-page gate applies (including IQE integration) if
this becomes its own page rather than a panel on `doc_detail`.

### A6 — app red team against our own target *(from STRIX; last)*
Model it on `llm_red_team.py` exactly — attack catalog in `args/`, detectors,
findings table, OWASP mapping, `--gate` non-zero exit — but over HTTP against our
own running dashboard. Highest-value probe families, chosen from where our real
defects have been: authz matrix (role × route × expected status), tenant-crossing
reads, classification read-up/read-down, IDOR on canvas object ids, CSRF on
mutating routes. Findings gain PoC validation from A4 and, for client-side checks,
the browser from A3.

**Most of the scaffolding already exists — reuse it rather than starting fresh:**
- `tools/security_canvas/dast_runtime_gates.py` already has the findings-recording
  tables (`zig_dast_scans`, `zig_dast_gate_results`), the OWASP check list, and the
  gate scorer. It is waiting for a scanner to pass it `findings`. Supply that, and
  make the no-scan case fail closed (gap S1).
- `tools/testing/route_smoke.py` already enumerates every nav route and authenticates
  against the live app; `tools/testing/api_contract_tester.py` already replays live
  requests against the OpenAPI spec and files evidence-bearing kanban bugs;
  `tools/testing/a11y_sweep.py` already injects vendored JS into a live browser.
  These are the harnesses to extend, not to duplicate.
- **Scope is hard-locked:** own-target allowlist only, refuse any non-allowlisted
  host, written authorization record per target. Never third-party hosts.
- **ICDEV is a public repo** — findings, locations, and payloads go to the private
  triage path; public artifacts (PR bodies, docs, cards) get redacted summaries only.

---

## 5. Cost, risk, and governance

- **Dependency budget: zero new runtime deps** for A1–A5 as scoped (stdlib
  `html.parser`, existing `rank_bm25`, existing Selenium + vendored drivers). This
  is the point of adapting rather than adopting.
- **A3 is the one with real blast radius.** An LLM that can click in a browser
  inside a platform that manages ATO artifacts needs the scope controls landed in
  the same change as the capability, not as a follow-up. Default-deny domains,
  audited actions, capped steps.
- **A6 is dual-use.** It is defensible only as a self-test against systems we own,
  with an authorization record and an enforced target allowlist. It must not
  become a general scanner, and it must not store exploit payloads beyond what a
  reproduction requires.
- **Attribution:** any code citing these projects needs `_ATTRIBUTION_REGISTRY`
  entries (url, license, audit_status, clean-room notes) or
  `check_attribution_claims` fails the gate. Follow the wording precedent in
  `tools/agent_toolkit/__init__.py` (concept adopted, independent implementation,
  no runtime dependency).
- **Sandbox coverage:** `page_extract.py` ingests untrusted third-party HTML and
  therefore needs a recorded decision in `docs/security/sandbox-coverage.md`
  (`coherence_checker.py:check_sandbox_coverage` enforces this).
- **Egress:** every fetch path through `tools/http/client.py` and the egress policy
  manager. No new direct `urllib`/`requests` call sites.
- **Air-gap:** each adaptation must degrade cleanly with no network and no model
  weights — the same discipline `extractors.py` and `driver_manager.py` already follow.
- **Delivery mechanics:** multi-task work here requires a project card in
  `args/projects.yaml` plus seeded kanban tasks, worktree-first branches, and the
  standard manifest/companion/coherence close-out.

**Non-goals:** Playwright; Docker-required paths; Elasticsearch/Infinity;
vendored torch/Paddle weights; a second agent framework; a general-purpose web
crawler; scanning anything we do not own; npm.

---

## 6. What I would *not* adapt, and why

| Upstream idea | Verdict | Reason |
|---|---|---|
| RAGFlow declarative ingestion pipeline (parser→chunker→transformer→indexer) | **Defer** | Real value, and we already have the "transformer" stage in spirit (`_ai_*` enrichment in `ingest_orchestrator.py`). But the hardcoded dispatch is not the bottleneck yet; revisit once A2 proves multiple templates are in use. A prerequisite worth doing first is consolidating the **two PDF chains** and ≥5 document parsers (gap R6). |
| RAGFlow DeepDoc / VLM parsing | **Reject the implementation, take the goal** | torch/Paddle + downloaded weights; `extractors.py` already made and documented this call, which is why layout mode is permanently `flat-ocr`. Table fidelity — the part that actually matters — is recoverable via pdfplumber (A2b). |
| RAGFlow RAPTOR toggle | **Reject (already measured)** | Built here, benchmarked, and recorded as a regression. Upstream popularity does not override our own numbers. |
| RAGFlow agent canvas, admin CLI | **Reject** | Duplicates `/chat`, ACE, and `icdev` CLI. |
| Crawl4AI deep crawl (BFS/DFS/BestFirst), URL seeding, adaptive crawling | **Defer/reject** | No current requirement pulls for multi-page crawling. If one appears, build C2/C3 (robots.txt, crawl-delay, depth cap, dedupe, page cache) on `tools/http/client.py` — do not vendor the crawler. |
| Crawl4AI CSS/XPath extraction schemas | **Defer** | Worth it per recurring source (SAM.gov, NVD, vendor advisories) to replace LLM extraction with deterministic parsing. Pull forward if a specific source keeps breaking. |
| browser-use cloud, `ChatBrowserUse`, CAPTCHA solving | **Reject** | External dependency, and CAPTCHA circumvention is out of scope. |
| STRIX Caido proxy, nuclei bundle, `curl \| bash` installer, Docker sandbox image | **Reject** | Not air-gap installable; overlaps `sandbox_executor.py`. |
| STRIX agent graph | **Reject (have it)** | `invoke_council` / CoD / ACE roles already cover specialist collaboration. |

---

## 7. Defects found while mapping (fix these regardless of what we adapt)

None of these depend on adopting anything. They were surfaced by the comparison and
are worth their own cards.

| # | Defect | Location | Severity |
|---|---|---|---|
| ~~D1~~ | ~~**DAST gate certifies itself.**~~ `run_dast_scan` ignored `target_url`; all checks defaulted to pass; `deploy_dast_gates()` scored 100% with no scan and marked ZIG activity p2-21 complete — and `continuous_authorization._resolve_dast_signal` fed that fabricated 1.0 into the **cATO ongoing-authorization posture**. **FIXED — PR #790**: per-check pass/fail/unknown, observed checks only written to `zig_dast_scans`, `gate_status="unknown"` (never `pass`) on any evidence gap, activity marked `in_progress` while no scanner is wired, and `unknown`/NULL scores earn no cATO credit. 17 regression tests, verified to fail against the pre-fix code. | `tools/security_canvas/dast_runtime_gates.py` | ~~**High**~~ — resolved |
| D2 | **`sandbox_execute` MCP tool has no handler** → silently served as an error stub, while advertised in the `security` agent bundle. | `tools/mcp/tool_registry.py:5061`, `args/agent_toolsets.yaml:65` | Medium |
| D3 | **The one good egress gate is unused.** `egress_guard` (HTTPS-only, allow/denylist, resolve-then-reject private/metadata IPs, per-hop redirect revalidation) is called by exactly one feature and defaults off; ~104 modules fetch external URLs with none of it. | `tools/doc_modernization/link_check.py:206` | Medium |
| D4 | **Fetched third-party bytes mostly skip injection scanning**, and the NLP link extractor explicitly passes raw HTML to an LLM with `skip_injection_scan=True`. | `tools/genesis/reflexes/research.py` | Medium |
| ~~D5~~ | ~~**Documented tool does not exist:** `python tools/showcase/validator.py --app <slug> --json` is in `CLAUDE.md`'s Quick Reference.~~ **FIXED — oss-fix-02**: phantom showcase commands removed from docs + manifest; new `doc_command_paths` coherence gate resolves all 560 documented `python tools/...` invocations against the filesystem and fails on any new breakage. Surfaced 55 further pre-existing broken references, grandfathered with reasons in `args/doc_command_gate.yaml`. | `CLAUDE.md` | ~~Low (docs)~~ — resolved |
| D6 | **Dead config key:** `"chunking"` on 19 `source_registry` entries is read only by a listing filter; `ingestion_manager` never honors it. Implies per-source chunking that doesn't happen. | `tools/rag/source_registry.py:822` | Low |
| D7 | **Stale docstring claims a capability that isn't imported** (trafilatura-based YouTube text fallback). | `tools/document_intelligence/extractors.py:1306` | Low (docs) |
| D8 | **Silent optional-dependency cliff:** on a clean `pip install -r requirements.txt`, XLSX extraction, image OCR, and the two best PDF passes all degrade with no loud signal (pdfplumber, pymupdf, openpyxl, easyocr absent). | `requirements.txt` vs `extractors.py` | Medium |
| D9 | **`tools/quality/review_loop.py` is referenced as a live abstraction but exists only under `.tmp/integrity_quarantine/`** — untracked by git. Anything planning to build on it is planning on nothing. | — | Low |

**Public-repo note:** this repository is public. D1–D4 are described here as
assurance/process defects with file locations but **no exploit paths, payloads, or
auth-gap specifics** — that level of detail belongs in the private triage path, not
in `docs/`. Keep it that way if these become cards or PRs.
