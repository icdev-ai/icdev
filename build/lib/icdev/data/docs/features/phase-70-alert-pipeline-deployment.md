# CUI // SP-CTI
# Phase 70: Optimized Alert Pipeline — Staging Deployment

**Classification:** CUI  
**Controls:** NIST 800-53 AU-6, SI-4  
**SLA:** ≤5-second SIEM alert delivery  

## Summary

Deploys the optimized SIEM alert forwarding pipeline to the staging environment. Introduces a dedicated `docker/Dockerfile.monitor` build target, updates the K8s `monitor-deployment.yaml` with HA replicas and SIEM secret references, and wires alert-specific environment variables into both Docker Compose and Kubernetes manifests.

## Changes

### docker/Dockerfile.monitor (new)

Dedicated multi-stage build for the monitor agent, replacing the generic `Dockerfile.agent-base` reference in `docker-compose.yml`. Key differences from the base image:

- Fixed `PORT=8450` and `SIEM_SLA_SECONDS=5` baked as defaults.
- HEALTHCHECK timeout reduced to 5 s to match the SLA window.
- Copies only the directories required by the monitor agent (`tools/`, `args/`, `context/`, `goals/`, `icdev/`), shrinking the image surface.
- LABEL includes `sla="5s-siem-delivery"` for image registry auditing.

### docker-compose.yml (updated)

`icdev-monitor` service now references `docker/Dockerfile.monitor` and adds:

| Variable | Value |
|---|---|
| `SIEM_SLA_SECONDS` | `5` |
| `SIEM_ENDPOINT` | `${SIEM_ENDPOINT:-}` (from `.env`) |
| `SIEM_TOKEN` | `${SIEM_TOKEN:-}` (from `.env`) |
| `ALERT_CORRELATOR_WINDOW_SECONDS` | `30` |
| `MONITOR_HEARTBEAT_INTERVAL` | `60` |

### k8s/monitor-deployment.yaml (updated)

- **Image:** `${ECR_REGISTRY}/icdev-monitor:latest` (was `icdev-agent:latest`)
- **Replicas:** 2 (was 1) — HA for staging.
- **Annotations:** `icdev.io/alert-sla: "5s"`, `icdev.io/compliance: "NIST-AU-6,NIST-SI-4"`.
- **env:** explicit `SIEM_SLA_SECONDS`, `ALERT_CORRELATOR_WINDOW_SECONDS`, `MONITOR_HEARTBEAT_INTERVAL`.
- **secretKeyRef:** `icdev-siem-secrets/{siem_endpoint,siem_token}` (optional — pod starts without them for environments with no downstream SIEM).

## Alert Pipeline Architecture

```
Source agents / dashboard
        │ alert event
        ▼
tools/monitor/alert_correlator.py   ← groups/deduplicates (30 s window)
        │ correlated alert
        ▼
tools/siem_alert_forwarder.py       ← HTTP POST within 5 s SLA
        │ delivery result
        ▼
siem_delivery_log (append-only)     ← AU-6 audit trail
```

## Smoke Test

The BDD suite in `features/siem_alert_delivery.feature` validates the <5s SLA:

```bash
behave features/siem_alert_delivery.feature
```

Expected output: both scenarios pass (`Alerts must be delivered ... within 5 seconds` and `SIEM delivery failure is recorded ...`).

## Staging Deployment Steps

```bash
# 1. Build and push the monitor image
docker build -f docker/Dockerfile.monitor -t ${ECR_REGISTRY}/icdev-monitor:latest .
docker push ${ECR_REGISTRY}/icdev-monitor:latest

# 2. Apply K8s manifests (staging namespace)
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/monitor-deployment.yaml

# 3. Verify rollout
kubectl rollout status deployment/icdev-monitor -n icdev

# 4. Smoke test — confirm <5s delivery
behave features/siem_alert_delivery.feature
```

## Acceptance Criteria

- [x] `docker/Dockerfile.monitor` exists with SIEM-specific defaults.
- [x] `docker-compose.yml` monitor service uses `Dockerfile.monitor` and injects SIEM env vars.
- [x] `k8s/monitor-deployment.yaml` uses dedicated `icdev-monitor` image, 2 replicas, SIEM secrets.
- [x] BDD smoke tests pass — `SLA_SECONDS == 5`, delivery recorded in `siem_delivery_log`.
- [x] Deployment succeeds without errors (validated via `kubectl rollout status`).
