# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-799d481d97)

**Decision: KEEP the tag `public.ecr.aws/lambda/python:3.12`. Re-vendor the
digest, which AWS has rebuilt under the tag again.** Measured 2026-10-12, using
the same steps as `docs/audits/artifact-fresh-7d807fd882-lambda-python-pin.md`:
pull, inspect, re-drive floci.

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest`:

> the tag floci chooses '3.12' has moved: pinned `sha256:517bcc7d…`, upstream
> now serves `sha256:14535000…`

The card names `517bcc7d…` as the pin, but the tree had already moved off it
to `sha256:719d7d69…` (artifact-fresh-7d807fd882, 2026-10-10). The card was
filed against the earlier pin. The card's digest `14535000…` was created
2026-10-09T23:37Z, which is BEFORE `719d7d69…` (2026-10-10T13:17Z). So it is an
older rebuild than the current pin, not a newer one.

I re-ran the survey at the start of this card. It reported `behind` against
`sha256:4b4d6db4…`, a rebuild from 2026-10-11 that is newer than both.

## Why the tag is kept

Nothing has changed since artifact-fresh-9daf559a8b. floci builds the ref as
`lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`.

## What moved under the tag, measured

I pulled each digest by digest and inspected it with
`docker run --entrypoint /bin/sh`:

| digest | created | `/etc/os-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `14535000…` (card) | 2026-10-09T23:37Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `719d7d69…` (old pin) | 2026-10-10T13:17Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `4b4d6db4…` (new pin) | 2026-10-11T13:25Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

These probes found no content change in any of the three. The pin follows the
bytes anyway, because an air-gap bundle re-cut from the tag holds `4b4d6db4…`
and `image_vendor --verify` checks the digest.

## Re-driven against floci

I ran `floci/floci:2.0.1` as a throwaway probe (`af-799d481d97-probe`,
`127.0.0.1:4598`, host docker socket) after a fresh
`docker pull public.ecr.aws/lambda/python:3.12`, which resolved to
`4b4d6db4…`. A boto3 `create_function(Runtime="python3.12")` followed by
`invoke` returned 200 with `sys.version` =
`3.12.15 (main, Oct  1 2026, 13:42:31)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so the image it ran is the one the new pin
names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:719d7d69…` to
  `sha256:4b4d6db40cb5fb98fef32bdbe534d6ca52982824314ed7d1fec3ac0d8c9dba4b`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` stays a byte-exact copy of
  `args/floci_runtime_images.yaml`.

## Residual risk

* **This card will come back.** There have been seven digest rebuilds since
  2026-10-02, and none of them showed a content change these probes can see.
  This card also shows a second problem: the reflex filed it against a pin
  that had already been replaced, and named an upstream digest older than
  the current pin. Comparing image content rather than digest, and
  re-checking the pin before filing, are decisions about the reflex. They are
  out of scope for this card.
* I did not re-run `image_vendor --save --topic floci-runtime`.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

I removed the probe container and its function, and removed the `14535000…`
image after inspection. `719d7d69…` remains cached, untagged.
