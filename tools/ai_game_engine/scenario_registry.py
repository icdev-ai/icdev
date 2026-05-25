# CUI // SP-CTI
"""AI GameDay Scenario Registry — ontology-enriched scenario loading."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from pathlib import Path
from typing import Any

from tools.ttx.scenario_loader import load_scenario as _load_scenario, list_scenario_slugs as _list_scenario_slugs
from .ontology import resolve_scenario_ontology, resolve_role_ontology

log = get_logger(__name__)


class OntologyScenarioRegistry:
    """Wraps the TTX scenario loader and enriches output with ontology tags."""

    def __init__(self, scenarios_dir: Path | None = None) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._scenarios_dir = scenarios_dir

    def list_slugs(self) -> list[str]:
        return _list_scenario_slugs()

    def load(self, slug: str) -> dict[str, Any]:
        """Load a scenario and attach ontology tags."""
        if slug in self._cache:
            return self._cache[slug]

        scenario = _load_scenario(slug)

        # Attach scenario-level ontology
        scenario["ontology"] = resolve_scenario_ontology(slug)

        # Attach role-level ontology
        for role in scenario.get("roles", []):
            role_id = role.get("id", "")
            role["ontology"] = resolve_role_ontology(role_id)

        # Attach inject-level ontology (derive from allowed tools)
        for inject in scenario.get("injects", []):
            inject["ontology"] = self._resolve_inject_ontology(inject)

        self._cache[slug] = scenario
        return scenario

    def reload(self, slug: str) -> dict[str, Any]:
        """Invalidate cache and reload a scenario."""
        self._cache.pop(slug, None)
        return self.load(slug)

    def get_all(self) -> list[dict[str, Any]]:
        """Load all scenarios with ontology enrichment."""
        return [self.load(slug) for slug in self.list_slugs()]

    @staticmethod
    def _resolve_inject_ontology(inject: dict[str, Any]) -> dict[str, Any]:
        """Derive ontology tags for an inject based on its allowed tools and type."""
        classes = []
        tools = inject.get("ai_tools_allowed", [])
        inject_type = inject.get("inject_type", "")

        if any(t.startswith("strategos.wargame") for t in tools):
            classes.append({"prefix": "strategy", "concept": "WargameInject", "iri": "http://icdev.io/ontology/strategy#WargameInject"})
        if any(t.startswith("strategos.oracle") or t.startswith("strategos.signals") for t in tools):
            classes.append({"prefix": "strategy", "concept": "IntelligenceInject", "iri": "http://icdev.io/ontology/strategy#IntelligenceInject"})
        if any(t.startswith("strategos.simulate") for t in tools):
            classes.append({"prefix": "strategy", "concept": "SimulationInject", "iri": "http://icdev.io/ontology/strategy#SimulationInject"})
        if any(t.startswith("finetune") for t in tools):
            classes.append({"prefix": "security", "concept": "MLInject", "iri": "http://icdev.io/ontology/security#MLInject"})
        if any(t.startswith("aadc") for t in tools):
            classes.append({"prefix": "security", "concept": "AADCInject", "iri": "http://icdev.io/ontology/security#AADCInject"})
        if any(t.startswith("aimc") for t in tools):
            classes.append({"prefix": "security", "concept": "AIMCInject", "iri": "http://icdev.io/ontology/security#AIMCInject"})
        if any(t.startswith("knowledge") for t in tools):
            classes.append({"prefix": "strategy", "concept": "KnowledgeInject", "iri": "http://icdev.io/ontology/strategy#KnowledgeInject"})
        if inject_type == "aadc_design_challenge":
            classes.append({"prefix": "security", "concept": "DesignChallenge", "iri": "http://icdev.io/ontology/security#DesignChallenge"})

        return {"classes": classes}

    def to_json(self) -> str:
        """Serialize the full registry to JSON."""
        return json.dumps(self.get_all(), indent=2)


# Singleton instance for import-time convenience
_registry: OntologyScenarioRegistry | None = None


def get_registry() -> OntologyScenarioRegistry:
    global _registry
    if _registry is None:
        _registry = OntologyScenarioRegistry()
    return _registry


def load_scenario(slug: str) -> dict[str, Any]:
    """Convenience wrapper: load scenario with ontology enrichment."""
    return get_registry().load(slug)


def list_scenario_slugs() -> list[str]:
    return get_registry().list_slugs()
