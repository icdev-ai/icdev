# Network Canvas: ACAS/Nessus Scan Overlay

**Task ID:** task-edb679d6af
**Tier:** 4 (Enhancement)
**Priority:** Low
**Date:** 2026-03-28
**Classification:** CUI // SP-CTI

---

## Summary

Import Nessus `.nessus` scan files into the Network Canvas, match discovered hosts to topology nodes by IP/hostname, and decorate nodes with vulnerability severity badges. Clicking a device reveals its top findings. The topology becomes a vulnerability heat map.

---

## Files Created / Modified

| File | Change |
|------|--------|
| `tools/network/vuln_overlay.py` | **NEW** — Nessus XML parser, DB persistence, host-node matching, overlay helpers |
| `tools/network/db/init_db.py` | **MODIFIED** — Added 3 tables + 5 indexes to SCHEMA |
| `tools/network/blueprint.py` | **MODIFIED** — 8 new API endpoints under `/network/api/vuln/` |

---

## Database Schema

### `nc_vuln_scans`
Scan-level metadata (name, policy, dates, file name, host count).

### `nc_vuln_hosts`
One row per host per scan: IP, FQDN, NetBIOS, OS, severity counts, matched `node_id`.

### `nc_vuln_findings`
One row per Nessus `ReportItem`: plugin ID/name, severity (0–4), CVEs, CVSS, port, synopsis, description, solution.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/network/api/vuln/upload` | Upload `.nessus` file; parse, save, auto-match |
| `GET` | `/network/api/vuln/scans` | List scans (filter: `?topology_id=`) |
| `GET` | `/network/api/vuln/scans/<id>` | Scan summary with totals |
| `DELETE` | `/network/api/vuln/scans/<id>` | Delete scan + all findings |
| `GET` | `/network/api/vuln/overlay/<id>` | Per-node overlay data (matched only) |
| `GET` | `/network/api/vuln/hosts/<id>` | All hosts with counts |
| `GET` | `/network/api/vuln/findings/<id>/<ip>` | Top N findings for a host |
| `POST` | `/network/api/vuln/rematch/<id>` | Re-match hosts after topology edit |

---

## Nessus Parser (`parse_nessus_file`)

Parses `.nessus` XML (Nessus v2 format):

- **Scan metadata**: `Report/@name`, `Policy/policyName`, `HOST_START`/`HOST_END`
- **Host properties**: `host-ip`, `host-fqdn`, `netbios-name`, `operating-system`
- **Findings per host**: all `ReportItem` elements → plugin ID/name, severity (0–4), CVEs, CVSS base score, port, protocol, synopsis, description, solution, plugin output

Severity mapping:

| Nessus int | Label | Color |
|-----------|-------|-------|
| 0 | info | `#95a5a6` |
| 1 | low | `#3498db` |
| 2 | medium | `#f39c12` |
| 3 | high | `#e67e22` |
| 4 | critical | `#e94560` |

---

## Host-to-Node Matching (`match_hosts_to_nodes`)

Three-step match strategy (in order):

1. **Exact IP**: `nc_vuln_hosts.ip` == node's `data.ip_address`
2. **Subnet containment**: scan IP falls within node's CIDR (handles `192.168.1.0/24` nodes)
3. **Hostname / NetBIOS / FQDN short name**: case-insensitive match against node label, `data.hostname`, or FQDN prefix

On match, `nc_vuln_hosts.node_id` is updated. The `GET /api/vuln/overlay/<id>` endpoint re-triggers matching when `topology_id` is passed (supports post-edit re-match).

---

## Canvas Overlay Integration

The `/api/vuln/overlay/<scan_id>` response provides:

```json
{
  "scan_id": "...",
  "nodes": [
    {
      "node_id": "fw-1",
      "ip": "10.0.0.1",
      "fqdn": "fw1.corp.local",
      "counts": {"critical": 2, "high": 5, "medium": 12, "low": 8, "info": 41},
      "worst_severity": "critical",
      "color": "#e94560"
    }
  ],
  "severity_colors": { "critical": "#e94560", "high": "#e67e22", ... }
}
```

The frontend (`network-canvas.js`) can consume this to:
- Tint node fill to `color`
- Add a badge showing `counts.critical + counts.high`
- On click → call `/api/vuln/findings/<scan_id>/<ip>` to populate a findings panel

---

## Usage Flow

1. **Open a topology** in the Network Canvas
2. **Import scan**: `POST /api/vuln/upload` with `topology_id` + `.nessus` file
3. **Enable overlay**: fetch `/api/vuln/overlay/<scan_id>?topology_id=<id>`
4. **Canvas decorates nodes**: worst-severity color badge with critical/high counts
5. **Click device**: modal shows top 20 findings (severity → CVE → synopsis → solution)
6. **After editing topology**: `POST /api/vuln/rematch/<scan_id>` to re-sync

---

## Security Notes

- `.nessus` XML is parsed with `xml.etree.ElementTree` on trusted uploads (annotated `nosec B314`)
- File upload validated: `.nessus` extension enforced; saved to OS temp dir, deleted after parse
- All endpoints are `@nc_login_required` (ICDEV session auth)
- Audit trail: `VULN_UPLOAD` and `VULN_DELETE` events logged to `nc_audit`
- No raw SQL user input: all query params are bound parameters

---

## Gaps / Future Work (Tier 5+)

- **Frontend badges**: implement `applyVulnOverlay()` in `network-canvas.js`
- **Findings modal**: click handler → right-panel findings list
- **Multi-scan diff**: compare two scan dates to track remediation progress
- **CVSS vector parsing**: parse AV/AC/PR vectors for detailed risk scoring
- **ACAS tenable.sc API**: pull scans directly without manual file upload
- **Risk score rollup**: aggregate topology-level risk score for executive view
