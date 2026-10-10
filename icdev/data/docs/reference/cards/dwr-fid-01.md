# The UPLOADED original is KEPT, content-addressed, before its temp file goes (dwr-fid-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.document_intelligence.originals --survey [--json] [--verify]   # verdict per document, counted
python -m tools.document_intelligence.originals --root                         # retention root, files, bytes
python tools/db/migrate.py --up            # 20260908003311: original_path / original_sha256 / original_retained_at
```

/api/ingest saved the upload to a NamedTemporaryFile and os.unlink()'d it in
the ingest thread's `finally` while dic_documents.filepath kept pointing at
it. MEASURED 2026-09-07 on the live board: 55 documents, 29 with a filepath
that no longer exists (all under %TEMP%), 15 with none (generated in-canvas),
11 readable. Now retain_original() copies the bytes to
<root>/<sha256[:2]>/<sha256><suffix> (hash while copying, atomic os.replace,
dedup by content) BEFORE ingest_file runs, and the temp deletion is KEPT.
RETENTION IS A STORAGE DECISION: ICDEV_DIC_ORIGINALS_DIR is the root (default
data/document_intelligence/originals, git-ignored beside the rmf-wp-02
artifacts dir which was NOT ignored before), ICDEV_DIC_RETAIN_ORIGINALS=0
switches it off, and off is REPORTED on the ingest result (`original.reason:
disabled_by_env`), never silent. Growth is one file per DISTINCT upload and
nothing prunes it; the survey reports files/bytes on disk.
SIX VERDICTS, and `absent` is never one bucket: retained | retained_missing |
retained_mismatch (--verify re-hashes) | source_on_disk (a CLI ingest whose
file persists) | absent (THE FINDING) | no_source (generated in-canvas, there
was never an upload -- 15 of the 55, and filing them as findings buries the
29 real ones). The collection listing carries `original_status`; the SELECT
names the new columns only when the CATALOGUE says the live table has them,
so an un-migrated board still lists (a SELECT naming a missing column,
swallowed by _safe_rows, is an EMPTY list -- the "silently broken" the card
names) and the survey says `original_columns_present: false` rather than 55
`absent`. Unmeasurable, never clean, over no documents.
NOT retained here, and named: CLI/batch ingests (the operator's own file
persists at filepath); nothing prunes; the 29 are gone for good.
