# CUI // SP-CTI
"""SSH (CDP/LLDP) discovery adapter (rmf-disc-01).

Wraps ``tools/network/discovery.py::discover_ssh``, the other half of the
946-line module nothing imported. Like the SNMP adapter it calls through the
MODULE OBJECT so the harness can substitute Netmiko's ``ConnectHandler`` with a
mock device and exercise the REAL ``show cdp neighbors detail`` /
``show lldp neighbors detail`` parsers.

SHIPS DISABLED, for the same reason as SNMP and one more: this adapter logs
into gear with an account that can run enable-level commands. That is not
something to turn on by inheriting a default.

``health()`` DOES NOT LOG IN. ``discover_ssh`` is the only login this adapter
makes; a health probe that opened a second session would double the auth
attempts against every device on every sweep, and on a network with account
lockout that is how discovery takes the estate offline. Health therefore
reports what it can establish WITHOUT touching the gear — dependency present,
credentials declared, targets declared — and says so in ``detail``. That is
``degraded``, never ``healthy``: nothing has answered.
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


class SSHDiscoveryAdapter(DiscoveryAdapter):
    """Log into declared devices and read their neighbour tables."""

    name = "ssh"
    # "discovery" and not "ssh-discovery": the vocabulary is rmf-disc-02's and
    # is what defacto_learner's `exclude_when` is keyed on. A host that
    # answered an SSH login is the same EVIDENCE CLASS as one that answered
    # SNMP; which adapter asked is in properties_json, not in the label.
    evidence_source = "discovery"
    requires = "netmiko"

    @property
    def targets(self) -> list[str]:
        raw = self.config.get("targets") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(t).strip() for t in raw if str(t).strip()]

    def _discovery_module(self):
        from tools.network import discovery

        return discovery

    # -- contract ---------------------------------------------------------

    def health(self) -> AdapterHealth:
        module = self._discovery_module()
        if not getattr(module, "_HAS_NETMIKO", False):
            ok, detail = probe_dependency("netmiko")
            return self._health(
                "unavailable",
                (
                    "netmiko imports now but tools.network.discovery loaded without it"
                    if ok
                    else "netmiko is not installed on this host (%s)" % detail
                ),
                dependency=self.requires,
            )
        missing = self._missing_config("username")
        if not self.targets:
            missing.append("targets")
        if missing:
            return self._health(
                "unconfigured", "missing required config: %s" % ", ".join(missing)
            )
        # Deliberately NOT healthy: nothing has answered. `degraded` is the
        # honest state for "everything we can check without logging in is in
        # place", and it still discovers.
        return self._health(
            "degraded",
            "netmiko present, %d target(s) and credentials declared — NOT probed "
            "(a health login would double auth attempts against every device)"
            % len(self.targets),
        )

    def discover(self) -> list[DiscoveredDevice]:
        module = self._discovery_module()
        username = str(self.config.get("username", "") or "")
        password = str(self.config.get("password", "") or "")
        # The NETMIKO driver name, not a discovered attribute. Named apart
        # from the per-device `device_type` below, which is inferred and
        # would otherwise rebind this loop-invariant on the first device.
        netmiko_driver = str(self.config.get("device_type", "cisco_ios") or "cisco_ios")
        enable_secret = str(self.config.get("enable_secret", "") or "")
        port = int(self.config.get("port", 22) or 22)

        devices: list[DiscoveredDevice] = []
        for target in self.targets:
            record = module.discover_ssh(
                target, username, password, netmiko_driver, enable_secret, port
            )
            if not record:
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
                        "interfaces": record.get("interfaces", []),
                        "neighbors": record.get("neighbors", []),
                        "method": record.get("method", "ssh"),
                        "netmiko_device_type": netmiko_driver,
                    },
                )
            )
        return devices
