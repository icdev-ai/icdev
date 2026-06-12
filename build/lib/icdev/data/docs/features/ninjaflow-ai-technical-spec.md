# NinjaFlow AI — Technical Specification

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Product | NinjaFlow AI |
| Version | 0.1.0 (MVP) |
| Date | 2026-03-01 |
| Author | ICDEV™ Research + Innovation + Creative Engines |
| Status | DRAFT |
| Classification | CUI // SP-CTI |

---

## 1. Problem Statement

NinjaTrader 8 is the dominant futures trading platform for retail traders (60,000+ users), but it has:
- **Zero AI/ML integration** — locked to C#/.NET Framework 4.8
- **Zero MCP servers** — while Alpaca (529 stars), TradingView (406), MetaTrader (166) all have MCP integration
- **No compliance tooling** — retail traders using algorithmic strategies have zero CFTC/NFA compliance tools
- **Limited backtesting** — no walk-forward, no Monte Carlo, no regime-aware validation
- **No strategy health monitoring** — no alpha decay detection, no regime mismatch alerts

The Python AI/ML ecosystem (PyTorch, scikit-learn, XGBoost, 517 academic papers on AI trading) cannot be used with NinjaTrader without a bridge.

---

## 2. Product Architecture

### 2.1 System Overview

```
┌────────────────────────────────────────────────────────┐
│               NinjaTrader 8 Desktop                     │
│  ┌──────────────────────────────────────────────────┐  │
│  │    NinjaFlow Bridge (C# NinjaScript Addon)       │  │
│  │                                                   │  │
│  │  ┌─────────┐ ┌──────────┐ ┌────────────────────┐│  │
│  │  │ Data    │ │ Order    │ │ Strategy           ││  │
│  │  │ Export  │ │ Executor │ │ Deployer           ││  │
│  │  │         │ │          │ │                    ││  │
│  │  │• DOM    │ │• ATI     │ │• Load NinjaScript  ││  │
│  │  │• Volume │ │• Bracket │ │• Hot-reload        ││  │
│  │  │• Ticks  │ │• OCO     │ │• Param injection   ││  │
│  │  │• Bars   │ │• Limit   │ │                    ││  │
│  │  └────┬────┘ └─────┬────┘ └────────┬───────────┘│  │
│  └───────┼────────────┼───────────────┼────────────┘  │
└──────────┼────────────┼───────────────┼────────────────┘
           │ gRPC :50051│               │
┌──────────┼────────────┼───────────────┼────────────────┐
│  NinjaFlow Core (Python)                                │
│          │            │               │                 │
│  ┌───────▼────────────▼───────────────▼──────────────┐ │
│  │              gRPC Service Layer                    │ │
│  │  • MarketDataService (streaming)                   │ │
│  │  • OrderService (request/response)                 │ │
│  │  • StrategyService (deploy/undeploy)               │ │
│  │  • HealthService (heartbeat)                       │ │
│  └───────────────────┬───────────────────────────────┘ │
│                      │                                  │
│  ┌───────────────────▼───────────────────────────────┐ │
│  │              Agent Orchestrator                    │ │
│  │  Routes data to agents, manages lifecycle         │ │
│  └──┬──────┬──────┬──────┬──────┬──────┬────────────┘ │
│     │      │      │      │      │      │               │
│  ┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐          │
│  │Analy││Strat││Risk ││Exec ││Compl││Memo │          │
│  │st   ││egist││Mgr  ││utor ││iance││ry   │          │
│  └─────┘└─────┘└─────┘└─────┘└─────┘└─────┘          │
│                                                        │
│  ┌────────────────────────────────────────────────────┐│
│  │              MCP Server (:8600)                    ││
│  │  • Tools: market_data, place_order, backtest,     ││
│  │    regime_status, risk_check, compliance_report,   ││
│  │    strategy_health, trade_journal                  ││
│  └────────────────────────────────────────────────────┘│
│                                                        │
│  ┌────────────────────────────────────────────────────┐│
│  │              Web Dashboard (:5100)                 ││
│  │  Flask + SVG charts + SSE live updates             ││
│  └────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────┘
```

### 2.2 C#-Python Bridge (gRPC)

The bridge is the most critical component. NinjaTrader 8 runs on .NET Framework 4.8 (C#), while all AI/ML runs in Python.

**Protocol**: gRPC with Protocol Buffers
**Why gRPC over REST**: Low-latency bidirectional streaming for tick data (REST would add HTTP overhead per tick)

#### Proto Definitions

```protobuf
syntax = "proto3";
package ninjaflow;

// Market Data Service — streaming from NT8 to Python
service MarketDataService {
  // Stream real-time market data
  rpc StreamMarketData(MarketDataRequest) returns (stream MarketDataUpdate);
  // Stream order flow / DOM data
  rpc StreamOrderFlow(OrderFlowRequest) returns (stream OrderFlowUpdate);
  // Get historical bars
  rpc GetHistoricalBars(HistoricalBarsRequest) returns (HistoricalBarsResponse);
}

// Order Execution Service — Python sends orders to NT8
service OrderService {
  rpc PlaceOrder(OrderRequest) returns (OrderResponse);
  rpc ModifyOrder(ModifyOrderRequest) returns (OrderResponse);
  rpc CancelOrder(CancelOrderRequest) returns (OrderResponse);
  rpc GetPositions(PositionsRequest) returns (PositionsResponse);
  rpc GetAccountInfo(AccountInfoRequest) returns (AccountInfoResponse);
}

// Strategy Deployment — Python pushes generated NinjaScript to NT8
service StrategyService {
  rpc DeployStrategy(DeployStrategyRequest) returns (DeployStrategyResponse);
  rpc UndeployStrategy(UndeployStrategyRequest) returns (UndeployStrategyResponse);
  rpc ListStrategies(ListStrategiesRequest) returns (ListStrategiesResponse);
  rpc UpdateParameters(UpdateParamsRequest) returns (UpdateParamsResponse);
}

// Health check
service HealthService {
  rpc Check(HealthCheckRequest) returns (HealthCheckResponse);
  rpc Heartbeat(stream HeartbeatRequest) returns (stream HeartbeatResponse);
}

// === Messages ===

message MarketDataUpdate {
  string instrument = 1;
  double last_price = 2;
  double bid = 3;
  double ask = 4;
  int64 volume = 5;
  int64 timestamp_ns = 6;  // nanosecond precision
  BarData bar = 7;         // optional bar data
}

message OrderFlowUpdate {
  string instrument = 1;
  int64 timestamp_ns = 2;
  repeated DOMLevel bid_levels = 3;
  repeated DOMLevel ask_levels = 4;
  double delta = 5;           // buy volume - sell volume
  double cumulative_delta = 6;
  VolumeProfile volume_profile = 7;
}

message DOMLevel {
  double price = 1;
  int64 size = 2;
  int64 orders = 3;
}

message VolumeProfile {
  repeated PriceLevel levels = 1;
  double poc = 2;    // Point of Control
  double vah = 3;    // Value Area High
  double val = 4;    // Value Area Low
}

message OrderRequest {
  string instrument = 1;
  string action = 2;          // BUY, SELL
  string order_type = 3;      // MARKET, LIMIT, STOP, STOP_LIMIT
  int32 quantity = 4;
  double price = 5;           // for LIMIT/STOP_LIMIT
  double stop_price = 6;      // for STOP/STOP_LIMIT
  string tif = 7;             // GTC, DAY, IOC, FOK
  string strategy_id = 8;     // which strategy placed this
  string signal_id = 9;       // traceability to signal
  AutonomyLevel autonomy = 10;
}

enum AutonomyLevel {
  ADVISORY = 0;       // Signal only, human executes
  SEMI_AUTO = 1;      // AI proposes, human confirms
  FULL_AUTO = 2;      // AI executes, human monitors
}
```

### 2.3 Agent Architecture

| Agent | Responsibility | Input | Output | ML Models |
|-------|---------------|-------|--------|-----------|
| **Analyst** | Market intelligence | Order flow, news, sentiment | Market state, anomalies | LSTM, Transformer, XGBoost |
| **Strategist** | Strategy generation | Market state, regime | Signals, regime label | HMM, Random Forest |
| **Risk Manager** | Risk controls | Positions, P&L, drawdown | Position size, stop levels | Monte Carlo, VaR |
| **Executor** | Order management | Signals, risk params | Orders via gRPC | Slippage model |
| **Compliance** | Regulatory audit | All agent actions | Audit records, reports | Rule engine |
| **Memory** | State persistence | All events | Queryable history | Embedding search |

### 2.4 MCP Server Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_market_data` | Current price, bid/ask, volume | instrument, timeframe |
| `get_order_flow` | DOM, delta, cumulative delta, volume profile | instrument, depth |
| `place_order` | Submit order to NinjaTrader | instrument, action, type, qty, price |
| `get_positions` | Current open positions | account (optional) |
| `backtest_strategy` | Run backtest with validation | strategy_desc, period, validation_type |
| `get_regime` | Current market regime | instrument, timeframe |
| `get_risk_status` | Drawdown, exposure, limits | account (optional) |
| `get_strategy_health` | Alpha decay, regime fit, performance | strategy_id |
| `get_compliance_report` | Audit trail, regulatory status | period, format |
| `search_trade_journal` | Natural language search of past trades | query, date_range |
| `generate_strategy` | NL description to NinjaScript code | description, constraints |
| `monte_carlo` | Monte Carlo simulation on strategy | strategy_id, iterations |

---

## 3. Data Model

### 3.1 Core Tables (SQLite, append-only audit)

```sql
-- Trade journal (append-only)
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    instrument TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    order_type TEXT NOT NULL,
    strategy_id TEXT,
    signal_id TEXT,
    agent_id TEXT NOT NULL,
    autonomy_level TEXT CHECK(autonomy_level IN ('ADVISORY', 'SEMI_AUTO', 'FULL_AUTO')),
    slippage REAL,
    commission REAL,
    pnl REAL,
    regime TEXT,
    notes TEXT,
    audit_hash TEXT NOT NULL  -- SHA-256 chain for tamper detection
);

-- Strategy registry
CREATE TABLE strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    ninjascript_code TEXT,
    python_logic TEXT,
    parameters TEXT,  -- JSON
    created_at TEXT NOT NULL,
    status TEXT CHECK(status IN ('draft', 'backtesting', 'paper', 'live', 'paused', 'retired')),
    health_score REAL,
    alpha_decay_rate REAL,
    regime_fit TEXT,
    last_backtest TEXT,
    monte_carlo_p50 REAL,
    monte_carlo_p95 REAL
);

-- Market regime history
CREATE TABLE regimes (
    id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    regime TEXT NOT NULL CHECK(regime IN ('trending_up', 'trending_down', 'ranging', 'volatile', 'breakout')),
    confidence REAL,
    features TEXT,  -- JSON: volatility, trend strength, volume profile shape
    duration_bars INTEGER
);

-- Compliance audit trail (append-only, CFTC Reg AT)
CREATE TABLE compliance_audit (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,
    instrument TEXT,
    details TEXT,  -- JSON
    risk_level TEXT CHECK(risk_level IN ('low', 'medium', 'high', 'critical')),
    regulatory_mapping TEXT,  -- which regulation this satisfies
    hash TEXT NOT NULL,
    prev_hash TEXT  -- chain to previous record
);

-- Agent signals (append-only)
CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    instrument TEXT,
    direction TEXT CHECK(direction IN ('LONG', 'SHORT', 'FLAT', 'REDUCE')),
    confidence REAL,
    regime TEXT,
    features TEXT,  -- JSON: what drove this signal
    outcome TEXT,   -- filled post-trade: 'win', 'loss', 'scratch'
    pnl REAL
);

-- Backtest results
CREATE TABLE backtests (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    validation_type TEXT CHECK(validation_type IN ('in_sample', 'out_of_sample', 'walk_forward', 'monte_carlo')),
    total_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    sharpe_ratio REAL,
    max_drawdown REAL,
    monte_carlo_p50 REAL,
    monte_carlo_p95 REAL,
    overfitting_score REAL,  -- 0-1, higher = more overfit
    regime_breakdown TEXT,   -- JSON: performance per regime
    results_json TEXT
);

-- Risk snapshots (periodic)
CREATE TABLE risk_snapshots (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    account_value REAL,
    open_pnl REAL,
    closed_pnl REAL,
    max_drawdown REAL,
    current_drawdown REAL,
    daily_var REAL,       -- Value at Risk
    position_count INTEGER,
    exposure TEXT,        -- JSON: per-instrument exposure
    risk_limits TEXT,     -- JSON: current limits vs actual
    circuit_breaker_status TEXT CHECK(circuit_breaker_status IN ('normal', 'warning', 'triggered'))
);
```

---

## 4. Regulatory Compliance Mapping

| Regulation | Requirement | NinjaFlow Implementation |
|-----------|-------------|--------------------------|
| CFTC Reg AT §1.80 | Source code repository | Git + append-only audit trail |
| CFTC Reg AT §1.81 | Risk controls pre-trade | Risk Manager Agent circuit breakers |
| CFTC Reg AT §1.82 | Algo testing requirements | Walk-forward + Monte Carlo backtesting |
| CFTC Reg AT §1.83 | Reporting to DCO/DCM | Compliance Agent automated reports |
| NFA Rule 2-36(e)(12) | Cybersecurity program | ICDEV™ security scanning pipeline |
| NFA Rule 2-9(a) | Supervision of activities | Multi-agent audit trail |
| SEC Rule 15c3-5 | Market access risk controls | Pre-trade risk checks in Executor |
| FINRA Rule 3110 | Supervisory system | Compliance dashboard + alerts |
| MiFID II Art. 17 | Algo trading authorization | Strategy registration + testing |
| FCA MAR 5A.3 | Algo testing before deployment | Mandatory backtest validation gate |

---

## 5. MVP Phase Plan

### Phase 1: Foundation (Months 1-2)
- [ ] gRPC proto definitions and C# server in NinjaScript addon
- [ ] Python gRPC client + basic market data streaming
- [ ] MCP server with 4 basic tools (market_data, positions, order, health)
- [ ] SQLite database with trade journal and audit trail
- [ ] Basic Flask dashboard (connection status, live data)

### Phase 2: Intelligence (Months 3-4)
- [ ] Analyst Agent (order flow ML: delta divergence, volume anomalies)
- [ ] Strategist Agent (regime detection: HMM-based, 5 regimes)
- [ ] AI Backtester (walk-forward, Monte Carlo, overfitting score)
- [ ] Natural language strategy builder via MCP
- [ ] Dashboard: strategy health, backtest results, regime chart

### Phase 3: Compliance + Execution (Months 5-6)
- [ ] Risk Manager Agent (drawdown prediction, adaptive sizing)
- [ ] Executor Agent (smart routing, tiered autonomy)
- [ ] Compliance Agent (CFTC Reg AT audit, NFA cyber, risk reports)
- [ ] Memory Agent (trade journal search, strategy memory)
- [ ] Tiered UX (beginner/power mode)
- [ ] Open-core packaging (MIT core, premium license)

---

## 6. Competitive Differentiation

| Feature | NinjaFlow AI | Trade Ideas | TrendSpider | QuantConnect | freqtrade |
|---------|-------------|-------------|-------------|--------------|-----------|
| NinjaTrader integration | Native addon | No | No | No | No |
| MCP server | Yes (first) | No | No | No | No |
| Multi-agent AI | 6 agents | 1 (Holly) | 1 | No | No |
| Order flow AI | ML-powered | No | No | No | No |
| Walk-forward backtest | Yes | No | Yes | Yes | Basic |
| Monte Carlo | Yes | No | No | Yes | No |
| Regime detection | ML (HMM) | No | Basic | No | No |
| Compliance/audit | CFTC/NFA/SEC | No | No | No | No |
| Open source | MIT core | No | No | Partial (LEAN) | GPL (blocked) |
| Tiered autonomy | 3 levels | Advisory | Advisory | Full auto | Full auto |
| Air-gap safe | Yes (Ollama) | No | No | No | No |

---

## 7. Risk Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| NT8 .NET 9 migration breaks bridge | High | Medium | Abstraction layer, monitor NT forum, .NET Standard compat |
| gRPC latency for tick data | Medium | Low | Binary proto, local loopback, batch streaming |
| LLM hallucination in trade signals | Critical | Medium | Advisory-default, human confirm for auto, confidence thresholds |
| Regulatory changes (CFTC AI rules) | Medium | High | Compliance-first design, crosswalk engine auto-cascading |
| Open-source copycats | Low | Medium | First-mover MCP, compliance moat, community momentum |
| Data quality from NT8 | Medium | Low | Validation layer in gRPC service, data quality checks |

---

## 8. Technology Stack

| Component | Technology | Version | License | Air-Gap |
|-----------|-----------|---------|---------|---------|
| NT8 Bridge | C# / .NET Framework 4.8 | 4.8 | Microsoft | Yes |
| gRPC (C#) | Grpc.Core | 2.x | Apache-2.0 | Yes |
| gRPC (Python) | grpcio | 1.60+ | Apache-2.0 | Yes |
| AI/ML | PyTorch | 2.x | BSD | Yes |
| ML Classic | scikit-learn | 1.4+ | BSD | Yes |
| Gradient Boost | XGBoost | 2.x | Apache-2.0 | Yes |
| LLM (local) | Ollama | Latest | MIT | Yes |
| LLM (cloud) | AWS Bedrock | - | AWS | No |
| MCP SDK | mcp (Python) | 1.x | MIT | Yes |
| Web | Flask | 3.x | BSD | Yes |
| Database | SQLite | 3.x | Public domain | Yes |
| Exchange Data | ccxt | 4.x | MIT | Yes |
| Backtesting | lumibot | 4.x | MIT | Yes |
| Charts | SVG (custom) | - | - | Yes |
| IPC | Protocol Buffers | 3.x | BSD | Yes |

**All copyleft libraries excluded** (freqtrade GPL, backtrader GPL, OctoBot GPL) per ICDEV™ D202 license policy.

---

*CUI // SP-CTI*
