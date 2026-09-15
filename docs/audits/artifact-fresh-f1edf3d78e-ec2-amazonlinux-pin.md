# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured again (artifact-fresh-f1edf3d78e)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest — the tag rebuilt again, exactly as
`docs/audits/artifact-fresh-b5394142b3-ec2-amazonlinux-pin.md` predicted it
would.** Measured 2026-09-15.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `public.ecr.aws/amazonlinux/amazonlinux:2023`. Upstream
> publishes `sha256:155687eb…`. Decided on `digest`.
> Digest drift as well: the pinned tag no longer serves
> `sha256:a0646b8b…`; upstream now serves `sha256:155687eb…`.

This is the recurrence the prior card named in its own "Residual risk"
section: *"The drift will recur. Amazon rebuilds `2023` every few weeks, so
this entry will file a card again on the next rebuild, and the answer will
again be 're-vendor', not 'bump'."* Nothing about floci changed between the
two cards — same floci `2.0.1`, same embedded AMI catalogue, same
`defaultDockerImage` fallback behaviour already measured live in
`artifact-fresh-b5394142b3`. Only the bytes served by the upstream tag moved,
three days after that card's own re-measurement (2026-09-09 → 2026-09-14).

## Version half — unchanged, not re-argued

`2027` is still not a release floci `2.0.1` can request: the AMI catalogue
measurement in `artifact-fresh-b5394142b3` (RunInstances against a known and
an unknown ImageId, `describe_images()`, the embedded catalogue read out of
the shipped binary) is a fact about that floci binary, not about the tag's
current digest, and it has not changed because floci has not changed. Nothing
in this card re-drives that measurement; re-driving it would not tell us
anything the prior card didn't already establish, and doing so anyway would
be exactly the busywork "prefer simpler" warns against. `decided_by: consumer`
/ `consumer: floci` stays as recorded in `args/pinned_artifacts.yaml`.

## Digest half — re-measured directly, not copied off the survey

```
$ docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
Digest: sha256:155687eb8e1f156da67beda919f1ca4dab36a9f6fc7576cc514d7773b7d6a311
Status: Downloaded newer image for public.ecr.aws/amazonlinux/amazonlinux:2023

$ docker image inspect public.ecr.aws/amazonlinux/amazonlinux:2023 \
    --format '{{index .RepoDigests 0}}'
public.ecr.aws/amazonlinux/amazonlinux@sha256:155687eb8e1f156da67beda919f1ca4dab36a9f6fc7576cc514d7773b7d6a311

$ docker run --rm public.ecr.aws/amazonlinux/amazonlinux:2023 cat /etc/os-release | grep PRETTY_NAME
PRETTY_NAME="Amazon Linux 2023.12.20260914"
```

The tag now serves a build five days newer than the one the previous card
recorded (`2023.12.20260909` → `2023.12.20260914`), same major release, and
the digest matches exactly what the reflex reported upstream — an independent
confirmation, by identity, not an assumption that the survey's own number is
right.

| | digest | `PRETTY_NAME` |
|---|---|---|
| pinned before this card | `sha256:a0646b8b…` | Amazon Linux 2023.12.20260909 |
| served by the same tag on 2026-09-15 | `sha256:155687eb…` | Amazon Linux 2023.12.20260914 |

No live floci was driven for this card. The behaviour that matters — floci
follows the tag rather than a digest it names itself, so a re-pull changes
what `ImageCacheService` serves without floci changing at all — was already
proven live in `artifact-fresh-b5394142b3` (pull, then re-run RunInstances,
then confirm the newer container resolves to the new digest while the older
containers keep the old one). Re-running that same proof today would show the
identical mechanism working the identical way; it is not a new fact, and
`args/floci_runtime_images.yaml`'s `--measure-help` recipe exists so the next
recurrence that DOES change floci's own behavior gets the full drive, not this
one.

## What moved

* `vendor/images/images-floci-runtime.txt` — digest line for `ec2` now reads
  `sha256:155687eb8e1f156da67beda919f1ca4dab36a9f6fc7576cc514d7773b7d6a311`,
  and the comment records both prior rebuilds plus this one.
* `args/floci_runtime_images.yaml` — the `ec2-amazonlinux` row's `digest:`
  field and note updated to match, so
  `tests/cloud/test_floci_runtime_images.py`'s cross-check that the two files
  agree keeps passing.
* `args/pinned_artifacts.yaml` — **not touched.** Its note explains *why*
  `decided_by: consumer` applies and that the tag is rolling; both are still
  true and neither depends on which specific digest is currently pinned.

## Residual risk, stated rather than buried

* This will recur again. Every future rebuild of the `2023` major-version tag
  files a new card with a new idempotency key, and the answer will again be
  "re-vendor the digest, do not touch the tag" — right up until a floci
  release ships an AMI catalogue that actually requests `2027`, at which point
  the version half of this decision changes and needs its own re-measurement.
* `image_vendor --save --topic floci-runtime` was **not** re-run here, for the
  same reason the prior card gave: re-cutting the air-gap bundle is a
  separate, GB-scale act. A `--verify` of a bundle cut before 2026-09-15 will
  now fail on this one line, correctly — it names a digest the tag no longer
  serves.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```

One image was pulled (the `amazonlinux:2023` tag being re-measured, replacing
its previous local copy under the same tag); nothing was removed, no floci
container was started.
