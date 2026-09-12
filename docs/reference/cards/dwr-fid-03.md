# A DEGRADED render SAYS it is degraded, and the ingest posture is REAL (dwr-fid-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.document_intelligence.reading_pane --survey [--json]   # every document's render basis, counted
python -m tools.document_intelligence.reading_pane --doc <doc_id>      # one document's pane + its stated limits
python tools/workflow/coherence_checker.py --check sandbox_coverage --json
python -c "from tools.document_intelligence.ingest_guard import evaluate_upload as f; print(f('x.pdf', strict=True))"
```

`doc_detail` rendered `dic_sections` and, when there were none, ONE line --
"No sections yet. Generate a draft or instantiate a template." -- so a THIN
render was indistinguishable from a complete one. MEASURED on the live PG
board 2026-09-08 and the 39 are NOT one population:
  55 documents; 16 carry a section row -> the FULL render, untouched
  19 no sections, text recoverable from rag_chunks   -> the degraded pane
   9 no sections, chunk LINKS exist and EVERY ONE DANGLES: the rag_chunks
     rows they name are gone (a reindex, a retention sweep, delete_source).
     It WAS chunked and the text has since been removed. THE FINDING.
  11 no sections, no links, no chunks -- never chunked at all.
`chunks_missing` and `no_text` send a reader to DIFFERENT fixes -- re-ingest
from the original vs. nothing ever extracted it -- and neither is "this
document is short", so they are never merged. A fifth basis, `unmeasurable`,
is what a FAILED READ reports: `_safe_rows` in blueprint.py turns a SELECT
naming a missing column into an EMPTY LIST, which is exactly how a broken
query reads as a document with no chunks, so every read here reports its own
failure and is never a clean bill of health.
TWO DERIVATIONS FOR THE TEXT AND THE ANSWERING ONE IS RECORDED as
`text_source`: `dic_chunk_links` first (the document's OWN record -- resolves
for 2 documents live), then `rag_chunks` by source_type/source_table/source_id
(`rag_source_id` -- 17 more). A SILENT fallback would hide that the link table
has gone stale, so the pane prints "0 of N of this document's own links
resolve". Both keys are required, so a compliance_reference row sharing an id
can never be served as this document's prose.
THE ORIGINAL HALF IS `originals.original_verdict` IMPORTED (dwr-fid-01), never
re-derived -- an AST test refuses a local copy. On a board that has not run
that migration the pane reads `original_unmeasurable`, NEVER
`original_absent`: reporting 43 documents as having lost their upload when the
column does not exist is a fabricated finding the size of the real one. The
limits band renders on the FULL render too -- saying it only on the degraded
page hides it on exactly the documents that look complete -- and an EMPTY
limits list renders NOTHING, because a standing "no known limitations" banner
is a claim, not an absence.
ANCHORS: every rendered chunk carries a stable `anchor_id`, so `#chunk-<id>`
is deep-linkable and survives a re-render. That is the PANE's coordinate
space and NOT the REVIEW one -- comments and suggestions index offsets into a
section's content (dwr-anchor-03/dwr-cmt-01) and this document has none -- and
the pane SAYS anchored review is unavailable rather than rendering a comment
box that would drop what a reviewer typed.

AND THE INGEST HAD NO SANDBOX DECISION AT ALL -- sandbox-coverage Gap 69.
`POST /document-intelligence/api/ingest` accepts an ARBITRARY file with NO
extension allowlist (extract_file falls through to a utf-8 decode for every
unknown suffix) and NO per-route size cap (only the platform-wide
MAX_CONTENT_LENGTH, ICDEV_MAX_UPLOAD_MB=50), and hands it to pypdf / pymupdf /
pdfplumber / pypdfium2 / python-docx / openpyxl / python-pptx / Pillow /
easyocr / pytesseract (which SPAWNS the external `tesseract` binary), plus
MarkItDown's .zip / .msg / .eml / audio expansion where it is installed. Gap
23 (.pptx), Gap 24 (BI dataset) and Gap 14 (NMCE config) all had an entry;
this did not, and `--check sandbox_coverage` asserts only that the DOCUMENT
exists and is linked, so nothing would ever have caught it.
Decision: sandboxed-on-demand -- the Gap 3 / Gap 25 class and its widest
member. `ingest_guard.evaluate_upload` is what makes that more than a
sentence: on ICDEV_STRICT_SANDBOX=1 a format reaching a native parser is
REFUSED at the route with a 415 and a stated reason.
IT IS A REFUSAL AND NOT ISOLATION, and says so everywhere it is reported. DIC
extraction is NOT routed through SandboxExecutor (that needs a container image
carrying the platform -- see the deployment note in tools/analyzers/sandbox.py)
and declaring a posture nothing consults is the declared-but-never-consumed
defect one layer up from the code it purports to govern.
THE STRICT SWITCH IS THE SHARED ONE: `tools.analyzers.sandbox
.strict_sandbox_enabled` IMPORTED, never a second reading of the env var, and
AST-pinned. THE PERMITTED CLASS IS DERIVED FROM `extractors._EXTRACTORS` -- an
extension is `text` only because the registry maps it to `_extract_text` -- so
a binary format added there cannot join the permitted class by being forgotten
in a second list. An UNKNOWN extension is `text` and CORRECTLY so: the
fallback is Path.read_text, so a .pdf renamed .foo never reaches a parser; the
classification is of the CODE PATH. .zip/.msg/.eml/.epub/.xls are `native` on
their WORST case, not on what happens to be installed.
DEFAULT IS UNCHANGED -- ICDEV_STRICT_SANDBOX is unset on every deployment this
ships to, so it refuses nothing today and owes no fire-rate survey.
NOT DONE, and named rather than implied: no per-route size cap (a 49 MB PDF is
inside the platform cap and reaches four PDF parsers and possibly OCR -- a
tighter cap refuses uploads that work today and needs its own survey); no
isolation on a permissive host; and /api/ingest/url + /api/ingest/youtube are
DIFFERENT routes (extract_video spawns yt-dlp) each owing their own entry.
