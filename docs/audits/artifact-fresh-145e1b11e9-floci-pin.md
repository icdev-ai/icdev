# CUI // SP-CTI — `floci` stays on `2.0.1` past `2.2.0`, and this time the release notes contradict a measured fact (artifact-fresh-145e1b11e9)

This is the second time the `artifact_freshness` reflex has filed against the
emulator itself. The first, `artifact-fresh-1f206ea0ad` (upstream `2.1.0`,
2026-09-15), was kept at `2.0.1` for reasons recorded in
[`artifact-fresh-1f206ea0ad-floci-pin.md`](artifact-fresh-1f206ea0ad-floci-pin.md):
`floci/floci:2.0.1` is the root every measured floci decision in this tree was
taken against — the whole of `args/floci_runtime_images.yaml` and the six
`decided_by: consumer` entries in `args/pinned_artifacts.yaml`. Moving it is a
re-measurement, not a tag edit. All of that still holds. What is new is that
`2.2.0` does not merely *touch* those surfaces — it changes one of them in a way
that falsifies a sentence the measured table states.

## What the card said

> This tree pins `floci/floci:2.0.1`. Upstream publishes `2.2.0`. Decided on
> `version_tag`.

Re-derived 2026-10-07 — confirmed: `status: behind`, `basis: version_tag`,
`newest: "2.2.0"`, `tags_seen: 456`, `digest_drift: false` (declared and
observed digest are both
`sha256:4e451c39c7bb88e3cd4f87e8fc0c25d5b47695a51185d521e2241fa00486e8eb`).
The `2.0.1` tag has not moved; this is a genuine version gap.

## What I checked before deciding

`2.2.0` was published **2026-10-06T05:23:38Z** (GitHub release on
`floci-io/floci`, compare `2.1.0...2.2.0`). It is not a patch release: the
release body lists 744 entries — a `### Bug Fixes` section, a `### Features`
section of ~237 entries, and `### Performance Improvements`. Since the pin is
still `2.0.1`, the gap this card covers is `2.1.0` (already judged to need a
re-drive) **plus** all of that.

Entries that land directly on paths this tree measured on a live `2.0.1`:

| area | entry in `2.2.0` | what it contradicts or touches here |
|---|---|---|
| **elasticache** | `create single-node redis clusters and close provisioning races (#3634)` | `args/floci_runtime_images.yaml` header states as MEASURED: *"floci REFUSES `Engine=redis` on CreateCacheCluster"* and that Redis goes only through CreateReplicationGroup (→ `valkey/valkey:8`). On `2.2.0` that refusal is very likely gone, so a CreateCacheCluster Redis path that the table says does not exist may pull an image that nobody has recorded. |
| **elasticache** | `serve a second cache asking for 6379 instead of refusing it (#4693)`; `re-provision caches on restart (#4094)` | the `elasticache-valkey` consumer entry (`artifact-fresh-6ba5fc339e`, `artifact-fresh-ee3339893b`) |
| **rds** | `add SQL Server support to RDS (#3752)` | a NEW `Engine` value, so a new runtime base image that the table does not list (the table is keyed by declared configuration; this adds a variant) |
| **rds** | `apply ... EngineVersion on ModifyDBInstance (#4214)`; `read replicas`, `point-in-time restore`, `stop/start/reboot` | the `EngineVersion -> image tag` mapping the `rds-postgres` / `rds-mysql` consumer entries assert (`artifact-fresh-5b1750f240`, `artifact-fresh-da63da118f`) |
| **lambda** | `allow per-runtime image reference overrides (#4552)` | the `lambda-python` / `lambda-nodejs` entries assert the image ref is a constant floci holds per runtime; it is now configurable |
| **ecr** | `serve the registry data plane over TLS (#4040)`; `advertise TLS registry URIs on opt-in (#4252)`; `answer GetAuthorizationToken without the backing registry (#4635)`; pull-through cache rules | the `ecr-registry` backing-registry lifecycle `artifact-fresh-54fe12d6ad` pinned line-by-line |
| **ecs** | `pull task images at launch so a moved tag reaches the next task (#4583)` | changes WHEN a pull happens, i.e. what `docker events` would show during the measurement itself |
| **docker** | `move the image bases to UBI 9.8 (#4782)`; `own the floci-aws- container and volume prefix (#4273)`; `give internal Docker labels io.floci.* names (#4871)`; `add cpu limits and readonly rootfs to container specs (#3893)` | the emulator image itself is rebuilt on a new base — a different SBOM, not just a different version string |

None of these entries is marked `BREAKING`, and nothing here suggests `2.2.0`
is broken. The point is narrower: the first row is a line of
`args/floci_runtime_images.yaml` that would become **false** the moment the
pin moved, and the SQL Server row is an image the vendored air-gap bundle would
not contain. Moving the tag without re-driving the emulator would ship a
runtime-image table describing a binary this tree no longer runs — the exact
failure the card's own body warns about.

## The decision

**Keep `floci/floci:2.0.1`.** Same reason as `artifact-fresh-1f206ea0ad`, and a
stronger one: a measured statement in `args/floci_runtime_images.yaml` is
expected to stop being true on `2.2.0`, and the air-gap bundle would be missing
at least one image class (RDS SQL Server). A re-measurement against a
744-entry release is a multi-probe drive across every container-backed service.
It is not chore-sized, and this card is not the place to attempt it — it would
also mean pulling and running a full set of RDS/OpenSearch/MSK runtime
containers on a host whose Docker VM is load-bearing for Postgres and the CI
runners.

No file changes beyond this record and the `note` on the `floci` entry in
`args/pinned_artifacts.yaml`. `docker-compose.yml`,
`vendor/images/images-floci.txt`, `args/floci_iac_gate.yaml` and
`args/floci_runtime_images.yaml` are untouched, because nothing here claims a
re-measurement happened.

## What would need to happen before this pin moves

The five steps in
[`artifact-fresh-1f206ea0ad-floci-pin.md`](artifact-fresh-1f206ea0ad-floci-pin.md#what-would-need-to-happen-before-this-pin-moves)
still apply, with `2.2.0` (or whatever is newest then) in place of `2.1.0`,
plus three probes this release adds:

1. ElastiCache: drive `CreateCacheCluster(Engine=redis)` and record whether it
   now succeeds and which image it pulls. Correct the header of
   `args/floci_runtime_images.yaml` accordingly.
2. RDS: drive `CreateDBInstance` with a SQL Server engine and record the image
   it pulls as a new `variant` row (or record that it pulls nothing).
3. Lambda: confirm the per-runtime defaults are unchanged when no override is
   set (#4552 makes them overridable; the default is what the table records).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci --json
# status: behind, basis: version_tag, newest: "2.2.0", digest_drift: false
```

This keeps reading `behind` until the re-measurement lands or upstream publishes
past `2.2.0`. The reflex's idempotency key is `artifact-freshness:floci:2.2.0`,
so this finding files once; a further release is a new key and a new card,
which should be judged the same way: read the release notes against the measured
paths above, not against the version number.
