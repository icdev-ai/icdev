# CUI // SP-CTI
"""Which model AI-Ify recommends for a paradigm, and which it calls itself -- from CONFIG, never code (icx-doc-01).

ICDEV keeps model IDs out of code: routing lives in ``args/llm_config.yaml``, and AI-Ify's own choices live beside
its other settings (``args/aiify_config.yaml`` for AI-Ify, ``args/aac_config.yaml`` for the AI-Augmentation canvas):

    paradigm_models:   ai paradigm -> the model AI-Ify RECOMMENDS for an opportunity of that paradigm
    nlp_model:         the model AI-Ify's own NLP enrichment calls

A key the config does not set gives ``None`` (a recommendation) or ``""`` (a call: the router's chain for the
function decides). Nothing here falls back to a model name.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from icdev.core.paths import repo_root

_ARGS = repo_root(__file__) / "args"
AIIFY_CONFIG = _ARGS / "aiify_config.yaml"
AAC_CONFIG = _ARGS / "aac_config.yaml"


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def paradigm_models(config: Path = AIIFY_CONFIG) -> dict[str, str]:
    m = _load(str(config)).get("paradigm_models") or {}
    return {str(k): str(v) for k, v in m.items() if v}


def paradigm_model(paradigm: str, config: Path = AIIFY_CONFIG) -> str | None:
    return paradigm_models(config).get(paradigm)


def nlp_model(config: Path = AIIFY_CONFIG) -> str:
    return str(_load(str(config)).get("nlp_model") or "")
