# SDK & API Reference

ICDEV exposes its full capability through three programmatic interfaces: REST API, MCP Streamable HTTP, and direct Python CLI. Use these when you need automation beyond what the conversational (Claude Code) or invisible (pipeline) tiers provide.

---

## REST API

The SaaS API gateway runs at port 8443 and serves all endpoints under `/api/v1/`.

### Authentication

Three authentication methods are supported:

```bash
# Method 1: API Key (simplest)
curl -H "Authorization: Bearer icdev_abc123..." https://icdev.example.com/api/v1/projects

# Method 2: OAuth 2.0 / OIDC
curl -H "Authorization: Bearer eyJhbGciOi..." https://icdev.example.com/api/v1/projects

# Method 3: CAC/PIV (via client certificate)
curl --cert client.pem https://icdev.example.com/api/v1/projects
```

### Core Endpoints

#### Projects

```bash
# List projects
GET /api/v1/projects

# Create project
POST /api/v1/projects
{
  "name": "my-app",
  "type": "microservice",
  "impact_level": "IL4",
  "language": "python"
}

# Get project status
GET /api/v1/projects/{project_id}

# Get project status with compliance summary
GET /api/v1/projects/{project_id}/status
```

#### Compliance

```bash
# Generate SSP
POST /api/v1/compliance/ssp
{"project_id": "proj-123"}

# Generate POAM
POST /api/v1/compliance/poam
{"project_id": "proj-123"}

# STIG check
POST /api/v1/compliance/stig
{"project_id": "proj-123"}

# Generate SBOM
POST /api/v1/compliance/sbom
{"project_id": "proj-123", "project_dir": "/path/to/project"}

# Multi-framework assessment
GET /api/v1/compliance/assess/{project_id}

# Crosswalk query (implement once, satisfy many)
GET /api/v1/compliance/crosswalk?control=AC-2
```

#### Security

```bash
# Full security scan
POST /api/v1/security/scan
{"project_dir": "/path/to/project"}

# SAST only
POST /api/v1/security/sast
{"project_dir": "/path/to/project"}

# Dependency audit
POST /api/v1/security/dependencies
{"project_dir": "/path/to/project"}

# Secret detection
POST /api/v1/security/secrets
{"project_dir": "/path/to/project"}
```

#### Builder

```bash
# Scaffold project
POST /api/v1/builder/scaffold
{
  "type": "python-backend",
  "name": "my-service",
  "project_path": "/tmp"
}

# Generate code from test (TDD GREEN)
POST /api/v1/builder/generate
{
  "test_file": "/path/to/test.py",
  "project_dir": "/path/to/project",
  "language": "python"
}

# Run tests
POST /api/v1/builder/test
{"project_dir": "/path/to/project"}

# Lint
POST /api/v1/builder/lint
{"project_dir": "/path/to/project"}
```

#### Dev Profiles

```bash
# Create profile from template
POST /api/v1/dev-profiles/create
{
  "scope": "tenant",
  "scope_id": "tenant-abc",
  "template": "dod_baseline",
  "created_by": "admin@mil"
}

# Get current profile
GET /api/v1/dev-profiles/{scope}/{scope_id}

# Resolve 5-layer cascade
GET /api/v1/dev-profiles/resolve/{scope}/{scope_id}

# Auto-detect from repo
POST /api/v1/dev-profiles/detect
{
  "repo_path": "/path/to/repo",
  "tenant_id": "tenant-abc"
}
```

#### Requirements Intake

```bash
# Create intake session
POST /api/v1/intake/sessions
{
  "project_id": "proj-123",
  "customer_name": "Jane Smith",
  "customer_org": "DoD PEO",
  "impact_level": "IL5"
}

# Process turn
POST /api/v1/intake/sessions/{session_id}/turns
{"message": "We need a mission planning tool for SOF operations"}

# Get readiness score
GET /api/v1/intake/sessions/{session_id}/readiness

# Decompose requirements
POST /api/v1/intake/sessions/{session_id}/decompose
{"level": "story", "generate_bdd": true}
```

### API Documentation

Interactive Swagger UI is available at `/api/v1/docs` when the API gateway is running. OpenAPI 3.0.3 spec at `/api/v1/openapi.json`.

---

## MCP Streamable HTTP

For Claude Code clients or MCP-compatible tools, ICDEV exposes the same functionality via MCP Streamable HTTP transport (spec 2025-03-26).

### Endpoint

```
POST /mcp/v1/
GET  /mcp/v1/      (SSE stream for notifications)
```

### Example: Call a Tool

```json
POST /mcp/v1/
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "scaffold",
    "arguments": {
      "type": "python-backend",
      "name": "my-app",
      "project_path": "/tmp"
    }
  },
  "id": 1
}
```

### Available MCP Servers

| Server | Tool Count | Key Tools |
|--------|-----------|-----------|
| icdev-core | 5 | project_create, project_list, task_dispatch |
| icdev-compliance | 30+ | ssp_generate, stig_check, crosswalk_query, fedramp_assess |
| icdev-builder | 10 | scaffold, generate_code, run_tests, dev_profile_create, dev_profile_resolve |
| icdev-security | 4 | sast_run, dependency_audit, secret_detect, container_scan |
| icdev-infra | 5 | terraform_plan, ansible_run, k8s_deploy, pipeline_generate |
| icdev-requirements | 9 | create_intake_session, process_turn, detect_gaps, decompose |
| icdev-devsecops | 12 | profile_create, maturity_assess, pipeline_security, policy_generate |

Full tool list: see `CLAUDE.md` MCP Servers section.

---

## Direct Python CLI

Every ICDEV tool can be called from the command line. All tools support `--json` for structured output and `--human` for colored terminal output.

### Pattern

```bash
python tools/<domain>/<tool>.py --<action> --project-id "proj-123" --json
```

### Common Operations

```bash
# ── Project ──
python tools/project/project_create.py --name "my-app" --type microservice
python tools/project/project_status.py --project-id "proj-123" --json

# ── Build ──
python tools/builder/scaffolder.py --type python-backend --name "my-app" --project-path /tmp
python tools/builder/test_writer.py --feature "user auth" --project-dir /path --language python
python tools/builder/code_generator.py --test-file /path/test.py --project-dir /path
python tools/builder/linter.py --project-dir /path
python tools/builder/formatter.py --project-dir /path

# ── Compliance ──
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/poam_generator.py --project-id "proj-123"
python tools/compliance/stig_checker.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-dir /path
python tools/compliance/crosswalk_engine.py --control AC-2

# ── Security ──
python tools/security/sast_runner.py --project-dir /path
python tools/security/dependency_auditor.py --project-dir /path
python tools/security/secret_detector.py --project-dir /path
python tools/security/container_scanner.py --image "my-image:latest"

# ── Dev Profiles ──
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --resolve --json
python tools/builder/profile_detector.py --repo-path /path/to/repo --json
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --json

# ── Infrastructure ──
python tools/infra/terraform_generator.py --project-id "proj-123"
python tools/infra/k8s_generator.py --project-id "proj-123"
python tools/infra/pipeline_generator.py --project-id "proj-123"

# ── Testing ──
pytest tests/ -v --tb=short
python tools/testing/test_orchestrator.py --project-dir /path
python tools/testing/e2e_runner.py --run-all
```

### JSON Output

All tools support `--json` for machine-readable output. This is the recommended mode for scripting:

```bash
result=$(python tools/compliance/stig_checker.py --project-id "proj-123" --json)
cat1_count=$(echo "$result" | python -c "import sys,json; print(json.load(sys.stdin)['summary']['cat1_count'])")

if [ "$cat1_count" -gt 0 ]; then
  echo "BLOCKED: $cat1_count CAT1 STIG findings"
  exit 1
fi
```

### Chaining Tools

Tools are designed to be chained. The output of one tool feeds into the next:

```bash
# Full compliance pipeline in 4 commands
python tools/security/sast_runner.py --project-dir /path --json > .tmp/sast.json
python tools/compliance/stig_checker.py --project-id "proj-123" --json > .tmp/stig.json
python tools/compliance/ssp_generator.py --project-id "proj-123" --json > .tmp/ssp.json
python tools/compliance/sbom_generator.py --project-dir /path --json > .tmp/sbom.json
```

---

## Rate Limits

API rate limits vary by subscription tier:

| Tier | Rate Limit | Burst |
|------|-----------|-------|
| Starter | 60/min | 10 |
| Professional | 300/min | 50 |
| Enterprise | Unlimited | Unlimited |

Rate limit headers are returned on every response:
```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 298
X-RateLimit-Reset: 1640000000
```

---

## Error Handling

All endpoints return consistent error responses:

```json
{
  "success": false,
  "error": {
    "code": "GATE_BLOCKED",
    "message": "Merge gate blocked: 2 CAT1 STIG findings",
    "details": {
      "gate": "merge",
      "findings": [
        {"id": "V-123456", "severity": "CAT1", "description": "..."}
      ]
    }
  }
}
```

Common error codes:
| Code | Meaning |
|------|---------|
| `AUTH_REQUIRED` | Missing or invalid authentication |
| `FORBIDDEN` | Insufficient role for this operation |
| `NOT_FOUND` | Project/resource not found |
| `GATE_BLOCKED` | Security gate blocking the operation |
| `RATE_LIMITED` | Too many requests |
| `VALIDATION_ERROR` | Invalid request parameters |
