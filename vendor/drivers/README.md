# vendor/drivers

Vendored browser driver binaries for air-gap E2E testing.

Runtime code (`tools/browser/driver_manager.py`) resolves drivers from this
tree **without touching the network**. The fetch tool
(`tools/airgap/driver_vendor.py`) populates this tree and writes SHA256
checksums — it requires network access and is intended for admin / CI use only.

---

## Directory layout

```
vendor/drivers/
  msedgedriver/
    {major}/                  ← Edge major version number (e.g. 134)
      msedgedriver.exe        ← Windows binary
      msedgedriver            ← Linux / macOS binary
      SHA256SUM               ← hex digest of whichever binary is present
  chromedriver/
    {major}/                  ← Chrome major version number (e.g. 134)
      chromedriver.exe        ← Windows binary
      chromedriver            ← Linux / macOS binary
      SHA256SUM               ← hex digest of whichever binary is present
```

Only one platform binary is present per `{major}/` directory — the one that
was fetched on the host where `driver_vendor.py` ran. The SHA256SUM file sits
beside the binary it describes.

---

## SHA256 manifest file format

`SHA256SUM` follows the BSD `sha256sum` single-file format:

```
<lowercase-hex-digest>  <binary-filename>
```

Example:

```
3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b  msedgedriver.exe
```

The file is written automatically by `driver_vendor.py --fetch-edge` /
`--fetch-chrome` and verified by `--verify`. The runtime manager
(`driver_manager.py`) trusts the binary on disk without re-checking the hash;
run `--verify` explicitly before a release or after any manual update.

---

## Refresh workflow

### 1. Detect current Edge version and fetch matching driver

```bash
# Auto-detect installed Edge version and fetch
python tools/airgap/driver_vendor.py --fetch-edge

# Or pin a specific version
python tools/airgap/driver_vendor.py --fetch-edge --version 134.0.3124.57
```

### 2. Fetch chromedriver (optional fallback)

```bash
# Auto-detect installed Chrome version
python tools/airgap/driver_vendor.py --fetch-chrome

# Or specify major
python tools/airgap/driver_vendor.py --fetch-chrome --major 134
```

### 3. Verify all vendored drivers

```bash
python tools/airgap/driver_vendor.py --verify
```

Expected output:

```
  OK   msedgedriver major=134  vendor/drivers/msedgedriver/134/msedgedriver.exe
  OK   chromedriver major=134  vendor/drivers/chromedriver/134/chromedriver.exe

All 2 driver(s) verified OK
```

### 4. List what is currently vendored

```bash
python tools/airgap/driver_vendor.py --list --json
```

### 5. Probe driver resolution (no network, no browser launch)

```bash
python tools/browser/driver_manager.py --probe
```

### 6. Smoke-test (instantiate WebDriver and quit)

```bash
python tools/browser/driver_manager.py --smoke
```

---

## Edge version policy — N and N-1

Keep drivers for the **two most recent Edge major versions** (current N and
prior N-1). This ensures that developers on an older update channel can still
run E2E tests while N-1 support wind-down is in progress.

| Slot | Example | Retention |
|------|---------|-----------|
| N (current) | 134 | Keep indefinitely until N+1 ships |
| N-1 (prior) | 133 | Remove once the team is fully on N |
| N-2 and older | ≤ 132 | Delete from vendor tree |

When a new Edge major ships:

1. Fetch N: `python tools/airgap/driver_vendor.py --fetch-edge`
2. Delete the N-2 directory:
   ```bash
   # Example: removing major 132
   rm -rf vendor/drivers/msedgedriver/132
   ```
3. Run `--verify` to confirm the remaining two majors are intact.

---

## Security — check binary SHA before commit

> **WARNING: Driver binaries are executable code. Always verify the SHA256
> checksum before committing or distributing a driver binary.**

The SHA256SUM file next to each binary records the digest at fetch time. If
you manually copy, replace, or update a binary, regenerate and re-verify the
checksum:

```bash
# Re-generate SHA256SUM for a binary you replaced manually
python -c "
import hashlib, pathlib
p = pathlib.Path('vendor/drivers/msedgedriver/134/msedgedriver.exe')
digest = hashlib.sha256(p.read_bytes()).hexdigest()
(p.parent / 'SHA256SUM').write_text(f'{digest}  {p.name}\n')
print(digest)
"

# Then verify the whole tree
python tools/airgap/driver_vendor.py --verify
```

Cross-check the digest against the official Microsoft Edge WebDriver release
page or the Azure CDN URL used during the original fetch:

```
https://msedgewebdriverstorage.blob.core.windows.net/edgewebdriver/{version}/edgedriver_{platform}.zip
```

For chromedriver, cross-check against the
[Chrome for Testing JSON index](https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json).

Driver binaries (`.exe`, bare executables) are excluded from git via
`.gitignore`. Only `SHA256SUM` files and this `README.md` are tracked. Never
force-add a driver binary to git — distribute it through the fetch workflow
instead so every consumer gets a checksum-verified copy.

---

## Related tools

| Tool | Purpose |
|------|---------|
| `tools/airgap/driver_vendor.py` | Fetch + verify vendored drivers (requires network) |
| `tools/browser/driver_manager.py` | Runtime driver resolution + WebDriver factory (no network) |
