# OSS poc-01 — Reproduce-or-drop rule for dynamic findings

**Classification:** CUI // SP-CTI
**Status:** shipped
**Task:** `oss-poc-01`
**Adapted from:** STRIX (Apache-2.0) — concept only, clean-room. See
`docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md` and the
`strix` entry in `_ATTRIBUTION_REGISTRY` (`tools/workflow/coherence_checker.py`).

## Problem

ICDEV had no notion of a *proven* finding. Severity came entirely from static
taxonomy — bandit severity, CVSS lookup, STIG CAT — and gates fired on counts.
A grep for `false.positive|proof.of.concept|PoC|exploitab` across
`tools/security/` and `tools/quality/` returned four hits, none of them a
validator: two `POC:` strings in CUI banner boilerplate, a Luhn check, and a
comment.

That is tolerable for claims about *code*, which is what a static scanner makes.
It is not tolerable for claims about a *running system*. "This endpoint fails
open to admin" and "this endpoint looks like it might fail open to admin" are
the same sentence, and nothing in the pipeline could tell them apart — so an
unverified assertion could block a gate with the same authority as a
demonstrated defect.

STRIX's single load-bearing discipline is the fix: **a finding ships with a
working proof-of-concept or it is not a finding.**

## The rule

Every **DYNAMIC** finding must carry a stored, replayable reproduction — an HTTP
request/response sequence, or an agent action trace (from `oss-browse-01`).

- Reproduction replays and the vulnerability predicate fires → `confirmed`.
  May block a gate, subject to severity.
- No reproduction, or the replay is not decisive → `unconfirmed`. Reportable as
  a lead; **structurally incapable of blocking**.
- A *previously-confirmed* reproduction that stops firing → `remediated`. A
  first-ever replay that does not fire is `unconfirmed`, not `remediated` —
  never-established is not the same as fixed.

**STATIC** findings are explicitly out of scope and pass through untouched.
The module has no way to replay a claim about source text and does not pretend
to.

## Why a replay, and not a second opinion

The existing verification machinery grades *text*: the `adversarial_verifier`
ACE role re-reads an implementation and emits `task.approved`/`task.rejected`;
`run_agent_loop_with_rubric` has an LLM judge a final response against a rubric.
Neither can discriminate a real dynamic finding from a plausible one, because
the evidence they see is prose either way.

So the primary decision here is deterministic and LLM-free — it makes the
request and reads the status code. The reuse of `run_agent_loop_with_rubric` is
at the one point where it genuinely helps: `make_reproduction_grader()` supplies
the loop's **pluggable code grader**, which *replaces* the LLM rubric judge. An
agent claiming a dynamic finding is then graded on whether the reproduction it
wrote actually fires. A `needs_revision` verdict is injected verbatim and the
loop resumes, so the agent iterates until it has a working PoC or runs out of
rounds — at which point the finding is, correctly, unconfirmed.

## Discrimination is the real bar

A reproduction that *runs* proves nothing. `status_in: [200, 403]` "reproduces"
against a vulnerable target and a fixed one alike. Two guards:

1. **Static — `validate_predicate()`.** Rejects trivially-true predicates: a
   `status_in` listing ≥ `predicate.max_status_codes` codes, an empty
   substring, a regex that matches the empty string, and a bare negative
   assertion (`body_not_contains` alone fires against any error page, so it may
   only appear conjoined with a positive clause inside `all_of`).

2. **Empirical — `verify_discrimination()`.** This is the task's success
   criterion. The *same* reproduction is replayed against a target that still
   has the defect and one where the fix is applied. `discriminating` is set only
   when it fires on the first and **stops** firing on the second. Firing on both
   is reported as a tautology; firing on neither means the finding was never
   established; firing only on the fixed build means the predicate is inverted.

The proof is exercised for real in `tests/test_reproduction_validator.py`: two
loopback `ThreadingHTTPServer`s are stood up from one handler with a single
switch — `authz_enforced=False` is the seeded defect (an endpoint serving
tenant-scoped records to an unentitled caller), `True` is the fix. The
reproduction is `reproduced` against the first and `not_reproduced` against the
second, and the tautological and inverted predicates are both rejected.

## Scope lock

Replay makes real outbound requests, so `is_target_allowed()` is **default-deny**
against `target_allowlist` in `args/reproduction_policy.yaml` — loopback only
out of the box. A non-allowlisted host is `refused` with zero observations;
nothing is sent. `ICDEV_REPRO_TARGET_ALLOWLIST` widens it for a self-hosted
staging box — own targets only, never a third-party host.

Supporting controls: retries are disabled per replay
(`HTTPAdapter(max_retries=0)`) so a reproduction is exactly-once; redirects are
not followed unless a step opts in (a 302 to a login page *is* the authz
signal); proxies are cleared for loopback targets so an operator-configured
egress proxy cannot answer in place of the target. Sandbox decision: Gap 41,
`docs/security/sandbox-coverage.md` (**bypass-documented** — reproductions are
interpreted, never executed).

## Evidence hygiene

ICDEV is a public repo and these rows are read by dashboards and PR-adjacent
tooling. `_redact()` strips response bodies from every observation before it
leaves the replay. What persists is `status`, `body_len` and `body_sha256` —
enough to make two replays comparable, without retaining the payload. A test
asserts the seeded secret marker never appears in a serialized observation.

## Storage

Migration `320_dynamic_finding_reproductions.sql`, two tables with deliberately
different mutability:

| Table | Mutability | Role |
|---|---|---|
| `dynamic_findings` | **mutable** | One row per dynamic finding. `status` transitions unconfirmed → confirmed → remediated as replays run, so it is intentionally *absent* from `APPEND_ONLY_TABLES`. |
| `finding_replay_attempts` | **append-only** (NIST AU) | One row per replay, ever. The evidence trail behind a `confirmed` claim, and the record that proves a reproduction discriminates. Listed in `APPEND_ONLY_TABLES`. |

Persistence is **best-effort**: an un-migrated checkout degrades to in-memory
classification rather than raising, because the *rule* must hold even where the
tables do not exist yet.

## Surfaces

```bash
python tools/security/reproduction_validator.py --validate repro.json --json
python tools/security/reproduction_validator.py --replay repro.json --target http://127.0.0.1:5051 --json
python tools/security/reproduction_validator.py --enforce findings.json --gate
```

MCP: `finding_replay`, `finding_enforce_reproduction`,
`finding_verify_discrimination`.

Gate definition: `dynamic_finding_reproduction` in `args/security_gates.yaml`.
`gate.block_on_unconfirmed` exists solely so that re-admitting unreproduced
findings as gate blocks — which defeats this entire module — is an explicit,
visible configuration act rather than a silent default.

## Deliberately not done

- **No STRIX runtime.** Its Docker sandbox image, Caido proxy, nuclei bundle and
  `curl | bash` installer are all rejected. The concept was adopted; no code is
  shared and there is no runtime dependency.
- **No trace replay engine yet.** `agent_trace` reproductions replay as
  `unavailable` until `oss-browse-01` registers one via
  `register_trace_replayer()`. `unavailable` is explicitly *not*
  `not_reproduced` — we learned nothing, so the finding stays `unconfirmed`.
  That is the correct answer, not a stub.
- **No retrofit of existing scanners.** Wiring current dynamic detectors to emit
  reproductions is follow-on work; this ships the rule, the storage and the
  proof that the rule discriminates.

## Tests

`tests/test_reproduction_validator.py` — 45 tests. The load-bearing class is
`TestDiscrimination`; the rest cover the rule itself, the scope lock, predicate
hygiene, agent-trace availability, evidence hygiene, persistence (including that
a persistence failure never breaks the verdict), and the rubric-loop grader.
