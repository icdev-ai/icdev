<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 6040 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-06d89-phase-6040`
- **Roadmap:** `rm-06d89040cf` (scan_id 43)
- **Opportunity:** 6040
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `src/documents/migrations/0016_sha256_checksums.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `6722978fa`** — the DIC intra-document duplicate-content
anomaly-detection refactor (opportunity 5984).

`src/documents/migrations/0016_sha256_checksums.py` is a paperless-ngx **Django
schema migration** that adds a `content_sha256` (and archive checksum) column so
documents can be de-duplicated by content hash. It is generated migration
boilerplate: a migration file carries no runtime logic and no meaningful
tunable threshold, so the scanner's `hardcoded_threshold` hit is a
false-positive on migration scaffolding. The *semantic* intent the file encodes
— SHA-256 content hashing for duplicate/integrity detection over documents — is
exactly the content-anomaly paradigm.

That analog already exists in DIC and is the faithful AI-ification of the
`hardcoded_threshold` → `anomaly_detection` pattern for content-hash dedup:
inline repeat/length constants were lifted into a config-driven block, and
`_detect_duplicate_blocks` flags repeated content, escalating from a plain
`duplicate_block` to an `anomalous_repeat` anomaly when the repeat count is a
statistical outlier (non-degenerate spread). `assess_duplicate_blocks` wraps
`_segment_blocks` + `_detect_duplicate_blocks` with an optional LLM grade
(`dic_duplicate_block_assessment`). This is the internal, controllable
counterpart to paperless's fixed `content_sha256` equality check.

This opportunity is also a **re-emitted scan artifact**: the identical paperless
file `migrations/0016_sha256_checksums.py` has been flagged with the same
`hardcoded_threshold` → `anomaly_detection` mapping across nine separate temp
clones (opps 1773, 2153, 3491, 3916, 4344, 4693, 4851, 5079, 6040). Each clone
is reaped after the scan, so no edit could ever persist against the flagged
path.

## Verification (branch kanban/aiify-rm-06d89-phase-6040)

- External clone `aiify_git_zwu66zfu/src/documents/migrations/0016_sha256_checksums.py`: **GONE** (reaped by engine).
- `6722978fa` is an **ancestor of HEAD**.
- Content-hash + duplicate-anomaly layer present in DIC:
  - `tools/document_intelligence/ingest_orchestrator.py` — `_sha256` (L271), `_detect_duplicate_blocks` (L1836), `assess_duplicate_blocks` (L1975), `content_sha256` columns (L171/L187).
  - `duplicate_block` → `anomalous_repeat` escalation on statistical outlier (L1843–1891).
- DIC anomaly family already covers the related paperless surfaces: freshness (6042), date-parsing (6048), OCR confidence (6105), search relevance (6052), intra-doc dedup (5984/this).

No new code required — closing as a duplicate with `bypass_verification`.
