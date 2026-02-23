#!/usr/bin/env python3
# CUI // SP-CTI
"""PROV-AGENT Provenance Recorder — W3C PROV standard (D287).

Records Entity/Activity/Relation provenance data in SQLite.
Auto-populates from span completion callbacks when tracing is active.

Entity types: prompt, response, document, code, report, artifact
Activity types: tool_invocation, llm_call, decision, review, generation
Relation types: wasGeneratedBy, used, wasInformedBy, wasDerivedFrom, wasAttributedTo

Usage:
    from tools.observability.provenance.prov_recorder import ProvRecorder
    recorder = ProvRecorder()

    entity_id = recorder.record_entity("prompt", "User query", content_hash="abc...")
    activity_id = recorder.record_activity("llm_call", "Claude Opus invocation")
    recorder.record_relation("wasGeneratedBy", entity_id, activity_id)
"""

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("icdev.observability.provenance")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class ProvRecorder:
    """W3C PROV-AGENT provenance recorder (D287).

    Records entities, activities, and relations in append-only SQLite tables.
    Follows NIST AI RMF MEASURE 2.5 (provenance).
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        classification: str = "CUI",
    ):
        self._db_path = db_path or DB_PATH
        self._agent_id = agent_id
        self._project_id = project_id
        self._classification = classification

    def _content_tracing_enabled(self) -> bool:
        return os.environ.get("ICDEV_CONTENT_TRACING_ENABLED", "").lower() in ("true", "1", "yes")

    def record_entity(
        self,
        entity_type: str,
        label: str,
        content: Optional[str] = None,
        content_hash: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> Optional[str]:
        """Record a provenance entity (D287).

        Args:
            entity_type: prompt, response, document, code, report, artifact.
            label: Human-readable label.
            content: Entity content (only stored if content tracing enabled).
            content_hash: SHA-256 hash (computed from content if not provided).
            attributes: Additional metadata.
            trace_id: Associated trace ID.
            span_id: Associated span ID.

        Returns:
            Entity ID or None if DB unavailable.
        """
        entity_id = str(uuid.uuid4())

        if content and not content_hash:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        stored_content = content if self._content_tracing_enabled() else None

        if not self._db_path.exists():
            return None

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO prov_entities
                   (id, entity_type, label, content_hash, content, attributes,
                    trace_id, span_id, agent_id, project_id, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, entity_type, label, content_hash, stored_content,
                    json.dumps(attributes or {}),
                    trace_id, span_id,
                    self._agent_id, self._project_id, self._classification,
                ),
            )
            conn.commit()
            conn.close()
            return entity_id
        except sqlite3.Error as e:
            logger.error("Failed to record entity: %s", e)
            return None

    def record_activity(
        self,
        activity_type: str,
        label: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> Optional[str]:
        """Record a provenance activity (D287).

        Args:
            activity_type: tool_invocation, llm_call, decision, review, generation.
            label: Human-readable label.
            start_time: ISO format start time.
            end_time: ISO format end time.
            attributes: Additional metadata.
            trace_id: Associated trace ID.
            span_id: Associated span ID.

        Returns:
            Activity ID or None.
        """
        activity_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if not self._db_path.exists():
            return None

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO prov_activities
                   (id, activity_type, label, start_time, end_time, attributes,
                    trace_id, span_id, agent_id, project_id, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    activity_id, activity_type, label,
                    start_time or now, end_time,
                    json.dumps(attributes or {}),
                    trace_id, span_id,
                    self._agent_id, self._project_id, self._classification,
                ),
            )
            conn.commit()
            conn.close()
            return activity_id
        except sqlite3.Error as e:
            logger.error("Failed to record activity: %s", e)
            return None

    def record_relation(
        self,
        relation_type: str,
        subject_id: str,
        object_id: str,
        attributes: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """Record a provenance relation (D287).

        Args:
            relation_type: wasGeneratedBy, used, wasInformedBy, wasDerivedFrom, wasAttributedTo.
            subject_id: Source entity/activity ID.
            object_id: Target entity/activity ID.
            attributes: Additional metadata.
            trace_id: Associated trace ID.

        Returns:
            True if recorded, False on failure.
        """
        if not self._db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO prov_relations
                   (relation_type, subject_id, object_id, attributes,
                    trace_id, project_id, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    relation_type, subject_id, object_id,
                    json.dumps(attributes or {}),
                    trace_id, self._project_id, self._classification,
                ),
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error as e:
            logger.error("Failed to record relation: %s", e)
            return False

    def get_lineage(
        self,
        entity_id: str,
        direction: str = "backward",
        max_depth: int = 50,
    ) -> List[Dict]:
        """Query provenance lineage for an entity.

        Args:
            entity_id: Starting entity ID.
            direction: "backward" (what produced this?) or "forward" (what did this produce?).
            max_depth: Maximum traversal depth.

        Returns:
            List of relation dicts forming the lineage chain.
        """
        if not self._db_path.exists():
            return []

        results = []
        visited = set()
        queue = [entity_id]

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row

            depth = 0
            while queue and depth < max_depth:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)

                if direction == "backward":
                    rows = conn.execute(
                        "SELECT * FROM prov_relations WHERE subject_id = ?",
                        (current,),
                    ).fetchall()
                    for row in rows:
                        results.append(dict(row))
                        queue.append(row["object_id"])
                else:
                    rows = conn.execute(
                        "SELECT * FROM prov_relations WHERE object_id = ?",
                        (current,),
                    ).fetchall()
                    for row in rows:
                        results.append(dict(row))
                        queue.append(row["subject_id"])

                depth += 1

            conn.close()
        except sqlite3.Error as e:
            logger.error("Lineage query error: %s", e)

        return results

    def export_prov_json(self, project_id: Optional[str] = None) -> Dict:
        """Export provenance data in PROV-JSON format.

        Args:
            project_id: Filter by project. Defaults to recorder's project_id.

        Returns:
            PROV-JSON dictionary.
        """
        pid = project_id or self._project_id
        if not self._db_path.exists():
            return {"entity": {}, "activity": {}, "relation": []}

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row

            where = "WHERE project_id = ?" if pid else ""
            params = (pid,) if pid else ()

            entities = conn.execute(f"SELECT * FROM prov_entities {where}", params).fetchall()
            activities = conn.execute(f"SELECT * FROM prov_activities {where}", params).fetchall()
            relations = conn.execute(f"SELECT * FROM prov_relations {where}", params).fetchall()
            conn.close()

            return {
                "prefix": {"prov": "http://www.w3.org/ns/prov#", "icdev": "urn:icdev:"},
                "entity": {
                    e["id"]: {
                        "prov:type": e["entity_type"],
                        "prov:label": e["label"],
                        "icdev:content_hash": e["content_hash"],
                    }
                    for e in entities
                },
                "activity": {
                    a["id"]: {
                        "prov:type": a["activity_type"],
                        "prov:label": a["label"],
                        "prov:startTime": a["start_time"],
                        "prov:endTime": a["end_time"],
                    }
                    for a in activities
                },
                "relation": [
                    {
                        "prov:type": r["relation_type"],
                        "prov:subject": r["subject_id"],
                        "prov:object": r["object_id"],
                    }
                    for r in relations
                ],
            }
        except sqlite3.Error as e:
            logger.error("PROV-JSON export error: %s", e)
            return {"entity": {}, "activity": {}, "relation": []}
