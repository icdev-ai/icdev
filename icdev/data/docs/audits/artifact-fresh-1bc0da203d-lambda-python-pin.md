# CUI // SP-CTI — `lambda-python` stays on `3.12`; the digest was RE-VENDORED again (artifact-fresh-1bc0da203d)

**Decision: KEEP the tag `public.ecr.aws/lambda/python:3.12`. Re-vendor the
digest, which AWS has rebuilt under the tag again.** Measured 2026-10-05. This
is the outcome `docs/audits/artifact-fresh-9daf559a8b-lambda-python-pin.md`
predicted: "Expect this card back on every AWS rebuild; the answer is pull +
inspect + re-drive."

## What the card said

`artifact_freshness` (xrv-pin-01), decided on `digest` because `lambda-python`
is `decided_by: consumer` / `consumer: floci`:

> the tag floci chooses '3.12' has moved: pinned `sha256:7df298e6…`, upstream
> now serves `sha256:22103834…`

Re-derived at the start of this card, the same command reported `behind`
against a THIRD digest, `sha256:517bcc7d…`. The tag moved again between the
card being filed and being picked up.

## Why the tag is kept

Unchanged from artifact-fresh-9daf559a8b: floci builds the ref as
`lambda/python:<minor of the declared Runtime>`, and every Lambda this
repository's IaC generators emit declares `python3.12`. Nothing in this card
changes the declared runtime, so nothing here argues for a different tag.

## What moved under the tag, measured

Each digest pulled by digest and inspected:

| digest | created | `/etc/os-release` | `python3 --version` | rpm set (md5 of sorted `rpm -qa`) | awslambdaric / boto3 |
|---|---|---|---|---|---|
| `7df298e6…` (old pin) | 2026-10-02T15:47Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `22103834…` (card) | 2026-10-03T13:14Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |
| `517bcc7d…` (new pin) | 2026-10-05T00:21Z | Amazon Linux 2023.12.20260914 | 3.12.15 | `7102b2dd…` | 4.1.0 / 1.42.97 |

There were two rebuilds and none of these probes found a content change. The
digests differ anyway. The pin follows the bytes the tag serves, because an
air-gap bundle re-cut from the tag would hold `517bcc7d…`, and
`image_vendor --verify` checks the digest, not package versions.

## Re-driven against floci

`floci/floci:2.0.1` was run as a throwaway probe (`af-1bc0da203d-probe`,
`127.0.0.1:4597`, with the host docker socket) after a fresh
`docker pull public.ecr.aws/lambda/python:3.12`. A boto3
`create_function(Runtime="python3.12")` followed by `invoke` returned 200 with
`sys.version` = `3.12.15 (main, Oct  1 2026, …)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/lambda/python:3.12`, so it ran the image the new pin names.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
  move the `lambda/python3.12` digest from `sha256:7df298e6…` to
  `sha256:517bcc7dc1a3ba62e324961c1563cc54a2bd6dc1060188f9d376211ecb7b2926`.
  The tag is unchanged.
* `icdev/data/args/floci_runtime_images.yaml` is a byte-exact copy of
  `args/floci_runtime_images.yaml` (it was in sync before this change).

After the change, `python -m tools.airgap.artifact_freshness --artifact
lambda-python --json` reports `status: "current"` and `digest_drift: false`.

## Residual risk

* **This card will come back** on the next rebuild. It has now happened three
  times in four days. If that churn turns out to be noise, the follow-up is a
  decision about the reflex, for example comparing image content instead of the
  digest. Changing that rule is out of scope for this card.
* `image_vendor --save --topic floci-runtime` was **not** re-run. A bundle cut
  before today will fail `--verify` on this one line, which means the pin is
  working.
* An AI session made this decision. The PR review is the human decision the
  card asks for.

## Cleanup

The probe container and its function were removed. The `22103834…` image was
removed after inspection. `7df298e6…` remains cached, untagged.
