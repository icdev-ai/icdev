# Goal: Creative Engine — Customer-Centric Feature Opportunity Discovery

> CUI // SP-CTI

## Objective

Automate the process of scouting the internet for customer wishlists, pain points, and feature gaps that competitors don't address. Output ranked feature specs with justification. Supports daemon and on-demand CLI.

## Pipeline

```
DISCOVER → EXTRACT → SCORE → RANK → GENERATE
```

### Stage 1: DISCOVER
- Auto-discover competitors from G2/Capterra/TrustRadius category pages
- Scan review sites, community forums, GitHub issues for customer signals
- Store raw signals append-only with content_hash dedup
- **Tools:** `competitor_discoverer.py`, `source_scanner.py`

### Stage 2: EXTRACT
- Extract pain points from raw signals via deterministic keyword matching (D354)
- Sentiment detection (positive/negative/neutral/mixed)
- 15 categories: ux, performance, integration, pricing, compliance, security, reporting, customization, support, scalability, documentation, onboarding, api, automation, other
- Cluster signals with >= 3 shared keywords into pain points
- **Tool:** `pain_extractor.py`

### Stage 3: SCORE
- 3-dimension composite scoring (D355):
  - pain_frequency (0.40): normalized signal count
  - gap_uniqueness (0.35): 1.0 - (competitors_addressing / total_confirmed)
  - effort_to_impact (0.25): (frequency * severity_weight) / complexity
- Identify feature gaps from scored pain points
- **Tool:** `gap_scorer.py`

### Stage 4: RANK
- Deduplicate, cluster, rank by composite score
- Detect trends via keyword co-occurrence (velocity/acceleration)
- Lifecycle transitions: emerging → active → declining → stale
- **Tools:** `gap_scorer.py`, `trend_tracker.py`

### Stage 5: GENERATE
- Template-based feature specs (D356) — no LLM, reproducible
- Spec includes: problem statement, evidence, user quotes, competitive landscape, proposed feature, justification, score breakdown, persona, competitive advantage, effort, acceptance criteria
- Thresholds: >= 0.75 auto-spec, 0.50-0.74 suggest, < 0.50 log only
- Cross-register high-scoring signals to Innovation Engine (D360)
- **Tool:** `spec_generator.py`

## Source Adapters

| Source | Type | Adapter |
|--------|------|---------|
| G2 | Review site | `scan_g2()` |
| Capterra | Review site | `scan_capterra()` |
| TrustRadius | Review site | `scan_trustradius()` |
| Reddit | Forum | `scan_reddit()` |
| GitHub Issues | Issue tracker | `scan_github_issues()` |
| Product Hunt | Launch site | `scan_producthunt()` |
| GovCon Blogs | Industry blogs | `scan_govcon_blogs()` |

## Scoring

| Dimension | Weight | Formula |
|-----------|--------|---------|
| pain_frequency | 0.40 | normalized signal count |
| gap_uniqueness | 0.35 | 1.0 - (addressing / total_confirmed) |
| effort_to_impact | 0.25 | (frequency * severity) / complexity |

**Thresholds:**
- `>= 0.75` — auto-generate spec
- `0.50 - 0.74` — suggest for review
- `< 0.50` — log only

## Database Tables (6)

| Table | Append-Only | Notes |
|-------|-------------|-------|
| `creative_competitors` | No (UPDATE for status transitions) | discovered → confirmed → archived |
| `creative_signals` | Yes | Raw signals from all sources |
| `creative_pain_points` | Yes | Extracted + scored pain points |
| `creative_feature_gaps` | Yes | Identified feature gaps |
| `creative_specs` | Yes | Generated feature specifications |
| `creative_trends` | Yes | Trend clusters with velocity |

## Architecture Decisions

| ID | Decision |
|----|----------|
| D351 | Separate from Innovation Engine (different domain, scoring, sources) |
| D352 | Source adapters via function registry dict (web_scanner pattern) |
| D353 | Competitor auto-discovery is advisory-only (human must confirm) |
| D354 | Pain extraction is deterministic keyword/regex (air-gap safe) |
| D355 | 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25) |
| D356 | Feature specs are template-based (no LLM, reproducible) |
| D357 | All tables append-only except creative_competitors (UPDATE for status transitions) |
| D358 | Reuses _safe_get(), _get_db(), _now(), _audit() helpers |
| D359 | Daemon mode respects quiet hours from config |
| D360 | High-scoring signals cross-register to innovation_signals |

## CLI Commands

```bash
# Full pipeline
python tools/creative/creative_engine.py --run --json
python tools/creative/creative_engine.py --run --domain "proposal management" --json

# Individual stages
python tools/creative/creative_engine.py --discover --domain "proposal management" --json
python tools/creative/creative_engine.py --scan --all --json
python tools/creative/creative_engine.py --scan --source reddit --json
python tools/creative/creative_engine.py --extract --json
python tools/creative/creative_engine.py --score --json
python tools/creative/creative_engine.py --rank --top-k 20 --json
python tools/creative/creative_engine.py --generate --json

# Status and queries
python tools/creative/creative_engine.py --status --json
python tools/creative/creative_engine.py --pipeline-report --json
python tools/creative/creative_engine.py --competitors --json
python tools/creative/creative_engine.py --trends --json
python tools/creative/creative_engine.py --specs --json

# Sub-tools
python tools/creative/source_scanner.py --scan --all --json
python tools/creative/source_scanner.py --list-sources --json
python tools/creative/competitor_discoverer.py --discover --domain "proposal management" --json
python tools/creative/competitor_discoverer.py --list --json
python tools/creative/competitor_discoverer.py --confirm --competitor-id <id> --json
python tools/creative/pain_extractor.py --extract-all --json
python tools/creative/gap_scorer.py --score-all --json
python tools/creative/gap_scorer.py --top --limit 20 --json
python tools/creative/gap_scorer.py --gaps --json
python tools/creative/trend_tracker.py --detect --json
python tools/creative/trend_tracker.py --report --json
python tools/creative/spec_generator.py --generate-all --json
python tools/creative/spec_generator.py --list --json

# Daemon mode
python tools/creative/creative_engine.py --daemon --json
```

## Config

`args/creative_config.yaml` — All configuration:
- `domain` — product domain name and category URLs
- `sources` — per-source rate limits and endpoints
- `competitor_discovery` — refresh interval, max competitors
- `extraction` — keyword lists, categories, clustering params
- `scoring.weights` — 3-dimension weights
- `scoring.thresholds` — auto_spec, suggest
- `spec_generation` — template config
- `innovation_bridge` — cross-registration to Innovation Engine
- `scheduling` — daemon interval, quiet hours

## Edge Cases

1. **No competitors discovered** — Pipeline skips scan and reports empty results
2. **Air-gapped mode** — All web sources disabled, logs scan-skipped
3. **Duplicate signals** — Content hash dedup prevents re-insertion
4. **Quiet hours** — Spec generation skipped during configured quiet hours (D359)
5. **Innovation Engine tables missing** — Cross-registration silently skipped
6. **Sub-module import failure** — Graceful degradation via `_try_import()`
