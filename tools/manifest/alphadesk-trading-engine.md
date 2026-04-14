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


