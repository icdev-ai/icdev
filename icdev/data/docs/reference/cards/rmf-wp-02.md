# A DIC version leaves the canvas through ONE gated door, and CoT/CoD prose is redacted (rmf-wp-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.document_intelligence.exporter import export_version, export_gate, EXPORT_FORMATS
  out = export_version(version_id, "docx", exported_by="alice")   # {artifact, gate}
Route: GET /document-intelligence/api/versions/<id>/export/<fmt>  (md|html|docx|pdf)
       GET /document-intelligence/api/versions/<id>/artifacts
       GET /document-intelligence/api/artifacts/<id>/download
DIC HAD NO EXPORT ROUTE. Its prose left the canvas by copy-paste, which passes
every TRUST gate the approve route enforces by never touching one. docgen has
had the right shape since cnr-doc-01 (TRUST gate -> WriteGuard gate ->
idr_artifacts); this is that shape on DIC's own tables. THREE GATES, IN ORDER,
EVERY ONE FAIL-CLOSED: placeholder_guard and citation_guard are the SAME
consistency_checker gates the approve route runs (the shared
placeholder_findings / citation_gate -- never a second copy), then WriteGuard
(run_full_quality_check) over the ASSEMBLED document -- docgen blocks publish
on it, DIC never called it. A gate that could not MEASURE is `unmeasured`,
blocks, and NO force_* opens it. A measured defect needs the matching flag AND
a non-empty force_reason (400 without), the reviewer role, and is audited
BEFORE the file exists: TRUST guards to idr_publish_audit, the decision as a
fail-closed dic.hitl_decision `dic_version.export_forced`. One dic_artifacts
row per export (migration 20260903194350): sha256, WriteGuard score, the full
gate report, forced/force_reason, and version_status AT EXPORT TIME -- export
does not require `approved`, so the row says what it was. docx is
rfi_docx_exporter.markdown_to_docx with the classification LABEL as the
marking, never its FOUO default; pdf only where fpdf2 is installed.
THE SANITIZER HALF, and the card's stated cause was half right: `invoke` has
run _pre_invoke_redaction since D-RDT-1 and cortex.complete reaches it, so the
single-shot rfi_workbench / doc_generator paths were covered. `invoke_for_role`
-- what ChainOrchestrator hands EVERY CoT/CoD step to -- was not, so a
debater or reasoner received the raw prompt. #2028 closed that seam while this
card was in flight (the same pre/post pair, the local-only skip judged on the
ROLE chain via `chain_key`, and `_invoke_model_direct` redacting a request
that `invoke` has not ALREADY marked, so the two-tier hop is never sanitized
twice). This card independently arrived at the same diagnosis, adopted #2028's
router verbatim, and pins the CONSUMERS: tests/llm/test_role_invoke_redaction.py
sweeps rfi_workbench and doc_generator by AST so every LLM dispatch there is
`router.invoke`, `cortex.complete` or a ChainOrchestrator entry -- never a
provider or `_invoke_model_direct` call that would step around the seam.
