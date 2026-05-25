# CUI // SP-CTI
"""Ontology bridge — maps canvas entity types to ICDEV ontology class URIs.

Provides a single get_ontology_id(canvas_key, entity_type) function that
canonicalises every canvas node/edge type into an ontology URI.

Usage
-----
    from tools.canvas.ontology_bridge import get_ontology_id
    uri = get_ontology_id("idc", "aws-ec2")
    # -> "icdev:ComputeService.AWS.EC2"

KG builder and blueprints call this at persistence / serialisation time so
that every KG node carries an ontology_id without hard-coding URIs in
multiple places.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Callable

logger = get_logger("icdev.canvas.ontology_bridge")

# ── Base namespace ─────────────────────────────────────────────────────────────
_ONTO_BASE = "https://icdev.dev/ontology"

# ── Canvas-specific ontology namespaces ───────────────────────────────────────
_CANVAS_NS: dict[str, str] = {
    "odc":     f"{_ONTO_BASE}/observability#",
    "idc":     f"{_ONTO_BASE}/infrastructure#",
    "aadc":    f"{_ONTO_BASE}/ai#",
    "mdc":     f"{_ONTO_BASE}/migration#",
    "ndc":     f"{_ONTO_BASE}/network#",
    "ohc":     f"{_ONTO_BASE}/observability#",
    "aisg":    f"{_ONTO_BASE}/ai-ethics#",
    "bdc":     f"{_ONTO_BASE}/boundary#",
    "sdc":     f"{_ONTO_BASE}/security#",
    "ddc":     f"{_ONTO_BASE}/data#",
    "pdc":     f"{_ONTO_BASE}/pipeline#",
    "qdc":     f"{_ONTO_BASE}/quality#",
    "aiml":    f"{_ONTO_BASE}/aiml#",
    "mission": f"{_ONTO_BASE}/mission#",
}

# ── Lazy import registry ─────────────────────────────────────────────────────
# Each entry is a callable that returns the canvas ONTOLOGY_MAP dict.
# We use lazy imports to avoid circular deps at module load time.
_CANVAS_MAP_GETTERS: dict[str, Callable[[], dict[str, str]]] = {}


def _register(canvas_key: str, getter: Callable[[], dict[str, str]]) -> None:
    _CANVAS_MAP_GETTERS[canvas_key.lower()] = getter


# Register canvas constant getters (lazy to avoid circular imports)
def _idc_map() -> dict[str, str]:
    from tools.infra_canvas.constants import INFRA_ONTOLOGY_MAP
    return INFRA_ONTOLOGY_MAP


def _odc_map() -> dict[str, str]:
    from tools.observability_canvas.constants import OBSERVABILITY_ONTOLOGY_MAP
    return OBSERVABILITY_ONTOLOGY_MAP


def _aadc_map() -> dict[str, str]:
    from tools.agentic_ai_canvas.constants import AADC_ONTOLOGY_MAP
    return AADC_ONTOLOGY_MAP


def _mdc_map() -> dict[str, str]:
    from tools.migration_canvas.constants import MIGRATION_ONTOLOGY_MAP
    return MIGRATION_ONTOLOGY_MAP


def _ndc_map() -> dict[str, str]:
    from tools.network.constants import NETWORK_ONTOLOGY_MAP
    return NETWORK_ONTOLOGY_MAP


def _ohc_map() -> dict[str, str]:
    from tools.ops_hub.constants import OHC_ONTOLOGY_MAP
    return OHC_ONTOLOGY_MAP


def _aisg_map() -> dict[str, str]:
    from tools.aisg.constants import AISG_ONTOLOGY_MAP
    return AISG_ONTOLOGY_MAP


def _bdc_map() -> dict[str, str]:
    from tools.boundary_canvas.constants import BOUNDARY_ONTOLOGY_MAP
    return BOUNDARY_ONTOLOGY_MAP


def _sdc_map() -> dict[str, str]:
    from tools.security_canvas.constants import SECURITY_ONTOLOGY_MAP
    return SECURITY_ONTOLOGY_MAP


def _ddc_map() -> dict[str, str]:
    from tools.data_canvas.constants import DATA_ONTOLOGY_MAP
    return DATA_ONTOLOGY_MAP


def _pdc_map() -> dict[str, str]:
    from tools.pipeline.constants import PIPELINE_ONTOLOGY_MAP
    return PIPELINE_ONTOLOGY_MAP


def _qdc_map() -> dict[str, str]:
    from tools.qdc_canvas.constants import QDC_ONTOLOGY_MAP
    return QDC_ONTOLOGY_MAP


def _aiml_map() -> dict[str, str]:
    from tools.aiml_canvas.constants import AIML_ONTOLOGY_MAP
    return AIML_ONTOLOGY_MAP


def _mission_map() -> dict[str, str]:
    from tools.mission_canvas.constants import MISSION_ONTOLOGY_MAP
    return MISSION_ONTOLOGY_MAP


_register("idc", _idc_map)
_register("odc", _odc_map)
_register("aadc", _aadc_map)
_register("mdc", _mdc_map)
_register("ndc", _ndc_map)
_register("ohc", _ohc_map)
_register("aisg", _aisg_map)
_register("bdc", _bdc_map)
_register("sdc", _sdc_map)
_register("ddc", _ddc_map)
_register("pdc", _pdc_map)
_register("qdc", _qdc_map)
_register("aiml", _aiml_map)
_register("mission", _mission_map)


def _ai_observatory_map() -> dict[str, str]:
    from tools.ai_observatory.constants import AI_OBSERVATORY_ONTOLOGY_MAP
    return AI_OBSERVATORY_ONTOLOGY_MAP


def _govlift_map() -> dict[str, str]:
    from tools.govlift.constants import GOVLIFT_ONTOLOGY_MAP
    return GOVLIFT_ONTOLOGY_MAP


def _system_graph_map() -> dict[str, str]:
    from tools.system_graph.constants import SYSTEM_GRAPH_ONTOLOGY_MAP
    return SYSTEM_GRAPH_ONTOLOGY_MAP


_register("ai_observatory", _ai_observatory_map)
_register("govlift", _govlift_map)
_register("system_graph", _system_graph_map)

# ── Public API ─────────────────────────────────────────────────────────────────


def get_ontology_id(canvas_key: str, entity_type: str) -> str | None:
    """Return the ontology URI for a canvas entity type, or None if unmapped.

    Parameters
    ----------
    canvas_key : str
        Short canvas identifier (e.g. ``"idc"``, ``"odc"``).
    entity_type : str
        The node/edge type string from the canvas constants.

    Returns
    -------
    str | None
        Full ontology URI (e.g. ``"https://icdev.dev/ontology/infrastructure#ComputeService.AWS.EC2"``)
        or ``None`` if no mapping exists.
    """
    key = canvas_key.lower()
    getter = _CANVAS_MAP_GETTERS.get(key)
    if not getter:
        return None

    try:
        mapping = getter()
    except Exception as exc:
        logger.debug("ontology_bridge: failed to load map for %s — %s", key, exc)
        return None

    uri = mapping.get(entity_type)
    if uri is not None:
        return uri

    # Fallback: synthesise a URI from the namespace + normalised type
    ns = _CANVAS_NS.get(key, f"{_ONTO_BASE}/{key}#")
    safe_type = entity_type.replace("-", ".").replace("_", ".")
    return f"{ns}{safe_type}"


def get_namespace(canvas_key: str) -> str:
    """Return the ontology namespace URI for a canvas."""
    return _CANVAS_NS.get(canvas_key.lower(), f"{_ONTO_BASE}/{canvas_key.lower()}#")


def list_registered_canvases() -> list[str]:
    """Return all canvas keys that have registered ontology getters."""
    return list(_CANVAS_MAP_GETTERS.keys())
