# CUI // SP-CTI — `lambda-python` stays on `3.12`, and the digest was RE-VENDORED (artifact-fresh-9daf559a8b)

**Decision: KEEP the tag `public.ecr.aws/lambda/python:3.12`. Do not move it to
`3.14`. Re-vendor the digest, which had moved under the tag.** Measured
2026-10-02. The survey was right that `3.14` exists and is reachable; it was
wrong that this tree should chase it.

## What the card said

`artifact_freshness` (xrv-pin-01) filed two findings:

> This tree pins `public.ecr.aws/lambda/python:3.12`. Upstream publishes `3.14`.
> Decided on `version_tag`. Digest drift as well: the pinned tag no longer serves
> `sha256:5207fd64…` — upstream now serves `sha256:7f72f9c0…`.

Re-derived at the start of this card, the same command reported
`status: "current"`, `newest: null`, `digest_drift: true` and a THIRD digest,
`sha256:7df298e6…`. Both differences have a cause:

* **`current` / `newest: null`** is the decision-rule gap
  `docs/audits/artifact-fresh-591100ea58-lambda-python-pin.md` already
  described. The entry was decided on `version_tag`, and the tag listing is a
  capped page (`tags_seen: 1000`) of a repository with many more tags. Whether
  bare `3.14` is on that page varies run to run: it was when the reflex filed
  this card, and it was not an hour later. The verdict for this entry was
  therefore not reproducible.
* **The third digest** is the tag moving again between the card being filed and
  being picked up. The image the tag serves now was created
  2026-10-02T15:47Z, after the card was scheduled (15:23Z).

## Finding 1 — the declared runtime is still `python3.12`

The search the previous card ran, repeated on this tree (`tools/`, `args/`,
`context/`, `goals/`):

| file | declares |
|---|---|
| `tools/infra/dr_generator.py` (x2) | `python3.12` |
| `tools/aadc/iac_generator.py` (x2) | `python3.12` |
| `tools/infra_canvas/iac_generator.py` (x2) | `python3.12` |
| `tools/data/iac_generator.py` | `python3.12` |
| `tools/ohc/iac_generator.py` | `python3.12` |
| `args/dr_config.yaml` | `python3.12` |

Nothing declares `python3.13` or `python3.14`. The remaining `python3.11` hits
are not Lambda runtimes: a `site-packages` path inside a Dockerfile string in
`tools/ttx/scenarios/sim_forge_ascent.py`, and a usage example in the
`tools/cloud/runtime_images.py` docstring.

## Finding 2 — the image is chosen by the declared `Runtime`, measured

Driven live: `floci/floci:2.0.1`, host docker socket mounted, memory storage,
boto3 against a throwaway probe emulator on `127.0.0.1:4598`.

| probe | observed |
|---|---|
| `docker pull public.ecr.aws/lambda/python:3.12`, then `docker image inspect` | `sha256:7df298e6d9a1ca16b3ed1b69040dc0acd2907a96c533f8e5256189ec82b2ac56`, matching a direct registry `HEAD` on the tag |
| `python3 --version` in the old pin (`sha256:5207fd64…`) | `Python 3.12.14` |
| `python3 --version` in the fresh pull | `Python 3.12.15` |
| `create_function(Runtime="python3.12")` + `invoke`, after the pull | 200, `sys.version` = `3.12.15 (main, Oct  1 2026 …)`. floci logged `ImageCacheService  Image already present locally, skipping pull: public.ecr.aws/lambda/python:3.12`. |
| `create_function(Runtime="python3.14")` + `invoke` | 200, `sys.version` = `3.14.8`. floci logged `Pulling image: public.ecr.aws/lambda/python:3.14`, then `Image pulled successfully`. |
| `create_function(Runtime="python3.99")` + `invoke` | `Lambda.InitError: The runtime parameter python3.99 is not supported.` No pull. |
| runtime strings in the floci binary (`grep -a -o` on `/app/application`) | `python3.4`, `3.6`–`3.15` |

So the ref is `public.ecr.aws/lambda/python:<minor of the declared Runtime>`,
validated against a set floci carries. This is the same shape as
`lambda-nodejs` (artifact-fresh-333eeead1d): `3.14` is reachable, but only by a
caller that declares it, and nothing here does. Moving the pin to `3.14` would
vendor an image nothing in this tree requests and drop the one every generated
Lambda pulls, so the first air-gapped invoke of a generated function would fail.

The digest half of the card was real and is acted on. `3.12` is rebuilt in
place; this rebuild carries a Python patch release (3.12.14 -> 3.12.15). An
air-gap bundle re-cut from the tag would not have matched the old pin.

## What changed

* `vendor/images/images-floci-runtime.txt` and `args/floci_runtime_images.yaml`:
  the `lambda/python3.12` digest moves `sha256:5207fd64…` -> `sha256:7df298e6…`.
  The tag is unchanged.
* `args/pinned_artifacts.yaml`: `lambda-python` gains `decided_by: consumer` /
  `consumer: floci`. It is now decided on **digest drift**, which is the signal
  that matters for a tag rebuilt in place, and the run-to-run `version_tag`
  verdict described above no longer applies to it. Upstream's newest comparable
  tag is still looked up and carried on `upstream_newest` whenever the listing
  page contains one.
* `icdev/data/args/floci_runtime_images.yaml`: the wheel mirror still held the
  `python:3.11` row and digest from before artifact-fresh-591100ea58. It is now
  a byte-exact copy of `args/floci_runtime_images.yaml`.
* `tests/airgap/test_artifact_freshness.py`: one test pins the decision.

After the change, `python -m tools.airgap.artifact_freshness --artifact
lambda-python --json` reports `status: "current"`, `basis: "digest"`,
`digest_drift: false`, `decided_by: "consumer"`.

## Residual risk

* **This card will come back.** AWS rebuilds `3.12` periodically, and each
  rebuild now reports `behind` on digest. The answer each time is a re-vendor
  (pull, inspect, re-drive floci), not a bump.
* **`3.14` also rebuilds.** A registry `HEAD` on `:3.14` returned
  `sha256:f8ec15d9…`; the pull floci made minutes later resolved
  `sha256:63a6235f…`. Neither is recorded anywhere as a pin, because nothing
  vendors `3.14`.
* **The move, when a deployment needs it, is additive:** declare `python3.14`,
  vendor `public.ecr.aws/lambda/python:3.14` alongside `3.12`, add a
  `python3.14` row to `args/floci_runtime_images.yaml` with its
  `variant_tokens.lambda` entry (today `python3.14` resolves
  `variant_undetermined`), and measure the digest on that day.
* **The `version_tag` rule itself is unchanged.** Any other entry decided that
  way against a repository with more than one page of tags can still report
  `current` on one run and `behind` on the next.
* `image_vendor --save --topic floci-runtime` was **not** re-run. A bundle cut
  before 2026-10-02 will fail `--verify` on this one line, which is the pin
  working.
* This decision was made by an AI session. The PR review is the human decision
  the card asks for; reject it there if `3.14` is wanted.

## Reproducing this

```bash
python -m tools.airgap.artifact_freshness --artifact lambda-python --json
python -m tools.cloud.runtime_images --measure-help
python -m pytest tests/cloud/test_floci_runtime_images.py tests/airgap/test_artifact_freshness.py -q
```

Driving floci needs the docker socket. The probe container
(`af-9daf559a8b-probe`) and its three probe functions were removed. Two images
were pulled: `python:3.12` (kept, it is the pin) and `python:3.14` (removed
again after the probe). The superseded `sha256:5207fd64…` image is still in
this host's cache, untagged. A pre-existing exited container named
`floci-probe-lambda` (2026-09-17) is not this card's and was left alone.
