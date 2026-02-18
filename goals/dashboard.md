# Goal: Web Dashboard

## Purpose
Provide a web-based dashboard for business users and operators to view project status, compliance posture, security findings, deployment history, and agent health. Server-side rendered with Flask for minimal attack surface and STIG compliance.

## Trigger
- User navigates to dashboard URL
- `/icdev-status` skill invoked (CLI equivalent)
- Automated status report generation

## Inputs
- ICDEV database (`data/icdev.db`)
- Args configuration files
- CUI marking templates (`args/cui_markings.yaml`)

## Architecture

### Technology Stack
- **Backend:** Flask (Python) — chosen for simplicity, auditability, smaller STIG surface
- **Frontend:** Server-side rendered Jinja2 templates — no client-side frameworks
- **Styling:** Custom CSS with gov-themed dark mode
- **JavaScript:** Minimal vanilla JS for API polling (30s auto-refresh)
- **Security:** CUI banners on every page, CSRF protection, no inline scripts

### Directory Structure
```
tools/dashboard/
├── app.py              # Flask app factory, routes
├── config.py           # Configuration from args/*.yaml
├── api/                # REST API blueprints
│   ├── projects.py     # /api/projects/*
│   ├── agents.py       # /api/agents/*
│   ├── compliance.py   # /api/compliance/*
│   ├── audit.py        # /api/audit/*
│   └── metrics.py      # /api/metrics/*
├── templates/          # Jinja2 HTML templates
│   ├── base.html       # CUI banners, nav, content block
│   ├── index.html      # Overview dashboard
│   ├── projects/       # Project views
│   ├── agents/         # Agent views
│   └── monitoring/     # Monitoring views
└── static/
    ├── css/style.css   # Gov-themed dark CSS
    └── js/api.js       # API client + auto-refresh
```

## Process

### Step 1: Application Setup
**Tool:** `tools/dashboard/app.py`
- Create Flask app with factory pattern
- Register blueprints for API routes
- Configure CUI markings from `args/cui_markings.yaml`
- Set up database connection to `data/icdev.db`

### Step 2: Dashboard Pages

#### Overview (/)
- Project count with status breakdown
- Compliance score summary
- Active alerts
- Recent audit trail entries
- Agent health overview

#### Projects (/projects, /projects/<id>)
- Project list with filtering by status
- Project detail with 5 tabs:
  - Overview (metadata, health)
  - Compliance (SSP, POAM, STIG, SBOM status)
  - Security (scan results, gate status)
  - Deployments (history, current versions)
  - Audit Trail (project-scoped events)

#### Compliance (/compliance)
- Control family coverage matrix
- STIG findings summary (CAT1/CAT2/CAT3)
- SSP/POAM document status
- SBOM currency

#### Security (/security)
- Vulnerability summary across projects
- Dependency CVE listing
- Secret detection alerts
- Security gate status

#### Agents (/agents)
- Agent health grid (8 agents)
- Task queue depths
- Recent task completions
- Error rates per agent

#### Monitoring (/monitoring)
- Health check status
- Metric charts (via API data)
- Active alerts with correlation
- Self-healing event history

#### Audit Trail (/audit)
- Searchable audit log
- Filter by event type, project, date range
- Export capability

### Step 3: REST API
All dashboard data is also available via REST API:
- `GET /api/projects` — List projects
- `GET /api/projects/<id>` — Project detail
- `GET /api/compliance/<project_id>` — Compliance status
- `GET /api/agents` — Agent health
- `GET /api/audit?event_type=&project_id=&limit=` — Audit trail
- `GET /api/metrics/<project_id>` — Metric snapshots

### Step 4: CUI Compliance
Every page includes:
- Top banner: "CUI // SP-CTI"
- Bottom banner: "CUI // SP-CTI"
- Designation indicator in page metadata
- No data exported without CUI markings

### Step 5: Security Hardening
- CSRF token on all forms
- Content-Security-Policy headers
- No inline JavaScript
- Session timeout (30 minutes)
- HTTPS-only in production
- Rate limiting on API endpoints

## Outputs
- Running Flask web application
- REST API for programmatic access
- CUI-marked HTML pages
- Auto-refreshing status displays

## Edge Cases
- Database locked: retry with exponential backoff
- Large audit trail: paginate (50 items per page)
- Agent unreachable: show last known status with staleness indicator
- No projects: show onboarding guide

## Related Goals
- `monitoring.md` — Data sources for monitoring views
- `compliance_workflow.md` — Compliance data
- `agent_management.md` — Agent health data
