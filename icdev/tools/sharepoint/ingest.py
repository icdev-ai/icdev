#!/usr/bin/env python3
# CUI // SP-CTI
"""tools/sharepoint/ingest — SharePoint list/library walker and DB persister.

Phase E4 / P4.1: implements ``ingest_all(config)`` that walks every site URL
in ``site_scope``, enumerates lists + items + documents, upserts to
``sharepoint_*`` tables via ``get_connection()``, and emits an audit event via
``tools.audit.audit_logger``.

Never calls ``sqlite3.connect()`` directly — all DB access goes through
``tools.db.storage.get_connection()`` which transparently supports both SQLite
(dev) and PostgreSQL (prod).

CLI::

    python -m tools.sharepoint.ingest --json                  # all sites from config
    python -m tools.sharepoint.ingest --site URL --json       # single site
    python -m tools.sharepoint.ingest --dry-run --json        # preview, no DB writes
    python tools/sharepoint/ingest.py --site URL [--dry-run] --json
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "sharepoint.yaml"

logger = get_logger("icdev.sharepoint.ingest")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load and return the ``sharepoint`` block from args/sharepoint.yaml."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return raw.get("sharepoint", {})


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


def _resolve_credentials(cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(username, password)`` based on ``credential_source`` setting.

    - ``env``         → ``ICDEV_SHAREPOINT_USER`` + ``ICDEV_SHAREPOINT_PASS``
    - ``windows_sso`` → ``(None, None)`` — ambient Kerberos/NTLM TGT
    - ``vault``       → fetched from Vault via ``ICDEV_VAULT_ADDR`` token
    """
    source = cfg.get("credential_source", "env")
    if source == "env":
        return (
            os.environ.get("ICDEV_SHAREPOINT_USER"),
            os.environ.get("ICDEV_SHAREPOINT_PASS"),
        )
    if source == "windows_sso":
        # Ambient credential; SharePointClient handles it via kerberos auth.
        return None, None
    if source == "vault":
        return _resolve_vault_credentials(cfg)
    logger.warning("Unknown credential_source=%r — falling back to env", source)
    return (
        os.environ.get("ICDEV_SHAREPOINT_USER"),
        os.environ.get("ICDEV_SHAREPOINT_PASS"),
    )


def _resolve_vault_credentials(cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Fetch username/password from a local Vault agent."""
    vault_addr = os.environ.get("ICDEV_VAULT_ADDR", "http://localhost:8200")
    vault_token = os.environ.get("ICDEV_VAULT_TOKEN")
    vault_key = cfg.get("vault_key", "secret/icdev/sharepoint")
    if not vault_token:
        raise ValueError("ICDEV_VAULT_TOKEN env var required for credential_source=vault")
    import requests  # already a hard dep via client.py
    resp = requests.get(
        f"{vault_addr}/v1/{vault_key}",
        headers={"X-Vault-Token": vault_token},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return data.get("username"), data.get("password")


# ---------------------------------------------------------------------------
# Stable ID helper
# ---------------------------------------------------------------------------


def _stable_id(*parts: str) -> str:
    """Deterministic 32-char ID derived from URL/list/item components."""
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# DB upsert helpers  (SQLite-style SQL; translate_sql handles PG promotion)
# ---------------------------------------------------------------------------


def _upsert_site(conn, site_id: str, url: str, title: str, last_modified: str | None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sharepoint_sites (id, url, title, last_modified)
           VALUES (%s, %s, %s, %s)""",
        (site_id, url, title or "", last_modified),
    )


def _upsert_list(conn, list_id: str, site_id: str, name: str, item_count: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sharepoint_lists (id, site_id, name, item_count)
           VALUES (%s, %s, %s, %s)""",
        (list_id, site_id, name or "", int(item_count)),
    )


def _upsert_item(
    conn,
    item_id: str,
    list_id: str,
    title: str,
    payload: dict,
    modified_at: str | None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sharepoint_items
           (id, list_id, title, payload, modified_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (item_id, list_id, title or "", json.dumps(payload), modified_at),
    )


def _upsert_document(
    conn,
    doc_id: str,
    site_id: str,
    path: str,
    size: int,
    mime_type: str,
    content_hash: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sharepoint_documents
           (id, site_id, path, size, mime_type, content_hash)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (doc_id, site_id, path or "", int(size), mime_type or "", content_hash),
    )


def _document_change_hash(server_rel: str, size: int, modified_at: Any) -> str:
    """A per-document change token for sharepoint_documents.content_hash.

    This is a metadata list crawl — file content is never downloaded — so a true
    content hash is not available. It must nonetheless CHANGE when the document
    changes, or change-detection built on it silently never fires. Hashing the
    path alone (the original bug) failed that: the path is stable across edits.
    Fold in SharePoint's Modified timestamp (its canonical change signal) and the
    size; keep the path for per-document namespacing.
    """
    signal = f"{server_rel}|{int(size)}|{modified_at or ''}"
    return hashlib.sha256(signal.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-site walk
# ---------------------------------------------------------------------------


def _ingest_via_browser_fallback(
    site_url: str,
    stats: dict[str, int],
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Delegate a site to browser_fallback.fetch_classic_page when REST returns empty/404.

    Counts extracted rows as items.  DB writes are skipped in dry-run mode
    because the fallback rows lack the stable IDs needed for upsert.
    """
    from tools.sharepoint.browser_fallback import fetch_classic_page, FallbackDisabledError
    from tools.sharepoint.selectors import DOCUMENT_LINK_ANCHOR, LIST_ITEM_ROW

    selectors = {
        "Title": DOCUMENT_LINK_ANCHOR,
        "Row": LIST_ITEM_ROW,
    }
    try:
        rows = fetch_classic_page(site_url, selectors)
        logger.info(
            "browser_fallback extracted %d row(s) from %s",
            len(rows), site_url,
        )
        stats["items"] += len(rows)
    except FallbackDisabledError as exc:
        # Config gate changed between the flag check and here — treat as warning.
        logger.warning("browser_fallback gate closed unexpectedly for %s: %s", site_url, exc)
        stats["errors"] += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("browser_fallback error on %s: %s", site_url, exc)
        stats["errors"] += 1
    return stats


def _ingest_site(
    client: Any,
    site_url: str,
    conn: Any | None,
    *,
    dry_run: bool,
    fallback_enabled: bool = False,
) -> dict[str, int]:
    """Walk one site: enumerate lists → items (and document records for libraries).

    Returns per-site counts: ``{lists, items, documents, errors}``.
    When ``dry_run`` is True, ``conn`` is None and no DB writes occur.

    If the REST endpoint returns a 404 or an empty list for a site that is known
    to have content (i.e. it is in ``site_scope``), the behaviour depends on
    ``fallback_enabled`` (from ``args/sharepoint.yaml``):

    - ``fallback_enabled=False`` (default): log a warning and skip the site.
    - ``fallback_enabled=True``: delegate to
      :func:`tools.sharepoint.browser_fallback.fetch_classic_page` and log the
      delegation decision.
    """
    from tools.sharepoint.client import SharePointTransientError, SharePointError

    stats: dict[str, int] = {"lists": 0, "items": 0, "documents": 0, "errors": 0}
    site_id = _stable_id(site_url)

    # Register site row (we update title if the API later gives us one)
    if not dry_run and conn is not None:
        _upsert_site(conn, site_id, site_url, "", None)

    try:
        lists = client.get_lists(site_url)
    except SharePointTransientError as exc:
        logger.warning("Transient error listing %s: %s", site_url, exc)
        stats["errors"] += 1
        return stats
    except SharePointError as exc:
        # 404 or other non-transient REST failure — site may be classic-only.
        logger.warning(
            "REST error listing %s (%s); site may be classic-only",
            site_url, exc,
        )
        lists = []  # fall through to the empty-list fallback check below

    # Empty REST response: the site is in scope but REST returned nothing.
    # Consult fallback_enabled to decide whether to skip or delegate.
    if not lists:
        if not fallback_enabled:
            logger.warning(
                "REST returned empty/404 for site %s; fallback_enabled=False — skipping with warning",
                site_url,
            )
            return stats
        logger.info(
            "REST returned empty/404 for site %s; fallback_enabled=True — delegating to "
            "browser_fallback.fetch_classic_page",
            site_url,
        )
        return _ingest_via_browser_fallback(site_url, stats, dry_run=dry_run)

    for lst in lists:
        # SharePoint list GUIDs may appear as "Id" or "id"
        list_guid = lst.get("Id") or lst.get("id") or ""
        if not list_guid:
            continue

        list_name = (
            lst.get("Title")
            or lst.get("title")
            or lst.get("EntityTypeName")
            or ""
        )
        item_count = int(lst.get("ItemCount") or 0)
        db_list_id = _stable_id(site_url, list_guid)
        stats["lists"] += 1

        # BaseTemplate 101 == Document Library; BaseType 1 == Document Library
        is_doc_library = (
            lst.get("BaseTemplate") == 101
            or lst.get("BaseType") == 1
        )

        if not dry_run and conn is not None:
            _upsert_list(conn, db_list_id, site_id, list_name, item_count)

        try:
            items = client.get_list_items(site_url, list_guid)
        except SharePointTransientError as exc:
            logger.warning("Transient error on list %s [%s]: %s", list_name, list_guid, exc)
            stats["errors"] += 1
            continue

        for item in items:
            # SP REST returns numeric Id; metadata may have a URI id
            item_id_raw = (
                str(item.get("Id") or "")
                or str((item.get("__metadata") or {}).get("id") or "")
            )
            if not item_id_raw:
                continue

            title = (
                item.get("Title")
                or item.get("FileLeafRef")
                or ""
            )
            modified_at = item.get("Modified") or item.get("Last_x0020_Modified")
            db_item_id = _stable_id(site_url, list_guid, item_id_raw)
            stats["items"] += 1

            if not dry_run and conn is not None:
                _upsert_item(conn, db_item_id, db_list_id, title, item, modified_at)

            # For document libraries, record a sharepoint_documents entry too
            if is_doc_library:
                server_rel = item.get("FileRef") or item.get("ServerRelativeUrl") or ""
                size = int(
                    item.get("File_x0020_Size")
                    or item.get("FileSizeDisplay")
                    or 0
                )
                mime = item.get("File_x0020_Type") or ""
                content_hash = _document_change_hash(server_rel, size, modified_at)
                db_doc_id = _stable_id(site_url, server_rel)
                stats["documents"] += 1

                if not dry_run and conn is not None:
                    _upsert_document(
                        conn, db_doc_id, site_id, server_rel, size, mime, content_hash
                    )

    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_all(
    config: dict[str, Any] | None = None,
    *,
    site_override: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest all SharePoint sites from ``config`` into the ICDEV DB.

    Args:
        config: The ``sharepoint`` block from ``args/sharepoint.yaml``.
                Pass ``None`` to auto-load from the default path.
        site_override: Ingest only this site URL, ignoring ``site_scope``.
        dry_run: When ``True``, enumerate sites/lists/items but do NOT write
                 to the database or emit audit events.

    Returns:
        ``{"status": "success"|"partial"|"error", "sites_processed": int,
           "total_lists": int, "total_items": int, "total_documents": int,
           "total_errors": int, "dry_run": bool}``
    """
    from tools.sharepoint.client import (
        SharePointClient,
        SharePointAuthError,
        SharePointTransientError,
    )

    if config is None:
        config = _load_config()

    endpoint = (config.get("endpoint_url") or "").strip()
    auth_mode = config.get("auth_mode", "ntlm")
    verify = config.get("verify_tls", True)
    timeout = float(config.get("request_timeout_sec", 30))
    fallback_enabled: bool = bool(config.get("fallback_enabled", False))
    site_scope: list[str] = (
        [site_override] if site_override else list(config.get("site_scope") or [])
    )

    if not endpoint:
        return {
            "status": "error",
            "error": "endpoint_url is blank in args/sharepoint.yaml",
            "dry_run": dry_run,
        }

    # Resolve credentials
    try:
        username, password = _resolve_credentials(config)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "dry_run": dry_run}

    # Build SharePointClient
    try:
        client = SharePointClient(
            endpoint=endpoint,
            auth_mode=auth_mode,
            username=username,
            password=password,
            timeout=timeout,
            verify=verify,
        )
    except (ValueError, ImportError) as exc:
        return {"status": "error", "error": str(exc), "dry_run": dry_run}

    # When site_scope is empty, discover sites from the root web
    if not site_scope:
        try:
            discovered = client.list_sites()
            site_scope = [s["Url"] for s in discovered if s.get("Url")]
            if not site_scope:
                # Fall back to root site itself
                site_scope = [endpoint]
        except (SharePointAuthError, SharePointTransientError, Exception) as exc:  # noqa: BLE001
            client.close()
            return {"status": "error", "error": f"site discovery failed: {exc}", "dry_run": dry_run}

    # Open one DB connection shared across all sites (None in dry_run)
    conn: Any | None = None
    if not dry_run:
        from tools.db.storage import get_connection
        conn = get_connection()

    total: dict[str, int] = {"lists": 0, "items": 0, "documents": 0, "errors": 0}
    sites_processed = 0

    try:
        for site_url in site_scope:
            logger.info("Ingesting site: %s", site_url)
            try:
                stats = _ingest_site(client, site_url, conn, dry_run=dry_run, fallback_enabled=fallback_enabled)
                for key in total:
                    total[key] += stats[key]
                sites_processed += 1
                if not dry_run and conn is not None:
                    conn.commit()
            except SharePointAuthError as exc:
                logger.error("Auth error on %s: %s", site_url, exc)
                total["errors"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unexpected error on %s: %s", site_url, exc)
                total["errors"] += 1
    finally:
        client.close()
        if conn is not None:
            conn.close()

    status = (
        "success"
        if total["errors"] == 0
        else ("partial" if sites_processed > 0 else "error")
    )

    # Emit audit event (never in dry_run)
    if not dry_run:
        _emit_audit(sites_processed, total, site_scope)

    return {
        "status": status,
        "sites_processed": sites_processed,
        "total_lists": total["lists"],
        "total_items": total["items"],
        "total_documents": total["documents"],
        "total_errors": total["errors"],
        "dry_run": dry_run,
    }


def _emit_audit(sites_processed: int, total: dict[str, int], site_scope: list[str]) -> None:
    """Write one ``integration_sync_pull`` audit event summarising the ingest run."""
    try:
        from tools.audit.audit_logger import log_event

        log_event(
            "integration_sync_pull",
            actor="sharepoint-ingest",
            action=(
                f"SharePoint ingest: {sites_processed} site(s), "
                f"{total['lists']} lists, {total['items']} items, "
                f"{total['documents']} documents"
            ),
            details={
                "sites_processed": sites_processed,
                "total_lists": total["lists"],
                "total_items": total["items"],
                "total_documents": total["documents"],
                "total_errors": total["errors"],
                "site_scope": site_scope,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audit log write failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description=(
            "Ingest on-prem SharePoint sites into ICDEV sharepoint_* tables. "
            "Config: args/sharepoint.yaml. "
            "Credentials: ICDEV_SHAREPOINT_USER + ICDEV_SHAREPOINT_PASS env vars."
        )
    )
    parser.add_argument(
        "--site",
        metavar="URL",
        help="Ingest a single site URL (overrides site_scope in config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate sites/lists/items but do NOT write to the DB or emit audit events.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print result as JSON to stdout.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=str(CONFIG_PATH),
        help=f"Path to sharepoint.yaml (default: {CONFIG_PATH})",
    )
    args = parser.parse_args()

    cfg = _load_config(Path(args.config))
    result = ingest_all(cfg, site_override=args.site, dry_run=args.dry_run)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"status: {result.get('status', 'unknown')}")
        for key in (
            "sites_processed",
            "total_lists",
            "total_items",
            "total_documents",
            "total_errors",
            "dry_run",
        ):
            if key in result:
                print(f"{key}: {result[key]}")
        if "error" in result:
            print(f"error: {result['error']}", file=sys.stderr)

    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
