# Phase 2 — Core Modernization: classification-boundary constants in auto_indexer

**CUI // SP-CTI**

AI-ify opportunity **5459** (roadmap `rm-6efad73721`, scan 28) —
`hardcoded_threshold → anomaly_detection` at `tools/rag/auto_indexer.py:348`
(`if project_level <= 1:`). Sibling opportunity **5458** (line 345,
`project_level < 2`) is the same kind of constant in the same function and is
resolved by the same change.

## Assessment — why NOT anomaly detection

The flagged numerics live in `AutoIndexer._is_above_classification()`, the
CUI-boundary enforcement check that blocks files marked above the project's
classification (UNCLASSIFIED < CUI < SECRET < TOP SECRET). `project_level <= 1`
and `project_level < 2` are **deterministic security comparisons against a fixed
NIST classification hierarchy**, not tunable operational thresholds.

Replacing them with an ML anomaly-detection model would be actively harmful:
classification and audit (NIST AU) controls must be deterministic, reproducible,
and auditable — a probabilistic model could silently admit a SECRET-marked file
into a CUI index. The scanner's *paradigm* recommendation is a false positive
here.

The scanner's *underlying* signal — bare magic numbers (`1`, `2`) — is a genuine
code smell. That is the part worth fixing.

## Change

`tools/rag/auto_indexer.py` (+ `icdev/` mirror):

- Promote the previously method-local `LEVELS` dict to a documented module
  constant `_CLASSIFICATION_LEVELS`.
- Derive named boundary constants from it:
  - `_CUI_LEVEL = _CLASSIFICATION_LEVELS["CUI"]` (= 1)
  - `_SECRET_LEVEL = _CLASSIFICATION_LEVELS["SECRET"]` (= 2)
  - `_DEFAULT_CLASSIFICATION_LEVEL = _CLASSIFICATION_LEVELS["CUI"]`
- Rewrite the comparisons to read as named boundaries:
  - `project_level < 2`  → `project_level < _SECRET_LEVEL`
  - `project_level <= 1` → `project_level <= _CUI_LEVEL`
  - `LEVELS.get(proj_class, 1)` → `_CLASSIFICATION_LEVELS.get(proj_class, _DEFAULT_CLASSIFICATION_LEVEL)`

The behavior is **identical** — the constants equal the former literals — but the
magic numbers are gone and the security semantics are self-documenting. The check
remains fully deterministic.

## Tests

`tests/test_auto_indexer_classification_boundary.py` — 8 tests:

- Constants: hierarchy is monotonic; named constants derive from the hierarchy;
  named constants equal the former literals (behavior-preserving).
- Boundary behavior: CUI project blocks `// SECRET`, NOFORN/SAP/SCI/HCS/SI/TK,
  and `// TOP SECRET`; admits plain CUI; a SECRET-tier project does not fire the
  CUI-tier NOFORN block.

All pass; `ruff check` clean.

## Note — duplicate / mislabeled opportunities

Opportunities 5457/5458/5459 all target `auto_indexer.py` under the same
`hardcoded_threshold → anomaly_detection` label:

- **5457** (line 132) — the real `max_file_size_mb` file-size gate; genuinely
  suited to adaptive detection (handled on its own branch).
- **5458** (line 345) and **5459** (line 348) — classification-level comparison
  constants in the security boundary check; resolved here by naming, not ML.
