#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Search Engine — Hybrid keyword + semantic search over marketplace assets.

Combines BM25 keyword scoring with semantic vector search for relevance-ranked
discovery of marketplace assets (skills, goals, hardprompts, context, args,
compliance extensions).

Adapted from tools/memory/hybrid_search.py pattern. Default weights:
0.6 BM25 + 0.4 semantic (tuned for marketplace asset discovery where exact
keyword matches on names/tags carry higher weight than memory recall).

Architecture:
    - BM25 keyword search: tokenize query + asset text (name, description, tags),
      score via rank_bm25 library or TF-IDF fallback
    - Semantic search: cosine similarity between query embedding and stored
      marketplace_embeddings vectors
    - Combined: final_score = bm25_weight * bm25_norm + semantic_weight * sem_norm
    - Filters applied post-scoring: asset_type, impact_level, catalog_tier, tenant_id

Embedding providers (in priority order):
    1. Ollama nomic-embed-text (air-gapped, 768 dims) — POST http://localhost:11434/api/embeddings
    2. Hashlib-based deterministic pseudo-vectors (zero deps, 256 dims) — fallback

Usage:
    # Search published assets
    python tools/marketplace/search_engine.py --search "STIG checker for Oracle" --json

    # Search with filters
    python tools/marketplace/search_engine.py --search "compliance scanner" \\
        --asset-type compliance --impact-level IL5 --json

    # Search with custom weights
    python tools/marketplace/search_engine.py --search "BDD test generator" \\
        --bm25-weight 0.8 --semantic-weight 0.2 --json

    # Index a single asset for semantic search
    python tools/marketplace/search_engine.py --index "asset-abc123" --json

    # Reindex all published assets
    python tools/marketplace/search_engine.py --reindex-all --json
"""

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# ---------------------------------------------------------------------------
# Graceful imports (all optional with fallbacks)
# ---------------------------------------------------------------------------
try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_EMBED_DIMS = 768
FALLBACK_EMBED_DIMS = 256

# BM25 tuning parameters
BM25_K1 = 1.5
BM25_B = 0.75


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None):
    """Write an audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                details=details,
                db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------
def _tokenize(text):
    """Tokenize text: lowercase, split on non-alphanumeric characters.

    Returns list of non-empty tokens.
    """
    return [t for t in re.split(r'[^a-z0-9]+', text.lower()) if t]


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------
def _bm25_score(query_terms, document_text):
    """Compute BM25 relevance score for a single document.

    Uses rank_bm25 library when available. Falls back to simple TF-IDF.

    Args:
        query_terms: List of tokenized query terms.
        document_text: Raw document text string.

    Returns:
        Float score (unnormalized).
    """
    doc_tokens = _tokenize(document_text)
    if not doc_tokens or not query_terms:
        return 0.0

    score = 0.0
    len(doc_tokens)

    # Count term frequencies in document
    tf_map = {}
    for token in doc_tokens:
        tf_map[token] = tf_map.get(token, 0) + 1

    for term in query_terms:
        tf = tf_map.get(term, 0)
        if tf == 0:
            continue

        # TF component with BM25 saturation
        tf_component = (tf * (BM25_K1 + 1)) / (tf + BM25_K1)

        # Simple IDF approximation: boost rarer terms
        # Without corpus stats we treat each doc independently;
        # IDF normalization happens at the corpus level in search_assets.
        score += tf_component

    return score


def _bm25_score_corpus(query_terms, documents):
    """Score all documents in a corpus using BM25.

    Uses rank_bm25 library when available for proper IDF across the corpus.
    Falls back to per-document TF-IDF with log-IDF estimation.

    Args:
        query_terms: List of tokenized query terms.
        documents: List of raw document text strings.

    Returns:
        List of float scores (one per document).
    """
    if not documents or not query_terms:
        return [0.0] * len(documents)

    if _HAS_BM25:
        tokenized_corpus = [_tokenize(doc) for doc in documents]
        bm25 = BM25Okapi(tokenized_corpus, k1=BM25_K1, b=BM25_B)
        scores = [float(s) for s in bm25.get_scores(query_terms)]
        # BM25Okapi returns 0 for all docs when query terms appear in >= 50%
        # of the corpus (negative IDF clamped to 0). Fall back to TF scoring.
        if max(scores) <= 0:
            for i, doc_tokens in enumerate(tokenized_corpus):
                tf = sum(doc_tokens.count(t) for t in query_terms)
                if tf > 0:
                    scores[i] = tf / max(len(doc_tokens), 1)
        return scores

    # Fallback: TF-IDF with proper IDF across the corpus
    n_docs = len(documents)
    tokenized_corpus = [_tokenize(doc) for doc in documents]

    # Compute document frequencies for query terms
    df = {}
    for term in query_terms:
        df[term] = sum(1 for doc_tokens in tokenized_corpus if term in doc_tokens)

    # Compute average document length
    avg_dl = sum(len(doc) for doc in tokenized_corpus) / max(n_docs, 1)

    scores = []
    for doc_tokens in tokenized_corpus:
        doc_len = len(doc_tokens)
        if not doc_tokens:
            scores.append(0.0)
            continue

        # Term frequency map
        tf_map = {}
        for token in doc_tokens:
            tf_map[token] = tf_map.get(token, 0) + 1

        score = 0.0
        for term in query_terms:
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue

            # IDF: log(N / (1 + df))
            idf = math.log(n_docs / (1 + df.get(term, 0)))

            # BM25 TF normalization
            numerator = tf * (BM25_K1 + 1)
            denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / max(avg_dl, 1))
            tf_norm = numerator / denominator

            score += idf * tf_norm

        scores.append(score)

    return scores


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------
def _cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors.

    Uses numpy when available for performance. Falls back to stdlib math.

    Args:
        vec_a: List or array of floats.
        vec_b: List or array of floats.

    Returns:
        Float in [-1, 1], or 0.0 if either vector is zero.
    """
    if _HAS_NUMPY:
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # Pure Python fallback
    if len(vec_a) != len(vec_b):
        min_len = min(len(vec_a), len(vec_b))
        vec_a = vec_a[:min_len]
        vec_b = vec_b[:min_len]

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------
def _embedding_to_blob(embedding):
    """Convert list of floats to BLOB for SQLite storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _blob_to_embedding(blob):
    """Convert BLOB back to list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _generate_embedding_ollama(text):
    """Generate embedding via Ollama nomic-embed-text (air-gapped).

    Returns list of floats (768 dims) or None on failure.
    """
    if not _HAS_REQUESTS:
        # Try urllib as last resort for HTTP
        try:
            import urllib.request
            import urllib.error
            url = f"{OLLAMA_BASE_URL}/api/embeddings"
            payload = json.dumps({
                "model": OLLAMA_EMBED_MODEL,
                "prompt": text,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("embedding")
        except Exception:
            return None

    try:
        resp = _requests.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("embedding")
    except Exception:
        pass
    return None


def _generate_embedding_fallback(text):
    """Generate deterministic pseudo-embedding using hashlib.

    Creates a 256-dimensional vector by hashing each word and converting
    hash bytes to normalized float values in [-1, 1]. Provides consistent
    results across runs (same text = same vector). Zero external dependencies.

    Args:
        text: Input text string.

    Returns:
        List of 256 floats.
    """
    dims = FALLBACK_EMBED_DIMS
    # Initialize accumulator
    vector = [0.0] * dims

    tokens = _tokenize(text)
    if not tokens:
        return vector

    for token in tokens:
        # Hash each token to get deterministic bytes
        hashlib.sha256(token.encode("utf-8")).digest()
        # Extend hash if needed (SHA-256 = 32 bytes, we need 256 dims)
        # Use multiple rounds with index suffix
        token_vec = []
        for chunk_idx in range(0, dims, 32):
            chunk_hash = hashlib.sha256(
                f"{token}:{chunk_idx}".encode("utf-8")
            ).digest()
            for byte_val in chunk_hash:
                if len(token_vec) >= dims:
                    break
                # Map byte [0, 255] to float [-1, 1]
                token_vec.append((byte_val / 127.5) - 1.0)

        # Accumulate
        for i in range(dims):
            vector[i] += token_vec[i]

    # Normalize to unit vector
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude > 0:
        vector = [v / magnitude for v in vector]

    return vector


def generate_embedding(text, db_path=None):
    """Generate embedding vector for text.

    Tries Ollama nomic-embed-text first (air-gapped, 768 dims).
    Falls back to hashlib-based deterministic pseudo-vectors (256 dims).

    Args:
        text: Input text to embed.
        db_path: Unused, included for API consistency.

    Returns:
        Tuple of (embedding_list, model_name, dimensions).
    """
    # Try Ollama first
    embedding = _generate_embedding_ollama(text)
    if embedding is not None:
        return embedding, OLLAMA_EMBED_MODEL, OLLAMA_EMBED_DIMS

    # Fallback: deterministic hash-based vectors
    embedding = _generate_embedding_fallback(text)
    return embedding, "hashlib-fallback", FALLBACK_EMBED_DIMS


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def index_asset(asset_id, db_path=None):
    """Index a single asset for semantic search.

    Reads asset name + description + tags, generates embedding, and upserts
    into marketplace_embeddings. Uses content_hash for deduplication (skips
    re-embedding if content unchanged).

    Args:
        asset_id: The marketplace asset ID.
        db_path: Optional database path override.

    Returns:
        Dict with indexing result.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, description, tags FROM marketplace_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "error": f"Asset not found: {asset_id}"}

        # Build searchable text from name + description + tags
        parts = [row["name"] or "", row["description"] or ""]
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
                if isinstance(tags, list):
                    parts.extend(tags)
                elif isinstance(tags, str):
                    parts.append(tags)
            except (json.JSONDecodeError, TypeError):
                parts.append(str(row["tags"]))

        content_text = " ".join(parts)
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()

        # Check if already indexed with same content
        existing = conn.execute(
            "SELECT content_hash FROM marketplace_embeddings WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()

        if existing and existing["content_hash"] == content_hash:
            return {
                "status": "skipped",
                "asset_id": asset_id,
                "reason": "Content unchanged (hash match)",
            }

        # Generate embedding
        embedding, model_name, dimensions = generate_embedding(content_text)
        embedding_blob = _embedding_to_blob(embedding)

        # Upsert: delete old + insert new (SQLite doesn't have ON CONFLICT for all cases)
        if existing:
            conn.execute(
                "DELETE FROM marketplace_embeddings WHERE asset_id = ?",
                (asset_id,),
            )

        conn.execute(
            """INSERT INTO marketplace_embeddings
               (asset_id, content_hash, embedding, embedding_model, embedding_dimensions)
               VALUES (?, ?, ?, ?, ?)""",
            (asset_id, content_hash, embedding_blob, model_name, dimensions),
        )
        conn.commit()

        return {
            "status": "indexed",
            "asset_id": asset_id,
            "content_hash": content_hash,
            "model": model_name,
            "dimensions": dimensions,
        }
    finally:
        conn.close()


def reindex_all(db_path=None):
    """Reindex all published marketplace assets.

    Iterates over all assets with status='published', generates embeddings,
    and stores them in marketplace_embeddings.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with reindex summary.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM marketplace_assets WHERE status = 'published'"
        ).fetchall()
    finally:
        conn.close()

    results = {"total": len(rows), "indexed": 0, "skipped": 0, "errors": 0, "details": []}

    for row in rows:
        try:
            result = index_asset(row["id"], db_path)
            status = result.get("status", "error")
            if status == "indexed":
                results["indexed"] += 1
            elif status == "skipped":
                results["skipped"] += 1
            else:
                results["errors"] += 1
            results["details"].append(result)
        except Exception as e:
            results["errors"] += 1
            results["details"].append({
                "status": "error",
                "asset_id": row["id"],
                "error": str(e),
            })

    _audit(
        event_type="marketplace_search_reindex",
        actor="marketplace-search-engine",
        action=f"Reindexed {results['indexed']}/{results['total']} marketplace assets",
        details={
            "indexed": results["indexed"],
            "skipped": results["skipped"],
            "errors": results["errors"],
        },
    )

    return results


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------
def search_assets(query, asset_type=None, impact_level=None, catalog_tier=None,
                  tenant_id=None, limit=50, bm25_weight=0.6, semantic_weight=0.4,
                  db_path=None):
    """Hybrid search over published marketplace assets.

    Combines BM25 keyword scoring with semantic vector similarity for
    relevance-ranked results. Applies optional filters after scoring.

    Args:
        query: Search query string.
        asset_type: Filter by asset type (skill, goal, hardprompt, etc.).
        impact_level: Filter by impact level (IL2, IL4, IL5, IL6).
        catalog_tier: Filter by catalog tier (tenant_local, central_vetted).
        tenant_id: Filter by publisher tenant ID.
        limit: Maximum number of results to return.
        bm25_weight: Weight for BM25 keyword score (default 0.6).
        semantic_weight: Weight for semantic similarity score (default 0.4).
        db_path: Optional database path override.

    Returns:
        Dict with ranked results list and metadata.
    """
    conn = _get_db(db_path)
    try:
        # Build query for published assets with optional filters
        sql = """SELECT id, slug, name, display_name, asset_type, description,
                        current_version, classification, impact_level,
                        publisher_tenant_id, publisher_org, catalog_tier,
                        status, tags, compliance_controls, supported_languages,
                        download_count, install_count, avg_rating, rating_count,
                        created_at, updated_at
                 FROM marketplace_assets
                 WHERE status = 'published'"""
        params = []

        if asset_type:
            sql += " AND asset_type = ?"
            params.append(asset_type)
        if impact_level:
            sql += " AND impact_level = ?"
            params.append(impact_level)
        if catalog_tier:
            sql += " AND catalog_tier = ?"
            params.append(catalog_tier)
        if tenant_id:
            sql += " AND publisher_tenant_id = ?"
            params.append(tenant_id)

        rows = conn.execute(sql, params).fetchall()

        if not rows:
            return {
                "query": query,
                "results": [],
                "total": 0,
                "search_method": "none",
                "bm25_weight": bm25_weight,
                "semantic_weight": semantic_weight,
            }

        # Convert rows to dicts and build searchable documents
        assets = []
        documents = []
        for row in rows:
            asset = dict(row)
            # Parse JSON fields
            for field in ("tags", "compliance_controls", "supported_languages"):
                if asset.get(field):
                    try:
                        asset[field] = json.loads(asset[field])
                    except (json.JSONDecodeError, TypeError):
                        pass

            assets.append(asset)

            # Build document text: name + description + tags
            parts = [asset.get("name", ""), asset.get("description", "")]
            if isinstance(asset.get("tags"), list):
                parts.extend(asset["tags"])
            elif isinstance(asset.get("tags"), str):
                parts.append(asset["tags"])
            documents.append(" ".join(p for p in parts if p))

        # --- BM25 keyword scoring ---
        query_terms = _tokenize(query)
        bm25_scores = _bm25_score_corpus(query_terms, documents)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores) if bm25_scores else 0.0
        if max_bm25 > 0:
            bm25_norm = [s / max_bm25 for s in bm25_scores]
        else:
            bm25_norm = [0.0] * len(bm25_scores)

        # --- Semantic scoring ---
        semantic_norm = None
        search_method = "keyword_only"

        # Load stored embeddings
        asset_ids = [a["id"] for a in assets]
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            emb_rows = conn.execute(
                f"""SELECT asset_id, embedding
                    FROM marketplace_embeddings
                    WHERE asset_id IN ({placeholders})""",
                asset_ids,
            ).fetchall()

            stored_embeddings = {r["asset_id"]: r["embedding"] for r in emb_rows}

            if stored_embeddings:
                # Generate query embedding
                query_embedding, _, _ = generate_embedding(query)

                semantic_scores = []
                for asset in assets:
                    emb_blob = stored_embeddings.get(asset["id"])
                    if emb_blob is None:
                        semantic_scores.append(0.0)
                        continue
                    stored_emb = _blob_to_embedding(emb_blob)
                    sim = _cosine_similarity(query_embedding, stored_emb)
                    # Clamp to [0, 1] (cosine sim can be negative)
                    semantic_scores.append(max(0.0, sim))

                # Normalize semantic scores to [0, 1]
                max_sem = max(semantic_scores) if semantic_scores else 0.0
                if max_sem > 0:
                    semantic_norm = [s / max_sem for s in semantic_scores]
                else:
                    semantic_norm = [0.0] * len(semantic_scores)

                search_method = "hybrid"

        # --- Combine scores ---
        combined_results = []
        for i, asset in enumerate(assets):
            bm25_s = bm25_norm[i]

            if semantic_norm is not None:
                sem_s = semantic_norm[i]
                final_score = (bm25_weight * bm25_s) + (semantic_weight * sem_s)
            else:
                # No semantic available, use BM25 only (full weight)
                final_score = bm25_s
                sem_s = None

            # Skip zero-score results unless query is very short
            if final_score <= 0 and len(query_terms) > 0:
                continue

            combined_results.append({
                "asset_id": asset["id"],
                "slug": asset["slug"],
                "name": asset["name"],
                "display_name": asset.get("display_name"),
                "asset_type": asset["asset_type"],
                "description": asset["description"],
                "current_version": asset["current_version"],
                "classification": asset["classification"],
                "impact_level": asset["impact_level"],
                "publisher_tenant_id": asset.get("publisher_tenant_id"),
                "publisher_org": asset.get("publisher_org"),
                "catalog_tier": asset["catalog_tier"],
                "tags": asset.get("tags"),
                "compliance_controls": asset.get("compliance_controls"),
                "supported_languages": asset.get("supported_languages"),
                "download_count": asset.get("download_count", 0),
                "install_count": asset.get("install_count", 0),
                "avg_rating": asset.get("avg_rating", 0.0),
                "rating_count": asset.get("rating_count", 0),
                "relevance_score": round(final_score, 4),
                "bm25_score": round(bm25_s, 4),
                "semantic_score": round(sem_s, 4) if sem_s is not None else None,
            })

        # Sort by relevance score descending
        combined_results.sort(key=lambda x: x["relevance_score"], reverse=True)

        # Apply limit
        combined_results = combined_results[:limit]

        return {
            "query": query,
            "results": combined_results,
            "total": len(combined_results),
            "total_published": len(assets),
            "search_method": search_method,
            "bm25_weight": bm25_weight,
            "semantic_weight": semantic_weight,
            "bm25_available": _HAS_BM25,
            "semantic_available": semantic_norm is not None,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Marketplace Search Engine — Hybrid BM25 + Semantic Search"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Override database path")

    # Actions (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--search", metavar="QUERY",
                       help="Search published marketplace assets")
    group.add_argument("--index", metavar="ASSET_ID",
                       help="Index a single asset for semantic search")
    group.add_argument("--reindex-all", action="store_true",
                       help="Reindex all published assets")

    # Search filters
    parser.add_argument("--asset-type",
                        choices=["skill", "goal", "hardprompt", "context", "args", "compliance"],
                        help="Filter by asset type")
    parser.add_argument("--impact-level", choices=["IL2", "IL4", "IL5", "IL6"],
                        help="Filter by impact level")
    parser.add_argument("--catalog-tier", choices=["tenant_local", "central_vetted"],
                        help="Filter by catalog tier")
    parser.add_argument("--tenant-id", help="Filter by publisher tenant ID")
    parser.add_argument("--limit", type=int, default=50,
                        help="Maximum results (default: 50)")
    parser.add_argument("--bm25-weight", type=float, default=0.6,
                        help="BM25 keyword score weight (default: 0.6)")
    parser.add_argument("--semantic-weight", type=float, default=0.4,
                        help="Semantic similarity score weight (default: 0.4)")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.search:
            result = search_assets(
                query=args.search,
                asset_type=args.asset_type,
                impact_level=args.impact_level,
                catalog_tier=args.catalog_tier,
                tenant_id=args.tenant_id,
                limit=args.limit,
                bm25_weight=args.bm25_weight,
                semantic_weight=args.semantic_weight,
                db_path=db_path,
            )

        elif args.index:
            result = index_asset(asset_id=args.index, db_path=db_path)

        elif args.reindex_all:
            result = reindex_all(db_path=db_path)

        else:
            result = {"error": "No action specified"}

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if args.search:
                total = result.get("total", 0)
                method = result.get("search_method", "unknown")
                print(f"Search: \"{args.search}\" ({total} results, method: {method})")
                if not result.get("semantic_available"):
                    print("  (Semantic search unavailable -- using keyword search only)")
                print()
                for item in result.get("results", []):
                    score = item.get("relevance_score", 0)
                    name = item.get("name", "")
                    atype = item.get("asset_type", "")
                    desc = item.get("description", "")
                    il = item.get("impact_level", "")
                    slug = item.get("slug", "")
                    bm25_s = item.get("bm25_score", 0)
                    sem_s = item.get("semantic_score")
                    sem_str = f", sem:{sem_s:.3f}" if sem_s is not None else ""
                    print(f"  [{score:.3f}] {name} ({atype}, {il})")
                    print(f"    slug: {slug}  bm25:{bm25_s:.3f}{sem_str}")
                    if desc:
                        # Truncate description for display
                        desc_short = desc[:120] + "..." if len(desc) > 120 else desc
                        print(f"    {desc_short}")
                    print()
            elif args.index:
                status = result.get("status", "unknown")
                asset_id = result.get("asset_id", "")
                print(f"Index: {status} (asset: {asset_id})")
                if result.get("model"):
                    print(f"  model: {result['model']}, dims: {result.get('dimensions', '?')}")
                if result.get("reason"):
                    print(f"  reason: {result['reason']}")
            elif args.reindex_all:
                print(f"Reindex: {result.get('indexed', 0)}/{result.get('total', 0)} indexed, "
                      f"{result.get('skipped', 0)} skipped, {result.get('errors', 0)} errors")
            else:
                for k, v in result.items():
                    print(f"  {k}: {v}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
