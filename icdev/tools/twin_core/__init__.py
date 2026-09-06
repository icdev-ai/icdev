# CUI // SP-CTI — Twin Core: additive cross-canvas digital-twin unification layer
"""twin_core — a thin, additive layer that unifies the eight working canvas
digital twins (NDC, PDC, BDC, SDC, DDC, ODC, IDC, Mission) behind one registry
and one canonical verdict/violation schema, WITHOUT rewriting any of them.

Public surface:

* :mod:`tools.twin_core.schema` — canonical verdict/severity/category enums,
  normalization, and the canonical violation factory (Sequoia Pattern 4).
* :class:`tools.twin_core.registry.TwinRegistry` — data-driven registry of thin
  per-canvas adapters (:class:`~tools.twin_core.registry.TwinAdapter`).

Reference adapters (twx-core-01): ``ndc``, ``pdc``.
"""
from tools.twin_core.airgap_rules import evaluate_airgap, is_airgap_environment, load_rules
from tools.twin_core.target_presets import evaluate_target, get_preset, list_presets
from tools.twin_core.event_bridge import (
    TWIN_SIMULATION_COMPLETED,
    TWIN_SNAPSHOT_TAKEN,
    recent_twin_events,
    register_subscriptions,
    simulate,
    snapshot,
)
from tools.twin_core.observer import observe
from tools.twin_core.registry import (
    TwinAdapter,
    TwinRegistry,
    register_twin,
    known_canvas_keys,
)
from tools.twin_core.schema import (
    CATEGORIES,
    DEFAULT_SNAPSHOT_PROVENANCE,
    PROVENANCE_EMULATED,
    SEVERITIES,
    SNAPSHOT_PROVENANCES,
    TARGET_CSPS,
    UNKNOWN_VERDICT,
    VERDICTS,
    canonical_violation,
    derive_verdict_from_violations,
    normalize_csp,
    normalize_severity,
    normalize_verdict,
    summarize_violations,
    twin_verdict,
    worst_severity,
    worst_verdict,
)

__all__ = [
    "TwinAdapter",
    "TwinRegistry",
    "register_twin",
    "known_canvas_keys",
    "observe",
    "simulate",
    "snapshot",
    "register_subscriptions",
    "recent_twin_events",
    "evaluate_airgap",
    "is_airgap_environment",
    "load_rules",
    "evaluate_target",
    "get_preset",
    "list_presets",
    "TWIN_SNAPSHOT_TAKEN",
    "TWIN_SIMULATION_COMPLETED",
    "VERDICTS",
    "UNKNOWN_VERDICT",
    "SEVERITIES",
    "CATEGORIES",
    "TARGET_CSPS",
    "SNAPSHOT_PROVENANCES",
    "DEFAULT_SNAPSHOT_PROVENANCE",
    "PROVENANCE_EMULATED",
    "normalize_verdict",
    "normalize_severity",
    "normalize_csp",
    "canonical_violation",
    "worst_verdict",
    "worst_severity",
    "summarize_violations",
    "derive_verdict_from_violations",
    "twin_verdict",
]
