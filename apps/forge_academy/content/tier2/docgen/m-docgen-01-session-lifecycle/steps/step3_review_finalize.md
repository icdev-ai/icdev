---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:3
step_class: icdev:verify
---
# Review and Finalize

Once WriteGuard has passed, the session can advance to stage 7 (`reviewing`). A human reviews the draft on the dashboard at `/docgen/<session_id>/review`, which shows the session's analyses and any remediation diagrams next to the draft.

## Publish and collect the artifacts

```bash
# Stage 8: export the server-side validated document to HTML + PDF (+ DOCX)
POST /docgen/api/sessions/<session_id>/publish
{"title": "ICDEV Training Platform SSP", "classification": "CUI"}

# List what was produced, then download one
GET /docgen/api/sessions/<session_id>/artifacts
GET /docgen/api/sessions/<session_id>/artifacts/<artifact_id>/download
```

Two things about publish are worth noticing:

- **It publishes only the validated text.** A `doc_text` in the request body is ignored, so nobody can pass clean text through WriteGuard and then publish different bytes.
- **TRUST gates run at publish.** Placeholder, citation and claim defects block the export. A reviewer can override with `force_citations` / `force_placeholders` / `force_claims`, but only with a `force_reason`, and every override writes an append-only audit row.

If a section needs rework there is no per-section rewrite call: send the session back with `POST /docgen/api/sessions/<session_id>/advance` and an earlier `stage`, or regenerate with new `supplemental_text`.

## Your task

For your SSP session, decide what you would check on the review page before publishing (name at least three things), and what you would do if publish returned a citation defect: fix the source, or override? If you would override, write the `force_reason` you would be willing to sign.
