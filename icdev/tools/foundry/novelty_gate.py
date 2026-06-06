# CUI // SP-CTI
"""novelty_gate.py — ACF dedup gate: novelty score vs the existing capability catalog.

THE DIFFERENTIATOR FROM ORACLE/GENESIS. Those reflexes improve EXISTING tasks
incrementally; ACF must invent NET-NEW products. This gate compares a candidate
concept's proposed capability against a catalog built from assets ICDEV *already*
has — active canvases (``tools/canvas/canvas_registry``), tool manifests
(``tools/manifest/*.md``) and goal workflows (``goals/manifest.md``) — and rejects
concepts that are merely incremental rehashes.

    novelty_score = 1 - max_similarity(concept_capability, catalog)

* ``max_similarity >= duplicate_similarity``  -> ``is_duplicate``, reject 'duplicate'
* ``novelty_score   <  min_novelty``          -> reject 'low_novelty'
* otherwise the concept survives the gate.

Deterministic-first / air-gap safe: similarity is a blend of token-frequency cosine
and Jaccard set overlap — no network, no LLM. An OPTIONAL embedding re-rank via the
LLM router refines the nearest candidates when explicitly enabled in
``args/foundry_config.yaml`` AND a provider is available; it NEVER blocks and silently
falls back to the deterministic score.

Public API
----------
    build_catalog(force_rebuild=False) -> list[CatalogEntry]
    score_novelty(concept, *, config=None, catalog=None) -> dict
    apply_novelty_gate(concept, *, config=None, catalog=None) -> dict   # annotates concept

CLI
---
    python tools/foundry/novelty_gate.py --concept-json '{...}' --json
    python tools/foundry/novelty_gate.py --catalog --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:  # optional — only needed to read args/foundry_config.yaml
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - yaml is a core dep but stay defensive
    yaml = None  # type: ignore
    _HAS_YAML = False


# =========================================================================
# PATH / REPO ROOT
# =========================================================================
def _find_repo_root() -> Path:
    """Walk up from this file to the repo root.

    The module is mirrored into both ``tools/foundry`` and ``icdev/tools/foundry``,
    so a fixed ``parent.parent.parent`` is wrong for one of the two trees. Anchor on
    a marker that only exists at the true repo root.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "goals" / "manifest.md").exists() or (parent / "CLAUDE.md").exists():
            return parent
    # Fallback: tools/foundry/novelty_gate.py -> repo root is three levels up.
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()
CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# =========================================================================
# CONSTANTS (degrade gracefully if tools.foundry.constants not yet built)
# =========================================================================
# acf-db-01 owns tools/foundry/constants.py; until it lands, use literal fallbacks
# that match the documented REJECT_REASONS enum.
try:  # pragma: no cover - exercised once constants.py exists
    from icdev.tools.foundry import constants as _const  # type: ignore

    REJECT_DUPLICATE = getattr(_const, "REJECT_DUPLICATE", "duplicate")
    REJECT_LOW_NOVELTY = getattr(_const, "REJECT_LOW_NOVELTY", "low_novelty")
    _DEFAULT_MIN_NOVELTY = getattr(_const, "DEFAULT_THRESHOLDS", {}).get("min_novelty", 0.35)
except Exception:
    REJECT_DUPLICATE = "duplicate"
    REJECT_LOW_NOVELTY = "low_novelty"
    _DEFAULT_MIN_NOVELTY = 0.35

# Deterministic gate defaults — overridable via args/foundry_config.yaml -> novelty.*
DEFAULTS: dict[str, Any] = {
    "min_novelty": _DEFAULT_MIN_NOVELTY,        # below this -> rejected
    "duplicate_similarity": 0.85,               # at/above this -> is_duplicate
    "cosine_weight": 0.6,                        # blend cosine vs jaccard
    "catalog_sources": ["canvas_registry", "manifests", "goals"],
    "embedding": {"enabled": False, "rerank_top_n": 5},
}

# Minimal English + structural stopword set. Kept deliberately small so that
# distinctive capability terms survive and near-duplicates still match sharply.
_STOPWORDS = frozenset(
    """
    a an the and or of to for in on with from by as at is are be that this these those
    it its their our your his her my we you they i he she them us into over under via
    using use uses used can will would should could may might also not no yes than then
    each per any all both more most some such other same new across within without
    canvas design the across capability capabilities feature features
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# =========================================================================
# CONFIG
# =========================================================================
def _load_config() -> dict[str, Any]:
    """Load the ``novelty`` section of args/foundry_config.yaml, merged over DEFAULTS."""
    cfg = dict(DEFAULTS)
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:
        return cfg
    novelty = doc.get("novelty") if isinstance(doc, dict) else None
    if isinstance(novelty, dict):
        for key, val in novelty.items():
            if key == "embedding" and isinstance(val, dict):
                merged = dict(DEFAULTS["embedding"])
                merged.update(val)
                cfg["embedding"] = merged
            else:
                cfg[key] = val
    return cfg


# =========================================================================
# TOKENIZATION + SIMILARITY (deterministic core)
# =========================================================================
def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens."""
    if not text:
        return []
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    ]


def _term_freq(tokens: Iterable[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for tok in tokens:
        tf[tok] = tf.get(tok, 0) + 1
    return tf


def _cosine(tf_a: dict[str, int], tf_b: dict[str, int]) -> float:
    if not tf_a or not tf_b:
        return 0.0
    common = set(tf_a) & set(tf_b)
    if not common:
        return 0.0
    dot = sum(tf_a[t] * tf_b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in tf_a.values()))
    norm_b = math.sqrt(sum(v * v for v in tf_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _similarity(tokens_a: list[str], tokens_b: list[str], cosine_weight: float = 0.6) -> float:
    """Blended token similarity in [0, 1]: w*cosine + (1-w)*jaccard."""
    if not tokens_a or not tokens_b:
        return 0.0
    cos = _cosine(_term_freq(tokens_a), _term_freq(tokens_b))
    jac = _jaccard(set(tokens_a), set(tokens_b))
    w = min(max(cosine_weight, 0.0), 1.0)
    return w * cos + (1.0 - w) * jac


# =========================================================================
# CAPABILITY CATALOG (built from EXISTING assets)
# =========================================================================
class CatalogEntry:
    """One existing-capability reference the concept is deduped against."""

    __slots__ = ("id", "kind", "name", "text", "tokens")

    def __init__(self, entry_id: str, kind: str, name: str, text: str):
        self.id = entry_id
        self.kind = kind          # 'canvas' | 'tool' | 'goal'
        self.name = name
        self.text = text
        self.tokens = _tokenize(text)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name}


def _catalog_from_canvas_registry() -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    try:
        try:
            from icdev.tools.canvas import canvas_registry as cr  # type: ignore
        except Exception:
            from tools.canvas import canvas_registry as cr  # type: ignore
    except Exception:
        return entries
    display = getattr(cr, "CANVAS_DISPLAY_NAMES", {})
    flows = getattr(cr, "CANVAS_FLOW_NOUNS", {})
    for key, name in display.items():
        flow = flows.get(key, "")
        entries.append(
            CatalogEntry(f"canvas:{key}", "canvas", name, f"{name} {flow} {key}")
        )
    return entries


def _looks_like_separator(cells: list[str]) -> bool:
    """True for a markdown table separator row like ``|---|:--:|``."""
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def _parse_markdown_table_rows(text: str) -> list[list[str]]:
    """Return data rows from every pipe table in *text* (header + separator stripped)."""
    rows: list[list[str]] = []
    in_table = False          # have we consumed the header row of the current block?
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if _looks_like_separator(cells):
            in_table = True
            continue
        if not in_table:
            # First pipe row of a block is the header — skip it and arm the block.
            in_table = True
            continue
        rows.append(cells)
    return rows


def _catalog_from_manifests() -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    manifest_dir = BASE_DIR / "tools" / "manifest"
    if not manifest_dir.is_dir():
        return entries
    for md in sorted(manifest_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        for cells in _parse_markdown_table_rows(text):
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or name.lower() in ("tool", "name"):
                continue
            # Description is usually the 3rd column; fall back to the longest cell.
            desc = cells[2] if len(cells) > 2 else ""
            if not desc:
                desc = max(cells[1:], key=len, default="")
            entries.append(
                CatalogEntry(f"tool:{md.stem}:{name}", "tool", name, f"{name} {desc}")
            )
    return entries


def _catalog_from_goals() -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    goals_manifest = BASE_DIR / "goals" / "manifest.md"
    if not goals_manifest.is_file():
        return entries
    try:
        text = goals_manifest.read_text(encoding="utf-8")
    except Exception:
        return entries
    for cells in _parse_markdown_table_rows(text):
        if len(cells) < 2:
            continue
        name = cells[0]
        if not name or name.lower() in ("goal", "name"):
            continue
        desc = cells[2] if len(cells) > 2 else (cells[1] if len(cells) > 1 else "")
        entries.append(CatalogEntry(f"goal:{name}", "goal", name, f"{name} {desc}"))
    return entries


_CATALOG_CACHE: list[CatalogEntry] | None = None
_SOURCE_BUILDERS = {
    "canvas_registry": _catalog_from_canvas_registry,
    "manifests": _catalog_from_manifests,
    "goals": _catalog_from_goals,
}


def build_catalog(
    *, sources: Iterable[str] | None = None, force_rebuild: bool = False
) -> list[CatalogEntry]:
    """Build (and cache) the capability catalog from existing ICDEV assets.

    *sources* defaults to all of canvas_registry / manifests / goals. The full
    catalog is cached module-side; ``force_rebuild=True`` or a non-default
    *sources* selection bypasses the cache.
    """
    global _CATALOG_CACHE
    if sources is None:
        if _CATALOG_CACHE is not None and not force_rebuild:
            return _CATALOG_CACHE
        selected = list(_SOURCE_BUILDERS)
    else:
        selected = [s for s in sources if s in _SOURCE_BUILDERS]

    catalog: list[CatalogEntry] = []
    for src in selected:
        try:
            catalog.extend(_SOURCE_BUILDERS[src]())
        except Exception:
            continue

    if sources is None:
        _CATALOG_CACHE = catalog
    return catalog


# =========================================================================
# CONCEPT TEXT
# =========================================================================
def _concept_text(concept: dict[str, Any]) -> str:
    """Capability-bearing prose for a concept draft (synthesizer output shape)."""
    parts = [
        str(concept.get("name", "")),
        str(concept.get("proposed_capability", "")),
        str(concept.get("problem_statement", "")),
        str(concept.get("target_users", "")),
    ]
    return " ".join(p for p in parts if p).strip()


# =========================================================================
# OPTIONAL EMBEDDING RE-RANK (air-gap safe; never blocks)
# =========================================================================
def _embedding_rerank(
    concept_text: str, candidates: list[tuple[CatalogEntry, float]], top_n: int
) -> tuple[CatalogEntry, float] | None:
    """Re-score the *top_n* deterministic candidates with embedding cosine.

    Returns the single best (entry, similarity) or ``None`` if embeddings are
    unavailable for any reason — the caller then keeps the deterministic result.
    """
    if os.environ.get("ICDEV_NO_LLM"):
        return None
    pool = candidates[: max(1, top_n)]
    if not pool:
        return None
    try:
        try:
            from icdev.tools.llm.router import LLMRouter  # type: ignore
        except Exception:
            from tools.llm.router import LLMRouter  # type: ignore
        provider = LLMRouter().get_embedding_provider()
        texts = [concept_text] + [e.text for e, _ in pool]
        if hasattr(provider, "embed_batch"):
            vectors = provider.embed_batch(texts)
        else:
            vectors = [provider.embed(t) for t in texts]
    except Exception:
        return None
    if not vectors or len(vectors) != len(pool) + 1:
        return None

    def _vec_cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    base = vectors[0]
    best: tuple[CatalogEntry, float] | None = None
    for (entry, _det), vec in zip(pool, vectors[1:]):
        sim = max(0.0, min(1.0, _vec_cosine(base, vec)))
        if best is None or sim > best[1]:
            best = (entry, sim)
    return best


# =========================================================================
# PUBLIC: score_novelty
# =========================================================================
def score_novelty(
    concept: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    catalog: list[CatalogEntry] | None = None,
) -> dict[str, Any]:
    """Score a concept's novelty against the existing capability catalog.

    Returns a dict with at least::

        {
          "novelty_score": float,   # 1 - max_similarity, in [0, 1]
          "nearest": {id, kind, name, similarity} | None,
          "is_duplicate": bool,
          "rejected": bool,
          "reject_reason": "duplicate" | "low_novelty" | None,
          "max_similarity": float,
          "method": "deterministic" | "embedding",
          "catalog_size": int,
        }
    """
    cfg = config if config is not None else _load_config()
    min_novelty = float(cfg.get("min_novelty", DEFAULTS["min_novelty"]))
    dup_threshold = float(cfg.get("duplicate_similarity", DEFAULTS["duplicate_similarity"]))
    cosine_weight = float(cfg.get("cosine_weight", DEFAULTS["cosine_weight"]))

    cat = catalog if catalog is not None else build_catalog()
    concept_tokens = _tokenize(_concept_text(concept))

    # Empty concept can't be novel-scored meaningfully -> treat as maximally novel
    # but flag it; downstream scorer/deliberator will catch the empty draft.
    if not concept_tokens or not cat:
        return {
            "novelty_score": 1.0,
            "nearest": None,
            "is_duplicate": False,
            "rejected": False,
            "reject_reason": None,
            "max_similarity": 0.0,
            "method": "deterministic",
            "catalog_size": len(cat),
        }

    scored = [
        (entry, _similarity(concept_tokens, entry.tokens, cosine_weight)) for entry in cat
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best_entry, max_sim = scored[0]
    method = "deterministic"

    # Optional embedding re-rank over the nearest deterministic candidates.
    emb_cfg = cfg.get("embedding", DEFAULTS["embedding"]) or {}
    if isinstance(emb_cfg, dict) and emb_cfg.get("enabled"):
        refined = _embedding_rerank(
            _concept_text(concept), scored, int(emb_cfg.get("rerank_top_n", 5))
        )
        if refined is not None and refined[1] > max_sim:
            best_entry, max_sim = refined
            method = "embedding"

    max_sim = max(0.0, min(1.0, max_sim))
    novelty_score = round(1.0 - max_sim, 4)
    is_duplicate = max_sim >= dup_threshold

    reject_reason: str | None = None
    if is_duplicate:
        reject_reason = REJECT_DUPLICATE
    elif novelty_score < min_novelty:
        reject_reason = REJECT_LOW_NOVELTY

    return {
        "novelty_score": novelty_score,
        "nearest": {**best_entry.as_dict(), "similarity": round(max_sim, 4)},
        "is_duplicate": is_duplicate,
        "rejected": reject_reason is not None,
        "reject_reason": reject_reason,
        "max_similarity": round(max_sim, 4),
        "method": method,
        "catalog_size": len(cat),
    }


# =========================================================================
# PUBLIC: apply_novelty_gate (annotate the concept in place)
# =========================================================================
def apply_novelty_gate(
    concept: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    catalog: list[CatalogEntry] | None = None,
) -> dict[str, Any]:
    """Score *concept* and stamp the verdict onto it (returns the same dict).

    Sets ``novelty_score`` and ``nearest`` always; on rejection also sets
    ``status='rejected'`` and ``reject_reason``. Persistence to ``foundry_concepts``
    is the scorer/db tasks' responsibility — this is pure in-memory annotation.
    """
    result = score_novelty(concept, config=config, catalog=catalog)
    concept["novelty_score"] = result["novelty_score"]
    concept["nearest"] = result["nearest"]
    if result["rejected"]:
        concept["status"] = "rejected"
        concept["reject_reason"] = result["reject_reason"]
    return concept


# =========================================================================
# CLI
# =========================================================================
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACF novelty gate — dedup vs capability catalog")
    parser.add_argument("--concept-json", help="Concept draft as a JSON object")
    parser.add_argument("--catalog", action="store_true", help="Print the capability catalog")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    if args.catalog:
        cat = build_catalog()
        payload = {"catalog_size": len(cat), "entries": [e.as_dict() for e in cat]}
        print(json.dumps(payload, indent=None if args.json else 2))
        return 0

    if not args.concept_json:
        parser.error("provide --concept-json '{...}' or --catalog")

    try:
        concept = json.loads(args.concept_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --concept-json: {exc}")
        return 2

    result = score_novelty(concept)
    print(json.dumps(result, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
