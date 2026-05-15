---
ontology_id: icdev:mission:m07-multi-agent:step:1
step_class: icdev:Lesson
---

# Multi-Agent Coordination

A single agent hits the wall fast. Context limits. Latency. One skill set. Multi-agent systems break a complex task into parallel subproblems — each agent specializes, and an orchestrator synthesizes the results.

## Architectures

### Swarm (peer-to-peer handoff)
Agents hand tasks to each other based on specialization. No central coordinator. Agent A processes a document, decides it needs compliance analysis, hands off to Agent B. Agent B spots a legal issue and routes to Agent C.

```
User → Router Agent
         ├──▶ STIG Scanner Agent → findings
         ├──▶ RAG Search Agent   → context
         └──▶ Report Agent       → synthesizes all → final output
```

### Orchestrator-Worker
A supervisor agent decomposes the task, dispatches subtasks to workers in parallel, collects results, and produces the final answer. Workers are stateless — they do one job and return.

```
Orchestrator
  ├── dispatch(worker_a, subtask_1) ──▶ result_1
  ├── dispatch(worker_b, subtask_2) ──▶ result_2
  └── synthesize(result_1, result_2) ──▶ final
```

### Pipeline (sequential)
Each agent's output is the next agent's input. Good when the task has strict ordering (collect → analyze → report). Less parallel, but easier to debug.

## When to go multi-agent

- Task requires parallel research across different knowledge domains
- Different steps need different LLM capabilities (cheap model for classification, expensive for generation)
- A single agent hits context limits mid-task
- You need fault isolation — one agent's failure shouldn't corrupt the whole pipeline

## In ICDEV

The `goagiq/sentiment-swarm-agents` pattern demonstrates peer handoff. ICDEV's proposal genesis uses orchestrator-worker: one agent per proposal section, synthesizer agent for the final document.

## Your task

Implement an orchestrator-worker pattern where one orchestrator dispatches to three specialist workers and synthesizes their results.
