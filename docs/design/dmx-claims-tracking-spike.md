# CUI // SP-CTI

# DocMod Semantic Claim Tracking — Design Spike (dmx-claims-01)

**Status:** DESIGN SPIKE — awaiting human go/no-go. No code, no migration, no
new tables have been created by this document.
**Gap addressed:** DMX Gap #4 — semantic claim tracking (rated hardest /
highest-value).
**Downstream task:** `dmx-claims-02` is HELD behind `dmx-gate-00` and MUST NOT
build until a human approves this document.

---

## 1. Problem statement

DocMod today answers *"what named entity in this document is no longer
current?"* — a `Catalyst 6500` past EOL, a `TLS 1.1` rulebook hit, a superseded
NIST rev. A finding attaches to the **chunk / section** that contained the
entity (`docmod_findings.chunk_link_id`, `section_heading`, `page` — see
`tools/doc_modernization/scanner.py:154-179`).

What it cannot do is track the *assertion a document makes about* that entity:

> "Doc X requires **TLS 1.2** for all API endpoints."

That sentence is a **semantic claim** — a typed `(subject, predicate, object)`
proposition anchored to a specific span of prose. When the underlying evidence
moves (crypto rulebook deprecates TLS 1.2, a superseding STIG raises the floor
to 1.3), we want to flag *that specific sentence*, with its char offsets, not
re-flag the whole document. The whole-document granularity is exactly the
weakness `drift_bridge.py` already calls out in the *tag-based* ACOIC path it
replaced (`tools/doc_modernization/drift_bridge.py:5-10`).

### The hard constraint (deterministic-first, TRUST rule 1)

`base_pack.py:9-12` and `:98-100` are unambiguous: a pack's `evaluate()` verdict
MUST be a pure function of deterministic evidence (catalog rows, EOL dates,
rulebook matches, inventory counts). **No LLM-only verdicts, ever.** Any claim
feature must not smuggle an LLM judgement into the currency verdict path.

This spike's central reconciliation: **the LLM may help *extract* the claim
structure, but it may never *evaluate* whether a claim is still valid.** The
validity verdict stays on the existing deterministic pack path. The claim is
merely a richer, human-anchored *subject* that a deterministic finding attaches
to — replacing the coarse `entity_label` string with a typed, span-anchored
proposition.

---

## 2. Claim extraction approach (reconciled with deterministic-first)

### 2.1 What the LLM does and does not do

| Step | Actor | Deterministic? |
|------|-------|----------------|
| Propose candidate claim `(subject, predicate, object)` from chunk text | LLM (or no-LLM fallback) | No — a *proposal*, gated by §2.3 + HITL |
| Anchor claim to a **verbatim char span** in the version text | Deterministic `str.find()` grounding | **Yes** |
| Persist claim as `pending_review` | Deterministic | Yes |
| Human approves claim → `active` | HITL | Yes |
| Decide whether an active claim is still valid | **Deterministic pack `evaluate()`** | **Yes — TRUST rule 1 unchanged** |
| Word the redline around an invalidated claim | LLM | (already gated by `citation_grounding`) |

The verdict *about* a claim is never LLM-authored. The LLM only ever produces a
*candidate structure* that a human confirms and that is then matched against
deterministic evidence.

### 2.2 The anchor is the load-bearing invariant

Every claim MUST carry a **verbatim anchor span** — `(anchor_start, anchor_end)`
char offsets into the specific `version_id`'s reconstructed text, plus the
verbatim `claim_text` those offsets delimit. This is the exact anti-hallucination
pattern the ingest pipeline already uses for LLM-extracted metadata: *"must
appear verbatim in the source text (anti-hallucination)"*
(`tools/document_intelligence/ingest_orchestrator.py:1534-1536`,
`:1555-1558`).

Mechanism (deterministic, no LLM trust required):

1. LLM returns a candidate `claim_text` (the exact sentence/clause) plus the
   typed triple.
2. We call `text.find(candidate_claim_text)` on the chunk's source text. If it
   is **not found verbatim**, the candidate is **rejected** — an LLM that
   paraphrased or invented the sentence cannot produce a claim.
3. On a hit we record absolute `(anchor_start, anchor_end)` = chunk base offset
   + local match offsets. The span, not the model output, becomes the claim's
   identity.

Because the anchor is a byte-exact slice of an immutable, approved
`dic_versions` row, any later verdict *about* the claim is reasoning over real
evidence pinned to real text — the deterministic-first invariant holds by
construction. A claim whose anchor can no longer be located in the current
approved version is auto-transitioned to `superseded` (the text was edited out).

### 2.3 Confidence gating and air-gap fallback

- Candidate claims below `CONF_INCLUDE` (0.7, `constants.py:84`, mirroring
  `citation_grounding.classify_confidence`) never reach a human surface.
- **Air-gap / no-LLM path:** the KG extractor already ships a deterministic
  fallback, `_extract_no_llm()` (NIST-control regex + title-case noun
  extraction, `tools/rag/rag_to_kg_ingester.py:176-181`). In air-gap mode claim
  extraction degrades to **rulebook-anchored spans only**: a crypto rulebook hit
  (`TLS 1.1`, `MD5`) yields a claim whose subject is the matched token and whose
  span is the regex match — fully deterministic, lower recall, no LLM. This
  satisfies the DOCMOD air-gap-rulebook-fallback invariant.

---

## 3. Schema draft — `dic_claims` (DDL SKETCH — NOT APPLIED)

Follows the DIC table conventions verbatim: every DIC table carries
`tenant_id TEXT DEFAULT 'default'` + `classification TEXT DEFAULT 'CUI'` and uses
the RLS-aware `get_connection()` (`tools/document_intelligence/db/init_db.py:4`,
and every `dic_*` table therein). Append-only with a `supersedes_id` chain,
exactly like `docmod_findings` (`scanner.py:11-13`).

```sql
-- DDL SKETCH — presented for review only; dmx-claims-02 would author the real
-- migration under tools/db/migrations/<N>_dic_claims.sql after approval.
CREATE TABLE IF NOT EXISTS dic_claims (
    claim_id        TEXT PRIMARY KEY,              -- 'clm-<uuid12>'
    doc_id          TEXT NOT NULL,                 -- FK dic_documents.doc_id
    version_id      TEXT NOT NULL,                 -- FK dic_versions.version_id (the approved version the span lives in)
    section         TEXT,                          -- section heading, mirrors docmod_findings.section_heading
    chunk_link_id   TEXT,                          -- dic_chunk_links.link_id the span came from (join back to evidence)
    page            INTEGER,

    claim_text      TEXT NOT NULL,                 -- VERBATIM slice — must equal version_text[anchor_start:anchor_end]
    anchor_start    INTEGER NOT NULL,              -- char offset into the version's reconstructed text
    anchor_end      INTEGER NOT NULL,

    -- Typed proposition (subject/predicate/object). Reuses KG entity vocabulary
    -- (constants.KG_ENTITY_TYPES) for subject/object types so a claim's subject
    -- can be matched to a pack CandidateEntity.label deterministically.
    subject_label   TEXT NOT NULL,                 -- e.g. 'TLS 1.2'
    subject_type    TEXT,                          -- e.g. 'protocol' (KG_ENTITY_TYPES)
    predicate       TEXT NOT NULL,                 -- controlled verb, e.g. 'requires', 'mandates', 'prohibits'
    object_label    TEXT,                          -- e.g. 'all API endpoints'
    object_type     TEXT,

    pack_domain     TEXT,                          -- which pack owns validity checks, e.g. 'crypto_protocols'
    linked_evidence_ids TEXT,                      -- JSON array of evidence source ids / finding_ids (see §4)

    status          TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK (status IN ('pending_review','active','invalidated','superseded')),
    supersedes_id   TEXT,                          -- append-only chain: a state change is a NEW row
    dedupe_key      TEXT,                          -- sha256(doc_id|subject|predicate|object) — stable across versions

    -- Provenance (anti-hallucination audit trail)
    prov_model      TEXT,                          -- LLM model id, or 'no_llm_rulebook'
    prov_prompt_version TEXT,                       -- claim-extraction prompt registry version
    extracted_at    TEXT NOT NULL,                 -- ISO-8601 UTC
    confidence      REAL DEFAULT 1.0,

    tenant_id       TEXT DEFAULT 'default',        -- RLS
    classification  TEXT DEFAULT 'CUI'             -- RLS
);
CREATE INDEX IF NOT EXISTS idx_dic_claims_tenant   ON dic_claims(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_doc      ON dic_claims(doc_id, version_id);
CREATE INDEX IF NOT EXISTS idx_dic_claims_subject  ON dic_claims(subject_label);
CREATE INDEX IF NOT EXISTS idx_dic_claims_dedupe   ON dic_claims(dedupe_key);
```

Notes:
- **Append-only.** `dic_claims` would be added to `APPEND_ONLY_TABLES` in
  `.claude/hooks/pre_tool_use.py` (dmx-claims-02 checklist item). A status change
  = new row whose `supersedes_id` points at the prior row for the same
  `dedupe_key`, resolved latest-wins exactly as `scanner._open_findings()` does
  (`scanner.py:141-151`).
- **RLS columns are first-class**, unlike the shared KG tables (see §5), so the
  standard `get_connection()` RLS predicate applies with no bypass.
- `claim_text` is redundant with `anchor_*` on purpose: it lets a verifier assert
  `version_text[start:end] == claim_text` and auto-`superseded` a claim whose
  anchor drifted after an (append-only) version edit.

---

## 4. Linkage — evidence row → specific claim

Goal: a pack evidence change flags **the specific claim (with its span)**, not
the whole document.

The join path reuses the *existing* deterministic finding machinery. Nothing new
evaluates currency:

```
pack.evaluate(entity) ──> Verdict{finding_type, evidence:[{source,detail,date}]}   (scanner.py:252-258)
        │  (unchanged deterministic path)
        ▼
docmod_findings row  (entity_label, chunk_link_id, doc_id, version_id)             (scanner.py:154-179)
        │
        │  MATCH on: finding.doc_id == claim.doc_id
        │         AND finding.version_id == claim.version_id
        │         AND finding.entity_label == claim.subject_label   (or object_label)
        │         AND claim.status == 'active'
        ▼
dic_claims row flagged  ──> append supersede row status='invalidated',
                            carrying (anchor_start, anchor_end, claim_text)
```

Concretely, for *"Doc X requires TLS 1.2 for all API endpoints"*:

1. `crypto_protocols.extract()` matches `TLS 1.2` as a `CandidateEntity`
   (`base_pack.py:33-43`).
2. `crypto_protocols.evaluate()` reads the crypto rulebook / STIG evidence
   **deterministically** and returns a `deprecated` verdict with cited evidence.
3. The scanner writes a `docmod_findings` row for `entity_label='TLS 1.2'` at
   `version_id`, `chunk_link_id`.
4. A new **claim-linkage step** (dmx-claims-02) joins that finding to any
   `active` claim in the same `doc_id`/`version_id` whose `subject_label`
   (or `object_label`) equals `TLS 1.2`, and appends an `invalidated` claim row
   whose payload carries the char span. The `drift_bridge` payload — which
   already forwards `chunk_link_id`, `section_heading`, `page`, `rationale`
   (`drift_bridge.py:127-135`) — gains `claim_id` + `anchor_start`/`anchor_end`,
   so ACOIC/redline surfaces the *sentence* to a reviewer.

`linked_evidence_ids` on the claim persists the `finding_id`(s) that last
touched it, giving a bidirectional audit trail (claim ⇄ evidence) without a
separate join table. If a dedicated join is preferred later, an append-only
`dic_claim_evidence(claim_id, finding_id, linked_at, tenant_id, classification)`
is the drop-in alternative — but for one-pack-domain scope the JSON column is the
simpler choice (Karpathy #3, YAGNI).

**Deterministic guarantee:** the *only* thing that flips a claim to
`invalidated` is the existence of a deterministic `docmod_findings` row. No LLM
sits on that edge. The claim adds *precision of location*, not a new judgement.

---

## 5. Overlap analysis — extend the PR-#318 KG extractor, or not?

### What PR #318 actually built (code read)

- `tools/rag/rag_to_kg_ingester.py::ingest_chunk` reads `rag_chunks`, calls
  `tools/knowledge_graph/llm_relationship_extractor.py` (LLM) with a
  deterministic `_extract_no_llm()` fallback, and writes `kg_nodes` + `kg_edges`
  with a `source_chunk_id` back-ref (`rag_to_kg_ingester.py:3-4`, `:39-44`,
  `:491-531`).
- Entities are bare `(label, entity_type)` tuples — **no char offsets, no
  span** (`:491-503`). Granularity is the *chunk*, via `source_chunk_id`.
- `kg_nodes` / `kg_edges` / `kg_graphs` **have no `tenant_id` / `classification`
  columns**; tenant lives inside a `properties` JSON blob and reads must
  **bypass RLS** (`tools/document_intelligence/blueprint.py:1336-1340` —
  `set_security_context(None)` with a documented `rls-bypass`).
- The structure is a **co-occurrence / relationship graph of *things***, not a
  set of **assertions with a lifecycle**. Edges carry a `relationship` string +
  `evidence` blob (`:520-523`), but no `active/invalidated/superseded` status,
  no doc/version binding as a first-class key, no verbatim anchor.

### Recommendation: **REUSE the extractor's engine; do NOT extend its schema.**

A claim needs four things the KG tables structurally lack:

| Requirement | KG tables today | `dic_claims` |
|-------------|-----------------|--------------|
| Verbatim char anchor `(start,end)` | ✗ (chunk-granular) | ✓ |
| `doc_id` + `version_id` as first-class keys | ✗ (only `source_chunk_id`) | ✓ |
| Status lifecycle `active/invalidated/superseded` | ✗ | ✓ |
| RLS `tenant_id`/`classification` columns (no bypass) | ✗ (JSON + RLS bypass) | ✓ |

Forcing these onto `kg_nodes`/`kg_edges` would (a) add RLS columns to tables the
Ontology canvas and cross-canvas context read *without* RLS (a security-context
change with blast radius well beyond DocMod — `cross_canvas_context.py:13`,
`analytics_engine.py`), and (b) overload a co-occurrence graph with
assertion-lifecycle semantics it was never shaped for. That is a parallel-model
smell in the wrong direction.

**But** the *extraction machinery* is exactly right to reuse: the same
`llm_relationship_extractor` that yields `(subject, relation, object)` triples is
the natural claim-candidate source. dmx-claims-02 should call that extractor and
route its typed-triple output through the §2.2 verbatim-anchoring step into
`dic_claims` — **reuse the code path, new persistence table.** Optionally mirror
an approved claim as a `kg_node` with `entity_type='claim'` for graph
navigation, but `dic_claims` is the source of truth. This keeps one extraction
engine while giving claims the schema they actually need.

---

## 6. Cost model

Extraction is **per-version LLM work**, gated off by default.

- Unit of work: one approved `dic_versions` row → its chunks (`scanner._doc_chunks`,
  `scanner.py:85-138`). Chunks are ~500 tokens each.
- Per-chunk claim-extraction call ≈ chunk (~500) + prompt (~300) + output
  (~200) ≈ **~1,000 tokens/chunk**.
- A 30-page policy/crypto doc ≈ 60 chunks → **~60k tokens per version
  extraction** (one-time per approved version).
- **Incremental reuse:** the scanner already skips unchanged documents via
  `docmod_doc_scan_state` (last_version_id + evidence hash, `scanner.py:196-203`).
  Claim extraction piggybacks on this — it fires only when a *new approved
  version* appears, so steady-state cost is ~0. A 500-doc corpus pays the ~60k
  cost only for docs that actually revised.
- **Config toggle (default OFF):** `args/docmod/*.yaml` gains
  `claims.enabled: false` and `claims.pack_domains: [crypto_protocols]`
  (loaded via the existing `pack_loader.load_config()`, `drift_bridge.py:90`).
  With the toggle off, zero LLM calls and zero rows — DocMod behaves exactly as
  today. This mirrors the redaction toggles that "default off; never remove the
  toggles" pattern in the DOCMOD/TRUST guardrails.
- Air-gap: extraction runs the `_extract_no_llm` rulebook path only — **$0 LLM**,
  bounded regex cost.

---

## 7. Invariants preserved (DOCMOD 5)

| Invariant | How this design honors it |
|-----------|---------------------------|
| **1. Deterministic-first verdicts** | Claim *validity* comes only from a deterministic `docmod_findings` row produced by `pack.evaluate()`. The LLM proposes claim *structure*; it never evaluates currency. §2, §4. |
| **2. Append-only `dic_versions` / `dic_edit_history`** | `dic_claims` is itself append-only with a `supersedes_id` chain; it never mutates or deletes version/history rows. It reads approved versions read-only. §3. |
| **3. HITL `pending_review` gating** | Extracted claims land as `status='pending_review'`; a human promotes to `active`. Only `active` claims can be `invalidated`. Sub-0.7-confidence candidates never surface. §2.3, §3. |
| **4. `tenant_id` + `classification` RLS on every new table** | `dic_claims` carries both as first-class columns and uses RLS-aware `get_connection()` — no bypass (unlike the shared KG tables). §3, §5. |
| **5. Air-gap rulebook fallback** | No-LLM path via `_extract_no_llm` yields rulebook-anchored claims; toggle-off means no dependency at all. §2.3, §6. |

---

## 8. Go / No-Go recommendation

**GO — conditional, single-domain, human-approval-gated.**

Rationale: the design slots into existing, proven machinery (the deterministic
pack path, the append-only supersede chain, the incremental scan-state, the
PR-#318 extraction engine) without weakening any TRUST or RLS invariant. The one
genuinely new asset is a well-scoped, RLS-columned, append-only `dic_claims`
table whose verdicts remain deterministic by construction. Risk is contained to
extraction *recall/precision*, which the verbatim-anchor gate + HITL bound.

**Scope the first build to ONE pack domain: `crypto_protocols`** — the most
structured domain (finite protocol/algorithm vocabulary, clear
`requires/prohibits` predicates, existing rulebook evidence), so the
subject-match linkage in §4 is high-precision before generalizing.

### Phased plan for `dmx-claims-02` (HELD pending approval of THIS doc)

> **`dmx-claims-02` MUST NOT begin until a human approves this spike.** It is
> held behind `dmx-gate-00`.

- **Phase A — Schema & guardrails.** Author migration `<N>_dic_claims.sql` from
  the §3 sketch; add `dic_claims` to `APPEND_ONLY_TABLES`
  (`.claude/hooks/pre_tool_use.py`) and to `tests/conftest.py`
  `MINIMAL_ICDEV_SCHEMA`; add `claims.enabled` (default false) +
  `claims.pack_domains` to docmod config. RED tests for RLS + append-only.
- **Phase B — Anchored extraction (crypto only).** Call
  `llm_relationship_extractor` per chunk; route triples through the §2.2
  verbatim-`str.find()` anchoring gate; persist as `pending_review` with
  provenance. No-LLM rulebook fallback path. Reject any non-verbatim candidate.
- **Phase C — HITL promotion.** `pending_review → active` review surface
  (reuse DIC's existing pending/HITL pattern); confidence gating at 0.7.
- **Phase D — Linkage & flagging.** Add the finding→claim subject-match step
  (§4); on a matching deterministic finding, append an `invalidated` claim row
  and extend the `drift_bridge` payload with `claim_id` + anchor span. Auto-
  `superseded` on anchor drift.
- **Phase E — Verification.** Unit + BDD over crypto fixtures; e2e that a
  rulebook change flags the exact anchored sentence and nothing else; coherence
  gate. Then, and only then, consider generalizing to a second pack domain as a
  *separate* card.

---

*This document is the sole deliverable of `dmx-claims-01`. It creates no tables,
runs no migration, and seeds no tasks. `dmx-claims-02` requires explicit human
approval of this design before any implementation.*
