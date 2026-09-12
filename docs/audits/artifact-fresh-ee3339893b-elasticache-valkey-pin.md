# CUI // SP-CTI — `elasticache-valkey` stays on `valkey/valkey:8`, and its digest was re-cut (artifact-fresh-ee3339893b)

**Two findings, two different answers.**

1. **`8 -> 9` — REFUSED.** floci 2.0.1 hard-codes `valkey/valkey:8`. There is no
   declaration in any ElastiCache API that makes it request valkey 9, so
   vendoring 9 would leave *every* ElastiCache path unvendored.
2. **Digest drift `98c6217c... -> 3fbd2e3e...` — ACTED ON.** The tag really had
   moved. Re-measured and the pin files re-cut to the observed bytes.

Measured 2026-09-12, driving a live `floci/floci:2.0.1` with boto3 and the host
docker socket — the method `args/floci_runtime_images.yaml` records.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `valkey/valkey:8`. Upstream publishes `9`. Decided on
> `version_tag`. **Digest drift as well:** the pinned tag no longer serves
> `sha256:98c6217c...` — upstream now serves `sha256:3fbd2e3e...`.

Both halves are factually true. Docker Hub serves 293 tags for `valkey/valkey`;
`9` is the greatest of the pin's shape, and it is also what `:latest` resolves
to. The tag did move.

## Finding 1: the tag is a CONSTANT, not a default

This is the harder case than its sibling `rds-postgres`
([`artifact-fresh-5b1750f240`](artifact-fresh-5b1750f240-rds-postgres-pin.md)),
and the difference is load-bearing. There, floci *builds* the ref as
`postgres:<EngineVersion>-alpine` and `16.3` is its **default** — a caller who
names 18.6 gets `postgres:18.6-alpine` and must vendor it. ElastiCache has no
such knob at all.

| probe | observed |
|---|---|
| `create_replication_group(Engine="redis")`, no `EngineVersion` | floci logged `io.git.hec.flo.ser.lam.lau.ImageCacheService  Image already present locally, skipping pull: valkey/valkey:8`; started `floci-valkey-probe-default` from `valkey/valkey:8`; the container reported `Valkey version=8.1.10`. The API response carries **no `EngineVersion` field at all** (RDS's did). |
| `create_replication_group(Engine="redis", EngineVersion="7.1")` | succeeded; started `valkey/valkey:8`. |
| `create_replication_group(Engine="redis", EngineVersion="99.99")` | **succeeded** — and started `valkey/valkey:8`. The postgres analogue (`16.99`) produced a registry 404, because there the version reached the ref. Here it is never read. |
| `create_replication_group(Engine="valkey", EngineVersion="9")` | succeeded; started `valkey/valkey:8`. |
| `describe_cache_engine_versions()` | `UnsupportedOperation` — floci cannot be asked what it supports, it has to be driven. |
| `create_cache_cluster(Engine="redis")` | `InvalidParameterValue: Engine must be 'memcached'. For Redis/Valkey use CreateReplicationGroup.` — confirms the two-API split `args/floci_runtime_images.yaml` already records. |
| shipped binary (`/app/application`, Quarkus native, `grep -ao`) | contains **exactly one** valkey image string, `valkey/valkey:8`, at four sites. No template, no `valkey/valkey:9`, no `redis:` image string anywhere. |

All four replication groups, side by side:

```
floci-valkey-probe-valkey9    valkey/valkey:8
floci-valkey-probe-ev9999     valkey/valkey:8
floci-valkey-probe-ev71       valkey/valkey:8
floci-valkey-probe-default    valkey/valkey:8
```

So `EngineVersion` is accepted and **ignored**. Moving the pin to 9 would have
vendored an image floci never requests *and* dropped the only image it ever
requests — the first disconnected `create_replication_group` would then fail on
a pull, which is the precise failure `vendor/images/` exists to prevent. Unlike
the postgres case there is not even a caller who could opt back in.

`upstream_newest: "9"` is still carried on the survey result and printed in the
reason line. Not chasing a release is not the same as not knowing about it.

## Finding 2: the digest moved, and that one was real

`decided_by: consumer` decides on digest drift precisely so a case like this
still surfaces once the version noise is gone. Measured:

| | digest | engine reported by the running container |
|---|---|---|
| pinned (2026-09-05) | `sha256:98c6217c...` | `Valkey version=8.1.10` |
| upstream today (2026-09-12) | `sha256:3fbd2e3e...` | `Valkey version=8.1.10` |

Same engine either side, so this is a **base-image rebuild, not an upgrade** —
but it is still drift that matters: an air-gap bundle re-cut from the tag today
would not contain the bytes the pin file named, and `image_vendor.py --save`
resolves through the tag. Re-measured by pulling `valkey/valkey:8`, re-driving
floci, and reading `RepoDigests[0]` off the image the new container actually
ran (`floci-valkey-probe-redigest`) — the same derivation the original table
used, not a registry lookup written straight into the file.

## What changed

* `vendor/images/images-floci-runtime.txt` — valkey digest `98c6217c... ->
  3fbd2e3e...`, plus the reason the tag stays at `8`.
* `args/floci_runtime_images.yaml` — same digest (the test pins the two files to
  each other), and the measurement in the entry's `note`.
* `args/pinned_artifacts.yaml` — `elasticache-valkey` gains `decided_by:
  consumer` / `consumer: floci`.

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact elasticache-valkey --json
# status: current, basis: digest, upstream_newest: "9"
```

## What this did NOT do, and what is still owed

The other four runtime entries the sibling card named are untouched and still
file the wrong card: `rds-mysql` (`8.0.36 -> 26.7.0`), `ecr-registry` (`2 -> 3`),
`ec2-amazonlinux` (`2023 -> 2027`), `opensearch` (`2.19.5 -> 3.8.0`). Each needs
its own measurement; adding a row nobody observed is the fabrication
`vendor/images/README.md` is written against.

One observation made in passing and deliberately **not** acted on here:
`create_cache_cluster(Engine="memcached")` returned `EngineVersion: 1.6.22`
against a `memcached:1.6` pin. That is the `elasticache-memcached` entry, which
this card did not measure — whether `1.6` is a constant or a truncation of a
default is an open question owning its own card.
