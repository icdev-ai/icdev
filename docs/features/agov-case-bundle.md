# AGOV CASE — Portable Case Bundle with a SHA-256 Manifest (agov-case-02)

CUI // SP-CTI

## What this is

A single agent session, written out as a directory that can be copied to a
machine which never had the source database and verified there. It carries the
normalized event timeline, the tamper-evident records the timeline is built
from, the approval gate's enforcement decisions, the artifact paths the session
referenced, a W3C PROV-JSON document, and an endpoint/context header — plus a
manifest listing every member with its SHA-256.

Entry point: `tools/agent_case/case_bundler.py::build_case_bundle`.

## Why it is not a third bundler

ICDEV already ships two bundlers, and the card was explicit that this must be
built on them rather than beside them:

| Existing | What it bundles | Keyed by |
|----------|-----------------|----------|
| `tools/compliance/swft_evidence_bundler.py` | DoD SWFT software supply-chain evidence | `project_id` |
| `tools/observability/provenance/prov_recorder.py` (`export_prov_json`, MCP `prov_export`) | W3C PROV entities/activities/relations | `project_id` |

Neither can express agent **behaviour**, because neither is keyed by
`session_id`. This module adds exactly that axis and reuses the rest:

- the provenance member is `export_prov_json` output carried **verbatim** — a
  case bundle that reshaped it would be a second provenance format to keep in
  step;
- the artifact member follows `swft_evidence_bundler._collect_artifact_evidence`
  in recording that evidence **exists and where**, never copying its bytes;
- the digest and manifest recipes live in `tools/agent_case/bundle_format.py`,
  shared with the verifier (agov-case-03) so writer and reader cannot drift.

## Layout

```
<bundle>/
  manifest.json                      # SHA-256 of every other member
  context.json                       # endpoint/context header + classification
  timeline.json                      # the ordered join (session_timeline)
  records/hook_events.json           # raw stored values, HMAC-signed
  records/audit_trail.json           # raw stored values + chain_context anchor
  records/agent_session_events.json  # the event log, WITHOUT payload_json
  records/agent_findings.json        # present once agov-det-05 lands
  records/agent_approval_log.json    # enforcement decisions, free text redacted
  artifacts.json                     # paths referenced, not their contents
  provenance/prov.json               # W3C PROV-JSON, when a project resolves
```

`records/agent_session_events.json` (hcx-evt-04) is the case the table-level
transcript exclusion cannot express: the table **is** exported, and one of its
columns holds verbatim model input. `session_timeline.SOURCES` leaves
`payload_json` out of that table's column allowlist so the export never selects
it; `payload_hash` travels instead, computed by the same
`tools/audit/row_hash.py` recipe the migration-149 chain uses, so a recipient
holding the payload can still prove what it was. The omission is declared in
`case_bundler.EXCLUDED_COLUMNS` and surfaces in `context.json` under
`sources.excluded_columns` — an absence nobody wrote down is indistinguishable
from an oversight, and a guard fails at import if the allowlist ever selects the
column again.

The manifest never covers itself: it is the root of trust and a self-referential
digest is not computable.

## The three invariants

### 1. Every member is in the manifest with its SHA-256

`build_manifest` walks the directory that was actually written, so a member
cannot be added without being hashed. `bundle_digest` is a single digest over
the sorted member list — the bundle's identity in one value.

### 2. Identical input produces an identical manifest

**No member carries an export wall-clock.** `_VOLATILE_MEMBER_FIELDS` names the
fields stripped on the way out (`built_at`), and export time lives only in
`manifest.created_at`. Two consequences:

- pinning `created_at` yields a byte-identical bundle, manifest included;
- **not** pinning it still yields an identical `bundle_digest`, because the
  digest is over member content and no member moved.

That second property is the useful one: a recipient can say "this is the same
evidence I already hold" without coordinating clocks. Members are written with
`newline="\n"` and `sort_keys=True` — the manifest hashes raw bytes, so CRLF
would break every digest the moment a Windows-written bundle was verified on
Linux.

### 3. No raw transcript, by construction

`TRANSCRIPT_SOURCES` names the conversation-bearing tables, verified against the
live DDL, and **nothing in this module opens one**:

| Table | What it holds | Why the exclusion bites |
|-------|---------------|-------------------------|
| `intake_conversation` | customer/analyst turn `content` | **has `session_id`** — a join one column wider would pull this session's conversation in |
| `ci_conversation_turns` | developer/agent turn `content` | **has `session_id`** — same |
| `chat_messages` | chat pane message bodies | keys on `context_id` |
| `ace_audit_log` | ACE co-worker action `detail` | keys on `instance_id` |
| `agent_executions` | `prompt_hash` and `output_path` — never the prompt | the path is a *pointer* to run output, and `collect_artifact_paths` refuses to follow a stored path |

The first two are the load-bearing entries. They carry a `session_id` **and**
raw content, so their exclusion is a decision rather than an accident of which
tables happen to be joinable — which is why the test seeds both under the very
session being exported and asserts the canary is absent from the bundle's bytes.

The exported sources are a closed allowlist with per-source column allowlists, so
a later `ALTER TABLE` cannot silently widen a forensic export. A bundle cannot
leak a prompt it never read, and the excluded set travels in the header so the
decision is reviewable by the recipient rather than invisible.

## Redaction contract

Three rules, stated in `context.json` so a recipient does not have to infer them:

| Layer | Treatment | Why |
|-------|-----------|-----|
| Transcript tables | never read | see above |
| Operator free text — `agent_approval_log.reason`, `.detail` | redacted through `tools/llm/output_redactor.py::redact`, row flagged `redacted: true` | a human can paste a token into an approval note; the flag distinguishes "nothing sensitive" from "something removed" |
| Signed/chained values — `hook_events.payload`, `audit_trail.hash` | exported verbatim | the HMAC and the migration-149 chain are computed over these bytes; rewriting one would make an untampered bundle report as **tampered**, a worse failure than the one redaction prevents |

`agent_approval_log` can be redacted in place precisely because it carries no
HMAC and no hash chain — redacting it costs no one a verification.

`arg_keys` is deliberately key NAMES only and `input_sha256` is already a
digest, so neither needs redacting.

## Classification

Resolved, never written down. `resolve_classification` takes the most
restrictive marking present on the session's own records, ordered by
`classification_manager.get_clearance_order`; `classification_header` then
produces the banner and portion marking from
`classification_manager.get_marking_banner` / `get_portion_marking`. A session
carrying a SECRET audit row produces a SECRET bundle banner with no code change
— which is the assertion a hardcoded `CUI // SP-CTI` cannot pass.

## Honest degradation

Nothing here fails an export because evidence is missing; it reports what is
missing under `limits`:

- `agent_approval_log` absent (migration not run) → the member is still written
  and hashed with `present: false`, so "not in the bundle" is never confusable
  with "the session had no enforcement decisions";
- no `project_id` agreed across the records → `provenance/prov.json` carries
  `{"available": false, "reason": ...}`;
- **records and provenance must come from the same database, or neither.**
  `ProvRecorder` opens its own connection from a *path* and cannot be handed an
  open one, so when a caller supplies `conn` the exporter cannot prove the two
  agree and declines with that reason rather than attributing some other
  database's provenance to this session. Pass `prov_db_path=` to opt back in;
  the member then records which database answered;
- `audit_trail.hash` absent (pre-migration-149) → no `chain_context` anchor, and
  the verifier reports the link as not verifiable, which is the truth;
- sources that cannot be joined at all (`agent_executions`, `ai_telemetry`,
  `ace_audit_log` have no `session_id` column) are named in every header.

## Verification

Verified end-to-end against the independent verifier from agov-case-03: the
**manifest** and **HMAC** layers pass on a bundle this module wrote, which is the
proof that writer and verifier agree on member paths, JSON canonicalization and
line endings. Chain-layer status depends on whether migration 149 populated the
hash columns in the source database.

## Tests

`tests/test_agov_case_bundle.py` (22 tests, ~1.4s, in-tmpdir SQLite, no DB
service / LLM / network). Each acceptance property was confirmed to bite by
mutating the implementation and watching the suite fail:

| Mutation | Caught by |
|----------|-----------|
| write a member after the manifest (present but unhashed) | `test_every_file_in_the_bundle_is_in_the_manifest_with_a_matching_digest` |
| put export time back into `timeline.json` | `test_bundle_digest_is_time_independent`, `test_default_created_at_still_yields_a_stable_bundle_digest` |
| export a transcript table | `test_no_raw_transcript_reaches_the_bundle`, `test_transcript_tables_are_excluded_by_construction_and_named` |
| hardcode the banner as `CUI // SP-CTI` | `test_the_banner_is_the_one_classification_manager_produces` and three others |
| misspell the `ProvRecorder` import (degrades to a plausible `reason` string inside the broad `except`) | `test_provenance_reuses_prov_recorder_and_the_symbol_actually_resolves` |

The provenance reuse is proven in both directions: the happy path builds the
three PROV tables and checks a real W3C document comes back
(`test_provenance_export_really_produces_a_prov_json_document`), so a
`collect_provenance` that could never succeed would not pass.

## Related cards

- **agov-case-01** — the normalized session timeline this bundles.
- **agov-case-03** — verification that names WHICH records failed WHICH layer.
- **agov-case-04** — the operator CLI (`timeline` / `build` / `verify`).

CLI-only on purpose: a dashboard page would trip the 8-component completeness
gate (template + `icdev/` mirror + route + module + constants + migration + nav
+ full IQE wiring) and that is a separate card.
