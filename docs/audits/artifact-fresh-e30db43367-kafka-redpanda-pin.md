# CUI // SP-CTI — `kafka-redpanda` stays on `latest`, and its DIGEST was re-measured again (artifact-fresh-e30db43367)

**Decision: KEEP the tag `redpandadata/redpanda:latest` and MOVE the digest.**
Measured 2026-10-07. This is the recurrence predicted in
[artifact-fresh-5e94142e90](artifact-fresh-5e94142e90-kafka-redpanda-pin.md).
The reasons for keeping `latest` have not changed. floci 2.0.1's MSK path names
the image by that literal tag (`mutable_tag: true` in
`args/floci_runtime_images.yaml`). Pinning a numbered release would vendor an
image floci never requests and would drop the one it does request.

## The card

The `artifact_freshness` reflex reported that `:latest` had moved from the
pinned `sha256:9e83cfa9…` to `sha256:c98c2f04…`, with basis `digest`.

## Re-measured directly

```
$ docker pull redpandadata/redpanda:latest
Digest: sha256:c98c2f04a751e6646012cc701bac5183252cee44c88ba1dfa412e4c7124f2e89
Status: Downloaded newer image for redpandadata/redpanda:latest

$ docker image inspect redpandadata/redpanda:latest --format '{{index .RepoDigests 0}} {{.Created}}'
redpandadata/redpanda@sha256:c98c2f04a751e6646012cc701bac5183252cee44c88ba1dfa412e4c7124f2e89 2026-10-06T17:45:18.93312794Z

$ docker run --rm redpandadata/redpanda:latest --version
rpk version (Redpanda CLI): v26.2.4 (rev 9a85b6b72a2efafabaac6e7997f2c477cfedd1f7)

$ docker run --rm redpandadata/redpanda@sha256:9e83cfa9…dcfd9 --version
rpk version (Redpanda CLI): v26.2.3 (rev 3c9fc8dd623ed22ad66eaca7621fe7ab1c268369)
```

| | digest | `rpk --version` |
|---|---|---|
| pinned before this card | `sha256:9e83cfa9…` | v26.2.3 |
| served by `:latest` on 2026-10-07 | `sha256:c98c2f04…` | v26.2.4 |

This is a patch release under the same tag. The pulled digest matches the one
the reflex reported upstream.

## What moved

* `vendor/images/images-floci-runtime.txt`: the kafka digest line, plus a
  comment recording the rebuild.
* `args/floci_runtime_images.yaml`: the row's `digest:` and note.
* `icdev/data/args/floci_runtime_images.yaml`: a byte-exact copy of the file
  above (the wheel mirror).

## Not done, stated

* No live floci was driven. floci pulls the tag rather than a digest, and the
  flx-airgap-02 measurement already established that, so re-driving it would
  show the same mechanism again.
* `image_vendor --save --topic floci-runtime` was not re-run. Re-cutting the
  air-gap bundle is a separate GB-scale act. A bundle cut before today will
  correctly fail `--verify` on this line.
* This will recur with every upstream rebuild of `latest`. The answer each time
  is to re-vendor the digest and leave the tag alone.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact kafka-redpanda --json
python -m pytest tests/airgap/test_artifact_freshness.py tests/cloud/test_floci_runtime_images.py -q
```
