# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-c04b654d3d)

**Decision: keep the tag `public.ecr.aws/lambda/python:3.12` and re-vendor the
digest, because AWS has rebuilt the image under that tag again.** Measured
2026-10-13 with the same steps as
`docs/audits/artifact-fresh-4f06323aec-lambda-python-pin.md`: pull, inspect,
re-drive floci.

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest`:

> the tag floci chooses '3.12' has moved: pinned `sha256:0f717e90…`, upstream
> now serves `sha256:542709cf…`

The pin it names (`0f717e90…`) was the tree's pin, set by artifact-fresh-4f06323aec.

## What the survey said when this card was picked up

I re-ran the survey at the start of this card
(`python -m tools.airgap.artifact_freshness --artifact lambda-python --json`).
It reported `behind` against `sha256:c05509c1…`, a build created
2026-10-13T18:27Z. That is NEWER than the `542709cf…` this card names
(created 2026-10-12T22:25Z). The tag has rebuilt twice since the last
re-vendor. I vendored the newer digest, because that is what a fresh pull of
the tag returns today.

## Why the tag is kept

Nothing has changed since artifact-fresh-9daf559a8b. floci builds the image ref
as `lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`.

## What moved under the tag, measured

I pulled each image by digest and inspected it with
`docker run --entrypoint /bin/sh`:

| digest | created | `/etc/system-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `0f717e90…` (old pin) | 2026-10-12T12:55Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `542709cf…` (card's upstream) | 2026-10-12T22:25Z | — | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `c05509c1…` (new pin) | 2026-10-13T18:27Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

(`542709cf…` was probed for `VERSION="2023"`, Python, the rpm set and the two
packages, but not for the full `system-release` string.) These probes found no
content change between the three builds. The pin still follows the bytes,
because an air-gap bundle re-cut from the tag holds `c05509c1…` and
`image_vendor --verify` checks the digest. A fresh
`docker pull public.ecr.aws/lambda/python:3.12` resolved to `c05509c1…`.

## Re-driven against floci

I ran `floci/floci:2.0.1` as a throwaway probe (`af-c04b654d3d-probe`,
`127.0.0.1:4598`, host docker socket) after that fresh pull. A boto3
`create_function(Runtime="python3.12")` followed by `invoke` returned 200 with
`sys.version` = `3.12.15 (main, Oct  1 2026, 13:42:31)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so the image it ran is the one the new pin
names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:0f717e90…` to
  `sha256:c05509c1a303e7ca10acc26782f530ff1a8573411132e2a67c1924d687a65a96`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` stays a byte-exact copy of
  `args/floci_runtime_images.yaml`.

## Residual risk

* **This card will come back.** There have been ten digest rebuilds since
  2026-10-02, and none of them showed a content change these probes can see.
  Rebuilds now arrive more than once a day. Two possible fixes are to compare
  image content rather than digest, or to stop tracking a mutable tag's digest
  between deliberate re-vendors. Both are decisions about the reflex and are
  out of scope here.
* I did not re-run `image_vendor --save --topic floci-runtime`.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

I removed the probe container; its function was deleted before that. All three
pulled digests remain cached. Only `c05509c1…` carries the local `:3.12` tag,
as the fresh `docker pull` placed it.
