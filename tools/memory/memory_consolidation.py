#!/usr/bin/env python3
# CUI // SP-CTI
"""AI-driven memory consolidation (Phase 44 — D276).

Checks new memory entries against existing entries for similarity.
LLM decides: MERGE, REPLACE, KEEP_SEPARATE, UPDATE, SKIP.
Falls back to Jaccard keyword similarity when LLM unavailable.
Consolidation log is append-only (D6).

Usage:
    from tools.memory.memory_consolidation import MemoryConsolidator

    consolidator = MemoryConsolidator()
    result = consolidator.check_for_consolidation("new content", "fact", "user-1")
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("icdev.memory_consolidation")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"
ICDEV_DB_PATH = BASE_DIR / "data" / "icdev.db"

# Consolidation actions
ACTIONS = ("MERGE", "REPLACE", "KEEP_SEPARATE", "UPDATE", "SKIP")

# Jaccard thresholds (keyword fallback)
JACCARD_SKIP_THRESHOLD = 0.90
JACCARD_REPLACE_THRESHOLD = 0.80
JACCARD_KEEP_THRESHOLD = 0.75


class MemoryConsolidator:
    """Check and consolidate similar memory entries.

    Args:
        similarity_threshold: Minimum similarity for consolidation consideration.
        use_llm: Whether to use LLM for consolidation decisions.
        dry_run: If True, log recommendations but don't execute.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.75,
        use_llm: bool = True,
        dry_run: bool = False,
    ):
        self.similarity_threshold = similarity_threshold
        self.use_llm = use_llm
        self.dry_run = dry_run

    def check_for_consolidation(
        self,
        content: str,
        entry_type: str = "fact",
        user_id: str = "",
    ) -> dict:
        """Check if new content should be consolidated with existing entries.

        Returns:
            {similar_entries, recommended_action, merged_content,
             confidence, method, should_write}
        """
        # Find similar entries
        similar = self._find_similar(content, user_id)

        if not similar:
            return {
                "similar_entries": [],
                "recommended_action": "KEEP_SEPARATE",
                "merged_content": None,
                "confidence": 1.0,
                "method": "no_similar",
                "should_write": True,
            }

        # Decide action
        if self.use_llm:
            decision = self._llm_decide(content, similar)
            if decision:
                return decision

        # Keyword fallback
        return self._keyword_decide(content, similar)

    def _find_similar(
        self,
        content: str,
        user_id: str = "",
        max_candidates: int = 10,
    ) -> List[dict]:
        """Find similar existing entries via hybrid search or keyword matching."""
        similar = []

        # Try hybrid search first
        try:
            from tools.memory.hybrid_search import hybrid_search
            results = hybrid_search(
                query=content[:200],
                top_k=max_candidates,
            )
            for r in results:
                sim = r.get("score", 0)
                if sim >= self.similarity_threshold:
                    similar.append({
                        "id": r.get("id"),
                        "content": r.get("content", ""),
                        "entry_type": r.get("entry_type", ""),
                        "similarity": sim,
                    })
            return similar
        except (ImportError, Exception):
            pass

        # Fallback: Jaccard keyword search against recent entries
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, content, entry_type
                   FROM memory_entries
                   ORDER BY created_at DESC LIMIT 200"""
            ).fetchall()
            conn.close()

            content_kw = self._extract_keywords(content)
            if not content_kw:
                return []

            for row in rows:
                row_dict = dict(row)
                entry_kw = self._extract_keywords(row_dict["content"])
                if not entry_kw:
                    continue

                jaccard = self._jaccard_similarity(content_kw, entry_kw)
                if jaccard >= self.similarity_threshold:
                    similar.append({
                        "id": row_dict["id"],
                        "content": row_dict["content"],
                        "entry_type": row_dict["entry_type"],
                        "similarity": jaccard,
                    })

            # Sort by similarity descending
            similar.sort(key=lambda x: x["similarity"], reverse=True)
            return similar[:max_candidates]

        except (sqlite3.OperationalError, Exception) as exc:
            logger.debug("Keyword search failed: %s", exc)
            return []

    def _llm_decide(
        self,
        new_content: str,
        existing_entries: List[dict],
    ) -> Optional[dict]:
        """Use LLM to decide consolidation action."""
        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()

            # Build context for LLM
            entries_text = "\n".join(
                f"Entry #{e['id']} (similarity={e['similarity']:.2f}): {e['content'][:300]}"
                for e in existing_entries[:5]
            )

            prompt = f"""You are a memory consolidation system. Given a NEW entry and EXISTING similar entries, decide the best action.

NEW ENTRY: {new_content[:500]}

EXISTING ENTRIES:
{entries_text}

Choose ONE action:
- MERGE: Combine new and existing into a richer entry (return merged content)
- REPLACE: New entry supersedes existing (newer/more complete)
- KEEP_SEPARATE: Entries are related but distinct enough to keep both
- UPDATE: Modify existing entry with new information (return updated content)
- SKIP: New entry is duplicate, don't store it

Respond as JSON: {{"action": "ACTION", "target_id": <id_or_null>, "merged_content": "<text_or_null>", "reasoning": "<brief_explanation>", "confidence": <0.0-1.0>}}"""

            response = router.generate(
                function_name="memory_consolidation",
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.get("content", str(response)) if isinstance(response, dict) else str(response)

            # Parse JSON from response
            import re as _re
            json_match = _re.search(r'\{[^}]+\}', text, _re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())
                action = decision.get("action", "KEEP_SEPARATE").upper()
                if action not in ACTIONS:
                    action = "KEEP_SEPARATE"

                result = {
                    "similar_entries": existing_entries,
                    "recommended_action": action,
                    "merged_content": decision.get("merged_content"),
                    "target_id": decision.get("target_id"),
                    "confidence": decision.get("confidence", 0.7),
                    "method": "llm",
                    "reasoning": decision.get("reasoning", ""),
                    "should_write": action not in ("SKIP",),
                }

                # Log consolidation decision
                self._log_consolidation(
                    source_entry_id=None,
                    target_entry_id=decision.get("target_id"),
                    action=action,
                    method="llm",
                    similarity_score=existing_entries[0]["similarity"] if existing_entries else 0,
                    reasoning=decision.get("reasoning", ""),
                    merged_content=decision.get("merged_content"),
                )

                return result

        except (ImportError, json.JSONDecodeError, Exception) as exc:
            logger.debug("LLM consolidation unavailable: %s", exc)

        return None

    def _keyword_decide(
        self,
        new_content: str,
        existing_entries: List[dict],
    ) -> dict:
        """Fallback: decide using Jaccard keyword similarity."""
        if not existing_entries:
            return {
                "similar_entries": [],
                "recommended_action": "KEEP_SEPARATE",
                "merged_content": None,
                "confidence": 1.0,
                "method": "keyword",
                "should_write": True,
            }

        top = existing_entries[0]
        sim = top["similarity"]

        if sim >= JACCARD_SKIP_THRESHOLD:
            action = "SKIP"
            should_write = False
        elif sim >= JACCARD_REPLACE_THRESHOLD:
            action = "REPLACE"
            should_write = True
        elif sim >= JACCARD_KEEP_THRESHOLD:
            action = "KEEP_SEPARATE"
            should_write = True
        else:
            action = "KEEP_SEPARATE"
            should_write = True

        result = {
            "similar_entries": existing_entries,
            "recommended_action": action,
            "merged_content": None,
            "target_id": top.get("id"),
            "confidence": sim,
            "method": "keyword",
            "should_write": should_write,
        }

        self._log_consolidation(
            source_entry_id=None,
            target_entry_id=top.get("id"),
            action=action,
            method="keyword",
            similarity_score=sim,
            reasoning=f"Jaccard similarity={sim:.3f}",
        )

        return result

    def execute_consolidation(
        self,
        action: str,
        new_content: str,
        target_id: Optional[int] = None,
        merged_content: Optional[str] = None,
    ) -> dict:
        """Execute a consolidation action on the database.

        Returns: {status, action, target_id}
        """
        if self.dry_run:
            return {"status": "dry_run", "action": action, "target_id": target_id}

        if action == "SKIP":
            return {"status": "skipped", "action": action}

        if action == "REPLACE" and target_id:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(
                    "UPDATE memory_entries SET content = ?, updated_at = ? WHERE id = ?",
                    (new_content, datetime.now(timezone.utc).isoformat(), target_id),
                )
                conn.commit()
                conn.close()
                return {"status": "replaced", "action": action, "target_id": target_id}
            except sqlite3.OperationalError as exc:
                logger.error("Replace failed: %s", exc)

        if action in ("MERGE", "UPDATE") and target_id and merged_content:
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(
                    "UPDATE memory_entries SET content = ?, updated_at = ? WHERE id = ?",
                    (merged_content, datetime.now(timezone.utc).isoformat(), target_id),
                )
                conn.commit()
                conn.close()
                return {"status": "merged", "action": action, "target_id": target_id}
            except sqlite3.OperationalError as exc:
                logger.error("Merge/Update failed: %s", exc)

        return {"status": "no_action", "action": action}

    def consolidate_all(self, batch_size: int = 50) -> dict:
        """Run batch consolidation pass over recent entries.

        Returns: {processed, actions: {MERGE: N, REPLACE: N, ...}}
        """
        actions = {a: 0 for a in ACTIONS}
        processed = 0

        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, content, entry_type FROM memory_entries ORDER BY created_at DESC LIMIT ?",
                (batch_size,),
            ).fetchall()
            conn.close()
        except sqlite3.OperationalError:
            return {"processed": 0, "actions": actions}

        for row in rows:
            row_dict = dict(row)
            result = self.check_for_consolidation(
                row_dict["content"],
                row_dict["entry_type"],
            )
            action = result.get("recommended_action", "KEEP_SEPARATE")
            actions[action] = actions.get(action, 0) + 1
            processed += 1

            if action in ("MERGE", "REPLACE", "UPDATE") and not self.dry_run:
                self.execute_consolidation(
                    action=action,
                    new_content=row_dict["content"],
                    target_id=result.get("target_id"),
                    merged_content=result.get("merged_content"),
                )

        return {"processed": processed, "actions": actions}

    def get_stats(self) -> dict:
        """Get consolidation statistics from the log."""
        try:
            conn = sqlite3.connect(str(ICDEV_DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT action, method, COUNT(*) as cnt,
                          AVG(similarity_score) as avg_sim
                   FROM memory_consolidation_log
                   GROUP BY action, method"""
            ).fetchall()
            conn.close()
            return {"stats": [dict(r) for r in rows]}
        except sqlite3.OperationalError:
            return {"stats": []}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(text: str) -> set:
        """Extract keywords from text."""
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        stopwords = frozenset({
            "the", "a", "an", "is", "are", "was", "were", "have", "has",
            "had", "do", "does", "did", "will", "would", "could", "should",
            "this", "that", "not", "and", "but", "for", "with", "from",
        })
        return {w for w in words if w not in stopwords}

    @staticmethod
    def _jaccard_similarity(set1: set, set2: set) -> float:
        """Compute Jaccard similarity between two sets."""
        if not set1 and not set2:
            return 1.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    def _log_consolidation(
        self,
        source_entry_id: Optional[int] = None,
        target_entry_id: Optional[int] = None,
        action: str = "KEEP_SEPARATE",
        method: str = "keyword",
        similarity_score: float = 0.0,
        reasoning: str = "",
        merged_content: Optional[str] = None,
    ) -> None:
        """Log consolidation decision (append-only, D6)."""
        try:
            conn = sqlite3.connect(str(ICDEV_DB_PATH))
            conn.execute(
                """INSERT INTO memory_consolidation_log
                   (source_entry_id, target_entry_id, action, method,
                    similarity_score, reasoning, merged_content, dry_run, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_entry_id, target_entry_id, action, method,
                    similarity_score, reasoning,
                    merged_content[:2000] if merged_content else None,
                    1 if self.dry_run else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug("Consolidation log write skipped: %s", exc)
