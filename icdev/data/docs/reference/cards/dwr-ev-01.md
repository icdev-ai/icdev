# An AUTHOR's upload is a declared source, ranked top, and the catalog it contradicts survives (dwr-ev-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.document_intelligence.author_evidence import parse_assertions, record_assertions
  assertions = parse_assertions('[{"entity": "Catalyst 6500", "type": "hardware_model",'
                                ' "vendor": "cisco", "status": "fielded", "as_of": "2026-08-01"}]')
  ingest_file(path, "estate", author_assertions=assertions)      # same transaction as the document
```bash
python -m tools.currency.entity_currency --resolve "catalyst 6500" --entity-type hardware_model
```

Upload: POST /document-intelligence/api/ingest with a multipart `author_assertions`
JSON field (400 on a malformed entry, refused WHOLE — half an author's
declaration recorded silently is worse than none and a reason).
THE CHANGE IS EVIDENCE PRECEDENCE, NOT A UI. `dic_author_assertions` (migration
20260908003920, one row per document x entity x version) is the FIFTH source
in args/entity_currency.yaml, kind `author_supplied`, declared exactly the way
the endoflife.date feed and the curated catalog are, plus ONE new optional
key: `precedence: 0`, applied FIRST in `resolution.order`. Every source shipped
before it declares none, ties on DEFAULT_PRECEDENCE (100) and falls through to
`authoritative` exactly as before -- asserted: the catalog still beats a newer,
more confident feed. `precedence` is an EVIDENCE ORDERING (inventory_feeds.yaml's
idiom), not a confidence a bumped prior could overturn and not `authoritative`,
which stays the curated catalog's word. An author's statement is the latest
fact about THIS ESTATE; NIST EOL is a fact about a VENDOR's support.
ONE RESOLVER, AND THREE CARRIERS THAT NOW OBEY IT. `entity_currency.resolve()`
ranks, keeps every loser under `others` (each with its `rank`, `authoritative`,
`precedence`, `source_kind`) and reports `conflict: true`. The Cortex `currency`
rung carries that rank on every structured claim (`EntityClaim.rank`) and the
docmod lane `_currency_lane` sorts by it -- it USED to re-sort by
authority-then-confidence, a second copy of the rule that would have handed the
pack the catalog's verdict while the store handed the author's. `author_evidence`
itself contains no ranking and no model, pinned by AST; the author's status
word maps onto VERDICTS through the YAML `value_map`, a lookup.
TWO CLOCKS: `as_of` is the AUTHOR's (`as_of_basis: author_stated`), or the
upload time labelled `upload_time` so a defaulted clock is never read as a
stated one; `created_at`/`observed_at` are ours. Several statements about one
entity keep every row in dic_author_assertions and the store keeps the NEWEST
author clock (`order_by: [as_of, created_at]`). Two AUTHORS disagreeing is NOT
two store rows today -- that is dwr-ev-02's attributed SME assertion, named.
REACHES THE DRAFTER THROUGH THE EXISTING SEAMS ONLY: cortex.resolve ->
tools/doc_modernization/evidence.py (the ranked answer, `currency_assertion()`
now returns `others`), and the brokered rung -- `icdev_author_evidence`, a
credential-free local-DB DataBridge connector (table `author_assertions`,
roles docgen_analyst + cortex_analyst, descriptor `dic-author-evidence-local`)
serving the statements AS MADE (`ranked: false`) with one access-log row per
read. Never a private SELECT on the seam, pinned by AST. Shipped ceiling
UNCLASSIFIED like every grant; raising it is a deployment decision.
NOT built, and named: no extraction of assertions from prose (a `text_pattern`
claim can never reach a pack -- TRUST rule 2); the MCP `dic_ingest` handler
(tools/mcp/gap_handlers.py) still calls an `IngestOrchestrator` class that
does not exist in ingest_orchestrator.py, a pre-existing break this card did
not touch; tests/test_dic_ingest_orchestrator.py (ungated) asserts one chunk
link per chunk with embed=False, red since dic-ingest-link-01 (2026-08-22).
