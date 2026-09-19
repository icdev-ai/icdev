# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured a third time (artifact-fresh-66057392f8)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest.** Measured 2026-09-19.

## What the card said

The `artifact_freshness` reflex filed: this tree pins `:2023`, upstream now
serves `sha256:06da5a33...` where the pin file recorded `sha256:155687eb...`
(decided on `digest`). This is the recurrence
`artifact-fresh-b5394142b3` and `artifact-fresh-f1edf3d78e` both predicted:
Amazon rebuilds the `2023` major-version tag every few days.

## Version half — unchanged, not re-argued

`2027` is still not reachable: the floci `2.0.1` AMI catalogue (only `:2` and
`:2023` amazonlinux strings) was measured in `artifact-fresh-b5394142b3` and
floci has not changed. `decided_by: consumer` / `consumer: floci` in
`args/pinned_artifacts.yaml` stands, so that file is untouched.

## Digest half — re-measured directly

```
$ docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
Digest: sha256:06da5a3362eda00c5114227ac81abe56dd9b943395553b7c64a310457f4d9e2b

$ docker image inspect public.ecr.aws/amazonlinux/amazonlinux:2023 --format '{{index .RepoDigests 0}}'
public.ecr.aws/amazonlinux/amazonlinux@sha256:06da5a3362eda00c5114227ac81abe56dd9b943395553b7c64a310457f4d9e2b

$ docker run --rm --entrypoint cat public.ecr.aws/amazonlinux/amazonlinux:2023 /etc/os-release | grep PRETTY_NAME
PRETTY_NAME="Amazon Linux 2023.12.20260918"
```

| | digest | `PRETTY_NAME` |
|---|---|---|
| pinned before this card | `sha256:155687eb...` | Amazon Linux 2023.12.20260914 |
| served by the same tag on 2026-09-19 | `sha256:06da5a33...` | Amazon Linux 2023.12.20260918 |

The pulled digest equals the one the reflex reported, an independent
confirmation by identity. No live floci was driven: the mechanism (floci
follows the tag, so a re-pull changes what it serves) was proven live in
`artifact-fresh-b5394142b3` and is not a new fact.

## What moved

* `vendor/images/images-floci-runtime.txt` — `ec2` digest line and comment.
* `args/floci_runtime_images.yaml` — the `ec2-amazonlinux` row's `digest:` and
  note, so `tests/cloud/test_floci_runtime_images.py` keeps agreeing with the
  pin file.

## Residual risk

* This will recur on the next Amazon Linux rebuild; the answer stays
  "re-vendor, never bump" until a floci release requests `2027`.
* `image_vendor --save --topic floci-runtime` was not re-run (GB-scale, separate
  act); a `--verify` of a bundle cut before 2026-09-19 will fail on this one
  line, correctly.
* The wheel mirror `icdev/data/args/floci_runtime_images.yaml` was already
  drifted before this card (it still carries `fb70bd54`/`a0646b8b`); it is left
  alone because the sanctioned fix is a byte-exact single-file copy in its own
  reviewed change, not a side effect of a pin move.

## Reproducing

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
