# CUI // SP-CTI
"""Software EOL/EOS ground truth — endoflife.date cache with air-gap fallback.

Structurally cloned from tools/migration_canvas/eol_sync.py (the Cisco EoX
pattern): a mutable cache table (docmod_eol_products, migration 257), a static
YAML seed for air-gapped sites, a best-effort live API sync, and an
import_dataset() path for bundled exports.

CLI:
    python -m tools.doc_modernization.eol_products_sync --seed --json
    python -m tools.doc_modernization.eol_products_sync --sync --json
    python -m tools.doc_modernization.eol_products_sync --import <path> --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = _REPO_ROOT / "args" / "docmod" / "eol_products.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _config() -> dict:
    from .pack_loader import load_config
    return load_config()


def _upsert(conn, product: str, cycle: str, row: dict, source: str) -> None:
    """Upsert by (product, cycle). source ∈ migration-257 CHECK:
    'endoflife.date' | 'seed' | 'manual'."""
    conn.execute(
        "DELETE FROM docmod_eol_products WHERE product = %s AND cycle = %s",
        (product, cycle),
    )
    conn.execute(
        """INSERT INTO docmod_eol_products
           (id, product, cycle, eol_date, eos_date, latest_version, lts, source, synced_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            f"eol-{uuid.uuid4().hex[:10]}", product, str(cycle),
            row.get("eol_date"), row.get("eos_date"), row.get("latest_version"),
            1 if row.get("lts") else 0, source, _now(),
        ),
    )


def load_seed(path: Path | None = None) -> dict:
    """Load args/docmod/eol_products.yaml into the cache (source='seed')."""
    import yaml

    path = path or SEED_PATH
    if not path.exists():
        return {"loaded": 0, "error": f"seed not found: {path}"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    conn = _connect()
    loaded = 0
    try:
        for product, cycles in (raw.get("products") or {}).items():
            for cycle, row in (cycles or {}).items():
                _upsert(conn, product, str(cycle), row or {}, "seed")
                loaded += 1
        conn.commit()
    finally:
        conn.close()
    return {"loaded": loaded, "source": "seed"}


def sync(products: list[str] | None = None) -> dict:
    """Best-effort live sync from endoflife.date. Offline-safe: honors the
    docmod_config offline flag and swallows network errors (cache keeps its
    seed/import rows)."""
    cfg = _config()
    if cfg.get("offline"):
        return {"synced": 0, "skipped": "offline flag set"}

    base = (cfg.get("endoflife_base_url") or "https://endoflife.date/api").rstrip("/")
    if products is None:
        import yaml
        products = []
        if SEED_PATH.exists():
            raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8")) or {}
            products = list((raw.get("products") or {}).keys())
    if not products:
        return {"synced": 0, "skipped": "no products configured"}

    try:
        import requests
    except ImportError:
        return {"synced": 0, "skipped": "requests not installed"}

    conn = _connect()
    synced, errors = 0, []
    try:
        for product in products:
            try:
                resp = requests.get(f"{base}/{product}.json", timeout=15)
                resp.raise_for_status()
                cycles = resp.json()
            except Exception as exc:  # network/parse — best-effort per product
                errors.append(f"{product}: {exc}")
                continue
            for c in cycles if isinstance(cycles, list) else []:
                cycle = str(c.get("cycle", ""))
                if not cycle:
                    continue
                eol = c.get("eol")
                support = c.get("support")
                _upsert(conn, product, cycle, {
                    # endoflife.date: eol/support may be bool (False = still alive)
                    "eol_date": eol if isinstance(eol, str) else None,
                    "eos_date": support if isinstance(support, str) else None,
                    "latest_version": c.get("latest"),
                    "lts": bool(c.get("lts")),
                }, "endoflife.date")
                synced += 1
        conn.commit()
    finally:
        conn.close()
    return {"synced": synced, "errors": errors}


def import_dataset(path: str | Path) -> dict:
    """Air-gap import of a JSON/YAML bundle: {products: {slug: {cycle: {...}}}}.
    Rows land with source='manual' (migration-257 vocabulary for operator loads)."""
    import yaml

    p = Path(path)
    if not p.exists():
        return {"imported": 0, "error": f"not found: {p}"}
    text = p.read_text(encoding="utf-8")
    raw = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    conn = _connect()
    imported = 0
    try:
        for product, cycles in ((raw or {}).get("products") or {}).items():
            for cycle, row in (cycles or {}).items():
                _upsert(conn, product, str(cycle), row or {}, "manual")
                imported += 1
        conn.commit()
    finally:
        conn.close()
    return {"imported": imported, "source": "manual"}


def get_product_eol(product: str, cycle: str | None = None,
                    alias_map: dict | None = None, conn=None) -> dict | None:
    """Lookup a product cycle. `product` may be a document phrase
    ('Windows Server 2012') resolved through alias_map to a slug + cycle."""
    slug = (product or "").strip().lower()
    resolved_cycle = cycle
    for alias, mapped in (alias_map or {}).items():
        a = alias.lower()
        if slug.startswith(a) or a in slug:
            if resolved_cycle is None:
                remainder = slug.replace(a, "").strip().replace("r2", "").strip()
                resolved_cycle = remainder or None
            slug = mapped
            break
    own = conn is None
    if own:
        conn = _connect()
    try:
        if resolved_cycle is not None:
            row = conn.execute(
                "SELECT * FROM docmod_eol_products WHERE product = %s AND cycle = %s",
                (slug, str(resolved_cycle)),
            ).fetchone()
            return dict(row) if row else None
        rows = conn.execute(
            "SELECT * FROM docmod_eol_products WHERE product = %s", (slug,)
        ).fetchall()
        return [dict(r) for r in rows] or None
    finally:
        if own:
            conn.close()


def newest_supported_cycle(product_slug: str, conn=None) -> dict | None:
    """Newest cycle whose eol_date is NULL or in the future — the replacement
    recommendation source for the software pack."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM docmod_eol_products WHERE product = %s", (product_slug,)
        ).fetchall()]
    finally:
        if own:
            conn.close()
    today = _now()[:10]
    alive = [r for r in rows if not r.get("eol_date") or str(r["eol_date"])[:10] > today]
    if not alive:
        return None
    return max(alive, key=lambda r: str(r.get("cycle", "")))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="docmod endoflife.date cache")
    parser.add_argument("--seed", action="store_true", help="load static seed")
    parser.add_argument("--sync", action="store_true", help="live sync from endoflife.date")
    parser.add_argument("--import", dest="import_path", metavar="PATH", help="air-gap bundle import")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result: dict = {}
    if args.seed:
        result["seed"] = load_seed()
    if args.sync:
        result["sync"] = sync()
    if args.import_path:
        result["import"] = import_dataset(args.import_path)
    if not result:
        parser.print_help()
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
