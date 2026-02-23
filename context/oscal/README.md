# NIST OSCAL Content (Optional)

ICDEV can use the official NIST SP 800-53 Rev 5 catalog in OSCAL format for
full control coverage (1000+ controls vs 39 in the ICDEV custom catalog).

These files are **public domain** (NIST) but large (~14MB for the full catalog),
so they are not committed to the repository. Download them manually or via the
commands below.

## Setup

### Full Catalog (recommended)

```bash
curl -o context/oscal/NIST_SP-800-53_rev5_catalog.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json
```

### Baseline Profiles (optional — needed for profile resolution)

```bash
curl -o context/oscal/NIST_SP-800-53_rev5_LOW-baseline_profile.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_LOW-baseline_profile.json

curl -o context/oscal/NIST_SP-800-53_rev5_MODERATE-baseline_profile.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_MODERATE-baseline_profile.json

curl -o context/oscal/NIST_SP-800-53_rev5_HIGH-baseline_profile.json \
  https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_HIGH-baseline_profile.json
```

## Fallback Behavior

If these files are not present, ICDEV falls back to
`context/compliance/nist_800_53.json` (39 selected controls in ICDEV format).
All existing functionality continues to work — the official catalog adds
completeness, not correctness.

## Source

- Repository: https://github.com/usnistgov/oscal-content
- License: Public Domain (NIST)
- OSCAL Version: 1.1.2
