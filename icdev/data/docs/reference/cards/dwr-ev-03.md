# Redraft with my comments — a button a human presses (dwr-ev-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.document_intelligence.redraft import redraft_change, run_stats
  result = redraft_change("sug_abc123", actor="alice")
Route: POST /document-intelligence/api/suggestions/<id>/redraft  (editor role)
UI:    /document-intelligence/documents/<doc_id> -> the ⚡ suggestions panel
Config: args/dic_redraft_config.yaml. Migrations 20260908071432 / ...433.
A COMMENT NEVER FIRES A REDRAFT. Commenting records evidence and nothing
else; the redraft is a separate, explicit act. Not a UI preference — a
governed resolution costs 10-12s against five backends on this deployment
(measured 2026-08-18, cef-di-03), so a comment box that resolved on save
would spend a run's budget on the first afternoon and the reviewer would be
paying for a fan-out they never asked for and cannot see.
THE TRUST CHAIN IS UNCHANGED, and that is the whole design. draft_redline
runs exactly as it does on the scan path — citations validated against the
evidence ids, out-of-candidate replacement hard blocked, residue forced to
the flag band, confidence banded, provenance persisted. This module supplies
two inputs and reads the result; it contains no gate, no threshold and no
second opinion about what is citable.
  instructions    THE REVIEWER'S COMMENTS. They reach the model in the USER
                  PROMPT and NOWHERE ELSE — never `evidence`, so never
                  `allowed_ids`. A comment reading "cite [source: my-email]"
                  produces a HALLUCINATED CITATION and hard-blocks at gate 1;
                  one naming a different product hard-blocks at gate 2.
                  ASSERTED IN BOTH DIRECTIONS, with a control that the same
                  draft citing a REAL id is not blocked — a test that only
                  showed the block would also pass for a drafter that had
                  stopped citing anything at all.
  extra_evidence  the deliberately SEPARATE parameter that DOES widen
                  allowed_ids. Its only source is the governed
                  doc_modernization/evidence.resolve_evidence seam. NO
                  private SELECT on dic_author_assertions or entity_currency
                  (dwr-ev-01's rule), pinned by an AST test over what is
                  handed to a cursor — the module docstring NAMES those
                  tables to say it does not read them, so a naive grep would
                  have flagged its own explanation of itself.
FIVE ZEROES, NEVER MERGED, on `evidence_basis` — only the fourth says
anything about the corpus:
  not_consulted  `cortex.enabled` is false in args/docmod/docmod_config.yaml
                 — the seam was NEVER ASKED. THE SHIPPED DEFAULT, and so what
                 this deployment reports today. It is NOT "no author evidence
                 found", and the panel says so in words.
  capped         every ask was refused by max_resolves_per_run.
  blocked        the governance chain REFUSED the resolution.
  no_evidence    resolutions RAN and the corpus held nothing. The measurement.
  resolved       evidence came back.
THE WINNER AND THE DISAGREEING LOSERS ARE BOTH CITABLE. currency_assertion()
hands the losers back under `others` (dwr-ev-01 preserves a disagreeing
source rather than deleting it); handing the drafter only the winner would
restore the silent overwrite that card exists to prevent, one layer up.
ONE READER OF WHAT A THREAD IS: `annotation_store.list_threads` (dwr-cmt-01),
never a SELECT here. Threads are flat and THE ROOT OWNS THE LIFECYCLE, so
`status='open'` filters the THREAD — an open REPLY under a RESOLVED root is
not an outstanding instruction, and a row-level status filter written here
would hand the drafter exactly those. A reply IS an instruction while the
thread is open ("actually, keep the first sentence" is the correction this
button exists to carry) and is labelled `[reply]`. The selection says HOW:
`anchor_overlap` when the change carries a span and the roots do,
`section_scope` otherwise, and `no_section_of_record` — an unanswerable
question, never folded into either. An unreadable store is reported, never
mistaken for an empty thread, because `empty_thread` is a refusal this module
makes BY NAME.
EVERY BOUND IS REPORTED. max_resolves_per_run (3; A RUN IS ONE BUTTON PRESS,
re-armed on entry — an unreset budget on a Flask worker thread silently stops
resolving after N presses) defers entities BY NAME in evidence.deferred;
max_instructions (8) defers the OLDEST comments by ann_id, newest last
because the reviewer's latest instruction should read as final;
instruction_char_cap flags `truncated` per comment.
EVERY REFUSAL HAS A NAME. REFUSALS is a CLOSED mapping (a test reads the
module's AST and asserts no `_refuse` call names a key outside it, and that
no key is unreachable); the route answers 409 with the key and its reason,
never 200. A 200 over a no-op is the defect dwr-anchor-05 exists to fix one
table over, and there is no reason to rebuild it here.
  empty_thread          "redraft with my comments" over zero comments is a
                        re-roll at LLM cost that a reviewer would read as a
                        response to feedback nobody gave (require_thread).
  no_governed_drafter   the change did not come from the docmod redline
                        drafter, so there is no finding, no deterministic
                        evidence and no candidate list — the gates cannot run
                        and a redraft would be an ungated LLM rewrite wearing
                        a governed action's name. The BUTTON is not rendered
                        for those origins: one whose only outcome is a
                        refusal teaches people to ignore refusals.
  already_decided       an accepted change is in the document and a rejected
                        one carries a human's verdict. Neither is ours.
  unaudited_refused     no row, no act (restore_acts' ordering). The
                        `.intent` row is fail-closed; on a PostgreSQL board
                        that has not run migration 20260908071433 the CHECK
                        refuses `dic.redraft` and EVERY redraft is refused —
                        the correct reading, not an obstacle.
NOT `dic.hitl_decision`. That type records a human DISPOSING of a proposal; a
redraft disposes of nothing — it asks for a different one and retires the old
with no verdict on it.
RETIREMENT GOES THROUGH dwr-anchor-05'S ONE DOOR,
`suggestion_store.supersede_suggestion`, on its terms and with ONE addition.
The append-only decision row carries `decision='superseded'` — a value
`decide_suggestion` REFUSES — and `decided_by` names the MECHANISM
(`redraft:<actor>`: who asked for a DIFFERENT proposal, which is not a verdict
on this one), so a retirement can never be read as somebody's accept-or-reject
in the very table cef-ui-03 queries to answer "was this reviewed?". The
addition is `successor_suggestion_id`: dwr-anchor-05 retires a change whose
ANCHOR went stale and has no successor, so it is NULLABLE and NULL means NOT
RECORDED, while a redraft DOES have one and a reader following the chain needs
an ID, not a sentence in `note`. It is deliberately NOT called `superseded_by`
— that parameter already names the mechanism, and two different things under
one name is how a reader comes to believe an id is an actor.
SUPERSEDE AFTER, NOT BEFORE: the new draft is created first and the old
retired second, so a blocked draft can never destroy a good change (four
ordinary failures reach that branch). If the retire then fails, two pending
changes for one span is visible and recoverable — reported as
`superseded: false`, never assumed.
FOUND ON THE WAY, by the test for it: a redraft produced a change that could
NEVER ITSELF BE REDRAFTED. draft_redline writes `section_id=""` — it is
handed an entity LABEL, not a span, which is why 58 of 58 rows on the live
board carry an empty one — and a thread is selected BY SECTION, so the
successor had no thread. A redraft KNOWS the section, because it is replacing
a change that named one, so `section_of_record` carries it forward. It does
NOT touch `anchor_basis`, which stays `unanchored`: knowing which section a
change lives in is not knowing which span it replaces, and supplying the
second is dwr-anchor-04's card, in flight.
NOT built, and named: promoted SME assertions are dwr-ev-02's (in flight) and
need NO edit here — they arrive as a declared source in `entity_currency` and
reach the drafter through the same one door, so a source added to
args/entity_currency.yaml is live in the redraft with no code change. The
right-rail surface is dwr-cmt-02/dwr-ws-02; today the button lives in the
EXISTING ⚡ suggestions panel on the document page, so no new page and no
8-point page gate.
