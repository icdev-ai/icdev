---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:1
step_class: icdev:Lesson
---
# ACE Co-Worker Engine: The 6 Roles

The ACE (ANVIL Co-Worker Engine) is ICDEV's agentic team system. Instead of one monolithic agent, ACE fields a *team* of specialized co-workers — each with a defined role, skill set, and communication channel.

## The 6 ACE Roles

| Role | Specialty | When to use |
|------|-----------|-------------|
| `ai_developer` | LLM code generation, prompt tuning | Build features using AI |
| `agent_developer` | Agent loop design, tool wiring | Build agent systems |
| `security_analyst` | STIG triage, threat modeling, vulnerability scan | Secure a system |
| `data_engineer` | RAG pipelines, embeddings, data quality | Data ingestion + retrieval |
| `devops_engineer` | CI/CD, infra-as-code, deployment | Ship and operate |
| `compliance_officer` | NIST controls, audit trails, ATO artifacts | Govern and comply |

## The Delegation Model

```
Task Card ──► Role Assignment ──► Step Loop ──► HITL Approval ──► Result
```

1. A **task card** arrives (from kanban, chat, or API)
2. ACE classifies the task and assigns the best-fit **role**
3. The co-worker runs its **step loop** (ThreadPoolExecutor, parallel capable)
4. High-risk outputs trigger a **HITL gate** — human approves before the result ships
5. The approved **result** is returned to the caller

## Your task

Read the ACE role YAML for the `ai_developer` role at `icdev/tools/ace/` and identify: what `listen_topics` does it subscribe to? What `steps` does it execute? What triggers its HITL gate?
