# CUI // SP-CTI — `rds-mysql` stays on `8.0.36` (artifact-fresh-da63da118f)

**Decision: KEEP `mysql:8.0.36`. Do not move it to `26.7.0`.**
Measured 2026-09-12. The freshness survey was right that a greater tag of the
same shape exists and wrong that it was a release this tree can move to.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `mysql:8.0.36`. Upstream publishes `26.7.0`.
> Decided on `version_tag`.

Both halves are literally true. Docker Hub serves 384 tags for `library/mysql`
and `26.7.0` is the greatest of the pin's shape.

## This is the postgres argument, but it was NOT assumed from it

`docs/audits/artifact-fresh-5b1750f240-rds-postgres-pin.md` reached the same
verdict for `postgres:16.3-alpine` and said plainly that the argument "plainly
reaches" mysql — while refusing to convert the entry, because a row nobody
observed is the fabrication `vendor/images/README.md` is written against. So
this card drove the **mysql** path itself. It is a good thing it did: the
postgres row would have taught the wrong ref shape (see the negative control).

Driven live the way flx-airgap-02 obtained the table — `floci/floci:2.0.1`, host
docker socket mounted, boto3 against a probe emulator on `127.0.0.1:4599` (the
compose port 4566 was in use by a concurrent session):

| probe | observed |
|---|---|
| `create_db_instance(Engine="mysql")` | API returned `EngineVersion: 8.0.36`. floci logged `io.git.hec.flo.ser.lam.lau.ImageCacheService  Image already present locally, skipping pull: mysql:8.0.36`. The container it started, `floci-rds-db-F24CC2CEAB…`, ran `mysql:8.0.36` at digest `sha256:a532724022429812ec797c285c1b540a644c15e248579c6bfdf12a8fbaab4964` — **the digest this tree pins**, and the server announced itself `mysqld 8.0.36`. |
| `create_db_instance(Engine="mysql", EngineVersion="8.0.99")` | floci logged `ImageCacheService  Pulling image: mysql:8.0.99`, then the daemon's 404: `failed to resolve reference "docker.io/library/mysql:8.0.99": not found`. |
| `create_db_instance(Engine="mariadb", EngineVersion="99.99")` | `Pulling image: mariadb:99.99`, then a 404. A **different repository**. |
| `describe_db_engine_versions(Engine="mysql")` | `UnsupportedOperation` — floci cannot be asked which versions it supports; it has to be driven. |
| shipped binary (`/app/application`, Quarkus native) | the only mysql image string is `mysql:8.0`; the only mariadb one is `mariadb:11`. Nothing resembling `26.7.0` exists in it. |

So the ref is built as **`mysql:<EngineVersion>`** and **`8.0.36` is floci
2.0.1's default EngineVersion**.

### The one thing the postgres card could not have told us

**The mysql ref carries no suffix.** postgres is `postgres:<EngineVersion>-alpine`;
mysql is bare. `-alpine` is per-engine, not a house style — and a conversion that
had generalised the postgres row would have declared `mysql:8.0.36-alpine`, a tag
that does not exist. That is the whole reason the sibling card refused to convert
five entries it had not driven, and this probe is what that refusal bought.

## Why moving the pin would have broken the air gap

`vendor/images/images-floci-runtime.txt` is not a list of images we picked. It is
the enumeration of images **floci pulls at run time**, so they can be pre-loaded
into a local daemon before the host is disconnected. Moving the pin would have:

1. vendored `mysql:26.7.0`, which nothing in a default deployment pulls, and
2. **dropped `mysql:8.0.36` from the bundle** — so the first
   `create_db_instance(Engine="mysql")` on the disconnected side would try a
   run-time pull and fail. That is the exact failure `vendor/images/` exists to
   prevent.

## What changed instead

`args/pinned_artifacts.yaml` grew `decided_by: consumer` + `consumer: floci` on
`rds-mysql`, so the entry is decided on **digest drift** — the one currency
question upstream can answer about a tag chosen by someone else. The declared
and observed digests agree today, so:

```
$ python -m tools.airgap.artifact_freshness --artifact rds-mysql --json
  "status": "current",  "basis": "digest",  "upstream_newest": "26.7.0",
  "reason": "floci requests mysql:8.0.36 and the tag still serves the pinned
             digest; upstream also publishes '26.7.0', which floci does not
             request"
```

Nothing is hidden: `26.7.0` is still fetched and named on `upstream_newest`, in
both the JSON and the text render. It simply does not decide the status. The pin
moves when **floci** moves, which is a re-measurement
(`python -m tools.cloud.runtime_images --measure-help`), not a bump.

`args/floci_runtime_images.yaml` — the declaration of record — now carries the
measurement on the mysql row itself rather than inheriting a sentence from the
postgres row, and `vendor/images/images-floci-runtime.txt` says next to the pin
why a newer mysql is not an upgrade for that line.
`tests/airgap/test_artifact_freshness.py` gained the sibling of the postgres
assertion, so re-arming the tag comparison on this entry fails a gated test
rather than filing the card again.

## Residual risk, stated rather than buried

* `mysql:8.0.36` was released 2024-01. floci will not pull a newer 8.0.x unless a
  caller asks for `EngineVersion=8.0.x`, so this is not a stale *pin* — but a
  deployment that cares about mysql CVEs in a demo stack should declare the
  EngineVersion it wants and vendor that tag. The emulator backs demo and test
  workloads, not production data.
* **`Engine=mariadb` is not covered by this entry and not vendored at all.**
  `args/floci_runtime_images.yaml` lists `mariadb` among the aliases of the `rds`
  `mysql` variant, which is right for *routing a request to the rds/mysql lane*
  and wrong as a claim that this image covers it: floci builds
  `mariadb:<EngineVersion>` from a different repository. Its default tag is
  unmeasured here because measuring it pulls an image (the binary's only mariadb
  string is `mariadb:11`), and nothing in this tree declares `Engine=mariadb`. A
  deployment that does must measure and vendor it; this is named, not fixed.
* `26.7.0` is a real `library/mysql` tag. This card does not claim otherwise — it
  claims floci never asks for it.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact rds-mysql --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```

Driving floci needs the docker socket and a free host port; the probe container
and the `floci-rds-db-*` container it starts must be removed afterwards. **No
image was pulled and none was removed while measuring this** — `mysql:8.0.36` was
already in the local cache (which is what the `skipping pull` line records), and
both negative controls 404'd before any layer was fetched.
