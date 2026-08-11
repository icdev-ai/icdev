# AGOV CASE — the operator CLI (agov-case-04)

**Classification:** CUI // SP-CTI

Three commands over one agent session, and no dashboard page.

```bash
python tools/agent_case/cli.py timeline --session <id> [--json]
python tools/agent_case/cli.py build    --session <id> --out <dir> [--json]
python tools/agent_case/cli.py verify   --bundle <dir> [--json]
```

## Why there is no page

CLAUDE.md's new-dashboard-page gate requires **all eight** components to ship in
one change: the template, its `icdev/` mirror, the blueprint route, the backing
module, constants, a migration, a nav/parent link, and full IQE wiring (an
adapter registering collections, a `POST /api/iqe-query` route, the
`iqe_query_widget` include, a `_CANVAS_MAP` entry in `app.py`, a `PATH_CANVAS`
entry in `base.html`, and at least three seed queries under
`context/iqe/queries/`). Shipping a template without the other seven is named in
CLAUDE.md as a *repeated past failure*.

A CASE UI is therefore a separate card, and it must land all eight together.
`tests/test_agov_case_cli.py::test_cli_ships_no_dashboard_template` pins the
absence so nobody adds a lone template here later.

## What each command does

### `timeline` — the join that did not exist

ICDEV writes rich agent activity into several append-only tables and, before
AGOV, read none of it back keyed by session. `tools/agent_case/session_timeline.py`
is that join.

| Source | Joined on | Notes |
|---|---|---|
| `hook_events` | `session_id` | Every pre/post tool-use hook, HMAC-signed |
| `audit_trail` | `session_id` | Immutable audit, hash-chained since migration 149 |
| `agent_findings` | `session_id` | AGOV detection findings; present once `agov-det-05` lands, reported as `present: false` until then |

**What it cannot join, stated on every result.** `agent_executions`,
`ai_telemetry` and `ace_audit_log` all record agent activity, and none of them
has a `session_id` column — they key on `execution_id`, `agent_id`/`user_id` and
`instance_id`. A forensic timeline must not silently omit them, so all three are
named under `limits`. Correlating them needs a schema change, not a wider
`SELECT`.

Ordering is `(timestamp, source, record_id)`, so two runs over the same data
produce the same sequence. Rows with no timestamp cannot be placed in time: they
sort last, are counted in `undated`, and are reported — not dropped.

### `build` — a bundle the verifier can check

`tools/agent_case/case_bundler.py` writes the session out through
`bundle_format.build_manifest`, so the writer and the verifier cannot drift:

```
<bundle>/
  manifest.json                 # SHA-256 of every other member
  timeline.json                 # the ordered join
  records/hook_events.json      # {"table", "records"}
  records/audit_trail.json      # {"table", "records", "chain_context"}
  records/agent_findings.json   # only when the table exists
```

Three properties are load-bearing:

- **Raw values travel.** `hook_events.payload`, `hook_events.signature` and
  `audit_trail.hash` are copied verbatim. Re-serializing a parsed payload would
  change key order and spacing and break every HMAC.
- **`chain_context` anchors the slice.** The migration-149 chain links row *N* to
  row *N-1*, and a session's audit rows are a slice out of the middle of it, so
  the first row's predecessor is outside the bundle. Its hash is looked up once
  at export and carried. Predecessors *inside* the slice are deliberately not
  anchored — a tampered slice must not be able to supply its own anchor.
- **LF, sorted keys.** Members are written with `newline="\n"` and
  `sort_keys=True` so a bundle written on Windows verifies byte-identically on
  Linux. The manifest hashes raw bytes; CRLF would break every member digest.

An empty source still gets a member file. A missing `records/hook_events.json`
makes the verifier report `NOT_VERIFIED`, and that must mean "not in the bundle",
never "this session had no hook events".

`build` refuses to write into a directory that already holds a `manifest.json`
unless `--force` is passed: a bundle is evidence, and half-replacing one leaves a
manifest describing some files and not others.

### `verify` — names which records failed

Delegates to `tools/agent_case/bundle_verifier.py` (`agov-case-03`), which checks
the manifest digests, the `hook_events` HMACs and the audit hash chain
independently and reports per-record findings rather than a boolean.

## Exit codes

Identical across all three subcommands so a caller can branch uniformly:

| Code | Meaning |
|---|---|
| 0 | Succeeded / every layer passed |
| 1 | A verification layer FAILED, or the command errored |
| 2 | Nothing failed, but something could not be verified |
| 3 | The bundle is unreadable |

Two deliberate choices:

- **An empty session exits 0.** "No records for this session" is a finding to
  report, not an error to raise — and a `--json` caller should not have to
  distinguish "no rows" from "the query broke".
- **Errors are JSON when `--json` was asked for**, always carrying `ok: false`. A
  consumer should never have to infer failure from a missing key or parse a
  traceback off stderr.

## MCP

`case_timeline`, `case_build` and `case_verify` are registered in
`tools/mcp/tool_registry.py` with handlers in `tools/mcp/gap_handlers.py`
(Pattern A, direct import). `case_timeline` and `case_verify` are declared
read-only; `case_build` is not, because it writes a bundle directory at a
caller-named path.

`case_verify` carries the report's `exit_code` through rather than collapsing it
to a boolean: 2 ("could not be verified") must stay distinguishable from 0
("verified clean").

## Verification

`tests/test_agov_case_cli.py` — 30 tests, ~1.1s, no DB service, LLM or network.
The load-bearing one is the **round trip**: a bundle written by `case_bundler`
verifies clean under `bundle_verifier` across all three layers. Writer and
verifier are separate modules with separate digest code paths, so a per-module
test would pass while the two disagreed about member paths, JSON canonicalization
or line endings — exactly the drift that makes a bundle worthless on the machine
it is carried to.

Also covered: cross-source ordering, undated records sorting last and being
counted, the unjoinable-table disclosure, window and limit handling, tuple *and*
dict row factories, `chain_context` anchoring only the out-of-slice predecessor,
tampering named by member path, no CRLF in any member, overwrite refusal, all
four exit codes through the CLI, and the absence of a dashboard template.

Run:

```bash
pytest tests/test_agov_case_cli.py -q
python tools/agent_case/cli.py timeline --session <id> --json
```

## Scope notes

- `agov-case-01` and `agov-case-02` own the depth of `session_timeline` and
  `case_bundler` respectively. This card shipped the minimum each needed for the
  CLI to be live rather than dead code — optional PROV-JSON / SWFT enrichment of
  the bundle is theirs, not here.
- `bundle_format.py` and `bundle_verifier.py` are carried byte-identical from
  `agov-case-03` (PR #1485) so `verify` works on this branch; landing this PR
  first makes that part of #1485 a no-op.
