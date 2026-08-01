# CUI // SP-CTI
"""BDC cATO Twin Phase 1 — compliance snapshot + IQE queries + POA&M auto-generation.

Sub-modules:
  snapshot_writer     — freeze cross-framework compliance state at assessor-run end
  poam_auto_generator — auto-generate POA&M entries from twin violations
  cli                 — command-line interface

The IQE query surface has moved onto the maintained IQE executor/adapters
(``tools/iqe/adapters/compliance.py`` — ``compliance.twin_*`` collections and
the validated ``run_query`` entrypoint). The Phase-1 regex engine that used to
live here (``query_engine.py``) was retired in bdt-iqe-1.
"""
