# ACE — Autonomous Collaborative Engine (Co-Worker Canvas)
## CUI // SP-CTI

The ACE Co-Worker Engine is the ICDEV™ canvas for assembling, dispatching, and
monitoring teams of autonomous digital coworkers. It is a core building block of
the NOVA initiative; see `tools/manifest/autonomous-coworker.md` for the
ECHO/SOUL/TRUST/SELA integration.

## Canvas Blueprint

| Tool | File | Purpose |
|------|------|---------|
| ACE Blueprint | `tools/ace/blueprint.py` | Flask Blueprint (`ace_bp` + `ace_api_bp`) that mounts the Co-Worker canvas at `/coworker` and the JSON API at `/api/ace`. Provides pages (active teams, role catalog, instance detail, trust leaderboard), launch/status/messages/artifacts/abort endpoints, profile generation helpers, event-bus feeds, and the `/api/ace/iqe-query` IQE widget route. Uses `get_canvas_connection()` because `ace_*` tables have no `tenant_id`/`classification` columns. |
