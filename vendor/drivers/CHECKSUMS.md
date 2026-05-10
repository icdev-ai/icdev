# vendor/drivers — Binary Checksum Manifest

> **Classification:** CUI // SP-CTI
> **Last updated:** 2026-04-26

SHA-256 checksums for all vendored browser driver binaries.
Binaries are excluded from git (see .gitignore) but must be verified
against this manifest before use in air-gap / IL4+ environments.

Verify: ```python tools/airgap/driver_vendor.py --verify```

## Checksums

| Binary | Major | SHA-256 | Source |
|--------|-------|---------|--------|
| chromedriver.exe | 147 | `579a0c6a48e768e606daee39adc255d77198d11ebcb130f2bf570f11b34166f6` | Chrome for Testing |
