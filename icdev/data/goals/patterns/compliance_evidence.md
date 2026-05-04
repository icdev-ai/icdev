# Goal: Compliance Evidence Collector
# CUI // SP-CTI

## Purpose
Map system artifacts (screenshots, logs, config exports) to NIST 800-53 controls and auto-generate SSP evidence packages for assessors — cutting evidence collection from weeks to hours.

## Inputs
- `$ARTIFACT_DIR` — directory containing evidence artifacts (screenshots, logs, exports)
- `$CONTROL_BASELINE` — control baseline (e.g., "NIST_800-53_HIGH", "FedRAMP_MOD", "CMMC_L3")
- `$SYSTEM_NAME` — system name for SSP header (e.g., "ICDEV™ Platform")
- `$OUTPUT_DIR` — where to write the evidence package (default: .tmp/evidence-package/)
- `$ASSESSOR_FORMAT` — output format: "xlsx", "pdf", or "json" (default: "xlsx")

## Steps

### 1. Index Artifacts
```bash
python tools/compliance/artifact_indexer.py \
  --source $ARTIFACT_DIR \
  --json
```
**Expected output:** `{"artifacts": N, "types": {"screenshot": X, "log": Y, "config": Z}}`

### 2. Map Artifacts to Controls
```bash
python tools/compliance/control_mapper.py \
  --baseline $CONTROL_BASELINE \
  --json
```
**Expected output:** `{"controls_mapped": N, "coverage_pct": F, "gaps": [...]}`

### 3. Generate SSP Evidence Package
```bash
python tools/compliance/evidence_packager.py \
  --system "$SYSTEM_NAME" \
  --baseline $CONTROL_BASELINE \
  --format $ASSESSOR_FORMAT \
  --output $OUTPUT_DIR \
  --json
```
**Expected output:** `{"package_path": "...", "controls_covered": N, "total_controls": M}`

### 4. Validate Package Completeness
```bash
python tools/compliance/package_validator.py \
  --package $OUTPUT_DIR \
  --min-coverage 0.80 \
  --json
```
**Expected output:** `{"valid": true/false, "coverage": F, "missing_controls": [...]}`

### 5. Cross-walk to FedRAMP/CMMC (optional)
```bash
python tools/compliance/crosswalk_engine.py \
  --source NIST_800-53 \
  --targets FedRAMP CMMC \
  --json
```

## Outputs
- Evidence package at `$OUTPUT_DIR` in requested format
- Gap report listing controls with no mapped artifacts
- Optional cross-walk to FedRAMP Mod/High and CMMC L2/L3

## Acceptance Criteria
- ≥ 80% control coverage before package is considered "ready for assessor"
- Each mapped control has ≥ 1 artifact with filename, description, and capture date
- Package validates without errors at `package_validator.py`

## Error Handling
- Coverage < 80%: warn and list gap controls; do not block — assessor decides
- Artifact with no text/metadata: flag as `needs_annotation`, exclude from auto-map
- Cross-walk engine unavailable: skip cross-walk, log warning, continue
