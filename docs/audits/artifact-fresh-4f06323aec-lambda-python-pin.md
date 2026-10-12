# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-4f06323aec)

**Decision: KEEP the tag `public.ecr.aws/lambda/python:3.12`. Re-vendor the
digest, which AWS has rebuilt under the tag again.** Measured 2026-10-12, using
the same steps as `docs/audits/artifact-fresh-799d481d97-lambda-python-pin.md`:
pull, inspect, re-drive floci.

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest`:

> the tag floci chooses '3.12' has moved: pinned `sha256:719d7d69…`, upstream
> now serves `sha256:4b4d6db4…`

The card names `719d7d69…` as the pin, but the tree had already moved off it
to `sha256:4b4d6db4…` (artifact-fresh-799d481d97, earlier the same day —
merged commit `73e163446`, 2026-10-12 05:57Z). The card (scheduled
2026-10-12 13:40Z) was filed against the earlier pin, naming as "upstream"
the very digest the tree already pins. One card behind the tree again —
the same failure shape 799d481d97 recorded on its own card.

## What the survey said when this card was picked up

I re-ran the survey at the start of this card
(`python -m tools.airgap.artifact_freshness --artifact lambda-python --json`).
It reported `behind` against `sha256:0f717e90…`, a rebuild created
2026-10-12T12:55Z — NEWER than the digest this card names, newer than the pin
799d481d97 set hours before. The tag had rebuilt again underneath both cards.

## Why the tag is kept

Nothing has changed since artifact-fresh-9daf559a8b. floci builds the ref as
`lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`.

## What moved under the tag, measured

I pulled each digest by digest and inspected it with
`docker run --entrypoint /bin/sh`:

| digest | created | `/etc/os-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `4b4d6db4…` (old pin) | 2026-10-11T13:25Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `0f717e90…` (new pin) | 2026-10-12T12:55Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

These probes found no content change between the two. The pin follows the
bytes anyway, because an air-gap bundle re-cut from the tag holds `0f717e90…`
and `image_vendor --verify` checks the digest. A fresh
`docker pull public.ecr.aws/lambda/python:3.12` resolved to `0f717e90…`
today.

## Re-driven against floci

I ran `floci/floci:2.0.1` as a throwaway probe (`af-4f06323aec-probe`,
`127.0.0.1:4598`, host docker socket) after the fresh
`docker pull public.ecr.aws/lambda/python:3.12`, which resolved to
`0f717e90…`. A boto3 `create_function(Runtime="python3.12")` followed by
`invoke` returned 200 with `sys.version` =
`3.12.15 (main, Oct  1 2026, 13:42:31)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so the image it ran is the one the new
pin names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:4b4d6db4…` to
  `sha256:0f717e90928485c3318cd5598d9572d504f02ae5f69a8e7292c5008269125e89`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` stays a byte-exact copy of
  `args/floci_runtime_images.yaml`.

## Residual risk

* **This card will come back.** There have been eight digest rebuilds since
  2026-10-02, and none of them showed a content change these probes can see —
  they now arrive roughly daily, faster than cards are worked. This card is
  also the second in a row filed against a pin that no longer exists, naming
  an upstream digest the tree already pins: the reflex reads a stale tree or
  a stale survey at filing time. Comparing image content rather than digest,
  re-checking the pin before filing, and suppressing a finding whose named
  upstream already equals the pin, are decisions about the reflex. They are
  out of scope for this card.
* I did not re-run `image_vendor --save --topic floci-runtime`.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

I removed the probe container and its function. Both pulled digests remain
cached, untagged except `0f717e90…`, which carries the local `:3.12` tag
exactly as the fresh `docker pull` placed it.
