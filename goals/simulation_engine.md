# Goal: Digital Program Twin Simulation (RICOAS Phase 3)

## Purpose
Run 6-dimension what-if simulations to predict impact of requirements on architecture, compliance, supply chain, schedule, cost, and risk. Generate and compare COAs. Use Monte Carlo for probabilistic schedule/cost estimation.

## When to Use
- Before committing to a set of requirements, simulate their impact
- Compare multiple COAs side-by-side across all dimensions
- Need Monte Carlo estimation for schedule or cost confidence levels
- Scenario planning — "what if we add/remove these requirements?"
- RED-tier requirements need alternative COA generation

## Workflow

### What-If Simulation
1. Create scenario: `create_scenario` with modifications (add/remove requirements, change architecture)
2. Run simulation: `run_simulation` — computes baseline vs simulated across 6 dimensions
3. Review results: architecture impact, compliance delta, supply chain changes, schedule estimate, cost projection, risk score
4. Fork scenario for variations: `manage_scenarios` action=fork

### Monte Carlo Estimation
1. Run Monte Carlo: `run_monte_carlo` with dimension (schedule/cost/risk) and iterations
2. Review percentiles: P10 (optimistic), P50 (likely), P80 (management reserve), P90 (conservative)
3. Use histogram and CDF data for inline visualization

### COA Generation & Selection
1. Generate 3 COAs: `generate_coas` — Speed, Balanced, Comprehensive
2. Optionally simulate each COA: `generate_coas` with simulate=true
3. Compare COAs: `compare_coas` — side-by-side across all dimensions
4. For RED items: `generate_alternative_coa` — within-boundary alternatives
5. Present to customer: formatted comparison with recommendation (Balanced by default)
6. Customer selects: `select_coa` — records selection with rationale

### Simulation Pipeline Flowchart

```mermaid
flowchart TD
    A["Create Scenario"] --> B["Run Simulation"]

    subgraph SIM["6 Simulation Dimensions"]
        direction TB
        S1["Architecture Impact"]
        S2["Compliance Delta"]
        S3["Supply Chain Changes"]
        S4["Schedule Estimate"]
        S5["Cost Projection"]
        S6["Risk Score"]
    end

    B --> SIM
    SIM --> C["Monte Carlo Estimation"]
    C --> D["P50 / P80 / P90 Estimates"]
    D --> E["Generate 3 COAs"]

    subgraph COA["COA Options"]
        direction LR
        C1["Speed COA\nMVP, 1-2 PIs\nHigher Risk"]
        C2["Balanced COA\n P1+P2, 2-3 PIs\nModerate Risk"]
        C3["Comprehensive COA\nFull Scope, 3-5 PIs\nLowest Risk"]
    end

    E --> COA
    COA --> F["Compare COAs"]
    F --> G["Customer Selects COA"]
    G --> H["Record Selection + Rationale"]

    style A fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style B fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style SIM fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S1 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S2 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S3 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S4 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S5 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S6 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style C fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style D fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style E fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style COA fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style C1 fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style C2 fill:#3a2a1a,stroke:#e8590c,color:#e0e0e0
    style C3 fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style F fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style G fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style H fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
```

### 6 Simulation Dimensions
| Dimension | Metrics | Data Sources |
|-----------|---------|-------------|
| Architecture | Component count, coupling, API surface, data flow complexity | SysML elements, digital thread |
| Compliance | Control coverage delta, POAM projection, boundary tier | project_controls, crosswalk, SSP |
| Supply Chain | New dependencies, vendor risk, SBOM delta, ISA changes | dependency graph, vendors, ISAs |
| Schedule | PERT estimates, Monte Carlo confidence, critical path | SAFe decomposition, risk events |
| Cost | T-shirt roll-up, vendor licensing, infra delta | ricoas_config cost models |
| Risk | Compound risk score, risk interaction, mitigation effectiveness | risk register, Monte Carlo |

## Tools Used
| Tool | Purpose |
|------|---------|
| tools/simulation/simulation_engine.py | 6-dimension simulation core |
| tools/simulation/monte_carlo.py | PERT/Monte Carlo estimation |
| tools/simulation/coa_generator.py | 3 COAs + RED alternatives |
| tools/simulation/scenario_manager.py | Fork, compare, export scenarios |
| tools/mcp/simulation_server.py | MCP server (8 tools) |

## Edge Cases
- Zero requirements in session → return empty simulation with warning
- Monte Carlo with < 100 iterations → warn about low confidence
- All requirements GREEN → skip boundary dimension
- COA selection without simulation → warn, allow anyway
