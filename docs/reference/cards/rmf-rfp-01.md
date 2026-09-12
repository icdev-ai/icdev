# The RFP shredder is WIRED, and there is ONE compliance matrix (rmf-rfp-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --ingest solicitation.pdf --json
python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --coverage --json
python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --gate --json
```

Routes: POST /rfp/upload (multipart rfp_file, optional opportunity_id) and
POST /api/proposals/opportunities/<id>/compliance/batch with {"parsed": ...,
"section_text": {"L"|"M"|"C": ...}} beside the unchanged {"items": [...]}.
solicitation_parser.py extracted Section A-M, L.x instructions, M factors,
volumes and CLINs and NOTHING consumed it but response_drafter: no route, no
UI. compliance_matrix_builder.py had ZERO callers. Now an RFP upload seeds
ONE WORKBENCH SECTION PER SECTION L INSTRUCTION (rfi_workbench._rfp_section_rows,
never the RFI questionnaire defaults -- seeding those for an RFP fabricates
what the RFP asks for; a parse with no L items and no volumes seeds nothing
and says `parse_fallback`), and with an opportunity_id it populates that
opportunity's L/M/C matrix through build_from_parsed().
TWO MATRICES WAS THE DEFECT. proposal_compliance_matrix is what the
/api/proposals compliance routes, the auto-populate route, the detail pages
and the IQE adapter read and write -- 499 rows on the live board 2026-09-03.
pg_compliance_matrix held 0 rows (its only writer had no callers) while
opportunity_lifecycle's MAP->DRAFT gate, color_review_simulator,
program_bridge's CDRL gatherer and the proposal_genesis bridge/trace reflexes
ALL computed coverage over it: "No compliance matrix entries found" for
opportunities with hundreds of rows in the other table. Migration
20260903185253 adds evaluation_factor / evaluation_weight / amendment_version
to the survivor, widens its requirement_type CHECK to the builder's sources
(C, attachment, amendment), copies any legacy rows across (status mapped:
addressed->compliant, gap->not_addressed, na->not_applicable) and DROPS the
second table. Both CHECKs and the fold derive from ONE module,
tools/govcon/compliance_matrix_schema.py, and
tests/govcon/test_rmf_rfp_one_matrix.py refuses any runtime SQL that names
pg_compliance_matrix again. Apply it on a live PG board with
`python tools/db/migrate.py --up`; SQLite keeps its old CHECK (a CHECK cannot
be ALTERed there) and a fresh SQLite database derives the new one from init.
