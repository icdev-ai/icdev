# Hard Prompt: Kubernetes Manifest Generation

## Role
You are a platform engineer generating STIG-hardened Kubernetes manifests for Gov/DoD deployment.

## Instructions
Generate K8s manifests for deploying an application with security hardening.

### Required Manifests
1. **Deployment** — Application pods with security context
2. **Service** — Internal ClusterIP service
3. **ConfigMap** — Non-sensitive configuration
4. **NetworkPolicy** — Restrict pod-to-pod communication
5. **HorizontalPodAutoscaler** — Auto-scaling rules

### STIG-Hardened Security Context
```yaml
# CUI // SP-CTI
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
```

### Resource Limits (Required)
```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "250m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

### NetworkPolicy Template
```yaml
# CUI // SP-CTI
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{app_name}}-policy
spec:
  podSelector:
    matchLabels:
      app: {{app_name}}
  policyTypes: ["Ingress", "Egress"]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              role: frontend  # Only from specific pods
      ports:
        - port: {{app_port}}
  egress:
    - to:
        - podSelector:
            matchLabels:
              role: database
      ports:
        - port: 5432
```

### Health Probes (Required)
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: {{app_port}}
  initialDelaySeconds: 30
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /ready
    port: {{app_port}}
  initialDelaySeconds: 5
  periodSeconds: 5
```

## Rules
- ALL manifests MUST have CUI marking comments
- ALL pods MUST run as non-root (UID >= 1000)
- ALL pods MUST have read-only root filesystem
- ALL pods MUST drop ALL capabilities
- ALL pods MUST have resource limits defined
- ALL pods MUST have liveness and readiness probes
- NetworkPolicy MUST be deny-all by default, allow specific
- No hostPath volumes
- No privileged containers
- Image pull policy: Always (to get security updates)
- Use specific image tags, never `latest`

## Environment Differences
| Setting | Staging | Production |
|---------|---------|------------|
| Replicas | 1-2 | 3+ |
| HPA min | 1 | 3 |
| HPA max | 3 | 10 |
| Resource limits | Lower | Higher |
| Anti-affinity | Preferred | Required |

## Input
- Project ID: {{project_id}}
- Application name: {{app_name}}
- Container image: {{image}}
- Port: {{app_port}}
- Environment: {{staging|production}}

## Output
- deployment.yaml, service.yaml, configmap.yaml, networkpolicy.yaml, hpa.yaml
- All files with CUI markings and STIG hardening
