#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Telemetry Logger — append-only audit trail for LLM interactions.

Logs all AI model interactions with SHA-256 prompt/response hashing (D218).
Supports anomaly detection (volume spikes, cost spikes) and usage summaries.
Pattern: tools/audit/audit_logger.py (append-only log_event).

CLI:
    python tools/security/ai_telemetry_logger.py --summary --json
    python tools/security/ai_telemetry_logger.py --summary --project-id proj-123 --json
    python tools/security/ai_telemetry_logger.py --anomalies --window 24 --json
"""

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class AITelemetryLogger:
    """Append-only AI interaction telemetry (D218, D6)."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH

    @staticmethod
    def hash_text(text: str) -> str:
        """SHA-256 hash for prompt/response content (D218)."""
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def log_ai_interaction(
        self,
        model_id: str,
        provider: str,
        prompt_hash: str,
        response_hash: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        function: Optional[str] = None,
        classification: str = "CUI",
        api_key_source: str = "system",
        injection_scan_result: Optional[str] = None,
    ) -> Optional[str]:
        """Log an AI interaction to the ai_telemetry table.

        Append-only per D6 — no UPDATE/DELETE.

        Returns:
            Entry ID or None if DB unavailable.
        """
        if not self._db_path.exists():
            return None

        entry_id = str(uuid.uuid4())
        logged_at = datetime.now(timezone.utc).isoformat()

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO ai_telemetry
                   (id, project_id, user_id, agent_id, model_id, provider,
                    function, prompt_hash, response_hash,
                    input_tokens, output_tokens, thinking_tokens,
                    latency_ms, cost_usd, classification, api_key_source,
                    injection_scan_result, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id, project_id, user_id, agent_id,
                    model_id, provider, function,
                    prompt_hash, response_hash,
                    input_tokens, output_tokens, thinking_tokens,
                    latency_ms, cost_usd, classification, api_key_source,
                    injection_scan_result, logged_at,
                ),
            )
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    def detect_anomalies(self, window_hours: int = 24) -> List[Dict]:
        """Detect anomalies in AI usage within a time window.

        Checks for:
          - Volume spikes (>2x average hourly volume)
          - Cost spikes (>3x average hourly cost)
          - Unusual model usage patterns

        Returns:
            List of anomaly dicts.
        """
        anomalies = []
        if not self._db_path.exists():
            return anomalies

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

        try:
            conn = sqlite3.connect(str(self._db_path))

            # Total volume in window
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(input_tokens + output_tokens), 0) "
                "FROM ai_telemetry WHERE logged_at >= ?",
                (cutoff,),
            ).fetchone()
            volume = row[0] if row else 0
            total_cost = row[1] if row else 0.0
            total_tokens = row[2] if row else 0

            # Hourly average (from all-time data, excluding current window)
            row_all = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM ai_telemetry WHERE logged_at < ?",
                (cutoff,),
            ).fetchone()
            historical_count = row_all[0] if row_all else 0

            if historical_count > 0:
                # Get time range of historical data
                row_range = conn.execute(
                    "SELECT MIN(logged_at), MAX(logged_at) FROM ai_telemetry WHERE logged_at < ?",
                    (cutoff,),
                ).fetchone()
                if row_range and row_range[0] and row_range[1]:
                    try:
                        t_min = datetime.fromisoformat(row_range[0].replace("Z", "+00:00"))
                        t_max = datetime.fromisoformat(row_range[1].replace("Z", "+00:00"))
                        hours_span = max(1, (t_max - t_min).total_seconds() / 3600)
                        avg_hourly_vol = historical_count / hours_span
                        avg_hourly_cost = (row_all[1] or 0) / hours_span
                        current_hourly_vol = volume / max(1, window_hours)
                        current_hourly_cost = total_cost / max(1, window_hours)

                        if avg_hourly_vol > 0 and current_hourly_vol > 2 * avg_hourly_vol:
                            anomalies.append({
                                "type": "volume_spike",
                                "severity": "high",
                                "message": f"Current hourly volume ({current_hourly_vol:.1f}) > 2x average ({avg_hourly_vol:.1f})",
                                "current_value": current_hourly_vol,
                                "threshold": avg_hourly_vol * 2,
                            })

                        if avg_hourly_cost > 0 and current_hourly_cost > 3 * avg_hourly_cost:
                            anomalies.append({
                                "type": "cost_spike",
                                "severity": "critical",
                                "message": f"Current hourly cost (${current_hourly_cost:.4f}) > 3x average (${avg_hourly_cost:.4f})",
                                "current_value": current_hourly_cost,
                                "threshold": avg_hourly_cost * 3,
                            })
                    except (ValueError, TypeError):
                        pass

            # Check for blocked injections in window
            blocked = conn.execute(
                "SELECT COUNT(*) FROM ai_telemetry WHERE logged_at >= ? AND injection_scan_result = 'blocked'",
                (cutoff,),
            ).fetchone()
            if blocked and blocked[0] > 0:
                anomalies.append({
                    "type": "injection_attempts",
                    "severity": "critical",
                    "message": f"{blocked[0]} blocked injection attempt(s) in last {window_hours}h",
                    "current_value": blocked[0],
                    "threshold": 0,
                })

            conn.close()
        except Exception:
            pass

        return anomalies

    def get_usage_summary(
        self,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        hours: int = 24,
    ) -> Dict:
        """Get usage summary for a project/user/time window.

        Returns:
            Dict with total_requests, total_tokens, total_cost, by_provider, by_model.
        """
        if not self._db_path.exists():
            return {"error": "Database not found"}

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        try:
            conn = sqlite3.connect(str(self._db_path))

            where_parts = ["logged_at >= ?"]
            params = [cutoff]
            if project_id:
                where_parts.append("project_id = ?")
                params.append(project_id)
            if user_id:
                where_parts.append("user_id = ?")
                params.append(user_id)
            where = " AND ".join(where_parts)

            # Overall summary
            row = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
                f"COALESCE(SUM(thinking_tokens), 0), COALESCE(SUM(cost_usd), 0), "
                f"COALESCE(AVG(latency_ms), 0) FROM ai_telemetry WHERE {where}",
                params,
            ).fetchone()

            # By provider
            by_provider = {}
            for prow in conn.execute(
                f"SELECT provider, COUNT(*), COALESCE(SUM(cost_usd), 0), "
                f"COALESCE(SUM(input_tokens + output_tokens), 0) "
                f"FROM ai_telemetry WHERE {where} GROUP BY provider",
                params,
            ):
                by_provider[prow[0]] = {
                    "requests": prow[1],
                    "cost_usd": round(prow[2], 6),
                    "tokens": prow[3],
                }

            # By model
            by_model = {}
            for mrow in conn.execute(
                f"SELECT model_id, COUNT(*), COALESCE(SUM(cost_usd), 0) "
                f"FROM ai_telemetry WHERE {where} GROUP BY model_id",
                params,
            ):
                by_model[mrow[0]] = {
                    "requests": mrow[1],
                    "cost_usd": round(mrow[2], 6),
                }

            conn.close()

            return {
                "total_requests": row[0],
                "total_input_tokens": row[1],
                "total_output_tokens": row[2],
                "total_thinking_tokens": row[3],
                "total_cost_usd": round(row[4], 6),
                "avg_latency_ms": round(row[5], 2),
                "by_provider": by_provider,
                "by_model": by_model,
                "window_hours": hours,
                "project_id": project_id,
                "user_id": user_id,
            }
        except Exception as e:
            return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="AI Telemetry Logger — usage stats and anomaly detection")
    parser.add_argument("--summary", action="store_true", help="Show usage summary")
    parser.add_argument("--anomalies", action="store_true", help="Detect anomalies")
    parser.add_argument("--window", type=int, default=24, help="Time window in hours (default: 24)")
    parser.add_argument("--project-id", help="Filter by project ID")
    parser.add_argument("--user-id", help="Filter by user ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logger = AITelemetryLogger()

    if args.summary:
        result = logger.get_usage_summary(
            project_id=args.project_id,
            user_id=args.user_id,
            hours=args.window,
        )
    elif args.anomalies:
        result = {"anomalies": logger.detect_anomalies(window_hours=args.window)}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.summary:
            print(f"AI Usage Summary (last {args.window}h)")
            print(f"  Requests: {result.get('total_requests', 0)}")
            print(f"  Input tokens: {result.get('total_input_tokens', 0):,}")
            print(f"  Output tokens: {result.get('total_output_tokens', 0):,}")
            print(f"  Total cost: ${result.get('total_cost_usd', 0):.4f}")
            print(f"  Avg latency: {result.get('avg_latency_ms', 0):.0f}ms")
        elif args.anomalies:
            anomalies = result.get("anomalies", [])
            print(f"Anomaly Detection (last {args.window}h)")
            if anomalies:
                for a in anomalies:
                    print(f"  [{a['severity']}] {a['type']}: {a['message']}")
            else:
                print("  No anomalies detected.")


if __name__ == "__main__":
    main()
