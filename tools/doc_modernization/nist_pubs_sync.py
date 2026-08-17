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

THE FEED IS GONE, AND THAT USED TO LOOK LIKE AIR-GAP (cef-fnd-02). Measured
2026-08-17 from a host with working egress: ``DEFAULT_FEED_URL`` answers HTTP
404 — NIST retired the CSRC publications RSS feed. Every one of those runs
reported ``"feed unavailable (offline?)"``, which is what a genuinely
air-gapped site reports, so a dead URL was indistinguishable from the posture
this module is designed for and nobody noticed. ``fetch_status`` now names the
cause instead: see ``FETCH_STATUS`` below. Nothing about a real air-gap
changed — ``unreachable`` is still swallowed and still returns cleanly.

No live replacement was adopted, and the two candidates are recorded here so
the next reader does not re-derive them:

  * ``/CSRC/media/feeds/pubs/drafts-open-for-comment.xml`` is the only feed CSRC
    still serves. It lists DRAFTS. Treating a draft as "the latest revision NIST
    publishes" would flag a document citing the current FINAL revision as
    superseded — a false finding, which is worse than a missing one.
  * ``NIST-Cybersecurity-Publications.xlsx`` is a real inventory with a
    Final/Draft stage column, but the copy served on 2026-08-17 still had SP
    800-171 Rev 3 and SP 800-63-4 as "Public Draft" — roughly three years stale,
    and adopting it would REGRESS the curated seed (which has 800-171 Rev 3
    right) to Rev 2.

So the static seed remains the most accurate source available, and ``sync()``
now falls back to it when the cache is EMPTY. That fallback is the substrate
fix: ``doc_modernization_sweep`` only ever calls ``sync()``, never
``load_seed()``, so before this change a deployment where nobody hand-ran
``--seed`` left the cache at zero rows forever and the policy_refs pack's
dynamic half answered "unknown" for every citation. The fallback never
overwrites live rows — it only runs when there is nothing at all to overwrite.

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
#
# RETIRED BY NIST — this URL answered HTTP 404 when measured 2026-08-17. It is
# kept as the default deliberately: it costs one cadence-gated request, it is
# the endpoint to restore if NIST brings the feed back, and a run against it now
# reports fetch_status 'feed_not_found' rather than implying air-gap. Do NOT
# repoint it at the drafts feed or the stale XLSX inventory — see the module
# docstring for why both were rejected.
DEFAULT_FEED_URL = "https://csrc.nist.gov/CSRC/media/feeds/rss/publications.xml"

# Why the live pull produced no rows. FOUR of these are not defects and only
# 'feed_not_found' / 'http_error' point at a broken configuration — collapsing
# them into one "offline?" string is what hid a dead feed for months.
FETCH_STATUS = {
    "offline":        "offline flag set",                    # deliberate air-gap
    "not_attempted":  "within cadence window",               # nothing was fetched
    "not_https":      "refused non-https feed url",          # egress policy
    "unreachable":    "feed unreachable (network/TLS/timeout)",  # genuine offline
    "feed_not_found": "feed url returned HTTP 404 (retired by NIST?)",
    "http_error":     "feed url returned an HTTP error",
    "empty_feed":     "feed served no NIST publication rows",
    "ok":             "",
}

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


def _fetch_feed(url: str, timeout: int) -> tuple[str | None, str]:
    """Best-effort https-only GET -> ``(body_text_or_None, status)``.

    ``status`` is a FETCH_STATUS key. A 404 is reported as 'feed_not_found', not
    as the generic offline status: a retired URL and an air-gapped host are
    different problems with different fixes, and only one of them is a defect.
    Still swallows every error — the caller never sees an exception.
    """
    import urllib.error
    import urllib.request

    if not url.lower().startswith("https://"):
        logger.warning("nist_pubs_sync: refusing non-https feed url: %s", url)
        return None, "not_https"

    req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-docmod/1.0"})
    try:
        # https-only enforced above; default SSL context verifies the cert chain.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 -- https scheme validated above; TLS verified
            return resp.read().decode("utf-8", errors="replace"), "ok"
    except urllib.error.HTTPError as exc:
        # The server answered — this is a configuration fact, not an egress fact.
        status = "feed_not_found" if exc.code == 404 else "http_error"
        logger.warning("nist_pubs_sync: feed url %s returned HTTP %s", url, exc.code)
        return None, status
    except Exception as exc:  # network/TLS/timeout — offline is a normal state
        logger.info("nist_pubs_sync: feed fetch skipped (%s)", exc)
        return None, "unreachable"


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


def load_seed(path: Path | None = None, conn=None) -> dict:
    """Load args/docmod/nist_pubs.yaml into the cache (source='seed').

    ``conn`` lets sync() reuse its open connection for the empty-cache fallback
    (opening a second one mid-transaction deadlocks on some backends).
    """
    import yaml

    path = path or SEED_PATH
    if not path.exists():
        return {"loaded": 0, "error": f"seed not found: {path}"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    own = conn is None
    if own:
        conn = _connect()
    loaded = 0
    try:
        for pub_id, row in (raw.get("publications") or {}).items():
            _upsert(conn, _normalize_pub_id(pub_id), row or {}, "seed")
            loaded += 1
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"loaded": loaded, "source": "seed"}


def _cache_row_count(conn) -> int | None:
    """Rows currently in the cache. None when the table is ABSENT.

    Absent and empty are different failures — a missing migration versus a
    writer that never ran — so they must not both read as 0.
    """
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM docmod_nist_pubs").fetchone()
        return int(dict(row)["n"])
    except Exception:
        try:
            conn.rollback()  # PG: failed statement poisons the transaction
        except Exception:
            pass
        return None


def _seed_if_empty(conn) -> dict:
    """Populate from the static seed ONLY when the cache holds nothing.

    The live pull is the preferred source; this is the floor that keeps the
    policy_refs pack from having no substrate at all on a deployment where
    nobody hand-ran ``--seed``. It never overwrites live rows because it never
    runs when there are any.
    """
    count = _cache_row_count(conn)
    if count is None:
        return {"seeded": 0, "cache_rows": None,
                "cache_status": "absent (docmod_nist_pubs table does not exist)"}
    if count > 0:
        return {"seeded": 0, "cache_rows": count, "cache_status": "populated"}
    seeded = load_seed(conn=conn)
    return {"seeded": seeded.get("loaded", 0), "cache_rows": seeded.get("loaded", 0),
            "cache_status": "seeded (live pull unavailable, cache was empty)",
            **({"seed_error": seeded["error"]} if seeded.get("error") else {})}


def sync(force: bool = False) -> dict:
    """Best-effort live pull from the NIST CSRC publications feed.

    Offline-safe: honors the docmod_config ``offline`` flag, gates on
    ``nist_pubs_cadence_hours`` (skip if a live sync ran inside the window unless
    ``force``), and swallows network errors so the sweep never fails.

    Always reports ``fetch_status`` (a FETCH_STATUS key) so a caller can tell a
    retired URL from an air-gapped host from a cadence skip. When the pull lands
    nothing AND the cache is empty, falls back to the static seed so the
    policy_refs pack is never left without a substrate.
    """
    cfg = _config()
    if cfg.get("offline"):
        # Air-gap: no egress attempt at all, and no seed fallback either — an
        # offline site loads its substrate deliberately (--seed / --import).
        return {"synced": 0, "skipped": FETCH_STATUS["offline"],
                "fetch_status": "offline"}

    cadence = float(cfg.get("nist_pubs_cadence_hours", 24) or 0)
    conn = _connect()
    try:
        if not force and _within_cadence(conn, cadence):
            return {"synced": 0, "skipped": FETCH_STATUS["not_attempted"],
                    "fetch_status": "not_attempted"}

        url = cfg.get("nist_pubs_feed_url") or DEFAULT_FEED_URL
        timeout = int(cfg.get("nist_pubs_timeout_seconds", 15) or 15)
        body, status = _fetch_feed(url, timeout)
        if body is None:
            return {"synced": 0, "skipped": FETCH_STATUS[status],
                    "fetch_status": status, "feed_url": url,
                    **_seed_if_empty(conn)}

        rows = parse_feed(body)
        if not rows:
            # 200 OK but nothing parsed: a live-but-wrong endpoint, distinct
            # from both a 404 and an unreachable host.
            return {"synced": 0, "skipped": FETCH_STATUS["empty_feed"],
                    "fetch_status": "empty_feed", "feed_url": url,
                    **_seed_if_empty(conn)}

        synced = 0
        for row in rows:
            _upsert(conn, row["pub_id"], row, "nist.gov")
            synced += 1
        conn.commit()
        return {"synced": synced, "fetch_status": "ok",
                "publications": [r["pub_id"] for r in rows]}
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
    # Bare invocation runs the namesake action. It used to print help and exit 1,
    # so the documented `python -m tools.doc_modernization.nist_pubs_sync` did
    # nothing at all; --sync stays accepted and every other flag is unchanged.
    if args.sync or not (args.seed or args.import_path):
        result["sync"] = sync(force=args.force)
    if args.import_path:
        result["import"] = import_dataset(args.import_path)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
