---
ontology_id: icdev:mission:m04-first-agent:step:1
step_class: icdev:Lesson
---

# The Agent Loop

An agent is not a prompt. An agent is a **control loop** — a process that runs continuously, observes its environment, decides what to do, takes actions via tools, and uses the results to decide what to do next.

```
   ┌─────────────────────────────────────────┐
   │              AGENT LOOP                 │
   │                                         │
   │  Observe → Think → Act → Observe → ...  │
   │                                         │
   │  ┌──────────┐    ┌──────────────────┐   │
   │  │  LLM     │───▶│  Tool Selection  │   │
   │  │ (decide) │    │  (what to call?) │   │
   │  └──────────┘    └──────────────────┘   │
   │       ▲                   │             │
   │       │                   ▼             │
   │  ┌──────────────────────────────────┐   │
   │  │     Tool Execution + Result      │   │
   │  │  search() | calculate() | run()  │   │
   │  └──────────────────────────────────┘   │
   └─────────────────────────────────────────┘
```

## The minimal agent

```python
def run_agent(task: str, tools: dict, max_steps: int = 5) -> str:
    messages = [{"role": "user", "content": task}]
    
    for step in range(max_steps):
        # 1. Think: ask the LLM what to do
        response = llm.chat(messages=messages)
        
        # 2. Check: did the LLM call a tool?
        if response.tool_call:
            # 3. Act: execute the tool
            result = tools[response.tool_call.name](**response.tool_call.args)
            # 4. Observe: feed the result back
            messages.append({"role": "tool", "content": str(result)})
        else:
            # LLM said it's done — return the final answer
            return response.content
    
    return "Max steps reached"
```

## Why loops beat pipelines

A pipeline is a DAG — fixed steps, fixed order. An agent dynamically decides the next step based on intermediate results. If a tool fails, it can retry. If a result is incomplete, it can dig deeper. If the task changes, it adapts.

In ICDEV, agents handle compliance scanning, proposal generation, and monitoring — tasks that require iterative reasoning, not predetermined sequences.

## Your task

Implement a minimal agent that takes a security question, decides which tool to call, calls it, and returns the result. Tools are provided — your job is to wire the loop.
