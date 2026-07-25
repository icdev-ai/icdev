# CUI // SP-CTI
"""Genesis Reflex — FathomDesk Daily Fundamentals Sweep (fdmm-fund-sweep-01).

Runs once per day (post-market). Fetches PE, P/B, ROE, gross margin, D/E,
and FCF yield from yfinance for all tickers in ad_fundamental_metrics and
upserts fresh values. Powers the PE/NAV Mispricing Universe on /value.

GREEN tier (read + upsert, no LLM). Air-gap safe when yfinance available.
COOLDOWN_HOURS = 23  (daily cadence, guards against rapid re-fire).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from pathlib import Path
import sys
from typing import Any

IMPLEMENTATION_STATUS = "full"

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

COOLDOWN_HOURS = 23
_REFLEX_KEY    = "fathomdesk_fundamentals_sweep"


def run(ctx: dict[str, Any], conn: Any) -> dict[str, Any]:
    """Entry point called by the Genesis scheduler."""
    try:
        from tools.trading.analysts.fundamentals_sweep import run_sweep
        result = run_sweep(conn=conn)
        return {
            "status":      "ok",
            "updated":     result["updated"],
            "skipped":     result["skipped"],
            "elapsed_ms":  result["elapsed_ms"],
            "as_of_date":  result["as_of_date"],
            "errors":      result["errors"][:5],   # trim for log size
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json
    if get_connection is None:
        print("storage not available")
        sys.exit(1)
    conn = get_connection()
    result = run({}, conn)
    conn.close()
    print(json.dumps(result, indent=2))
