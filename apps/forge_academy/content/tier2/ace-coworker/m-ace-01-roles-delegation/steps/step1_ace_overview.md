---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:1
step_class: icdev:Lesson
title: ACE Co-Worker Engine: Roles and Delegation
---
# ACE Co-Worker Engine: Roles and Delegation

ACE (the **Autonomous Collaborative Engine**) is ICDEV's agentic team system. Instead of one monolithic agent, ACE fields a *team* of specialized co-workers, each with a defined role, a step list and the message-bus topics it listens and emits on.

## Roles are YAML files

Every role is a YAML file under `args/ace/roles/` (91 of them on `main`). Six you will meet throughout this track:

| Role id | What it does | Example steps |
|---------|--------------|---------------|
| `ai_developer` | Writes, tests and refactors code (TDD / ANVIL) | `analyze_requirements`, `write_tests`, `implement_code` |
| `agent_developer` | Runs an agentic LLM tool loop until the task is verified done | (agent loop, no fixed steps) |
| `security_analyst` | Threat surface, vulnerability review, control mapping | `analyze_threat_surface`, `review_vulnerabilities`, `map_controls` |
| `data_analyst` | Collects, cleans and analyses data sources | `collect_data_sources`, `run_analysis`, `validate_insights` |
| `devops_engineer` | Infrastructure, CI/CD, deployment | `provision_infrastructure`, `configure_pipeline`, `run_deploy` |
| `compliance_manager` | Framework review, gap assessment, evidence | `review_frameworks`, `assess_gaps`, `collect_evidence` |

The live catalog is at `/coworker/roles`, or from a shell: `python -m icdev.tools.ace.controller --list-roles`.

## The delegation model

```
Problem text ──► Team assembly ──► Co-workers run ──► HITL gate (if tripped) ──► Artifacts
```

1. A **problem** arrives (dashboard, chat, kanban or API: `POST /api/ace/launch`)
2. ACE's problem classifier picks a **team** of roles, or you name the roles yourself with `role_ids`
3. Each co-worker runs its **step loop** on a shared `ThreadPoolExecutor`, so the team works concurrently
4. Some situations trip a **HITL gate**: the co-worker pauses in state `hitl_pending` until a human resolves it
5. Results land as **artifacts** on the instance

## Your task

Open `args/ace/roles/ai_developer.yaml` (in the repo, or browse `/coworker/roles`) and find: which `listen_topics` it subscribes to under `communication`, which `steps` it executes, and its `trust_tier` (the tier scopes which tools the co-worker may call). Hold those answers for the next steps.
