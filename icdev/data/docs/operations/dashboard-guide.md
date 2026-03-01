# Dashboard Operations Guide

## Overview

The ICDEV Dashboard is a Flask-based web UI providing project status, compliance posture, security metrics, agent monitoring, and administrative controls. It is designed with a "GI-proof" UX philosophy: accessible, self-documenting, and operable without prior training.

**Key characteristics:**
- Server-side rendered (Flask SSR) with zero-dependency SVG charts
- SQLite-backed authentication (independent of SaaS layer)
- CUI-aware banner system for classified environments
- WCAG-accessible with ARIA attributes, skip-to-content links, and keyboard navigation
- Air-gap safe (no CDN dependencies in production)

---

## Starting the Dashboard

```bash
# Development mode (port 5000)
python tools/dashboard/app.py

# Production mode (gunicorn behind reverse proxy)
gunicorn -w 4 -b 0.0.0.0:5000 tools.dashboard.app:app
```

The dashboard binds to port 5000 by default. In production, place it behind nginx or an ALB with TLS termination.

---

## Dashboard Pages

### Core Pages

| Route | Purpose | Min Role |
|-------|---------|----------|
| `/` | Home dashboard with auto-notifications, pipeline status, sparkline charts | All |
| `/projects` | Project listing with friendly timestamps and status indicators | All |
| `/projects/<id>` | Project detail with role-based tab visibility (compliance, security, MBSE, etc.) | All |
| `/agents` | Agent registry with heartbeat age, health status, port assignments | All |
| `/monitoring` | Monitoring console with status icons and accessibility annotations | All |

### Workflow Pages

| Route | Purpose | Min Role |
|-------|---------|----------|
| `/wizard` | Getting Started wizard (3 questions to workflow recommendation) | All |
| `/quick-paths` | Quick Path workflow templates and error recovery reference | All |
| `/events` | Real-time event timeline via Server-Sent Events (SSE) | All |
| `/query` | Natural language compliance queries (NLQ-to-SQL via Bedrock) | developer |
| `/chat` | Agent chat interface (SQLite-based, no WebSocket) | developer |
| `/gateway` | Remote Command Gateway admin (bindings, command log, channels) | admin |
| `/batch` | Batch operations panel (multi-tool workflow execution) | developer |

### Observability Pages (Phase 46)

| Route | Purpose | Min Role |
|-------|---------|----------|
| `/traces` | Trace explorer: stat grid, trace list, span waterfall SVG | developer |
| `/provenance` | Provenance viewer: entity/activity tables, lineage query, PROV-JSON export | developer |
| `/xai` | XAI dashboard: assessment runner, coverage gauge, SHAP bar chart | isso |

### Profile & Administration Pages

| Route | Purpose | Min Role |
|-------|---------|----------|
| `/login` | API key login page | Public |
| `/logout` | Clear session and redirect to login | All |
| `/activity` | Merged activity feed (audit + hook events, WebSocket + polling fallback) | developer |
| `/usage` | Usage tracking and cost dashboard (per-user, per-provider) | pm |
| `/profile` | User profile and BYOK LLM key management | All |
| `/dev-profiles` | Dev profile management (create, resolve cascade, lock, version history) | developer |
| `/children` | Child application registry (health, genome version, capabilities, heartbeat) | developer |
| `/admin/users` | Admin user and API key management | admin |

---

## Authentication

### Architecture

Dashboard auth is self-contained against `data/icdev.db` (not imported from the SaaS layer). This keeps the dashboard independently deployable in air-gapped environments.

- **Storage:** `dashboard_users`, `dashboard_api_keys`, `dashboard_auth_log` tables in `data/icdev.db`
- **Sessions:** Flask signed sessions using `app.secret_key`
- **API Keys:** SHA-256 hashed before storage; never stored in plaintext

### Creating the First Admin

```bash
# Create initial admin user and receive an API key
python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"
```

This outputs an API key prefixed with `icdev_`. Store it securely; it cannot be retrieved after creation.

### Managing Users

```bash
# List all dashboard users
python tools/dashboard/auth.py list-users
```

Additional user management is available via the `/admin/users` page (admin role only). From there you can:
- Create new users with assigned roles
- Generate and revoke API keys
- View authentication logs

### Login Flow

1. Navigate to `/login`
2. Enter your API key
3. Flask sets a signed session cookie
4. Session persists until `/logout` or cookie expiration

---

## RBAC: Role-Based Access Control

Five roles control page visibility and feature access:

| Role | Description | Key Access |
|------|-------------|------------|
| **admin** | Full platform administration | `/admin/users`, `/gateway`, all pages |
| **pm** | Program/project management | `/usage`, `/projects`, `/quick-paths`, `/batch` |
| **developer** | Development and integration | `/query`, `/chat`, `/dev-profiles`, `/traces`, `/batch` |
| **isso** | Information System Security Officer | `/xai`, compliance tabs, security views |
| **co** | Contracting Officer | Project detail (read-only), compliance status |

### Role-Based Views

Append `?role=` to any page URL to switch the displayed perspective:

```
/projects/proj-123?role=pm          # PM view: schedule, cost, milestones
/projects/proj-123?role=developer   # Developer view: code, tests, builds
/projects/proj-123?role=isso        # ISSO view: compliance, security, ATO
/projects/proj-123?role=co          # CO view: contract deliverables, status
```

Role-based views use progressive disclosure: each role sees tabs and sections relevant to their responsibilities.

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `ICDEV_DASHBOARD_SECRET` | Flask session signing key | Auto-generated (insecure for production) |
| `ICDEV_CUI_BANNER_ENABLED` | Enable/disable CUI classification banners | `true` |
| `CUI_BANNER_TOP` | Custom top banner text | Standard CUI marking |
| `CUI_BANNER_BOTTOM` | Custom bottom banner text | Standard CUI marking |
| `ICDEV_BYOK_ENABLED` | Enable Bring Your Own Key LLM key management | `false` |
| `ICDEV_BYOK_ENCRYPTION_KEY` | Fernet AES-256 key for encrypting user LLM keys | Required when BYOK enabled |

### Production Configuration

For production deployments, always set `ICDEV_DASHBOARD_SECRET` explicitly:

```bash
export ICDEV_DASHBOARD_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
export ICDEV_CUI_BANNER_ENABLED=true
```

---

## CUI Banner Configuration

Classification banners are rendered at the top and bottom of every page when `ICDEV_CUI_BANNER_ENABLED=true`.

Banner text is generated by `tools/compliance/classification_manager.py` based on the project's impact level:

| Impact Level | Banner |
|-------------|--------|
| IL4 | `CUI // SP-CTI` |
| IL5 | `CUI // SP-CTI` |
| IL6 | `SECRET // NOFORN` (SIPR environments) |

To customize banners, set `CUI_BANNER_TOP` and `CUI_BANNER_BOTTOM` environment variables. Markings are applied at generation time (not post-processing) per architecture decision D5.

---

## BYOK LLM Key Management

When enabled, users can supply their own LLM provider API keys from the `/profile` page.

### Setup

```bash
# Generate a Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set environment variables
export ICDEV_BYOK_ENABLED=true
export ICDEV_BYOK_ENCRYPTION_KEY=<generated-key>
```

### Key Hierarchy

1. **Per-user BYOK key** (highest priority)
2. **Per-department environment variable**
3. **System configuration** (`args/llm_config.yaml`)

User keys are stored AES-256 encrypted (Fernet) in the `dashboard_user_llm_keys` table. The encryption key must be provided via `ICDEV_BYOK_ENCRYPTION_KEY`.

### Administration

Admins can enable or disable BYOK per tenant. When disabled, the "LLM Keys" section is hidden from the profile page.

---

## Real-Time Features

### SSE Live Updates (live.js)

The dashboard uses Server-Sent Events for near-real-time updates without WebSocket dependencies (air-gap compatible).

- Events debounce to 3-second batches to prevent API hammering (D99)
- Connection status indicator shows live/disconnected state
- Automatic reconnection on connection loss
- Falls back to HTTP polling when SSE is unavailable

The `/events` page provides a dedicated real-time event timeline powered by SSE.

### Batch Operations (batch.js)

Four built-in workflow templates execute from the UI:

| Workflow | Tools Executed |
|----------|---------------|
| ATO Package | SSP + POAM + STIG + SBOM generation |
| Security Scan | SAST + dependency audit + secret detection + container scan |
| Compliance Check | Multi-framework assessment across all applicable frameworks |
| Build Pipeline | Lint + format + test + coverage |

Batch operations run as sequential subprocesses in background threads (D100). The Flask request returns immediately; the frontend polls for status updates.

### Keyboard Shortcuts (shortcuts.js)

Chord-pattern shortcuts using `g` + key (1.5-second chord window):

| Shortcut | Action |
|----------|--------|
| `g` + `h` | Go to Home |
| `g` + `p` | Go to Projects |
| `g` + `a` | Go to Agents |
| `g` + `m` | Go to Monitoring |
| `g` + `e` | Go to Events |
| `?` | Open keyboard help modal |
| `/` | Focus search input |

Shortcuts avoid conflicts with browser-native key bindings. Invalid keys within the chord window cancel the chord.

---

## UX Features

### Glossary Tooltips

Technical terms display inline definitions on hover. Implemented via `data-glossary` HTML attributes and client-side JavaScript (D89). No backend changes are needed to add new terms.

### Friendly Timestamps

All timestamps display in human-readable relative format ("3 minutes ago", "Yesterday at 14:30") with full ISO timestamp on hover.

### Breadcrumbs

Hierarchical navigation breadcrumbs appear on all pages below the primary navigation bar.

### ARIA Accessibility

- `role="img"` and `aria-label` on all SVG charts
- Skip-to-content link as first focusable element
- Status icons with screen reader text
- Form labels and error descriptions linked via `aria-describedby`

### Charts (charts.js)

Zero-dependency SVG chart library supporting:
- Sparkline (inline trend indicators)
- Line charts (time series)
- Bar charts (comparisons)
- Donut charts (proportions)
- Gauge charts (scores and thresholds)

All charts are server-rendered data into lightweight SVG. No Chart.js or D3 dependency. WCAG accessible with `role="img"` and `aria-label`.

### Tables (tables.js)

Auto-enhances all `.table-container` tables on page load with:
- Full-text search across all columns
- Column sorting (click header)
- Column filtering (dropdown per column)
- CSV export (download button)

No per-table configuration required.

### Onboarding Tour (tour.js)

First-time visitors see a guided tour with spotlight overlay highlighting key dashboard features. Detection uses `localStorage` key `icdev_tour_completed` (no server-side tracking, air-gap safe per D98).

---

## Troubleshooting

### Dashboard won't start

```bash
# Verify database exists
python tools/db/init_icdev_db.py

# Check for port conflicts
lsof -i :5000
```

### Session errors

If users receive "session expired" errors frequently, ensure `ICDEV_DASHBOARD_SECRET` is set consistently across restarts. Auto-generated secrets change on each restart.

### SSE not updating

Verify the Flask process is running (SSE requires a persistent connection). Behind load balancers, ensure HTTP/1.1 chunked transfer encoding is not being buffered. Nginx configuration:

```nginx
location /events {
    proxy_pass http://localhost:5000;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
    chunked_transfer_encoding off;
    proxy_buffering off;
    proxy_cache off;
}
```

### CUI banners not appearing

```bash
# Verify the environment variable
echo $ICDEV_CUI_BANNER_ENABLED

# Should be "true" (string, not boolean)
export ICDEV_CUI_BANNER_ENABLED=true
```

---

## Related Configuration

| File | Purpose |
|------|---------|
| `args/observability_config.yaml` | Hook settings, HMAC signing, SIEM forwarding |
| `args/nlq_config.yaml` | NLQ-to-SQL settings for `/query` page |
| `args/resilience_config.yaml` | Circuit breaker and retry settings |
| `args/dev_profile_config.yaml` | Dev profile dimensions for `/dev-profiles` page |
| `args/observability_tracing_config.yaml` | Tracing config for `/traces` page |
