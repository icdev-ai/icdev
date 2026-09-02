# CUI // SP-CTI
"""SNMP discovery adapter (rmf-disc-01).

Wraps ``tools/network/discovery.py::discover_snmp``, which is 946 lines with
ZERO importers — written, reviewed, never once run. This adapter is its first
consumer, and the characterization harness (``harness.py``) is the first
evidence that its OID walking and its sysDescr inference do what they claim.

THIS ADAPTER SHIPS DISABLED. ``args/discovery_adapters.yaml`` declares it
``enabled: false`` on every fabric. SNMP discovery walks live gear: on an
enclave that matters, an unannounced walk against production switches is a
change-control incident before it is a feature. Turning it on is a deliberate
act by somebody who owns the maintenance window, and until then the fabric's
inventory comes from csv/netbox and says so.

It calls through the ``tools.network.discovery`` MODULE OBJECT rather than
binding ``discover_snmp`` at import time, so the harness can substitute the
transport (``_snmp_get`` / ``_snmp_walk``) and exercise the REAL parsing and
inference code against a mock MIB — rather than a stub that would only prove
this file forwards its arguments.
"""

from __future__ import annotations

from tools.assets.discovery_adapters.base import (
    AdapterHealth,
    DiscoveredDevice,
    DiscoveryAdapter,
    normalize_inference,
    probe_dependency,
    utcnow,
)

#: sysDescr — the one OID a reachability probe needs.
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"


class SNMPDiscoveryAdapter(DiscoveryAdapter):
    """Poll declared SNMP targets for identity, interfaces and neighbours."""

    name = "snmp"
    evidence_source = "discovery"
    requires = "pysnmp"

    @property
    def targets(self) -> list[str]:
        raw = self.config.get("targets") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(t).strip() for t in raw if str(t).strip()]

    def _discovery_module(self):
        from tools.network import discovery

        return discovery

    def _dependency_health(self) -> AdapterHealth | None:
        """``unavailable`` when pysnmp is absent — never ``unreachable``.

        Both mean "no devices came back" and they have completely different
        repairs: one is ``pip install``, the other is a firewall or a community
        string. Reporting them as one state sends an operator to the wrong
        ticket, so they stay two states.
        """
        module = self._discovery_module()
        if getattr(module, "_HAS_PYSNMP", False):
            return None
        ok, detail = probe_dependency("pysnmp")
        if ok:
            # Importable, but the module decided otherwise at ITS import time.
            return self._health(
                "unavailable",
                "pysnmp imports now but tools.network.discovery loaded without it",
                dependency=self.requires,
            )
        return self._health(
            "unavailable",
            "pysnmp is not installed on this host (%s)" % detail,
            dependency=self.requires,
        )

    # -- contract ---------------------------------------------------------

    def health(self) -> AdapterHealth:
        dep = self._dependency_health()
        if dep is not None:
            return dep
        if not self.targets:
            return self._health("unconfigured", "no `targets` declared")
        module = self._discovery_module()
        first = self.targets[0]
        try:
            sys_descr = module._snmp_get(
                first,
                _OID_SYS_DESCR,
                str(self.config.get("community", "public")),
                int(self.config.get("port", 161)),
                float(self.config.get("timeout", 2.0)),
            )
        except Exception as exc:  # noqa: BLE001 — health must never raise
            return self._health(
                "unreachable", "%s did not answer: %s" % (first, exc)
            )
        if not sys_descr:
            return self._health(
                "unreachable", "%s returned no sysDescr" % first
            )
        return self._health(
            "healthy",
            "%d target(s) declared; %s answered" % (len(self.targets), first),
            source_version=str(sys_descr)[:120],
        )

    def discover(self) -> list[DiscoveredDevice]:
        module = self._discovery_module()
        community = str(self.config.get("community", "public"))
        port = int(self.config.get("port", 161))
        timeout = float(self.config.get("timeout", 2.0))

        devices: list[DiscoveredDevice] = []
        for target in self.targets:
            record = module.discover_snmp(target, community, port, timeout)
            if not record:
                # A target that does not answer is NOT an asset that does not
                # exist. It is absent from the inventory and its absence is
                # visible as declared-targets vs discovered-count.
                continue
            device_type, vendor, inference = normalize_inference(
                str(record.get("device_type", "")), str(record.get("vendor", ""))
            )
            devices.append(
                self._device(
                    str(record.get("hostname") or target),
                    label=str(record.get("hostname") or target),
                    device_type=device_type,
                    vendor=vendor,
                    ip_address=str(record.get("ip", target)),
                    observed_at=str(record.get("discovered_at") or utcnow()),
                    properties={
                        "inference": inference,
                        "sys_descr": record.get("sys_descr", ""),
                        "sys_object_id": record.get("sys_object_id", ""),
                        "interfaces": record.get("interfaces", []),
                        "neighbors": record.get("neighbors", []),
                        "method": record.get("method", "snmp"),
                    },
                )
            )
        return devices
