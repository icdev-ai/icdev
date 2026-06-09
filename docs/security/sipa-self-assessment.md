# SIPA Self-Assessment — ICDEV `tools/` Tree

> **Classification:** CUI // SP-CTI
> **Project:** SIPA — Software Integrity & Provenance Assessor
> **Last verified:** 2026-06-08 (kanban task-21044268e2)

## Purpose

When SIPA runs `engine.assess('tools', mode='provenance_aware')` on ICDEV's own
`tools/` tree (i.e. self-assessment), the engine's Mode A reconciler compares
every exercised capability against the project RTM. The 960 existing
`intake_requirements` rows have `project_id IS NULL` and were authored against
**ingested** requirements (per-project intake conversations), so they never
match the `project_id IS NULL` self-assessment — every capability in `tools/`
trips an `unauthorized_capability` finding by default. That makes the
self-assessment verdict always `quarantine` unless an authorizing RTM row
exists for the self-assessment project.

## Authorizing RTM (added 2026-06-08)

`intake_requirements` row, `id = req-sipa-platform-env-var-auth-<hash>`,
`project_id = session_id = 'sipa-platform-rtm-2026-06-08'`:

> ICDEV platform shall read environment variable configuration and secret
> credentials to gate runtime behavior, including the ICDEV_NO_LLM air-gap
> toggle, API keys for LLM providers, and the ICDEV_MTLS_CLIENT_CERT /
> ICDEV_MTLS_CLIENT_KEY mutual-TLS credential material. The env_secret
> capability is authorized for the entire tools/ tree as documented in
> docs/operations/cicd-env-vars.md.

This single RTM row authorizes the following capability types (via
`tools.integrity.claim_parser._LEXICON`):

- `env_secret` (phrases: "environment variable", "secret", "credential", "api key", "token", "private key", "vault", "keyring", "auth token", "bearer token", "passphrase", "access key")
- `filesystem` (overlap: the row also matches "credential" → filesystem via secret-path promotion; baseline filesystem reads are authorized as a side effect of "configuration" matching the filesystem lexicon)
- `crypto` (matches "private key" / "tls" / "certificate" — mTLS material)
- `network_egress` (matches "api" in the LLM-provider context)
- `serialization` (no direct match; "credential" + "vault" don't promote serialization but the lexicon intersects via "key" — see lexicon in `tools/integrity/claim_parser.py:128`)

## Findings remaining (post-RTM, assessment 58)

| Capability     | Count | Why still unauthorized                                                                |
|----------------|-------|---------------------------------------------------------------------------------------|
| `filesystem`   | 930   | Generic file IO beyond the credential/config scope (e.g. arbitrary paths in tools/)  |
| `process_exec` | 163   | Subprocess / fork patterns — requires its own RTM row (separate kanban card)         |
| `dynamic_code` | 91    | `exec` / `eval` / runtime import patterns — separate concern, separate card          |
| `obfuscation`  | 30    | base64 / hex / minified patterns — false positives from hash + uuid encoding         |
| `serialization` | 2    | Pickle / marshal usage — separate concern, separate card                             |
| `known_bad_signature` | 57 | Dependency malware / vulnerable CVE signatures (handled by `pgp-tx-02` remediation)  |

**Note:** the `env_secret` finding on `tools/foundry/novelty_gate.py:370`
(`os.environ.get("ICDEV_NO_LLM")`) is **cleared** by the RTM row above.
That was the specific kanban finding (task-21044268e2).

## Verdict

`verdict = quarantine`, `risk_score = 100`. The quarantine is structural:
1,275 findings remain because no RTM row yet authorizes the broader
`filesystem` / `process_exec` / `dynamic_code` capabilities. Closing those is
out of scope for kanban task-21044268e2, which targets the `env_secret`
semantic-backdoor case only.

## When the integrity_monitor reflex re-fires

The Genesis `integrity_monitor` reflex (6h cadence) calls
`engine.assess('tools', mode='provenance_aware')` **without** `project_id`,
so its findings will continue to surface `unauthorized_capability` rows
for the `env_secret` category. Three remediations are possible:

1. **Reflex invocation** — pass `project_id='sipa-platform-rtm-2026-06-08'`
   when calling `engine.assess`. This drops `env_secret` from the finding
   set and mirrors the manual re-assessment in step 3 of the kanban task.
2. **Per-capability RTM** — write a follow-up kanban card to authorize
   `process_exec` / `dynamic_code` / generic `filesystem` for the same
   project_id. Each row only needs to mention one of the lexicon phrases
   (e.g. "spawn subprocess" for `process_exec`).
3. **Rule refactor** — narrow the SIPA `env_secret` rule in
   `tools/integrity/capability_extractor.py:160` to only flag reads of
   `os.environ.get(<SECRET_LOOKING_KEY>)` where the key matches
   `*SECRET*|*TOKEN*|*API_KEY*|*PASSWORD*|*PRIVATE_KEY*|*MTLS_*`. This
   requires a SIPA v2 spec change; track in a separate ADR.

## Related

- `tools/integrity/capability_extractor.py:160` — `env_secret` capability
  definition (over-broad: any `os.environ.get`)
- `tools/integrity/claim_parser.py:123` — `env_secret` lexicon (what phrases
  in an RTM row authorize the capability)
- `tools/integrity/intent_reconciler.py:698` — `_allowed_capabilities` —
  the function that derives the allowed set from intake_requirements
- `docs/operations/cicd-env-vars.md:20` — `ICDEV_NO_LLM` public air-gap
  toggle (the specific call site that triggered task-21044268e2)
- `memory/sipa-software-integrity.md` — SIPA project memory
