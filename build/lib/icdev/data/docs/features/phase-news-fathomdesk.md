# CUI // SP-CTI

# FathomDesk News Intelligence Pipeline

## Why /news

Before this feature, the FathomDesk scenario engine required manual input to trigger
what-if analyses. There was no automated pipeline feeding real-world events into
scenarios. Traders had to manually decide which scenario to run based on their own
news monitoring.

The /news page closes this gap: RSS feeds are polled automatically, classified by
category/impact/direction, matched to existing scenario templates, and aggregated
into clusters that detect regime-level market shifts.

## Design Decisions

### RSS-only (no paid APIs)
- Free, air-gap friendly, no API keys required
- 28 feeds across 6 groups: macro, financial, geopolitical, sector, corporate
- feedparser + stdlib html.parser (minimal deps)
- 10s per-feed timeout prevents hangs

### Reuse of scenario_impacts
- No new projection math; the existing 33 scenario templates provide all sector/ticker
  impact projections
- News items map to scenarios via deterministic keyword matching (+ optional LLM fallback)
- This means /news inherits all the existing scenario analysis capabilities

### Aggregation layer
- Clusters form by (category, scenario_key) grouping
- Cumulative score = sum of (impact_weight * source_reliability * time_decay * direction)
- Promotion thresholds: emerging (3) -> cluster (6) -> regime (10)
- Source diversity guard: reject promotion if >= 50% from single outlet
- Meta-scenarios fire when multiple scenario_keys co-occur in regime clusters

### INTaaS integration
- perspective_scorer.py now consumes news net_direction as a signal
- runner.py pulls live headlines from ad_news_items instead of mock data
- Active clusters inject pseudo-signals proportional to cumulative_score

## Architecture

```
args/news_feeds.yaml -> rss_ingestor.py -> ad_news_items
                                              |
                                        classifier.py -> category, impact, direction, tickers
                                              |
                                        scenario_matcher.py -> ad_news_scenario_links
                                              |
                                        aggregator.py -> ad_news_clusters
                                              |
                                        daemon reflexes (news_poller 15m, news_aggregator 5m)
                                              |
                                        /news dashboard page
```

## Files Created/Modified

### New files
- `tools/trading/news/__init__.py` - Package marker
- `tools/trading/news/db.py` - DDL + CRUD (3 tables, 6 indexes)
- `tools/trading/news/rss_ingestor.py` - RSS polling + HTML sanitization
- `tools/trading/news/classifier.py` - Rule-based classification
- `tools/trading/news/scenario_matcher.py` - News-to-scenario mapping
- `tools/trading/news/aggregator.py` - Clustering + promotion + meta-scenarios
- `tools/trading/news/meta_scenarios.yaml` - Meta-scenario definitions
- `tools/trading/dashboard/templates/news.html` - Dashboard page

### Modified files
- `tools/trading/analysts/news.py` - Extended keyword maps (geopolitical, monetary, fiscal, direction)
- `tools/trading/market_intel/daemon.py` - Added news_poller + news_aggregator reflexes
- `tools/trading/dashboard/app.py` - Added /news route + 4 API endpoints
- `tools/trading/dashboard/templates/base.html` - Added /news nav link
- `tools/trading/analysis/perspective_scorer.py` - News direction signals
- `tools/trading/runner.py` - Live news from DB instead of mock headlines
- `args/trading_daemon_config.yaml` - news_poller + news_aggregator config
- `args/llm_config_trading.yaml` - news_classify + news_scenario_match functions
- `.claude/hooks/pre_tool_use.py` - 3 append-only tables
- `tests/conftest.py` - DDL for 3 tables in MINIMAL_ICDEV_SCHEMA
- `docs/security/sandbox-coverage.md` - Gap 5 sandboxed-on-demand decision
- `requirements.txt` - feedparser>=6.0

## Database Tables (all append-only)
- `ad_news_items` - Raw RSS articles (888 items from 22 sources on first run)
- `ad_news_scenario_links` - News-to-scenario matches
- `ad_news_clusters` - Aggregated clusters with promotion status

## Metrics (first run)
- 888 items ingested from 22 active sources
- 888/888 classified (0 errors)
- Categories: macro (173), general (403), geopolitical (158), earnings (76), regulatory (35), sector (29), corporate (14)
- Impact: low (679), medium (146), high (63)
- 8 clusters created, 7 at regime level
