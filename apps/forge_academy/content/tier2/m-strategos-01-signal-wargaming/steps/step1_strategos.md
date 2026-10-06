---
ontology_id: icdev:mission:m-strategos-01-signal-wargaming:step:1
step_class: icdev:Lab
---

# Strategos — From Raw Signal to Wargamed Decision

**Strategos** (`tools/strategos/`, registry key `strategos` — *"Strategic intelligence IQE
adapter"*) is ICDEV's DIB (defense-industrial-base) supply-chain and wargaming intelligence
subsystem. Its pages are a Flask blueprint (`apps/strategos/blueprint.py`, mounted at
`/strategos`) plus an IQE adapter (`tools/iqe/adapters/strategos.py`, collections
`strategos.signals`, `strategos.conflict_events`, `strategos.leadership_briefs`,
`strategos.sio_assessments`) — notably it exposes **no `strategos_*` MCP tools**; you reach it
through the canvas and IQE, not the MCP gateway. This lab walks the path an analyst actually
travels: score the noise, keep the top signals, then wargame the decision.

> **This lab is a simplified model.** The formulas you implement are deliberately smaller
> than the production ones; each section below says what the real code does.

## Scoring a signal the Signal Scout way

The **Signal Scout** Genesis reflex (`tools/genesis/reflexes/strategos/signal_scout.py`)
reads unprocessed rows from `sg_raw_signals`, scores them, and writes the **top-N** (default
`top_n: 20`) into `sg_prioritized_signals`. Its composite score is a **weighted sum** of four
dimensions, with weights in `args/strategos_config.yaml` under `signal_scout.weights`:

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| `posterior_shift` | 0.35 | rarity of the signal's **PMESII-PT** domain (Political, Military, Economic, Social, Information, Infrastructure, Physical-environment, Time) vs. the recent prior |
| `source_discriminability` | 0.25 | **STANAG 2022 source grading** — reliability `A` (reliable) through `F` (cannot be judged) |
| `temporal_recency` | 0.20 | freshness decay, `exp(-hours_old / temporal_decay_hours)` (24 h by default) |
| `domain_coverage` | 0.20 | whether the signal matches an active PIR/CCIR topic |

Your `score_signal()` keeps the same three ideas — domain priority, source reliability and
freshness decay — but combines them as a simple **product** (raw strength x domain weight x
source reliability x a 30-day half-life). `prioritize_signals()` is the top-N cut. (The
PMESII-PT domain scorers in `tools/strategos/iw_scorers.py` — `MilitarySignalScorer`,
`EconomicSignalScorer`, `DiplomaticSignalScorer`, `InfrastructureScorer`,
`InformationScorer` — serve the indications-and-warning engine, not Signal Scout.)

## Wargaming the decision

Prioritized intel feeds decision math in `tools/strategos/ooda.py`:

- **`score_coa()`** ranks courses of action. The real function takes a *list* of COAs and
  scores each across six criteria (`speed`, `surprise`, `mass`, `economy_of_force`,
  `maneuver`, `sustainability`; equal weights by default), adding `composite_score` and
  `rank`. Your version scores one COA on feasibility and impact against risk. Strategos
  generates COAs (`strategy_agent.py`), stress-tests them with a Red Cell most-likely /
  most-dangerous analysis (`red_cell.py::synthesize_mlcoa` / `synthesize_mdcoa`), and rolls
  the survivors into a War Council brief (`war_council.py`).
- **`lanchester_square()`** predicts a force-on-force outcome. The real function steps the
  attrition equations (`dB/dt = -rho*R`, `dR/dt = -beta*B`) over time and returns the series;
  your version uses the closed-form **square law** directly: combat power scales with the
  *square* of the number of units, so concentrated mass beats dispersed quality — `a*A^2` vs
  `b*D^2`. It is why the "few elite units vs many ordinary units" matchup so often goes to
  mass — you'll see that in the grader.

Open `step1_starter.py` and implement the four `TODO`s. Everything here is deterministic and
offline; the real subsystem ingests live OSINT feeds (`acled_importer.py`, `gdelt_importer.py`,
`darkweb.py`, SOCMINT) and runs Monte-Carlo Lanchester (`lanchester_monte_carlo`) and
Nash-equilibrium (`find_nash_2x2`) solvers, but the scoring-then-wargaming spine is what you
build.
