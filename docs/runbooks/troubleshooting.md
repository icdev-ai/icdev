# Troubleshooting Runbook

> CUI // SP-CTI

## Overview

This runbook covers diagnostic tools, common issues, and resolution procedures for the ICDEV platform. Start with the diagnostic tools section to assess system health, then consult the common issues section for specific problems.

---

## Diagnostic Tools

### Health Check

Full system health check covering databases, directories, configuration files, and tool availability.

```bash
# Human-readable output
python tools/testing/health_check.py

# JSON output for automation
python tools/testing/health_check.py --json
```

### Production Audit

30 automated checks across 6 categories (platform, security, compliance, integration, performance, documentation). Use this for pre-deployment validation or periodic health assessment.

```bash
# Full audit with streaming output
python tools/testing/production_audit.py --human --stream

# JSON output
python tools/testing/production_audit.py --json

# Single category
python tools/testing/production_audit.py --category security --json

# Multiple categories
python tools/testing/production_audit.py --category security,compliance --json

# Gate evaluation (exit code 0=pass, 1=fail)
python tools/testing/production_audit.py --gate --json
```

### Production Remediation

Auto-fix audit blockers using a 3-tier confidence model.

```bash
# Auto-fix all issues
python tools/testing/production_remediate.py --auto --json

# Preview fixes without applying (dry run)
python tools/testing/production_remediate.py --dry-run --human --stream

# Fix a specific check
python tools/testing/production_remediate.py --check-id SEC-002 --auto

# Reuse latest audit results (skip re-audit)
python tools/testing/production_remediate.py --skip-audit --auto --json

# Stream output
python tools/testing/production_remediate.py --human --stream
```

Confidence tiers:
- **>= 0.7**: Auto-fix applied immediately.
- **0.3 - 0.7**: Suggested fix shown; requires manual approval.
- **< 0.3**: Escalated with full context; no auto-fix attempted.

### .claude Directory Governance

Validates alignment of `.claude` configuration files (hooks, settings, commands, deny rules).

```bash
# JSON output
python tools/testing/claude_dir_validator.py --json

# Human-readable output
python tools/testing/claude_dir_validator.py --human

# Single check
python tools/testing/claude_dir_validator.py --check append-only --json
```

Six checks performed:
1. Append-only table protection in `pre_tool_use.py`
2. Hook syntax validation
3. Hook reference integrity
4. Dashboard route documentation
5. E2E spec coverage
6. Settings deny rule completeness

### Platform Compatibility

Validates OS compatibility, Python version, and required dependencies.

```bash
# Human output
python tools/testing/platform_check.py

# JSON output
python tools/testing/platform_check.py --json
```

---

## Common Issues

### MCP Server Won't Start

**Symptoms**: MCP server process exits immediately or returns connection errors.

**Diagnosis**:
1. Check Python path:
   ```bash
   which python
   python --version
   ```
2. Verify environment variables:
   ```bash
   echo $ICDEV_DB_PATH
   echo $ICDEV_PROJECT_ROOT
   ```
3. Check if the database exists:
   ```bash
   ls -la data/icdev.db
   ```
4. Test the MCP server directly:
   ```bash
   python tools/mcp/core_server.py
   ```

**Resolution**:
- Set `ICDEV_DB_PATH` to the absolute path of `data/icdev.db`.
- Set `ICDEV_PROJECT_ROOT` to the ICDEV root directory.
- Initialize the database if missing: `python tools/db/init_icdev_db.py`.
- Check `.mcp.json` for correct Python path and server configuration.

---

### Dashboard Authentication Issues

**Symptoms**: Cannot log in, API key rejected, session expired immediately.

**Diagnosis**:
1. Verify the dashboard secret is set:
   ```bash
   echo $ICDEV_DASHBOARD_SECRET
   ```
2. Check if admin user exists:
   ```bash
   python tools/dashboard/auth.py list-users
   ```
3. Check auth log in database (query `dashboard_auth_log` table).

**Resolution**:
- Set `ICDEV_DASHBOARD_SECRET` to a secure random string (Flask session signing).
- Create an admin user if none exists:
  ```bash
  python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"
  ```
  This outputs an API key. Store it securely.
- Clear browser cookies and retry login.
- For RBAC issues, verify user role: 5 roles are supported (admin, pm, developer, isso, co).

---

### Database Locked Errors

**Symptoms**: `sqlite3.OperationalError: database is locked`

**Diagnosis**:
1. Check for concurrent processes accessing the database:
   ```bash
   # Linux/macOS
   fuser data/icdev.db
   # or
   lsof data/icdev.db
   ```
2. Check WAL mode status:
   ```bash
   python -c "import sqlite3; c=sqlite3.connect('data/icdev.db'); print(c.execute('PRAGMA journal_mode').fetchone())"
   ```

**Resolution**:
- ICDEV databases use WAL (Write-Ahead Logging) mode for concurrent read access.
- If locked, wait and retry. The `busy_timeout` pragma is set by default.
- Kill stale processes holding the lock.
- If WAL files (`-wal`, `-shm`) are corrupt, checkpoint and recover:
  ```bash
  python -c "import sqlite3; c=sqlite3.connect('data/icdev.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
  ```
- As a last resort, restore from backup (see `docs/runbooks/backup-restore.md`).

---

### Import Errors / Missing Dependencies

**Symptoms**: `ModuleNotFoundError`, `ImportError` when running tools.

**Diagnosis**:
1. Check if virtual environment is activated:
   ```bash
   which python
   pip list
   ```
2. Verify requirements are installed:
   ```bash
   pip install -r requirements.txt
   ```

**Resolution**:
- Activate the correct virtual environment.
- Install all dependencies: `pip install -r requirements.txt`.
- For optional packages (openai, anthropic, boto3), install only what your deployment needs.
- For air-gapped environments, pre-download wheels and install from local directory:
  ```bash
  pip install --no-index --find-links /path/to/wheels -r requirements.txt
  ```

---

### Playwright Screenshots in Wrong Directory

**Symptoms**: E2E tests pass but screenshots are not found, or screenshots saved to unexpected location.

**Resolution**:
- Use the `playwright/screenshots/` prefix for all screenshot paths in E2E test specs.
- Check `.tmp/test_runs/screenshots/` for test runner output.
- Verify Playwright MCP configuration in `.mcp.json`.

---

### Air-Gapped Environment Setup

**Symptoms**: Tools fail to reach external APIs, embedding generation fails, LLM calls time out.

**Diagnosis**:
1. Check LLM config:
   ```bash
   cat args/llm_config.yaml | grep prefer_local
   ```
2. Check Ollama availability:
   ```bash
   curl http://localhost:11434/v1/models
   ```

**Resolution**:
- Set `prefer_local: true` in `args/llm_config.yaml`.
- Set `OLLAMA_BASE_URL=http://localhost:11434/v1` for local model support.
- Ensure Ollama is running with required models:
  - LLM: `ollama pull llama3` (or configured model)
  - Embeddings: `ollama pull nomic-embed-text`
  - Vision: `ollama pull llava` (for screenshot validation)
- Set `environment.mode: air_gapped` in `args/remote_gateway_config.yaml` to disable internet-dependent channels.
- Set `cloud_mode: air_gapped` in `args/cloud_config.yaml` for cloud provider configuration.

---

### Agent Communication Failures

**Symptoms**: A2A task dispatch fails, agents cannot reach each other, timeout errors between agents.

**Diagnosis**:
1. Check agent health:
   ```bash
   python tools/monitor/health_checker.py --target "http://localhost:8444/health"
   ```
2. Verify agent is running:
   ```bash
   kubectl get pods -n icdev
   kubectl logs -n icdev <pod-name>
   ```
3. Check network policies:
   ```bash
   kubectl get networkpolicies -n icdev
   ```

**Resolution**:
- Verify mTLS certificates are valid and not expired.
- Check agent port assignments in `args/agent_config.yaml` (ports 8443-8458).
- Verify K8s network policies allow inter-agent traffic within the `icdev` namespace.
- Check the circuit breaker state for the target agent (see Circuit Breakers below).
- Test connectivity directly:
  ```bash
  kubectl exec -n icdev <source-pod> -- curl -k https://<target-service>:8444/health
  ```

---

### Compliance Gate Failures

**Symptoms**: Security gate blocks deployment, merge, or artifact generation.

**Diagnosis**:
1. Run the specific gate check:
   ```bash
   python tools/testing/production_audit.py --category compliance --json
   ```
2. Check which gate failed in the output.

**Resolution by Gate**:
- **STIG CAT1**: Fix all Category 1 STIG findings before proceeding. Zero tolerance.
- **Critical vulnerabilities**: Update dependencies: `python tools/maintenance/remediation_engine.py --auto`.
- **Missing CUI markings**: Apply markings: `python tools/compliance/cui_marker.py --file <path> --marking "CUI // SP-CTI"`.
- **SBOM outdated**: Regenerate: `python tools/compliance/sbom_generator.py --project-dir <path>`.
- **FedRAMP/CMMC failures**: Run the specific assessor and address findings:
  ```bash
  python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate
  python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2
  ```

---

## Circuit Breakers

ICDEV uses application-level circuit breakers (D146) with a 3-state machine.

### States

| State | Behavior | Transition |
|-------|----------|-----------|
| **CLOSED** | Normal operation. All requests pass through. | Failure threshold exceeded -> OPEN |
| **OPEN** | All requests fail fast (no downstream calls). | Reset timeout expires -> HALF_OPEN |
| **HALF_OPEN** | One test request allowed. | Success -> CLOSED; Failure -> OPEN |

### Configuration (args/resilience_config.yaml)

```yaml
circuit_breaker:
  default:
    failure_threshold: 5
    reset_timeout_seconds: 60
    half_open_max_calls: 1

  services:
    bedrock:
      failure_threshold: 3
      reset_timeout_seconds: 120
    redis:
      failure_threshold: 10
      reset_timeout_seconds: 30
    jira:
      failure_threshold: 5
      reset_timeout_seconds: 300
    servicenow:
      failure_threshold: 5
      reset_timeout_seconds: 300
    gitlab:
      failure_threshold: 5
      reset_timeout_seconds: 300
```

### Monitoring Circuit Breaker State

Check the Prometheus `/metrics` endpoint for `icdev_circuit_breaker_state`:
- `0` = CLOSED (healthy)
- `1` = OPEN (failing)
- `2` = HALF_OPEN (testing)

---

## Retry Configuration

ICDEV uses exponential backoff with full jitter (D147).

### Default Settings (args/resilience_config.yaml)

```yaml
retry:
  max_retries: 3
  base_delay: 1.0          # seconds
  max_delay: 30.0           # seconds cap
```

### Behavior

- Delay = `min(max_delay, base_delay * 2^attempt) * random(0, 1)`
- Full jitter prevents thundering herd on recovery.
- Configurable exception list (only transient errors are retried).
- Optional `on_retry` callback for logging/metrics.

---

## Correlation IDs

Every request is tagged with a correlation ID (D149) for end-to-end tracing.

### Format

12-character UUID prefix, extended to W3C `traceparent` format (D281) for distributed tracing:

```
traceparent: 00-<trace-id>-<span-id>-01
```

### Propagation Path

1. Flask `before_request` middleware generates the correlation ID.
2. ID propagates through A2A JSON-RPC metadata.
3. ID recorded in audit trail `session_id` field.
4. ID passed to MCP tool calls via trace context.

### Finding a Request by Correlation ID

1. Check the audit trail:
   ```bash
   python tools/audit/audit_query.py --project "proj-123" --format json
   ```
   Filter by `session_id` matching the correlation ID prefix.

2. Check distributed traces (Phase 46):
   Navigate to `/traces` on the dashboard and search by trace ID.

3. Check agent logs:
   Search for the correlation ID in agent log output (structured JSON logging includes the ID).

---

## Log Locations and Formats

### Application Logs

| Component | Location | Format |
|-----------|----------|--------|
| Dashboard | stdout/stderr | Structured JSON (Flask) |
| MCP Servers | stdout/stderr | Structured JSON |
| Agent Executor | `agents/` directory | JSONL (one JSON object per line) |
| Memory System | `memory/logs/YYYY-MM-DD.md` | Markdown daily logs |
| CI/CD Workflows | `.tmp/ci/` | Run artifacts |
| Test Results | `.tmp/test_runs/` | Test output and screenshots |
| Backups | `data/backups/` | Backup files with metadata |

### Agent Executor Logs (D35)

Agent executor stores JSONL output for audit and replay:

```bash
ls agents/
# Example: agents/builder-agent-2026-02-23T10-30-00.jsonl
```

### Audit Trail

Append-only audit events in `data/icdev.db`:

```bash
python tools/audit/audit_query.py --project "proj-123" --format json
```

### Hook Events

Hook events (Phase 39) stored in `hook_events` table with HMAC-SHA256 signatures:

```bash
# View merged activity feed (audit + hooks)
# Navigate to /activity on dashboard
```

---

## Diagnostic Quick Reference

| Symptom | First Command | Next Steps |
|---------|--------------|------------|
| System won't start | `python tools/testing/health_check.py --json` | Check missing files, DB init |
| Gate blocks deploy | `python tools/testing/production_audit.py --gate --json` | Check specific gate category |
| Agent unreachable | `python tools/monitor/health_checker.py --target <url>` | Check pods, mTLS, network |
| Slow responses | Open `/traces` dashboard | Check span waterfall for bottleneck |
| LLM calls fail | `python tools/cloud/csp_health_checker.py --check --json` | Check circuit breaker, CSP health |
| Database errors | `python tools/db/migrate.py --status --json` | Check migrations, WAL, locks |
| Auth rejected | `python tools/dashboard/auth.py list-users` | Check user exists, key valid |
| Compliance gap | `python tools/compliance/crosswalk_engine.py --project-id X --coverage` | Run specific framework assessor |
| Missing embeddings | `python tools/memory/embed_memory.py --all` | Check Ollama/OpenAI availability |
| Platform compat | `python tools/testing/platform_check.py --json` | Check OS, Python version, deps |

---

## Escalation Path

If the issue cannot be resolved using this runbook:

1. Collect diagnostic output:
   ```bash
   python tools/testing/health_check.py --json > /tmp/health.json
   python tools/testing/production_audit.py --json > /tmp/audit.json
   python tools/testing/platform_check.py --json > /tmp/platform.json
   ```
2. Export recent audit trail:
   ```bash
   python tools/audit/audit_query.py --project "system" --format json > /tmp/audit-trail.json
   ```
3. Export trace data if applicable:
   ```bash
   python tools/observability/provenance/prov_export.py --project-id "proj-123" --json > /tmp/provenance.json
   ```
4. Package all diagnostic files and provide to the platform engineering team.
