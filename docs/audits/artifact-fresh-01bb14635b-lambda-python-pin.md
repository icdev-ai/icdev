# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-01bb14635b)

**Decision: keep the tag `public.ecr.aws/lambda/python:3.12` and re-vendor the
digest, because AWS has rebuilt the image under that tag again.** Measured
2026-10-17 with the same steps as
`docs/audits/artifact-fresh-c04b654d3d-lambda-python-pin.md`: pull, inspect,
re-drive floci.

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest`:

> the tag floci chooses '3.12' has moved: pinned `sha256:c05509c1…`, upstream
> now serves `sha256:471e480c…`

The pin it names (`c05509c1…`) was the tree's pin, set by artifact-fresh-c04b654d3d.

## What the survey said when this card was picked up

I re-ran the survey at the start of this card
(`python -m tools.airgap.artifact_freshness --artifact lambda-python --json`).
It reported `behind` against `sha256:3856536f…`, a build created
2026-10-15T13:25Z. That is NEWER than the `471e480c…` this card names
(created 2026-10-14T19:26Z). The tag has rebuilt twice since the last
re-vendor. I vendored the newer digest, because that is what a fresh pull of
the tag returns today.

## Why the tag is kept

Nothing has changed since artifact-fresh-9daf559a8b. floci builds the image ref
as `lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`. The survey's
`upstream_newest` is `3.14`, which is reachable only by a deployment that
declares `python3.14`; nothing here does.

## What moved under the tag, measured

I pulled each image by digest and inspected it with
`docker run --entrypoint /bin/sh`:

| digest | created | `/etc/system-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `c05509c1…` (old pin) | 2026-10-13T18:27Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `471e480c…` (card's upstream) | 2026-10-14T19:26Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `3856536f…` (new pin) | 2026-10-15T13:25Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

These probes found no content change between the three builds. The pin still
follows the bytes, because an air-gap bundle re-cut from the tag holds
`3856536f…` and `image_vendor --verify` checks the digest. A fresh
`docker pull public.ecr.aws/lambda/python:3.12` resolved to `3856536f…`.

## Re-driven against floci

I ran `floci/floci:2.0.1` as a throwaway probe (`af-01bb14635b-probe`,
`127.0.0.1:4596`, host docker socket) after that fresh pull. A boto3
`create_function(Runtime="python3.12")` followed by `invoke` returned 200 with
`sys.version` = `3.12.15 (main, Oct  1 2026, 13:42:31)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so the image it ran is the one the new pin
names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:c05509c1…` to
  `sha256:3856536f53e945debf4c90d0ff301cbaa837c07a4ab9b0e2c102c6e54ceb9762`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` stays a byte-exact copy of
  `args/floci_runtime_images.yaml`.

## Residual risk

* **This card will come back.** This is the twelfth digest rebuild since
  2026-10-02 with no content change these probes can see. The fix named in
  artifact-fresh-c04b654d3d still stands: compare image content rather than
  digest, or stop tracking a mutable tag's digest between deliberate
  re-vendors. Both are decisions about the reflex and are out of scope here.
* I did not re-run `image_vendor --save --topic floci-runtime`.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

I removed the probe container; its function was deleted before that. All three
pulled digests remain cached. Only `3856536f…` carries the local `:3.12` tag,
as the fresh `docker pull` placed it.
