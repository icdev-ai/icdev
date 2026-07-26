# CUI // SP-CTI

# oss-filter-01 — `tools/http/page_extract.py`: two-pass fit_markdown content filter

**Status:** shipped
**Card:** `oss-` (OSS adaptation) — see `docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md`
**Sandbox decision:** Gap 37 in [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md)

## The defect

`tools/chat_router/url_analyzer.py` fetched a page, regex-stripped its tags, and
cut the result at `_MAX_CONTENT = 7000` characters. The cut is **positional**:
it keeps the first 7000 characters regardless of where the answer is. A long
article's conclusion, a spec's normative section, or a changelog entry below the
fold was silently discarded, and the LLM was handed 7000 characters of nav bar,
cookie banner and preamble instead.

## The fix

Crawl4AI's `fit_markdown` idea, reimplemented as pure deterministic Python.
**No new runtime dependency** — stdlib `html.parser` plus the `rank_bm25`
already pinned in `requirements.txt`. Explicitly *not* bs4, lxml, html2text,
markdownify or trafilatura.

```python
from tools.http.page_extract import extract

result = extract(html, query="how do I rotate the signing key?")
# -> {title, raw_text, fit_markdown, links, dropped_blocks, reason}
```

### Pass 1 — structural pruning

Every block gets a composite score from five signals, each weighted in
`args/page_extract.yaml`:

| Signal | What it catches |
|---|---|
| text density (chars per descendant tag) | markup-heavy widgets vs prose |
| link density | nav bars, "related posts", link farms |
| tag weight | `<article>`/`<main>` outrank `<nav>`/`<footer>` |
| id/class hints | `class="sidebar"` / `id="cookie-banner"` vs `class="entry-content"` |
| text ratio | this block's share of the page's total text |

`threshold_type: dynamic` nudges the bar per block — a semantic tag earns a
discount, a short block pays a penalty. One deliberate rule: **a high-value tag
earns its bonus only when the id/class signal is not negative**, so
`<section class="comments">` cannot ride in on `<section>`'s reputation. That
single condition was the difference between comment threads leaking into every
extraction and none of them doing so.

Headings are never pruned (they are structure, and cheap). `table`, `pre` and
`figure` are exempt from `min_word_threshold` — dense by nature, not chatty.

### Pass 2 — BM25 relevance

When the caller supplies a `query`, the surviving blocks are ranked with BM25
and only the relevant ones are kept. Position on the page is irrelevant.

Three refinements that make it work on real pages:

- **Section propagation** — a heading that matches the query pulls in its body
  down to the next heading of the same or higher level (capped by
  `max_section_blocks`). A matched heading with its paragraph stripped away
  answers nothing.
- **Light suffix stemming** — a five-rule, config-driven suffix stripper so a
  query for "report a vulnerability" matches a `## Reporting` heading. Not a
  Porter stemmer and not trying to be one; the rules live in args.
- **`min_blocks` floor** — a query that matches nothing still returns the top
  ranked blocks rather than an empty string.

`rank_bm25` is used when importable; a pure-Python `_FallbackBM25` reproduces
`BM25Okapi` exactly (including its negative-IDF epsilon floor) so output does
not silently change between environments. A test asserts the two agree to six
decimal places.

### Markdown rendering

Headings, ordered/unordered/nested lists, tables, `<pre>` blocks, blockquotes
and definition lists all render as markdown. Links are **reference-style**:

```markdown
Publish the public half to the [JWKS endpoint][1]

## References

[1]: https://jwks.example.gov/keys
```

Raw URLs never inline into the prose the LLM reads — which cuts tokens and
blunts prompt-injection-via-URL. `javascript:` and fragment hrefs are emitted as
plain text, never as links.

## FORGE layer separation

Every threshold is in `args/page_extract.yaml`. The module contains no magic
numbers and **refuses to invent defaults** — if the args file cannot be found it
raises rather than falling back to hardcoded values. `test_thresholds_come_from_args_not_code`
proves the args are load-bearing by changing the outcome from the config alone.

## Results

Measured on the fixed 20-page corpus in `tests/fixtures/page_extract_corpus.py`
(realistic chrome — nav, cookie banner, sidebar, comments, footer, analytics
scripts — around 20 distinct content bodies):

| Metric | Target | Actual |
|---|---|---|
| Token reduction (with query) | ≥ 50% | **73.8%** (21,226 → 5,554 words) |
| Answer preserved | all 20 pages | **20/20** |
| Byte-identical output for identical input | required | **yes**, asserted per page |

Answer quality is asserted, not assumed: each corpus entry names the sentence a
caller asking its query actually wants, and the suite fails if the filter drops
it. Several answers sit past the 7000-character positional cut —
`test_relevance_beats_positional_truncation` asserts those are returned, which
the old code could not do.

## Integration

`url_analyzer.fetch_content(url, query=None)` now routes HTML through
`page_extract`, falling back to the old regex strip if extraction raises or
returns nothing. The `[:_MAX_CONTENT]` cap survives only as a backstop. The
signature is backward compatible — existing callers pass no query and get
prune-only extraction, which is still strictly better than the regex strip.

Plumbing alone would have left pass 2 dead at the site the task was aimed at,
so `analyze()` supplies one: it already derives a canvas **lens** describing
what the reader is after (`_CANVAS_LENS[canvas_type]`, else `_DEFAULT_LENS`),
and that lens is now computed above the fetch and passed as the retrieval
query. Blocks are therefore kept by relevance to the lens rather than by
position. `tests/chat_router/test_url_analyzer_query_wiring.py` pins the full
hop — `analyze()` → `fetch_content(query=…)` → `extract(query=…)` — plus the
regex-strip fallback and the error short-circuit the reorder moved past.

The other two callers (`tech_writing_assist.py`, `tfw_chat_agent.py`) pass no
query and keep prune-only behaviour.

## Files

| File | Role |
|---|---|
| `tools/http/page_extract.py` | the module (mirrored to `icdev/tools/`) |
| `args/page_extract.yaml` | every threshold (mirrored to `icdev/args/`) |
| `tests/fixtures/page_extract_corpus.py` | fixed 20-page corpus |
| `tests/http/test_page_extract.py` | 60 tests |
| `tools/chat_router/url_analyzer.py` | consumer, defect fixed — lens supplied as the query |
| `tests/chat_router/test_url_analyzer_query_wiring.py` | 6 tests pinning the wiring |
| `docs/security/sandbox-coverage.md` | Gap 37 decision |
| `tools/manifest/security.md` | tool registration |
