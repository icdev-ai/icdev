# CUI // SP-CTI

# Phase 79 — FLX: the Floci Cloud Emulator replaces LocalStack

**Card:** `flx` (`args/projects.yaml`, prefix `flx-`, 14 epics, 21 tasks)
**ADRs:** D398–D401 (`docs/reference/adrs.md`, Phase 79)
**Supersedes:** `docs/spikes/twx-spk-01-localstack-go-no-go.md` — **on the
licensing question ONLY**, by a dated addendum appended to that spike
**Status as at 2026-09-05:** 16 of 21 cards `done` (17 with this one),
`flx-test-01` in flight,
`flx-az-01` / `flx-gcp-01` / `flx-oci-01` in `backlog` and deliberately unbuilt

---

## The defect

ICDEV referenced LocalStack from **three mutually-unaware places and none of
them could reach an emulator**:

- `tools/databridge/feature_flags.py::localstack()` — its own disabled-reason
  text told the operator to run `docker compose --profile localstack up -d`,
  and `docker-compose.yml` **had never declared that service or profile** (its
  only profile was `llm-proxy`);
- `tools/studio/executors/_base.py::detect_mode()` keyed on the bare presence
  of `LOCALSTACK_ENDPOINT` — a **second competing switch**, and the one that
  actually runs `terraform apply`. So the flag could be off while the executor
  ran against an emulator, or on while nothing was reachable;
- twelve `tools/studio/sim/*_topology.py` builders declared containers that
  `gns3_sim.py` started **only when `mode == "dry_run"`** — the one mode whose
  entire purpose is to touch nothing.

**The blocker was never the wiring.** `twx-spk-01` had ruled LocalStack NO-GO
for air-gapped/classified because its 2026 single authenticated image validates
an **auth token at container start**, and priced commercial CI per seat; it
required human sign-off before any `twx-ls-*` task could be scheduled. That sign-
off never came, so the wiring stayed dead and nobody could justify repairing it.

## What changed, and what did not

**floci** — MIT, Java/Quarkus, port 4566, release `2.0.1` (2026-09-01) — is a
documented LocalStack drop-in: it keeps `/_localstack/health`, translates
`LOCALSTACK_*` environment variables by default, and consumes the identical
stock `hashicorp/aws` provider shape (`endpoints{}`, `s3_use_path_style`,
`skip_*`, dummy credentials). It validates **no token** and carries **no
subscription**.

So the spike's *conclusion* stopped following while its *reasoning* stayed
correct. The spike is **appended to, never rewritten** (D398): a rewritten spike
destroys the reasoning that made the original call defensible, and the next
reader cannot tell a decision that was reversed from one that was never made.
**Everything in that spike except the licensing finding still stands** — the
Docker footprint objection, the Cloud Pods DEFER, the chaos-injection NO-GO,
and the two standing guards below.

### The two standing guards, carried forward UNCHANGED

1. **NEVER source a performance, cost or capacity claim from emulator timings.**
   An emulator reproduces the AWS **API contract**, not AWS's **performance
   characteristics**. Twin cost/latency estimates stay sourced from the
   catalog/estimate engines and stay labelled `estimate=True`. The guard now
   travels **on the capability** — the commands doc, the `flx-ci-01` report
   path, the `flx-twin-01` manifest row — rather than sitting in a spike nobody
   re-reads.
2. **The IAM policy sandbox stays NO-GO.** The PDP/PEP ABAC engine in
   `tools/security/` already answers IAM questions offline and
   deterministically. A partial emulation gives a second answer to the same
   question with no rule for choosing between them. The licence was never the
   objection here, so removing the licence changes nothing.

## What shipped

| Epic | Card | What landed |
|------|------|-------------|
| seam | `flx-seam-01` | `tools/cloud/emulator.py` — the ONE switch (`enabled/endpoint/region/account_id/credentials/docker_backed/service_supported/status`). Both existing switches delegate; `LOCALSTACK_*` stay readable as **deprecated aliases** |
| seam | `flx-seam-02` | `.env.example` gains the `FLOCI_*` block, the switch LIVE at its air-gap-safe default and the rest **commented out** |
| compose | `flx-compose-01` | The pinned `floci` compose profile, persistent state, and — MEASURED — `data/floci/` **gitignored**, which it was not |
| compose | `flx-compose-02` | `floci` declared in `args/component_registry.yaml` as a `core_extension`, so `icdev enable floci` exists |
| bridge | `flx-bridge-01` | `localstack_connector.py` → `floci_connector.py`; each of the seven logical tables declares `docker_backed` |
| bridge | `flx-bridge-02` | The governed door: a `db_connections` row + the `twin_observatory_analyst` grant |
| studio | `flx-studio-01` | `LOCALSTACK_PROVIDER_OVERRIDE` → `FLOCI_PROVIDER_OVERRIDE`, `localstack_docker_endpoint` → `emulator_docker_endpoint` |
| studio | `flx-studio-02` | The workflow templates' four-mode vocabulary becomes **data**, bound to `detect_mode()` |
| sim | `flx-sim-01` | `dry_run` starts nothing; the three executing modes start what they declare; the image is read from the seam |
| gen | `flx-gen-01` | The infra_canvas adapter and the **generated customer compose** emit floci |
| airgap | `flx-airgap-01` | `tools/airgap/image_vendor.py` — `docker save`/`load` of a **pinned digest**, proven without a daemon |
| airgap | `flx-airgap-02` | The **eleven run-time base images**, measured from `docker events`, and a rule that fires on a host missing one |
| airgap | `flx-airgap-03` | `FLOCI_DOCKER_DOCKER_HOST` + per-registry credential **references**; an internal mirror satisfies the same ONE rule |
| twin | `flx-twin-01` | `tools/twin_core/adapters/floci.py` — reads **through the broker**, provenance `emulated`, `unknown` never `pass` |
| ci | `flx-ci-01` | The opt-in floci IaC gate — the spike's pattern 1, **never a required check** |
| ci | `flx-ci-02` | ONE pre-apply gate: the duplicate `pre_apply_gate.py` measured, then deleted |
| docs | `flx-docs-01` | This document, the ADRs, the dated spike addendum, the commands and manifest rows |

## The honesty rules this project runs on

Each is **enforced**, not stated.

- **An emulated estate is never readable as an observed one.** Every
  `floci_twin_snapshots` row carries provenance `emulated` — the
  `ni_devices.source` vocabulary, where a fabricated estate is spelled out as
  "NOT evidence of anything". `_persist_snapshot` takes **no** provenance
  parameter (asserted by reading its AST, because a behavioural test over
  today's callers would still pass the day somebody threads a kwarg through)
  and the database CHECKs the vocabulary (migration `20260905070028`).
- **An unanswerable question is not an empty answer.** A container-backed table
  on a socket-less host returns `unsupported_without_docker`, never `[]` — the
  `rmf-disc-02` defect exactly, where every local NQE query raised on a table
  with no DDL, was swallowed, returned `[]`, and the attack-surface map
  correlated every advisory against ZERO devices while reporting success.
  `resource_count` is `None`, never 0, when nothing was measured.
- **`unknown` is never `pass`.** The twin's four verdicts keep `unknown`
  (disabled / unreachable / broker-denied) apart from `pass`, and the BASIS is
  recovered from structured facts — `broker.list_available()` for the grant,
  `emulator.enabled()` for the switch — never from a refusal's prose.
- **A refusal is not fabricated either.** `docker_backed()` is **tri-state**:
  measured 2026-09-04, Docker Desktop 28.5.1 was RUNNING while
  `os.path.exists(r"\.\pipe\docker_engine")` returned False, so a plain
  existence check reports a definite absence for a working daemon. Only a
  PROVEN absence refuses.
- **The contradiction case is refused, not guessed.** An endpoint declared
  while the switch is off degrades to `dry_run` — plan only. Falling through to
  `aws` would send an apply written for localhost at a real GovCloud account.
- **Credentials are always `test`/`test`.** These values reach `docker run -e`
  and a Terraform provider block; the emulator accepts any non-empty pair, so
  honouring a real `AWS_ACCESS_KEY_ID` buys nothing and hands live keys to a
  container talking to localhost.

## Air-gap: what is proven, and what it costs

Removing the auth token makes disconnected operation **possible**. It does not
make it free, and the measurements say what it costs.

- **`floci/floci:2.0.1` cached is NECESSARY AND NOT SUFFICIENT.** floci resolves
  eleven further container base images from the public internet on first use of
  a container-backed service. They are enumerated with digests and per-service
  attribution in `args/floci_runtime_images.yaml`, **measured** from
  `docker events` (2026-09-05, Docker 28.5.1) — never read off a README — and
  the set is a function of the declared **variant**, not of the service.
- **A pin is a DIGEST, never a tag.** `tools/airgap/image_vendor.py` saves from
  the local cache and never pulls (an allowlisted `version|image|save|load`
  frozenset, AST-asserted), and verifies by re-hashing every OCI blob against
  its filename and matching `index.json` to the pin — **no daemon required**,
  because media is verified before there is anywhere to load it. `unmeasured`
  exits 2 and is never a clean bundle.
- **A registry-mandating site cannot pre-seed hosts**, so `flx-airgap-03`
  extends the ONE `airgap-emulator-runtime-images` rule with a second way to
  answer no. There is deliberately **no second rule**: two rules could disagree
  about what a run-time pull is, leaving a reviewer with two verdicts and no way
  to choose.
- **The host Docker socket is ROOT-EQUIVALENCE ON THE HOST**, and ships as an
  explicit security decision — Gap 65 in `docs/security/sandbox-coverage.md`,
  behind the opt-in profile, every port on `127.0.0.1`, no `env_file`, and a
  test asserting floci is the ONLY service granted it.

## Open findings this card NAMES rather than fixes

- **`tools/cloud/emulator.py` declares the image pin TWICE.** `DEFAULT_IMAGE`
  (`flx-gen-01`, read by `dockerfile_generator`) and `IMAGE` (`flx-sim-01`,
  read by the sim topologies) are two independent constants, each documented as
  the one place the tag lives. They **agree today** (`floci/floci:2.0.1`), so
  nothing is broken — which is exactly why the next version bump can move one
  and not the other with no symptom but an unattributable behaviour difference.
  `tests/cloud/test_flx_docs.py` now asserts the two agree, turning "they agree
  today" into a checked fact; collapsing them to one constant is a code change
  with its own red-first proof and is not this card's.
- **The packaged `icdev/` copies of two documents are behind.**
  `icdev/docs/reference/commands.md` is ~1,500 lines behind `docs/` and no `flx`
  card updated it; `icdev/tools/manifest/databridge.md` is missing the
  `Connection Seeder` row and carries a stale `RSS Connector` description. Both
  are **pre-existing** (measured against `HEAD` before this card touched
  either), unrelated to floci, and reconciling them means rewriting neighbouring
  rows — which this card was told not to do. `mirror_parity` reports the second
  as content-drift and does not gate on it, `.md` being merge=union by design.
  Named so the next reader does not mistake the packaged copy for the current
  one. The rows **this** card added are byte-identical in both trees.

## Not built, on purpose

`flx-az-01`, `flx-gcp-01`, `flx-oci-01` — the floci-az / floci-gcp / floci-oci
siblings — are in `backlog`. Each is gated on its own **dated parity
measurement**, exactly as `twx-spk-01` gated LocalStack. **None inherits floci's
AWS parity by sharing its authors**, and the OCI sibling is the youngest of the
four. `flx-test-01` (a gated suite that needs no Docker) was in flight when this
was written.
