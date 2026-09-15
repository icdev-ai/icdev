# CUI // SP-CTI — `floci` stays on `2.0.1`, and this is the pin the whole measured table stands on (artifact-fresh-1f206ea0ad)

**This is not one of the eleven runtime-base-image entries `floci` asks for at
run time — it is the emulator itself, and the six sibling `artifact-fresh-*`
decisions (`ecr-registry`, `rds-postgres`, `rds-mysql`, `elasticache-valkey`,
`ec2-amazonlinux`, `opensearch`) all say `consumer: floci` and were MEASURED by
driving a live `floci/floci:2.0.1`.** Moving this pin does not move a leaf; it
moves the root every one of those measurements was taken against.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci:2.0.1`. Upstream publishes `2.1.0`. Decided on
> `version_tag`.

Re-derived 2026-09-15 — confirmed: `status: behind`, `basis: version_tag`,
`newest: "2.1.0"`, `tags_seen: 411`. Digest drift is `false`: the `2.0.1` tag
itself has not moved, so this is a genuine version gap, not a mutable-tag
finding.

## What I checked before deciding

`floci/floci` is a real upstream project (`floci-io/floci` on GitHub, MIT,
Java/Quarkus — the Docker Hub description confirms it). `2.1.0` was published
**2026-09-15T04:56:15Z**, hours before this card was filed, so it is plausibly
the release that triggered the reflex. Its GitHub release body
(`compare/2.0.1...2.1.0`) is entirely `### Bug Fixes` — no `BREAKING CHANGES`
section, no `### Features`. That is reassuring in general and does not settle
the question this repository actually needs answered, which is narrower than
"is it safe to run": **does it change what the emulator does on the exact
service paths `args/floci_runtime_images.yaml` measured?**

Two clusters of fixes intersect those paths directly:

| area | fixes in `2.1.0` | what it touches here |
|---|---|---|
| **ecr** | `clean up registry storage`; `enforce immutable image tags`; `join the ECR API with the backing registry`; `recover backing registry containers` | the exact `ecr-registry` backing-registry lifecycle `artifact-fresh-54fe12d6ad` measured line-by-line (`ImageCacheService`/`EcrRegistryManager` log sequence, `registry:2` default, `FLOCI_SERVICES_ECR_REGISTRY_IMAGE` override behavior) |
| **rds** | `map Aurora MySQL engine versions to the MySQL image tag`; `use MySQL 8.4 auth options` | the `rds-mysql` (`artifact-fresh-da63da118f`) and `rds-postgres` (`artifact-fresh-5b1750f240`) `EngineVersion -> image tag` mapping the two `decided_by: consumer` entries assert |

Neither fix is described as changing a default tag or removing a code path —
but the changelog is a one-line commit summary, not a diff, and "join the ECR
API with the backing registry" is exactly the kind of description that could
mean the `CreateRepository` log sequence `artifact-fresh-54fe12d6ad` pinned
verbatim no longer reads the way it was measured. **Nobody has driven a live
`floci/floci:2.1.0` with boto3 to find out**, which is the same sentence the
card itself uses to describe why this is not a decision the reflex gets to
make.

## The decision

**Keep `floci/floci:2.0.1`.** Not because `2.1.0` is suspected to be broken —
nothing found here suggests that — but because six standing decisions in
`args/pinned_artifacts.yaml`, and the whole of `args/floci_runtime_images.yaml`,
are evidence about a specific binary, obtained by *driving* it rather than by
reading about it. Moving the root pin without re-driving `2.1.0` through the
same probes would leave that evidence describing a version this tree no longer
runs, silently — the exact "two files agree about a version that shipped a
year ago" failure `tools/airgap/artifact_freshness.py`'s own module docstring
was written to stop, one level up from where it currently looks.

No file changes beyond this record and the `note` on the `floci` entry in
`args/pinned_artifacts.yaml`. `docker-compose.yml`, `args/floci_iac_gate.yaml`
and `args/floci_runtime_images.yaml` are untouched — there is nothing to
re-cut because nothing here is claiming a re-measurement happened.

## What would need to happen before this pin moves

1. `docker pull floci/floci:2.1.0` and start it under the same profile
   (`docker compose --profile floci up -d floci`).
2. Re-run the ECR and RDS probes `artifact-fresh-54fe12d6ad`,
   `artifact-fresh-da63da118f` and `artifact-fresh-5b1750f240` document,
   confirming the `CreateRepository`/`CreateDBInstance` log sequences and
   default tags are unchanged (or recording what changed).
3. Re-derive `args/floci_runtime_images.yaml` in full —
   `python -m tools.cloud.runtime_images --measure-help` — since a version
   bump of the emulator itself is exactly the case that manifest's own header
   says invalidates a prior measurement.
4. Re-cut the digest in `vendor/images/images-floci.txt` and
   `args/floci_iac_gate.yaml`, and move `docker-compose.yml`'s `floci` image
   tag, in the same reviewed diff.
5. Re-run this survey and confirm `current`.

That is a multi-probe re-measurement effort comparable to the five sibling
cards combined, not a chore-sized diff, which is why it is named here as owed
rather than attempted in this card.

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci --json
# status: behind, basis: version_tag, newest: "2.1.0", digest_drift: false
```

This will keep reading `behind` until either the work above lands or upstream
publishes past `2.1.0` — the reflex's idempotency key is
`artifact-freshness:floci:2.1.0`, so this exact finding files once; a further
release is a new key and a new card, not a re-fire of this one.
