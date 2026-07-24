# CUI // SP-CTI
"""NIST publication revision cache — policy_refs evidence with air-gap fallback.

Structurally cloned from tools/doc_modernization/eol_products_sync.py (the
endoflife.date pattern): a mutable cache table (docmod_nist_pubs, migration 282),
a static YAML seed for air-gapped sites, a best-effort live pull, and an
import_dataset() path for bundled exports.

The live pull reads the NIST CSRC publications RSS/Atom feed with STDLIB ONLY
(urllib + xml.etree — no new dependencies), extracts each publication's latest
revision from the feed item titles, and upserts one row per publication. The
policy_refs pack then flags any document that cites an OLDER revision than the
one recorded here (deterministic numeric comparison — TRUST rule 1, no LLM).

Air-gap discipline (mirrors eol_products_sync): honors the docmod_config
``offline`` flag, is https-only with TLS verification and a tight timeout, and
swallows every network/parse error — the cache keeps its seed/import rows and the
nightly sweep never fails because egress is unavailable. True push/webhooks are
out of scope: this is scheduled pull only.

CLI:
    python -m tools.doc_modernization.nist_pubs_sync --seed --json
    python -m tools.doc_modernization.nist_pubs_sync --sync --json
    python -m tools.doc_modernization.nist_pubs_sync --sync --force --json
    python -m tools.doc_modernization.nist_pubs_sync --import <path> --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = _REPO_ROOT / "args" / "docmod" / "nist_pubs.yaml"

# Default NIST CSRC publications feed. Operators may override in
# args/docmod/docmod_config.yaml (nist_pubs_feed_url); the static seed is the
# air-gap source of truth when no egress is available.
DEFAULT_FEED_URL = "https://csrc.nist.gov/CSRC/media/feeds/rss/publications.xml"

# "NIST SP 800-53 Rev. 5" / "SP 800-171r3" -> pub_id 'SP 800-53', revision 5.
_PUB_RE = re.compile(
    r"(?i)\b(?:NIST\s+)?(SP\s?800-\d+[A-Za-z]?)\s?"
    r"(?:Rev(?:ision|\.)?\s?|r)(\d+)\b"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _config() -> dict:
    from .pack_loader import load_config
    return load_config()


def _normalize_pub_id(raw: str) -> str:
    """'SP 800-53' / 'sp800-53' -> canonical 'SP 800-53'."""
    m = re.match(r"(?i)\s*SP\s?(800-\d+[A-Za-z]?)\s*", raw or "")
    return f"SP {m.group(1)}" if m else (raw or "").strip()


def _upsert(conn, pub_id: str, row: dict, source: str) -> None:
    """Upsert by pub_id. source ∈ migration-282 CHECK: 'nist.gov'|'seed'|'manual'."""
    conn.execute("DELETE FROM docmod_nist_pubs WHERE pub_id = %s", (pub_id,))
    conn.execute(
        """INSERT INTO docmod_nist_pubs
           (id, pub_id, latest_revision, revision_num, title, url,
            published_date, source, synced_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            f"nistpub-{uuid.uuid4().hex[:10]}", pub_id,
            row.get("latest_revision"),
            int(row["revision_num"]) if row.get("revision_num") is not None else None,
            row.get("title"), row.get("url"), row.get("published_date"),
            source, _now(),
        ),
    )


def parse_feed(xml_text: str) -> list[dict]:
    """Parse RSS or Atom feed XML into per-publication latest-revision rows.

    Pure function (no DB, no network) — the unit-testable core. For each pub_id
    only the HIGHEST revision seen is kept. Returns a list of dicts:
    {pub_id, latest_revision, revision_num, title, url, published_date}.
    """
    # Feed XML is untrusted network content — parse with defusedxml (blocks
    # entity-expansion / external-entity attacks). Any parse failure (malformed
    # or hostile) degrades to an empty result so the sweep never crashes.
    import defusedxml.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        logger.warning("nist_pubs_sync: feed parse error: %s", exc)
        return []

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    # Collect (title, link, date) triples from RSS <item> or Atom <entry>.
    items: list[tuple[str, str, str]] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = link = date = ""
        for child in el:
            name = _local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link":
                # RSS: text; Atom: href attribute.
                link = (child.text or "").strip() or child.get("href", "")
            elif name in ("pubDate", "updated", "published", "date"):
                date = date or (child.text or "").strip()
        if title:
            items.append((title, link, date))

    best: dict[str, dict] = {}
    for title, link, date in items:
        m = _PUB_RE.search(title)
        if not m:
            continue
        pub_id = _normalize_pub_id(m.group(1))
        rev = int(m.group(2))
        cur = best.get(pub_id)
        if cur is None or rev > cur["revision_num"]:
            best[pub_id] = {
                "pub_id": pub_id,
                "latest_revision": f"Rev {rev}",
                "revision_num": rev,
                "title": title,
                "url": link,
                "published_date": date,
            }
    return list(best.values())


def _fetch_feed(url: str, timeout: int) -> str | None:
    """Best-effort https-only GET. Returns body text, or None on any failure."""
    if not url.lower().startswith("https://"):
        logger.warning("nist_pubs_sync: refusing non-https feed url: %s", url)
        return None
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-docmod/1.0"})
    try:
        # https-only enforced above; default SSL context verifies the cert chain.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 -- https scheme validated above; TLS verified
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # network/TLS/timeout — offline is a normal state
        logger.info("nist_pubs_sync: feed fetch skipped (%s)", exc)
        return None


def _within_cadence(conn, cadence_hours: float) -> bool:
    """True when a live sync ran within the cadence window (skip this run)."""
    if cadence_hours <= 0:
        return False
    try:
        row = conn.execute(
            "SELECT MAX(synced_at) AS last FROM docmod_nist_pubs WHERE source = 'nist.gov'"
        ).fetchone()
    except Exception:
        return False
    last = dict(row).get("last") if row else None
    if not last:
        return False
    try:
        ts = datetime.fromisoformat(str(last))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - ts < timedelta(hours=cadence_hours)


def load_seed(path: Path | None = None) -> dict:
    """Load args/docmod/nist_pubs.yaml into the cache (source='seed')."""
    import yaml

    path = path or SEED_PATH
    if not path.exists():
        return {"loaded": 0, "error": f"seed not found: {path}"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    conn = _connect()
    loaded = 0
    try:
        for pub_id, row in (raw.get("publications") or {}).items():
            _upsert(conn, _normalize_pub_id(pub_id), row or {}, "seed")
            loaded += 1
        conn.commit()
    finally:
        conn.close()
    return {"loaded": loaded, "source": "seed"}


def sync(force: bool = False) -> dict:
    """Best-effort live pull from the NIST CSRC publications feed.

    Offline-safe: honors the docmod_config ``offline`` flag, gates on
    ``nist_pubs_cadence_hours`` (skip if a live sync ran inside the window unless
    ``force``), and swallows network errors so the sweep never fails.
    """
    cfg = _config()
    if cfg.get("offline"):
        return {"synced": 0, "skipped": "offline flag set"}

    cadence = float(cfg.get("nist_pubs_cadence_hours", 24) or 0)
    conn = _connect()
    try:
        if not force and _within_cadence(conn, cadence):
            return {"synced": 0, "skipped": "within cadence window"}

        url = cfg.get("nist_pubs_feed_url") or DEFAULT_FEED_URL
        timeout = int(cfg.get("nist_pubs_timeout_seconds", 15) or 15)
        body = _fetch_feed(url, timeout)
        if body is None:
            return {"synced": 0, "skipped": "feed unavailable (offline?)"}

        rows = parse_feed(body)
        synced = 0
        for row in rows:
            _upsert(conn, row["pub_id"], row, "nist.gov")
            synced += 1
        conn.commit()
        return {"synced": synced, "publications": [r["pub_id"] for r in rows]}
    finally:
        conn.close()


def import_dataset(path: str | Path) -> dict:
    """Air-gap import of a JSON/YAML bundle: {publications: {pub_id: {...}}}.
    Rows land with source='manual' (migration-282 vocabulary for operator loads)."""
    import yaml

    p = Path(path)
    if not p.exists():
        return {"imported": 0, "error": f"not found: {p}"}
    text = p.read_text(encoding="utf-8")
    raw = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    conn = _connect()
    imported = 0
    try:
        for pub_id, row in ((raw or {}).get("publications") or {}).items():
            _upsert(conn, _normalize_pub_id(pub_id), row or {}, "manual")
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return {"imported": imported, "source": "manual"}


def get_latest_revision(pub_id: str, conn=None) -> dict | None:
    """Latest known revision for a publication, or None if uncached."""
    slug = _normalize_pub_id(pub_id)
    own = conn is None
    if own:
        conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM docmod_nist_pubs WHERE pub_id = %s", (slug,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def evidence_hash(conn=None) -> str:
    """sha256 over the cache contents — a refresh re-triggers policy_refs scans."""
    import hashlib

    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = conn.execute(
            "SELECT pub_id, revision_num FROM docmod_nist_pubs ORDER BY pub_id"
        ).fetchall()
        payload = "|".join(f"{r['pub_id']}:{r['revision_num']}" for r in (dict(x) for x in rows))
    except Exception:
        try:
            conn.rollback()  # PG: failed statement poisons the transaction
        except Exception:
            pass
        payload = "no-nist-pubs"
    finally:
        if own:
            conn.close()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="docmod NIST publication revision cache")
    parser.add_argument("--seed", action="store_true", help="load static seed")
    parser.add_argument("--sync", action="store_true", help="live pull from NIST CSRC feed")
    parser.add_argument("--force", action="store_true", help="ignore cadence gate on --sync")
    parser.add_argument("--import", dest="import_path", metavar="PATH", help="air-gap bundle import")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result: dict = {}
    if args.seed:
        result["seed"] = load_seed()
    if args.sync:
        result["sync"] = sync(force=args.force)
    if args.import_path:
        result["import"] = import_dataset(args.import_path)
    if not result:
        parser.print_help()
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
