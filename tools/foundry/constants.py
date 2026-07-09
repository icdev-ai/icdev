# CUI // SP-CTI
"""ACF — Autonomous Capability Foundry — shared constants.

Single source of truth for the enumerated values used as SQL ``CHECK`` predicates
across the ``foundry_*`` platform tables (see ``tools/foundry/db/init_db.py``) AND
as the Python-side validation surface for the harvester / synthesizer / scorer /
deliberator pipeline. SQL and Python stay in lock-step by deriving both from the
tuples below — never hard-code an enum in a CREATE TABLE or a comparison.

Append-only note: ``foundry_runs`` / ``foundry_signals`` / ``foundry_specs`` /
``foundry_tasks_emitted`` / ``foundry_outcomes`` are append-only (registered in
``.claude/hooks/pre_tool_use.py``). ``foundry_concepts`` is intentionally mutable
(status transitions proposed -> scored -> approved | rejected).
"""
from __future__ import annotations

# ── foundry_signals.source_engine ───────────────────────────────────────────
# Which existing ICDEV engine store a harvested signal was pulled from. The
# harvester (acf-harvest-01) emits innovation / creative / research; genesis /
# telemetry are added by acf-harvest-02; rfi (RFI capability-gap demand signals,
# rfi_capability_gaps) added by rfidem-fdry-02. Mirrors the keys under ``sources:``
# in args/foundry_config.yaml.
SOURCE_ENGINES: tuple[str, ...] = (
    "innovation",
    "creative",
    "research",
    "genesis",
    "rfi",
    "telemetry",
)

# ── foundry_runs.status ──────────────────────────────────────────────────────
# Lifecycle of one harvest -> synth -> novelty -> score -> CoD -> SIPA cycle.
# ``rate_limited`` is recorded by the engine (acf-engine-02) when a cycle is
# short-circuited before emit because the per-cycle / active-project caps in
# ``args/foundry_config.yaml -> rate_limits`` are hit. ``circuit_open`` is
# reserved for the engine circuit breaker (acf-engine-03).
RUN_STATUSES: tuple[str, ...] = (
    "running",
    "completed",
    "failed",
    "deferred",
    "rate_limited",
    "circuit_open",
)

# ── foundry_concepts.status ──────────────────────────────────────────────────
# Concept lifecycle emitted by the synthesizer -> scorer -> deliberator pipeline.
# ``shipped`` and ``failed`` are post-build terminal states written by
# ``tools/foundry/learner.py`` (acf-learn-01) when ``record_outcomes`` reconciles
# each concept's emitted kanban tasks against their final statuses. They are
# distinct from ``rejected`` (concept was never built) and from ``approved``
# (build is in flight). The CHECK on ``foundry_concepts.status`` is derived
# from this tuple via ``sql_in()`` in ``tools/foundry/db/init_db.py`` so the
# schema re-derives automatically.
CONCEPT_STATUSES: tuple[str, ...] = (
    "proposed",
    "scored",
    "approved",
    "rejected",
    "shipped",
    "failed",
)

# ── foundry_concepts.reject_reason (nullable) ────────────────────────────────
# Why a concept was rejected; NULL while the concept is still in play.
REJECT_REASONS: tuple[str, ...] = (
    "duplicate",
    "low_novelty",
    "low_score",
    "cod_reject",
    "vv_fail",
)

# ── foundry_outcomes.outcome ─────────────────────────────────────────────────
# Recorded build/ship result for an ACF-seeded concept (feeds the circuit breaker).
OUTCOME_VALUES: tuple[str, ...] = (
    "shipped",
    "vv_pass",
    "vv_fail",
    "abandoned",
)


def sql_in(column: str, values: tuple[str, ...]) -> str:
    """Build a ``<column> IN ('a', 'b', ...)`` predicate from a values tuple."""
    joined = "', '".join(values)
    return f"{column} IN ('{joined}')"
