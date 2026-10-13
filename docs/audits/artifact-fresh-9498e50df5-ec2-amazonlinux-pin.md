# CUI // SP-CTI — `ec2-amazonlinux` stays on `2023`, and its DIGEST was re-measured a fifth time (artifact-fresh-9498e50df5)

**Decision: KEEP the tag `public.ecr.aws/amazonlinux/amazonlinux:2023`. Do NOT
move it to `2027`. MOVE the digest to `sha256:f8da58e5...`, which is the digest
the card named and what the tag serves today.** Measured 2026-10-13.

## Re-derived first

```
$ python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
  "status": "behind", "basis": "digest",
  "declared_digest": "sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061",
  "observed_digest": "sha256:f8da58e59abcaf46c6c3aebc1a2f12195d3bdee88f337652c9e795541bc71dd5",
  "upstream_newest": "2027",
```

Unlike `artifact-fresh-d20e410382`, the tag had NOT moved again between filing
and pickup: the card's digest is the live one.

## Version half — unchanged, not re-argued

`2027` is still unreachable: floci `2.0.1` (`vendor/images/images-floci.txt`
still pins `sha256:4e451c39...`) carries only `:2` and `:2023` amazonlinux
strings, measured in `artifact-fresh-b5394142b3`. `decided_by: consumer` /
`consumer: floci` in `args/pinned_artifacts.yaml` stands, so that file is
untouched. `upstream_newest: 2027` stays reported.

## Digest half — re-measured

```
$ docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
Digest: sha256:f8da58e59abcaf46c6c3aebc1a2f12195d3bdee88f337652c9e795541bc71dd5
$ docker image inspect ... --format '{{json .RepoDigests}} {{.Created}} {{.Architecture}}'
["public.ecr.aws/amazonlinux/amazonlinux@sha256:f8da58e5..."] 2026-10-08T17:08:05Z amd64
```

Both digests were run BY DIGEST:

| | digest | `PRETTY_NAME` | `rpm -qa` count |
|---|---|---|---|
| pinned before this card | `sha256:12052e9b...` | Amazon Linux 2023.12.20260930 | 106 |
| served by the tag on 2026-10-13 (pinned now) | `sha256:f8da58e5...` | Amazon Linux 2023.12.20261012 | 106 |

The package diff is distro patch releases only, no package added or removed:
`openssl-libs` / `openssl-fips-provider-latest` 3.5.8 `amzn2023.0.1 -> 0.3`,
`dnf`/`yum`/`libdnf`/`python3-dnf`/`python3-hawkey`/`python3-libdnf`
`0.8 -> 0.9`, `libsolv 0.4 -> 0.5`, `python3-pip-wheel 0.21 -> 0.22`, plus
`system-release` and `amazon-linux-repo-cdn`. A base-image rebuild with
security patches, not an upgrade.

## floci re-driven against the new digest

This time a live floci WAS driven (the previous card did not): `floci/floci@sha256:4e451c39...`
on a spare port (`127.0.0.1:4597`), `RunInstances(ImageId=ami-0abcdef1234567891)`.
floci logged `ImageCacheService  Image already present locally, skipping pull:
public.ecr.aws/amazonlinux/amazonlinux:2023`, and the container it started,
`floci-ec2-i-f104eb376ec50b3c1`, has `.Image == sha256:f8da58e5...` — the new
pin, by identity. Both containers were removed afterwards.

## What moved

* `vendor/images/images-floci-runtime.txt` — `ec2` digest line and comment.
* `args/floci_runtime_images.yaml` — the `ec2-amazonlinux` row's `digest:` and
  note, so `tests/cloud/test_floci_runtime_images.py` keeps agreeing with the
  pin file.
* `icdev/data/args/floci_runtime_images.yaml` — byte-exact copy of the above
  (the mirror was byte-identical before this change, so it stays so).

## Residual risk

* Recurs on the next Amazon Linux rebuild — five cards in a month. The answer
  stays "re-vendor, never bump" until a floci release requests `2027`.
* `image_vendor --save --topic floci-runtime` was not re-run (GB-scale, separate
  act); a `--verify` of a bundle cut before 2026-10-13 fails on this line,
  correctly.

## Reproducing

```bash
python -m tools.airgap.artifact_freshness --artifact ec2-amazonlinux --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
