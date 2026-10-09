# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured (artifact-fresh-b5394142b3)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest, because the tag is rolling and it had drifted.**
Measured 2026-09-12. The card filed two findings and they resolve differently:
the version finding is answered "not a release this tree can move to", the digest
finding is answered "re-vendored".

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `public.ecr.aws/amazonlinux/amazonlinux:2023`. Upstream
> publishes `2027`. Decided on `version_tag`.
> Digest drift as well: the pinned tag no longer serves
> `sha256:fb70bd54…`; upstream now serves `sha256:a0646b8b…`.

Both halves are true as stated. The public ECR repository serves 756 tags for
`amazonlinux/amazonlinux`; of the five that have this pin's shape — `1`, `2`,
`2023`, `2027`, `latest` — `2027` is the greatest, and it is a real release
(`2027`, `2027-minimal`, `2027.0.20260903.0`, published 2026-09-03).

## Finding 1 — `2027` is not a release this tree can move to

`vendor/images/images-floci-runtime.txt` enumerates the images **floci pulls at
run time**, so a host can pre-load them before it is disconnected. The question
that decides an entry is therefore "what does floci ask for", not "what has the
vendor released" — the argument `docs/audits/artifact-fresh-5b1750f240-rds-postgres-pin.md`
made for `postgres:16.3-alpine`, which named this entry as one of the five
siblings it did NOT convert and asked for exactly this measurement.

Driven live: `docker compose --profile floci up -d floci` (floci 2.0.1, host
docker socket mounted), boto3 against `127.0.0.1:4566`.

| probe | observed |
|---|---|
| `run_instances(ImageId="ami-0abcdef1234567891")` (the AL2023 AMI) | `ImageCacheService  Image already present locally, skipping pull: public.ecr.aws/amazonlinux/amazonlinux:2023`, then `Ec2ContainerManager  EC2 instance i-d8ea7c… running in container …`. The container ran that image. |
| `run_instances(ImageId="ami-0f00f00f00f00f00f")` (an AMI floci has never heard of) | `AmiImageResolver  Unknown AMI ID ami-0f00f00f00f00f00f; falling back to default image public.ecr.aws/amazonlinux/amazonlinux:2023`. No error — the request succeeded and started the same image. |
| `describe_images()` | 10 AMIs: Amazon Linux 2 and 2023, four Ubuntu, Debian 12, Alpine, a Windows Server 2022 entry. |
| shipped binary (`/app/application`, Quarkus native) | carries the catalogue as embedded YAML: `defaultDockerImage: public.ecr.aws/amazonlinux/amazonlinux:2023`, then one `dockerImage:` per AMI — `ami-amazonlinux2 -> …/amazonlinux:2`, `ami-ubuntu2204 -> …/ubuntu:22.04`, `ami-debian12 -> …/debian:12`, `ami-alpine -> …/alpine:latest`, `ami-ubuntu2404-cloud -> floci/ami-ubuntu:24.04-arm64`. The only amazonlinux strings in the whole binary are `:2` and `:2023`. |

So `2023` is **floci's own default**, and floci 2.0.1 has no code path — no
ImageId, known or unknown — that requests `amazonlinux:2027`. Moving the pin
would have vendored an image nothing pulls **and dropped the image the default
and unknown-AMI paths do pull** out of the air-gap bundle: the precise failure
`vendor/images/` exists to prevent, and the same reductio the postgres card
recorded.

`args/pinned_artifacts.yaml` therefore grew `decided_by: consumer` +
`consumer: floci` on this entry. Upstream's newest comparable tag is still
fetched and reported — nothing is hidden, it simply does not decide the status:

```
$ python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
  "status": "current",  "basis": "digest",  "upstream_newest": "2027",
  "reason": "floci requests public.ecr.aws/amazonlinux/amazonlinux:2023 and the
             tag still serves the pinned digest; upstream also publishes '2027',
             which floci does not request"
```

### A correction this measurement forced, which the card did not ask for

The row's note in `args/floci_runtime_images.yaml` read *"EC2 RunInstances starts
an Amazon Linux container, whatever ImageId is given."* The catalogue above shows
that is wrong, and wrong in the direction that costs an air-gapped operator a
demo: a deployment that launches `ami-ubuntu2204` needs `ubuntu:22.04` in its
bundle, and this table does not hold it. What IS universal is the FALLBACK. The
note now says which of the two it means — the same "declared configuration"
rule the file's header already states for Lambda runtimes and RDS engine
versions, one service further on.

## Finding 2 — the digest drift is real, and it is NOT the postgres situation

`postgres:16.3-alpine` names one build forever. `amazonlinux:2023` does not:
upstream rebuilds the major-version tag. Measured here, by identity rather than
by inference —

| | digest | `PRETTY_NAME` in the image |
|---|---|---|
| pinned when the table was first measured (2026-09-05) | `sha256:fb70bd54…` | `Amazon Linux 2023.12.20260831` |
| served by the same tag on 2026-09-12 | `sha256:a0646b8b…` | `Amazon Linux 2023.12.20260909` |

Same major release, a nine-day-later rebuild. So the pin was not "old" in the
sense finding 1 was about; it named a build the tag had stopped serving, and an
air-gap bundle re-cut from the tag on a fresh host would have failed
`image_vendor --verify` against the pin file.

**Re-measured, not copied off the survey.** `docker pull` of the tag, then
`docker image inspect … --format '{{index .RepoDigests 0}}'` (step 4 of
`python -m tools.cloud.runtime_images --measure-help`), then floci was driven a
third time to confirm it follows the tag: the new container reports
`Config.Image=…amazonlinux:2023` resolving to `sha256:a0646b8b…`, while the two
containers started before the pull still show the now-untagged `fb70bd54…`.
The digest in `vendor/images/images-floci-runtime.txt` and
`args/floci_runtime_images.yaml` moved together — `tests/cloud/test_floci_runtime_images.py`
asserts the two agree, so one measured fact cannot come to be spelled two ways.

The row is now flagged `mutable_tag: true`, beside `redpandadata/redpanda:latest`
and `rancher/k3s:latest`. It is the first row so flagged that DOES carry a tag
ordering: mutability and orderability are independent properties, and this entry
has one of each. The practical consequence is the one the two `:latest` rows
already carry — **a re-vendor re-measures this tag rather than assuming it.**

## Residual risk, stated rather than buried

* The drift will recur. Amazon rebuilds `2023` every few weeks, so this entry
  will file a card again on the next rebuild, and the answer will again be
  "re-vendor", not "bump". That is the signal working; do not silence it by
  removing the declared digest, which would make the entry `unmeasurable`.
* The old build (`fb70bd54…`, AL 2023.12.20260831) is still present in this
  host's image cache, untagged. Nothing was removed or pruned.
* `image_vendor --save --topic floci-runtime` was **not** re-run here: re-cutting
  the bundle is a separate, GB-scale act, and this card moved the pin the bundle
  is cut against. The next `--save` picks up the new digest; a `--verify` of a
  bundle cut before 2026-09-12 will now fail on this one line, correctly.
* Amazon Linux 2027 exists and is the release a NEW deployment would want. The
  pin moves when **floci** moves — a floci version whose AMI catalogue names
  `amazonlinux:2027` — which is a re-measurement, not a bump, and the `floci`
  entry in the same manifest is what re-asks that question.

## Still not converted, still named rather than implied

Four of the five siblings the postgres card listed remain decided by upstream's
release train and reported `behind` against releases floci does not request:
`mysql:8.0.36 -> 26.7.0`, `registry:2 -> 3`, `valkey:8 -> 9`,
`opensearch:2.19.5 -> 3.8.0`. The argument reaches them; the measurement has not
been made. Each needs its own card: drive the emulator for that service, read the
`ImageCacheService` line, convert the entry with the evidence written down.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```

Driving floci needs the docker socket and port 4566. The three probe instances
were terminated, the `floci-ec2-*` containers and `icdev-floci` removed, and the
compose network deleted. One image was pulled — the AL2023 tag being
re-measured — and none was removed.
