# CUI // SP-CTI — `ecr-registry` stays on `registry:2`, and this is the one where the newer release WORKS (artifact-fresh-54fe12d6ad)

**`registry:3` is real, floci runs it, and the pin does not move.** That
combination is new in this family, and it is the whole point of the record: the
four cards before this one refused a release because the emulator would not or
could not request it, and none of those arguments applies here. This one is
refused because **nothing in this repository sets the property that would make
floci ask for it**, so the path every deployment here actually takes still pulls
`registry:2` — and vendoring 3 instead would drop that path out of the air-gap
bundle.

Measured 2026-09-13, driving a live `floci/floci:2.0.1` with boto3 and the host
docker socket — the method `args/floci_runtime_images.yaml` records.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `registry:2`. Upstream publishes `3`. Decided on `version_tag`.

Factually true, and the digest half was clean: `registry:2` still served
`sha256:a3d8aaa6…`, the bytes `vendor/images/images-floci-runtime.txt` records,
re-checked against a fresh pull. So unlike the valkey card there was no second
finding hiding behind the version noise — one question, one answer.

## A third shape of default

The two shapes this file already had:

| shape | example | who can change the tag |
|---|---|---|
| **default behind an AWS API field** | `postgres:<EngineVersion>-alpine`, default `16.3` | any **caller** — `CreateDBInstance(EngineVersion=…)` |
| **constant** | `valkey/valkey:8` | **nobody**; EngineVersion is accepted and ignored |

`ecr-registry` is neither. floci names the backing registry through a property
of **its own deployment**, `floci.services.ecr.registry-image`
(`FLOCI_SERVICES_ECR_REGISTRY_IMAGE`), and uses the value as the **full ref** —
not a version substituted into a template. So no *caller* can vary it, and an
*operator* can.

Reading it as a constant would have produced the valkey conclusion ("3 is
unreachable") which is false. Reading it as an API default would have produced
the postgres conclusion by the wrong route ("a caller who wants 3 asks for it")
which is also false — no request field reaches it.

## Measured

| probe | observed |
|---|---|
| `create_repository()`, no override | floci logged `io.git.hec.flo.ser.lam.lau.ImageCacheService  Image already present locally, skipping pull: registry:2`, then `io.git.hec.flo.ser.ecr.reg.EcrRegistryManager  Started ECR backing registry floci-ecr-registry on host port 5100`. Container `floci-ecr-registry` ran `registry:2` and reported `service=registry version=2.8.3`. |
| `FLOCI_SERVICES_ECR_REGISTRY_IMAGE=registry:99.99` | `Pulling image: registry:99.99`, then `NotFoundException: Status 404: failed to resolve reference "docker.io/library/registry:99.99"`, and `CreateRepository` failed with `InternalFailure`. **The property reaches the ref verbatim.** |
| `FLOCI_SERVICES_ECR_REGISTRY_IMAGE=registry:3` | `Pulling image: registry:3`; `Started ECR backing registry floci-ecr-registry on host port 5100`; container reported `service=registry version=3.1.1`. **`CreateRepository` SUCCEEDED.** |
| `describe_registry()` | `UnsupportedOperation` — floci cannot be asked, it has to be driven, as with ElastiCache and RDS. |
| shipped binary (`/app/application`, Quarkus native, `grep -ao`) | exactly **one** registry image string, `registry:2`, at three sites. No `registry:3`, no template. The only other match is the log format `registry: {0}`. |
| repository-side | `docker pull registry:2` → `sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373`, unchanged from the pin. |
| this tree | nothing sets `FLOCI_SERVICES_ECR_REGISTRY_IMAGE`. `docker-compose.yml`'s `floci` service declares four environment variables and that is not one of them; a repo-wide grep finds only unrelated GitLab `$CI_REGISTRY_IMAGE` scaffolding. |

One correction to the existing row while measuring it.
`args/floci_runtime_images.yaml` attributed this image to the ECS/EKS probes —
"pulled during the ECS/EKS probes, not by an explicit ECR call". A plain
`CreateRepository` starts it on its own, so the row is reachable through the ECR
API in its own right. The `implies: {ecs: [ecr], eks: [ecr]}` mapping is
unaffected and still correct; only the provenance sentence was too narrow.

## The decision

**Keep `registry:2`.** floci's default is `registry:2`, this repository sets no
override, so a disconnected `CreateRepository` pulls `registry:2` or fails. That
is the failure `vendor/images/` exists to prevent, and re-cutting the file to
`registry:3` alone would cause it — vendoring an image floci is never asked for
while dropping the one it always pulls.

The honest statement of the alternative, which the other four cards could not
make: an operator who sets `FLOCI_SERVICES_ECR_REGISTRY_IMAGE=registry:3` gets a
working distribution 3.1.1, and **must vendor that tag too**. That is the
declared-configuration rule `args/floci_runtime_images.yaml` states for Lambda
runtimes and RDS EngineVersion, one level out: here the declaration is made
against the emulator rather than against a request.

`upstream_newest: "3"` stays on the survey result. Not chasing a release is not
the same as not knowing about it.

## What changed

* `args/pinned_artifacts.yaml` — `ecr-registry` gains `decided_by: consumer` /
  `consumer: floci` and the note; a new prose block records the third shape; the
  stale "Three remain: mysql, registry, opensearch" line is corrected (all three
  have since been measured).
* `args/floci_runtime_images.yaml` — the measurement in the entry's `note`,
  including the corrected provenance.
* `vendor/images/images-floci-runtime.txt` — the reason the tag stays at `2`.
  **No digest changed** — unlike the valkey and amazonlinux cards, there was
  nothing to re-cut.
* `tests/airgap/test_artifact_freshness.py` — the shipped-pin assertion, plus
  six restored assertions (below).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact ecr-registry --json
# status: current, basis: digest, upstream_newest: "3"
```

## Repaired in passing: six assertions that were lost to a merge

`test_the_shipped_ec2_pin_…`, `test_the_shipped_valkey_pin_…` and
`test_the_shipped_mysql_pin_…` each asserted `pinned` and **nothing else** on
main, while their postgres and opensearch siblings asserted
`decided_by == consumer` and `consumer == "floci"` as well. Those six lines were
written by their cards and dropped when sibling appends were hand-resolved —
the appended test bodies ended in identical lines, so the wrong halves were
merged. The consequence was live: deleting `decided_by: consumer` from any of
those three entries would have re-armed the upstream tag comparison and re-filed
the exact card each of them exists to refuse, with all three tests still green.
Restored here, along with the missing blank line between the four functions.

## What this did NOT do, and what is still owed

The survey now reads **16 declared / 16 current / 0 behind / 0 unmeasurable**,
and an empty `behind` list is the one result this family has never produced. It
should be read narrowly:

* Six entries are current *because* `decided_by: consumer` says upstream's
  release train is the wrong question, and each carries the release it declined
  (`18.6-alpine`, `26.7.0`, `9`, `3.8.0`, `2027`, `3`).
* **`lambda-python` reports `digest_drift: true` while reporting `current`.**
  It is decided on `version_tag`, no greater tag of its shape was found, and the
  drift does not reach the verdict — so the tag is serving bytes
  `vendor/images/` did not record and the survey says `current` anyway. That is
  a gap in the decision rule, not in a pin, and it is the next card here. It was
  observed, not fixed: fixing it means changing how a non-consumer entry is
  decided, which is a change to every row in the file and not a thing to
  smuggle into a pin decision.
