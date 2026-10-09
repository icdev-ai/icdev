# CUI // SP-CTI — `lambda-nodejs` stays on `20` (artifact-fresh-333eeead1d)

**Decision: KEEP `public.ecr.aws/lambda/nodejs:20`. Do not move it to `24`.**
Measured 2026-09-19. The survey was right that `24` exists and is reachable; it was
wrong that this tree should chase it.

## What the card said

`artifact_freshness` (xrv-pin-01) filed: pinned `20`, upstream `24`, decided on
`version_tag`. Both literally true.

## Measurement

Driven live the way flx-airgap-02 obtained the table: `floci/floci:2.0.1`, host
docker socket mounted, boto3 against a throwaway probe emulator on
`127.0.0.1:4597` (memory storage, removed afterwards along with the two Lambda
containers it spawned).

| probe | observed |
|---|---|
| `create_function(Runtime="nodejs20.x")` + `invoke` | 200, `process.version` = `v20.20.2`. floci logged `ImageCacheService  Image already present locally, skipping pull: public.ecr.aws/lambda/nodejs:20`. |
| `create_function(Runtime="nodejs24.x")` + `invoke` | 200, `process.version` = `v24.21.0`. floci logged `Pulling image: public.ecr.aws/lambda/nodejs:24`, then `Image pulled successfully`. Digest observed: `sha256:cf160411731bb339542e275373bd272f0940b6e1e5a2e0499864aa10c6d806ce`. |

So the ref is `public.ecr.aws/lambda/nodejs:<major>`, chosen by the **declared
`Runtime`**, and `24` is reachable and works. That is the same shape as
`lambda-python` — and the reason the outcome differs is what the repository
declares.

## Why keep, when `lambda-python` moved

`docs/audits/artifact-fresh-591100ea58-lambda-python-pin.md` moved python
`3.11 -> 3.12` because every IaC generator in the tree declares `python3.12`; the
old pin vendored a runtime nothing requested. Here the same search
(`nodejs` / `runtime` in `tools/`, `icdev/tools/`, `args/`) finds **no** declared
Node Lambda runtime — only the measurement scaffolding in
`args/floci_runtime_images.yaml`. Neither `20` nor `24` is demanded by anything
in this tree, so there is no declared-configuration evidence for a move, and a
move would (a) vendor an image nothing requests and (b) drop the one variant
that was actually measured in the manifest. The bundle is left as it was.

## What changed instead

`args/pinned_artifacts.yaml`: `lambda-nodejs` gained `decided_by: consumer` /
`consumer: floci`, so the entry is decided on **digest drift** (has the tag
stopped serving the recorded bytes) rather than on upstream's release train.
`24` is still fetched and carried on `upstream_newest`; nothing is hidden. A
comment next to the pin in `vendor/images/images-floci-runtime.txt` states the
same, and `tests/airgap/test_artifact_freshness.py` pins the decision.

## Residual risk, stated rather than buried

* **Node 20 is past upstream end-of-life.** It is kept because nothing here
  requests a Node runtime, not because it is current. A human should revisit
  this if a Node Lambda is ever added to a generator or design.
* **The move, when a deployment needs it, is additive:** declare `nodejs24.x`,
  vendor `public.ecr.aws/lambda/nodejs:24@sha256:cf160411…` **alongside** `20`,
  add a `nodejs24.x` row to `args/floci_runtime_images.yaml` and its
  `variant_tokens.lambda` entry (today `nodejs24` resolves
  `variant_undetermined`), and re-measure. Vendoring 24 *instead of* 20 breaks
  any deployment still declaring `nodejs20.x`.
* This decision is mine, an AI session's; the PR review is the human decision
  the card asks for. Reject it there if 24 is wanted.
