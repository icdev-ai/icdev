# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured a fourth time (artifact-fresh-d20e410382)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest — to what the tag serves TODAY, which is not
the digest the card named.** Measured 2026-10-02.

## What the card said, and what had changed by the time it was worked

The `artifact_freshness` reflex filed: this tree pins `:2023`, upstream now
serves `sha256:99860dc4...` where the pin file recorded `sha256:06da5a33...`
(decided on `digest`). This is the recurrence the three earlier cards
(`artifact-fresh-b5394142b3`, `artifact-fresh-f1edf3d78e`,
`artifact-fresh-66057392f8`) each predicted.

Re-deriving it on 2026-10-02 did NOT reproduce the card's digest:

```
$ python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
  "status": "behind", "basis": "digest",
  "declared_digest": "sha256:06da5a3362eda00c5114227ac81abe56dd9b943395553b7c64a310457f4d9e2b",
  "observed_digest": "sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061",
```

The tag rebuilt AGAIN between the card being filed and being picked up. Pinning
the card's `99860dc4...` would have landed a pin that was already one rebuild
stale, and the reflex would have filed the next card the same day.

## Version half — unchanged, not re-argued

`2027` is still not reachable: the floci `2.0.1` AMI catalogue (only `:2` and
`:2023` amazonlinux strings) was measured in `artifact-fresh-b5394142b3`, and
`vendor/images/images-floci.txt` still pins the same floci digest
(`sha256:4e451c39...`). `decided_by: consumer` / `consumer: floci` in
`args/pinned_artifacts.yaml` stands, so that file is untouched. The survey
still carries `"upstream_newest": "2027"` — reported, not hidden.

## Digest half — re-measured directly

```
$ docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
Digest: sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061
Status: Downloaded newer image for public.ecr.aws/amazonlinux/amazonlinux:2023

$ docker image inspect public.ecr.aws/amazonlinux/amazonlinux:2023 --format '{{json .RepoDigests}} {{.Created}} {{.Architecture}}'
["public.ecr.aws/amazonlinux/amazonlinux@sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061"] 2026-09-29T19:52:51.975879058Z amd64
```

All three digests were then run BY DIGEST and `/etc/os-release` read from each,
so the ordering is by identity and not inferred from the reflex's wording:

| | digest | `PRETTY_NAME` | image `Created` |
|---|---|---|---|
| pinned before this card | `sha256:06da5a33...` | Amazon Linux 2023.12.20260918 | 2026-09-18 |
| named by the card | `sha256:99860dc4...` | Amazon Linux 2023.12.20260928 | 2026-09-24 |
| served by the tag on 2026-10-02 (pinned now) | `sha256:12052e9b...` | Amazon Linux 2023.12.20260930 | 2026-09-29 |

The pulled digest equals the one the survey observed on the same day — two
independent readings (registry API, docker pull) agreeing. Same `2023.12`
minor on all three: base-image rebuilds, not an upgrade.

No live floci was driven: the mechanism (floci follows the tag, so a re-pull
changes what it serves) was proven live in `artifact-fresh-b5394142b3` and is
not a new fact. What this card did NOT observe is a floci `RunInstances`
against the new digest specifically.

## What moved

* `vendor/images/images-floci-runtime.txt` — `ec2` digest line and comment.
* `args/floci_runtime_images.yaml` — the `ec2-amazonlinux` row's `digest:` and
  note, so `tests/cloud/test_floci_runtime_images.py` keeps agreeing with the
  pin file.

## Residual risk

* This will recur on the next Amazon Linux rebuild — four cards in three weeks,
  and this one was overtaken before it was worked. The answer stays "re-vendor,
  never bump" until a floci release requests `2027`. A card for this artifact
  should always be re-derived first; its named digest is a snapshot.
* `image_vendor --save --topic floci-runtime` was not re-run (GB-scale, separate
  act); a `--verify` of a bundle cut before 2026-10-02 will fail on this one
  line, correctly.
* The wheel mirror `icdev/data/args/floci_runtime_images.yaml` was already
  drifted before this card and is left alone, for the reason
  `artifact-fresh-66057392f8` records: the sanctioned fix is a byte-exact
  single-file copy in its own reviewed change, not a side effect of a pin move.

## Reproducing

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
