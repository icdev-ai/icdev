# CUI // SP-CTI

# `vendor/images/` — pinned container-image bundles for the high side

Written by `tools/airgap/image_vendor.py` (flx-airgap-01). It is the
container-image sibling of `vendor/wheels/` (PyPI) and `vendor/drivers/`
(browser drivers).

```
vendor/images/
  images-<topic>.txt      the PINS        — tracked in git, this is the evidence
  <topic>/
    *.tar                 the BUNDLES     — gitignored, they travel on media
    SHA256SUM             tar hashes
    MANIFEST.json         pin -> tar, digest, layout, verification state
```

## What gets committed, and why

The **pin file is tracked** and the **tar bundles are not**. The pin is the
claim — "this bundle contains exactly this image" — and it is small, reviewable
and diffable. The tar is hundreds of megabytes of content that the pin already
identifies cryptographically, so committing it would add no evidence and a lot
of repository.

## A pin is a digest, never a tag

```
# vendor/images/images-floci.txt
floci/floci@sha256:<64 hex>
```

`floci/floci:2.0.1` is **refused**. A tag is mutable — the registry can move it
— so a bundle built from one is not reproducible and cannot be shown to contain
what was intended. Resolve a tag to its digest on the low side:

```bash
docker image inspect floci/floci:2.0.1 --format '{{index .RepoDigests 0}}'
```

## The source is the local image cache, and nothing pulls

`--save` reads what the local daemon already holds. If a pinned digest is
absent it **reports that and exits non-zero** rather than fetching it: a vendor
that pulls on demand cannot run on the disconnected side it exists to serve.
The refusal is structural — `image_vendor.ALLOWED_DOCKER_COMMANDS` does not
contain `pull`, and a test reads the module's AST to prove `subprocess` is
reached from nowhere but the one allowlisted door.

## Usage

```bash
# low side — the image must already be in the local cache
python tools/airgap/image_vendor.py --save --topic floci --json

# transport vendor/images/floci/ to the high side, then
python tools/airgap/image_vendor.py --verify --topic floci --json   # no docker needed
python tools/airgap/image_vendor.py --load   --topic floci --json
```

## What `--verify` proves

`docker save` writes an **OCI layout**: every blob under `blobs/sha256/` is
named by its own sha256, and `index.json` records the manifest digest that a
`repo@sha256:…` reference names. So verification re-hashes every blob against
its filename and compares `index.json`'s digest to the pin — a cryptographic
proof that the tar holds the pinned image, with **no daemon required**, which
matters because media is verified before there is anywhere to load it.

Measured 2026-09-05 (Docker 28.5.1): flipping a single byte in a real bundle is
caught twice over — by the recorded tar hash and, independently, by blob
content-addressing, which names the offending layer — and `--load` refuses the
bundle *before* importing it.

Statuses are three-valued and never merged: `verified` (checked, passed),
`failed` (checked, FAILED — a real finding), `unmeasured` (could not check — no
docker CLI, no bucket, or a legacy `docker-v1` tar that records no manifest
digest). **`unmeasured` is never a clean bundle**, and `--verify` exits 2 there
so a caller cannot read "could not measure" as "clean".

## No floci pin is committed yet

Deliberate. The floci digest has to be **measured** by resolving the real image,
and `floci/floci:2.0.1` was not in this host's cache when this tool was built
(verified 2026-09-05). Writing a plausible-looking digest here would be exactly
the fabrication the tool exists to prevent. Resolve it on a connected host with
the `docker image inspect` line above and commit the result.
