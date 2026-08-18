# CUI // SP-CTI
"""Backward-compatible re-export of the SSP evidence seam (cef-di-03).

The module lives at :mod:`icdev.tools.document_intelligence.ssp_evidence` — the
canonical namespace CLAUDE.md mandates for new code. This file exists so
``from tools.document_intelligence import ssp_evidence`` keeps working for the
legacy import shape the rest of this canvas still uses.

It is a RE-EXPORT, not a copy, and that is load-bearing rather than tidy.
Everything else under ``tools/document_intelligence/`` is mirrored as a
byte-identical copy, which is harmless for modules whose functions are pure.
This one is not: it holds thread-local run state — the per-run memo cache, the
outbound resolution budget and the re-entrancy flag. ``tools.X`` and
``icdev.tools.X`` are SEPARATE module objects (``a is b`` -> ``False``), so two
copies would be two ``_STATE`` locals: a caller resetting one while ``acoic``
imported the other would re-arm a budget that was never spent, resolve the same
control twice, and defeat the recursion guard the whole seam depends on. That is
the two-module-objects defect this repository has already hit with
``tools.db.storage`` and ``extension_manager``.

Importing the NAMES rather than re-declaring them keeps one set of function
objects and one ``_STATE``. It also means a test that patches this module's
``load_config`` patches only this namespace and NOT the canonical globals the
functions actually read — patch
``icdev.tools.document_intelligence.ssp_evidence`` instead.
"""
from icdev.tools.document_intelligence.ssp_evidence import (  # noqa: F401
    CONFIG_KEY,
    CONFIG_PATH,
    DEFAULT_MAX_RESOLVES,
    DEFAULT_TOP_K,
    PACK_EVIDENCE_TYPE,
    PATH_CALLER,
    PATH_CORTEX,
    PATH_CORTEX_EMPTY_FALLBACK,
    PATH_LEGACY,
    SSPEvidence,
    cortex_config,
    cortex_enabled,
    evidence_question,
    fallback_on_empty,
    load_config,
    reset_run_state,
    resolve_evidence,
    run_stats,
)

__all__ = [
    "CONFIG_KEY",
    "CONFIG_PATH",
    "DEFAULT_MAX_RESOLVES",
    "DEFAULT_TOP_K",
    "PACK_EVIDENCE_TYPE",
    "PATH_CALLER",
    "PATH_CORTEX",
    "PATH_CORTEX_EMPTY_FALLBACK",
    "PATH_LEGACY",
    "SSPEvidence",
    "cortex_config",
    "cortex_enabled",
    "evidence_question",
    "fallback_on_empty",
    "load_config",
    "reset_run_state",
    "resolve_evidence",
    "run_stats",
]
