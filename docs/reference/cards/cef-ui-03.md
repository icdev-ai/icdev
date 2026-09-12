# HITL approve/reject for a resolve-produced proposal — EXISTING routes (cef-ui-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

No CLI and NO NEW ROUTE. The decision surface is frozen at the eight routes
`tests/docmod/test_hitl_decision_wiring.py::_DECISION_ROUTES` enumerates; a
ninth fails that test.
  POST /document-intelligence/api/modernization/findings/<id>/resolve
       {"disposition": "accepted"|"rejected", "reviewer": "...", "note": "..."}
  POST /document-intelligence/api/suggestions/<id>/accept | /reject
  POST /document-intelligence/api/review/<id>/approve | /reject

A REDLINE PROPOSAL HAS TWO IDENTITIES AND THEY WERE NEVER CONNECTED. The
TRUST-gated drafter writes a `docmod_findings` state row (the DISPOSITION
door, `/resolve`) AND a `dic_suggestions` row (the APPLY door,
`/api/suggestions/<id>/accept`, the only writer of `dic_sections.content`).
Measured on the live PG board 2026-08-18: 49 findings in `redline_drafted`,
49 suggestions all `pending`, and ZERO rows in `dic_suggestion_decisions` —
nobody had ever decided anything, and rejecting a finding left its proposal
fully applyable through the other door.

THE CASCADE IS ASYMMETRIC, ON PURPOSE — it only ever REMOVES an apply
capability. `rejected` also rejects the linked pending proposal through the
existing `decide_suggestion` seam, so a declined redline can never afterwards
be applied. `accepted` leaves the proposal PENDING and returns
`proposal.apply_url`: accepting a FINDING means "yes, this document is
stale", NOT authorisation to write LLM prose into the document. Auto-applying
there is exactly the "never auto-apply" prohibition. A cascade that fails is
reported as `proposal.cascade_failed`, never silently.

THE APPLY DOOR RECORDS THE DECISION BEFORE IT TOUCHES THE DOCUMENT. It used
to UPDATE `dic_sections` first and call `decide_suggestion` afterwards
WITHOUT CHECKING ITS RETURN — so a failed (or already-decided) decision left
drafted prose in the document with no recorded human decision, silently. The
order is now decision -> audit -> apply, each fail-closed, and the response
says `decision_recorded` / `applied` separately so the residual case (decided,
apply failed) is visible rather than guessed at.

ACCEPT AND REJECT ARE BOTH AUDITED, at every door: one event type
`dic.hitl_decision` with the surface namespaced in `action`
(`docmod_finding.accepted`, `dic_suggestion.rejected`, `dic_version.approved`,
`dic_ssp_fragment.approved`), following the `migration_canvas` precedent
rather than six types. `_record_hitl_decision` in blueprint.py is the one
writer; it is FAIL-CLOSED (`raise_on_error=True`) and called BEFORE the
mutation it authorises, because `dic_suggestions` / `dic_versions` hold only
the CURRENT status and an unaudited approval leaves no evidence of who
decided. A surface that records only its POSITIVE outcome can answer "was
this approved?" but never "was this reviewed?" — hence both legs.

TWO PRE-EXISTING DEFECTS FIXED ON THE WAY, and they are the same defect:
`acoic._review_fragment` has written every human SSP-fragment decision under
`dic.ssp_fragment.review` since it was authored, and that name was NEVER in
`VALID_EVENT_TYPES` — so `log_event` raised `ValueError` on its first line,
before touching the database, on EVERY approval. It passes
`raise_on_error=True` precisely so an unaudited approval cannot stand; and
`blueprint.py`'s `except Exception:` fallback caught that refusal and ran the
UPDATE anyway, unaudited. Both names are admitted by migration
20260819021003, and the fallback now records the decision itself (the broad
`except` stays — narrowing it would change how an unrelated legacy-schema
failure is handled, which is not this card's business). There is no longer a
branch through these routes that approves without an audit row.

The redline drafter is UNTOUCHED — the TRUST chain (hallucinated citation =>
hard block, out-of-candidate replacement => hard block, reasoning-residue
scrub, confidence bands, provenance) still runs upstream of every door, so a
blocked draft never becomes a `dic_suggestions` row and there is nothing to
approve. Asserted, not assumed.
NOT wired, on purpose: `/api/sections/<id>/approve|reject` (section-level
editing state, not a proposal) and `/api/modernization/claims/<id>/reject`
(a claim, which has no drafted replacement to apply).
