# Where on the page did each word SIT? The layer a left pane renders from (dwr-fid-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.document_intelligence.page_geometry --survey [--json]
python -m tools.document_intelligence.page_geometry --doc <doc_id> [--page 1]
python -m tools.document_intelligence.page_geometry --backfill --limit 5
python -m tools.document_intelligence.page_geometry --limits
python tools/db/migrate.py --up            # 20260908091858: dic_page_words / dic_doc_runs / dic_document_geometry
```

UI: /document-intelligence/doc/<doc_id> -> "Page Layer"
API: GET /api/documents/<id>/geometry | /pages/<n>/words | /runs -- GET with
no POST sibling. Geometry is captured at INGEST, while the upload's temp file
is still on disk; a route that could TRIGGER a capture would put a
multi-second pdfplumber pass on a page render and, for an upload, would have
no file left to read.
`extractors._extract_pdf_text` ends every one of its four passes in
`extract_text()` -- a string and a page COUNT -- so nothing recorded WHERE a
word sat and a positioned-text view had no coordinate space to render into.
TWO FIDELITY STORIES, TWO TABLES, NEVER MERGED. `dic_page_words` is PDF only:
one row per word with its box in PDF POINTS, `top` measured from the page top
(pdfplumber's convention, and CSS's, so a renderer converts nothing).
`dic_doc_runs` is DOCX only -- paragraph/run order, style name and bold/italic
from python-docx -- and has NO `page` column, because OOXML has no pages until
something renders it and a NULL page on a table called `page_words` reads as a
box we failed to measure rather than one that CANNOT EXIST. Page DIMENSIONS
are per PAGE, not per document (measured on this board: 612x792 AND
595.3x841.9), and live on the geometry row's `pages_json`; a renderer scaling
by a document-wide guess puts every word slightly out of place and nothing
looks broken enough to notice.
pdfplumber, NEVER PyMuPDF. requirements.txt:218-220 refuses to declare
pymupdf/fitz (dual-licensed AGPL-3.0 / Artifex-commercial) and it IS installed
on this host -- so a pymupdf implementation would have looked perfect locally
and produced NOTHING on a clean install or an air-gapped one. Only an AST test
can see that difference, so an AST test is what pins it.
`use_text_flow=True` IS THE CARD'S ONE REAL DECISION, and it was MEASURED, not
reasoned. Words placed in the document's own stored text, every readable PDF
on the live board 2026-09-08:
  constitution.pdf 19p  flow=False 1,334/9,178  14.5%  flow=True 9,178/9,178 100.0%
  SOP09-36.pdf     16p  flow=False 3,232/3,347  96.6%  flow=True 3,346/3,347  99.9%
  ArtOfWar.pdf     20p  flow=False 2,568/3,749  68.5%  flow=True 2,568/3,749  68.5%
Better on two, IDENTICAL on the third, worse on none -- and the word COUNT and
every BOX are the same either way, so it costs nothing. constitution.pdf is
TWO-COLUMN: the default visual sort walks each LINE across both columns while
every text extractor reads the stream column by column, and the two orders
share almost no runs. This is not a heuristic of ours; it is the order the PDF
declares, which is the order pymupdf and pypdf produce their text in. The
consequence to know: `word_index` is STREAM order, not visual reading order --
a renderer does not care, and a consumer wanting the page read aloud should
sort by (top, x0) rather than have this table guess at columns.
ArtOfWar's 68.5% is NOT that flag's doing and is not fixable here: that PDF
places characters with NO SPACE GLYPHS, so pdfplumber glues whole lines into
one "word" in BOTH modes (mean word length 11.5 against 4.7 and 6.2). Those
runs are not in the spaced text, so they cannot be placed. The BOXES stay
correct. Reported, never repaired, and never averaged away.
CHAR OFFSETS INDEX THE DOCUMENT'S OWN TEXT, OR THEY ARE NULL. The trap is that
the WORDS come from pdfplumber while the TEXT comes from whichever pass won --
measured, that is NEVER pdfplumber (10 of 13 PDFs pymupdf, 3 pypdf). So
`align_char_offsets` scans the STORED text: each page's segment located by the
`--- Page N ---` marker every pass writes, then a greedy forward walk bounded
at ALIGN_LOOKAHEAD=200 chars so a short common word cannot bind to its next
occurrence a paragraph later. An unplaceable word gets NULL -- never 0, which
would point every one of them at the first character of the document -- and a
miss does NOT move the cursor, so one dropped ligature cannot desynchronise
everything after it. `char_basis`: document_text | text_changed | unaligned |
not_attempted. `text_changed` is a BACKFILL whose re-extraction hashes to
something other than the recorded `content_sha256`: the offsets are WITHHELD
and the BOXES are KEPT, because where a word sits on the page does not depend
on which library read it. `_sha256_text` hashes with `errors="replace"`
EXACTLY as `ingest_orchestrator._sha256` does -- two hashes are only comparable
if one rule produced both.
AN EMPTY WORD LIST IS SEVEN DIFFERENT THINGS and only ONE is about the
document: extracted | truncated | no_text_layer (the file opened and yielded
ZERO words -- a scanned page, a MEASURED zero) | unsupported_format |
disabled_by_env | library_unavailable | source_unreadable | failed. A document
with no geometry STILL GETS A ROW, so "nobody looked" can never read as "the
pages are blank" -- absence and emptiness are the two things this whole module
exists to keep apart.
TWO RATES THAT REFUSE TO FABRICATE, and the first was caught on the live API
during this card: a `text_changed` document reported `align_rate_pct: 0.0`
beside 3,347 words, which reads as "every word was tried and none could be
placed" -- an alarming claim about the extraction -- when alignment was
deliberately SKIPPED and the boxes are perfect. The rate is None unless
`char_basis` is in ALIGNMENT_ATTEMPTED_BASES. And 3,346 of 3,347 rounds to
100.0 at one decimal place, so `_rate` FLOORS to 99.9: 100.0 is reserved for a
rate that IS 100, and a display reading a perfect score for an imperfect one
is args/perfect_score_gate.yaml's defect one rounding away.
THE COST IS REAL, BOUNDED, AND THE BOUND IS REPORTED. Measured 2026-09-08:
constitution 19p/9,178 words/1.27s, ArtOfWar 130p/22,808/6.24s -- ~0.05s and
100-500 ROWS PER PAGE, so this board's 490 PDF pages are ~100k rows and a
2,000-page manual is one upload away. ICDEV_DIC_GEOMETRY_MAX_PAGES (50),
_MAX_WORDS (50,000), _MAX_RUNS (20,000); a hit bound is `truncated` carrying
pages_extracted/pages_total and the reason, never a quietly short list.
ICDEV_DIC_WORD_GEOMETRY=0 switches it off and the ingest result SAYS
`disabled_by_env`. Nothing prunes; `--survey` reports the row counts so the
growth is measured.
BACKFILL REACH IS dwr-fid-01'S, which is why that card had to land first. It
asks `originals.original_verdict` -- the ONE predicate for "is there a file to
re-read", never a second opinion. Measured 2026-09-08: 4 of 13 PDFs still had
a readable source and the other 9 point at deleted temp files; all four read
`text_changed`, because they were ingested 2026-06-17 as plain `pymupdf`,
before oss-table-01's `+tables` append existed. That is the guard working, not
failing. Live after backfill: 11 documents with a geometry row (4
pdf_word_box, 7 unsupported_format), 30,340 word rows.
RENDER PROOF, and it is the card's DONE criterion: page 5 of SOP09-36 rendered
from `dic_page_words` alone lands every paragraph, indent, line ending, header,
footer and the page number where the source PDF has them (side by side in
playwright/screenshots/dwr-fid-02-side-by-side-p5.png). Only the font
substitution and the graphic rules differ -- this is a TEXT layer, not a
raster.
NOT built, and named: nothing prunes these rows; the 9 PDFs whose temp file is
gone can never be backfilled; `dic_doc_runs` is empty on this board because it
holds no DOCX upload (an empty read there is "no DOCX has been ingested", not
a broken writer); and a word's box is NOT a link to a dic_sections offset --
`char_start` indexes the document's extracted text, and mapping that onto a
section's own coordinate space is dwr-anchor-05's.
