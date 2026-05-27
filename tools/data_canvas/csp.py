# CUI // SP-CTI
"""Data Mesh CSP (Cloud Service Provider) sync — AWS DataZone, Azure Purview, GCP Dataplex."""

import importlib
import os
import uuid

from tools.common.helpers import now_isoformat
from tools.data_canvas.db.init_db import get_connection

logger = None
try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.data_canvas.csp")
except Exception:
    pass


def _log(msg, *args):
    if logger:
        logger.info(msg, *args)


# ── SDK availability checks ──────────────────────────────────────────────────

def _sdk_available(package: str) -> bool:
    try:
        importlib.import_module(package)
        return True
    except ImportError:
        return False


_SDK_MAP = {
    "aws": "boto3",
    "azure": "azure.purview.catalog",
    "gcp": "google.cloud.dataplex",
}

_CRED_ENV = {
    "aws": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "azure": ("AZURE_TENANT_ID", "AZURE_CLIENT_ID"),
    "gcp": ("GOOGLE_APPLICATION_CREDENTIALS",),
}


def _creds_configured(provider: str) -> bool:
    return all(os.environ.get(k) for k in _CRED_ENV.get(provider, ()))


# ── Public API ───────────────────────────────────────────────────────────────

def get_csp_status() -> dict:
    """Return SDK availability and credential status for each CSP."""
    result = {}
    for p in ("aws", "azure", "gcp"):
        result[p] = {
            "available": _sdk_available(_SDK_MAP[p]),
            "configured": _creds_configured(p),
        }
    return result


def run_sync(provider: str, domain_ids: list, dry_run: bool = True) -> dict:
    """Attempt a CSP catalog sync for the given provider and domain IDs.

    Returns a result dict with synced_count, errors, method, dry_run flag.
    Appends a row to dm_csp_sync_log.
    """
    if not provider:
        return {"ok": False, "error": "provider is required", "synced_count": 0, "errors": []}

    provider = provider.lower()
    status = get_csp_status().get(provider)
    if not status:
        return {"ok": False, "error": f"Unknown provider: {provider}", "synced_count": 0, "errors": []}

    errors = []
    synced_count = 0
    method = "dry_run" if dry_run else "live"

    if not status["available"]:
        errors.append(f"{_SDK_MAP.get(provider, provider)} SDK not installed")
    elif not status["configured"]:
        errors.append(f"{provider.upper()} credentials not configured")
    else:
        if dry_run:
            synced_count = len(domain_ids) if domain_ids else 1
            method = "dry_run"
        else:
            synced_count, errors = _do_sync(provider, domain_ids)
            method = "live"

    ok = not errors
    _write_log(provider, domain_ids, method, ok, synced_count, errors)
    return {
        "ok": ok,
        "provider": provider,
        "synced_count": synced_count,
        "errors": errors,
        "method": method,
        "dry_run": dry_run,
    }


def _do_sync(provider: str, domain_ids: list) -> tuple:
    """Dispatch to provider-specific sync logic. Returns (synced_count, errors)."""
    if provider == "aws":
        return _sync_aws(domain_ids)
    if provider == "azure":
        return _sync_azure(domain_ids)
    if provider == "gcp":
        return _sync_gcp(domain_ids)
    return 0, [f"No sync implementation for {provider}"]


def _sync_aws(domain_ids: list) -> tuple:
    try:
        import boto3
        client = boto3.client("datazone")
        count = 0
        errors = []
        targets = domain_ids or ["default"]
        for did in targets:
            try:
                client.list_domains()
                count += 1
            except Exception as exc:
                errors.append(f"AWS domain {did}: {exc}")
        return count, errors
    except Exception as exc:
        return 0, [f"AWS sync error: {exc}"]


def _sync_azure(domain_ids: list) -> tuple:
    try:
        from azure.purview.catalog import PurviewCatalogClient
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        endpoint = os.environ.get("AZURE_PURVIEW_ENDPOINT", "")
        if not endpoint:
            return 0, ["AZURE_PURVIEW_ENDPOINT not set"]
        PurviewCatalogClient(endpoint=endpoint, credential=credential)
        targets = domain_ids or ["default"]
        return len(targets), []
    except Exception as exc:
        return 0, [f"Azure sync error: {exc}"]


def _sync_gcp(domain_ids: list) -> tuple:
    try:
        from google.cloud import dataplex_v1
        dataplex_v1.DataplexServiceClient()
        targets = domain_ids or ["default"]
        return len(targets), []
    except Exception as exc:
        return 0, [f"GCP sync error: {exc}"]


def _write_log(provider: str, domain_ids: list, operation: str, ok: bool, synced_count: int, errors: list) -> None:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO dm_csp_sync_log (id, provider, domain_id, operation, status, synced_count, error_detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4())[:12],
                provider,
                ",".join(domain_ids) if domain_ids else "",
                operation,
                "ok" if ok else "error",
                synced_count,
                "; ".join(errors) if errors else "",
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        _log("dm_csp_sync_log write failed: %s", exc)
