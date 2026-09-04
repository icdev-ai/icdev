# rmf-wp-02 — Export for DIC, and TRUST rails on whitepaper prose

**Classification:** CUI // SP-CTI
**Card:** rmf-wp-02 (project `rmf`, epic `wp`)

## What was wrong

Three things, and the card named all three:

1. **DIC had no export route.** A version could be drafted, reviewed, approved
   and annotated, and the only way its prose left the canvas was copy-paste
   from the page, which passes every TRUST gate this canvas enforces at
   approval by never touching one. docgen has had the right shape since
   cnr-doc-01: a `citation_publish_gate`, a WriteGuard gate that must have
   passed, then one `idr_artifacts` row per exported format.
2. **WriteGuard never ran on a DIC document.** docgen blocks publish on
   `run_full_quality_check`; DIC's only WriteGuard touch point was a
   per-section sidebar and a `writeguard_mode` column nothing gated on.
3. **Proposal prose reached a cloud model unredacted on the Chain-of-Debate
   and Chain-of-Thought paths.** The card said `sanitize_for_llm` was wired
   only into `response_drafter`. Half right. `LLMRouter.invoke` has run
   `_pre_invoke_redaction` on every call since D-RDT-1, and `cortex.complete`
   reaches `invoke`, so the single-shot paths in `rfi_workbench` and
   `doc_generator` were covered. What was not covered was
   `LLMRouter.invoke_for_role`, the method `ChainOrchestrator` hands every
   reasoner, critic, debater and judge step to. It resolved a role chain,
   ranked it and called the provider with the raw text. `rfi_workbench`'s
   `_generate_draft` (CoD for the judgment sections) and `doc_generator`'s
   `_cot_generate` / `_cod_compress` all go that way.

## What shipped

### The export (`tools/document_intelligence/exporter.py`)

`GET /document-intelligence/api/versions/<id>/export/<fmt>` with
`fmt` in `md | html | docx | pdf`. Three gates run, in this order, and every
one fails closed:

| Gate | Source | On defect |
|------|--------|-----------|
| `placeholder_guard` | `consistency_checker.check_version_consistency` (the shared `placeholder_findings`) | 409, or `force_placeholders` + reason |
| `citation_guard` | `consistency_checker.check_version_citations` (the shared `citation_gate`, AI-authored sections against the evidence recorded for them) | 409, or `force_citations` + reason |
| `writeguard` | `tools.pulse.writeguard.run_full_quality_check` over the **assembled** document | 409, or `force_writeguard` + reason |

The first two are the same gates the approve route runs, so an export can
never be laxer than an approval. A gate that could not measure (a DB error
under the section read, WriteGuard unimportable or raising) is reported under
`unmeasured`, blocks, and no `force_*` flag opens it. A force without a
`force_reason` is a 400. Any force needs the `reviewer` role; a plain export
needs `editor`.

Overrides are audited **before the file is written**: TRUST guards to the
append-only `idr_publish_audit` (its CHECK admits `PUBLISH_GATES` only), and
the decision itself as a fail-closed `dic.hitl_decision` event with action
`dic_version.export_forced`. If the audit write raises, nothing is written.

One `dic_artifacts` row per export (migration `20260903194350_dic_artifacts`,
mirroring `idr_artifacts`): the file's sha256 and size, the WriteGuard score
and verdict, the full gate report, `forced` + `force_reason`, `exported_by`,
and `version_status` **at export time**. Export does not require `approved`
(a draft exported for offline review is legitimate), so the artifact says what
it was. `GET /api/versions/<id>/artifacts` lists them;
`GET /api/artifacts/<id>/download` streams one, with a cross-tenant guard.

Renderers: `md` (the assembled markdown with the classification label top and
bottom), `html` (docgen's sanitised renderer, escaped title), `docx`
(`rfi_docx_exporter.markdown_to_docx`, the exporter rmf-docx-01 proved works,
with the classification **label** as the header/footer marking rather than its
hard-coded FOUO default), `pdf` (only when fpdf2 is installed; without it
`pdf_export` writes HTML under a `.pdf` name, cnr-doc-04, so the format reports
`unavailable` instead). Export buttons sit beside the version strip on the
document page; a 409 names the gate and offers the audited override.

### The redaction seam (`tools/llm/router.py`)

PR #2028 (`fix(llm): invoke_for_role runs the same redaction gate as invoke`)
merged while this card was in flight and closed exactly this hole:
`invoke_for_role` now runs the same `_pre_invoke_redaction` /
`_post_invoke_deanonymize` pair `invoke` runs, the local-only skip is judged
on the **role** chain the request travels (`chain_key`), and
`_invoke_model_direct` redacts a request that `invoke` has not already marked,
so the two-tier hop is never sanitized twice. This card arrived at the same
diagnosis independently, adopted #2028's router verbatim on merge, and pins the
**consumers** instead: an AST sweep asserts every LLM dispatch in
`rfi_workbench` and `doc_generator` is `router.invoke`, `cortex.complete` or a
`ChainOrchestrator` entry, never a provider or `_invoke_model_direct` call that
would step around the seam.

## Verification

- `tests/document_intelligence/test_export.py`: each gate refuses with the
  right name and writes nothing; WriteGuard sees the whole assembled document
  and never runs on a draft already refused; a clean version produces a real
  `.docx` (zip with `word/document.xml`, CUI marking, no FOUO), a row whose
  sha256 matches the file, and a download; forces without a reason are 400,
  with a reason mark the row and write both audit rows; the migration's
  `format` CHECK and the runtime DDL are rendered from `EXPORT_FORMATS`.
- `tests/llm/test_role_invoke_redaction.py`: the AST sweep over
  `rfi_workbench` and `doc_generator`, plus a structural belt that
  `invoke_for_role` still carries the pre/post pair. The router doors
  themselves are pinned by #2028's `tests/test_invoke_for_role_redaction.py`.
  Exempt from the red-first gate with a written reason: it asserts a property
  main already satisfies, by design.

## Not done, on purpose

- The claim tier (`claim_guard`) is not run at export. DIC's `citations_json`
  records chunk ids, not source texts, so the claim guard would be
  `unmeasurable` on every section; the drafting profile records that as a
  warning, which is what the approve route already gets. Wiring source texts
  through is a separate card.
- Export does not require `approved`. The artifact records the status instead.
- `pdf` is available only where fpdf2 is; the deployment this was built on has
  it, CI may not, so the docx path is what the tests pin.
