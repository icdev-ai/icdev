"""AgentSHAP attribution runner."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[6]))


def get_recent_traces(n: int = 5) -> list[dict]:
    """Return the N most recent traces from the DB."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT trace_id, operation, started_at, duration_ms FROM otel_traces ORDER BY started_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


def run_attribution_report():
    """Run AgentSHAP on recent traces and print attribution table."""
    traces = get_recent_traces(5)
    if not traces:
        print("No traces found. Run some agent operations first.")
        return []

    results = []
    for trace in traces:
        # TODO: run AgentSHAP on trace["trace_id"]
        # TODO: print attribution table for each trace
        results.append(trace)

    # TODO: print summary: tool with highest average SHAP value
    return results


if __name__ == "__main__":
    run_attribution_report()
