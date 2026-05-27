# CUI // SP-CTI
"""NDC Lab Backend Health Monitor.

Probes every registered lab backend (GNS3, Containerlab, EVE-NG, ...) listed
in `args/network_lab_backends.yaml` and returns a normalized health payload
used by the `/network/labs` dashboard and the `/api/ndc/labs/health` API.

Probe cadence is 30s (polled by the dashboard). Each probe is wrapped in a
short timeout and never raises — a failed probe becomes `status='red'` with
`last_error` populated.

Cross-platform: stdlib only (yaml is already a project dep per requirements.txt).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

import yaml

logger = get_logger("icdev.network.lab_health")

_BACKENDS_YAML = Path(__file__).resolve().parents[2] / "args" / "network_lab_backends.yaml"

_DEFAULT_TIMEOUT = 3.0  # seconds — keep probes cheap


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_backends(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load registered backends from YAML. Returns [] if file missing."""
    target = Path(path) if path else _BACKENDS_YAML
    if not target.exists():
        logger.warning("Backends YAML not found: %s", target)
        return []
    try:
        with target.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.error("Failed to load backends YAML: %s", exc)
        return []
    return list(data.get("backends", []) or [])


def _probe_tcp(host: str, port: int, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _probe_http(url: str, timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Simple HTTP HEAD/GET probe. Returns {reachable, status_code, error}."""
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — internal mgmt URL
            return {"reachable": True, "status_code": resp.status, "error": None}
    except URLError as exc:
        return {"reachable": False, "status_code": None, "error": str(exc.reason)}
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return {"reachable": False, "status_code": None, "error": str(exc)}


def _classify(reachable: bool, queue_depth: int, free_ram_mb: int) -> str:
    if not reachable:
        return "red"
    if queue_depth > 5 or free_ram_mb < 512:
        return "yellow"
    return "green"


def probe_backend(backend: Dict[str, Any]) -> Dict[str, Any]:
    """Probe a single backend via its adapter.

    Returns a normalized dict:
      {name, type, url, status: 'green'|'yellow'|'red',
       free_ram_mb, running_labs, queue_depth, last_error, last_probed}

    The adapter layer (tools/network/adapters/) is called opportunistically —
    if an adapter is missing or raises, we fall back to a plain TCP/HTTP probe
    so the dashboard never goes blank.
    """
    name = backend.get("name", "<unnamed>")
    btype = backend.get("type", "unknown")
    url = backend.get("url", "")
    enabled = bool(backend.get("enabled", False))

    result: Dict[str, Any] = {
        "name": name,
        "type": btype,
        "url": url,
        "enabled": enabled,
        "status": "red",
        "free_ram_mb": 0,
        "running_labs": 0,
        "queue_depth": 0,
        "last_error": None,
        "last_probed": _utcnow(),
    }

    if not enabled:
        result["status"] = "red"
        result["last_error"] = "backend disabled in config"
        return result

    # Try an adapter first (adapter names mirror backend types).
    adapter_ok = False
    try:
        adapter_mod = __import__(
            f"tools.network.adapters.{btype}_adapter",
            fromlist=["probe"],
        )
        if hasattr(adapter_mod, "probe"):
            probed = adapter_mod.probe(backend) or {}
            # Merge known metrics.
            for key in ("free_ram_mb", "running_labs", "queue_depth"):
                if key in probed:
                    result[key] = int(probed[key])
            reachable = bool(probed.get("reachable", False))
            result["last_error"] = probed.get("error")
            result["status"] = _classify(
                reachable, result["queue_depth"], result["free_ram_mb"] or 4096
            )
            adapter_ok = True
    except ImportError:
        logger.debug("No adapter for backend type '%s' — using fallback probe", btype)
    except Exception as exc:  # noqa: BLE001 — must not escape
        logger.warning("Adapter probe for %s failed: %s", name, exc)

    if adapter_ok:
        return result

    # Fallback: HTTP probe for http(s) URLs, else TCP.
    start = time.monotonic()
    if url.startswith(("http://", "https://")):
        http_result = _probe_http(url)
        reachable = http_result["reachable"]
        result["last_error"] = http_result["error"]
    elif url:
        parsed = urlparse("//" + url if "://" not in url else url)
        host = parsed.hostname or url
        port = parsed.port or 22
        reachable = _probe_tcp(host, port)
        if not reachable:
            result["last_error"] = f"TCP connect failed to {host}:{port}"
    else:
        reachable = False
        result["last_error"] = "no URL configured"

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.debug("Probe %s took %dms reachable=%s", name, elapsed_ms, reachable)

    # Fallback metrics — unknown, assume healthy defaults if reachable.
    if reachable:
        result["free_ram_mb"] = result["free_ram_mb"] or 4096
    result["status"] = _classify(
        reachable, result["queue_depth"], result["free_ram_mb"] or 0
    )
    return result


def probe_all(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Probe every enabled backend. Disabled backends are included with status=red."""
    backends = load_backends(path)
    return [probe_backend(b) for b in backends]


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="NDC lab backend health probe")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--name", help="probe a single backend by name")
    args = ap.parse_args()

    all_backends = load_backends()
    if args.name:
        match = next((b for b in all_backends if b.get("name") == args.name), None)
        output = probe_backend(match) if match else {"error": f"unknown backend '{args.name}'"}
    else:
        output = probe_all()

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        if isinstance(output, list):
            for b in output:
                print(
                    f"{b['name']:20s} {b['type']:14s} {b['status']:7s} "
                    f"ram={b['free_ram_mb']:>5d}MB labs={b['running_labs']:>2d} "
                    f"q={b['queue_depth']:>2d} err={b['last_error'] or '-'}"
                )
        else:
            print(output)
