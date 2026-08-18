# CUI // SP-CTI

# Cross-backend semantic entity resolution (cef-rsv-02)

**Module:** `tools/cortex/entity_resolution.py` (mirrored to `icdev/tools/cortex/`)
**Consumer:** `tools/cortex/resolver.py::resolve` → `CortexResolution.conflicts` / `.gaps`
**Tests:** `tests/cortex/test_entity_resolution.py` (69 cases, gated via
`args/ci_test_files/core.d/cef-rsv-02.txt`)

## The defect

Cortex fuses ranked results across backends by weighted RRF, and cross-source
synthesis was string concatenation. Both operate on *relevance*. Neither ever
read two hits' **claims** side by side.

So a RAG chunk asserting "TLS 1.1 remains approved for legacy interconnects" and
an `entity_currency` row asserting `deprecated` both landed in one result set,
were ranked against each other, and the contradiction between them was
invisible — not suppressed, not scored down, simply never computed. Nothing in
the platform could notice that two of its own sources disagreed.

## What was built

`resolve_entities(hits, assessments, backend_errors, entities, backends, config)`
resolves hits from different backends onto the same real-world entity, extracts
each source's claim, and compares them.

### Four outcomes that used to render identically

| Outcome | Signal | Why it must stay separate |
|---|---|---|
| **agreement** | `conflicts: []`, entity `answered: true` | Empty conflicts now means *detection ran and found none* |
| **conflict** | one `EntityConflict` per (entity, kind) | Both sides + provenance; **no winner** |
| **gap** | a `gaps` entry with a reason | Unknown is a visible finding, not silence |
| **dead backend** | a `backend_error` + an `unresolved` record | An outage is not a statement about the corpus |

### Three claim lanes, stamped and never merged

`EntityClaim.extraction` is carried on the claim and on **every conflict side**,
so a reader can discount a weaker lane without the detector having quietly
discounted it first:

- **`structured`** — typed currency metadata the `currency` backend already
  publishes (cef-bck-01), plus each disagreeing source the `entity_currency`
  store already carried under `others` (cef-fnd-04). That store preserved the
  disagreement; Cortex carried it as a boolean nothing acted on. Promoting the
  losing sources to first-class claims is what makes them comparable against the
  *other* backends.
- **`pack_evaluate`** — each registered `DomainPack` assessment.
  `resolver.reduce_assessments` picks the highest-ranked verdict and reports the
  loser only as `pack_id=verdict` in prose; promoting each assessment to a claim
  makes that reduction auditable.
- **`text_pattern`** — declared, entity-**anchored**, **directional** rules over
  retrieved prose. This is the only lane that can express what a document merely
  states, and therefore the only lane that can detect the motivating case at
  all: no RAG/DIC/KB hit carries a typed currency field. Toggle
  `resolve.text_claims` in `args/cortex_config.yaml`.

The anchoring is the safety property: `"TLS 1.2 supersedes TLS 1.1"` yields
`superseded` for TLS 1.1 and **nothing** for TLS 1.2. An unanchored keyword scan
reads that sentence as evidence TLS 1.2 is superseded and fabricates a conflict
against the catalog. Negation is handled the same way — `"TLS 1.1 is not
deprecated"` matches no rule.

### Identity is reused, not reinvented

- **Source identity** is `search_service.fusion_ident` — the *same* predicate RRF
  fusion uses, promoted from `_fusion_ident` with the private name kept as an
  alias. One document retrieved by both `rag` and `dic` is **one** claim with two
  backends recorded on it. Counted twice it would corroborate itself, and a
  conflict's apparent weight of evidence would depend on how many rungs happened
  to index the document.
- **Entity identity** is `tools/currency/entity_currency.normalize_key` plus the
  version, so `TLS 1.1` and `TLS 1.2` never join. Entity *type* is carried but is
  deliberately not part of the key: only the `currency` backend and the packs
  supply one, and requiring it would mean a RAG chunk could never join to a
  catalog row — the single case this card exists to make visible.

## No silent winner

`EntityConflict` has no `winner`, no `resolved_value`, no `consensus`, no average
and no score. `TestNoSilentWinner` asserts that **structurally**, against the
dataclass rather than one serialized instance, so a field that merely happened to
be unset in a test's data cannot ship.

Authority is real and is recorded *on* the sides (`authoritative`, `confidence`,
`as_of`, `extraction`) — it is not applied. `entity_currency.resolve()` resolves
authority at read time to answer *"what is the best available answer"*; that is a
different question from *"do my sources agree"*, and answering the second with
the first deletes the finding.

The verdict is unaffected. It still comes from `DomainPack.evaluate()` and
nothing else; a disagreement between two evidence sources is not a vote.

## Gaps and outages

- `no_evidence` — nothing mentioned the entity. An ingestion problem.
- `no_claim` — documents mention it and none states its currency. A content
  problem. Different fix, so a different reason.
- A claim whose status is `unknown` with no successor and no EOL date asserts
  nothing and does **not** count as an answer — that equivalence is how "nobody
  knows" started rendering the same as "current".
- **A dead fan-out produces no gap.** It produces a `backend_error` and an
  `unresolved` record naming the entity and the failed rungs. A *partial* outage
  still yields a real gap (something did look and did not cover this entity),
  with the failures on the gap's own `backends_failed` field rather than smuggled
  into its reasons.

### Two layers, one vocabulary, two questions

`resolver._gaps` (cef-rsv-01) answers *"why is the SUBJECT's verdict unknown"*,
where a dead fan-out **is** a legitimate answer — hence `backends_failed` remains
in its reason list and its contract is unchanged. `entity_resolution` answers
*"did anything ANSWER for this entity"*, where a dead fan-out is an outage rather
than an answer. The reason constants are defined once, in `entity_resolution`,
and re-exported from `resolver` so `resolver.GAP_*` keeps resolving; the subject's
gap is filed by exactly one of the two layers so the same entity never draws two
contradictory findings in one list.

## Surfaces

Nothing new. `CortexResolution.conflicts` was declared and empty by cef-rsv-01
precisely so this card could fill it without a contract change, so the finding
reaches `POST /cortex/api/v1/resolve`, the `cortex_resolve` MCP verb and
`cortex.resolve()` unchanged. The full report (claims, per-entity roll-up,
`unresolved`) travels under `metadata["entity_resolution"]`.
