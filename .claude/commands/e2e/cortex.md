# E2E Test: ICDEV Cortex Canvas

Verify the unified **Cortex** canvas at `/cortex` renders correctly and honors the
user-visible TRUST contract: grounded search answers with inline citations, the
Cortex Analyst data path, the security-mode triage path, and inline IQE queries.

Cortex is the platform's Snowflake-Cortex-style unified AI layer — a single facade
over the RAG / knowledge-graph / document-intelligence / keyword-KB backends plus
the GovernancePipeline (anti-hallucination, provenance, redaction). Every answer
fragment must be traceable to a source row; uncited content is suppressed, never
shown. This spec drives the real page in a browser and asserts that contract.

## Prerequisites
- Flask dashboard running on `http://localhost:5050`
- `ICDEV_CORTEX_ENABLED=true` in `.env` (the canvas is gated behind this toggle;
  when unset the nav link is hidden and `/cortex` returns 404)
- Database initialized (Cortex governance/audit tables present; at least one RAG
  source ingested so search returns grounded evidence)
- A valid dashboard API key for login
- LLM router reachable (or air-gap local tier configured) so Cortex can synthesize
  answers; `classify()` degrades to deterministic heuristics offline

> **Screenshot / outputDir lesson (repo rule):** save browser screenshots as
> `playwright/screenshots/cortex-<scenario>.png`. When driven by the native
> Playwright runner, NEVER set `outputDir` to the `playwright/screenshots` root —
> it is wiped each run; point `outputDir` at an `artifacts/` subdir instead.

> **Failure handling (acceptance):** on ANY failed assertion, capture a full DOM
> snapshot (accessibility tree / `page.content()`) and a screenshot named
> `playwright/screenshots/cortex-<scenario>-FAIL.png` BEFORE aborting, so the
> failure is actionable (e.g. a 404 body proves the canvas route is not wired).

## Scenario 1 — Load /cortex (page renders, nav + IQE mini-bar)

### Login
1. Navigate to `http://localhost:5050/login`
2. Fill the API key field with the test API key
3. Click "Login" and assert redirect to the home page (`/`)

### Nav link
4. On the home page, assert a navigation link to the Cortex canvas is present
   (link text contains "Cortex", href resolves to `/cortex`)
5. Click the "Cortex" nav link (or navigate to `http://localhost:5050/cortex`)

### Page render + CUI banners
6. Assert the response is HTTP 200 (NOT 404) and no traceback text is present
7. Assert the page title or main heading contains "Cortex"
8. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the page
9. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the page
10. Assert the primary Cortex ask/search input box is visible and enabled
11. Assert the mode/scope control is visible (e.g. a search ⇄ analyst ⇄ security
    selector or equivalent toggle)

### IQE mini-bar
12. Assert the IQE query mini-bar is visible on the page (the shared
    `includes/iqe_query_widget.html` widget — an inline query field with a run
    button and, if seeded, example query chips)
13. Screenshot the loaded canvas as `playwright/screenshots/cortex-load.png`
14. Check the browser console — assert no JavaScript errors on load

## Scenario 2 — Search-shaped message → grounded answer with citations

15. Focus the Cortex ask/search input
16. Type a retrieval-shaped question, e.g.
    "What are the FedRAMP AC-2 account management implementation patterns?"
17. Submit (click the ask/send button or press Enter)
18. Wait for the answer to render (answer text block appears, or an explicit
    "no grounded evidence" message)
19. If an answer renders, assert it carries inline citations — at least one
    `[source: …]` marker in the answer text OR a citation list/footnote area with
    at least one entry exposing a source id + source type
    (`rag_chunk` / `kg_node` / `dic_document` / `kb_entry`)
20. Assert a grounding/confidence badge is visible on the answer, reflecting the
    Cortex TRUST contract — one of:
    - a **grounded** / **ungrounded** indicator (from `CortexResult.grounded`), and/or
    - a **confidence** badge reading include / flag / abstain (from
      `metadata.confidence`)
21. Assert that when the answer is marked ungrounded / abstain, it is visually
    flagged (not presented as evidence-backed prose)
22. Screenshot the grounded answer as `playwright/screenshots/cortex-search.png`

## Scenario 3 — Data question → Cortex Analyst answer with analyst citations

23. Switch the canvas to the analyst/data path (select the "Analyst" mode/scope,
    or ask a clearly data-shaped question that routes to the analyst)
24. Type a data question, e.g.
    "How many open kanban tasks are there grouped by status?"
25. Submit and wait for the analyst answer to render
26. Assert the analyst answer surfaces a structured result — a rows/records
    area or summary of `row_count` (the analyst returns
    `data = {rows, row_count, iqe}`)
27. Assert the underlying IQE / query text is shown or inspectable (the `iqe`
    the analyst executed), demonstrating provenance of the numbers
28. Assert analyst citations are present — the answer is traceable to its source
    rows/tables, not free-form prose
29. Assert a confidence badge (include / flag / abstain) is shown on the analyst
    answer; if the analyst abstains, assert the abstention message renders and no
    fabricated numbers are shown
30. Screenshot the analyst answer as `playwright/screenshots/cortex-analyst.png`

## Scenario 4 — Security mode → threat query → triage summary + scoped sources

31. Toggle the canvas into **security mode** (the security-scoped mode/scope control)
32. Assert the security-mode state is visibly active (mode indicator/badge changes)
33. Type a threat-shaped query, e.g.
    "Summarize the active exploited CVEs affecting our container base images and
    their triage priority."
34. Submit and wait for the response to render
35. Assert the response is a triage-style summary (prioritized / severity-ordered
    finding list or a triage summary block — NOT a generic chat reply)
36. Assert the sources are scoped to the security domain — citations point to
    security-relevant sources (CVE / advisory / scan / threat-intel rows) rather
    than unrelated general-knowledge chunks
37. Assert the grounding/confidence badge is still present and honored in security
    mode (the GovernancePipeline TRUST gates run in every mode)
38. Screenshot the security triage answer as
    `playwright/screenshots/cortex-security.png`
39. Toggle security mode back off and assert the canvas returns to the default
    search scope

## Scenario 5 — IQE widget: run one seed query inline

40. Scroll to / focus the IQE mini-bar widget on the canvas
41. If seeded example query chips are present, click the first one; otherwise type
    a seed query into the IQE field (e.g. "cortex answers grounded true last 7 days")
42. Run the IQE query (click the widget's run button or press Enter)
43. Wait for the inline IQE result panel to populate (a results table/summary, or
    an explicit "no results" message — assert it renders WITHOUT a 500/traceback)
44. Assert the IQE result renders inline on the canvas (does not navigate away)
45. Screenshot the IQE result as `playwright/screenshots/cortex-iqe.png`
46. Check the browser console again — assert no JavaScript errors accumulated
    across the session

## Expected Results
- `/cortex` loads with HTTP 200 (gated on `ICDEV_CORTEX_ENABLED=true`); a nav link
  reaches it and the IQE mini-bar is present
- Search-shaped questions return answers with inline `[source: …]` citations and a
  grounded/confidence badge; ungrounded/abstain answers are visibly flagged
- Data questions route to the Cortex Analyst and render structured rows/row_count
  with the executed IQE and analyst citations; abstentions never show fabricated data
- Security mode yields a triage-style summary scoped to security sources, with TRUST
  badges still enforced
- The IQE widget runs one query inline and renders results without errors
- No JavaScript console errors across the session
- Every failed assertion produces an actionable DOM snapshot + `-FAIL` screenshot

## CUI Verification
- Top banner displays "CUI // SP-CTI"
- Bottom banner displays "CUI // SP-CTI"
- Canvas content renders between the banners on every view

## Screenshots
- `playwright/screenshots/cortex-load.png`
- `playwright/screenshots/cortex-search.png`
- `playwright/screenshots/cortex-analyst.png`
- `playwright/screenshots/cortex-security.png`
- `playwright/screenshots/cortex-iqe.png`
