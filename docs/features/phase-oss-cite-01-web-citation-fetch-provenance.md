# CUI // SP-CTI

# oss-cite-01 — Make a fetched web page citeable

**Status:** shipped
**Date:** 2026-07-26
**Card:** `oss-` (OSS adaptation — RAGFlow / Crawl4AI / browser-use / STRIX)
**Migration:** `295_web_citation_fetch_provenance`

## The defect

The TRUST invariant requires that every inline `[source: ...]` citation validate
against a **persisted provenance record**. For a fetched web page, neither half
of that was possible.

**The citation half.** `source_citation_registry.citation_type` carries a CHECK
constraint, written by migration 149, that hardcodes ten values:

```
hitl, rag, prov_entity, prov_activity, canvas_ai,
slsa, sbom, compliance_evidence, agent_decision, manual
```

There is no `web`, `url`, or `crawl`. A fetched page could not be registered as
a first-class citation at all — the INSERT failed the constraint, and
`register_citation()` swallowed the exception and returned `""`. The caller saw
what looked like a successful registration of a citation that did not exist.

**The provenance half.** Provenance for a fetched URL was a URL string, a
content hash, and a metadata JSON blob hanging off whatever row happened to hold
it. Nothing recorded the HTTP status, the redirect chain, or the revalidators —
so a citation could not be re-checked later, and a page that resolved through
three hops to a mirror was indistinguishable from one served directly.

The consequence: everything `oss-filter-01` and `oss-filter-02` improve about
*extracting* web content still could not satisfy TRUST, because the extracted
content had nowhere citeable to live.

## What shipped

### 1. `citation_type` gains `web` — derived, never hardcoded

`tools/provenance/registry.py` now owns the vocabulary:

```python
CITATION_TYPES: tuple[str, ...] = (
    "hitl", "rag", "prov_entity", "prov_activity", "canvas_ai",
    "slsa", "sbom", "compliance_evidence", "agent_decision", "manual",
    "web",   # oss-cite-01
)
```

The CHECK constraint is **derived** from that tuple by
`citation_type_check_sql()`, and `repair_citation_type_constraint(conn)`
re-applies it. This follows the guardrail that a SQL CHECK must come from the
Python constant, and the repair shape that migration 271 used for the ACE state
constraints.

Two details that matter:

- **It must be a repair, not a `CREATE TABLE IF NOT EXISTS`.** That statement
  never alters a constraint on a table that already exists, so on every live
  database the stale ten-value CHECK would have survived untouched.
- **SQLite needs a table rebuild.** A CHECK cannot be altered in place, so the
  SQLite path does create-copy-drop-rename. This is not test-harness-only
  concern — SQLite is a real runtime backend here, and without the rebuild an
  `INSERT ... citation_type='web'` fails the stale CHECK.

The scan for existing values is scoped to the `citation_type` CHECK body
specifically. A naive scan of the whole stored DDL also picks up
`DEFAULT 'CUI'`, which never matches the constant — every call would report
`"repaired"` and rebuild the table forever.

`register_citation()` now **raises `ValueError`** for a type outside the tuple.
Previously an unknown type failed the CHECK and was swallowed into an empty
return, so a typo produced a "registered" citation that did not exist. Failing
loudly is the only way that distinction survives.

### 2. `web_fetch_provenance` — what a citation needs to stay checkable

`tools/provenance/web_citation.py` (mirrored to `icdev/tools/provenance/`)
persists, per fetch:

| Column | Why it is evidence |
|---|---|
| `requested_url` | what the caller asked for |
| `final_url` | where the server actually served from |
| `redirect_chain` | every hop in between, in order |
| `http_status` | the status of the response that produced the content |
| `fetched_at` | ISO-8601 UTC instant of the fetch |
| `content_hash` | sha256 of the exact bytes used |
| `etag` / `last_modified` | revalidators, when the server sent them |

`requested_url` and `final_url` are stored **separately on purpose**. A citation
saying "per https://example.gov/policy" is a different claim from one that
resolved through three hops to a mirror, and a record keeping only one of the
two cannot tell those apart afterwards.

This generalizes the richest provenance ICDEV already had for a fetched
artifact — `tools/genesis/reflexes/research.py::_export_signal`, which attaches
`evidence={"feed": ..., "fetched_at": ...}` to an exported GKP.

The table is **append-only** (registered in `APPEND_ONLY_TABLES` in
`.claude/hooks/pre_tool_use.py`, NIST AU). Every fetch inserts a new row: a
fetch is an *event*, and two fetches of the same URL a month apart are two
pieces of evidence even when the bytes are identical. `get_by_hash()` finds
prior observations of the same content.

A non-2xx response is **not** an error — the status is part of the record, and
"the page said nothing at that instant" is a citeable fact. The caller decides
what it means.

### 3. Citation validation delegates — nothing is reimplemented

Per the guardrail, `tools/quality/citation_grounding.py` does all parsing and
validation. This module contributes exactly one thing: the **allow-set of ids
that actually exist**, read out of `web_fetch_provenance`.

```python
rec = fetch_and_record("https://example.gov/policy", project_id="proj-1")
draft = f"The policy requires annual review [source: {rec['fetch_id']}]."
validate_web_citations(draft, project_id="proj-1")["valid"]   # True
```

`web_citation_gate()` is the promote/export gate, handing off to
`citation_grounding.citation_gate` so defects come back in the same shape as
every other drafting surface. `to_source_provenance()` projects a fetch onto the
shared `Provenance` record (`fetched_at` → `ingest_timestamp`, ETag →
`version_ref`), so a web source drops into `build_artifact_provenance` next to a
RAG chunk with no remapping.

`capture(response, url)` is deliberately separate from the fetch, so a call site
that already owns its HTTP call (`url_analyzer`, extractors, `source_scanner`)
can record provenance without re-fetching.

### 4. Egress

`fetch_with_provenance()` goes through `tools/http/client.py::request` (mTLS,
proxy, retry/backoff), never a bare `urllib`/`requests` call.

`args/http_client.yaml` gains an `egress` section, **default `enabled: false`** —
the same default-off semantics as the guard's original home in
`tools/doc_modernization/link_check.py`. When enabled, the guard runs before any
socket is opened (HTTPS-only, suffix allow/denylist with deny-wins,
resolve-then-reject on any private/loopback/link-local answer) and a denial
raises `EgressDenied`.

A guard that is **configured but unimportable fails closed**
(`(False, "guard_unavailable")`). An SSRF gate that silently no-ops when its
module is missing is worse than no gate, because the config claims cover.

`oss-filter-03` will promote the guard to `tools/http/egress_guard.py`; the
import already prefers that path as soon as it exists.

## Verification

```
pytest tests/provenance/test_web_citation.py -q     # 35 passed
```

Coverage includes: egress denial and fail-closed-on-missing-guard, redirect-chain
capture, header capture, hashing (str and bytes), the append-only re-fetch shape,
the `citation_type` CHECK repair on both backends (including the
rebuild-forever regression), `register_citation` raising on an unknown type, and
citation validation against persisted rows.

## Security

`docs/security/sandbox-coverage.md` **Gap 39** records the ingress decision:
**bypass-documented** for the response side (the module hashes bytes and writes
a row; it never parses, renders, or executes them), with the request side
covered by the explicit fail-closed egress gate rather than by a sandbox.

Residual risk, stated plainly: at the default `egress.enabled: false` this module
will fetch any URL a caller hands it, including an internal one. That is the
pre-existing platform default for outbound fetches, not a regression introduced
here.

## Files

| File | Change |
|---|---|
| `tools/provenance/web_citation.py` | new — fetch provenance + citation adapter |
| `icdev/tools/provenance/web_citation.py` | mirror |
| `tools/provenance/registry.py` | `CITATION_TYPES`, derived CHECK, repair, validating `register_citation` |
| `icdev/tools/provenance/registry.py` | mirror |
| `tools/db/migrations/295_web_citation_fetch_provenance/` | constraint repair + table create |
| `args/http_client.yaml` | `egress` section (default off) |
| `.claude/hooks/pre_tool_use.py` | `web_fetch_provenance` → `APPEND_ONLY_TABLES` |
| `tests/conftest.py` | both tables in `MINIMAL_ICDEV_SCHEMA` |
| `tests/provenance/test_web_citation.py` | new — 35 tests |
| `docs/security/sandbox-coverage.md` | Gap 39 |
| `tools/manifest/provenance.md` | two entries |
| `docs/reference/commands.md` | Provenance & Citation Commands |

## Follow-on

- `oss-filter-03` promotes the egress guard to `tools/http/egress_guard.py`.
- Call sites that fetch today (`url_analyzer.fetch_content`, extractors,
  `source_scanner`) can adopt `capture()` to gain provenance without changing
  their fetch.
