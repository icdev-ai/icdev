# Plan — DIC Notebook: Auto-Detect Document Domain & Generic Team Launch

## Problem
From `/document-intelligence/notebook`, uploading a non-intelligence file (e.g. `constitution.pdf`) and clicking **Launch Intel Team → Open Team Session** produces a response that is unrelated to the uploaded document. Two root causes:

1. **The launch is hard-coded for intelligence work.** The UI label reads "INTEL TEAM", the role chips always include `intelligence_analyst` + `derivative_classifier`, and the `problem_text` is fixed to *"Produce an Intelligence Report (INTSUM) ..."* regardless of document type.
2. **Selected roles are ignored by the backend.** `/api/ace/launch` receives `role_ids` from the notebook but never forwards them to `ACEController.launch()`, so `ProblemClassifierLens` assembles its own team. The hard-coded prompt also causes poor DIC retrieval because the DIC context query is derived from the prompt text, not the document content.

## Goal
Make the co-worker launch **domain-aware and generic**:
- Auto-detect the document domain from uploaded file titles/content.
- Pick roles that match the domain (intel roles only for IC/intel documents; generic document-analysis roles otherwise).
- Use a domain-appropriate prompt that references the actual collection/document.
- Improve context retrieval so the team gets relevant document snippets.
- Forward the selected role list to the ACE controller so the UI and runtime match.

## Proposed Changes

### 1. Frontend — `tools/dashboard/templates/document_intelligence/notebook.html` (+ `icdev/` mirror)
- Rename section label from **"INTEL TEAM"** to **"Document Team"**.
- Add a client-side `detectDomain(docs)` helper that inspects `doc.title || doc.filename` for domain keywords.
  - IC/intelligence signals: `intsum`, `sitrep`, `osint`, `humint`, `sigint`, `capco`, `bluf`, `threat`, `classified`, `intel`, `intelligence`, `recon`, `spy`, `national security`.
  - Legal/government signals: `constitution`, `law`, `legal`, `statute`, `court`, `contract`, `brief`, `regulation`, `compliance`, `policy`.
  - Medical, financial, corporate, technical, academic signals (mapped similarly).
  - Default domain: `generic`.
- Render role chips dynamically based on detected domain:
  - **intel**: `researcher`, `intelligence_analyst`, `writer`, `editor`, `derivative_classifier`.
  - **legal/government/generic/default**: `researcher`, `writer`, `editor`, `document_classification_specialist`, `document_reviewer`.
  - (Other verticals can extend the map later; the default keeps the team useful for any document.)
- Update `launchIntelTeam()` to:
  - Build a domain-appropriate `problem_text` (e.g. *"Analyze the documents in collection {COLLECTION} and produce a structured summary with key findings, citations, and recommended actions."* instead of a fixed INTSUM request).
  - Pass `context_query: "summarize key topics"` (or a query derived from detected domain) in the `/api/ace/launch` payload so the backend searches DIC with a document-focused query.
  - Continue sending `role_ids`, `dic_collection_ids`, and `trigger_source: 'dic_notebook'`.

### 2. Backend — `tools/ace/blueprint.py` (+ `icdev/` mirror)
- Accept optional `role_ids` in `api_launch()` and forward them to `ACEController.launch(..., role_ids=role_ids)`.
- Accept optional `context_query` in `api_launch()`.
- When `dic_collection_ids` are provided, use `context_query` (if given) for the DIC search instead of `problem_text[:500]`. Fallback to `problem_text[:500]` only when no `context_query` is supplied.
- This fixes both the ignored-role bug and the poor DIC retrieval bug.

### 3. Optional/Incremental — `tools/document_intelligence/blueprint.py`
- No required changes; the collection and document data already feed the notebook page.
- If client-side detection proves too coarse later, add a lightweight server endpoint `/api/collections/<id>/detect-domain` that samples the newest chunk. Out of scope for this plan.

## Files to Modify
1. `tools/dashboard/templates/document_intelligence/notebook.html`
2. `icdev/tools/dashboard/templates/document_intelligence/notebook.html`
3. `tools/ace/blueprint.py`
4. `icdev/tools/ace/blueprint.py`

## Testing Plan
1. **Unit/frontend:** Verify `detectDomain()` maps sample filenames correctly in a temp test.
2. **API test:** POST to `/api/ace/launch` with `role_ids` and `context_query`; confirm the launched instance has the requested roles and that the DIC context search uses the context query.
3. **End-to-end:**
   - Upload `constitution.pdf` in the notebook.
   - Observe that the team section now reads "Document Team" and the chips default to generic roles (no `intelligence_analyst`, no `derivative_classifier`).
   - Launch and open the team session; verify the output references the uploaded document.
   - Upload an Intel-style file (e.g. filename containing `INTSUM`); verify chips switch to intel roles.
4. **Regression:** Confirm `ruff check` clean on modified Python files and that existing ACE launch paths still work.

## Success Criteria
- [ ] Notebook team section label is generic ("Document Team").
- [ ] Role chips adapt to detected document domain.
- [ ] `/api/ace/launch` respects `role_ids` from the frontend.
- [ ] `/api/ace/launch` uses `context_query` for DIC retrieval when provided.
- [ ] Launching a non-intel document (e.g. constitution.pdf) produces a co-worker session whose output is grounded in the uploaded document.
- [ ] No regressions in existing ACE flows.
