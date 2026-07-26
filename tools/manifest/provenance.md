# Provenance

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Provenance
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Provenance Registry | tools/provenance/registry.py | Unified source citation registry — indexes citations across subsystems into source_citation_registry for cross-subsystem provenance queries and trust scoring. `CITATION_TYPES` is the single source of truth for the `citation_type` CHECK constraint: `citation_type_check_sql()` derives the SQL and `repair_citation_type_constraint(conn)` re-applies it (PG: DROP/ADD CONSTRAINT; SQLite: table rebuild), so the constraint is never hand-written. `register_citation()` raises ValueError on an unlisted type rather than swallowing the CHECK failure into an empty id. | `--index-existing`, `--list-project`, `--json` | JSON |
| Web Citation & Fetch Provenance | tools/provenance/web_citation.py | Makes a fetched web page a first-class citeable source (oss-cite-01). Fetches through the central HTTP client, captures requested URL / final URL / redirect chain / HTTP status / fetched_at / sha256 / ETag / Last-Modified into the append-only `web_fetch_provenance` table, and registers a `web` citation. Optional fail-closed SSRF gate (`egress` in args/http_client.yaml, default off). Citation parsing/validation delegates to tools/quality/citation_grounding.py — this module only supplies the persisted allow-set. `capture()` records provenance for a response the caller already fetched. Mirrored to icdev/tools/provenance/. | `--fetch URL`, `--show FETCH_ID`, `--list [--project P]`, `--validate FILE`, `--init-db`, `--no-register`, `--json` | JSON |

