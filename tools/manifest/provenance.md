# Provenance

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Provenance
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Citation Types | tools/provenance/citation_types.py | oss-cite-01. Canonical `CITATION_TYPES` vocabulary for `source_citation_registry.citation_type`, plus `check_constraint_sql()` which RENDERS the SQL CHECK clause. Migration 295 and pg_consolidated.sql are generated from it and `tests/provenance/test_citation_types.py` asserts all three still agree — a value added here without regenerating the SQL fails CI instead of failing at INSERT time. Adds `web` so a fetched page is citeable | (import) | tuple / CHECK clause |
| Web Citation Registration | tools/provenance/registry.py | `register_web_citation()` writes BOTH halves: the registry row that makes a fetched page citeable and the append-only `web_fetch_provenance` row recording what was actually served (requested vs final URL, status, sha256, content-type/length, ETag/Last-Modified, fetched_at, fetcher). `get_web_provenance()` returns every fetch newest-first — the history IS the evidence-drift signal. `register_citation()` now RAISES on an unknown type rather than swallowing the CHECK violation and returning an empty id | (import) | {citation_id, provenance_id, content_hash} |
| Provenance Registry | tools/provenance/registry.py | Unified source citation registry — indexes citations across subsystems into source_citation_registry for cross-subsystem provenance queries and trust scoring. | `--index-existing`, `--list-project`, `--json` | JSON |

