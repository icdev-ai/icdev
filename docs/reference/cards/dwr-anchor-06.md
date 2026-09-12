# A suggestion drafted against a TOKEN is retired, never back-filled (dwr-anchor-06)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.document_intelligence.suggestion_redraft --census        # by status x anchor_basis
python -m tools.document_intelligence.suggestion_redraft --plan [--json] # probe every target; ACTS ON NOTHING
python -m tools.document_intelligence.suggestion_redraft --apply --limit 5   # THE ONLY FLAG THAT WRITES
```

`draft_redline` built its prompt from `finding["entity_label"]` -- a bare token
like `TLS 1.1` -- and stored `section_id=""` with `anchor_basis="unanchored"`.
MEASURED 2026-09-08 on the live PG board: 58 pending dic_suggestions, 58 with an
empty section_id, 58 with a NULL basis, all canvas_source=doc_modernization.
THEY CANNOT BE REPAIRED IN PLACE. The prose was written without the model ever
seeing the surrounding sentence, so back-filling an anchor pins text to a span
it was never fitted to -- a plausible-looking edit nobody wrote. Each is
SUPERSEDED (`supersede_suggestion`, dwr-anchor-05, reused -- this module never
UPDATEs dic_suggestions itself, pinned by AST) and its finding re-drafted
through the UNCHANGED TRUST gate chain.
IT PROVES BEFORE IT ACTS, and nothing is superseded for a draft that
structurally cannot happen -- trading 58 misleading proposals for 58 absent
ones is not progress. FIVE preconditions, each asked of primary data and each
sending a reader to a DIFFERENT repair:
  drafter_does_not_anchor  the installed redline_drafter has no
                           `resolve_passage` (it predates dwr-anchor-04) and
                           would mint another unanchored row. Refused for the
                           WHOLE run, never per item.
  origin_unresolved        two independent routes to the finding --
                           docmod_findings.redline_suggestion_id ->
                           supersedes_id (structured, asked first) and the
                           `[docmod:<id>]` rationale prefix. A DISAGREEMENT is
                           unresolved, never a pick between two.
  origin_not_open          draft_redline only drafts an `open` finding, and the
                           ORIGIN row is open while its redline_drafted
                           SUCCESSOR is not -- which row is returned is
                           load-bearing.
  finding_has_no_span      THE FIX IS A RE-SCAN. dwr-anchor-01/02 made the
                           packs record a span; a finding written before that
                           has none and no re-draft can invent it.
  doc_has_no_sections      THE FIX IS section_deriver (dwr-sect-01).
MEASURED, AND THE MEASUREMENT IS THE OUTCOME: on this board today the tool
reports 58 REFUSALS AND ZERO TOKENS SPENT. All 58 resolve through the
structured route and all 58 origins are open -- and 127 of 127 docmod_findings
carry a NULL span, while the document holding 47 of the 58
(dic_doc_28e2ee4d984f3f35) carries ZERO dic_sections rows. Re-probed with
anchors=True, i.e. as if dwr-anchor-04 were installed, all 58 still refuse on
`finding_has_no_span`. That is not a clean bill of health. DO NOT relax a
precondition to make the sweep do something -- each one is what stops it
minting 58 fresh copies of the defect it exists to retire.
CONFIRM IS A RE-READ, never the drafter's claim: the new row must carry an
APPLIABLE basis AND a section, and a row failing that is `redrafted_unanchored`
-- reported loudly, never counted as a success. THE LOOP IS CLOSED BY
CONSTRUCTION and not by a visited-set: an anchored row is not a target, and
`validate_anchor` refuses to write an exact/relocated basis with no section, so
a written row is anchored or was never written.
Bounded by `max_redrafts_per_run` (args/docmod/docmod_config.yaml, 10) with
deferred items NAMED. Every outcome counted -- redrafted | abstained | blocked |
error | supersede_refused | redrafted_unanchored -- because successes alone
cannot say whether the sweep worked. Exit 2 = the survey could not be produced.
Survey: docs/audits/dwr-anchor-06-unanchored-suggestion-survey.md
