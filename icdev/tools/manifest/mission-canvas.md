# Mission Canvas (Program Command Center)

> ICDEV canvas module — unified dashboard for 13 operational capabilities.
> Location: `icdev/tools/mission_canvas/`

## Capabilities

| # | Capability | Reused Module |
|---|---|---|
| 1 | Autonomous AI agent orchestration | `icdev.tools.agent.team_orchestrator` |
| 2 | Real-time correlation / monitoring / alerting | `icdev.tools.strategos.temporal_correlator`, `icdev.tools.awareness.drift_detector` |
| 3 | Plain-English mission-ready outputs | `icdev.tools.studio.wne.narrative_generator` |
| 4 | Living digital twin | `icdev.tools.data_canvas.twin`, `icdev.tools.pipeline.twin` |
| 5 | Traceable source-attributed evidence | `icdev.tools.observability.provenance.prov_recorder` |
| 6 | Spatial & temporal analysis | `icdev.tools.strategos.temporal_correlator`, `icdev.tools.strategos.ipb` |
| 7 | Automated discovery & visualization | `icdev.tools.awareness.health_prober`, `icdev.tools.awareness.component_indexer` |
| 8 | Conflict detection & resolution | `icdev.tools.filesync.conflict_resolver` |
| 9 | Portfolio scaling / optimization | `icdev.tools.pipeline.snapshot`, `icdev.tools.network.montecarlo` |
| 10 | FedRAMP Security & Zero Trust | `icdev.tools.devsecops.zta_maturity_scorer` |
| 11 | AI trust mechanisms | `icdev.tools.security.confabulation_detector`, `icdev.tools.aiml_canvas.governance_assessor` |
| 12 | Modular agentic architecture / plugin system | `icdev.tools.agent.skill_router`, `icdev.tools.extensions` |
| 13 | DevSecOps CI/CD integration | `icdev.tools.pipeline.blueprint`, `icdev.tools.pipeline.deploy_catalog` |

## Files

- `icdev/tools/mission_canvas/blueprint.py` — Flask blueprint factory
- `icdev/tools/mission_canvas/constants.py` — canvas constants & intent rules
- `icdev/tools/mission_canvas/orchestrator.py` — agent orchestration wrapper
- `icdev/tools/mission_canvas/twin.py` — digital twin wrapper
- `icdev/tools/mission_canvas/evidence.py` — provenance wrapper
- `icdev/tools/mission_canvas/correlator.py` — real-time correlation wrapper
- `icdev/tools/mission_canvas/discovery.py` — auto-discovery wrapper
- `icdev/tools/mission_canvas/conflict_resolver.py` — conflict detection wrapper
- `icdev/tools/mission_canvas/portfolio.py` — portfolio optimization wrapper
- `icdev/tools/mission_canvas/security_posture.py` — ZTA / FedRAMP wrapper
- `icdev/tools/mission_canvas/ai_trust.py` — AI trust wrapper
- `icdev/tools/mission_canvas/narrative.py` — plain-English narrative wrapper
- `icdev/tools/mission_canvas/cicd_bridge.py` — CI/CD wrapper
- `icdev/tools/dashboard/templates/mission_canvas/index.html` — landing page
- `icdev/tools/dashboard/templates/mission_canvas/detail.html` — drill-down page
- `icdev/tools/db/migrations/117_mission_canvas/up.py` — DB migration
- `icdev/tools/iqe/adapters/mission_canvas.py` — IQE collection adapters

## Feature Flag

`ICDEV_MISSION_CANVAS_ENABLED` (default: `true`)

## IQE Collections

- `mission.sessions` — `mission_canvas_sessions`
- `mission.twins` — `mission_canvas_twins`
- `mission.evidence` — `mission_canvas_evidence`
- `mission.alerts` — `mission_canvas_alerts`

## Dashboard Route

`/mission-canvas/`

## API Routes

- `GET /mission-canvas/api/twin`
- `POST /mission-canvas/api/correlate`
- `GET /mission-canvas/api/evidence`
- `GET /mission-canvas/api/discover`
- `POST /mission-canvas/api/orchestrate`
- `GET /mission-canvas/api/portfolio`
- `GET /mission-canvas/api/security-posture`
- `GET /mission-canvas/api/ai-trust`
- `POST /mission-canvas/api/narrative`
- `GET /mission-canvas/api/cicd`
- `POST /mission-canvas/api/cicd/deploy`
- `POST /mission-canvas/api/conflicts`
- `POST /mission-canvas/api/iqe-query`
