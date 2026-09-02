# CUI // SP-CTI
"""NetBox discovery adapter (rmf-disc-01).

DELEGATES to the existing ``NetBoxClient`` — this file contains no HTTP, no
auth and no pagination. ``tools/network/nms_adapter.py`` and
``tools/network/adapters/netbox_adapter.py`` are untouched and keep answering
the NMS-pull contract; what was missing was never a NetBox client, it was
anything that PERSISTED what one returns.

WHY IT DOES NOT USE ``NetBoxClient.get_devices()``

That mapper is the canvas-node mapper: it returns ``label``/``type``/``ip``/
``site``/``rack``/``serial`` and DROPS ``device_type.manufacturer`` and
``device_type.model``. Vendor and model are exactly the two fields the de-facto
standard learner reads (``args/docmod/inventory_feeds.yaml`` maps
``vendor -> [vendor]``, ``product -> [model]``), so a feed built on that mapper
would populate ni_devices with rows the learner can learn nothing from. This
adapter reads the raw device objects via ``get_devices_raw`` and maps the fields
it needs, reusing the client's role -> type table so the two mappings cannot
disagree about what a "leaf" is.
"""

from __future__ import annotations

from typing import Any

from tools.assets.discovery_adapters.base import (
    AdapterHealth,
    DiscoveredDevice,
    DiscoveryAdapter,
)


def _nested(obj: Any, *keys: str) -> str:
    """Descend a chain of dict keys, returning "" at the first miss."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return "" if cur is None else str(cur)


class NetBoxDiscoveryAdapter(DiscoveryAdapter):
    """NetBox DCIM as a source of asset truth."""

    name = "netbox"
    evidence_source = "netbox"

    def _client(self):
        from tools.network.netbox_client import NetBoxClient

        return NetBoxClient(
            url=str(self.config.get("url", "") or ""),
            token=str(self.config.get("token", "") or ""),
            timeout=int(self.config.get("timeout", 15) or 15),
            verify_ssl=bool(self.config.get("verify_ssl", True)),
        )

    # -- contract ---------------------------------------------------------

    def health(self) -> AdapterHealth:
        missing = self._missing_config("url", "token")
        if missing:
            return self._health(
                "unconfigured", "missing required config: %s" % ", ".join(missing)
            )
        try:
            result = self._client().test_connection()
        except Exception as exc:  # noqa: BLE001 — health must never raise
            return self._health(
                "unreachable", "%s: %s" % (type(exc).__name__, exc)
            )
        if not result.get("ok"):
            return self._health("unreachable", str(result))
        return self._health(
            "healthy",
            "NetBox at %s" % result.get("url", self.config.get("url", "")),
            source_version=str(result.get("netbox_version", "") or ""),
        )

    def discover(self) -> list[DiscoveredDevice]:
        from tools.network.netbox_client import _ROLE_TO_TYPE

        site = str(self.config.get("site", "") or "") or None
        raw_devices = self._client().get_devices_raw(site=site)

        devices: list[DiscoveredDevice] = []
        for dev in raw_devices:
            netbox_id = dev.get("id")
            if netbox_id is None:
                continue
            role = dev.get("role") or dev.get("device_role") or {}
            role_slug = ""
            if isinstance(role, dict):
                role_slug = str(role.get("slug", "") or role.get("name", "")).lower()
            primary_ip = _nested(dev, "primary_ip", "address").split("/")[0]
            devices.append(
                self._device(
                    str(netbox_id),
                    label=str(dev.get("name") or "device-%s" % netbox_id),
                    device_type=_ROLE_TO_TYPE.get(role_slug, "") or role_slug,
                    vendor=_nested(dev, "device_type", "manufacturer", "name"),
                    model=(
                        _nested(dev, "device_type", "model")
                        or _nested(dev, "device_type", "slug")
                    ),
                    firmware_version=_nested(dev, "custom_fields", "firmware_version"),
                    serial=str(dev.get("serial") or ""),
                    site=_nested(dev, "site", "name"),
                    rack=_nested(dev, "rack", "name"),
                    ip_address=primary_ip,
                    properties={
                        "netbox_url": str(dev.get("url") or ""),
                        "netbox_status": _nested(dev, "status", "value"),
                        "netbox_role": role_slug,
                        "platform": _nested(dev, "platform", "name"),
                        "tags": [
                            str(t.get("name", ""))
                            for t in (dev.get("tags") or [])
                            if isinstance(t, dict)
                        ],
                    },
                )
            )
        return devices
