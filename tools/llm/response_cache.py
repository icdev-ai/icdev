# [TEMPLATE: CUI // SP-CTI]
"""LLM Response Cache — deterministic key-value store for LLM responses.

PostgreSQL-native (JSONB + BRIN) with automatic SQLite fallback. The table is
LOGGED: it is also the savings ledger — see the note above _PG_DDL.
Air-gap safe — 100% local storage via get_connection().

Usage::

    from tools.llm.response_cache import LLMResponseCache
    cache = LLMResponseCache()
    cache.set("key", response, ttl_seconds=3600)
    hit = cache.get("key")

CLI::

    python tools/llm/response_cache.py --stats --json
    python tools/llm/response_cache.py --clear --function code_generation --json
    python tools/llm/response_cache.py --warm --function narrative_generation --limit 50 --json
    python tools/llm/response_cache.py --enable
    python tools/llm/response_cache.py --disable
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from tools.db.storage import get_connection, get_backend, is_pg
from icdev.core.paths import repo_root

# LLMResponse import with graceful fallback
try:
    from tools.llm.provider import LLMResponse
except ImportError:
    LLMResponse = None

logger = get_logger("icdev.llm.response_cache")

BASE_DIR = repo_root(__file__)
CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"

# ---------------------------------------------------------------------------
# DDL per backend
# ---------------------------------------------------------------------------

# LOGGED, deliberately — this table is not only a cache.
#
# It was UNLOGGED, which is the right call for data that is cheap to regenerate
# and written at high frequency. Neither half held here.
#
# It is also the LEDGER. tools/cache_savings/savings.py computes every number on
# the dashboard's LLM Prompt Cache card — total hits, hit rate, dollars saved —
# with `FROM llm_response_cache` and nothing else; there is no separate savings
# table. PostgreSQL TRUNCATES an unlogged table on crash recovery, so any unclean
# shutdown silently reset a cumulative business metric to $0.0000, with no record
# that it had ever been anything else. Observed 2026-08-16: card reading 0
# entries / 0.0% / $0.0000 on a platform that has been routing LLM traffic for
# months.
#
# And the optimisation bought nothing. Writes here happen once per cache MISS —
# i.e. once per real LLM API call, which takes seconds. Benchmarked on this
# deployment, 400 inserts of a 2KB body: UNLOGGED 0.482 ms/insert, LOGGED
# 0.446 ms/insert. LOGGED measured marginally FASTER; the difference is noise,
# which is the point — there is no throughput to protect at this write rate.
_PG_DDL = """
CREATE TABLE IF NOT EXISTS llm_response_cache (
    cache_key TEXT PRIMARY KEY,
    function TEXT NOT NULL,
    model_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls_json JSONB,
    structured_output_json JSONB,
    provider TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    stop_reason TEXT,
    hit_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_response_cache USING BRIN (expires_at);
CREATE INDEX IF NOT EXISTS idx_llm_cache_function ON llm_response_cache (function) WHERE function IS NOT NULL;
"""

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS llm_response_cache (
    cache_key TEXT PRIMARY KEY,
    function TEXT NOT NULL,
    model_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls_json TEXT,
    structured_output_json TEXT,
    provider TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cache_read_input_tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    stop_reason TEXT,
    hit_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_response_cache (expires_at);
CREATE INDEX IF NOT EXISTS idx_llm_cache_function ON llm_response_cache (function);
"""

# D-CACHE-10: ALTER TABLE for upgrading existing installs that lack cache token columns.
_UPGRADE_STMTS = {
    "pg": [
        "ALTER TABLE llm_response_cache ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER DEFAULT 0",
        "ALTER TABLE llm_response_cache ADD COLUMN IF NOT EXISTS cache_read_input_tokens INTEGER DEFAULT 0",
    ],
    "sqlite": [
        "ALTER TABLE llm_response_cache ADD COLUMN cache_creation_input_tokens INTEGER DEFAULT 0",
        "ALTER TABLE llm_response_cache ADD COLUMN cache_read_input_tokens INTEGER DEFAULT 0",
    ],
}

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_config_cache: Optional[dict] = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: float = 60.0


def _load_config() -> dict:
    """Load response_cache section from args/llm_config.yaml."""
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache

    cfg = {"enabled": False, "ttl_seconds": 3600, "max_entries": 100000,
           "match_strategy": "exact", "excluded_functions": [], "per_function": {}}

    try:
        import yaml
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            rc = data.get("response_cache", {})
            cfg["enabled"] = rc.get("enabled", False)
            cfg["backend"] = rc.get("backend", "postgresql")
            cfg["ttl_seconds"] = rc.get("ttl_seconds", 3600)
            cfg["max_entries"] = rc.get("max_entries", 100000)
            cfg["match_strategy"] = rc.get("match_strategy", "exact")
            cfg["excluded_functions"] = rc.get("excluded_functions", [])
            cfg["per_function"] = rc.get("per_function", {})
    except Exception as exc:
        logger.debug("Failed to load response_cache config: %s", exc)

    _config_cache = cfg
    _config_cache_time = now
    return cfg


# ---------------------------------------------------------------------------
# Cache key strategy
# ---------------------------------------------------------------------------

def canonical_key(function: str, model_id: str, request: Any) -> str:
    """Compute a deterministic SHA-256 cache key from request components.

    Canonicalizes:
      - function name
      - model_id
      - sorted message list (role + content)
      - system_prompt
      - temperature (only if != 1.0)
      - max_tokens
      - tools schema (if present)
      - output_schema (if present)
    """
    payload: Dict[str, Any] = {"function": function, "model_id": model_id}

    messages = getattr(request, "messages", None) or []
    canonical_messages = []
    for msg in messages:
        if isinstance(msg, dict):
            canonical_messages.append({
                "role": msg.get("role", ""),
                "content": msg.get("content", "")
            })
        else:
            canonical_messages.append({"role": str(getattr(msg, "role", "")),
                                       "content": str(getattr(msg, "content", ""))})
    # Sort by role+content for determinism
    canonical_messages.sort(key=lambda m: (m["role"], m["content"]))
    payload["messages"] = canonical_messages

    system_prompt = getattr(request, "system_prompt", "") or ""
    payload["system_prompt"] = system_prompt

    temp = getattr(request, "temperature", None)
    if temp is not None and temp != 1.0:
        payload["temperature"] = temp

    max_tokens = getattr(request, "max_tokens", None)
    if max_tokens is not None and max_tokens != 4096:
        payload["max_tokens"] = max_tokens

    tools = getattr(request, "tools", None)
    if tools:
        payload["tools"] = _canonical_json(tools)

    output_schema = getattr(request, "output_schema", None)
    if output_schema:
        payload["output_schema"] = _canonical_json(output_schema)

    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _canonical_json(obj: Any) -> str:
    """Return a canonical JSON string for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Response serialization
# ---------------------------------------------------------------------------

def _response_to_row(response: Any, function: str, model_id: str) -> dict:
    """Serialize an LLMResponse (or dict) into a DB row dict."""
    if LLMResponse is not None and isinstance(response, LLMResponse):
        return {
            "cache_key": "",  # filled by caller
            "function": function,
            "model_id": model_id,
            "content": response.content or "",
            "tool_calls_json": json.dumps(response.tool_calls) if response.tool_calls else None,
            "structured_output_json": json.dumps(response.structured_output) if response.structured_output else None,
            "provider": response.provider or "",
            "input_tokens": response.input_tokens or 0,
            "output_tokens": response.output_tokens or 0,
            "thinking_tokens": response.thinking_tokens or 0,
            "cache_creation_input_tokens": response.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": response.cache_read_input_tokens or 0,
            "duration_ms": response.duration_ms or 0,
            "stop_reason": response.stop_reason or "",
        }
    # Fallback for dict-like responses
    return {
        "cache_key": "",
        "function": function,
        "model_id": model_id,
        "content": str(getattr(response, "content", "") or getattr(response, "text", "")),
        "tool_calls_json": json.dumps(getattr(response, "tool_calls", [])) if getattr(response, "tool_calls", None) else None,
        "structured_output_json": json.dumps(getattr(response, "structured_output", None)) if getattr(response, "structured_output", None) else None,
        "provider": getattr(response, "provider", "") or "",
        "input_tokens": getattr(response, "input_tokens", 0) or 0,
        "output_tokens": getattr(response, "output_tokens", 0) or 0,
        "thinking_tokens": getattr(response, "thinking_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(response, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(response, "cache_read_input_tokens", 0) or 0,
        "duration_ms": getattr(response, "duration_ms", 0) or 0,
        "stop_reason": getattr(response, "stop_reason", "") or "",
    }


def _row_to_response(row: Any) -> Any:
    """Deserialize a DB row into an LLMResponse (or dict fallback)."""
    if LLMResponse is None:
        return dict(row)

    resp = LLMResponse()
    resp.content = row["content"] or ""
    resp.provider = row["provider"] or ""
    resp.model_id = row["model_id"] or ""
    resp.input_tokens = row["input_tokens"] or 0
    resp.output_tokens = row["output_tokens"] or 0
    resp.thinking_tokens = row["thinking_tokens"] or 0
    try:
        resp.cache_creation_input_tokens = row["cache_creation_input_tokens"] or 0
    except (KeyError, IndexError):
        resp.cache_creation_input_tokens = 0
    try:
        resp.cache_read_input_tokens = row["cache_read_input_tokens"] or 0
    except (KeyError, IndexError):
        resp.cache_read_input_tokens = 0
    resp.duration_ms = row["duration_ms"] or 0
    resp.stop_reason = row["stop_reason"] or ""

    try:
        tool_json = row["tool_calls_json"]
    except (KeyError, IndexError):
        tool_json = None
    if tool_json:
        try:
            if isinstance(tool_json, str):
                resp.tool_calls = json.loads(tool_json)
            else:
                resp.tool_calls = list(tool_json)
        except Exception:
            resp.tool_calls = []

    try:
        struct_json = row["structured_output_json"]
    except (KeyError, IndexError):
        struct_json = None
    if struct_json:
        try:
            if isinstance(struct_json, str):
                resp.structured_output = json.loads(struct_json)
            else:
                resp.structured_output = dict(struct_json)
        except Exception:
            resp.structured_output = None

    return resp


# ---------------------------------------------------------------------------
# Core cache class
# ---------------------------------------------------------------------------

class LLMResponseCache:
    """Deterministic LLM response cache backed by PostgreSQL UNLOGGED (or SQLite).

    Features:
      - SHA-256 canonical keys
      - Lazy TTL eviction + eager LRU sweep
      - Timing jitter for side-channel mitigation (InputSnatch defense)
      - Per-function TTL overrides
      - Excluded function list
      - Air-gap safe — 100% local
    """

    _instance: Optional["LLMResponseCache"] = None

    def __new__(cls, *args, **kwargs):
        """Singleton per process — avoids table recreation churn."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: Optional[dict] = None):
        if self._initialized:
            return
        self._config = config or _load_config()
        self._ensure_tables()
        self._initialized = True

    def _ensure_tables(self) -> None:
        """Create cache table and indexes (backend-aware)."""
        conn = get_connection()
        try:
            if is_pg():
                # PostgreSQL: UNLOGGED + JSONB + BRIN
                for stmt in _PG_DDL.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
            else:
                # SQLite: regular table + B-tree indexes
                for stmt in _SQLITE_DDL.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
            # D-CACHE-10: Upgrade existing tables — add cache token columns if missing
            backend_key = "pg" if is_pg() else "sqlite"
            for stmt in _UPGRADE_STMTS[backend_key]:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass  # Column already exists — safe to ignore
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def get(self, cache_key: str, jitter_ms: int = 50) -> Optional[Any]:
        """Lookup a cached response.

        Returns None if miss, expired, or cache disabled.
        On hit: increments hit_count, applies random jitter (0-jitter_ms ms)
        for InputSnatch side-channel mitigation.
        """
        if not self._config.get("enabled", False):
            return None

        conn = get_connection()
        try:
            # For PG: BRIN may return false positives — recheck with expires_at < NOW()
            if is_pg():
                row = conn.execute(
                    """
                    SELECT * FROM llm_response_cache
                    WHERE cache_key = %s AND expires_at > NOW()
                    """,
                    (cache_key,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM llm_response_cache
                    WHERE cache_key = %s AND datetime(expires_at) > datetime('now')
                    """,
                    (cache_key,),
                ).fetchone()

            if row is None:
                return None

            # Increment hit_count
            conn.execute(
                """
                UPDATE llm_response_cache
                SET hit_count = hit_count + 1
                WHERE cache_key = %s
                """,
                (cache_key,),
            )
            conn.commit()

            # Timing side-channel mitigation
            if jitter_ms > 0:
                delay = random.randint(0, jitter_ms) / 1000.0
                time.sleep(delay)

            return _row_to_response(row)
        finally:
            conn.close()

    def set(self, cache_key: str, response: Any, ttl_seconds: Optional[int] = None, function: str = "") -> None:
        """Store a response in the cache.

        If ttl_seconds is None, uses config default.
        Does nothing if cache disabled or function is excluded.
        """
        if not self._config.get("enabled", False):
            return

        excluded = self._config.get("excluded_functions", [])
        if function in excluded:
            return

        ttl = ttl_seconds if ttl_seconds is not None else self._config.get("ttl_seconds", 3600)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)

        row = _response_to_row(response, function, getattr(response, "model_id", "") or "")
        row["cache_key"] = cache_key
        row["created_at"] = now.isoformat()
        row["expires_at"] = expires.isoformat()

        conn = get_connection()
        try:
            if is_pg():
                conn.execute(
                    """
                    INSERT INTO llm_response_cache
                        (cache_key, function, model_id, content, tool_calls_json,
                         structured_output_json, provider, input_tokens, output_tokens,
                         thinking_tokens, cache_creation_input_tokens, cache_read_input_tokens,
                         duration_ms, stop_reason, hit_count, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        content = EXCLUDED.content,
                        tool_calls_json = EXCLUDED.tool_calls_json,
                        structured_output_json = EXCLUDED.structured_output_json,
                        provider = EXCLUDED.provider,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        thinking_tokens = EXCLUDED.thinking_tokens,
                        cache_creation_input_tokens = EXCLUDED.cache_creation_input_tokens,
                        cache_read_input_tokens = EXCLUDED.cache_read_input_tokens,
                        duration_ms = EXCLUDED.duration_ms,
                        stop_reason = EXCLUDED.stop_reason,
                        hit_count = EXCLUDED.hit_count,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        row["cache_key"], row["function"], row["model_id"], row["content"],
                        row["tool_calls_json"] or "null",
                        row["structured_output_json"] or "null",
                        row["provider"], row["input_tokens"], row["output_tokens"],
                        row["thinking_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        row["duration_ms"], row["stop_reason"],
                        1, row["created_at"], row["expires_at"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO llm_response_cache
                        (cache_key, function, model_id, content, tool_calls_json,
                         structured_output_json, provider, input_tokens, output_tokens,
                         thinking_tokens, cache_creation_input_tokens, cache_read_input_tokens,
                         duration_ms, stop_reason, hit_count, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["cache_key"], row["function"], row["model_id"], row["content"],
                        row["tool_calls_json"], row["structured_output_json"],
                        row["provider"], row["input_tokens"], row["output_tokens"],
                        row["thinking_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        row["duration_ms"], row["stop_reason"],
                        1, row["created_at"], row["expires_at"],
                    ),
                )
            conn.commit()

            # Eager LRU sweep if max_entries exceeded
            self._maybe_evict(conn)
        finally:
            conn.close()

    def invalidate(self, function: Optional[str] = None, agent_id: Optional[str] = None) -> int:
        """Remove cached entries. Returns number of rows deleted."""
        conn = get_connection()
        try:
            if function and agent_id:
                cur = conn.execute(
                    "DELETE FROM llm_response_cache WHERE function = %s AND model_id = %s",
                    (function, agent_id),
                )
            elif function:
                cur = conn.execute(
                    "DELETE FROM llm_response_cache WHERE function = %s",
                    (function,),
                )
            elif agent_id:
                cur = conn.execute(
                    "DELETE FROM llm_response_cache WHERE model_id = %s",
                    (agent_id,),
                )
            else:
                cur = conn.execute("DELETE FROM llm_response_cache")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def stats(self) -> dict:
        """Return cache statistics."""
        conn = get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM llm_response_cache"
            ).fetchone()[0]
            hits = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) FROM llm_response_cache"
            ).fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM llm_response_cache WHERE expires_at < %s",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone()[0]
            avg_hits = conn.execute(
                "SELECT COALESCE(AVG(hit_count), 0) FROM llm_response_cache"
            ).fetchone()[0]
            return {
                "total_entries": int(total),
                "total_hits": int(hits),
                "expired_entries": int(expired),
                "avg_hit_count": float(avg_hits),
                "enabled": self._config.get("enabled", False),
                "backend": get_backend(),
                "max_entries": self._config.get("max_entries", 100000),
                "ttl_seconds": self._config.get("ttl_seconds", 3600),
            }
        finally:
            conn.close()

    def clear(self) -> int:
        """Clear all cache entries. Returns number of rows deleted."""
        return self.invalidate()

    def warm(self, function: str, limit: int = 50) -> int:
        """Pre-populate cache from recent telemetry (best-effort).

        Reads from ai_telemetry table and stores responses.
        Returns number of entries warmed.
        """
        conn = get_connection()
        warmed = 0
        try:
            rows = conn.execute(
                """
                SELECT prompt_hash, model_id, response_hash, input_tokens, output_tokens,
                       latency_ms, created_at
                FROM ai_telemetry
                WHERE function = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (function, limit),
            ).fetchall()

            for row in rows:
                cache_key = row["prompt_hash"]
                if not cache_key:
                    continue
                # Build a minimal response
                resp = LLMResponse() if LLMResponse else {}
                if LLMResponse:
                    resp.model_id = row["model_id"] or ""
                    resp.input_tokens = row["input_tokens"] or 0
                    resp.output_tokens = row["output_tokens"] or 0
                    resp.duration_ms = int(row["latency_ms"] or 0)
                    resp.content = ""
                self.set(cache_key, resp)
                warmed += 1

            return warmed
        except Exception as exc:
            logger.warning("Cache warm failed: %s", exc)
            return 0
        finally:
            conn.close()

    def _maybe_evict(self, conn) -> None:
        """Eager LRU sweep when row count exceeds max_entries."""
        max_entries = self._config.get("max_entries", 100000)
        if max_entries <= 0:
            return

        count_row = conn.execute("SELECT COUNT(*) FROM llm_response_cache").fetchone()
        count = int(count_row[0]) if count_row else 0
        if count <= max_entries:
            return

        to_evict = count - max_entries + int(max_entries * 0.05)  # Evict 5% overage
        to_evict = max(to_evict, 1)

        if is_pg():
            conn.execute(
                """
                DELETE FROM llm_response_cache
                WHERE ctid IN (
                    SELECT ctid FROM llm_response_cache
                    ORDER BY hit_count ASC, created_at ASC
                    LIMIT %s
                )
                """,
                (to_evict,),
            )
        else:
            conn.execute(
                """
                DELETE FROM llm_response_cache
                WHERE rowid IN (
                    SELECT rowid FROM llm_response_cache
                    ORDER BY hit_count ASC, created_at ASC
                    LIMIT %s
                )
                """,
                (to_evict,),
            )
        conn.commit()
        logger.info("Cache LRU evicted %d entries (count=%d, max=%d)", to_evict, count, max_entries)

    # -------------------------------------------------------------------
    # Config toggles
    # -------------------------------------------------------------------

    @staticmethod
    def enable() -> bool:
        """Enable caching by writing response_cache.enabled = true to config."""
        return _toggle_config(True)

    @staticmethod
    def disable() -> bool:
        """Disable caching by writing response_cache.enabled = false to config."""
        return _toggle_config(False)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _toggle_config(enabled: bool) -> bool:
    """Idempotently flip response_cache.enabled in args/llm_config.yaml."""
    try:
        import yaml
        if not CONFIG_PATH.exists():
            logger.warning("Config file not found: %s", CONFIG_PATH)
            return False
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        rc = data.setdefault("response_cache", {})
        if rc.get("enabled") == enabled:
            return True  # Already correct
        rc["enabled"] = enabled
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        # Clear config cache
        global _config_cache, _config_cache_time
        _config_cache = None
        _config_cache_time = 0
        return True
    except Exception as exc:
        logger.error("Failed to toggle cache config: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(description="LLM Response Cache CLI")
    parser.add_argument("--stats", action="store_true", help="Show cache statistics")
    parser.add_argument("--clear", action="store_true", help="Clear all cache entries")
    parser.add_argument("--function", type=str, default=None, help="Filter by function name")
    parser.add_argument("--agent-id", type=str, default=None, help="Filter by agent/model id")
    parser.add_argument("--warm", action="store_true", help="Warm cache from telemetry")
    parser.add_argument("--limit", type=int, default=50, help="Warm limit")
    parser.add_argument("--enable", action="store_true", help="Enable caching")
    parser.add_argument("--disable", action="store_true", help="Disable caching")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    cache = LLMResponseCache()
    result = {}

    if args.enable:
        ok = LLMResponseCache.enable()
        result = {"action": "enable", "ok": ok}
    elif args.disable:
        ok = LLMResponseCache.disable()
        result = {"action": "disable", "ok": ok}
    elif args.clear:
        deleted = cache.invalidate(args.function, args.agent_id)
        result = {"action": "clear", "deleted": deleted}
    elif args.warm:
        if not args.function:
            print("--warm requires --function", file=sys.stderr)
            sys.exit(1)
        warmed = cache.warm(args.function, limit=args.limit)
        result = {"action": "warm", "function": args.function, "warmed": warmed}
    elif args.stats:
        result = cache.stats()
    else:
        parser.print_help()
        sys.exit(0)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    _main()
