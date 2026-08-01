#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the OSS Adaptation (oss-) kanban card — 23 tasks across 12 epics.

Adapts four upstream OSS projects into ICDEV as patterns, not dependencies.
Analysis, per-item verdicts, and the rejection rationale live in
``docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md``.

Two layers of dependency, both required:

1. **Scalar** — every build task sets ``depends_on_task_id = oss-gate-00``, the
   manual-mode sentinel held ``in_progress`` per ``tools/kanban/gates.py``. While
   the gate is held, nothing dispatches. Releasing it is a deliberate human act:
   ``python -m tools.kanban.cli --set-status oss-gate-00 done``.

2. **Junction** (:data:`EDGES` → ``kanban_task_deps``) — intra-epic ordering.
   This layer is NOT optional bookkeeping. The scalar dep alone makes all 22
   tasks eligible the instant the gate opens, and because ``create_tasks`` writes
   one identical ``created_at`` for the whole batch, the promoter's
   ``created_at ASC`` tiebreak is arbitrary among same-priority tasks. Without
   these edges the runner can pick ``oss-browse-02`` (scope controls) before
   ``oss-browse-01`` (the primitive they constrain), or ``oss-filter-02``
   (retrofit call sites) before ``oss-filter-01`` creates the module being
   retrofitted onto. Both the promoter and the dispatcher re-check junction deps
   (``promote_backlog_to_scheduled._deps_satisfied``,
   ``genesis/reflexes/kanban.py`` dep clause), so the edges hold at both stages.

Idempotent: existing tasks and existing edges are skipped, so re-running is safe.

CLI::

    python tools/kanban/seed_oss_adaptation.py --dry-run --json
    python tools/kanban/seed_oss_adaptation.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

SPIKE = "docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md"
GATE = "oss-gate-00"

TASKS: list[dict] = [
    {
        "id": GATE,
        # Contains the canonical GATE_TITLE_MARKER from tools/kanban/gates.py, so the
        # sentinel is recognised by BOTH the id suffix and the title — the redundancy
        # gates.py intends, so a rename of either one cannot silently un-gate the card.
        "title": "OSS Adaptation - MANUAL-MODE GATE (held; release to start the oss- card)",
        "priority": "high",
        "status": "in_progress",
        "task_type": "chore",
        "description": (
            "Pipeline-exempt manual gate for the oss- card. Every other oss- task "
            "declares depends_on_task_id=oss-gate-00, so while this task sits in "
            "in_progress the scheduler will not dispatch any of them.\n\n"
            "Release procedure: a human moves this task to done, which unblocks the "
            "card. Do NOT open a PR for this task and do NOT let an agent close it.\n\n"
            f"Scope, rationale, and per-item verdicts: {SPIKE}\n\n"
            "Recommended order: meas -> filter -> cite -> chunk/table -> browse -> "
            "poc -> hitl -> redteam. fix-* and xcut-* can run in parallel at any time."
        ),
    },
    # ---------------- meas (A0) ----------------
    {
        "id": "oss-meas-01",
        "title": "Benchmark the five retrieval toggles that ship OFF",
        "priority": "high",
        "description": (
            "Before adapting anything from RAGFlow, measure what is already built here "
            "and disabled. Toggles, all currently false in args/rag_config.yaml:\n"
            "  - rag.rerank.enabled            (cross-encoder; BGE + LLM providers exist)\n"
            "  - rag.reflective_rerank.enabled (Self-RAG per-doc reflection)\n"
            "  - rag.adaptive_routing.enabled  (query-complexity pre-routing)\n"
            "  - rag.quantization.binary_prefilter\n"
            "  - rag.auto_indexer.enabled\n\n"
            "Method: tools/rag/rag_benchmark.py over args/rag/golden_query_set.yaml, "
            "comparing against the committed baselines in data/rag/rce_baseline_compliance.json "
            "and data/rag/rce_contextual_compliance.json. Report recall@k, MRR, nDCG@5, "
            "citation_hit_rate per toggle, plus latency delta.\n\n"
            "Deliverable: docs/features/oss-meas-01-retrieval-toggle-benchmark.md with a "
            "KEEP/DROP decision per toggle and the numbers behind it. Flip the winners on "
            "in args/rag_config.yaml as part of this task.\n\n"
            "Note: RAPTOR (rag.raptor.enabled) was already measured as a REGRESSION "
            "(0.0 recall@5, 0.0 MRR, -0.0005 nDCG@5). Do not re-enable it because RAGFlow "
            "has it; include the existing measurement in the writeup for contrast.\n\n"
            "Nothing else on this card should be built before this baseline exists."
        ),
    },
    # ---------------- filter (A1) ----------------
    {
        "id": "oss-filter-01",
        "title": "tools/http/page_extract.py — fit_markdown two-pass content filter",
        "priority": "high",
        "description": (
            "Adapt Crawl4AI's fit_markdown as pure deterministic Python. NO new runtime "
            "dependencies: stdlib html.parser plus the rank_bm25 already in requirements.txt. "
            "Do NOT add bs4, lxml, html2text, markdownify, or trafilatura.\n\n"
            "API: extract(html, *, query=None) -> {title, raw_text, fit_markdown, links, "
            "dropped_blocks, reason}\n\n"
            "Pass 1 (pruning): block-level scoring on text density, link density, and word "
            "count. Thresholds (threshold, threshold_type: fixed|dynamic, min_word_threshold) "
            "live in args/, not in code — FORGE layer separation.\n"
            "Pass 2 (BM25): rank surviving blocks against the caller's query and keep the "
            "relevant ones. This replaces positional truncation, which is the actual defect: "
            "tools/chat_router/url_analyzer.py cuts at _MAX_CONTENT=7000 chars regardless of "
            "where the answer is.\n\n"
            "Preserve headings, lists, and tables as markdown. Emit reference-style link "
            "citations so URLs do not inline into the LLM context.\n\n"
            "Also required: a sandbox-coverage decision recorded in "
            "docs/security/sandbox-coverage.md — this module ingests untrusted third-party "
            "HTML, and coherence_checker.py:check_sandbox_coverage enforces the ledger.\n\n"
            "Success criteria: on a fixed corpus of ~20 saved pages, >=50% token reduction at "
            "equal or better answer quality; byte-identical output for identical input."
        ),
    },
    {
        "id": "oss-filter-02",
        "title": "Retrofit HTML call sites onto page_extract + central HTTP client",
        "priority": "high",
        "description": (
            "Replace the regex-strip-and-truncate paths with oss-filter-01's extractor.\n\n"
            "Call sites:\n"
            "  - tools/chat_router/url_analyzer.py — drop _strip_html and the _MAX_CONTENT "
            "    truncation; it also fetches with raw urllib.request.\n"
            "  - tools/document_intelligence/extractors.py — _strip_html (:487) and "
            "    extract_url (:1180), which is fetch + <title> regex + strip with a 2 MB cap.\n"
            "  - tools/research/source_scanner.py scan_news_blogs, which currently stores raw "
            "    <description> text with no stripping at all.\n"
            "  - tools/creative/competitor_discoverer.py regex extractors.\n\n"
            "Every retrofitted site must fetch through tools/http/client.py::get_session (mTLS, "
            "proxy, retry/backoff from args/http_client.yaml) instead of raw urllib/requests, "
            "and must run tools/security/injection_scanner.py::scan_text on extracted content "
            "before it reaches an LLM. While here, remove skip_injection_scan=True from "
            "tools/genesis/reflexes/research.py::_extract_links_nlp, which hands raw HTML to a "
            "model unscanned.\n\n"
            "Also delete the stale docstring at extractors.py:1306 claiming a trafilatura-based "
            "fallback that is never imported.\n\n"
            "Do not add a new direct urllib/requests call site — there are already ~104 modules "
            "that fetch external URLs outside the central client, and this task should reduce "
            "that count, not raise it."
        ),
    },
    {
        "id": "oss-filter-03",
        "title": "Promote egress_guard into tools/http/ and call it from every fetch site",
        "priority": "high",
        "description": (
            "ICDEV already has a correct SSRF-safe fetch gate and almost nothing uses it: "
            "tools/doc_modernization/link_check.py:206 egress_guard — HTTPS-only, suffix "
            "allow/denylist with deny-wins, getaddrinfo then reject if ANY resolved answer is "
            "private/loopback/link-local/multicast/reserved (covers cloud metadata at "
            "169.254.169.254), paired with a _NoRedirect opener so every redirect hop is "
            "re-validated by the caller.\n\n"
            "Move it to tools/http/egress_guard.py (keep a thin re-export at the old path so "
            "link_check keeps working), preserve its default-off config semantics, and call it "
            "from the fetch paths retrofitted in oss-filter-02.\n\n"
            "Do NOT confuse this with the two MCP-visible 'egress' tools, neither of which "
            "gates a Python request: tools/security/egress_policy_manager.py generates K8s "
            "NetworkPolicy YAML (its orchestrator/infrastructure presets allow *:443 and its "
            "hostname rules emit port-only K8s rules), and tools/registry/egress_monitor.py is "
            "post-hoc, self-reported child-app telemetry.\n\n"
            "Secondary cleanup: air-gap detection is reimplemented at least four times across "
            "two different env vars (ICDEV_AIRGAP vs ICDEV_ENVIRONMENT=air-gapped). Converge "
            "the fetch paths on tools/airgap.is_airgap()."
        ),
    },
    # ---------------- cite (A1b) ----------------
    {
        "id": "oss-cite-01",
        "title": "Make a fetched web page citeable (web citation type + fetch provenance)",
        "priority": "high",
        "description": (
            "source_citation_registry.citation_type has a CHECK constraint whose values are "
            "hitl, rag, prov_entity, prov_activity, canvas_ai, slsa, sbom, "
            "compliance_evidence, agent_decision, manual. There is no web/url/crawl value, so "
            "a fetched page cannot be registered as a first-class citation — which means "
            "anything oss-filter-01/02 improves still cannot satisfy the TRUST invariant that "
            "inline [source: ...] citations validate against a persisted provenance record.\n\n"
            "Add a 'web' citation type (migration; derive the CHECK constraint from the Python "
            "constant, never hardcode it — and add the entity type to BOTH places) and persist "
            "real fetch provenance: requested URL, final URL after redirects, HTTP status, "
            "fetched_at, content hash, ETag/Last-Modified when present.\n\n"
            "Today provenance for a fetched URL is just url + content_hash + a metadata JSON "
            "blob on the signal row. tools/genesis/reflexes/research.py:207 has the richest "
            "existing example (export_gkp with evidence={feed, fetched_at}) — generalize that.\n\n"
            "Reuse tools/quality/citation_grounding.py for parsing/validation. Do not "
            "re-implement citation parsing."
        ),
    },
    # ---------------- chunk (A2) ----------------
    {
        "id": "oss-chunk-01",
        "title": "Template chunking driven by the existing source_registry chunking key",
        "priority": "high",
        "description": (
            "tools/rag/chunker.py has exactly one strategy: short content -> single chunk, "
            "long -> sliding window with 10% overlap snapped to a sentence boundary, adaptive "
            "size clamped to [150, 2000] tokens. That destroys structure in the document types "
            "we care most about — it splits NIST/OSCAL controls and STIG rules mid-item.\n\n"
            "A config hook ALREADY EXISTS and is dead: 19 entries in tools/rag/source_registry.py "
            "carry a 'chunking' key (canvas_graph, canvas_assessment), read at exactly one place "
            "(source_registry.py:822, a listing filter). ingestion_manager.py never consults it. "
            "Activate that key rather than inventing a parallel config surface.\n\n"
            "Create args/chunking_templates.yaml with ICDEV-relevant templates:\n"
            "  - oscal_catalog   — one chunk per control, never split a control\n"
            "  - stig_checklist  — one chunk per rule\n"
            "  - rfp_sow         — Section L/M boundaries\n"
            "  - contract        — numbered-clause boundaries\n"
            "  - sop_runbook     — numbered-step boundaries\n"
            "  - slide_deck      — one chunk per slide\n"
            "  - spreadsheet     — row groups with header repetition\n"
            "  - general         — today's sliding window (default, unchanged)\n\n"
            "Dispatch on an explicit template= argument in chunk_content(). Auto-detection is a "
            "SUGGESTION surfaced to the operator, never a silent choice. Record the template "
            "used on each chunk so the decision is auditable."
        ),
    },
    {
        "id": "oss-chunk-02",
        "title": "Add page/section/doc_id columns to rag_chunks + section breadcrumbs",
        "priority": "high",
        "description": (
            "rag_chunks (DDL at tools/rag/sqlite_vector_store.py:362) has no page, no bbox, no "
            "section/heading, and no doc_id column. Page and section exist only in the DIC-side "
            "join table dic_chunk_links, so every non-DIC ingestion path loses them entirely and "
            "a chunk reading 'shall be documented in the SSP' is positionally orphaned.\n\n"
            "This is RAGFlow 0.21's 'long-context RAG' idea: attach chapter/TOC context to each "
            "chunk so a fragment knows where it came from and retrieval can expand to the "
            "enclosing section.\n\n"
            "Work:\n"
            "  1. Migration adding page, section, doc_id columns to rag_chunks (PG-native SQL; "
            "     translate_sql is an init-only fallback, not load-bearing).\n"
            "  2. Prepend a deterministic 'doc -> section -> subsection' breadcrumb to the "
            "     embedded text.\n"
            "  3. Allow section-level expansion of a hit at retrieval time.\n\n"
            "This is the DETERMINISTIC complement to tools/rag/contextual_retrieval.py, which is "
            "already shipped ON and uses an LLM-generated per-chunk prefix (measured +0.0151 "
            "recall@5, +0.0202 MRR). Do not duplicate or replace it — unlike the LLM prefix, real "
            "columns are also usable for filtering and citation display."
        ),
    },
    {
        "id": "oss-chunk-03",
        "title": "Benchmark template chunking against the golden set",
        "priority": "medium",
        "description": (
            "Acceptance for oss-chunk-01/02.\n\n"
            "  1. A control-catalog fixture must chunk 1:1 with controls — assert zero split "
            "     controls. Same for a STIG checklist fixture (one chunk per rule).\n"
            "  2. tools/rag/rag_benchmark.py over args/rag/golden_query_set.yaml must show no "
            "     regression in recall@k / MRR / nDCG@5 / citation_hit_rate versus the committed "
            "     baselines in data/rag/rce_*_compliance.json, on general documents.\n"
            "  3. Re-index cost measured and recorded.\n\n"
            "Same measurement discipline that produced the existing KEEP(contextual_retrieval) / "
            "DROP(raptor) decisions. If template chunking does not beat the baseline, say so and "
            "keep it opt-in per source."
        ),
    },
    # ---------------- table (A2b) ----------------
    {
        "id": "oss-table-01",
        "title": "Real table extraction via pdfplumber (DeepDoc's goal, not its stack)",
        "priority": "medium",
        "description": (
            "There is no table object model anywhere: no cells, bboxes, or rowspans, and no "
            "markdown/HTML table reconstruction. DOCX tables are dumped under a '--- Tables ---' "
            "heading as tab-joined rows; XLSX rows flatten to 'a | b | c'; PDF tables are "
            "whatever extract_text() emits. pdfplumber.extract_tables() is never called in the "
            "DIC path even though pdfplumber is already used with word-coordinate precision "
            "elsewhere in-repo (tools/network/pdf_import.py, "
            "tools/govcon/solicitation_parser.py for CLIN tables).\n\n"
            "RAGFlow's layout advantage comes from DeepDoc, which we correctly refuse: it pulls "
            "torch/Paddle and downloads model weights, which is why "
            "document_intelligence/extractors.py permanently runs in flat-ocr mode (paddleocr "
            "and doclayout-yolo are commented out of requirements.txt:48-56 as not air-gap safe). "
            "Table fidelity is the part that actually matters, and pdfplumber gets most of it "
            "with no weights.\n\n"
            "Work: emit markdown tables into extracted text for PDF (pdfplumber), DOCX, and "
            "XLSX; add a table-aware chunk rule (never split a table; repeat the header row when "
            "a large table must span chunks) wired into the oss-chunk-01 templates.\n\n"
            "Do NOT add paddleocr, doclayout-yolo, torch, or Paddle."
        ),
    },
    {
        "id": "oss-table-02",
        "title": "Close the silent optional-dependency cliff in extraction",
        "priority": "medium",
        "description": (
            "A clean `pip install -r requirements.txt` yields pypdf + python-docx + python-pptx "
            "+ rank_bm25 + numpy. pymupdf, pdfplumber, openpyxl, easyocr, pytesseract, "
            "markitdown, chromadb, and faiss are all optional imports ABSENT from "
            "requirements.txt — so XLSX extraction, image OCR, and the two best PDF passes "
            "silently degrade on a clean install.\n\n"
            "'Silently' is the problem, not 'degrade'. Either add pdfplumber and openpyxl to "
            "requirements.txt (both are pure-Python and air-gap safe, and oss-table-01 needs "
            "pdfplumber anyway), or make the degrade loud: surface the active extraction "
            "capability set in tools/testing/health_check.py --json and warn once at ingest time "
            "when a file type resolves to a degraded provider.\n\n"
            "Follow the existing precedent in extractors.py, which already probes layout libs at "
            "import and exposes the mode via layout_mode()/LAYOUT_MODE. Extend that pattern "
            "rather than inventing a second capability registry."
        ),
    },
    # ---------------- browse (A3) ----------------
    {
        "id": "oss-browse-01",
        "title": "tools/browser/agent_browser.py — indexed-element page representation",
        "priority": "medium",
        "description": (
            "No ICDEV agent can use a browser. get_driver() has 8 consumers: the driver manager "
            "itself, its __init__, the air-gap vendoring tool, one scheduling script, "
            "tools/sharepoint/browser_fallback.py, and three tools/testing/e2e_* modules. ACE "
            "agent mode declares 12 tools (tools/ace/agent_tools.py:50 _SCHEMAS), none "
            "browser-shaped; the canonical loop greps clean for "
            "navigate|click|screenshot|selenium|playwright|webdriver.\n\n"
            "browser-use's load-bearing idea is not its agent loop (we have several) — it is the "
            "PAGE REPRESENTATION: extract interactive elements and assign each a stable integer "
            "index, so a model acts via click(14) instead of inventing a CSS selector.\n\n"
            "Build on tools/browser/driver_manager.py::get_driver — Selenium with VENDORED "
            "msedgedriver/chromedriver and no runtime downloads. Do NOT add Python Playwright "
            "or a chromium download; the existing @playwright/test setup is npm-based E2E "
            "tooling for our own dashboard and is not the agent path.\n\n"
            "Surface: read_state() -> indexed interactive elements (index, role, visible text, "
            "allowlisted attributes) + title/URL + optional screenshot; navigate(url), "
            "click(index), type(index, text), select(index, value), press(key), screenshot(). "
            "DOM verbosity governed by an include_attributes-style allowlist in args/.\n\n"
            "The assertion half already exists and should be reused, not rebuilt: MCP "
            "validate_screenshot (tools/mcp/gap_handlers.py:596) and "
            "tools/testing/screenshot_validator.py."
        ),
    },
    {
        "id": "oss-browse-02",
        "title": "Ship the agent browser's scope controls in the same change as the capability",
        "priority": "high",
        "description": (
            "An LLM that can click inside a platform managing ATO artifacts needs its guardrails "
            "landed WITH the capability, not as a follow-up. There is currently no "
            "domain-restriction concept anywhere in the Python tree — grep for "
            "allowed_domains|domain_allowlist across tools/ returns zero matches. The only "
            "network restriction is K8s NetworkPolicy generation, which does nothing for a "
            "`python tools/...` run or the Flask process.\n\n"
            "Required in v1 of oss-browse-01:\n"
            "  - allowed_domains, defaulting to localhost/127.0.0.1 ONLY. Anything else requires "
            "    explicit config and must pass egress_guard (see oss-filter-03).\n"
            "  - sensitive_data placeholder substitution: credentials resolved at the driver, "
            "    never rendered into the prompt or the saved transcript. Reuse "
            "    tools/security/credential_broker.py rather than adding a new secret path.\n"
            "  - per-run action cap and per-step timeout (cf. browser-use max_actions_per_step / "
            "    max_failures).\n"
            "  - every action written to audit_trail, following the audit=True pattern in "
            "    tools/agent_toolkit/_shell.py.\n\n"
            "Context for why this matters: the one place an LLM drives a browser today is "
            "tools/testing/e2e_runner.py:548 _execute_via_claude(), which shells out to the "
            "external claude CLI with --dangerously-skip-permissions. Replacing that with an "
            "in-process, audited, scope-limited primitive is part of the point."
        ),
    },
    {
        "id": "oss-browse-03",
        "title": "Register the browser primitive across all four agent-tool seams",
        "priority": "medium",
        "description": (
            "There are four parallel agent-tool registries; registering in the wrong one strands "
            "the capability. Implement the primitive once (oss-browse-01) and expose it via:\n"
            "  1. tools/agent_toolkit/__init__.py — export alongside read_file/execute_shell for "
            "     in-process use.\n"
            "  2. TOOL_REGISTRY in tools/mcp/tool_registry.py + a handler in "
            "     tools/mcp/gap_handlers.py. tools/agent_runtime/discovery.py then derives the "
            "     ToolSpec automatically.\n"
            "  3. A new 'browser' bundle in args/agent_toolsets.yaml, marked mutating: true, so "
            "     the standalone agent can reach it.\n"
            "  4. tools/ace/agent_tools.py — add to _SCHEMAS and _make_handler if ACE co-workers "
            "     should have it.\n\n"
            "Enforcement: default_safety_gate in tools/agent_runtime/dispatch.py (mutating tools "
            "denied unless ICDEV_SAG_ALLOW_MUTATION) and deny-first RBAC in "
            "tools/security/mcp_tool_authorizer.py + args/owasp_agentic_config.yaml.\n\n"
            "Heed the oss-fix-01 lesson: a TOOL_REGISTRY entry whose handler does not exist is "
            "silently replaced by an error stub in unified_server.py:75-95. Add a test that the "
            "handler actually resolves.\n\n"
            "Also complete the 8-point new-tool registration checklist in CLAUDE.md (manifest "
            "shard, commands doc, security gate if blocking, MCP registry, append-only tables, "
            "conftest schema, companion sync, coherence gate)."
        ),
    },
    {
        "id": "oss-browse-04",
        "title": "First consumer — agent-driven page-completeness and acceptance V&V",
        "priority": "medium",
        "description": (
            "Prove the browser primitive on verification, not on browsing.\n\n"
            "Two gates are currently evaluated STATICALLY and would be strictly better evaluated "
            "by driving the real UI:\n"
            "  - new_page_completeness (args/security_gates.yaml:97) is detected by "
            "    coherence_checker.py::check_new_page_completeness — i.e. by grepping for a "
            "    template include. Its blocking conditions are incomplete_canvas_page and "
            "    icdev_mirror_gap, with template_missing_iqe_widget as a warning.\n"
            "  - acceptance_validation (:109) blocks on ui_page_renders_with_error and warns on "
            "    page_content_empty. Those are RUNTIME facts inferred from source text.\n\n"
            "Deliverable: an agent that, given a canvas key, verifies the 8-component dashboard "
            "page gate against the running dashboard — page renders, reachable from nav, IQE "
            "widget present and functional, no console errors — and reports per-component "
            "pass/fail with a screenshot and DOM evidence per finding.\n\n"
            "Success criteria: the agent reproduces one existing hand-written Selenium e2e "
            "script's assertions from a goal statement alone, with an audit row per action and "
            "zero navigation outside the allowlist.\n\n"
            "Visual regressions need screenshot + DOM evidence, not just a 200 status — that is "
            "the recurring V&V lesson this task is meant to institutionalize."
        ),
    },
    # ---------------- poc (A4) ----------------
    {
        "id": "oss-poc-01",
        "title": "Reproduce-or-drop rule for dynamic findings",
        "priority": "medium",
        "description": (
            "STRIX's discipline: a finding ships with a working proof-of-concept or it is not a "
            "finding. Nothing here does that — grep for "
            "false.positive|proof.of.concept|PoC|exploitab across tools/security/ and "
            "tools/quality/ returns four hits, none a validator (two are 'POC:' in CUI banner "
            "boilerplate, one a Luhn check, one a comment). Severity comes from static taxonomy "
            "(bandit severity, CVSS lookup, STIG CAT) and gates fire on counts.\n\n"
            "Rule to implement: every DYNAMIC finding must carry a stored, replayable "
            "reproduction — a request/response pair, or an agent action trace from oss-browse-01. "
            "Unreproducible => status 'unconfirmed', never 'finding', and never a gate block.\n\n"
            "Build on what is live: the adversarial_verifier ACE role "
            "(args/ace/roles/adversarial_verifier.yaml — independent re-check emitting "
            "task.approved/task.rejected, with a standing rule never to approve after finding a "
            "security regression) and run_agent_loop_with_rubric / _grade_against_rubric "
            "(icdev/tools/llm/agent_loop.py:1503-1719).\n\n"
            "IMPORTANT: do NOT plan to build on tools/quality/review_loop.py. It is not in the "
            "working tree or the git index — only quarantined copies exist under "
            ".tmp/integrity_quarantine/. See oss-fix-03.\n\n"
            "Findings already have places to land (failure_log, poam_items, finding_approvals) "
            "and there is a filing precedent with evidence in "
            "tools/testing/api_contract_tester.py, which opens [API-CONTRACT] kanban bugs.\n\n"
            "Success criteria: a seeded, deliberately-fixed vulnerability is confirmed by a "
            "replay that then FAILS once the fix is applied — the reproduction must be proven to "
            "discriminate, not merely to run."
        ),
    },
    # ---------------- hitl (A5) ----------------
    {
        "id": "oss-hitl-01",
        "title": "DIC chunk inspect & repair (merge/split/re-chunk/re-embed)",
        "priority": "medium",
        "description": (
            "RAGFlow's stated core value is 'visibility and explainability — view the chunking "
            "results and intervene where necessary'. In ICDEV chunks are READ-ONLY: "
            "tools/dashboard/templates/document_intelligence/doc_detail.html renders rag_chunks "
            "at :144 and :414 for provenance display only. There is no "
            "merge/split/re-chunk/re-embed path. For a canvas whose entire premise is grounded "
            "citations, an operator who can see a bad chunk but not fix it is a dead end.\n\n"
            "Reuse the HITL machinery that already exists one level up the hierarchy rather than "
            "building a second one: tools/document_intelligence/blueprint.py:455 /review plus "
            "/api/sections/<id>/{lock,annotations,approve,reject,revise,content,hash,history}, "
            "the presence registry, and the SSE stream. It is the same "
            "assign/revise/approve/lock shape applied to chunks.\n\n"
            "Hard requirements:\n"
            "  - Every mutation audited.\n"
            "  - dic_chunk_links.chunk_hash re-baselined after a repair, so the "
            "    hash-at-link-time evidence baseline stays honest and evidence-drift detection "
            "    keeps working.\n"
            "  - Respect WriteGuard and the existing HITL promotion gates.\n"
            "  - If this becomes its own page rather than a panel on doc_detail, the full "
            "    8-component dashboard-page gate applies, including IQE integration and the "
            "    icdev/ template mirror."
        ),
    },
    # ---------------- redteam (A6) ----------------
    {
        "id": "oss-redteam-01",
        "title": "App red team — dynamic self-test against our own dashboard",
        "priority": "low",
        "description": (
            "Nothing exercises the running app for security. grep -rl "
            "'import requests|import httpx|urllib.request' tools/security/ returns ZERO files; "
            "all 48 modules there operate on files, config, and DB rows. "
            "atlas_red_team.py is static by its own docstring; llm_red_team.py is genuinely "
            "active but targets a MODEL, not the app; nuclei/ZAP/Burp appear only as node types "
            "in tools/pipeline/constants.py, i.e. we generate CI YAML that would run DAST and "
            "never run it; caldera_adapter.py is a read-only metadata fetch; fuzzing is "
            "CLI-argument only (tools/testing/fuzz_cli.py).\n\n"
            "Model this on tools/security/llm_red_team.py EXACTLY — attack catalog in args/, "
            "detectors, findings table, OWASP mapping, --gate with non-zero exit — but over HTTP "
            "against our own running dashboard.\n\n"
            "Probe families chosen from where real defects have actually occurred here: authz "
            "matrix (role x route x expected status), tenant-crossing reads, classification "
            "read-up/read-down, IDOR on canvas object ids, CSRF on mutating routes.\n\n"
            "Reuse, do not duplicate: tools/testing/route_smoke.py already enumerates every nav "
            "route and authenticates; tools/testing/api_contract_tester.py already replays live "
            "requests against the OpenAPI spec and files evidence-bearing kanban bugs; "
            "tools/testing/a11y_sweep.py already injects vendored JS into a live browser.\n\n"
            "Feed findings into tools/security_canvas/dast_runtime_gates.py, which is now "
            "evidence-gated (fail-closed) and has the recording tables and gate scorer waiting "
            "for a real scanner. Findings must satisfy oss-poc-01. Prerequisites: oss-browse-01 "
            "for client-side checks, oss-poc-01 for validation.\n\n"
            "Do NOT vendor STRIX, Caido, nuclei, or a Docker sandbox image."
        ),
    },
    {
        "id": "oss-redteam-02",
        "title": "Scope-lock the app red team to owned targets, with redacted public output",
        "priority": "low",
        "description": (
            "The red team in oss-redteam-01 is dual-use and is only defensible as a self-test "
            "against systems we own.\n\n"
            "Required controls:\n"
            "  - Hard target allowlist. Refuse any non-allowlisted host outright; default to "
            "    localhost. No 'warn and continue' path.\n"
            "  - A written authorization record per target, persisted, checked before a run.\n"
            "  - No general-scanner mode, and no storage of exploit payloads beyond what a "
            "    reproduction requires.\n"
            "  - Findings route to POA&M (poam_items) and kanban.\n\n"
            "ICDEV is a PUBLIC repository. Findings, exploit paths, payloads, and auth-gap "
            "locations must NOT appear in public artifacts — PR bodies, docs/, or card "
            "descriptions. Public surfaces get redacted summaries; specifics go to the private "
            "triage path. This is an established constraint, not a preference.\n\n"
            "Also decide the policy question deliberately: tools/qdc/gate_checker.py ships "
            "dast_enabled: False and emits dast_missing as a WARNING rather than a blocker. Once "
            "a real scanner exists, revisit whether it should block, and record the decision."
        ),
    },
    # ---------------- fix ----------------
    {
        "id": "oss-fix-01",
        "title": "sandbox_execute MCP tool has no handler — implement it or remove it",
        "priority": "medium",
        "description": (
            "tools/mcp/tool_registry.py:5061 registers sandbox_execute pointing at "
            "tools.mcp.gap_handlers.handle_sandbox_execute, which does not exist — gap_handlers "
            "has only handle_sandbox_score (:1216). unified_server.py:75-95 therefore substitutes "
            "a _stub returning {'error': 'Module not available', 'status': 'pending'}. "
            "sandbox_execute is listed in the 'security' bundle of args/agent_toolsets.yaml:65, "
            "so the standalone agent's advertised sandbox tool is silently non-functional.\n\n"
            "Either implement handle_sandbox_execute on top of the real "
            "tools/security/sandbox_executor.py (803 lines; llm-sandbox over Docker/K8s/Podman, "
            "network_enabled False by default, 30s/512MB defaults, audit_all_executions True, "
            "config args/sandbox_config.yaml), or remove the registry entry and the bundle "
            "reference.\n\n"
            "Then add a generic regression test that EVERY TOOL_REGISTRY entry resolves to a real "
            "importable handler, so the stub path can never again masquerade as a working tool. "
            "That test is the more valuable half of this task."
        ),
    },
    {
        "id": "oss-fix-02",
        "title": "tools/showcase/validator.py is documented but does not exist",
        "priority": "low",
        "description": (
            "CLAUDE.md's Quick Reference documents `python tools/showcase/validator.py "
            "--app <slug> --json`. tools/showcase/ contains only ai_canvas_demo_runner.py, "
            "blueprint.py, constants.py, and synthetic_data_engine.py. There is no git history "
            "for the path.\n\n"
            "Decide and act: either implement the validator (nearest analogue is "
            "tools/migration/validator.py, and tools/builder/forge_validator.py is the "
            "established --gate pattern), or correct CLAUDE.md and docs/reference/commands.md.\n\n"
            "Then consider a coherence check that every `python tools/...` invocation documented "
            "in CLAUDE.md and docs/reference/commands.md resolves to a file that exists. A "
            "documented command that does not exist is worse than an undocumented one, because "
            "an agent will confidently try to run it."
        ),
    },
    {
        "id": "oss-fix-03",
        "title": "Productize or delete review_loop.py; clear stale capability claims",
        "priority": "low",
        "description": (
            "Two truthfulness cleanups.\n\n"
            "1. tools/quality/review_loop.py is referenced as if it were a live abstraction but "
            "is NOT in the working tree or the git index. It exists only as quarantined copies "
            "under .tmp/integrity_quarantine/{...}/quality/review_loop.py and "
            "{...}/genesis/reflexes/review_loop.py, with runtime logs at "
            ".logs/tools.quality.review_loop.ndjson*. The quarantined content is a "
            "'review-until-green' loop scoring ruff + coherence_checker + "
            "integrity/pr_gates.assess_changed_files over the branch diff, with deterministic "
            "autofix and a fix_brief, configured by args/review_loop_config.yaml. Decide: "
            "productize it under tools/quality/ with tests, or delete the quarantine copies and "
            "the config so nothing plans against a phantom module. Anything gitignored is "
            "invisible to future sessions — that is how this happened.\n\n"
            "2. tools/document_intelligence/extractors.py:1306 claims a YouTube fallback that "
            "uses 'the video page's text (trafilatura)'. trafilatura is never imported anywhere. "
            "Fix the docstring (oss-filter-02 also touches this file — coordinate to avoid a "
            "conflict).\n\n"
            "While here, sweep for other docstrings asserting optional capabilities that are not "
            "importable, and prefer a runtime capability probe (the layout_mode() pattern in the "
            "same file) over a prose claim."
        ),
    },
    # ---------------- xcut ----------------
    {
        "id": "oss-xcut-01",
        "title": "Attribution registry, sandbox coverage, and card close-out",
        "priority": "low",
        "description": (
            "Governance close-out for whatever ships from this card.\n\n"
            "1. Add _ATTRIBUTION_REGISTRY entries in tools/workflow/coherence_checker.py:1629 "
            "for each upstream actually cited in code — ragflow (Apache-2.0), crawl4ai "
            "(Apache-2.0), browser-use (MIT), strix (Apache-2.0) — each with url, license, "
            "audit_status, and clean-room notes. check_attribution_claims fails the gate on a "
            "citation whose upstream is not registered, and on GPL/AGPL upstreams without an "
            "explicit exemption. None of these four is GPL/AGPL. Follow the wording precedent in "
            "tools/agent_toolkit/__init__.py: concept adopted, independent implementation, no "
            "runtime dependency.\n\n"
            "2. Record the sandbox-coverage decision for tools/http/page_extract.py in "
            "docs/security/sandbox-coverage.md (it ingests untrusted third-party HTML). "
            "coherence_checker.py:check_sandbox_coverage enforces the ledger.\n\n"
            "3. Standard close-out: manifest shard entries for every new module, "
            "docs/reference/commands.md for every new CLI, "
            "`python tools/dx/companion.py --sync --write --json` (never in background), and "
            "`python tools/workflow/coherence_checker.py --all --fix --gate`.\n\n"
            "4. Write the disposition record: for each REJECTED upstream idea, state why, so the "
            "next person evaluating these projects does not redo the analysis. The rejections "
            "(Playwright, Elasticsearch/Infinity, DeepDoc/VLM weights, RAPTOR, STRIX's Docker "
            "sandbox + Caido + nuclei, a general web crawler) are as much the deliverable as the "
            "adoptions. Source analysis: "
            "docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md"
        ),
    },
]


# ---------------------------------------------------------------------------
# Intra-epic ordering — (task, depends_on) pairs written to kanban_task_deps.
# Read the module docstring before changing these: without them the runner can
# build a task before the thing it depends on exists.
# ---------------------------------------------------------------------------

EDGES: list[tuple[str, str]] = [
    # Measure the retrieval toggles that already ship OFF before changing retrieval.
    # oss-meas-01 says so in its own body; these edges enforce it.
    ("oss-filter-01", "oss-meas-01"),
    ("oss-chunk-01", "oss-meas-01"),
    # filter epic — the retrofit and the egress promotion both need the extractor.
    ("oss-filter-02", "oss-filter-01"),
    ("oss-filter-03", "oss-filter-01"),
    # a fetched page can only be made citeable once there is a fetch path worth citing
    ("oss-cite-01", "oss-filter-01"),
    # chunk epic
    ("oss-chunk-02", "oss-chunk-01"),
    ("oss-chunk-03", "oss-chunk-01"),
    ("oss-chunk-03", "oss-chunk-02"),
    # table-aware chunk rules need the template dispatch to hang off
    ("oss-table-01", "oss-chunk-01"),
    ("oss-table-02", "oss-table-01"),
    # browse epic — scope controls and registration cannot precede the primitive.
    # Note oss-browse-02 is `high` while oss-browse-01 is `medium`, so priority
    # ordering alone actively inverts this pair. The edge is what fixes it.
    ("oss-browse-02", "oss-browse-01"),
    ("oss-browse-03", "oss-browse-01"),
    ("oss-browse-03", "oss-browse-02"),
    ("oss-browse-04", "oss-browse-03"),
    # reproduce-or-drop needs agent action traces to replay
    ("oss-poc-01", "oss-browse-01"),
    # the red team needs both the browser and the validation discipline
    ("oss-redteam-01", "oss-poc-01"),
    ("oss-redteam-01", "oss-browse-01"),
    ("oss-redteam-02", "oss-redteam-01"),
    # chunk repair needs template chunking to re-chunk against
    ("oss-hitl-01", "oss-chunk-01"),
    # close-out attributes what actually shipped
    ("oss-xcut-01", "oss-filter-01"),
    ("oss-xcut-01", "oss-browse-01"),
]

# oss-fix-01/02/03 carry no edges on purpose: they are independent defect cleanup
# and the card brief promises they can run in parallel at any time.


def build_specs() -> list[dict]:
    """TASKS -> task_factory specs, with the scalar gate dependency applied."""
    specs = []
    for t in TASKS:
        spec = dict(t)
        spec.setdefault("task_type", "build")
        spec.setdefault("priority", "medium")
        spec.setdefault("status", "backlog")
        spec.setdefault("dispatch_source", "manual_seed")
        spec["idempotency_key"] = f"seed:{spec['id']}"
        if spec["id"] != GATE:
            spec["depends_on_task_id"] = GATE
        specs.append(spec)
    return specs


def validate() -> list[str]:
    """Structural checks on the declared graph. Returns a list of problems."""
    problems: list[str] = []
    ids = [t["id"] for t in TASKS]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate task ids: {sorted(dupes)}")
    known = set(ids)

    for task_id, dep_id in EDGES:
        if task_id not in known:
            problems.append(f"edge references unknown task: {task_id}")
        if dep_id not in known:
            problems.append(f"edge references unknown dependency: {dep_id}")
        if task_id == dep_id:
            problems.append(f"self-dependency: {task_id}")
        if task_id == GATE:
            problems.append("the gate must not depend on anything")

    # Cycle detection over the junction graph (the scalar gate dep is a star, not a cycle).
    adjacency: dict[str, list[str]] = {}
    for task_id, dep_id in EDGES:
        adjacency.setdefault(task_id, []).append(dep_id)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(known, WHITE)

    def visit(node: str, path: list[str]) -> None:
        colour[node] = GREY
        for nxt in adjacency.get(node, []):
            if colour.get(nxt) == GREY:
                problems.append(f"dependency cycle: {' -> '.join(path + [node, nxt])}")
            elif colour.get(nxt) == WHITE:
                visit(nxt, path + [node])
        colour[node] = BLACK

    for node in known:
        if colour[node] == WHITE:
            visit(node, [])
    return problems


def seed_edges(dry_run: bool = False) -> dict:
    """Insert missing kanban_task_deps rows. Existing edges are left alone."""
    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables

    # create_tasks() does this too, but --dry-run never calls it — and on a fresh
    # checkout kanban_task_deps does not exist yet, so the existence probe below
    # would raise instead of reporting.
    init_kanban_tables()

    now = datetime.now(timezone.utc).isoformat()
    added: list[str] = []
    existing: list[str] = []

    conn = get_connection()
    try:
        for task_id, dep_id in EDGES:
            label = f"{task_id}<-{dep_id}"
            found = conn.execute(
                "SELECT 1 FROM kanban_task_deps WHERE task_id = %s AND depends_on_id = %s",
                (task_id, dep_id),
            ).fetchone()
            if found:
                existing.append(label)
                continue
            if not dry_run:
                conn.execute(
                    "INSERT INTO kanban_task_deps "
                    "(task_id, depends_on_id, created_at, classification) "
                    "VALUES (%s,%s,%s,%s)",
                    (task_id, dep_id, now, "CUI"),
                )
            added.append(label)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {"declared": len(EDGES), "added": added, "already_present": existing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report without writing anything")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    problems = validate()
    if problems:
        report = {"ok": False, "problems": problems}
        print(json.dumps(report, indent=2) if args.json else
              "REFUSING TO SEED:\n  " + "\n  ".join(problems))
        return 1

    specs = build_specs()

    if args.dry_run:
        report = {
            "ok": True,
            "dry_run": True,
            "tasks": [s["id"] for s in specs],
            "edges": seed_edges(dry_run=True),
        }
    else:
        from tools.kanban.task_factory import create_tasks

        created = create_tasks(specs)
        report = {
            "ok": True,
            "dry_run": False,
            "requested": len(specs),
            "created": created,
            "skipped_existing": [s["id"] for s in specs if s["id"] not in created],
            "edges": seed_edges(),
            "gate": GATE,
            "release_with": f"python -m tools.kanban.cli --set-status {GATE} done",
        }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        edges = report["edges"]
        print(f"tasks declared : {len(specs)}")
        if not args.dry_run:
            print(f"tasks created  : {len(report['created'])} "
                  f"(skipped {len(report['skipped_existing'])} existing)")
        print(f"edges declared : {edges['declared']} "
              f"(added {len(edges['added'])}, already present {len(edges['already_present'])})")
        print(f"gate           : {GATE} — held in_progress until released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
