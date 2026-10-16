# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured a sixth time (artifact-fresh-ec04f18d53)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest to `sha256:18c71e6a...` — what the tag
serves today, ONE rebuild past the digest the card named.** Measured 2026-10-16.

## Re-derived first

```
$ python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
  "status": "behind", "basis": "digest",
  "declared_digest": "sha256:f8da58e59abcaf46c6c3aebc1a2f12195d3bdee88f337652c9e795541bc71dd5",
  "observed_digest": "sha256:18c71e6a20f8b0834b002b6002156a84eea76162d5adea72d2ea8b86800eaf91",
  "upstream_newest": "2027",
```

As in `artifact-fresh-d20e410382`, the tag moved again between the card being
filed (against `sha256:1eb8fe7a...`) and being worked. Pinning the card's digest
would have left the survey `behind` on arrival, so the live one is pinned.

## Version half — unchanged, not re-argued

`2027` is still unreachable: floci `2.0.1` (`vendor/images/images-floci.txt`
still pins `sha256:4e451c39...`) carries only `:2` and `:2023` amazonlinux
strings, measured in `artifact-fresh-b5394142b3`. `decided_by: consumer` /
`consumer: floci` in `args/pinned_artifacts.yaml` stands, so that file is
untouched.

## Digest half — re-measured

```
$ docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
Digest: sha256:18c71e6a20f8b0834b002b6002156a84eea76162d5adea72d2ea8b86800eaf91
$ docker image inspect ... --format '{{json .RepoDigests}} {{.Created}} {{.Architecture}}'
["public.ecr.aws/amazonlinux/amazonlinux@sha256:18c71e6a..."] 2026-10-14T18:33:03Z amd64
```

All three digests were run BY DIGEST (`grep PRETTY /etc/os-release; rpm -qa`):

| | digest | `PRETTY_NAME` | `rpm -qa` count |
|---|---|---|---|
| pinned before this card | `sha256:f8da58e5...` | Amazon Linux 2023.12.20261012 | 106 |
| named by the card | `sha256:1eb8fe7a...` | Amazon Linux 2023.12.20261013 | 106 |
| served by the tag on 2026-10-16 (pinned now) | `sha256:18c71e6a...` | Amazon Linux 2023.12.20261014 | 106 |

Same 106 packages, nothing added or removed. Old pin -> new pin differs only in
`tzdata 2026c -> 2026e` plus `system-release` and `amazon-linux-repo-cdn`
(release markers); card digest -> new pin differs only in the release markers.
A rebuild with a timezone-data update, not an upgrade.

## floci re-driven against the new digest

`floci/floci@sha256:4e451c39...` on a spare port (`127.0.0.1:4597`),
`RunInstances(ImageId=ami-0abcdef1234567891)`. floci logged
`ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/amazonlinux/amazonlinux:2023`, and the container it started,
`floci-ec2-i-7e587f9a04d78049d`, has `.Image == sha256:18c71e6a...` — the new
pin, by identity. Both containers were removed afterwards, by name.

## What moved

* `vendor/images/images-floci-runtime.txt` — `ec2` digest line and comment.
* `args/floci_runtime_images.yaml` — the `ec2-amazonlinux` row's `digest:` and
  note, so `tests/cloud/test_floci_runtime_images.py` keeps agreeing with the
  pin file.
* `icdev/data/args/floci_runtime_images.yaml` — byte-exact copy of the above.

## Residual risk

* Recurs on the next Amazon Linux rebuild — six cards in five weeks, and the tag
  rebuilt twice in the three days since the last one. The answer stays
  "re-vendor, never bump" until a floci release requests `2027`.
* `image_vendor --save --topic floci-runtime` was not re-run (GB-scale, separate
  act); a `--verify` of a bundle cut before 2026-10-16 fails on this line,
  correctly.

## Reproducing

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
