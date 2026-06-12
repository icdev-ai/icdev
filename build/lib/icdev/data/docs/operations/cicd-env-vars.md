# [TEMPLATE: CUI // SP-CTI]
# GitLab CI/CD Environment Variables Reference

> **Classification:** CUI // SP-CTI
> **Applies to:** All ICDEV™ GitLab pipelines (`.gitlab-ci.yml`)

Configure these variables at **Settings → CI/CD → Variables** in your GitLab project.
Variables marked **masked** should never appear in job logs.

---

## Core Platform Variables

| Variable | Required | Masked | Default | Description |
|----------|----------|--------|---------|-------------|
| `ICDEV_CLASSIFICATION` | Yes | No | `CUI` | Classification level: `UNCLASSIFIED`, `CUI`, `SECRET` |
| `ICDEV_IMPACT_LEVEL` | Yes | No | `IL4` | Impact level: `IL2`, `IL4`, `IL5`, `IL6` |
| `ICDEV_DASHBOARD_SECRET` | Yes | Yes | — | Flask session secret. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ICDEV_CUI_BANNER_ENABLED` | No | No | `true` | Show CUI classification banner on dashboard |
| `ICDEV_NO_LLM` | No | No | `false` | Set `true` for air-gapped environments (disables all LLM calls) |
| `PYTHONPATH` | No | No | `.` | Ensure tools/ is importable; set to `.` |

---

## LLM Provider Variables

At least one LLM provider is needed for `kanban-plan` and `kanban-build` jobs.
For air-gap environments, Ollama alone is sufficient.

### Anthropic Claude (for `kanban-plan` jobs)

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `ANTHROPIC_API_KEY` | For plan jobs | Yes | Get from [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `ANTHROPIC_MODEL` | No | No | Default: `claude-sonnet-4-20250514` |

### Ollama (for `kanban-build`, `kanban-chore` jobs — air-gap safe)

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `OLLAMA_HOST` | For build/chore | No | e.g., `http://10.0.1.50:11434` |
| `OLLAMA_MODEL` | No | No | Default: `qwen3.5:latest` |

### AWS Bedrock (GovCloud LLM)

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `AWS_DEFAULT_REGION` | For Bedrock | No | e.g., `us-gov-west-1` |
| `AWS_ACCESS_KEY_ID` | For Bedrock | Yes | IAM key with `bedrock:InvokeModel` permission |
| `AWS_SECRET_ACCESS_KEY` | For Bedrock | Yes | IAM secret |

### Azure OpenAI (GovCloud LLM)

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `AZURE_OPENAI_API_KEY` | For Azure | Yes | Azure OpenAI resource key |
| `AZURE_OPENAI_ENDPOINT` | For Azure | No | e.g., `https://<resource>.openai.azure.com/` |
| `AZURE_OPENAI_MODEL` | No | No | Default: `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | No | No | Default: `2024-12-01-preview` |

### LLM Routing

| Variable | Required | Masked | Default | Description |
|----------|----------|--------|---------|-------------|
| `LLM_TWO_TIER_ENABLED` | No | No | `true` | Ollama drafts, cloud LLM reviews |
| `LLM_CONFIDENCE_THRESHOLD` | No | No | `0.85` | Below this score, cloud LLM enhances |

---

## Kanban Pipeline Trigger Variables

These variables are set **only** when triggering pipeline via the GitLab trigger API
(`POST /api/v4/projects/:id/trigger/pipeline`). Do not set them as project-level variables.

| Variable | Description |
|----------|-------------|
| `KANBAN_TASK_ID` | UUID of the Kanban task (e.g., `aisg-b1-03`) |
| `KANBAN_TASK_TYPE` | One of: `plan`, `build`, `fix`, `test`, `deploy`, `chore`, `scan`, `compliance` |
| `KANBAN_PROMPT` | Base64-encoded task prompt. Encode: `base64 -w0 /tmp/prompt.txt` |
| `KANBAN_LLM_MODE` | `claude`, `ollama`, or `none` |

**Example trigger (curl):**
```bash
PROMPT_B64=$(echo "Build the AISG wizard backend" | base64 -w0)

curl --request POST \
  --form "token=$CI_JOB_TOKEN" \
  --form "ref=main" \
  --form "variables[KANBAN_TASK_ID]=aisg-b1-02" \
  --form "variables[KANBAN_TASK_TYPE]=build" \
  --form "variables[KANBAN_PROMPT]=$PROMPT_B64" \
  --form "variables[KANBAN_LLM_MODE]=ollama" \
  "https://gitlab.example.com/api/v4/projects/42/trigger/pipeline"
```

---

## Security Gate Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ICDEV_OPENCLAW_ENABLED` | No | `false` | Set `true` to activate OpenClaw marketplace bridge gate |

When `ICDEV_OPENCLAW_ENABLED=true`, the `security:openclaw-gate` job runs and blocks
the pipeline if any marketplace bridge check fails.

---

## Database Variables

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `DATABASE_URL` | For PostgreSQL | Yes | `postgresql://user:pass@host:5432/icdev` |
| `ICDEV_DB_BACKEND` | No | No | `sqlite` (default) or `postgresql` |

If `DATABASE_URL` is unset, jobs fall back to SQLite (`data/icdev.db`) — suitable
for CI ephemeral environments.

---

## RAG / Embedding Variables

| Variable | Required | Masked | Description |
|----------|----------|--------|-------------|
| `RAG_ENABLED` | No | No | `true` enables RAG subsystem |
| `RAG_EMBEDDING_MODEL` | No | No | Default: `nomic-embed-text` (via Ollama) |
| `OPENAI_API_KEY` | For OpenAI embed | Yes | Used if `RAG_EMBEDDING_MODEL=text-embedding-3-small` |

---

## Air-Gap / Offline Mode

For fully air-gapped environments, set:

```
ICDEV_NO_LLM=true
LLM_TWO_TIER_ENABLED=false
OLLAMA_HOST=http://10.x.x.x:11434   # local Ollama server
ICDEV_OPENCLAW_ENABLED=false         # no external registry calls
DATABASE_URL=                         # SQLite fallback
```

The `security:sandbox` stage requires Docker-in-Docker and will work offline
provided the `python:3.11-slim` image is mirrored to your internal registry.
Update `.gitlab-ci.yml` `image:` fields to point to your mirror:

```yaml
default:
  image: registry.internal.mil/base/python:3.11-slim
```

---

## Variable Validation

Run this locally before pushing to catch missing variables early:

```bash
python tools/ci/pipeline_config_generator.py --dir . --platform gitlab --dry-run --json
```

Expected: `"errors": []`

For a full environment health check:

```bash
python tools/testing/health_check.py --json
```

Expected: `"overall_health": "healthy"`

---

## Security Notes

- **Never** commit `.env` to version control (`.gitignore` already excludes it)
- All `*_API_KEY` and `*_SECRET` variables must be set as **masked** in GitLab
- Variables containing IP addresses or hostnames (`OLLAMA_HOST`, `DATABASE_URL`) should be **protected** (main/master branch only) if they point to production systems
- For IL5/IL6, variables containing CUI must be set as **protected + masked**
- Rotate `ICDEV_DASHBOARD_SECRET` after any team member departure

---

## Related Documentation

- `docs/operations/deployment-guide.md` — Full deployment instructions
- `goals/devsecops_cicd.md` — DevSecOps CI/CD Pipeline pattern (this pipeline's goal)
- `goals/devsecops_workflow.md` — DevSecOps profile lifecycle
- `args/security_gates.yaml` — Security gate thresholds
- `args/cicd_config.yaml` — CI/CD channel, routing, and recovery config
