---
ontology_id: icdev:mission:m-strategos-01-signal-wargaming:step:1
step_class: icdev:Lab
---

# Strategos — From Raw Signal to Wargamed Decision

**Strategos** (`tools/strategos/`, registry key `strategos` — *"Strategic intelligence IQE
adapter"*) is ICDEV's DIB (defense-industrial-base) supply-chain and wargaming intelligence
subsystem. It is a Flask blueprint at `/strategos` with an IQE adapter (collections
`strategos.signals`, `strategos.conflict_events`, `strategos.leadership_briefs`,
`strategos.sio_assessments`) — notably it exposes **no MCP tools**; you reach it through the
canvas and IQE, not the MCP gateway. This lab walks the path an analyst actually travels:
score the noise, keep the top signals, then wargame the decision.

## Scoring a signal the Signal Scout way

The **Signal Scout** Genesis reflex (`tools/genesis/reflexes/strategos/signal_scout.py`)
reads `sg_raw_signals` and ranks them across **PMESII-PT** domains — Political, Military,
Economic, Social, Information, Infrastructure, Physical-environment, Time — using the domain
scorers in `tools/strategos/iw_scorers.py` (`MilitarySignalScorer`, `EconomicSignalScorer`,
`DiplomaticSignalScorer`, `InfrastructureScorer`, `InformationScorer`). Two forces shape a
signal's weight:

- **STANAG 2022 source grading** — reliability `A` (reliable) through `F` (cannot be judged).
  A tip from a proven source counts for more than a rumor.
- **Half-life decay** — intelligence ages. A signal loses half its weight every half-life
  window, so a fresh report outranks a stale one of equal strength.

Your `score_signal()` multiplies raw strength x domain weight x source reliability x decay.
The reflex then writes the **top-N** into `sg_prioritized_signals` — your `prioritize_signals()`
is that cut.

## Wargaming the decision

Prioritized intel feeds decision math in `tools/strategos/ooda.py`:

- **`score_coa()`** ranks a course of action by weighing feasibility and impact against risk.
  Strategos generates COAs (`strategy_agent.py`), stress-tests them with a Red Cell most-likely
  / most-dangerous analysis (`red_cell.py::synthesize_mlcoa` / `synthesize_mdcoa`), and rolls
  the survivors into a War Council brief (`war_council.py`).
- **`lanchester_square()`** predicts a force-on-force outcome. Lanchester's **square law**
  says combat power scales with the *square* of the number of units, so concentrated mass beats
  dispersed quality: `a·A²` vs `b·D²`. It is why the "few elite units vs many ordinary units"
  matchup so often goes to mass — you'll see that in the grader.

Open `step1_starter.py` and implement the four `TODO`s. Everything here is deterministic and
offline; the real subsystem ingests live OSINT feeds (`acled_importer`, `gdelt_importer`,
`darkweb`, SOCMINT) and runs Monte-Carlo Lanchester and Nash-equilibrium solvers, but the
scoring-then-wargaming spine is exactly what you build.
