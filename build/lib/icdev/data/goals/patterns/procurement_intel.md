# Goal: Procurement Intelligence Analyzer
# CUI // SP-CTI

## Purpose
Automatically monitor SAM.gov solicitations, score them against agency requirements, and surface high-fit opportunities on the FathomDesk BD canvas — no BD analyst coding required.

## Inputs
- `$AGENCY_NAICS_CODES` — comma-separated NAICS codes to filter (e.g., "541511,541512")
- `$KEYWORDS` — space-separated keywords (e.g., "cybersecurity cloud migration")
- `$SET_ASIDE_FILTERS` — set-aside types (e.g., "SDVOSB,8(a),SB")
- `$MIN_VALUE` — minimum award value in USD (default: 100000)
- `$NOTIFY_EMAIL` — optional email for high-score alerts

## Steps

### 1. Pull SAM.gov Solicitations
```bash
python tools/govcon/sam_scraper.py \
  --naics $AGENCY_NAICS_CODES \
  --keywords "$KEYWORDS" \
  --set-aside $SET_ASIDE_FILTERS \
  --min-value $MIN_VALUE \
  --json
```
**Expected output:** `{"opportunities": N, "saved_to": "govcon_opportunities"}`

### 2. Score Opportunities
```bash
python tools/govcon/opportunity_scorer.py \
  --run-all \
  --json
```
**Expected output:** `{"scored": N, "high_fit": K}`

### 3. Sync to BD Pipeline
```bash
python tools/govcon/pipeline_sync.py \
  --threshold 0.7 \
  --json
```

### 4. Send High-Fit Alerts (optional)
```bash
python tools/govcon/alert_sender.py \
  --min-score 0.8 \
  --email $NOTIFY_EMAIL \
  --json
```

## Outputs
- Scored opportunities in `govcon_opportunities` table
- High-fit cards in the BD Pipeline kanban
- Optional email digest for scores ≥ 0.8

## Acceptance Criteria
- At least 1 opportunity fetched per run (if solicitations exist for the NAICS codes)
- Scoring completes in < 60s for up to 100 opportunities
- Pipeline cards include title, deadline, score, and set-aside type

## Error Handling
- SAM.gov API rate limit: add `--delay 2` to sam_scraper.py
- If scoring fails with `LLMUnavailableError`, set `ICDEV_NO_LLM=true` for keyword-only scoring
