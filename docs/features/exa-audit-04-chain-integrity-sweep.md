# Audit Chain Integrity Sweep (exa-audit-04)

**Classification:** CUI // SP-CTI
**Controls:** NIST 800-53 AU-9 (Protection of Audit Information), AU-10 (Non-repudiation)

## What this adds

`tools/blockchain/provenance_verifier.py::verify_audit_integrity` answers *"is row N
intact?"*. Nothing answered *"has this table been tampered with?"* — and you cannot get
there by calling a per-row verifier 80,000 times.

- **`tools/audit/chain_sweep.py`** — one ordered pass over the chain, bucketing every row.
  CLI with `--json` / `--gate`, and `sweep_chain()` as a library call.
- **`GET /api/govchain-provenance/chain-health`** — the sweep, verbatim.
- **`/provenance` → "Audit Chain Integrity"** — the four states rendered distinguishably.
- **Genesis `audit` reflex** — runs the sweep on the existing daily cadence.
- **MCP tool `audit_chain_sweep`** — agent-reachable, declared read-only.

## The four buckets

| Bucket | Meaning | Is it a problem? |
|--------|---------|------------------|
| `verified` | Chained; digest recomputes and the link to `id - 1` holds. | No. |
| `pre_cutover` | Written before the chain writer existed. NULL hash. | **No** — absence of evidence, not evidence of tampering. |
| `unchained` | Written *after* cutover by a call site that INSERTs into `audit_trail` directly. | Known structural gap. Not tampering. |
| `broken` | Chained, but the digest or the link does not hold. | **Yes. This is the tamper signal.** |

### Why four and not three

The task asked for three states. There are four because the third real state exists
whether or not we name it: 156 files under `tools/` INSERT into `audit_trail` without the
chain columns, while 154 go through `log_event`. Those rows are neither old nor tampered.

- Folding `unchained` into `broken` would put hundreds of rows a week into the alarm
  bucket for a known gap. A tamper alarm that cries wolf is not a tamper alarm.
- Folding it into `pre_cutover` would misdate them — they are not legacy rows.

The panel therefore reports all four, and the acceptance-critical separation
(`broken` ≠ `pre_cutover`) is pinned by
`tests/test_audit_chain_sweep.py::test_tampering_after_legacy_rows_still_reports_broken`.

### Signatures are reported, never a `broken` determinant

With no signing key configured `key_manager` returns `algorithm: "none"`, and rows on the
live deployment already carry exactly that. Scoring an unsigned row as broken would paint
a healthy chain 100% red the moment a key rotated or a worker started without the secret.
Tamper detection is the hash and the link; signature counts sit alongside.

## Cutover provenance

The boundary between `pre_cutover` and `unchained` comes from `audit_chain_genesis`
(`source: "marker"`, authoritative). When migration `20260812041301` has not run, the sweep
falls back to `MIN(id) WHERE hash IS NOT NULL` (`source: "derived"`) and **says so** — in
the JSON (`cutover.authoritative: false`) and on the page. A derived boundary would shift
silently if the first chained row were ever removed, which is precisely the event the chain
exists to expose, so it is never presented with the confidence of a recorded marker.

## Cost

O(chained rows), not O(table). A NULL-hash row needs only its id to bucket, so those two
counts come from aggregate `COUNT(*)`s and only chained rows are read back. Measured on the
live PostgreSQL primary: 80,351 rows, 17 chained → two counts and 17 rows.

## Correctness notes

- **The link rule is the writer's rule restated**: `previous_hash` must equal the hash of
  the row at `id - 1`, falling back to `GENESIS_HASH` when that row is absent or itself
  unchained. Writer, per-row verifier and sweep agree by construction. A gap therefore
  *restarts* the chain rather than faking a break — visible in the UI as `GENESIS`.
- **RLS is off for the read** (`chain.unfiltered_cursor`). A sweep running under a narrower
  security context than the writer would not see higher-classified predecessors, compute
  GENESIS where a real hash belongs, and report a healthy chain as broken. Only digests are
  read, never row content.
- **The marker table is probed with `table_exists` before it is queried.** On PostgreSQL a
  failed statement aborts the whole transaction, so querying a missing `audit_chain_genesis`
  would make every later read in the sweep raise and the panel report `unavailable` on a
  healthy chain. This was observed and fixed during implementation.

## Verification

```bash
python tools/audit/chain_sweep.py --json
pytest tests/test_audit_chain_sweep.py -v          # 18 tests
```

Screenshots (`playwright/screenshots/`):

- `exa-audit-04-provenance-chain-health.png` — live PostgreSQL primary: 17 verified,
  79,932 pre-cutover, 0 broken, with real hashes chaining row to row.
- `exa-audit-04-provenance-chain-broken.png` — a deliberately tampered fixture: one edited
  row (`hash_mismatch`) and one re-pointed link (`link_mismatch`), shown red and clearly
  distinct from the 9 pre-cutover rows.

## Known gap (not addressed here)

`args/genesis_config.yaml` lists `compliance_check`, `stig_check` and `sbd_assessment` under
`reflexes.audit.checks`, but none of the three has an entry in `audit.py`'s `check_map`, so
they silently no-op every run. Pre-existing and out of scope for this task; worth a card.
