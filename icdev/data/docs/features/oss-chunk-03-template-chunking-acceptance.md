# CUI // SP-CTI

# Template Chunking — acceptance results (oss-chunk-03)

**Status:** complete
**Tests:** `tests/rag/test_chunking_acceptance.py` (13), fixtures in
`tests/fixtures/chunking_corpora.py`
**Measures:** `oss-chunk-01` (template chunking), `oss-chunk-02` (position columns)

---

## Verdict

**KEEP, on by default per source.** Structural templates do what they claim on the
document types they target, and the general path is provably unchanged.

| Template | Fixture | Result |
|---|---|---|
| `oscal_catalog` | 9 controls incl. 2 enhancements | **1 chunk per control**, none split |
| `stig_checklist` | 5 rules with Check/Fix text | **1 chunk per rule**, Check+Fix kept together |
| `general` | prose, no structure | **byte-identical** to the pre-template sliding window |

## Re-index cost

Embeddings are per chunk, so the chunk-count delta *is* the cost:

| Template | general | templated | delta |
|---|---|---|---|
| `oscal_catalog` | 3 | 10 | **+7 (+233%)** |
| `stig_checklist` | 4 | 6 | **+2 (+50%)** |
| `general` | 24 | 24 | **0 (+0%)** |

The catalog figure looks alarming and is the point: three sliding-window chunks
became ten because nine controls were previously being *merged into each other*.
The cost is real — 3.3× the embeddings for a control catalog — and it buys the
ability to retrieve one control without dragging in its neighbours.

`general` at +0% is what makes this safe to enable per source: the corpus does not
get more expensive unless a source is explicitly given a structural template.

## How "no regression on general documents" was established

**Not** by a corpus re-index and a benchmark run. The `general` template *is* the
pre-existing sliding window, so a general document takes the identical code path
and produces byte-identical output. A re-index would have been an expensive way
to observe a tautology.

This matters given `oss-meas-01`: that card spent two dispatch budgets before
anyone noticed the instrument couldn't detect what it was measuring. The right
question to ask first is always *"can this measurement produce a different answer
than the one I expect?"*

Which is why `test_the_wrong_template_does_not_preserve_structure` exists: it
chunks the control catalog with the *general* template and asserts it recovers
**less** structure. If that ever fails, the acceptance suite has stopped being
able to distinguish the templates and proves nothing — the same failure mode as a
golden set with no headroom.

## Two measurement traps, both hit while writing this

**Substring matching on control ids.** The obvious check — *"does `AC-2` appear in
exactly one chunk?"* — reports every parent control as split, because `AC-2`
substring-matches inside `AC-2 (1)`. The first draft of the acceptance test used
`^AC-2\b` and failed for this reason: the word boundary sits before the space, so
it matches the enhancement too. The fix is `^AC-2(?!\s*\()`.

**Enhancements are not splits.** `AC-2 (1)` getting its own chunk is *fidelity to
OSCAL*, not a defect — an enhancement is its own control with its own id, and
retrieval should be able to return it alone. A test that demanded parent and
enhancement share a chunk would have been enforcing a bug.

## What this does not establish

Retrieval quality. These are structural assertions: they prove a control is not
cut in half, not that chunking this way retrieves better. Measuring *that* needs
the golden set re-indexed under each template, and — per `oss-meas-01` — a golden
set with enough headroom to register the difference. The v2 48-query set now has
that headroom, so the measurement is possible; it is not done here.
