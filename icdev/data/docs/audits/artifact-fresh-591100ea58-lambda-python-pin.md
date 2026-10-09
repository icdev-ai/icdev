# CUI // SP-CTI — `lambda-python` moves `3.11` -> `3.12`, and the digest was RE-MEASURED (artifact-fresh-591100ea58)

**Decision: MOVE the tag `public.ecr.aws/lambda/python:3.11` to `3.12`.** This is
not a bare "upstream published a newer tag" bump — every Lambda runtime this
repository's own IaC generators declare is already `python3.12`, so the `3.11`
pin was vendoring a runtime nothing here actually requests. Measured 2026-09-18.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `public.ecr.aws/lambda/python:3.11`. Upstream publishes
> `3.12`. Decided on `version_tag`. Digest drift as well: the pinned tag no
> longer serves `sha256:755093e1…` — upstream now serves `sha256:84e549d0…`.

Re-derived at the start of this card: `python -m tools.airgap.artifact_freshness
--artifact lambda-python --json` reported `status: "current"` with
`digest_drift: true` and yet a THIRD observed digest
(`sha256:08f6f75068…`), different again from the one the card named. That is
the gap `args/pinned_artifacts.yaml`'s own commentary on this entry already
names: `lambda-python` is decided on `version_tag`, and the survey's
`tags/list` call returns an unordered, capped (1,000-tag) page of a repository
that carries many thousands of tags — direct manifest `HEAD` requests below
confirm bare `3.11`, `3.12`, `3.13` and `3.14` all resolve, none of them were in
that page, and `newest_comparable` therefore found nothing greater than the
pin and reported `current`. **That is a defect in the decision rule, not
evidence the pin is fine, and it already owns its own card** (named in
`args/pinned_artifacts.yaml`, line ~247). This card is scoped to the pin,
which is a real, independently-justified move regardless of what the survey
reports on any given run.

## Finding 1 — this repo's own declared configuration is `python3.12`, not `python3.11`

`args/floci_runtime_images.yaml`'s header states the rule this whole manifest
runs on: *"vendor the variants you declare."* Grepping the codebase for every
place a Lambda runtime is actually declared:

| file | declares |
|---|---|
| `tools/infra/dr_generator.py` (x2) | `runtime = "python3.12"` |
| `tools/aadc/iac_generator.py` (x2) | `runtime = "python3.12"` |
| `tools/data/iac_generator.py` | `runtime = "python3.12"` |
| `tools/infra_canvas/iac_generator.py` (x2) | `runtime = "python3.12"` (Terraform + CloudFormation) |
| `tools/ohc/iac_generator.py` | `runtime = "python3.12"` |
| `args/dr_config.yaml` (`lambda_verifier`) | `runtime: python3.12` |

Zero call sites declare `python3.11` — the only place that string appeared was
`tools/cloud/runtime_images.py`'s own module docstring, a usage EXAMPLE, not a
declaration. So the pin was already wrong for this deployment's own generated
infrastructure before upstream currency entered into it at all: an air-gapped
host vendored from the old pin would have every one of the Lambda functions
above pull `python3.12` at run time and fail, because `3.12` was never in the
bundle.

`args/floci_runtime_images.yaml`'s `variant_tokens.lambda` table — which is
what lets `tools/cloud/runtime_images.py` recognise a declared Lambda's
runtime and resolve it to an image — carried only `python3.11`, so a design
declaring `runtime: python3.12` (i.e. every generator above) resolved
`variant_undetermined`, not a mismatch: the tool could not even name the gap.
That table now carries `python3.12` in place of `python3.11`.

## Finding 2 — re-measured against a live floci, and the digest moved AGAIN under the fresh tag

Driven live: `docker compose --profile floci up -d floci` (floci 2.0.1, host
docker socket mounted), boto3 against `127.0.0.1:4566`.

| probe | observed |
|---|---|
| `create_function(Runtime="python3.12", …)` + `invoke()` | `ImageCacheService  Image already present locally, skipping pull: public.ecr.aws/lambda/python:3.12`; `invoke` returned `200` with the handler's payload |
| `docker image inspect public.ecr.aws/lambda/python:3.12` (before a fresh pull) | `sha256:f2c8de27…`, image `Created` 2026-09-17 — a day-old local cache |
| direct registry `HEAD` on `lambda/python:3.12` right now | `Docker-Content-Digest: sha256:5207fd64…` — **different** from the locally-cached image |
| `docker pull public.ecr.aws/lambda/python:3.12` then re-inspect | `sha256:5207fd64…`, matching the registry |
| inside the freshly-pulled image | `python3 --version` -> `Python 3.12.14` |

So `3.12`, like `3.11` before it and like `amazonlinux:2023` in the same file,
is a tag AWS rebuilds in place: it carries a real ordering (`3.11` < `3.12`)
AND is mutable at the digest level, which the ec2-amazonlinux card already
established are independent properties. The digest recorded in
`vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`
is the **freshly pulled** one (`sha256:5207fd64…`), not the day-old local
cache, and the row is now flagged `mutable_tag: true`.

Both files were moved together —
`tests/cloud/test_floci_runtime_images.py::test_declaration_is_enabled_and_measured`
and the sibling test asserting the pin file and the yaml agree still pass.

## What did NOT move, and why not

Upstream also publishes bare `3.13` and `3.14` tags (confirmed by direct
manifest `HEAD` — both resolve `200`). Neither is a release this tree can
justify moving to: nothing in the codebase declares either runtime, and
"upstream has something newer" is exactly the reductio the postgres/mysql/
valkey/opensearch/amazonlinux/registry cards in this same file already made —
vendoring a variant nothing pulls while leaving the one everything pulls
unvendored. `3.12` is the one release backed by an actual declared
configuration; `3.13`/`3.14` are named here rather than silently dropped, and
either needs its own card the day something in this repo declares that
runtime.

## Tests updated

`tests/cloud/test_floci_runtime_images.py` and `tests/cloud/test_floci_registry.py`
fixture data referenced the literal string `python3.11` /
`public.ecr.aws/lambda/python:3.11` (both as their example Lambda runtime and
in ref-parsing parametrizations). Updated to `python3.12` / `:3.12` throughout
so the fixtures track the manifest they exercise; the LOGIC under test
(variant-is-load-bearing, both directions of the deployment-blocker rule,
registry-host detection) is unchanged and unrelated to which Python minor
version is used as the example.

## Residual risk, stated rather than buried

* The drift will recur, on the same schedule the ec2-amazonlinux card
  predicted for its own rolling tag: AWS rebuilds `3.12` periodically, and
  this row will file `digest_drift` again — with the DECISION-RULE gap noted
  above possibly reporting it `current` regardless. That gap is tracked
  separately and this card does not close it.
* `image_vendor --save --topic floci-runtime` was **not** re-run: re-cutting
  the 1.91 GB bundle is a separate, GB-scale act. A bundle cut before
  2026-09-18 will now fail `--verify` on this one line, correctly — that is
  the pin doing its job, not a regression.
* The old `3.11` local image and the day-old `3.12` build are both still
  present in this host's cache, untagged/superseded. Nothing was pruned.
* `variant_tokens.lambda` no longer recognises `python3.11` as a token. A
  design that still declares that runtime will now resolve
  `variant_undetermined` rather than silently resolving to the `3.12` image —
  the honest answer, since this manifest no longer vendors `3.11` at all.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact lambda-python --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/cloud/test_floci_runtime_images.py tests/cloud/test_floci_registry.py tests/airgap/test_artifact_freshness.py tests/airgap/test_image_vendor.py -q
```

Driving floci needs the docker socket and port 4566. The probe function
(`artifact-fresh-591100ea58-probe-312`) was deleted, the `icdev-floci`
container and its compose network were torn down, and the background
`docker events` logger was stopped. One image was pulled — the `3.12` tag
being re-measured — and none was removed.
