#!/usr/bin/env python3
# CUI // SP-CTI
"""Single resolver for the path to ``args/rag_config.yaml``.

Fifteen modules under ``tools/rag/`` independently hardcoded
``BASE_DIR / "args" / "rag_config.yaml"``. That is fine for normal operation and
useless for measurement: to attribute a retrieval delta to one toggle you must
run the pipeline twice with only that toggle changed, and the only ways to do
that were (a) edit the shared config file in place, or (b) thread a config dict
through every call site.

(a) is unsafe here — several agent sessions and the kanban scheduler share this
checkout, so a benchmark that rewrites ``args/rag_config.yaml`` races them and
leaves the tree dirty if it dies mid-run. (b) does not reach modules that load
the file themselves rather than accepting an injected dict.

So: one resolver, honouring ``ICDEV_RAG_CONFIG``. The benchmark writes a temp
config per run and points the env var at it; nothing on disk in the repo moves.

Adopt this in any module that reads ``rag_config.yaml``::

    from tools.rag.config_path import rag_config_path
    config_path = rag_config_path()

Unset (the normal case) it returns the committed ``args/rag_config.yaml``, so
behaviour is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Absolute path to an alternate rag_config.yaml. Empty/unset = repo default.
CONFIG_ENV_VAR = "ICDEV_RAG_CONFIG"

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def rag_config_path() -> Path:
    """Return the active ``rag_config.yaml`` path.

    Resolution order:

    1. ``$ICDEV_RAG_CONFIG`` when set to a non-empty value.
    2. ``<repo>/args/rag_config.yaml``.

    A non-existent override is returned as-is rather than silently falling back:
    every caller already guards with ``.exists()``, and a typo'd override that
    quietly reverted to the committed config would make a benchmark report a
    delta of zero for a toggle it never actually flipped. Failing visibly beats
    measuring the wrong thing.
    """
    override = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return BASE_DIR / "args" / "rag_config.yaml"
