# Phase 63 — Industry Research Engine

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 63 |
| Title | Industry Research Engine |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 35 (Innovation Engine), Phase 58 (Creative Engine) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-03-01 |

---

## 1. Problem Statement

ICDEV™ generates child applications for government and defense customers, but lacks systematic industry research capabilities to inform product strategy, competitive positioning, and regulatory awareness. Three gaps exist:

1. **No structured research pipeline** — Understanding a new vertical (healthcare, defense, fintech) requires manual research across scattered sources. There is no repeatable process for signal discovery, challenge extraction, or opportunity scoring.

2. **No video intelligence** — YouTube contains expert talks, conference presentations, and regulatory briefings that text-based sources miss. No mechanism exists to search, extract transcripts, or analyze video content.

3. **No forward-looking predictions** — Research produces retrospective analysis (what happened) but not predictive analysis (what will happen). Cross-engine correlation between Research, Innovation, and Creative signals is not performed.

Phase 63 addresses these gaps with a 9-stage research pipeline, YouTube as a 9th data stream, a FORECAST stage for predictive analysis, and cross-engine surprise recommendations.

---

## 2. Goals

- Build a vertical-specific research engine with configurable industry verticals
- Implement a 9-stage pipeline: SCOPE → SCAN → EXTRACT → CLUSTER → SCORE → MAP → SYNTHESIZE → FORECAST → DOSSIER
- Support 9 data source types: academic, news, regulatory, social, patent, code, market, community, video
- Add YouTube as an optional 9th data stream with transcript extraction and two-tier LLM processing
- Generate forward-looking predictions with confidence and surprise scoring
- Cross-correlate signals from Research, Innovation, and Creative engines
- Produce comprehensive dossiers with opportunity scores and actionable recommendations
- Provide a dashboard UI for session management and dossier viewing

---

## 3. Architecture

### 3.1 Pipeline Stages

| Stage | Purpose | Tool |
|-------|---------|------|
| SCOPE | Define research boundaries, load vertical config | `session_manager.py` |
| SCAN | Discover signals from 9 source types | `source_scanner.py` |
| EXTRACT | Extract challenges, pain points, opportunities | `challenge_scorer.py` |
| CLUSTER | Group related challenges by keyword fingerprint | `challenge_scorer.py` |
| SCORE | Score challenges (frequency × severity × opportunity) | `challenge_scorer.py` |
| MAP | Map to regulatory bodies and ICDEV™ capabilities | `regulatory_mapper.py`, `capability_mapper.py` |
| SYNTHESIZE | Build/buy analysis, trend detection | `build_buy_analyzer.py`, `trend_detector.py` |
| FORECAST | Generate predictions with surprise scoring | `forecast_generator.py` |
| DOSSIER | Compile final research dossier | `dossier_generator.py` |

### 3.2 Source Types

| Source | Type | Adapter |
|--------|------|---------|
| Academic papers | `academic` | ArXiv, PubMed, Google Scholar |
| News articles | `news` | RSS feeds, news APIs |
| Regulatory filings | `regulatory` | Government registries |
| Social discussions | `social` | Reddit, X/Twitter |
| Patent filings | `patent` | USPTO, Google Patents |
| Code repositories | `code` | GitHub, GitLab |
| Market reports | `market` | Industry reports |
| Community forums | `community` | Stack Overflow, specialized forums |
| Video content | `video` | YouTube (search, manual URL, channel) |

### 3.3 Industry Verticals

| Vertical | Config | Key Regulatory Bodies |
|----------|--------|----------------------|
| Trading | `context/research/verticals/trading.json` | CFTC, NFA, SEC, FINRA, MiFID II, FCA |
| Healthcare | `context/research/verticals/healthcare.json` | HHS OCR, FDA, CMS, ONC |
| Defense | `context/research/verticals/defense.json` | DoD CIO, DISA, NSA, NIST |
| Fintech | `context/research/verticals/fintech.json` | OCC, FDIC, Fed Reserve, CFPB, SEC |
| Cybersecurity | `context/research/verticals/cybersecurity.json` | CISA, NIST, NSA, FTC |
| Logistics | `context/research/verticals/logistics.json` | FMCSA, DOT, CBP, FDA Food |

### 3.4 YouTube Scanner

YouTube integration provides three discovery modes:

- **YouTube Search** — Uses YouTube Data API v3 (requires `YOUTUBE_API_KEY`). Searches for vertical-specific queries, returns video metadata and optional transcripts.
- **Manual URL** — Accepts user-provided video URLs. Uses oEmbed for metadata (no API key needed). Extracts transcripts via `youtube-transcript-api`.
- **Channel Scan** — Uses YouTube Data API v3 to scan specific channels for recent uploads.

Transcripts are processed via two-tier LLM: qwen3 summarizes transcript, Claude extracts key signals. Metadata-only fallback when transcripts are unavailable.

### 3.5 FORECAST Stage

The FORECAST stage generates forward-looking predictions by cross-correlating data from three engines:

- **Research Engine** — trends, challenges, signals
- **Innovation Engine** — innovation trends, innovation signals (if tables exist)
- **Creative Engine** — pain points, feature gaps (if tables exist)

Each prediction includes:
- **Confidence** (0-1) — How likely is this prediction?
- **Surprise Score** (0-1) — How non-obvious is this prediction?
- **Composite Rank** — confidence x surprise_score
- **Time Horizon** — 3 months, 6 months, 1 year, 3 years
- **Prediction Type** — trend_trajectory, greenfield, convergence, disruption, regulatory_shift

Top 5 predictions ranked by composite rank are included in the dossier.

---

## 4. Database Schema

### New Tables (10)

| Table | Purpose |
|-------|---------|
| `research_sessions` | Research session tracking with pipeline state |
| `research_verticals` | Industry vertical definitions |
| `research_signals` | Discovered signals from all 9 source types |
| `research_challenges` | Extracted and scored challenges |
| `research_regulatory_map` | Challenge-to-regulatory-body mappings |
| `research_capability_map` | Challenge-to-ICDEV™-capability mappings |
| `research_build_buy` | Build vs. buy analysis per challenge |
| `research_dossiers` | Generated research dossiers |
| `research_trends` | Detected industry trends |
| `research_forecasts` | Forward-looking predictions (append-only) |

### Key CHECK Constraints

- `research_signals.source`: academic, news, regulatory, social, patent, code, market, community, video
- `research_signals.source_type`: arxiv, pubmed, scholar, rss, registry, reddit, twitter, patent_search, github, market_report, stackoverflow, youtube_search, youtube_manual, youtube_channel
- `research_sessions.pipeline_stage`: SCOPE, SCAN, EXTRACT, CLUSTER, SCORE, MAP, SYNTHESIZE, FORECAST, DOSSIER
- `research_forecasts.prediction_type`: trend_trajectory, greenfield, convergence, disruption, regulatory_shift

---

## 5. Configuration

### args/research_config.yaml

Controls source scanning behavior, rate limits, transcript settings, and forecast parameters.

Key sections:
- `sources` — Per-source-type configuration (enabled, scan_interval, platforms, rate_limit)
- `video` — YouTube-specific config (API key requirement, transcript settings, LLM summarization)
- `forecast` — FORECAST stage config (enabled, method, cross_engine, max_predictions, thresholds)
- `scoring` — Challenge scoring weights (frequency, severity, opportunity)
- `dossier` — Dossier generation settings (sections, scoring thresholds)

### args/llm_config.yaml

Two new worker functions added to two-tier routing:
- `research_forecast` — qwen3 compact draft, Claude review for predictions
- `research_youtube_summarize` — qwen3 transcript summarization

---

## 6. CLI Commands

```bash
# Full pipeline
python tools/research/research_engine.py --run --vertical cybersecurity --json

# Individual stages
python tools/research/research_engine.py --run-stage SCOPE --session-id "rsess-xxx" --json

# Session management
python tools/research/session_manager.py --create --vertical cybersecurity --name "Q3 Research" --json
python tools/research/session_manager.py --list --json

# Vertical loading
python tools/research/vertical_loader.py --load --json
python tools/research/vertical_loader.py --list --json

# Source scanning
python tools/research/source_scanner.py --scan --session-id "rsess-xxx" --json
python tools/research/source_scanner.py --list-sources --json

# Challenge scoring
python tools/research/challenge_scorer.py --cluster --session-id "rsess-xxx" --json
python tools/research/challenge_scorer.py --score --session-id "rsess-xxx" --json

# Mapping
python tools/research/regulatory_mapper.py --map --session-id "rsess-xxx" --json
python tools/research/capability_mapper.py --map --session-id "rsess-xxx" --json

# Build/buy analysis
python tools/research/build_buy_analyzer.py --analyze --session-id "rsess-xxx" --json

# Trend detection
python tools/research/trend_detector.py --detect --json

# YouTube scanning
python tools/research/youtube_scanner.py --scan --queries "zero trust 2026" --json
python tools/research/youtube_scanner.py --scan --urls "https://youtube.com/watch?v=xxx" --json
python tools/research/youtube_scanner.py --scan --channels "UCxxx" --json

# FORECAST generation
python tools/research/forecast_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/forecast_generator.py --get --session-id "rsess-xxx" --json

# Dossier generation
python tools/research/dossier_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/dossier_generator.py --list --json

# Status and daemon
python tools/research/research_engine.py --status --json
python tools/research/research_engine.py --daemon --json
```

---

## 7. Dashboard

The research dashboard is accessible at `/research` and provides:

- **Stat Grid** — Total Sessions, Active Sessions, Verticals Loaded, Dossiers Generated
- **Session Creation Form** — Name, vertical dropdown, focus areas textarea, Start Research button
- **Sessions Table** — Name, vertical, pipeline status badges, signals count, challenges count, opportunity score, actions (Run, View Dossier, Regs)
- **Regulatory Landscape Panel** — Per-session regulatory view: stat cards (Regulations Mapped, Enforcement Actions, Avg Crosswalk Coverage, Regulatory Bodies), per-body detail table with coverage progress bars and upcoming deadline badges. Triggered via "Regs" button in session row.
- **Dossier Viewer** — Inline viewer with section navigation sidebar (20+ sections) and rendered markdown content with CUI markings. Sections include Executive Summary, Vertical Overview, Challenge Analysis, Regulatory Landscape, Competitive Landscape, Build/Buy/Partner Analysis, Predictive Analysis & Surprise Recommendations, and more.

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/research/sessions` | GET | List all sessions |
| `/api/research/sessions` | POST | Create new session |
| `/api/research/sessions/<id>/run` | POST | Run full pipeline |
| `/api/research/sessions/<id>/run-stage` | POST | Run specific pipeline stage |
| `/api/research/sessions/<id>/status` | GET | Get session status |
| `/api/research/sessions/<id>/dossier` | GET | Get dossier by session |
| `/api/research/sessions/<id>/retry` | POST | Retry errored pipeline |
| `/api/research/sessions/<id>/regulatory` | GET | Get regulatory landscape data |
| `/api/research/dossiers/<id>` | GET | Get dossier by dossier ID |
| `/api/research/dossiers/<id>/review` | POST | Review dossier (HITL) |
| `/api/research/verticals` | GET | List available verticals |
| `/api/research/verticals/load` | POST | Load verticals from config |

Stats are rendered server-side via Jinja2 in the `/research` page route (not a separate API).

Navigation: "Research" link added to main dashboard nav bar.

---

## 8. MCP Integration

New MCP tools registered in `tools/mcp/research_server.py` and `tools/mcp/tool_registry.py`:

| Tool | Purpose |
|------|---------|
| `research_create_session` | Create a new research session |
| `research_list_sessions` | List research sessions |
| `research_run_pipeline` | Run full pipeline for a session |
| `research_run_stage` | Run a specific pipeline stage |
| `research_get_status` | Get session status |
| `research_list_verticals` | List available verticals |
| `research_load_verticals` | Load verticals from config |
| `research_get_dossier` | Get a generated dossier |
| `research_get_forecasts` | Get forecasts for a session |

---

## 9. Architecture Decisions

| ADR | Decision |
|-----|----------|
| D-RES-1 | 9-stage pipeline: SCOPE through DOSSIER. Each stage is idempotent and restartable |
| D-RES-2 | Vertical configs are declarative JSON — add new verticals without code changes |
| D-RES-3 | Source adapters via function registry dict (reuses D352 Creative Engine pattern) |
| D-RES-4 | Challenge scoring uses deterministic 3-dimension weighted average (D21 pattern) |
| D-RES-5 | All research tables append-only except research_sessions (status updates) |
| D-RES-6 | Regulatory body mapping uses `context/research/regulatory_registry.json` with crosswalk hooks |
| D-RES-7 | Capability mapping is advisory-only — suggests ICDEV™ capabilities that address challenges |
| D-RES-8 | Build/buy analysis uses deterministic scoring (complexity, time-to-market, compliance-alignment) |
| D-RES-9 | Trend detection reuses Innovation Engine pattern (keyword co-occurrence, velocity tracking) |
| D-RES-10 | Dossier is a structured JSON document with optional markdown rendering |
| D-RES-11 | Dashboard page follows existing patterns (stat-grid, table-container, charts.js) |
| D-RES-12 | MCP server follows unified gateway pattern (D301) |
| D-RES-13 | Research Engine is a SaaS module (Phase 21 tenant isolation applies) |
| D-RES-14 | YouTube is a 9th source stream with 3 source_types: youtube_search, youtube_manual, youtube_channel |
| D-RES-15 | YouTube Data API v3 search requires YOUTUBE_API_KEY env var. Degrades gracefully without key |
| D-RES-16 | YouTube transcripts processed via two-tier LLM: qwen3 summarizes, Claude extracts signals |
| D-RES-17 | FORECAST is the 9th pipeline stage between SYNTHESIZE and DOSSIER |
| D-RES-18 | Cross-engine aggregation queries innovation and creative tables automatically |
| D-RES-19 | Each prediction has confidence, surprise_score, time_horizon, composite_rank |
| D-RES-20 | research_forecasts table is append-only with outcome field for accuracy tracking |
| D-RES-21 | Dossier gains "Predictive Analysis & Surprise Recommendations" section |

---

## 10. Testing

68 tests covering the full research engine:

```bash
pytest tests/test_research_engine.py -v    # All 68 tests
```

Test categories:
- Session management (create, list, status)
- Vertical loading (load, list, validate)
- Source scanning (9 source types including video)
- Challenge extraction and scoring
- Regulatory and capability mapping (19 tests — mapper invocation, body lookup, crosswalk coverage, enforcement actions, upcoming deadlines, cache TTL, dashboard stats aggregation)
- Build/buy analysis
- Trend detection
- YouTube scanner (API search, manual URL, channel, transcript extraction)
- FORECAST generator (cross-engine aggregation, prediction scoring, surprise ranking)
- Dossier generation
- Pipeline orchestration (stage transitions, error recovery)
- Dashboard API endpoints (including `/run-stage`, `/regulatory`, `/retry`)

---

## 11. Phase B — Dashboard Bug Fixes

Six bugs were discovered and fixed during Playwright E2E verification:

| Bug | File | Issue | Fix |
|-----|------|-------|-----|
| 1 | `app.py` | Missing `GET /api/research/sessions/<id>/dossier` endpoint | Added route calling `dossier_generator.get_dossier()` |
| 2 | `app.py` | Missing `POST /api/research/sessions/<id>/retry` endpoint | Added route calling `research_engine.run_pipeline()` in background thread |
| 3 | `research.html` | Status mismatch — template used `scanning/extracting/scoring/complete` instead of `scoping/scanning/synthesizing/dossier_ready` | Fixed all Jinja2 + JS status references |
| 4 | `app.py` | `review_dossier` route passed `decision=`/`notes=` instead of `status=`/`review_notes=` | Fixed parameter names |
| 5 | `research.html` | `'%.2f'\|format(s.score)` causes Jinja2 TypeError | Replaced with `s.score\|round(2)` |
| 6 | `research.html` | Dossier viewer JS expected `data.sections`/`data.rendered_markdown` but API returns `data.content` | Added `##` header parsing from `data.content` for section nav; fall back to `data.content` for rendering |

Additionally, cross-engine column mismatches were fixed in `forecast_generator.py` and `trend_detector.py` to match actual Innovation Engine and Creative Engine table schemas.

**Trading Vertical Pipeline Run**: Session `rsess-a48f9c31b16f` produced dossier `rdoss-bee79a9a62fa` — 1,179 signals, 812 challenges, 157 regulatory mappings, overall opportunity score 0.57.

---

## 12. Phase C — Stage 3 Regulatory Enhancements

Stage 3 (REGULATE) was extended with dashboard visualization and API endpoints:

| Enhancement | File | Description |
|-------------|------|-------------|
| 1 | `app.py` | Added `POST /api/research/sessions/<id>/run-stage` endpoint for running individual pipeline stages |
| 2 | `app.py` | Added `GET /api/research/sessions/<id>/regulatory` endpoint returning regulatory landscape data with per-body stats, crosswalk coverage, enforcement actions, and upcoming deadlines |
| 3 | `research.html` | Added "Regs" button to session table rows for sessions past SCOPE stage |
| 4 | `research.html` | Added regulatory landscape panel with stat cards and per-body detail table |
| 5 | `source_scanner.py` | Added 7-day cache TTL for regulatory scans (reuses cached signals within window) |
| 6 | `test_research_engine.py` | Added 19 regulatory mapper tests (49→68 total) |

**E2E Verification**: Full 57-step E2E test spec executed — all steps passed. Screenshots at 3 viewports (desktop 1440x900, tablet 768x1024, mobile 375x812). Only non-research error: `favicon.ico` 404.

---

## 13. Security Considerations

- All research data inherits CUI // SP-CTI classification markings
- YouTube API key stored in environment variable, never in code or config files
- Transcript extraction uses `youtube-transcript-api` (no API key needed for public videos)
- Rate limiting enforced on all external API calls
- Content hashing for signal deduplication (prevents double-counting)
- Append-only tables for audit compliance (NIST AU-2)
- research_forecasts table added to APPEND_ONLY_TABLES in hooks

**CUI // SP-CTI**
