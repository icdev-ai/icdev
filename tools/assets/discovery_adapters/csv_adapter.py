# CUI // SP-CTI
"""CSV discovery adapter — an exported inventory file as a source (rmf-disc-01).

The least glamorous adapter and the one most deployments actually have. An
air-gapped enclave that will never expose a NetBox API will hand over a
spreadsheet, and that spreadsheet is real observed inventory.

NOTHING IN THIS FILE NAMES A COLUMN. The header -> canonical-field mapping is
declared per instance in ``args/discovery_adapters.yaml`` (``columns:``), in the
same shape ``args/docmod/inventory_feeds.yaml`` already uses: canonical field ->
list of candidate headers, first non-empty wins. A second CSV with different
headers is a config entry, not a code change.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from tools.assets.discovery_adapters.base import (
    AdapterHealth,
    DiscoveredDevice,
    DiscoveryAdapter,
)

#: Canonical fields a CSV row may supply. ``node_id`` is the only required one.
CANONICAL_FIELDS: tuple[str, ...] = (
    "node_id",
    "label",
    "device_type",
    "vendor",
    "model",
    "firmware_version",
    "serial",
    "site",
    "rack",
    "ip_address",
)


class CSVDiscoveryAdapter(DiscoveryAdapter):
    """Read a CSV export into canonical device records."""

    name = "csv"
    evidence_source = "csv"

    # -- config -----------------------------------------------------------

    @property
    def path(self) -> Path | None:
        raw = str(self.config.get("path", "") or "").strip()
        return Path(raw) if raw else None

    @property
    def columns(self) -> dict[str, list[str]]:
        """canonical field -> candidate headers. Falls back to identity."""
        declared = self.config.get("columns") or {}
        out: dict[str, list[str]] = {}
        for field_name in CANONICAL_FIELDS:
            cands = declared.get(field_name)
            if isinstance(cands, str):
                cands = [cands]
            out[field_name] = [str(c) for c in (cands or [])] or [field_name]
        return out

    # -- helpers ----------------------------------------------------------

    def _read_header(self) -> tuple[list[str] | None, str]:
        path = self.path
        if path is None:
            return None, "no `path` declared"
        if not path.exists():
            return None, "no such file: %s" % path
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.reader(fh)
                for row in reader:
                    return [c.strip() for c in row], ""
            return [], "file is empty"
        except OSError as exc:
            return None, "unreadable: %s" % exc

    @staticmethod
    def _pick(row: dict[str, str], candidates: list[str]) -> str:
        for cand in candidates:
            val = row.get(cand)
            if val is None:
                # Header matching is case-insensitive as a convenience; the
                # DECLARED spelling is still what a reader of the config sees.
                for key, raw in row.items():
                    if key and key.strip().lower() == cand.strip().lower():
                        val = raw
                        break
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    # -- contract ---------------------------------------------------------

    def health(self) -> AdapterHealth:
        if self.path is None:
            return self._health("unconfigured", "no `path` declared for this instance")
        header, err = self._read_header()
        if header is None:
            return self._health("unreachable", err)
        if not header:
            return self._health("unreachable", "file is empty — no header row")
        lowered = {h.strip().lower() for h in header}
        wanted = self.columns["node_id"]
        if not any(c.strip().lower() in lowered for c in wanted):
            return self._health(
                "degraded",
                "header has no node_id column — looked for %s, header is %s"
                % (", ".join(wanted), ", ".join(header)),
            )
        return self._health("healthy", "%s, %d columns" % (self.path, len(header)))

    def discover(self) -> list[DiscoveredDevice]:
        path = self.path
        if path is None or not path.exists():
            return []
        columns = self.columns
        devices: list[DiscoveredDevice] = []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for raw_row in csv.DictReader(fh):
                row = {k: (v or "") for k, v in raw_row.items() if k is not None}
                node_id = self._pick(row, columns["node_id"])
                if not node_id:
                    # A row with no identity cannot become an asset. Skipped
                    # rows are counted by the caller via header vs row count;
                    # inventing an id here would manufacture an asset.
                    continue
                mapped: dict[str, Any] = {}
                for field_name in CANONICAL_FIELDS:
                    if field_name == "node_id":
                        continue
                    mapped[field_name] = self._pick(row, columns[field_name])
                claimed = {
                    c.strip().lower()
                    for cands in columns.values()
                    for c in cands
                }
                extras = {
                    k: v
                    for k, v in row.items()
                    if k and k.strip().lower() not in claimed and str(v).strip()
                }
                devices.append(
                    self._device(
                        node_id,
                        properties={"csv_extra": extras, "csv_path": str(path)},
                        **mapped,
                    )
                )
        return devices
