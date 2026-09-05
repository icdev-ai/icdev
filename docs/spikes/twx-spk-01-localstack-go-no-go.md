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
| 5 | IDC runtime engine (twin executes against live LocalStack) | **PARTIAL — already prototyped, keep flag-gated** | `tools/databridge/connectors/floci_connector.py` already exists (see below; it was `localstack_connector.py` when this spike was written and was renamed by flx-bridge-01). Keep it as an **optional** read surface; do NOT make the IDC twin depend on it. |

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

### 3. An IDC emulator connector ALREADY exists
`tools/databridge/connectors/floci_connector.py` (named `localstack_connector.py` when
this spike was written; renamed, with registry key `floci`, by flx-bridge-01):
- DataBridge connector (same pattern as `GNS3Connector`) — single egress point,
  secret resolution, audit logging, health probing.
- Feature flag **`FLOCI_ENABLED` in `.env`, default `false` (air-gap safe)** — read
  through the one switch `tools/cloud/emulator.py` (flx-seam-01), with
  `LOCALSTACK_ENABLED` honoured as a deprecated alias:
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

---

## Addendum — 2026-09-05: SUPERSEDED ON THE LICENSING QUESTION ONLY (flx)

**Nothing above this line has been edited.** This section is appended, dated and
signed to the `flx` project (`args/projects.yaml`, prefix `flx-`); the spike's
original text stands exactly as it was written on the evidence available then.
A rewritten spike destroys the reasoning that made the original call defensible,
and a reader who cannot see the superseded verdict cannot tell a decision that
was **reversed** from one that was **never made**.

### The spike was RIGHT, and it is the FACT that moved — not the reasoning

The 2026 LocalStack image consolidation is real and the spike's reading of it was
correct: an emulator whose container validates an auth token at start is a
non-starter on a disconnected high side, and a per-seat commercial subscription
has to be justified by one pattern's value alone. Every "no" above followed from
those two facts. **Neither fact changed. The product did.**

**floci** (MIT, Java/Quarkus, port 4566, release `2.0.1` 2026-09-01 — the
version pinned in `tools/cloud/emulator.py::DEFAULT_IMAGE`; no upstream URL is
cited here because none is recorded anywhere in this tree, and inventing one is
the fabrication these documents exist to refuse) is a documented LocalStack
drop-in: it keeps
`/_localstack/health`, translates `LOCALSTACK_*` environment variables by
default, and consumes the identical stock `hashicorp/aws` provider shape
(`endpoints{}`, `s3_use_path_style`, `skip_*`, dummy credentials). It validates
**no token** and carries **no subscription**. So the *conclusion* "an AWS
emulator cannot be used air-gapped or without a paid seat" no longer follows —
not because the argument was wrong, but because its premise was about a
different product.

**What that does NOT license.** "Air-gap-capable" here means the image can be
pre-pulled, pinned by digest and loaded on the high side; it does **not** mean
disconnected operation is free. Two measurements from the `flx` work say so:
having `floci/floci:2.0.1` cached is **necessary and not sufficient** — floci
resolves eleven further container base images from the public internet on first
use of a container-backed service (`args/floci_runtime_images.yaml`, measured
from `docker events` 2026-09-05) — and the emulator needs the host Docker socket
for Lambda/RDS/ElastiCache/OpenSearch/MSK/ECS/EC2/EKS, which is
**root-equivalence on the host** (recorded as Gap 65 in
`docs/security/sandbox-coverage.md`, not waved through). The spike's *footprint*
finding (§2 — a multi-hundred-MB Docker container, against this repo's
pure-Python/offline preference) therefore **STANDS UNCHANGED**. What moved is
licensing, and only licensing.

### Per-pattern re-disposition

| # | Pattern | Spike verdict | Now | Why |
|---|---------|---------------|-----|-----|
| 1 | PDC / IaC CI gate | GO — conditional, cloud-CI only | **BUILT** (`flx-ci-01`) | The condition the spike attached was the paid subscription. floci removes it. `tools/ci/floci_iac_gate.py` + `.github/workflows/floci-iac-gate.yml`: `workflow_dispatch`, a weekly schedule and a `floci-gate` label — **never one of the four required checks** (runners here are near-serial; an emulator in front of every merge is how a gate earns itself a bypass). |
| 2 | Cloud Pods fixtures | DEFER — Pro feature | **DEFER, unchanged** | The reason was never only the licence: our fixtures are already pure-Python, and floci ships no Cloud Pods equivalent to adopt. Nothing to revisit. |
| 3 | IAM policy sandbox | **NO-GO** | **NO-GO — CARRIED FORWARD UNCHANGED** | See the standing guards below. The ZTA/ABAC engine already models IAM decisions offline; a partial emulation would be a **second opinion**, and the licence was never the objection. |
| 4 | Chaos injection | NO-GO | **NO-GO, unchanged** | Out of scope for a design-time twin; belongs to a runtime resilience programme. Untouched by the licensing change. |
| 5 | IDC runtime engine (twin over a live emulator) | PARTIAL — keep flag-gated | **BUILT, still flag-gated** (`flx-twin-01`) | `tools/twin_core/adapters/floci.py` reads the connector's seven logical tables **through** `tools/databridge/broker.py` (the `flx-bridge-02` grant), off by default, `unknown` never `pass`, and every snapshot carries provenance `emulated`. The spike's instruction not to couple the IDC twin's verdict logic to the emulator is kept: this is a **separate** adapter, not a dependency of the IDC one. |

The spike's own follow-up `twx-ls-01` — "an opt-in cloud-CI job behind a
`LOCALSTACK_CI` flag, requiring human sign-off on the paid subscription" — is
**superseded by `flx-ci-01`**, which builds that job against floci. There is no
subscription to sign off. The operator decisions that DID gate the work are
dated and recorded in the `flx` card: replace LocalStack outright, must run
air-gapped, mount the Docker socket, default region `us-gov-west-1`, persistent
state, ship the opt-in CI IaC gate, register a Twin Observatory adapter, do all
four CSPs (2026-09-04); locally-hosted Docker for now (2026-09-05).

### The two standing guards CARRY FORWARD UNCHANGED

Neither depends on which emulator is running, and neither is softened by
anything above.

1. **NEVER source a performance, cost or capacity claim from emulator timings.**
   An emulator reproduces the AWS **API contract**, not AWS's **performance
   characteristics**. Twin cost/latency estimates stay sourced from the
   catalog/estimate engines and stay labelled `estimate=True`. It travels WITH
   the capability rather than sitting in a spike nobody re-reads, and the four
   sites are named so the claim is checkable: `docs/reference/commands.md`
   (twice), the `flx-ci-01` report path in `tools/ci/floci_iac_gate.py`, the
   `IDC Floci Adapter` row in `tools/manifest/design-canvases.md`, and the
   `flx-twin-01` block in `CLAUDE.md`. `tests/cloud/test_flx_docs.py`
   re-derives all four, because a documented claim nothing re-checks is exactly
   the shape this addendum exists to correct.
2. **IAM policy sandbox stays NO-GO** (pattern 3 above). The PDP/PEP ABAC engine
   in `tools/security/` already answers IAM questions offline and
   deterministically; adding a partial emulation gives a second answer to the
   same question with no rule for choosing between them.

### Where the superseding work is recorded

- ADR: `docs/reference/adrs.md`, **Phase 79 — FLX**, D398–D401. **D382's
  LocalStack half is superseded on the licensing question only**; its Batfish
  half (spk-02) is untouched.
- Feature doc: `docs/features/phase-flx-floci-emulator.md`.
- Commands: `docs/reference/commands.md` (the floci sections).
- Cards: `flx-seam-01/02`, `flx-compose-01/02`, `flx-bridge-01/02`,
  `flx-studio-01/02`, `flx-sim-01`, `flx-gen-01`, `flx-airgap-01/02/03`,
  `flx-twin-01`, `flx-ci-01/02`, `flx-docs-01`. **Not built:** the Azure, GCP and
  OCI siblings (`flx-az-01`, `flx-gcp-01`, `flx-oci-01`) — each gated on its own
  **dated parity measurement**, exactly as this spike gated LocalStack, and none
  of them inherits floci's AWS parity by being made by the same authors.

**Addendum author:** `flx-docs-01`, 2026-09-05.
