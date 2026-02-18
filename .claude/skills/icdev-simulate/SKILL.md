---
description: "Run Digital Program Twin simulations and generate COAs for requirements"
---

# ICDEV Digital Program Twin Simulation

Run what-if simulations across 6 dimensions, generate COAs (Speed/Balanced/Comprehensive), and use Monte Carlo for schedule/cost estimation.

## Usage
/icdev-simulate <project-id> [--session <session-id>] [--coas] [--monte-carlo] [--compare]

## Workflow

### Generate & Compare COAs
1. Generate 3 COAs with simulation: `generate_coas` with simulate=true
2. Run Monte Carlo on each: schedule and cost dimensions
3. Compare all COAs: `compare_coas`
4. Present comparison matrix to customer
5. Record selection: `select_coa`

### What-If Analysis
1. Create scenario with modifications
2. Run 6-dimension simulation
3. Review impact across all dimensions
4. Fork and adjust as needed

## MCP Tools Used
- create_scenario, run_simulation, run_monte_carlo
- generate_coas, generate_alternative_coa, compare_coas, select_coa
- manage_scenarios

## Example
```
/icdev-simulate proj-123 --session sess-abc --coas
```
This generates 3 COAs for the session's requirements, simulates each, and presents the comparison.
