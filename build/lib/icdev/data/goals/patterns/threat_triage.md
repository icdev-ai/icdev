# Goal: Threat Report Triage
# CUI // SP-CTI

## Purpose
Automatically classify incoming threat intelligence reports by severity, map to MITRE ATT&CK techniques, route to the correct analyst queue, and generate a 1-page brief — enabling SOC teams to scale triage without additional headcount.

## Inputs
- `$REPORT_PATH` — path to a single report (PDF/TXT/JSON) or a directory
- `$QUEUE_CONFIG` — YAML file mapping severity → analyst queue (default: args/threat_queues.yaml)
- `$BRIEF_TEMPLATE` — Jinja2 template for 1-page brief (default: context/threat_brief.j2)
- `$CONFIDENCE_THRESHOLD` — minimum classification confidence (default: 0.75)

## Steps

### 1. Ingest and Parse Reports
```bash
python tools/geosigint/threat_ingestor.py \
  --source $REPORT_PATH \
  --json
```
**Expected output:** `{"reports_ingested": N, "table": "threat_reports"}`

### 2. Classify Severity + MITRE Mapping
```bash
python tools/geosigint/threat_classifier.py \
  --run-pending \
  --threshold $CONFIDENCE_THRESHOLD \
  --json
```
**Expected output:** `{"classified": N, "avg_confidence": F, "techniques_found": [...]}`

### 3. Route to Analyst Queues
```bash
python tools/geosigint/queue_router.py \
  --config $QUEUE_CONFIG \
  --json
```
**Expected output:** `{"routed": N, "queues_updated": [...]}`

### 4. Generate 1-Page Briefs
```bash
python tools/geosigint/brief_generator.py \
  --template $BRIEF_TEMPLATE \
  --output-dir .tmp/briefs/ \
  --json
```
**Expected output:** `{"briefs_generated": N, "output_dir": ".tmp/briefs/"}`

## Outputs
- Classified rows in `threat_reports` table (severity, techniques, confidence)
- Queue assignments in `threat_queue_assignments` table
- PDF/HTML briefs in `.tmp/briefs/`

## Acceptance Criteria
- Classification confidence ≥ 0.75 for ≥ 80% of reports
- Routing completes for all severity levels defined in `$QUEUE_CONFIG`
- Each brief is ≤ 1 page and includes: title, severity, techniques, summary, recommended action

## Error Handling
- Low confidence (< threshold): report flagged as `needs_review`, not auto-routed
- If MITRE mapping fails: log technique as `T-UNKNOWN`, do not block routing
