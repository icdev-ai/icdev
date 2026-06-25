# Advisory NQL Templates

Deterministic NQL templates for advisory impact assessment.
Used by `run_impact_assessment` to generate Q2 — the template-based (non-AI) query
for cross-checking AI-generated Q1 results.

Template variables (substituted at runtime):
- `{vendor}` — hardware vendor string (e.g. `"cisco"`, `"juniper"`, `"arista"`)
- `{affected_models_list}` — NQL list literal (e.g. `["ASR9001", "ASR9006"]`)
- `{affected_versions_list}` — NQL list literal (e.g. `["7.3.1", "7.4.1"]`)

If `affected_models_list` is empty, the model predicate is omitted.
If `affected_versions_list` is empty, the version predicate is omitted.
If `vendor` is empty or `"other"`, the vendor predicate is omitted.

---

## Generic (Any Vendor) — Models + Versions

```nql
foreach d in network.devices
where d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.vendor, d.role }
```

## Generic — Models Only (no specific version range)

```nql
foreach d in network.devices
where d.hardware.platform in {affected_models_list}
select { d.hostname, d.hardware.platform, d.os.version, d.vendor, d.role }
```

## Generic — Versions Only (all platforms on affected OS version)

```nql
foreach d in network.devices
where d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.vendor, d.role }
```

## Cisco — IOS-XE / IOS-XR / NX-OS

```nql
foreach d in network.devices
where d.vendor == "cisco"
and d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }
```

## Juniper — JunOS

```nql
foreach d in network.devices
where d.vendor == "juniper"
and d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }
```

## Arista — EOS

```nql
foreach d in network.devices
where d.vendor == "arista"
and d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }
```

## Palo Alto Networks — PAN-OS

```nql
foreach d in network.devices
where d.vendor == "palo_alto"
and d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }
```

## Fortinet — FortiOS

```nql
foreach d in network.devices
where d.vendor == "fortinet"
and d.hardware.platform in {affected_models_list}
and d.os.version in {affected_versions_list}
select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }
```

---

## Total Devices (used as denominator for exposure ratio)

```nql
foreach d in network.devices
select count(d)
```

---

## Template Selection Logic (implemented in advisory.py)

1. If `vendor` matches a named section above → use that vendor template.
2. If `affected_models_list` is non-empty and `affected_versions_list` is non-empty → Generic Models+Versions.
3. If only `affected_models_list` → Generic Models Only.
4. If only `affected_versions_list` → Generic Versions Only.
5. Fallback → Total Devices query (returns all devices; impacted_count = total_devices).
