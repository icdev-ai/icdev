# [TEMPLATE: CUI // SP-CTI]

# oss-chunk-01 — Template chunking driven by the source_registry `chunking` key

## Problem

`tools/rag/chunker.py` had exactly one strategy: short content → single chunk,
long → sliding window with 10% overlap snapped to a sentence boundary, adaptive
size clamped to `[150, 2000]` tokens.

That destroys structure in the document types ICDEV cares most about. A NIST
control or a STIG rule gets split mid-item, so retrieval returns half of
`AC-2` and the assessor never sees the rest.

A config hook for this already existed and was dead: 19 entries in
`tools/rag/source_registry.py` carry a `chunking` key (values `canvas_graph`
and `canvas_assessment`), read at exactly one place —
`source_registry.py`'s listing filter. `ingestion_manager.py` never consulted
it. This change activates that key rather than inventing a parallel config
surface.

## What shipped

### `args/chunking_templates.yaml` (new)

Eight templates plus the two canvas values the registry already carried:

| Template | Strategy | Boundary |
|---|---|---|
| `oscal_catalog` | structural | One chunk per control; **never split** (`oversize_policy: keep`) |
| `stig_checklist` | structural | One chunk per rule (V-key / `SV-…_rule` / Group-Rule-STIG ID) |
| `rfp_sow` | structural | Section A–M, `L.x` / `M.x`, attachments/exhibits |
| `contract` | structural | `ARTICLE`/`CLAUSE`, numbered `2.1`, FAR/DFARS `52.xxx-xx` |
| `sop_runbook` | structural | `Step N`, numbered steps, Prerequisites/Rollback/Verification |
| `slide_deck` | structural | One chunk per slide (`Slide N`, `---`, form feed) |
| `spreadsheet` | row_groups | 25 body rows per chunk, header repeated on every chunk |
| `general` | sliding_window | **Today's behaviour, unchanged — the default** |
| `canvas_graph`, `canvas_assessment` | sliding_window | Registry's existing values, now resolvable |

Three strategies: `sliding_window` (unchanged), `structural` (new chunk at each
boundary regex match), `row_groups` (N rows + repeated header).

### `tools/rag/chunking_templates.py` (new, mirrored to `icdev/`)

Owns config loading and boundary detection only — no `VectorChunk` import, so
there is no cycle with the chunker. Ships a CLI:

```bash
python tools/rag/chunking_templates.py --list --json
python tools/rag/chunking_templates.py --show oscal_catalog --json
python tools/rag/chunking_templates.py --suggest docs/catalog.md --json
python tools/rag/chunking_templates.py --preview docs/catalog.md --template oscal_catalog --json
```

### `tools/rag/chunker.py`

`chunk_content(..., template=)` and `chunk_fields(..., template=)` dispatch on
an explicit name. The sliding-window body was extracted to
`_sliding_window_split()` and the adaptive sizing to `_resolve_chunk_size()`,
so the structural path can reuse the window for oversize items.

### `tools/rag/source_registry.py` → `tools/rag/ingestion_manager.py`

New `get_chunking_template(source_type)` reads the key; `ingest_source()` and
`ingest_single_record()` both forward it to `chunk_fields(template=...)`. That
is the line that was missing — the key is now live on both batch and realtime
paths.

## Two invariants

**1. Dispatch is explicit; auto-detection is only ever a suggestion.**
`suggest_template()` scores content against each template's `detect` patterns
and returns a ranked, `advisory: True` answer for an operator. Nothing in the
ingestion path calls it. A test asserts that OSCAL content passed with no
`template=` argument still chunks as `general`.

**2. The decision is auditable.** Every chunk carries `chunking_template` and
`chunking_strategy` in metadata. A typo in the registry does not silently
no-op — it records `chunking_template_requested` and
`chunking_fallback_reason: unknown_template:<name>` and proceeds with the
default. Structural templates that match no boundaries fall back to the sliding
window and say so (`no_boundaries_matched`) rather than emitting one giant
chunk. Bad boundary regexes are reported in `chunking_pattern_errors`, not
raised.

## Backward compatibility

The default is `general`, which is byte-for-byte today's behaviour — a test
asserts `chunk_content(text)` and `chunk_content(text, template="general")`
produce identical output. No registry entry currently names a structural
template, so ingestion output is unchanged until an operator opts a source in.

`canvas_graph` / `canvas_assessment` are deliberately defined as
`sliding_window`: naming them activates the key and makes the choice visible
**without** re-chunking already-ingested canvas content.

## Verification

- `tests/test_rag_chunking_templates.py` — 45 tests: config resolution,
  per-type structural segmentation, row groups, explicit-dispatch invariant,
  provenance metadata, advisory-only suggestion, registry wiring (including a
  monkeypatched `ingest_single_record` proving the template is forwarded).
- `tests/test_rag_chunker.py` — updated for the new metadata keys; adds a
  regression test that the caller's `metadata` dict is not mutated and is not
  shared between chunks.
- Suite: 1609 passed vs. a 1564-passed baseline on the same selection, with an
  **identical** 85 pre-existing failures (fresh-worktree cold-DB) — no
  regressions.
- `ruff check` clean; `coherence_checker --all` reports no finding against any
  file in this change.

### Known pre-existing failure (not from this change)

`tests/test_rag_chunker.py::TestChunkContent::test_medium_content_single_chunk`
fails on clean `HEAD` too. ~375-token content with
`chunk_size_tokens: 500` yields 3 chunks, not 1, because the adaptive clamp
(`min(configured, max(150, tokens // 70))`) lowers the effective size to 150.
The test encodes the pre-adaptive contract. Left alone — deciding whether the
clamp or the test is wrong is a separate call from activating template
chunking.

## Wiring a source

Set `chunking` on its `SOURCE_REGISTRY` entry:

```python
"stig_findings": {
    "table": "stig_scan_findings",
    ...
    "chunking": "stig_checklist",
},
```

Preview before committing to it:

```bash
python tools/rag/chunking_templates.py --preview <file> --template stig_checklist --json
```

Existing chunks are not re-chunked automatically; reindex the source to apply.
