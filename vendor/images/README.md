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

## The floci pin, and where its digest came from

`images-floci.txt` pins
`floci/floci@sha256:4e451c39c7bb88e3cd4f87e8fc0c25d5b47695a51185d521e2241fa00486e8eb`.

**That digest is not this card's measurement.** It was measured against Docker
Hub on 2026-09-05 by `flx-ci-01` and is declared in
`args/floci_iac_gate.yaml::image_digest` (`floci/floci:2.0.1`, published
2026-09-01). It is repeated here because `--save` needs a pin file, and two
spellings of one measured fact can drift — so
`tests/airgap/test_image_vendor.py::test_the_floci_pin_agrees_with_the_measured_digest`
asserts the two files still agree. A bundle vendored against a digest the IaC
gate no longer recognises is exactly the unattributable disagreement that
config's own comment warns about.

**The digest is independently corroborated.** `flx-ci-01` measured it against
Docker Hub; this tool re-derived it from the OCI layout of the locally cached
image, and the two agree. Two derivations that share no code arriving at the
same digest is worth more than either alone.

**Measured on this host 2026-09-05** — a real floci bundle, full round trip:

| act | result |
|---|---|
| `--save --topic floci` | `verified` — 139,021,824 bytes, 10 blobs, `manifest_digest_verified: true` |
| `--verify --topic floci` | `verified`, `in_local_daemon: true` |
| `--verify --no-daemon-probe` | `verified`, `in_local_daemon: null` — the high-side case, proved with no daemon |
| `--load --topic floci` | `verified`, `digest_verified_in_daemon: true` |

Note the cache is a moving target: when this tool was first built earlier the
same day the image was **absent**, and `--save` correctly reported
`absent_from_local_cache` and exited non-zero rather than pulling. Both the
refusal and the success are the designed behaviour.
