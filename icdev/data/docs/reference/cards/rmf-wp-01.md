# WHITEPAPER document type, and template_id made LOAD-BEARING (rmf-wp-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.quality.outline_contract --artifact-type WHITEPAPER       # the declared skeleton, 9 sections
python -m tools.quality.outline_contract --list                            # every template a draft can be built from
```

A library otherwise. generate_document(query, collection_id, template_id="WHITEPAPER")
doc_generator.generate_document ACCEPTED template_id AND NEVER REFERENCED IT
AGAIN -- one grep hit, the signature. Every document got a freeform LLM
outline sliced at [:6] while the twelve skeletons in constants.TEMPLATE_SECTIONS
went unused, and every section was drafted against ONE document-wide
retrieval. Now: template_id resolves through outline_contract.get_contract
(the SAME registry the publish gate validates against, so the skeleton a
draft is built from and the one it is checked against cannot be two lists);
the headings are the contract's, in order, WHATEVER the model returns (its
outline answer is advisory -- title and per-heading summaries only); the
[:6] slice is gone (a declared skeleton is exactly as long as declared; the
freeform path keeps ICDEV_DIC_MAX_FREEFORM_SECTIONS=24 as a runaway guard);
and every section runs its OWN retrieval through the same governed-first
seam, targeted hits first and the document-wide ones after, deduplicated
(ICDEV_DIC_SECTION_RETRIEVAL=0 stands it down). The result RECORDS where the
outline came from -- `outline_source`: contract:<id> | freeform |
freeform:unresolved:<id> -- so a caller that asked for WHITEPAPER and got a
freeform draft can see that. WHITEPAPER is declared in TEMPLATE_TYPES,
TEMPLATE_SECTIONS, TEMPLATE_TYPE_TO_WRITEGUARD_MODE (bluf_exec, a mode
content_modes.py already defines), DOCGEN_DOCTYPE_TO_TEMPLATE and
blueprint._TEMPLATES; outline_contract needed NO edit. The Tech Writer page's
hand-typed {'STANDARD_GUIDE': 9, ...} section-count map is gone -- the route
derives it from TEMPLATE_SECTIONS.
THE CHECK FOLLOWS THE CONSTANT. Migration 20260903100336 REBUILDS
dic_documents.template_type's CHECK from TEMPLATE_TYPES (a Python migration,
never a respelled list). Measured 2026-09-03: the live PG table carried NO
such constraint at all -- migration 230's inline CHECK never landed there --
so on PG this ADDS one. SQLite is a stated no-op: a SQLite database that ran
230 keeps the six-name inline CHECK and refuses a WHITEPAPER row on INSERT.

TWO COMPLIANCE AUDIT EVENTS WERE WRITTEN AND NEVER ADMITTED (same card).
oscal_generator._log_audit has always written `oscal_generated` and
cato_monitor._log_audit_event `cato_evidence_collected`; neither was in
tools/audit/audit_logger.py::VALID_EVENT_TYPES, so the CHECK refused EVERY row
and each writer's `except Exception: print(..., file=sys.stderr)` sent the
refusal to a stream nothing reads while the generator returned success.
Measured on the live PG board AND on a fresh SQLite database: every OSCAL
artifact ever generated and every piece of cATO evidence ever collected is
unaudited, under NIST AU, on the two chains an ATO package is assembled from.
Migration 20260902235404 rebuilds the constraint from the tuple.
