# CUI // SP-CTI — `opensearch` stays on `2.19.5` (artifact-fresh-a7486018c7)

**Decision: KEEP `opensearchproject/opensearch:2.19.5`. Do not move it to `3.8.0`.**
Measured 2026-09-12 by driving a live floci 2.0.1. The freshness survey was right
that `3.8.0` exists upstream and wrong that it is a release this tree can move to
— here, more sharply than in the postgres case: **no caller can make floci 2.0.1
pull `3.8.0` at all.**

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `opensearchproject/opensearch:2.19.5`. Upstream publishes
> `3.8.0`. Decided on `version_tag`.

Both halves are true. Docker Hub serves 80 tags for `opensearchproject/opensearch`
and `3.8.0` is the greatest of the same shape (`N.N.N`) as the pin.

## The measurement

`vendor/images/images-floci-runtime.txt` enumerates the images **floci pulls at
run time**, so they can be pre-loaded into a local daemon before the host is
disconnected. The question that decides the file is "what does floci ask for",
not "what has OpenSearch released".

Driven the way flx-airgap-02 obtained the table — `floci/floci:2.0.1`, host
docker socket mounted, `deploy.resources.limits` mirrored from
`docker-compose.yml`, boto3 against `127.0.0.1:4566`, region `us-gov-west-1`:

| probe | observed |
|---|---|
| `list_versions()` | **Answers.** A closed table of 21 engine versions: `OpenSearch_3.6, 3.5, 3.4, 3.3, 3.2, 3.1, 3.0, 2.19, 2.17, 2.15, 2.13, 2.11, 2.9, 2.7, 2.5, 2.3, 1.3, 1.2` and `Elasticsearch_7.10, 7.9, 7.8`. There is no `OpenSearch_3.8`. |
| `create_domain(DomainName="probe-default")`, no `EngineVersion` | `EngineVersion: OpenSearch_2.19`. floci logged `OpenSearchDomainManager  Starting OpenSearch container for domain: probe-default (version=OpenSearch_2.19, image=opensearchproject/opensearch:2.19.5)` then `ImageCacheService  Image already present locally, skipping pull: opensearchproject/opensearch:2.19.5`. |
| `create_domain(EngineVersion="OpenSearch_3.8")` | `ValidationException: Unsupported EngineVersion: OpenSearch_3.8. Supported: [OpenSearch_3.6, … OpenSearch_2.19, …]` — **refused, no container, no pull.** |
| `create_domain(EngineVersion="OpenSearch_3.6")` | The top of the table. `image=opensearchproject/opensearch:3.6.0`, then `ImageCacheService  Pulling image: opensearchproject/opensearch:3.6.0`. |
| `create_domain(EngineVersion="3.8.0")` | Rejected client-side by the AWS model itself (`valid min length: 14`) — the raw image tag is not an `EngineVersion` spelling. |
| shipped binary (`/app/application`, Quarkus native) | contains the whole table as literals: 18 `opensearchproject/opensearch:<x>` strings from `1.2.4` to `3.6.0`, and three `docker.elastic.co/elasticsearch/elasticsearch-oss:<x>`. `3.8.0` is not among them. |

So the ref is resolved through a **closed `EngineVersion` → image-tag lookup**,
and `OpenSearch_2.19` → `opensearchproject/opensearch:2.19.5` is floci 2.0.1's
default.

## Why this is stronger than the postgres case

`artifact-fresh-5b1750f240` kept `postgres:16.3-alpine` because floci builds
`postgres:<EngineVersion>-alpine` and defaults to `16.3` — `18.6-alpine` was a
real image floci *would* pull if a caller named it, so the argument was about
the DEFAULT path. OpenSearch is not open that way. The table is closed and
floci validates against it, so `3.8.0` is unreachable by configuration:

* it is not in `list_versions()`,
* `CreateDomain` refuses the version that would select it,
* and the string is not in the binary.

Moving the pin would therefore have vendored an image **the emulator cannot be
made to pull**, while dropping `2.19.5` — the image the default path does pull —
out of the bundle. The first disconnected `create_domain` would then attempt a
run-time pull and fail, which is the exact failure `vendor/images/` exists to
prevent.

Two probes also disagree with each other in a way worth recording: RDS could not
be asked what it supports (`describe_db_engine_versions` → `UnsupportedOperation`,
per the postgres card) while OpenSearch answers `list_versions()` directly. **Per
service, ask before assuming you must drive blind** — one `list_versions()` call
would have shortened that card.

## What changed instead

`args/pinned_artifacts.yaml`'s `opensearch` entry grew `decided_by: consumer` +
`consumer: floci`, so `tools/airgap/artifact_freshness.py` decides it on **digest
drift** — the one currency question upstream can answer about a tag chosen by
someone else, and on an immutable tag a supply-chain alarm rather than a
version-bump nag. No code changed; the lane the postgres card built already
handles this shape.

Nothing is hidden: upstream's newest comparable tag is still fetched and carried
on `upstream_newest`, and both the JSON and the text render name it.

```
$ python -m tools.airgap.artifact_freshness --artifact opensearch --json
  "status": "current",  "basis": "digest",  "upstream_newest": "3.8.0",
  "reason": "floci requests opensearchproject/opensearch:2.19.5 and the tag
             still serves the pinned digest; upstream also publishes '3.8.0',
             which floci does not request"
```

The declared digest and the digest the tag serves today are the same
(`sha256:4ee82ec…f52c44`), so no re-measurement of the vendored bytes was owed.
The pin moves when **floci** moves — a re-measurement
(`python -m tools.cloud.runtime_images --measure-help`), not a bump — and the
`floci` entry in the same manifest is what re-asks that question.

## Residual risk, stated rather than buried

`opensearch:2.19.5` is a major line behind upstream's `3.8.0`, and floci's own
ceiling (`3.6.0`) is itself behind upstream. A deployment that wants a newer
OpenSearch must (a) declare a supported `EngineVersion` — `OpenSearch_3.6` at the
most — and (b) **vendor `opensearchproject/opensearch:3.6.0` as well**, which
this table does not enumerate for it. That is the `variant` rule
`args/floci_runtime_images.yaml` already states for Lambda runtimes, one level
finer, exactly as RDS states it for `EngineVersion`. Anything above `3.6.0` needs
a newer floci, not a newer pin. The emulator backs demo and test workloads, not
production data.

## Still not converted, and named rather than implied

Measured immediately after this change, the full survey reads **16 declared, 11
current, 5 behind, 0 unmeasurable**. The five, with the counts stated rather than
summarised:

| entry | pinned | upstream newest | digest drift |
|---|---|---|---|
| `lambda-python` | `3.11` | `3.13` | **yes** |
| `rds-mysql` | `8.0.36` | `26.7.0` | no |
| `elasticache-valkey` | `8` | `9` | **yes** |
| `ec2-amazonlinux` | `2023` | `2027` | **yes** |
| `ecr-registry` | `2` | `3` | no |

Note this list is **not** the postgres card's list. That card named five —
`mysql`, `registry`, `amazonlinux`, `valkey`, `opensearch`. `opensearch` leaves
it here, and `lambda-python` has since joined, so the set churned while the count
held at five. That is the reason these are enumerated on every card rather than
counted once.

**Two of the five are already in flight as this lands** — #2267
(`elasticache-valkey`) and #2269 (`rds-mysql`) were open alongside this PR, and
`origin/main` carried only the `rds-postgres` conversion when the table above was
measured. So read the table as the state of *this* tree on its measured date, and
re-run the survey rather than trusting it. That is the churn, happening.

The consumer argument plainly reaches all five, and they are still NOT flipped,
because this card measured the **opensearch** path and a row nobody observed is
the fabrication `vendor/images/README.md` is written against. Each arrives with
its own card: drive the emulator for that service, read the `ImageCacheService`
log line, convert the entry with the measurement written down — and try
`list_versions()` or its per-service equivalent first.

**Three of the five also report `digest_drift: true`, and that is a different
question this card does not answer.** `lambda/python:3.11`, `valkey:8` and
`amazonlinux:2023` are tags upstream REBUILDS in place, so the tag no longer
serves the bytes `vendor/images/images-floci-runtime.txt` recorded. Unlike a
version-tag nag, that is the one signal a consumer-decided pin is still decided
on — converting those entries to `decided_by: consumer` would leave them
`behind`, not silence them. Whether to re-vendor the new bytes is a
re-measurement, and it needs its own card.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact opensearch --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```

Driving floci needs the docker socket and port 4566. The probe domains
(`probe-default`, `probe-36`) were deleted, the emulator container removed, and
the extracted binary deleted. **No image was pulled and none was removed**: the
`3.6.0` pull was aborted as soon as the `image=` line was read, and
`docker images opensearchproject/opensearch` afterwards still lists only
`2.19.5`. Host RAM was 76% before the run and the host was left as found.
