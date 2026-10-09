# CUI // SP-CTI — `kafka-redpanda` stays on `latest`, and its DIGEST was re-measured (artifact-fresh-5e94142e90)

**Decision: KEEP the tag `redpandadata/redpanda:latest`. Do NOT pin a
numbered release instead. MOVE the digest — the tag rebuilt, exactly as the
`# MUTABLE TAG upstream -- re-measure, never assume` comment beside it always
expected it would.** Measured 2026-09-18.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `redpandadata/redpanda:latest`. Upstream publishes
> `sha256:9e83cfa9…`. Decided on `digest`.
> Digest drift as well: the pinned tag no longer serves
> `sha256:468bd13a…`; upstream now serves `sha256:9e83cfa9…`.

This is the same recurrence already on record for the other two mutable-tag
entries in this file, `ec2-amazonlinux` and `eks-k3s`: nothing about floci
changed — same floci `2.0.1`, same `redpandadata/redpanda:latest` reference
recorded as `mutable_tag: true` in `args/floci_runtime_images.yaml` since the
original live measurement (flx-airgap-02, `docker events --filter
type=image` recorded while floci was driven through the kafka/MSK path with
boto3). Only the bytes the tag serves moved.

## Why `latest` is not itself a decision to re-argue

`kafka-redpanda`'s pin is not chosen from a floci EngineVersion/AMI table the
way `rds/postgres`, `rds/mysql` or `opensearch` are — floci's MSK path names
the image by the literal tag `redpandadata/redpanda:latest`, full stop, and
`args/floci_runtime_images.yaml` records that as a fact about the running
binary (`service: kafka`, `mutable_tag: true`), not a version this tree
picked. There is no "newer tag" to move to: `latest` already names whatever
upstream currently publishes, so a digest drift under it is a base-image
rebuild, not a release the tree is behind on. `decided_by`/`consumer`
semantics do not apply here the way they do for the RDS engine lines — this
row has no `variant` and no version table to consult.

## Digest — re-measured directly, not copied off the survey

```
$ docker pull redpandadata/redpanda:latest
Digest: sha256:9e83cfa99278f30d0133271c26bf670cd69c94ffa6ba0b42830dd0c3bd9dcfd9
Status: Downloaded newer image for redpandadata/redpanda:latest

$ docker image inspect redpandadata/redpanda:latest --format '{{index .RepoDigests 0}}'
redpandadata/redpanda@sha256:9e83cfa99278f30d0133271c26bf670cd69c94ffa6ba0b42830dd0c3bd9dcfd9

$ docker run --rm redpandadata/redpanda:latest --version
rpk version (Redpanda CLI): v26.2.3 (rev 3c9fc8dd623ed22ad66eaca7621fe7ab1c268369)

$ docker pull redpandadata/redpanda@sha256:468bd13a9f2bd24794cb7fddc867c767fb1008b9a07b297b89fde48c564d7d96
$ docker run --rm redpandadata/redpanda@sha256:468bd13a9f2bd24794cb7fddc867c767fb1008b9a07b297b89fde48c564d7d96 --version
rpk version (Redpanda CLI): v26.2.2 (rev 551a6866f4804ee5753e3ffb5953a3355733e78b)
```

| | digest | `rpk --version` |
|---|---|---|
| pinned before this card | `sha256:468bd13a…` | v26.2.2 |
| served by `:latest` on 2026-09-18 | `sha256:9e83cfa9…` | v26.2.3 |

A patch release under the same tag, and the digest matches exactly what the
reflex reported upstream — an independent confirmation, by identity, not an
assumption that the survey's own number is right.

No live floci was driven for this card. The mechanism that matters — floci
pulls `redpandadata/redpanda:latest` fresh rather than naming a digest itself,
so a re-pull changes what gets served without floci changing at all — was
already established live for the whole runtime image set during the
original flx-airgap-02 measurement, and re-running that same proof today
would show the identical mechanism, not a new fact.

## What moved

* `vendor/images/images-floci-runtime.txt` — the `kafka` digest line now
  reads `sha256:9e83cfa99278f30d0133271c26bf670cd69c94ffa6ba0b42830dd0c3bd9dcfd9`,
  and the comment records the rebuild and this file.
* `args/floci_runtime_images.yaml` — the `redpandadata/redpanda:latest` row's
  `digest:` field and note updated to match, so
  `tests/cloud/test_floci_runtime_images.py`'s cross-check that the two files
  agree keeps passing.

## Residual risk, stated rather than buried

* This will recur. `latest` is, by construction, always current — every
  future rebuild upstream files a new card with a new idempotency key, and
  the answer will again be "re-vendor the digest, do not touch the tag,"
  exactly as for `ec2-amazonlinux` and `eks-k3s`.
* `image_vendor --save --topic floci-runtime` was **not** re-run here, for
  the same reason given in the ec2-amazonlinux precedent: re-cutting the
  air-gap bundle is a separate, GB-scale act. A `--verify` of a bundle cut
  before 2026-09-18 will now fail on this one line, correctly — it names a
  digest the tag no longer serves.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact kafka-redpanda --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```

Two images were pulled (the `latest` tag being re-measured, and the
previously-pinned digest, retained upstream, pulled only to read its
`rpk --version` for comparison); nothing was removed, no floci container was
started.
