# CUI // SP-CTI
"""Document Modernization Engine — declarative pack registry.

Packs are YAML files in ``args/docmod/packs/``; each names an ``evaluator``
(dotted path to a DomainPack subclass). Adding a domain is YAML-first: drop a
pack file (+ evaluator module if the generic rulebook evaluator doesn't fit)
and the scanner, catalog, findings pipeline and KG hook pick it up.

Loading also publishes each pack's extraction regexes to
``tools.knowledge_graph.text_network.EXTRA_ENTITY_PATTERNS`` (when available)
so ingested documents grow KG nodes for hardware/software/protocol entities.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

from tools.logging.icdev_logger import get_logger

from .base_pack import DomainPack

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = _REPO_ROOT / "args" / "docmod" / "packs"
CONFIG_PATH = _REPO_ROOT / "args" / "docmod" / "docmod_config.yaml"

_REQUIRED_KEYS = ("pack_id", "label", "evaluator", "entity_types")

_cache: dict = {"mtimes": None, "packs": None, "config": None, "config_mtime": None}


class PackValidationError(ValueError):
    """A pack YAML is malformed — names the file and the offending key."""


def load_config() -> dict:
    """Engine config (thresholds, cadence, offline flag) with mtime hot-reload."""
    import yaml

    if not CONFIG_PATH.exists():
        return {}
    mtime = CONFIG_PATH.stat().st_mtime
    if _cache["config"] is not None and _cache["config_mtime"] == mtime:
        return _cache["config"]
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    _cache["config"], _cache["config_mtime"] = cfg, mtime
    return cfg


def _pack_files() -> list[Path]:
    if not PACKS_DIR.exists():
        return []
    return sorted(PACKS_DIR.glob("*.yaml"))


def load_packs(force: bool = False) -> dict[str, DomainPack]:
    """Load enabled packs, mtime-cached. Returns {pack_id: DomainPack}."""
    import yaml

    files = _pack_files()
    mtimes = tuple((str(f), f.stat().st_mtime) for f in files)
    if not force and _cache["packs"] is not None and _cache["mtimes"] == mtimes:
        return _cache["packs"]

    packs: dict[str, DomainPack] = {}
    for f in files:
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise PackValidationError(f"{f.name}: unparseable YAML: {exc}") from exc

        for key in _REQUIRED_KEYS:
            if key not in raw:
                raise PackValidationError(f"{f.name}: missing required key '{key}'")

        if not raw.get("enabled", True):
            logger.debug("docmod: pack %s disabled — skipped", raw["pack_id"])
            continue

        dotted = raw["evaluator"]
        try:
            mod_path, cls_name = dotted.rsplit(".", 1)
            cls = getattr(importlib.import_module(mod_path), cls_name)
        except Exception as exc:
            raise PackValidationError(
                f"{f.name}: evaluator '{dotted}' not importable: {exc}"
            ) from exc
        if not (isinstance(cls, type) and issubclass(cls, DomainPack)):
            raise PackValidationError(f"{f.name}: evaluator '{dotted}' is not a DomainPack")

        pack = cls(config=raw)
        packs[pack.pack_id] = pack

    _cache["packs"], _cache["mtimes"] = packs, mtimes
    _publish_kg_patterns(packs)
    return packs


def _publish_kg_patterns(packs: dict[str, DomainPack]) -> None:
    """Push pack extraction regexes into the KG extractor hook (best-effort;
    the hook lands with docmod-packs-06 — absence is not an error)."""
    try:
        from tools.knowledge_graph import text_network
    except Exception:
        return
    hook = getattr(text_network, "EXTRA_ENTITY_PATTERNS", None)
    if hook is None:
        return
    published: list[tuple] = []
    for pack in packs.values():
        extraction = (pack.config or {}).get("extraction", {}) or {}
        for pattern in extraction.get("patterns", []) or []:
            entity_type = pack.entity_types[0] if pack.entity_types else "component"
            try:
                published.append((re.compile(pattern), entity_type))
            except re.error as exc:
                logger.warning("docmod: pack %s bad regex %r: %s", pack.pack_id, pattern, exc)
    hook[:] = published
