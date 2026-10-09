# CUI // SP-CTI

# dwr-anchor-06 — the 58 suggestions written against a token

Measured on the live PostgreSQL board (`icdev`) on **2026-09-08**. Every number
below is re-derivable with the commands in the last section; a figure quoted off
a moving board is not a measurement, so each carries its instant.

## The defect

`redline_drafter.draft_redline` built its prompt from `finding["entity_label"]`
— a bare token like `TLS 1.1` — and stored the row with `section_id=""` and
`anchor_basis="unanchored"`. Two consequences, and they are separate:

1. **The prose was never fitted to a place.** The model wrote a replacement
   passage having never seen the sentence around the token, so back-filling an
   anchor later would pin text to a span it was not written for — a
   plausible-looking edit nobody wrote. This is why the 58 are superseded and
   re-drafted rather than repaired in place.
2. **The row named nowhere to land.** Accepting one ran
   `UPDATE dic_sections ... WHERE section_id = ''`, matched zero rows and
   reported success. dwr-anchor-05 closed that half; this card retires the rows
   it produced.

## What is on the board

```
dic_suggestions           58 rows, all status=pending
  canvas_source           doc_modernization        58
  anchor_basis            NULL                     58
  section_id              ''  (empty)              58
  anchored (basis + section)                        0
```

Every one of the 58 resolves to its origin finding through the **structured**
route (`docmod_findings.redline_suggestion_id` → `supersedes_id`); the
`[docmod:<id>]` rationale prefix agrees on all 58 and was never needed as a
fallback. All 58 origin findings are `open`, so `draft_redline` would accept
them.

## Why not one of them can be re-drafted today

Two independent blockers, both measured, both outside this card:

| Blocker | Measurement | The fix |
|---|---|---|
| The installed drafter does not anchor | `redline_drafter` on `main` has no `resolve_passage`; it still writes `section_id=""` | **dwr-anchor-04**, `pr_opened` as of 2026-09-08 |
| The findings carry no span | **127 of 127** `docmod_findings` rows have `anchor_start IS NULL` | a **re-scan** — dwr-anchor-01/02 made the packs record a span, and no scan has run since |

The second is decisive on its own. Re-running the probe with
`anchors=True` — i.e. as if dwr-anchor-04 were already installed — every one of
the 58 refuses at the next rung:

```
With the ANCHORING drafter, per verdict:
  finding_has_no_span      58
origin route: {'link': 58}
```

A third blocker sits behind that one for most of the corpus: the document
holding 47 of the 58 suggestions carries **zero** `dic_sections` rows, so even a
span would have nowhere to point.

| doc_id | suggestions | origin `open` | findings with no span | `dic_sections` |
|---|---|---|---|---|
| `dic_doc_28e2ee4d984f3f35` | 47 | 47 | 47 | **0** |
| `fa86383687b5228cedc1eac1` | 5 | 5 | 5 | 6 |
| `71ce76cc8e6fba38d6658e5b` | 2 | 2 | 2 | 15 |
| `56cbde669113db5215c52025` | 2 | 2 | 2 | 2 |
| `dee93acc855e93465a1a7ddb` | 2 | 2 | 2 | 10 |

## The outcome, and why it is the right one

On this board today the tool reports **58 refusals and zero tokens spent**.

That is not a clean bill of health and it is not a failure of the tool — it is
the measurement the card asked for. Superseding 58 proposals without a
replacement would trade 58 misleading rows for 58 absent ones and call it
progress, so the tool **proves before it acts** and refuses the whole run when
the drafter cannot anchor. The order of the refusals is deliberate: each one
sends a reader to a different repair (merge dwr-anchor-04; re-scan the
documents; derive sections for `dic_doc_28e2ee4d984f3f35`), and folding them
into one "cannot re-draft" would name none of them.

Do **not** respond to this by relaxing a precondition. Each precondition is the
thing that stops the sweep minting 58 fresh copies of the defect it exists to
retire.

## Re-derive it

```bash
python -m tools.document_intelligence.suggestion_redraft --census
python -m tools.document_intelligence.suggestion_redraft --plan --json
python -m tools.document_intelligence.suggestion_redraft --apply --limit 5   # writes
```

The per-document table and the `anchors=True` verdict distribution:

```bash
python -c "
from tools.db.storage import get_connection
from tools.document_intelligence.suggestion_redraft import _targets, probe
from collections import Counter
c = Counter()
with get_connection() as conn:
    for s in _targets(conn):
        c[probe(conn, s, anchors=True)['verdict']] += 1
print(c.most_common())
"
```
