# CUI // SP-CTI

# Cortex response cache — enabled (ctx-perf-06)

`tools/cortex/cache.py` shipped correct, wired at `tools/cortex/api.py` and
`enabled: false`. No consumer ever flipped it. This change makes it safe to flip
and flips it.

## What was blocking the flip

**1. Cached objects were shared by reference.** `put_by_key` stored the live
result and the facade returned it verbatim, so every hit handed out the *same*
`CortexResult` instance. One caller doing `result.text = trim(result.text)` or
`result.metadata["seen"] = True` would silently rewrite the answer every
subsequent hit was served, for the whole TTL, with nothing in the audit trail to
show it.

Entries are now deep-copied **in and out**. Copy-on-read alone is not enough: the
caller that produced the entry holds the object that was stored, so the write
side must copy too. If a payload cannot be copied, the entry is *not cached*
(write) and the read is served as a *miss* (read) — never as the stored instance.

**2. There was no invalidation** beyond TTL expiry and LRU eviction.

## The invalidation decision

`cortex.ask` was removed from the default `operations` list rather than hooked.

`ask` is live NL→SQL over the operational database, so its correct invalidation
trigger is "any write to any table the generated SQL touched". Those writes are
authored by every subsystem on the platform and — decisively — mostly by *other
processes*: the kanban runner, reflex daemons, ingestion workers, an operator at
`psql`. This cache is per-process and in-memory, so it cannot observe them at
all. A hook here would have covered only the minority of writes that happen to
share the interpreter while advertising a freshness guarantee it cannot keep. A
partial invalidation hook is worse than none.

The other four operations stay cacheable on different grounds:

| Operation | Why it is cacheable |
|---|---|
| `cortex.complete` | pure function of (text, system_prompt, function, sampling args) |
| `cortex.classify` | pure function of (text, labels); degraded-heuristic results bounded by a 600s TTL |
| `cortex.extract`  | pure function of (text, schema) |
| `cortex.search`   | reads the RAG corpus, which **does** have an in-process choke point |

So `search` gets the invalidation `ask` cannot have: `cache.invalidate(reason)`
is called from `tools/rag/ingestion_manager.py` (`ingest_source`,
`ingest_single_record`) and from `tools/mcp/rag_server.py`
(`handle_rag_delete_source`). Deletion is the sharper case — a cached answer can
keep citing a source that no longer exists. The purge is whole-cache: the key is
a digest, so there is no way to select the subset of entries a given corpus
change touched, and over-purging costs a recompute while under-purging serves a
stale answer. It is skipped when nothing was written, so a dedup-only sweep does
not throw away warm entries.

`cortex.ask` keeps its 30s entry under `ttl_seconds` deliberately: an operator who
knowingly adds it back to `operations` gets the short bound rather than silently
inheriting `default: 300`.

## What did not change

The security model is untouched and was already sound:

- the key folds `tenant_id` + `classification` + `domain` + `air_gap`, so a hit
  can never cross any of those boundaries;
- only the **final governed (post-redaction)** result is stored, so a hit never
  bypasses egress governance;
- every hit still writes a `cortex_audit` row (`cache_hit=true`, `cost_usd=0.0`)
  — the append-only NIST-AU trail and `/cortex/metrics` stay complete. This is
  asserted end-to-end against the shipped config, not just a fixture.
- `cortex.govern` is never cached (it returns a report, not a result).

## Operating it

```yaml
# args/cortex_config.yaml
cache:
  enabled: true
  max_entries: 512           # read ONCE at first use — a change needs a restart
  operations: [cortex.complete, cortex.search, cortex.classify, cortex.extract]
```

- **Rollback:** set `cache.enabled: false`. The facade reads the flag per call,
  so this takes effect on the next call — no code change, no restart.
- **`max_entries` is read once**, when the singleton is built. Editing it in a
  running process does nothing until `cache.reset()`, i.e. restart the service.
- `cache.reset()` drops the singleton (and re-reads `max_entries`);
  `cache.invalidate(reason)` purges entries and keeps the singleton.

## Test isolation

The cache is a process-wide singleton, so with it enabled by default a facade
call leaks its answer into every *later* test issuing an identical call. An
autouse fixture in `tests/conftest.py` resets it around every test, sweeping both
`tools.cortex.cache` and `icdev.tools.cortex.cache` (distinct module objects with
their own singletons). Cross-call reuse is the feature in production and
pollution in a suite — reset it rather than weaken it.

Tests: `tests/cortex/test_response_cache.py` (37, gated in
`args/ci_test_files/core.txt`).
