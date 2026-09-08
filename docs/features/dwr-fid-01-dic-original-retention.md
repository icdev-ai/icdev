# dwr-fid-01 — Stop deleting the uploaded original

**Classification:** CUI // SP-CTI
**Card:** dwr-fid-01 (project `dwr`, epic `fid`)

## What was wrong

`POST /document-intelligence/api/ingest` saved the upload to a
`NamedTemporaryFile`, handed that path to `ingest_file`, and `os.unlink()`'d
it in the ingest thread's `finally`. `dic_documents.filepath` kept pointing at
the deleted path. Nothing else held the bytes.

Measured on the live PG board, read-only, 2026-09-07:

| population | count | what it is |
|---|---|---|
| documents | 55 | |
| `filepath` still exists | 11 | CLI / batch ingests of a real file, and test fixtures |
| `filepath` recorded, file gone | 29 | every one under `%TEMP%` — the upload route |
| `filepath` empty | 15 | generated in the canvas (docgen, IDR) — there never was an upload |

So 44 of 55 have no original on disk (the card said 43; the board moved by one
between the card and this reading). Not all 29 are uploads: the survey's
examples show several under `pytest-of-<user>` — test runs that reached the
canonical board through a bare `get_connection()` and left rows behind. They
are `absent` all the same, and the survey names them so a human can tell the
residue from the real loss. Without the original there is no page
geometry and no fidelity, ever: an extractor can be re-run over a retained PDF
and never over a deleted temp file. The rest of the `dwr` epic depends on that
file existing.

## What shipped

### The retention seam — `tools/document_intelligence/originals.py`

`retain_original(src, filename)` copies the upload into a content-addressed
store: `<root>/<sha256[:2]>/<sha256><suffix>`. It hashes **while** copying
(one read of the source), lands under a `.partial-` temp name in the final
directory, then `os.replace`s onto the content address — so a crash mid-copy
and two concurrent uploads of the same bytes both leave either the complete
file or nothing. Two uploads with the same bytes share one file; the second
call reports `deduplicated: True`.

`record_original(conn, doc_id, retained)` writes three columns on the
`dic_documents` row (migration `20260908003311_dic_original_retention`):

| column | meaning |
|---|---|
| `original_path` | where the bytes are **kept** (`filepath` stays what ingest **read**) |
| `original_sha256` | so a reader can prove the retained file is the upload |
| `original_retained_at` | the timestamp a pruning policy would key on |

Every column is nullable with no default: NULL is *not retained*, and a
default would make a generated document indistinguishable from a retained one.

### The route

In `api_ingest`'s thread, the original is retained **before** `ingest_file`
runs and recorded **after** it (ingest writes the row with `INSERT OR
REPLACE`, so the update has to follow). The temp file's deletion in `finally`
is **kept** — the copy is the fix, not keeping the temp. A structural test
pins both the ordering and the unlink.

A retention failure never blocks the ingest and is never silent. The done
event, the job row and the result cache all carry an `original` block:

```json
"original": {"retained": true, "recorded": true, "reason": "ok",
             "path": ".../originals/3f/3f9a...c1.pdf", "sha256": "3f9a...c1",
             "byte_size": 20481, "deduplicated": false,
             "retained_at": "2026-09-07T23:41:02+00:00"}
```

and a failure appends to `errors`: `original not retained: <reason>` or
`original retained at <path> but not recorded on the document row:
column_absent`. That last one is the un-migrated case — the file **is** kept,
and the row cannot say so until `python tools/db/migrate.py --up` runs.

### Retention is a storage decision, so it has knobs and a measurement

| knob | default | effect |
|---|---|---|
| `ICDEV_DIC_ORIGINALS_DIR` | `data/document_intelligence/originals` | the root; the shape `exporter.artifact_dir()` already uses |
| `ICDEV_DIC_RETAIN_ORIGINALS` | on | `0` ingests without keeping the upload; the result says `reason: disabled_by_env` |

Growth: one file per **distinct** upload content, and nothing prunes it. The
survey reports `files_retained` and `bytes_retained` so the growth is a number
somebody can read; a pruning policy (keyed on `original_retained_at` and the
collection's existing `retention_days`) is a separate decision this card does
not make.

Both `data/document_intelligence/originals/` and the pre-existing
`data/document_intelligence/artifacts/` (rmf-wp-02's exports) are now
git-ignored, anchored, and registered in
`tests/test_generated_artifacts_untracked.py`. Neither was ignored before:
they hold CUI documents belonging to whoever uploaded them, and a broad
`git add` or the auto-commit hook would have published them from this public
repo.

### The verdict vocabulary — an absent original is never one bucket

`original_verdict(row)` reads one `dic_documents` row and says exactly one of:

| verdict | meaning | finding? |
|---|---|---|
| `retained` | `original_path` recorded and the file is there | no |
| `retained_missing` | recorded, file gone — the root was moved or pruned | yes |
| `retained_mismatch` | (`--verify` only) the file's sha256 is not the recorded one | yes |
| `source_on_disk` | nothing retained, but `filepath` still exists — readable, not retained | no |
| `absent` | nothing retained and `filepath` is gone | **yes** |
| `no_source` | no `filepath` at all: generated in-canvas, nothing to retain | no |

`no_source` is the bucket that had to exist. Fifteen of the 55 documents were
never uploaded; reporting them as "originals absent" would file fifteen
findings nobody can act on and bury the 29 real ones.

Surfaces:

* `GET /document-intelligence/api/collections/<id>/documents` — every row
  gains `original_status` and `original_detail`, and the Collections page
  draws a red `original absent` badge (green `original kept`, grey `source on
  disk`, nothing for `no_source`).
* `python -m tools.document_intelligence.originals --survey [--json] [--verify]`
  and `--root`.

### An un-migrated board is reported, not broken

The listing SELECT names the retention columns only when the live table
carries them — probed from `information_schema` / `PRAGMA table_info`, never
guessed. A board that has not run the migration still lists every document,
each reported `absent` / `source_on_disk` / `no_source` from `filepath`, with
`columns_present: false` beside the verdict. The alternative — a SELECT naming
a missing column, swallowed by `_safe_rows` — returns an empty list, which is
the "silently broken" the card names.

The survey states the same fact as `schema.original_columns_present`. On a
board where the columns are absent nothing can have been retained, so the
survey says the migration has not run rather than reporting 55 `absent` as if
the writer had failed. A board with no documents is `unmeasurable`, never
clean.

## Measured

Live PG board, read-only survey through the shipped module, 2026-09-07 (after
the change, before the migration ran there):

```
state: findings   documents: 55   findings: 29
schema: retention columns ABSENT (migration 20260908003311 not applied here)
  absent          29
  source_on_disk  11
  no_source       15
  retained         0
```

End to end under test (`tests/document_intelligence/test_original_retention.py`):
a PDF POSTed to `/api/ingest` is retained under the originals root before
ingest runs, the retained file's sha256 re-derived from its bytes equals the
`original_sha256` on the row, the listing reports it `retained`, and the temp
file is gone afterwards.

## Not done here, and named

* **CLI and batch ingests are not retained.** `python -m
  tools.document_intelligence --ingest <path>` reads the operator's own file,
  which persists at `filepath` (`source_on_disk`). Retaining those too is the
  same call one layer down in `ingest_file`; it changes what a batch ingest
  costs on disk and is its own decision.
* **Nothing prunes the root.** Deliberate: the card asked for a knob and a
  documented growth, not a silent retention policy.
* **The 29 pre-existing documents cannot be recovered.** Their bytes were in
  `%TEMP%` and are gone. They are reported `absent`; re-uploading is the only
  repair.
* **Re-extraction over the retained file** (page geometry, fidelity) is the
  rest of the `dwr-fid` epic, not this card.
