# AGOV CASE — Bundle Verification That Names Which Records Failed

> **Classification:** CUI // SP-CTI
> **Task:** `agov-case-03` (AGOV / CASE epic)
> **Modules:** `tools/agent_case/bundle_format.py`, `tools/agent_case/bundle_verifier.py`
> **Tests:** `tests/test_agov_case_bundle_verifier.py`

## What this is

A case bundle is the portable export of one agent session's forensic record.
This is the verifier for it. It answers "which records do not verify, and how?"
— not "is the bundle OK?". A single pass/fail boolean is not an acceptable
output for a forensic tool: the value of the bundle is being able to say
*"these 3 of 412 events do not verify, here they are"*, and a boolean throws
that away.

Verification runs from the bundle alone. It opens no database, which is the
point of a portable bundle: an auditor on a machine that never had the source
database must be able to check it.

## The three layers

| Layer | What it re-derives | Reported per record |
|---|---|---|
| `manifest` | SHA-256 of every member listed in `manifest.json`, plus detection of members that are missing or were never listed | member **path**, expected digest, actual digest |
| `hmac` | `hook_events.signature`, recomputed exactly as `.claude/hooks/send_event.py::compute_hmac` does with `ICDEV_HOOK_HMAC_SECRET` | hook **event id**, session, hook type, tool, expected vs stored HMAC |
| `chain` | `audit_trail.hash` and the `previous_hash` link added by migration `149_blockchain_audit_hash_chain` | audit **row id**, event type, actor, action, expected vs stored digest |

The layers are independent on purpose. An attacker who edits a payload *and*
re-seals the manifest defeats layer 1 and is caught by layer 2; one who edits an
audit row and re-seals both is caught by layer 3. Each layer reports its own
verdict, and a passing layer never launders a failing one.

## Honest degradation — NOT VERIFIED is a real answer

A layer that could not be checked reports `NOT_VERIFIED`. It is never reported
as passed, and never as failed:

* **`ICDEV_HOOK_HMAC_SECRET` is unset.** The verifier does **not** reproduce
  `send_event.py`'s fallback to the shipped default. Verifying with a key you
  supplied yourself is not verification.
* **The secret in the environment *is* the shipped default
  `icdev-default-hmac-key`.** The signatures will match arithmetically, and that
  proves nothing: the literal is public in this repository, so anyone can forge
  a matching signature. **hook_events signatures are cryptographically
  meaningful only where an operator set a real secret.** This caveat is printed
  on every run, including clean ones.
* **Every signature mismatches.** That is exactly what a secret rotated since
  capture looks like, and it is not distinguishable from wholesale tampering.
  The layer is indeterminate, and the affected event ids are still listed —
  indeterminate is not the same as silent.
* **The migration-149 hash columns are NULL.** Migration 149 adds
  `hash` / `previous_hash` / `signature` to 15 audit tables, but no ICDEV writer
  populates them today, so on a real database this is the common case. An empty
  chain means "never recorded", not "erased".
* **An audit row's predecessor is outside the bundle slice.** A per-session
  export is not a contiguous id range. The link is only checked when the
  predecessor is in the bundle, or when the bundle supplies a `chain_context`
  anchor `{"<id>": "<hash>"}` for it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every layer passed and every record was checkable |
| 1 | at least one layer **FAILED** — a record demonstrably does not verify |
| 2 | nothing failed, but at least one layer or record could **not be verified** |
| 3 | the bundle could not be read at all |

Code 2 exists so that "we could not check the HMACs" never exits 0 alongside a
genuinely clean bundle.

## Bundle layout

```
<bundle>/
  manifest.json               # {manifest_version, algorithm, members:[{path, sha256, bytes}], ...}
  records/hook_events.json    # {"table": "hook_events", "records": [...]}
  records/audit_trail.json    # {"table": "audit_trail", "records": [...], "chain_context": {...}}
  ...                         # any other member — manifest-verified only
```

`manifest.json` never covers itself: it is the root of trust, and a
self-referential digest is not computable.

`hook_events.payload` must be carried as the **raw text of the column**. The
HMAC is over those bytes; re-serializing the parsed JSON changes key order and
spacing and invalidates every signature in the bundle. The bundler
(`agov-case-02`) must build its manifest through
`bundle_format.build_manifest()` so the two sides cannot drift.

## Known limits

* The verifier proves *internal consistency* of the bundle. It cannot prove the
  bundle is a complete export of the session — a row deleted before export
  leaves no trace in any of the three layers. Completeness is the bundler's
  responsibility (`agov-case-02`).
* `audit_trail.signature` (the ECDSA/HMAC signature column) is **not** checked
  here; that needs key material outside the bundle. `tools/blockchain/
  provenance_verifier.py` covers it against a live database.
* CLI only, deliberately. A dashboard page would trigger the 8-component
  completeness gate and is a separate card.
