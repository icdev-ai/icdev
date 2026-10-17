# CUI // SP-CTI — `floci` stays on `2.0.1` past `2.3.0`: the gap now holds two unmeasured releases (artifact-fresh-aacec4a146)

This is the third time the `artifact_freshness` reflex has filed against the
emulator itself:

| card | upstream | decision | record |
|---|---|---|---|
| `artifact-fresh-1f206ea0ad` | `2.1.0` (2026-09-15) | keep `2.0.1` | [`artifact-fresh-1f206ea0ad-floci-pin.md`](artifact-fresh-1f206ea0ad-floci-pin.md) |
| `artifact-fresh-145e1b11e9` | `2.2.0` (2026-10-07) | keep `2.0.1` | [`artifact-fresh-145e1b11e9-floci-pin.md`](artifact-fresh-145e1b11e9-floci-pin.md) |
| `artifact-fresh-aacec4a146` | `2.3.0` (2026-10-17) | **keep `2.0.1`** | this file |

`floci/floci:2.0.1` is still the root every measured floci decision in this
tree was taken against: the whole of `args/floci_runtime_images.yaml` and the
six `decided_by: consumer` entries in `args/pinned_artifacts.yaml`. Moving the
pin to `2.3.0` means moving across `2.1.0` **and** `2.2.0` as well. The earlier
records judged each of those to need a re-drive, and neither re-drive has
happened.

## What the card said

> This tree pins `floci/floci:2.0.1`. Upstream publishes `2.3.0`. Decided on
> `version_tag`.

Re-derived 2026-10-17 and confirmed: `status: behind`, `basis: version_tag`,
`newest: "2.3.0"`, `tags_seen: 477`, `digest_drift: false`. The declared and
observed digests are both
`sha256:4e451c39c7bb88e3cd4f87e8fc0c25d5b47695a51185d521e2241fa00486e8eb`, so
the `2.0.1` tag has not moved. This is a real version gap, not a mutable tag
drifting.

## What I checked before deciding

`2.3.0` was published **2026-10-15T05:39:06Z** (GitHub release on
`floci-io/floci`, compare `2.2.0...2.3.0`). The release body lists 309
entries: `### Bug Fixes`, `### Features` (~60) and
`### Performance Improvements`. None of them is marked `BREAKING`.

These entries land on container-backed paths that this tree measured, or that
the runtime-image table would have to start covering:

| area | entry in `2.3.0` | what it touches here |
|---|---|---|
| **elasticache** | `add serverless cache APIs`; CloudFormation `provision ElastiCache serverless caches` and `cache clusters` | A new way to make a cache, so possibly a new runtime container. `args/floci_runtime_images.yaml` lists only the replication-group path (`valkey/valkey:8`). The `elasticache-valkey` consumer entry was measured without serverless. |
| **elasticache** | `add user groups and associate them with replication groups` | The same replication-group path the `elasticache-valkey` entry pins |
| **eks** | `join EC2 instances to a cluster as nodes` | EKS node containers. The table records what an EKS cluster pulls on `2.0.1`, and this changes what a cluster is made of. |
| **opensearch** | `allow configuring a data volume for domain containers` | The OpenSearch domain container spec, which is a measured variant |
| **rds** | `engine versions, engine defaults and valid modifications`; `list Aurora engines in DescribeOrderableDBInstanceOptions` | The `EngineVersion -> image tag` mapping that the `rds-postgres` / `rds-mysql` consumer entries assert. `2.2.0` already touched it (`#4214`). |
| **ecr** | `advertise the registry at the published address (#4863)`; `retry occupied registry ports (#4857)` | The `ecr-registry` backing-registry lifecycle that `artifact-fresh-54fe12d6ad` pinned line by line. This adds to the `2.2.0` TLS / auth-token changes. |
| **ecs** | `mount and clean up ephemeral task volumes (#5511)`; `launch service tasks through capacity provider strategies (#5500)` | What a task launch creates, and so what `docker events` records during a measurement |
| **docker / core** | `make the Docker call timeout configurable`; `cut healthy wait from 5 s to 0.1 s`; connection-pool / replay changes (`#5171`, `#5285`, `#5294`, `#5333`) | When containers come up, relative to the observation window the earlier drives used |

On top of that, `2.3.0` carries all of `2.2.0`. The `2.2.0` record found that
release likely **falsifies** a line `args/floci_runtime_images.yaml` states as
measured ("floci REFUSES `Engine=redis` on CreateCacheCluster", `#3634`). It
also adds RDS SQL Server (`#3752`), an image class the air-gap bundle does not
hold, and rebuilds the emulator image on UBI 9.8 (`#4782`). Nothing in `2.3.0`
reverts any of those.

## The decision

**Keep `floci/floci:2.0.1`.** The reason is the one `artifact-fresh-145e1b11e9`
gave, now covering two releases. On the target binary:

- one measured line of the runtime-image table is expected to be false;
- at least one image class (RDS SQL Server) is missing from the vendored
  bundle;
- `2.3.0` adds at least two more container-creating paths (ElastiCache
  serverless, EKS EC2 nodes) that nobody has observed.

Moving the tag without re-driving the emulator would ship a runtime-image table
for a binary this tree no longer runs, which is the failure the card's own body
warns about.

The re-measurement is a multi-probe drive across every container-backed service
over a 1,000+ entry gap (`2.1.0` + `2.2.0` + `2.3.0`). That is not
chore-sized, and it would mean pulling and running a full set of
RDS/OpenSearch/MSK/EKS runtime containers on a host whose Docker VM is
load-bearing for Postgres and the CI runners. It belongs on its own card,
decided by a human. This card is not the place to do it.

The only file changes are this record and the `note` on the `floci` entry in
`args/pinned_artifacts.yaml` (plus its `icdev/data/` mirror).
`docker-compose.yml`, `vendor/images/images-floci.txt`,
`args/floci_iac_gate.yaml` and `args/floci_runtime_images.yaml` are untouched,
because nothing here claims a re-measurement happened.

## What would need to happen before this pin moves

Every step in
[`artifact-fresh-1f206ea0ad-floci-pin.md`](artifact-fresh-1f206ea0ad-floci-pin.md#what-would-need-to-happen-before-this-pin-moves)
and the three probes added by
[`artifact-fresh-145e1b11e9-floci-pin.md`](artifact-fresh-145e1b11e9-floci-pin.md#what-would-need-to-happen-before-this-pin-moves)
still apply, with the newest release in place of `2.1.0`. This release adds
four more:

1. ElastiCache serverless: drive `CreateServerlessCache` and record which image
   it pulls, as a new `variant` row, or record that it pulls nothing.
2. EKS: create a cluster, join an EC2 instance as a node, and record every
   image pulled.
3. RDS: confirm the `EngineVersion -> image tag` mapping under the new engine
   defaults for the `rds-postgres` / `rds-mysql` consumer entries.
4. ECR: re-confirm the backing-registry address and port behaviour that the
   `ecr-registry` entry pins (`#4857`, `#4863`).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci --json
# status: behind, basis: version_tag, newest: "2.3.0", digest_drift: false
```

This keeps reading `behind` until the re-measurement lands or upstream
publishes past `2.3.0`. The reflex's idempotency key is
`artifact-freshness:floci:2.3.0`, so this finding files once. A further
release is a new key and a new card, and should be judged the same way: read
the release notes against the measured paths above, not against the version
number. Three keeps in a row is also a signal. The useful next act is the
re-measurement card, not a fourth record.
