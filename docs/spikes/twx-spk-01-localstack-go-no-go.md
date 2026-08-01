# TWX Spike: LocalStack Go/No-Go (twx-spk-01)

> Spike deliverable — **no production code**. Written go/no-go per pattern with
> costs. Feeds the TWX ADR (twx-xcut-01).

## Summary decision

| # | Pattern | Decision | Why |
|---|---------|----------|-----|
| 1 | **PDC / IaC CI gate** (terraform apply into LocalStack in CI) | **GO — conditional, cloud-CI only** | Highest value; validates the IDC pre-apply gate against a real AWS API surface. Gate it behind an opt-in CI job; never on the air-gapped path. |
| 2 | Cloud Pods fixtures (seed deterministic state) | **DEFER** | Useful for e2e fixtures but a Pro feature; our fixtures are already pure-Python. Revisit only if pattern 1 is adopted. |
| 3 | IAM policy sandbox | **NO-GO** | The Zero-Trust / ABAC engine (`tools/security/…`, PDP/PEP) already models IAM decisions offline. LocalStack IAM is a partial emulation — adds a Docker dep for marginal gain. |
| 4 | Chaos injection | **NO-GO** | Out of scope for design-time twins; belongs to a runtime resilience program, not the IDC design twin. |
| 5 | IDC runtime engine (twin executes against live LocalStack) | **PARTIAL — already prototyped, keep flag-gated** | `tools/databridge/connectors/localstack_connector.py` already exists (see below). Keep it as an **optional** read surface; do NOT make the IDC twin depend on it. |

**Net:** adopt **one** pattern (PDC/IaC CI gate) as an opt-in cloud-only CI job; keep the existing connector flag-gated; skip the rest. No change to the default (offline, heuristic) twin path.

## Cost / constraint findings

### 1. Licensing (blocking for classified/air-gapped)
LocalStack consolidated to a **single authenticated image in 2026** — the free
community edition is gone; the image requires an **auth token** validated at
container start. Implications:
- **Air-gapped / classified customers: NO-GO.** An auth-token check that phones
  home (or must be provisioned per-environment) is a non-starter on a
  disconnected high side. Any LocalStack use MUST be confined to connected,
  commercial CI — never shipped as part of the air-gapped runtime.
- Commercial CI use incurs a **paid subscription** (per-seat/per-CI). Cost must
  be justified by the CI-gate value (pattern 1) alone.

### 2. Footprint (against the pure-Python/offline preference)
LocalStack is a **Docker container** (multi-hundred-MB image) + boto3 client.
This conflicts with the repo's stated *"pure-Python / offline tooling"*
preference. Acceptable ONLY in a cloud CI runner that already has Docker;
unacceptable as a local-dev or air-gap dependency.

### 3. An IDC LocalStack connector ALREADY exists
`tools/databridge/connectors/localstack_connector.py`:
- DataBridge connector (same pattern as `GNS3Connector`) — single egress point,
  secret resolution, audit logging, health probing.
- Feature flag **`LOCALSTACK_ENABLED` in `.env`, default `false` (air-gap safe)**:
  when disabled, `health_check()` returns `disabled` and all calls return a
  disabled response — **no exceptions**.
- Logical tables: `health`, `services`, `s3_buckets`, `dynamodb_tables`,
  `lambda_functions`, `sqs_queues`, `ecr_repositories`.
- Default endpoint `http://localhost:4566`, dummy creds (`test`/`test`).

So **pattern 5 is already partially built and correctly flag-gated.** The spike's
recommendation is to leave it exactly as-is (optional, off by default) and NOT
couple the IDC twin's verdict logic to it.

### 4. Value ranking (highest → lowest)
1. **PDC/IaC CI gate** — real AWS-API validation of pre-apply diffs (highest).
2. IDC runtime read surface — already exists (connector), marginal extra value.
3. Cloud Pods fixtures — nice-to-have, Pro feature.
4. IAM sandbox — duplicates existing ABAC/ZTA modeling.
5. Chaos injection — out of scope for a design twin.

### 5. Performance-claim guard (docs risk #2)
LocalStack emulates the AWS **API contract**, not AWS **performance
characteristics**. **Never** use LocalStack timings/throughput for any
performance, cost, or capacity claim in a twin simulation or report. The twin's
cost/latency estimates must remain sourced from the catalog/estimate engines,
clearly labeled `estimate=True`.

## Recommended follow-ups (only if pattern 1 is greenlit by a human)
- `twx-ls-01` (MANUAL/gated): add an **opt-in** cloud-CI job that stands up
  LocalStack (Docker) and runs the IDC pre-apply gate against a real
  `terraform apply`, asserting the gate's verdict matches. Behind a
  `LOCALSTACK_CI` flag; excluded from the air-gap pipeline and from the 4
  required checks.
- No other patterns recommended for build at this time.

**Decision owner:** requires human sign-off on the paid subscription before any
`twx-ls-*` task is scheduled. This spike seeds **no** ungated build tasks
automatically.
