# CUI // SP-CTI
"""OSINT Harvester Reflex — three-tier signal collection for ICDEV™ Strategos.

Tier resolution order (first reachable wins):
    TIER_INTERNET   — RSS feeds, Telegram, Twitter/X
    TIER_GITLAB     — GitLab CI artifact download
    TIER_FILE_INBOX — Local JSON/TXT files in data/osint_inbox/
    TIER_NONE       — No source available; write audit row, exit 0

Deduplication: sha256(url or headline+date). Emits per-run summary.
Config: args/strategos_config.yaml (osint section).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import io
import json
import logging
import os
import shutil
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg  # noqa: E402
from tools.strategos.tier_resolver import (  # noqa: E402
    TIER_FILE_INBOX,
    TIER_GITLAB,
    TIER_INTERNET,
    TIER_NONE,
    resolve_tiers,
)

logger = get_logger(__name__)

_INBOX = BASE_DIR / "data" / "osint_inbox"
_FILE_INBOX_MAX_DEFAULT = 500
_MAX_SIGNALS_PER_RUN_DEFAULT = 200
_FETCH_TIMEOUT_SEC_DEFAULT = 30


# ── helpers ──────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_config() -> Dict[str, Any]:
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "strategos_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _ensure_tables() -> None:
    """Create sg_raw_signals and sg_raw_signals_audit if they don't exist yet."""
    # Fast-path: skip migration if tables already exist (avoids PG transaction abort)
    try:
        conn = get_connection()
        if is_pg():
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.tables "
                "WHERE table_name IN ('sg_raw_signals','sg_raw_signals_audit')"
            ).fetchone()
        else:
            # pg-portability: sqlite-only path — reached only when the backend is
            # SQLite (PG uses the information_schema branch above).
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sqlite_master "
                "WHERE type='table' AND name IN ('sg_raw_signals','sg_raw_signals_audit')"
            ).fetchone()
        conn.close()
        cnt = row["cnt"] if isinstance(row, dict) else row[0]
        if cnt >= 2:
            return
    except Exception:
        pass

    try:
        from tools.db.migrations.run_migration import run_migration  # type: ignore

        run_migration("052_sg_raw_signals")
    except Exception:
        # Inline fallback: create tables directly so the harvester can run
        # even before the migration runner is invoked.
        try:
            from tools.db.migrations._052_sg_raw_signals.up import up  # noqa: F401
        except Exception:
            pass
        try:
            conn = get_connection()
            _pg = is_pg()
            # Clear any aborted transaction from the failed migration attempt
            if _pg:
                try:
                    conn.rollback()
                except Exception:
                    pass
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sg_raw_signals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "url_hash TEXT NOT NULL UNIQUE,"
                "title TEXT NOT NULL,"
                "body TEXT,"
                "source TEXT,"
                "signal_date TEXT,"
                "geo_hint TEXT,"
                "tier_used TEXT,"
                "created_at TEXT NOT NULL,"
                "processed INTEGER NOT NULL DEFAULT 0"
                ")"
                if not _pg
                else "CREATE TABLE IF NOT EXISTS sg_raw_signals ("
                "id SERIAL PRIMARY KEY,"
                "url_hash TEXT NOT NULL UNIQUE,"
                "title TEXT NOT NULL,"
                "body TEXT,"
                "source TEXT,"
                "signal_date TEXT,"
                "geo_hint TEXT,"
                "tier_used TEXT,"
                "created_at TEXT NOT NULL,"
                "processed INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            # Idempotent: add processed column if table was created by migration 052
            try:
                conn.execute(
                    "ALTER TABLE sg_raw_signals ADD COLUMN processed INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sg_raw_signals_audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "run_at TEXT NOT NULL,"
                "tier TEXT NOT NULL,"
                "signals_harvested INTEGER NOT NULL DEFAULT 0,"
                "duplicates_skipped INTEGER NOT NULL DEFAULT 0,"
                "errors INTEGER NOT NULL DEFAULT 0,"
                "reason TEXT"
                ")"
                if not _pg
                else "CREATE TABLE IF NOT EXISTS sg_raw_signals_audit ("
                "id SERIAL PRIMARY KEY,"
                "run_at TEXT NOT NULL,"
                "tier TEXT NOT NULL,"
                "signals_harvested INTEGER NOT NULL DEFAULT 0,"
                "duplicates_skipped INTEGER NOT NULL DEFAULT 0,"
                "errors INTEGER NOT NULL DEFAULT 0,"
                "reason TEXT"
                ")"
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Could not ensure sg_raw_signals tables: %s", exc)


def _is_duplicate(url_hash: str) -> bool:
    try:
        conn = get_connection()
        if is_pg():
            query = "SELECT COUNT(*) AS cnt FROM sg_raw_signals WHERE url_hash = %s"
        else:
            query = "SELECT COUNT(*) AS cnt FROM sg_raw_signals WHERE url_hash = ?"
        row = conn.execute(query, (url_hash,)).fetchone()
        conn.close()
        cnt = row["cnt"] if isinstance(row, dict) else row[0]
        return cnt > 0
    except Exception:
        return False


def _insert_signal(
    url_hash: str,
    title: str,
    body: str,
    source: str,
    signal_date: str,
    geo_hint: Optional[str],
    tier: str,
) -> bool:
    """Insert a signal row. Returns True if inserted, False if duplicate/error."""
    try:
        conn = get_connection()
        if is_pg():
            query = (
                "INSERT INTO sg_raw_signals "
                "(url_hash, title, body, source, signal_date, geo_hint, tier_used, created_at, processed) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (url_hash) DO NOTHING"
            )
        else:
            query = (
                "INSERT OR IGNORE INTO sg_raw_signals "
                "(url_hash, title, body, source, signal_date, geo_hint, tier_used, created_at, processed) "
                "VALUES (?,?,?,?,?,?,?,?,?)"
            )
        conn.execute(query, (url_hash, title[:1000], (body or "")[:4000], source, signal_date, geo_hint, tier, _utcnow_iso(), 0))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.debug("Insert signal failed: %s", exc)
        return False


def _write_audit(
    tier: str,
    signals_harvested: int,
    duplicates_skipped: int,
    errors: int,
    reason: Optional[str] = None,
) -> None:
    try:
        conn = get_connection()
        if is_pg():
            query = (
                "INSERT INTO sg_raw_signals_audit "
                "(run_at, tier, signals_harvested, duplicates_skipped, errors, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s)"
            )
        else:
            query = (
                "INSERT INTO sg_raw_signals_audit "
                "(run_at, tier, signals_harvested, duplicates_skipped, errors, reason) "
                "VALUES (?,?,?,?,?,?)"
            )
        conn.execute(query, (_utcnow_iso(), tier, signals_harvested, duplicates_skipped, errors, reason))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Could not write audit row: %s", exc)


# ── fetch helpers ─────────────────────────────────────────────────────────────

def _get_thresholds(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return harvest thresholds from config with fallbacks to module-level constants."""
    osint = config.get("osint", {})
    return {
        "max_signals_per_run": osint.get("max_signals_per_run", _MAX_SIGNALS_PER_RUN_DEFAULT),
        "acled_fetch_limit": osint.get("acled_fetch_limit", 100),
        "telegram_message_limit": osint.get("telegram_message_limit", 50),
        "fetch_timeout_sec": osint.get("fetch_timeout_sec", _FETCH_TIMEOUT_SEC_DEFAULT),
        "file_inbox_max": osint.get("file_inbox_max", _FILE_INBOX_MAX_DEFAULT),
    }


def _fetch_url(url: str, timeout: int = 30, headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
    if not url.startswith(("http://", "https://")):
        logger.debug("Rejected non-http URL: %s", url[:100])
        return None
    try:
        h = {"User-Agent": "ICDEV-Strategos-OSINT/1.0"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.read()
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url, exc)
        return None


def _parse_rss(xml_bytes: bytes) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    try:
        content = xml_bytes.decode("utf-8", errors="replace")
        try:
            import defusedxml.ElementTree as _dxml  # type: ignore
            root = _dxml.fromstring(content)
        except ImportError:
            root = ET.fromstring(content)  # nosec B314 — defusedxml not available; content from admin-configured feeds
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            entries.append({
                "title": title,
                "body": (item.findtext("description") or "")[:2000].strip(),
                "url": (item.findtext("link") or "").strip(),
                "date": (item.findtext("pubDate") or "").strip(),
            })
        if not entries:
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//a:entry", ns):
                title = (entry.findtext("a:title", "", ns) or "").strip()
                if not title:
                    continue
                link_el = entry.find("a:link", ns)
                entries.append({
                    "title": title,
                    "body": (entry.findtext("a:summary", "", ns) or "")[:2000].strip(),
                    "url": link_el.get("href", "") if link_el is not None else "",
                    "date": (entry.findtext("a:updated", "", ns) or "").strip(),
                })
    except ET.ParseError:
        pass
    return entries


# ── TIER_INTERNET ─────────────────────────────────────────────────────────────

def _harvest_internet(config: Dict[str, Any], thresholds: Dict[str, Any]) -> Tuple[int, int, int]:
    """RSS feeds from strategos_config.yaml osint.sources[internet_required=true].

    Returns (harvested, duplicates_skipped, errors).
    """
    sources = config.get("osint", {}).get("sources", [])
    internet_sources = [s for s in sources if s.get("internet_required", True)]
    max_signals = thresholds["max_signals_per_run"]
    timeout = thresholds["fetch_timeout_sec"]

    harvested = dupes = errors = 0

    for src in internet_sources:
        url = src.get("url", "")
        name = src.get("name", url)
        feed_type = src.get("type", "rss")

        if not url:
            continue

        raw = _fetch_url(url, timeout=timeout)
        if raw is None:
            logger.warning("OSINT INTERNET: failed to fetch %s", name)
            errors += 1
            continue

        try:
            if feed_type == "rss":
                entries = _parse_rss(raw)
            else:
                entries = []
                logger.debug("Unsupported feed type %s for %s", feed_type, name)

            for e in entries:
                title = e.get("title", "")
                body = e.get("body", "")
                entry_url = e.get("url", "")
                date = e.get("date", "")

                url_hash = _sha256((entry_url or "") + title)

                if _is_duplicate(url_hash):
                    dupes += 1
                    continue

                inserted = _insert_signal(url_hash, title, body, name, date, None, TIER_INTERNET)
                if inserted:
                    harvested += 1
                    if harvested >= max_signals:
                        return harvested, dupes, errors

        except Exception as exc:
            logger.warning("OSINT INTERNET: parse error for %s: %s", name, exc)
            errors += 1

    if harvested >= max_signals:
        return harvested, dupes, errors

    # ACLED REST API (optional — graceful if ACLED_API_KEY / ACLED_EMAIL not set)
    try:
        h, d, er = _harvest_acled(config, thresholds)
        harvested += h
        dupes += d
        errors += er
    except Exception:
        pass

    if harvested >= max_signals:
        return harvested, dupes, errors

    # Telegram via Telethon (optional — graceful if not installed or unconfigured)
    try:
        h, d, er = _harvest_telegram(thresholds)
        harvested += h
        dupes += d
        errors += er
    except Exception:
        pass

    if harvested >= max_signals:
        return harvested, dupes, errors

    # Twitter/X via snscrape (optional — graceful if not installed)
    try:
        h, d, er = _harvest_snscrape(config, thresholds)
        harvested += h
        dupes += d
        errors += er
    except Exception:
        pass

    return harvested, dupes, errors


def _harvest_acled(config: Dict[str, Any], thresholds: Dict[str, Any]) -> Tuple[int, int, int]:
    """Collect from ACLED REST API. Graceful if ACLED_API_KEY / ACLED_EMAIL not set."""
    api_key = os.getenv("ACLED_API_KEY", "")
    email = os.getenv("ACLED_EMAIL", "")
    if not (api_key and email):
        return 0, 0, 0

    harvested = dupes = errors = 0
    acled_limit = thresholds["acled_fetch_limit"]
    max_signals = thresholds["max_signals_per_run"]
    timeout = thresholds["fetch_timeout_sec"]
    base = "https://api.acleddata.com/acled/read"
    params = f"?key={api_key}&email={email}&limit={acled_limit}&format=json"
    url = f"{base}{params}"

    raw = _fetch_url(url, timeout=timeout)
    if raw is None:
        return 0, 0, 1

    try:
        data = json.loads(raw.decode("utf-8"))
        events = data.get("data", [])
        for ev in events:
            title = (ev.get("notes") or "")[:200].strip() or ev.get("event_type", "ACLED event")
            body = json.dumps({k: ev[k] for k in ("event_type", "actor1", "actor2", "country", "location") if k in ev}, ensure_ascii=False)
            source = f"ACLED/{ev.get('country', '')}"
            date = ev.get("event_date", "")
            geo_hint = ev.get("country") or ev.get("location")
            event_id = str(ev.get("data_id") or ev.get("event_id_cnty") or "")
            url_hash = _sha256(event_id + title) if event_id else _sha256("" + title)
            if _is_duplicate(url_hash):
                dupes += 1
                continue
            ok = _insert_signal(url_hash, title, body, source, date, geo_hint, TIER_INTERNET)
            if ok:
                harvested += 1
                if harvested >= max_signals:
                    break
    except Exception as exc:
        logger.warning("ACLED parse error: %s", exc)
        errors += 1

    return harvested, dupes, errors


def _harvest_telegram(thresholds: Dict[str, Any]) -> Tuple[int, int, int]:
    """Collect from Telegram channels via Telethon. Graceful if unavailable."""
    try:
        import telethon  # noqa: F401 — import just to check availability
    except ImportError:
        return 0, 0, 0

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    channels_raw = os.getenv("TELEGRAM_OSINT_CHANNELS", "")

    if not (api_id and api_hash and channels_raw):
        return 0, 0, 0

    msg_limit = thresholds["telegram_message_limit"]
    harvested = dupes = errors = 0
    channels = [c.strip() for c in channels_raw.split(",") if c.strip()]

    try:
        from telethon.sync import TelegramClient  # type: ignore

        with TelegramClient("icdev_osint_session", int(api_id), api_hash) as client:
            for channel in channels:
                try:
                    for msg in client.iter_messages(channel, limit=msg_limit):
                        if not msg.text:
                            continue
                        lines = msg.text.strip().splitlines()
                        title = lines[0][:200] if lines else "Telegram message"
                        body = "\n".join(lines[1:])[:2000] if len(lines) > 1 else ""
                        date = msg.date.strftime("%Y-%m-%dT%H:%M:%SZ") if msg.date else _utcnow_iso()
                        url_hash = _sha256(f"tg:{channel}:{msg.id}")
                        if _is_duplicate(url_hash):
                            dupes += 1
                            continue
                        _insert_signal(url_hash, title, body, f"Telegram/{channel}", date, None, TIER_INTERNET)
                        harvested += 1
                except Exception as exc:
                    logger.warning("Telegram channel %s error: %s", channel, exc)
                    errors += 1
    except Exception as exc:
        logger.warning("Telegram client error: %s", exc)
        errors += 1

    return harvested, dupes, errors


def _harvest_snscrape(config: Dict[str, Any], thresholds: Dict[str, Any]) -> Tuple[int, int, int]:
    """Collect from Twitter/X via snscrape. Graceful if unavailable."""
    try:
        import snscrape.modules.twitter as sntwitter  # type: ignore
    except ImportError:
        return 0, 0, 0

    queries_raw = os.getenv("OSINT_TWITTER_QUERIES", "")
    if not queries_raw:
        return 0, 0, 0

    max_signals = thresholds["max_signals_per_run"]
    harvested = dupes = errors = 0
    queries = [q.strip() for q in queries_raw.split("|") if q.strip()]

    for query in queries:
        try:
            for tweet in sntwitter.TwitterSearchScraper(query).get_items():
                url_hash = _sha256(f"twitter:{tweet.id}")
                if _is_duplicate(url_hash):
                    dupes += 1
                    continue
                title = (tweet.rawContent or "")[:200]
                date = tweet.date.strftime("%Y-%m-%dT%H:%M:%SZ") if tweet.date else _utcnow_iso()
                _insert_signal(url_hash, title, "", f"Twitter/{query[:50]}", date, None, TIER_INTERNET)
                harvested += 1
                if harvested >= max_signals:
                    break
        except Exception as exc:
            logger.warning("snscrape query '%s' error: %s", query, exc)
            errors += 1

    return harvested, dupes, errors


# ── TIER_GITLAB ───────────────────────────────────────────────────────────────

def _harvest_gitlab(config: Dict[str, Any]) -> Tuple[int, int, int]:
    """Download GitLab CI artifact, parse osint_signals.json (and kg_delta.json).

    Returns (harvested, duplicates_skipped, errors).
    Falls through silently on any HTTP/artifact error.
    """
    gitlab_url = os.getenv("GITLAB_URL", "").rstrip("/")
    token = os.getenv("GITLAB_TOKEN", "")
    project_id = os.getenv("GITLAB_OSINT_PROJECT_ID", "")
    ref = os.getenv("GITLAB_OSINT_REF", "main")

    gl_cfg = config.get("osint", {}).get("gitlab", {})
    job_name = gl_cfg.get("job_name", "osint_collect")
    signals_file = gl_cfg.get("signals_file", "osint_signals.json")
    kg_delta_file = gl_cfg.get("kg_delta_file", "kg_delta.json")

    if not (gitlab_url and token and project_id):
        logger.warning("OSINT GITLAB: GITLAB_URL / GITLAB_TOKEN / GITLAB_OSINT_PROJECT_ID not set")
        return 0, 0, 1

    if not gitlab_url.startswith(("http://", "https://")):
        logger.warning("OSINT GITLAB: GITLAB_URL must use http/https scheme")
        return 0, 0, 1

    artifact_url = (
        f"{gitlab_url}/api/v4/projects/{project_id}/jobs/artifacts/{ref}"
        f"/download?job={job_name}"
    )
    headers = {"PRIVATE-TOKEN": token, "User-Agent": "ICDEV-Strategos-OSINT/1.0"}

    timeout = config.get("osint", {}).get("fetch_timeout_sec", _FETCH_TIMEOUT_SEC_DEFAULT)
    try:
        req = urllib.request.Request(artifact_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        logger.warning("OSINT GITLAB: HTTP %s fetching artifact — falling through to FILE_INBOX", exc.code)
        return 0, 0, 0
    except Exception as exc:
        logger.warning("OSINT GITLAB: artifact download failed (%s) — falling through to FILE_INBOX", exc)
        return 0, 0, 0

    harvested = dupes = errors = 0

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()

            # Parse osint_signals.json
            if signals_file in names:
                try:
                    signals_raw = json.loads(zf.read(signals_file).decode("utf-8"))
                    signals = signals_raw if isinstance(signals_raw, list) else signals_raw.get("signals", [])
                    for sig in signals:
                        h, d, er = _insert_normalized_signal(sig, TIER_GITLAB)
                        harvested += h
                        dupes += d
                        errors += er
                except Exception as exc:
                    logger.warning("OSINT GITLAB: parse %s failed: %s", signals_file, exc)
                    errors += 1
            else:
                logger.warning("OSINT GITLAB: %s not found in artifact zip", signals_file)

            # Parse kg_delta.json and queue KG enrichment if present
            if kg_delta_file in names:
                try:
                    kg_raw = json.loads(zf.read(kg_delta_file).decode("utf-8"))
                    _queue_kg_enrichment(kg_raw)
                except Exception as exc:
                    logger.warning("OSINT GITLAB: KG delta parse failed: %s", exc)

    except zipfile.BadZipFile as exc:
        logger.warning("OSINT GITLAB: artifact is not a valid zip: %s", exc)
        errors += 1

    return harvested, dupes, errors


def _insert_normalized_signal(sig: Dict[str, Any], tier: str) -> Tuple[int, int, int]:
    """Insert a signal that may use flexible key names."""
    title = (
        sig.get("title") or sig.get("headline") or sig.get("name") or ""
    ).strip()[:1000]
    body = (
        sig.get("body") or sig.get("content") or sig.get("text") or sig.get("description") or ""
    )[:4000]
    source = (sig.get("source") or sig.get("origin") or "").strip()
    date = (sig.get("date") or sig.get("published") or sig.get("timestamp") or "").strip()
    url = (sig.get("url") or sig.get("link") or "").strip()
    geo_hint = sig.get("geo_hint") or sig.get("geo") or None

    if not title:
        return 0, 0, 1

    url_hash = _sha256((url or "") + title)

    if _is_duplicate(url_hash):
        return 0, 1, 0

    ok = _insert_signal(url_hash, title, body, source, date, geo_hint, tier)
    return (1, 0, 0) if ok else (0, 0, 1)


def _queue_kg_enrichment(kg_delta: Any) -> None:
    """Queue KG enrichment nodes from kg_delta.json for downstream processing."""
    try:
        nodes = kg_delta if isinstance(kg_delta, list) else kg_delta.get("nodes", [])
        if not nodes:
            return
        conn = get_connection()
        if is_pg():
            kg_query = "INSERT INTO kg_nodes (node_type, label, created_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING"
        else:
            kg_query = "INSERT OR IGNORE INTO kg_nodes (node_type, label, created_at) VALUES (?,?,?)"
        now = _utcnow_iso()
        for node in nodes:
            node_type = (node.get("type") or "osint_signal").strip()
            label = (node.get("label") or node.get("name") or "").strip()
            if not label:
                continue
            try:
                conn.execute(kg_query, (node_type, label, now))
            except Exception:
                pass
        conn.commit()
        conn.close()
        logger.info("OSINT GITLAB: queued %d KG enrichment nodes", len(nodes))
    except Exception as exc:
        logger.warning("KG enrichment queue failed: %s", exc)


# ── TIER_FILE_INBOX ───────────────────────────────────────────────────────────

def _harvest_file_inbox(config: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None) -> Tuple[int, int, int]:
    """Process data/osint_inbox/*.json and *.txt. Move processed files to processed/{date}/.

    Returns (harvested, duplicates_skipped, errors).
    """
    if thresholds is not None:
        max_files = thresholds["file_inbox_max"]
    else:
        max_files = config.get("osint", {}).get("file_inbox_max", _FILE_INBOX_MAX_DEFAULT)

    if not _INBOX.is_dir():
        logger.info("OSINT FILE_INBOX: inbox directory does not exist")
        return 0, 0, 0

    files = [
        f for f in _INBOX.iterdir()
        if f.is_file() and f.suffix.lower() in (".json", ".txt")
    ][:max_files]

    if not files:
        return 0, 0, 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    processed_dir = _INBOX / "processed" / today
    processed_dir.mkdir(parents=True, exist_ok=True)

    harvested = dupes = errors = 0

    for fpath in files:
        try:
            suffix = fpath.suffix.lower()
            if suffix == ".json":
                h, d, er = _process_inbox_json(fpath)
            else:
                h, d, er = _process_inbox_txt(fpath)

            harvested += h
            dupes += d
            errors += er

            # Move regardless of outcome to avoid re-processing
            dest = processed_dir / fpath.name
            if dest.exists():
                dest = processed_dir / f"{fpath.stem}_{int(datetime.now(timezone.utc).timestamp())}{fpath.suffix}"
            shutil.move(str(fpath), str(dest))

        except Exception as exc:
            logger.warning("OSINT FILE_INBOX: error processing %s: %s", fpath.name, exc)
            errors += 1

    return harvested, dupes, errors


def _process_inbox_json(fpath: Path) -> Tuple[int, int, int]:
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        signals = data
    elif isinstance(data, dict) and "signals" in data:
        signals = data["signals"]
    else:
        signals = [data]

    harvested = dupes = errors = 0
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        h, d, er = _insert_normalized_signal(sig, TIER_FILE_INBOX)
        harvested += h
        dupes += d
        errors += er

    return harvested, dupes, errors


def _process_inbox_txt(fpath: Path) -> Tuple[int, int, int]:
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        return 0, 0, 0

    title = lines[0].strip()[:1000]
    body = "\n".join(lines[1:])[:4000] if len(lines) > 1 else ""
    source = fpath.stem
    date = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    url_hash = _sha256("" + title)
    if _is_duplicate(url_hash):
        return 0, 1, 0

    ok = _insert_signal(url_hash, title, body, source, date, None, TIER_FILE_INBOX)
    return (1, 0, 0) if ok else (0, 0, 1)


# ── anomaly detection ─────────────────────────────────────────────────────────

def _get_rolling_stats(window: int) -> Tuple[Optional[float], Optional[float], int]:
    """Return (mean, std_dev, sample_size) from the last `window` audit rows.

    Uses sample std deviation (ddof=1) when n >= 2, returns 0.0 when n == 1.
    Returns (None, None, 0) when no rows are available.
    """
    try:
        conn = get_connection()
        if is_pg():
            query = (
                "SELECT signals_harvested FROM sg_raw_signals_audit "
                "ORDER BY id DESC LIMIT %s"
            )
        else:
            query = (
                "SELECT signals_harvested FROM sg_raw_signals_audit "
                "ORDER BY id DESC LIMIT ?"
            )
        rows = conn.execute(query, (window,)).fetchall()
        conn.close()
        if not rows:
            return None, None, 0
        counts = [float(r["signals_harvested"] if isinstance(r, dict) else r[0]) for r in rows]
        n = len(counts)
        mean = sum(counts) / n
        if n < 2:
            return mean, 0.0, n
        variance = sum((x - mean) ** 2 for x in counts) / (n - 1)
        return mean, variance ** 0.5, n
    except Exception:
        return None, None, 0


def _get_rolling_baseline(window: int) -> Optional[float]:
    """Thin wrapper kept for backward compatibility."""
    mean, _, _ = _get_rolling_stats(window)
    return mean


def _compute_zscore(harvested: int, mean: float, std_dev: float) -> Optional[float]:
    """Return Z-score of `harvested` relative to historical distribution, or None."""
    if std_dev > 0:
        return round((harvested - mean) / std_dev, 2)
    return None


def _detect_anomalies(config: Dict[str, Any], harvested: int, tier: str) -> Dict[str, Any]:
    """Score signal spike anomaly using adaptive Z-score statistics + LLM classification.

    Computes rolling mean and standard deviation from historical audit rows, then
    derives a Z-score so the LLM prompt carries statistically meaningful context
    rather than a fixed ratio.  The LLM verdict drives the result; the Z-score
    fallback (configurable via zscore_threshold) fires only when the LLM is
    unavailable.
    """
    ad_cfg = config.get("osint", {}).get("anomaly_detection", {})
    if not ad_cfg.get("enabled", True):
        return {"enabled": False}

    min_signals = ad_cfg.get("min_signals_for_detection", 10)
    if harvested < min_signals:
        return {"enabled": True, "skipped": "below_min_signals", "harvested": harvested}

    # zscore_threshold: fallback spike gate (standard deviations above mean)
    zscore_threshold = ad_cfg.get("zscore_threshold", 2.5)
    # spike_threshold: ratio fallback when std_dev == 0 (all historical runs identical)
    spike_threshold = ad_cfg.get("spike_threshold", 3.0)
    baseline_window = ad_cfg.get("baseline_window", 10)
    model = ad_cfg.get("model", "claude-haiku-4-5-20251001")
    sample_size = ad_cfg.get("sample_size", 30)
    prompt_titles = ad_cfg.get("prompt_titles", 20)
    title_truncate_len = ad_cfg.get("title_truncate_len", 120)

    mean, std_dev, n_samples = _get_rolling_stats(baseline_window)

    if mean is None:
        return {
            "enabled": True,
            "skipped": "no_baseline",
            "harvested": harvested,
            "sample_size": 0,
        }

    z_score = _compute_zscore(harvested, mean, std_dev or 0.0)
    ratio = round(harvested / mean, 2) if mean > 0 else None

    result: Dict[str, Any] = {
        "enabled": True,
        "harvested": harvested,
        "baseline_avg": round(mean, 1),
        "baseline_std": round(std_dev, 2) if std_dev is not None else None,
        "sample_size": n_samples,
        "z_score": z_score,
        "zscore_threshold": zscore_threshold,
        "spike_threshold": spike_threshold,
        "tier": tier,
    }

    # LLM scoring: fetch recent signal titles and ask the model to assess the anomaly
    try:
        conn = get_connection()
        if is_pg():
            titles_query = (
                "SELECT title FROM sg_raw_signals ORDER BY id DESC LIMIT %s"
            )
        else:
            titles_query = (
                "SELECT title FROM sg_raw_signals ORDER BY id DESC LIMIT ?"
            )
        rows = conn.execute(titles_query, (min(harvested, sample_size),)).fetchall()
        conn.close()
        titles = [r["title"] if isinstance(r, dict) else r[0] for r in rows]
    except Exception:
        titles = []

    z_desc = f"{z_score:.2f} std devs above mean" if z_score is not None else f"{ratio}x ratio (std dev unavailable)"
    sample = "\n".join(f"- {t[:title_truncate_len]}" for t in titles[:prompt_titles]) if titles else "(no titles available)"
    prompt = (
        f"OSINT harvest run: {harvested} signals collected from tier '{tier}'.\n"
        f"Rolling baseline (last {n_samples} runs): mean={round(mean, 1)}, std_dev={round(std_dev or 0.0, 2)}.\n"
        f"Statistical deviation: {z_desc}.\n"
        f"Sample of recent signal titles:\n{sample}\n\n"
        "Assess whether this harvest represents an anomaly. Consider both statistical deviation and signal content.\n"
        "Reply with JSON only: "
        '{"verdict": "<normal|suspicious|surge|noise_flood>", "is_anomaly": <true|false>, "reason": "<one sentence>"}'
    )

    try:
        from tools.llm.anthropic_provider import anthropic_sdk  # noqa: PLC0415

        client = anthropic_sdk.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = (msg.content[0].text if msg.content else "{}").strip()
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        parsed = json.loads(raw_text[start:end]) if start >= 0 else {}
        result["verdict"] = parsed.get("verdict", "normal")
        result["is_anomaly"] = parsed.get("is_anomaly", False)
        result["reason"] = parsed.get("reason", "")
    except Exception as exc:
        logger.debug("Anomaly LLM call failed: %s", exc)
        # Fallback: use Z-score gate when LLM is unavailable
        if z_score is not None:
            is_anomaly_fallback = z_score > zscore_threshold
        elif mean > 0:
            is_anomaly_fallback = harvested > mean * spike_threshold
        else:
            is_anomaly_fallback = False
        result["verdict"] = "spike_detected" if is_anomaly_fallback else "normal"
        result["is_anomaly"] = is_anomaly_fallback
        result["llm_error"] = str(exc)

    if result.get("verdict") in ("suspicious", "noise_flood"):
        logger.warning("OSINT anomaly detected: %s — %s", result["verdict"], result.get("reason", ""))
    else:
        logger.info("OSINT anomaly check: %s", result.get("verdict"))

    return result


# ── reflex entry point ────────────────────────────────────────────────────────

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point. Returns {success, metric_value, details}."""
    _ensure_tables()

    cfg = _load_config()
    thresholds = _get_thresholds(cfg)
    tier_status = resolve_tiers()
    tier = tier_status.osint_tier

    harvested = dupes = errors = 0
    effective_tier = tier

    if tier == TIER_INTERNET:
        harvested, dupes, errors = _harvest_internet(cfg, thresholds)

    elif tier == TIER_GITLAB:
        harvested, dupes, errors = _harvest_gitlab(cfg)
        if harvested == 0 and dupes == 0:
            # Fall through to FILE_INBOX on empty/unreachable artifact
            effective_tier = TIER_FILE_INBOX
            harvested, dupes, errors = _harvest_file_inbox(cfg, thresholds)
            if harvested == 0 and dupes == 0:
                effective_tier = TIER_NONE

    elif tier == TIER_FILE_INBOX:
        harvested, dupes, errors = _harvest_file_inbox(cfg, thresholds)
        if harvested == 0 and dupes == 0:
            effective_tier = TIER_NONE

    else:
        effective_tier = TIER_NONE

    if effective_tier == TIER_NONE:
        msg = "No OSINT source available (internet=False, gitlab=unreachable, inbox=empty). Harvested 0 signals."
        logger.info(msg)
        print(f"  Strategos OSINT: {msg}")
        _write_audit(TIER_NONE, 0, 0, 0, reason="tier_none")
        return {
            "success": True,
            "metric_value": 0,
            "details": {
                "tier": TIER_NONE,
                "signals_harvested": 0,
                "duplicates_skipped": 0,
                "errors": 0,
            },
        }

    _write_audit(effective_tier, harvested, dupes, errors)

    summary = (
        f"tier={effective_tier} harvested={harvested} dupes_skipped={dupes} errors={errors}"
    )
    print(f"  Strategos OSINT: {summary}")
    logger.info("OSINT harvest complete: %s", summary)

    # LLM anomaly detection — scores signal spikes against rolling baseline
    anomaly = _detect_anomalies(cfg, harvested, effective_tier)

    # Normalize newly ingested raw signals into the unified schema
    norm_result: Dict[str, Any] = {"inserted": 0, "errors": 0}
    try:
        from tools.threat_analysis.osint_normalizer import run_normalization  # noqa: PLC0415
        norm_result = run_normalization(limit=harvested + 100)
        logger.info(
            "OSINT normalization: inserted=%d errors=%d",
            norm_result.get("inserted", 0),
            norm_result.get("errors", 0),
        )
    except Exception as _norm_exc:
        logger.warning("OSINT normalizer skipped: %s", _norm_exc)

    return {
        "success": True,
        "metric_value": harvested,
        "details": {
            "tier": effective_tier,
            "signals_harvested": harvested,
            "duplicates_skipped": dupes,
            "errors": errors,
            "normalized": norm_result.get("inserted", 0),
            "normalization_errors": norm_result.get("errors", 0),
            "anomaly": anomaly,
        },
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="ICDEV™ Strategos OSINT Harvester")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    result = run({}, None)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        d = result.get("details", {})
        print(f"Tier     : {d.get('tier')}")
        print(f"Harvested: {d.get('signals_harvested')}")
        print(f"Dupes    : {d.get('duplicates_skipped')}")
        print(f"Errors   : {d.get('errors')}")
