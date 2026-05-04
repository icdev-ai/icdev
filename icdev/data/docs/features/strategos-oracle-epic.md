# CUI // SP-CTI
# Strategos Oracle Epic — Feature Documentation

**Epic ID:** sg-oracle-01..sg-oracle-vv  
**Status:** Complete  
**Committed:** 2026-04-27  
**Classification:** CUI // SP-CTI (IL4/IL5)

---

## Overview

The Strategic Intelligence Oracle (SIO) is a multi-lens analytical engine embedded in the Strategos module. It produces composite geopolitical threat assessments by running four independent analytical lenses in sequence and aggregating their outputs into a single `OracleAssessment`.

Dashboard page: `/strategos/oracle`  
API endpoint: `GET /api/strategos/oracle`  
MCP tool: `sio_run`

---

## SIO Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SIOEngine.run_all()                   │
│                                                         │
│  ┌───────────────┐  ┌─────────────────┐                │
│  │ threat_posture│  │ behavior_pattern│                │
│  │   weight=0.30 │  │   weight=0.25   │                │
│  └───────┬───────┘  └───────┬─────────┘                │
│          │                  │                            │
│  ┌───────────────┐  ┌─────────────────┐                │
│  │intent_assess. │  │   convergence   │                │
│  │   weight=0.25 │  │   weight=0.20   │                │
│  └───────┬───────┘  └───────┬─────────┘                │
│          └──────────────────┘                            │
│                      │                                   │
│              composite_score = Σ(score_i × weight_i)    │
│              OracleAssessment dataclass returned         │
└─────────────────────────────────────────────────────────┘
```

All lenses write to `sg_sio_assessments` (migration 050). Rows older than 24h are pruned after each lens run.

---

## Lens Formulas

### 1. Threat Posture (`lens_source=threat_posture`, weight=0.30)

Reads `sg_conflict_events` (last 30 days) and `sg_raw_signals` (last 7 days).

```
adversary_mobility_score  = f(event_count, movement_event_ratio) → [0,1]
force_concentration_score = f(event_count, attack_concentration) → [0,1]
offensive_indicator_count = count(cyber_op + kinetic_strike events) / event_count

threat_posture_score = (mobility×0.35 + concentration×0.35 + offensive×0.30) × 10
```

Confidence = `min(1.0, (signal_count + event_count) / 30)`

### 2. Behavior Pattern (`lens_source=behavior_pattern`, weight=0.25)

Reads `sg_conflict_events` (last 30 days) ordered by `event_ts`.

```
escalation_velocity  = count(level[t] > level[t-1]) / (n-1) × 3, clamped 0–1
pattern_repetition   = (max_type_freq/n - 0.1) / 0.9, clamped 0–1
deception_indicators = count(info_op|psyop|disinformation events) / n × 5, clamped 0–1

behavior_pattern_score = (vel×0.40 + rep×0.35 + dec×0.25) × 10
```

Confidence = `min(1.0, event_count / 20)`

### 3. Intent Assessment (`lens_source=intent_assessment`, weight=0.25)

Uses cosine similarity against 15 seeded PMESII-PT historical cases (Gulf War 1991 through Taiwan Strait Crisis 1996). The 7-dimensional PMESII-PT vector represents:

| Index | Dimension | Description |
|-------|-----------|-------------|
| 0 | Political | Regime stability, alliance posture |
| 1 | Military | Force readiness, mobilization |
| 2 | Economic | Sanctions, resource pressure |
| 3 | Social | Civil unrest, diaspora mobilization |
| 4 | Information | Information ops, media environment |
| 5 | Infrastructure | Logistics, critical infrastructure targeting |
| 6 | Physical Terrain | Geographic factors, terrain advantage |

```
cosine_similarity(u, v) = (u · v) / (‖u‖ × ‖v‖)

top_matches = top-3 cases by cosine_similarity
intent_assessment_score = max_similarity × outcome_severity_weight × 10, clamped 0–10
```

Outcome severity weights: escalation_level 1→0.2, 2→0.4, 3→0.6, 4→0.8, 5→1.0

Confidence = `max_similarity × 0.9`

### 4. Convergence (`lens_source=convergence`, weight=0.20)

Reads latest score per peer lens from `sg_sio_assessments`. Requires ≥2 peer lenses with recent assessments.

```
scores  = [threat_posture.score, behavior_pattern.score, intent_assessment.score]
std_dev = population std deviation of scores

convergence_score = 10 - (std_dev × 2), clamped 0–10
confidence        = (1 - std_dev/10) × (n_available_lenses / 3)
```

---

## STANAG 2511 NATO Reliability Mapping

All lenses emit a NATO STANAG 2511 source reliability code based on confidence:

| Code | Confidence Threshold | Meaning |
|------|---------------------|---------|
| A1 | ≥ 0.80 | Completely reliable; confirmed by other sources |
| B2 | ≥ 0.65 | Usually reliable; probably true |
| C3 | ≥ 0.50 | Fairly reliable; possibly true |
| D4 | ≥ 0.35 | Not usually reliable; doubtful |
| E5 | ≥ 0.00 | Unreliable; improbable |
| F6 | < 0.00 | Cannot be judged |

---

## I&W Convergence Trigger Thresholds

Indications & Warnings (I&W) status is triggered when both conditions are met simultaneously:

```
I&W_TRIGGERED = (convergence_score > 7.5) AND (composite_score > 6.0)
```

When triggered, the dashboard I&W traffic light turns red. The convergence lens includes this boolean in its result and the `OracleAssessment` dataclass propagates `iw_triggered=True` to all consumers.

The threshold was chosen to require both high cross-lens agreement (low std dev) AND an elevated aggregate threat level — preventing false positives from a single high-confidence lens spiking.

---

## Database Schema

| Table | Migration | Purpose |
|-------|-----------|---------|
| `sg_sio_assessments` | 050 | Per-run lens results (24h rolling) |
| `historical_cases` | 056 | 15 seeded PMESII-PT conflict precedents |

---

## Dashboard Page Components

| Component | Status |
|-----------|--------|
| Composite score gauge | ✅ Live via `/api/strategos/oracle` |
| I&W traffic light (green/amber/red) | ✅ |
| 5-lens radar chart (Chart.js) | ✅ |
| STANAG 2511 reliability badges | ✅ |
| 14-day convergence sparkline | ✅ |
| Lens narrative cards | ✅ |
| Auto-refresh (5 min) | ✅ `<meta http-equiv="refresh" content="300">` |

---

## Files Delivered

| File | Description |
|------|-------------|
| `intelligence/oracle/sio_engine.py` | SIO orchestrator + MCP handler `run_all_json` |
| `intelligence/oracle/lenses/threat_posture.py` | Threat Posture lens |
| `intelligence/oracle/lenses/behavior_pattern.py` | Behavior Pattern lens |
| `intelligence/oracle/lenses/intent_assessment.py` | Intent Assessment (PMESII-PT) lens |
| `intelligence/oracle/lenses/convergence.py` | Cross-Domain Convergence + I&W lens |
| `tools/dashboard/templates/strategos/oracle.html` | Dashboard page template |
| `tools/db/migrations/050_sg_sio_assessments/up.py` | sg_sio_assessments table |
| `tools/db/migrations/056_historical_cases/up.py` | historical_cases table |
| `apps/strategos/blueprint.py` | `/strategos/oracle` + `/api/strategos/oracle` routes |
| `tools/mcp/tool_registry.py` | `sio_run` MCP tool registered |
| `tools/manifest/strategos.md` | All 5 oracle tools documented |
| `docs/security/sandbox-coverage.md` | Gap 9 — oracle engine trust decision |
