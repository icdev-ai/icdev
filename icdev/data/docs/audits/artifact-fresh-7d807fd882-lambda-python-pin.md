# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-7d807fd882)

**Decision: KEEP the tag `public.ecr.aws/lambda/python:3.12`. Re-vendor the
digest, which AWS has rebuilt under the tag again.** Measured 2026-10-10, using
the same steps as `docs/audits/artifact-fresh-1bc0da203d-lambda-python-pin.md`:
pull, inspect, re-drive floci.

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest`:

> the tag floci chooses '3.12' has moved: pinned `sha256:517bcc7d…`, upstream
> now serves `sha256:b02e44f3…`

I re-ran the same command at the start of this card. It reported `behind`
against a THIRD digest, `sha256:719d7d69…`, so the tag moved again between
filing and pickup. This happened on the previous card too.

## Why the tag is kept

Nothing has changed since artifact-fresh-9daf559a8b. floci builds the ref as
`lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`.

## What moved under the tag, measured

I pulled each digest by digest and inspected it with
`docker run --entrypoint /bin/sh`:

| digest | created | `/etc/os-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `517bcc7d…` (old pin) | 2026-10-05T00:21Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `b02e44f3…` (card) | 2026-10-08T22:26Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `719d7d69…` (new pin) | 2026-10-10T13:17Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

The tag was rebuilt twice more and these probes found no content change in
either rebuild. The pin follows the bytes anyway, because an air-gap bundle
re-cut from the tag holds `719d7d69…` and `image_vendor --verify` checks the
digest.

## Re-driven against floci

I ran `floci/floci:2.0.1` as a throwaway probe (`af-7d807fd882-probe`,
`127.0.0.1:4597`, host docker socket) after a fresh
`docker pull public.ecr.aws/lambda/python:3.12`. A boto3
`create_function(Runtime="python3.12")` followed by `invoke` returned 200 with
`sys.version` = `3.12.15 (main, Oct  1 2026, 13:42:31)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so the image it ran is the one the new pin
names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:517bcc7d…` to
  `sha256:719d7d69d6e946b2ddd2d039723dc9415d0bcf1eecb6e12fa35dac58e23cebc6`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` stays a byte-exact copy of
  `args/floci_runtime_images.yaml`.

## Residual risk

* **This card will come back.** There have been five digest rebuilds since
  2026-10-02, and none of them showed a content change these probes can see.
  Comparing image content rather than digest, as 1bc0da203d also suggested, is
  a decision about the reflex. It is out of scope for this card.
* I did not re-run `image_vendor --save --topic floci-runtime`.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

I removed the probe container and its function, and removed the `b02e44f3…`
image after inspection. `517bcc7d…` remains cached, untagged.
