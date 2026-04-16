# AlphaDesk Trading Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AlphaDesk Trading Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fundamental Analyst | tools/trading/analysts/fundamental.py | Fundamental analysis agent (SMA, valuation, trends) | --json | Analysis results |
| Sentiment Analyst | tools/trading/analysts/sentiment.py | Keyword-based sentiment analysis agent | --json | Sentiment scores |
| News Analyst | tools/trading/analysts/news.py | News analysis agent that categorizes market news into earnings, macro, sector, regulatory, and corporate action keywords | --json | Categorized news signals |
| Quality Scorer | tools/trading/analysts/quality.py | 6-dimension fundamental quality scorer (valuation, profitability, growth, balance sheet, capital allocation, moat) with Piotroski F-Score composite | --json | Quality scores |
| Technical Analyst | tools/trading/analysts/technical.py | Pure-Python technical analysis agent: RSI, EMA, and other indicators without external TA-Lib dependency | --json | Technical signals |
| Market Data | tools/trading/data/market_data.py | Alpaca market data fetch and cache layer | --json | Market data |
| Perspective Scorer | tools/trading/analysis/perspective_scorer.py | ICDEV™'s INTaaS bull/bear multiperspectivity scorer | --json | Perspective scores |
| Signal Generator | tools/trading/analysis/signal_generator.py | Weighted composite signal generator | --json | Trading signals |
| Order Manager | tools/trading/execution/order_manager.py | Alpaca order placement and tracking | --json | Order status |
| Position Tracker | tools/trading/execution/position_tracker.py | Position synchronization with Alpaca | --json | Position data |
| Risk Checker | tools/trading/execution/risk_checker.py | Pre-trade risk validation (position limits, VaR) | --json | Risk assessment |
| Pulse Article Generator | tools/trading/pulse/article_generator.py | Pulse article generator from analysis results | --json | Article draft |
| Portfolio Strategist | tools/trading/strategist/portfolio_strategist.py | Autonomous long-term investment strategy agent — 4-tier allocation (core/tactical/opportunistic/hedge) from multi-timeframe performance, macro regime, KG centrality, scenario resilience, expert consensus | --run --json | Strategy allocation |
| Trading Runner | tools/trading/runner.py | Main orchestrator for AlphaDesk trading engine analysis cycles — full lifecycle: analyze → persist → queue signal → trigger Pulse article. Runs 5-layer DAG (macro, analysts, debate, signal, risk/approval) for a ticker. | --ticker SYM, --json | Full analysis result with run_id, signal, confidence, signal_id, article_id |
| Trading DB | tools/trading/db.py | AlphaDesk database layer — persistent storage for portfolios, positions, orders, signals, and analysis runs (ad_ prefix tables) | N/A (library) | DB connection/helpers |
| Workflow Builder | tools/trading/workflow.py | DAG workflow builder for AlphaDesk — constructs 5-layer analysis DAG (analysts → debate → signal → risk → approval) for a given ticker | ticker | Workflow dict |
| News DB | tools/trading/news/db.py | DDL + CRUD helpers for ad_news_items, ad_news_scenario_links, ad_news_clusters tables (all append-only). CLI: --migrate, --json | --migrate --json | Table list / item count |
| RSS Ingestor | tools/trading/news/rss_ingestor.py | Polls RSS/Atom feeds from args/news_feeds.yaml, deduplicates by sha256(source\|\|link)[:16], stores in ad_news_items. Per-feed exception isolation. HTML-strips summaries (OPT-58). | --once \| --start [--interval N] [--json] | Ingest summary JSON |
| News Classifier | tools/trading/news/classifier.py | Rule-based news classifier: category, impact_level, net_direction, mentioned_tickers from keyword maps in analysts/news.py | --backfill-all \| --id ID [--json] | Classification stats |
| Scenario Matcher | tools/trading/news/scenario_matcher.py | Maps news items to scenario_engine SCENARIO_TEMPLATES via keyword matching. Wires match_and_run() to scenario_engine.run_scenario() | --batch \| --id ID [--run] [--json] | Match results |
| News Aggregator | tools/trading/news/aggregator.py | Clusters news by (category, scenario_key), computes cumulative scores with time decay and source reliability, promotes through emerging/cluster/regime tiers | --cluster --promote [--window N] [--json] | Cluster stats |
| News Reasoner | tools/trading/news/news_reasoner.py | INTaaS multiperspective intelligence: author intent, omission detection, macro contradiction detection, cross-signal divergence. LLM-powered (qwen3.5) with deterministic fallback | --item ID \| --cluster CAT \| --divergences [--json] | Intelligence assessment |
| Trading Oracle | tools/trading/oracle/runner.py | Anticipatory intelligence engine — 4-lens prediction system (Signal Convergence, News Intelligence, Regime Trajectory, Portfolio Stress). Detects multi-lens convergence events, persists predictions with confidence + horizon, daemon reflex every 30m. | --sweep \| --predictions \| --convergence [--json] | Predictions, convergence events, recommended actions |
| Systemic Radar | tools/trading/market_intel/systemic_radar.py | Systemic Risk & Opportunity Radar (SROR) — aggregates 8 indicator families (market stress, macro leading, monetary liquidity, news sentiment, supply chain/geo, cross-asset divergences, institutional flow, historical patterns) across 3 time horizons (immediate/near-term/medium-term) into composite danger/opportunity scores with regime classification. 100% deterministic, no LLM. | --compute \| --latest \| --history \| --alerts [--json] | Composite scores, regime, family breakdown, alerts |
| Cross-Asset Divergence | tools/trading/market_intel/cross_asset_divergence.py | SROR Family 6 detector — flags 5 classic dislocation patterns: stocks-vs-bonds, gold+dollar both rising, oil crash vs equity rally, VIX-equity divergence, breadth vs index, credit vs equity. Deterministic. | --detect [--json] | Divergence list + aggregate scores |
| Crisis Fingerprints | tools/trading/market_intel/crisis_fingerprints.py | SROR Family 8 historical pattern matcher — counts 7 recession precursors, computes cosine similarity to 2008 GFC / 2020 COVID / 2022 rate shock fingerprints, extracts HMM transition probabilities to risk-off states. | --analyze [--json] | Precursor count, similarity scores, HMM risk probs |
| Regime Lens | tools/trading/market_intel/regime_lens.py | Single source of truth for SROR + news context. Returns normalized RegimeContext with helper multipliers (position_size_multiplier, short_margin_multiplier, buy_confidence_floor, hedge_tier_floor, opportunistic_tier_cap, threshold shifts). Consumed by runner.py, signal_generator.py, risk_checker.py, portfolio_strategist.py to make every layer regime-aware. | [--json] | RegimeContext dict |

