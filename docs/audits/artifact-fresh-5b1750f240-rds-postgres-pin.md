# CUI // SP-CTI — `rds-postgres` stays on `16.3-alpine` (artifact-fresh-5b1750f240)

**Decision: KEEP `postgres:16.3-alpine`. Do not move it to `18.6-alpine`.**
Measured 2026-09-12. The freshness survey was right that the release exists and
wrong that it was a release this tree can move to.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `postgres:16.3-alpine`. Upstream publishes `18.6-alpine`.
> Decided on `version_tag`.

Both halves of that are true. Docker Hub serves 1,421 tags for `library/postgres`,
and `18.6-alpine` is the greatest of the same shape (`NN.N-alpine`) as the pin.

## Why moving the pin would have broken the air gap

`vendor/images/images-floci-runtime.txt` is not a list of images we picked. It is
the enumeration of images **floci pulls at run time**, so they can be pre-loaded
into a local daemon before the host is disconnected. The question that decides
that file is therefore "what does floci ask for", not "what has postgres
released".

Driven live the way flx-airgap-02 obtained the table — `floci/floci:2.0.1`, host
docker socket mounted, boto3 against `127.0.0.1:4566`:

| probe | observed |
|---|---|
| `create_db_instance(Engine="postgres")` | API returned `EngineVersion: 16.3`. floci logged `io.git.hec.flo.ser.lam.lau.ImageCacheService  Image already present locally, skipping pull: postgres:16.3-alpine`. The container it started ran `postgres:16.3-alpine`. |
| `create_db_instance(EngineVersion="16.99")` | floci logged `ImageCacheService  Pulling image: postgres:16.99-alpine`, then the daemon's 404: `failed to resolve reference "docker.io/library/postgres:16.99-alpine": not found`. |
| `describe_db_engine_versions(Engine="postgres")` | `UnsupportedOperation` — floci cannot be asked which versions it supports; it has to be driven. |
| shipped binary (`/app/application`, Quarkus native) | the only `-alpine` image strings it contains are `postgres:15-alpine` and `postgres:16-alpine`; the only mysql one is `mysql:8.0`. |

So the ref is built as **`postgres:<EngineVersion>-alpine`**, and **`16.3` is
floci 2.0.1's default EngineVersion**. `18.6-alpine` is an image floci requests
only if a caller names it.

Moving the pin would therefore have:

1. vendored `postgres:18.6-alpine`, which nothing in a default deployment pulls, and
2. **dropped `postgres:16.3-alpine` from the bundle** — so the first
   `create_db_instance(Engine="postgres")` on the disconnected side would try a
   run-time pull and fail. That is the exact failure `vendor/images/` exists to
   prevent.

This is the `variant` rule `args/floci_runtime_images.yaml` already states for
Lambda runtimes ("the image set is a function of DECLARED CONFIGURATION, not of
the service"), one level finer: for RDS the declared configuration includes
`EngineVersion`. A deployment that declares `EngineVersion=18.6` must vendor
`postgres:18.6-alpine` **as well**, and no table here can enumerate that for it.

## What changed instead

The survey was asking the wrong question of this entry, so the entry now says
which question is right. `args/pinned_artifacts.yaml` grew `decided_by: consumer`
+ `consumer: floci`, and `tools/airgap/artifact_freshness.py` decides such an
entry on **digest drift** — the one currency question upstream can answer about a
tag chosen by someone else, and on an immutable tag a supply-chain alarm rather
than a version-bump nag.

Nothing is hidden: upstream's newest comparable tag is still fetched and carried
on `upstream_newest`, and both the JSON and the text render name it.

```
$ python -m tools.airgap.artifact_freshness --artifact rds-postgres --json
  "status": "current",  "basis": "digest",  "upstream_newest": "18.6-alpine",
  "reason": "floci requests postgres:16.3-alpine and the tag still serves the
             pinned digest; upstream also publishes '18.6-alpine', which floci
             does not request"
```

The pin moves when **floci** moves. That is a re-measurement
(`python -m tools.cloud.runtime_images --measure-help`), not a bump — and the
`floci` entry in the same manifest is what re-asks it, so the delegation lands on
something that is itself surveyed. A test asserts `consumer:` names a declared
artifact, so it cannot rot into a dangling pointer.

## Residual risk, stated rather than buried

`postgres:16.3-alpine` was built 2024-06-03 and the 16 line is now at
`16.15-alpine`. floci will not pull 16.15 unless a caller asks for
`EngineVersion=16.15`, so this is not a stale *pin* — but a deployment that cares
about postgres CVEs in a demo stack should declare the EngineVersion it wants and
vendor that tag. The emulator backs demo and test workloads, not production data.

## Not converted here, and named rather than implied

Five sibling runtime entries are reported `behind` today against releases floci
does not request: `mysql:8.0.36 → 26.7.0`, `registry:2 → 3`,
`amazonlinux:2023 → 2027`, `valkey:8 → 9`, `opensearch:2.19.5 → 3.8.0`. The
argument above plainly reaches all of them — the binary's `mysql:8.0` string
makes `26.7.0` a reductio — but this card measured the **postgres** path, and a
row nobody observed is the fabrication `vendor/images/README.md` is written
against. Each arrives with its own card: drive the emulator for that service,
read the `ImageCacheService` log line, convert the entry with the measurement
written down.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact rds-postgres --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/airgap/test_artifact_freshness.py -q
```

Driving floci needs the docker socket and port 4566; the probe containers and the
`floci-rds-db-*` container it starts must be removed afterwards. No image was
pulled and none was removed while measuring this.
