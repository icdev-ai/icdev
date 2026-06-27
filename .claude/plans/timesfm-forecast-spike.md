# Plan: TimesFM Forecasting Microservice Spike

CUI // SP-CTI

## Context

The external-repo adaptation scan (`.tmp/external_repo_adaptation_report.md`) ranked **google-research/timesfm** as the top actionable opportunity:

- License: Apache-2.0 (low risk)
- Creative pain score: 0.86
- Fit: ICDEV `arg` (model provider config) + `context` (telemetry data source) layers
- Recommendation: **Build** a self-hostable time-series forecasting microservice behind the LLM Router and expose it through the MONITOR canvas.

This plan scopes a bounded, production-grade spike.

---

## A — Architect (App Brief)

- **Problem:** ICDEV's MONITOR and data canvases react to historical telemetry but cannot forecast future capacity, security-event trends, or resource exhaustion for regulated/air-gapped workloads.
- **User:** Platform engineer / security analyst using the `/monitoring` dashboard.
- **Success (testable):**
  1. A user can POST a time-series payload and receive a point forecast + optional quantile bounds.
  2. The feature works when `ICDEV_LLM_PROVIDER=ollama` (air-gap) using a local TimesFM checkpoint.
  3. Every forecast invocation writes an append-only audit row.
  4. The `/monitoring/forecast` page renders without JS errors and includes an IQE query widget.
- **Constraints:**
  - Must be Apache-2.0 clean; no GPL/AGPL code paths.
  - Must use PostgreSQL (`get_connection()`) and `%s` placeholders.
  - Must keep TimesFM as an optional dependency (graceful degradation if `timesfm` or `torch` not installed).
  - Must pass SIPA scan before model weights are bundled for air-gap.
- **Assumptions:**
  - TimesFM Python package (`google-timesfm`) and model weights are installed/mounted separately.
  - The dashboard (`icdev/tools/dashboard/app.py`) is the integration point for UI.
  - IQE collection registration pattern follows existing adapters.
- **Interpretation chosen:** Build a *forecasting service* wrapper, not a full model-training pipeline. TimesFM is treated as an external model provider similar to how ICDEV treats Ollama models.

---

## T — Trace

### Data Schema

New table in main `icdev.db` (PG-backed):

```sql
CREATE TABLE IF NOT EXISTS forecast_jobs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,           -- e.g. 'monitor_alert', 'manual_upload', 'api'
    context       TEXT,                    -- user-supplied context / labels
    input_rows    INTEGER NOT NULL,
    input_summary JSONB,                   -- {start, end, freq, value_stats}
    status        TEXT NOT NULL DEFAULT 'pending', -- pending, running, completed, failed
    prediction    JSONB,                   -- {horizon, point, lower, upper, freq}
    model_id      TEXT DEFAULT 'timesfm-2.5-200m',
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    classification TEXT DEFAULT 'CUI',
    tenant_id     TEXT
);
```

**Append-only audit table** (added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`):

```sql
CREATE TABLE IF NOT EXISTS forecast_audit (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES forecast_jobs(id),
    event_type  TEXT NOT NULL,  -- created, started, completed, failed
    actor       TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now(),
    classification TEXT DEFAULT 'CUI'
);
```

### Integrations Map

| Service | Purpose | Auth | Notes |
|---------|---------|------|-------|
| TimesFM Python package | Model inference | Local / HuggingFace token | Optional dependency; graceful fallback |
| Ollama / local path | Air-gap model loading | None (local) | `OLLAMA_BASE_URL` or local checkpoint dir |
| PostgreSQL | Job state + audit | `get_connection()` | RLS-aware via tenant_id/classification |
| ICDEV dashboard | UI + API | Session auth | Add `/monitoring/forecast` route |
| IQE | Natural-language querying | Register `forecast.jobs` collection | Reuse existing widget |
| SIPA | Model-weight integrity scan | `tools/integrity/engine.py` | Gate for air-gap packaging |

### Technology Stack

- **Backend:** Python module `icdev/tools/forecast/timesfm_adapter.py`
- **API:** New Flask routes in `icdev/tools/dashboard/app.py`
- **Frontend:** Jinja2 template `icdev/tools/dashboard/templates/monitoring/forecast.html`
- **DB:** PostgreSQL via `icdev/tools/db/storage.py`
- **Tests:** pytest (`tests/test_timesfm_adapter.py`, dashboard route tests)

### Edge Cases

- TimesFM package not installed → return `503` with helpful message.
- Model weights missing → status `failed`, audit event.
- Empty or single-value input → validation error before model call.
- Non-numeric timestamps → parse error with clear message.
- Very long series → cap context length and warn.

---

## L — Link

Before building, validate:

1. **DB connectivity:** `python icdev/tools/db/storage.py --health --json` returns healthy.
2. **TimesFM availability (optional):** `python -c "import timesfm; print(timesfm.__version__)"` — if missing, graceful fallback still works.
3. **Ollama / local model path:** Verify `OLLAMA_BASE_URL` or `TIMESFM_MODEL_PATH` env vars.
4. **SIPA scan target:** Confirm `tools/integrity/engine.py` can scan arbitrary file paths.
5. **Dashboard route list:** Confirm `/monitoring` route exists and template path conventions.

---

## As — Assemble

### Phase 1: Backend adapter (no UI)

1. Create `icdev/tools/forecast/__init__.py` (namespace package marker).
2. Create `icdev/tools/forecast/timesfm_adapter.py`:
   - `ForecastJob` dataclass
   - `_load_model()` — lazy, cached per process, air-gap aware
   - `forecast(payload: dict) -> dict` — main inference entrypoint
   - `create_job(conn, payload) -> job_id`
   - `run_job(conn, job_id)` — async-friendly wrapper
   - `get_job(conn, job_id)`
   - `health()` — returns model availability
3. Create DB migration `icdev/tools/db/migrations/219_forecast_jobs.sql`.
4. Add table to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` for `forecast_audit`.
5. Add minimal tests: `tests/test_timesfm_adapter.py`.

### Phase 2: Dashboard integration

6. Add `/monitoring/forecast` GET route to `icdev/tools/dashboard/app.py`.
7. Add `/api/forecast` POST endpoint.
8. Add `icdev/tools/dashboard/templates/monitoring/forecast.html` with form + results panel + IQE widget.
9. Update `.claude/commands/start.md` Pages line with new route.
10. Add nav link in monitoring overview page to forecast sub-page.

### Phase 3: IQE + cross-engine wiring

11. Create `icdev/tools/iqe/adapters/forecast.py` registering `forecast.jobs` collection.
12. Add `forecast` entry to `_CANVAS_MAP` in `app.py` and `PATH_CANVAS` in `base.html` mini-bar.
13. Seed ≥3 IQE queries in `context/iqe/queries/forecast/`.

### Phase 4: Compliance + documentation

14. Add TimesFM model-weight scan decision to `docs/security/sandbox-coverage.md`.
15. Update `tools/manifest/<topic>.md` with new tool entry.
16. Run `python icdev/tools/dx/companion.py --sync --write --json`.
17. Run `python icdev/tools/workflow/coherence_checker.py --all --fix --gate`.

### Phase 5: V&V

18. Run unit tests.
19. Run dashboard smoke test.
20. If dashboard changed, run Playwright MCP verification for `/monitoring/forecast`.

---

## S — Stress-test / Acceptance Criteria

1. `pytest tests/test_timesfm_adapter.py -v` passes.
2. `python icdev/tools/forecast/timesfm_adapter.py --health --json` returns valid JSON when model available; graceful `available: false` when not.
3. POST to `/api/forecast` with sample payload returns `200` and a forecast object (or `503` if model missing, with clear message).
4. `/monitoring/forecast` page renders HTTP 200 and includes the IQE widget.
5. `icdev/tools/workflow/coherence_checker.py --all --gate` passes.
6. `ruff check icdev/tools/forecast tests/test_timesfm_adapter.py` passes.

---

## Out of Scope

- Full model training or fine-tuning pipeline.
- Real-time streaming forecast ingestion (batch jobs only in this spike).
- Foundry/autonomous-build wiring (can be added after spike passes V&V).
- worldmonitor integration (blocked by AGPL; handled in separate architecture decision).

---

## Follow-on Recommendations

1. **Shared adapter registry** (`pp-acdcb090ebb3`) — consolidate source connectors after this spike.
2. **Situational-awareness canvas** — architecture decision on clean-room vs. partnership for worldmonitor patterns.
3. **Research Engine repo scanner** — add `github_repo` source for focused external-repo scans.
