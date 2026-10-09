# A conflict/gap the request DIDN'T take with it, browsable on Explorer (cef-ui-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.cortex.finding_store import list_findings, finding_stats
  stats = finding_stats("default")        # state: disabled|unmeasured|clean|findings
  rows  = list_findings("default", finding_type="conflict")
UI: /document-intelligence/explorer -> "Cross-Source Conflicts & Gaps"
API: GET /document-intelligence/api/explorer/cortex-findings?type=gap&reason=&backend=
Toggle: `resolve.persist_findings` in args/cortex_config.yaml -- default ON,
because the surface renders nothing without it and an empty Explorer that
LOOKS like a clean bill of health is the defect this card exists to prevent.
Migration 20260819030255.

cef-rsv-02 made a cross-source disagreement COMPUTABLE and cef-rsv-03 CITED
it -- and both then travelled on the CortexResolution the caller already held
and NOWHERE ELSE. So the only reader of a finding was whatever code happened
to trigger the resolution: a docmod sweep, a DocDrift draft screen, an MCP
verb. A conflict is a finding a HUMAN adjudicates and a gap is a data-quality
ticket, and neither is actionable if it dies with the request. `record_findings`
runs in `resolver.resolve` right after `register_resolution`.

A PROJECTION, NOT AN AUDIT TABLE. One upserted row per (tenant, entity,
finding), so a conflict observed on forty resolutions is ONE disagreement with
`seen_count` 40 -- forty rows would render as forty findings. A conflict whose
claimed VALUES change is a NEW finding, because what a human adjudicated is no
longer what is on the table. Deliberately NOT in APPEND_ONLY_TABLES; the
immutable record of a resolution is the source_citation_registry row
cef-rsv-03 already writes.

IT STORES NO WINNER, and the page renders none. There is no `resolved_value`,
`consensus` or score column in FINDING_COLUMNS; every side is persisted and
rendered whole with its OWN backend, source, source_id, source_table, as_of,
authoritative, confidence and extraction lane, side by side, under a standing
"unresolved by design" note. `TestNoSilentWinner` asserts that against the
COLUMN LIST and the round-tripped row, not one hand-built payload, so a field
that merely happened to be unset in a fixture cannot ship. Authority is
RECORDED on the sides and never APPLIED -- `entity_currency.resolve()` answers
"what is the best available answer", which is a DIFFERENT question from "do my
sources agree", and answering the second with the first deletes the finding.

A gap's `backends_failed` stays its OWN column and never becomes a reason; on
screen it is a red `outage:` badge beside the blue reason badges, and filtering
by reason cannot match it. A partial outage is CONTEXT for a gap, not its cause.

FOUR CAUSES OF AN EMPTY LIST and only ONE is "your sources agree":
  disabled     persist_findings off -- nothing recorded, says nothing about the corpus
  unmeasured   recording on, no resolution recorded on this deployment yet
  clean        resolutions ran and every claim was compatible  <- the measurement
  findings     rows exist
`conflicts`/`gaps` are None -- NEVER 0 -- for the first two, so the template
physically cannot print a reassuring zero for a surface that never looked. An
unreachable or unmigrated store degrades to the SAME unmeasured shape.

Filtering (entity/reason/backend) goes through the API so the filter and the
stored payloads cannot disagree about what a reason means; the filter
VOCABULARY is derived from the rows on screen, never from the constants, so a
chip can never offer a value matching nothing. `reason`/`backend` live inside
JSON payloads and are matched in PYTHON, not with SQLite-dialect JSON SQL.

NOTHING ON THIS PAGE TRIGGERS A RESOLUTION. It renders what resolve() has
already produced -- a browse surface that fanned out across five backends on
page load would put a 5-11s retrieval on every render. Measured 2026-08-19:
8 real resolutions, 4 clean (each still bumping the denominator, which is the
whole reason the denominator exists), 2 real gaps. This deployment's corpus
has produced NO real conflict yet, so `conflicts` reads a MEASURED 0.
Each finding links to /document-intelligence/docdrift?entity=<label> with its
open docmod_findings count -- this page says the sources disagree, DocDrift is
the queue a redline is drafted from.
