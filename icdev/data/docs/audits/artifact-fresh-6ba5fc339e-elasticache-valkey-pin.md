# CUI // SP-CTI — `elasticache-valkey` stays on `8`, and its DIGEST was re-measured a second time (artifact-fresh-6ba5fc339e)

**Decision: KEEP the tag `valkey/valkey:8`. Do NOT move it to `9`. MOVE the
digest.** Measured 2026-09-23.

## What the card said

The `artifact_freshness` reflex filed: this tree pins `valkey/valkey:8` at
`sha256:3fbd2e3e...`, and the tag now serves `sha256:640c5e62...` (decided on
`digest`). `upstream_newest` is `9`.

## Version half — unchanged, re-confirmed

`9` is still unreachable. `artifact-fresh-ee3339893b` measured that floci
2.0.1 holds the valkey ref as a CONSTANT (EngineVersion is never read). The
floci pin (`vendor/images/images-floci.txt`, `floci/floci:2.0.1`) has not
moved, and its native binary was grepped again today:

```
$ docker run --rm --entrypoint sh floci/floci@sha256:4e451c39... \
    -c "grep -ao 'valkey/valkey:[0-9A-Za-z._-]*' /app/application | sort | uniq -c"
      5 valkey/valkey:8
```

Moving to `9` would leave EVERY ElastiCache path unvendored.

## Digest half — re-measured directly

```
$ docker pull valkey/valkey:8
Digest: sha256:640c5e62cea04b6d6f2084232651d0cc70362d31f4f805e7be94dbed6855e8f2

$ docker image inspect --format '{{index .RepoDigests 0}} {{.Created}}' valkey/valkey:8
valkey/valkey@sha256:640c5e62cea04b6d6f2084232651d0cc70362d31f4f805e7be94dbed6855e8f2 2026-09-21T07:40:01Z
```

| | digest | `valkey-server --version` | `/etc/debian_version` |
|---|---|---|---|
| pinned before this card | `sha256:3fbd2e3e...` | 8.1.10 (build 9891dd30f525c8a2) | 13.6 |
| served by the tag on 2026-09-23 | `sha256:640c5e62...` | 8.1.10 (build f1f7d07da9c59343) | 13.7 |

Same engine, new Debian point release: a base-image rebuild, not an upgrade.
The pulled digest matches the one the reflex reported. No live floci was
driven, because floci's container comes from the tag and that was proven in
`artifact-fresh-ee3339893b`. Nothing about that has changed.

## What moved

* `vendor/images/images-floci-runtime.txt`: the `valkey/valkey@` pin line and
  its comment.
* `args/floci_runtime_images.yaml`: the `valkey/valkey:8` row's `digest:` and
  note, so `tests/cloud/test_floci_runtime_images.py` keeps agreeing with the
  pin file.
* `args/pinned_artifacts.yaml`: comment only. `decided_by: consumer` stands.
* `tests/airgap/test_artifact_freshness.py`: the re-measured/stale pair in
  `test_the_valkey_digest_is_the_one_re_measured_after_the_tag_moved`.

## Residual risk

* This will come back on the next Debian rebuild of `valkey:8`. The answer stays
  "re-vendor, never bump" until a floci release requests another tag.
* `image_vendor --save --topic floci-runtime` was not re-run (GB-scale, separate
  act); a `--verify` of a bundle cut before 2026-09-23 will fail on this line,
  correctly.
* The wheel mirror `icdev/data/args/floci_runtime_images.yaml` was already
  drifted before this card, and this change leaves it alone. Same reasoning as
  `artifact-fresh-66057392f8`.

## Reproducing

```bash
python -m tools.airgap.artifact_freshness --artifact elasticache-valkey --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
