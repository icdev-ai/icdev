# CUI // SP-CTI
"""GNS3 discovery adapter — a virtual lab as a source (rmf-disc-01).

A GNS3 lab is REAL inventory of a real thing: the lab. It is not, and must
never be reported as, inventory of the production estate. Every device this
adapter produces carries ``properties.evidence_kind = "lab"``, and the fabric
it is declared on in ``args/discovery_adapters.yaml`` is a lab fabric — so a
roll-up that mixes a lab into a production fabric's asset count is a
mis-declaration a reader can see, not a silent laundering inside the code.

This is the same distinction ``args/docmod/inventory_feeds.yaml`` already draws
between ``inventory`` and ``design`` evidence: an observed estate beats a
drawing of one, and no quantity of lab nodes adds up to a deployed switch.

DELEGATES to the existing ``tools/network/adapters/gns3_adapter.py::GNS3Adapter``
(which already has ``health()``); the only thing added there was the read
counterpart of ``add_node``.
"""

from __future__ import annotations

from typing import Any

from tools.assets.discovery_adapters.base import (
    AdapterHealth,
    DiscoveredDevice,
    DiscoveryAdapter,
)


class GNS3DiscoveryAdapter(DiscoveryAdapter):
    """Enumerate the nodes of one or every project on a GNS3 server."""

    name = "gns3"
    evidence_source = "topology_ingest"

    def _server(self):
        from tools.network.adapters.gns3_adapter import GNS3Adapter

        return GNS3Adapter(
            url=str(self.config.get("url", "") or ""),
            username=str(self.config.get("username", "") or ""),
            password=str(self.config.get("password", "") or ""),
            token=str(self.config.get("token", "") or ""),
            timeout=float(self.config.get("timeout", 10.0) or 10.0),
        )

    # -- contract ---------------------------------------------------------

    def health(self) -> AdapterHealth:
        missing = self._missing_config("url")
        if missing:
            return self._health(
                "unconfigured", "missing required config: %s" % ", ".join(missing)
            )
        try:
            result = self._server().health()
        except Exception as exc:  # noqa: BLE001 — health must never raise
            return self._health("unreachable", "%s: %s" % (type(exc).__name__, exc))
        if result.get("status") != "ok":
            return self._health(
                "unreachable",
                str(result.get("error") or result.get("status") or "no answer"),
            )
        return self._health(
            "healthy",
            "GNS3 server at %s" % self.config.get("url", ""),
            source_version=str(result.get("version", "") or ""),
        )

    def discover(self) -> list[DiscoveredDevice]:
        server = self._server()
        declared = self.config.get("project_ids") or []
        if isinstance(declared, str):
            declared = [declared]
        project_ids = [str(p).strip() for p in declared if str(p).strip()]

        projects: list[dict[str, Any]] = []
        if project_ids:
            projects = [{"project_id": pid, "name": pid} for pid in project_ids]
        else:
            for proj in server.list_projects():
                if isinstance(proj, dict) and proj.get("project_id"):
                    projects.append(proj)

        devices: list[DiscoveredDevice] = []
        for proj in projects:
            project_id = str(proj.get("project_id"))
            project_name = str(proj.get("name") or project_id)
            for node in server.list_nodes(project_id):
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("node_id") or "")
                if not node_id:
                    continue
                node_type = str(node.get("node_type") or "")
                props = node.get("properties") or {}
                devices.append(
                    self._device(
                        node_id,
                        label=str(node.get("name") or node_id),
                        device_type=node_type,
                        model=(
                            str(props.get("platform") or "")
                            if isinstance(props, dict)
                            else ""
                        ),
                        properties={
                            # Never merged with production inventory by accident.
                            "evidence_kind": "lab",
                            "gns3_project_id": project_id,
                            "gns3_project_name": project_name,
                            "gns3_node_type": node_type,
                            "gns3_status": str(node.get("status") or ""),
                            "gns3_compute_id": str(node.get("compute_id") or ""),
                        },
                    )
                )
        return devices
