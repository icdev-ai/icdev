# CUI // SP-CTI
"""Cache Warmer — pre-populates the LLM response cache on deploy.

Reads seed queries from context/cache_seeds/default_seeds.yaml (or a custom
seeds file) and invokes the LLM router for each, writing results to the
llm_response_cache so the first real user requests are cache hits.

Supports dry_run=True for testing — no LLM calls are made, only the seed
loading and result structure are exercised.

NIST 800-53: SA-11 (Developer Testing), SC-28 (Protection at Rest),
AU-12 (Audit Record Generation).

Usage::

    python tools/cache_savings/warmer.py --warm --json
    python tools/cache_savings/warmer.py --warm --dry-run --json
    python tools/cache_savings/warmer.py --warm --seeds context/cache_seeds/default_seeds.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run BY PATH, sys.path[0] is this file's own directory — never
# the import root — so `from tools...` below raises ModuleNotFoundError and the
# usage line above fails exactly as printed. This is the command the dashboard's
# LLM Prompt Cache card tells an operator to run when the cache reads cold, so
# the one moment it is reached is the one moment it must work. parents[2] is
# whatever holds this file's `tools` package: the repo root in tools/, and
# <repo>/icdev in the icdev/ mirror.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.cache_savings.warmer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SEEDS_PATH = BASE_DIR / "context" / "cache_seeds" / "default_seeds.yaml"

# Module-level import so patch("tools.cache_savings.warmer.LLMRouter") works in tests.
try:
    from tools.llm.router import LLMRouter, LLMUnavailableError  # noqa: F401
except ImportError:
    LLMRouter = None  # type: ignore[assignment,misc]
    LLMUnavailableError = RuntimeError  # type: ignore[assignment,misc]


def _load_seeds(seeds_path: Optional[Path] = None) -> Dict[str, List[str]]:
    """Load seed queries from YAML file.

    Returns:
        Dict mapping function_name -> list of seed query strings.
    """
    path = seeds_path or DEFAULT_SEEDS_PATH
    if not path.exists():
        logger.warning("Seeds file not found: %s", path)
        return {}
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("seeds", {})
    except Exception as exc:
        logger.warning("Failed to load seeds from %s: %s", path, exc)
        return {}


class CacheWarmer:
    """Pre-populates the LLM response cache with seed queries.

    Args:
        dry_run: When True, no LLM calls are made. Results have status='dry_run'.
        seeds_path: Path to the YAML seeds file. Defaults to DEFAULT_SEEDS_PATH.
        max_tokens: Max tokens per seed request (kept low to minimize cost).
    """

    def __init__(
        self,
        dry_run: bool = False,
        seeds_path: Optional[Path] = None,
        max_tokens: int = 256,
    ):
        self.dry_run = dry_run
        self.seeds_path = seeds_path
        self.max_tokens = max_tokens
        self._seeds: Optional[Dict[str, List[str]]] = None

    def _get_seeds(self) -> Dict[str, List[str]]:
        """Lazy-load seed queries."""
        if self._seeds is None:
            self._seeds = _load_seeds(self.seeds_path)
        return self._seeds

    def warm_on_deploy(self) -> List[Dict[str, Any]]:
        """Warm the cache using all configured seed queries.

        Returns:
            List of result dicts, each containing:
              - function (str): ICDEV™ function name.
              - query (str): Seed query string.
              - status (str): 'warmed', 'skipped', 'dry_run', 'error'.
              - tokens_used (int): Tokens consumed (0 in dry_run).
              - cached (bool): True if the result was already in cache.
        """
        seeds = self._get_seeds()
        results: List[Dict[str, Any]] = []

        if self.dry_run:
            # Return placeholder results without invoking any LLM
            for function, queries in seeds.items():
                for query in queries:
                    results.append({
                        "function": function,
                        "query": query,
                        "status": "dry_run",
                        "tokens_used": 0,
                        "cached": False,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
            logger.info("CacheWarmer: dry_run — %d entries enumerated, no LLM calls made", len(results))
            return results

        # Live mode: invoke router for each seed query
        try:
            from tools.llm.provider import LLMRequest

            if LLMRouter is None:
                logger.error("CacheWarmer: LLMRouter unavailable")
                return results
            router = LLMRouter()
        except ImportError as exc:
            logger.error("CacheWarmer: LLMRouter unavailable — %s", exc)
            return results

        for function, queries in seeds.items():
            for query in queries:
                entry: Dict[str, Any] = {
                    "function": function,
                    "query": query,
                    "status": "error",
                    "tokens_used": 0,
                    "cached": False,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    request = LLMRequest(
                        messages=[{"role": "user", "content": query}],
                        max_tokens=self.max_tokens,
                        cache_control="ephemeral",
                        skip_injection_scan=True,
                    )
                    response = router.invoke(function, request)
                    entry["status"] = "warmed"
                    entry["tokens_used"] = (response.input_tokens or 0) + (response.output_tokens or 0)
                    entry["cached"] = (response.cache_read_input_tokens or 0) > 0
                    logger.debug("Warmed %s: '%s...' (%d tokens)", function, query[:40], entry["tokens_used"])
                except Exception as exc:  # noqa: BLE001
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    logger.warning("CacheWarmer: failed to warm %s / '%s': %s", function, query[:40], exc)
                results.append(entry)

        warmed = sum(1 for r in results if r["status"] == "warmed")
        errors = sum(1 for r in results if r["status"] == "error")
        logger.info("CacheWarmer: %d warmed, %d errors out of %d total", warmed, errors, len(results))
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main():
    parser = argparse.ArgumentParser(description="ICDEV™ cache warming utility")
    parser.add_argument("--warm", action="store_true", help="Run cache warming")
    parser.add_argument("--dry-run", action="store_true", help="Dry run — no LLM calls")
    parser.add_argument("--seeds", type=str, default=None, help="Path to seeds YAML file")
    parser.add_argument("--max-tokens", type=int, default=256, help="Max tokens per seed request")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    seeds_path = Path(args.seeds) if args.seeds else None
    warmer = CacheWarmer(dry_run=args.dry_run, seeds_path=seeds_path, max_tokens=args.max_tokens)

    if args.warm:
        results = warmer.warm_on_deploy()
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            for r in results:
                print(f"[{r['status']:8s}] {r['function']:30s} | {r['query'][:60]}")
            warmed = sum(1 for r in results if r["status"] == "warmed")
            dry = sum(1 for r in results if r["status"] == "dry_run")
            errors = sum(1 for r in results if r["status"] == "error")
            print(f"\nTotal: {len(results)} | Warmed: {warmed} | Dry: {dry} | Errors: {errors}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
