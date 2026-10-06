---
ontology_id: icdev:mission:m-ace-03-multi-role-pipeline:step:1
step_class: icdev:design
---
# Multi-Role Co-Worker Pipelines

Real tasks need more than one co-worker. A *pipeline* is a design pattern for chaining roles so each stage's output becomes the next stage's input.

## Example: Secure Feature Pipeline

```
agent_developer ──► data_analyst ──► security_analyst ──► compliance_manager ──► HITL ──► Result
(design agent)     (data sources)   (security review)    (compliance check)
```

## How this maps onto ACE today

ACE has **no pipeline endpoint**. `POST /api/ace/launch` with `role_ids` assembles a *team*, and the team's co-workers run **concurrently** on a shared thread pool. Ordering between them comes from the message bus, not from a list:

- each role YAML declares `communication.emit_topics` and `communication.listen_topics`
- a co-worker reacts to the topics it listens on, so `security_analyst` (listening on `security.scan.completed`, `vulnerability.found`) naturally runs *after* whatever emits those topics

So when you design a pipeline you are really designing two things: **which roles** are on the team, and **which topics** hand work from one to the next. Human approval is not a per-stage flag on the API either: it comes from ACE's HITL gates (trust score, failed required steps, behavioral compliance checks).

A design document for a pipeline is still worth writing as a list of stages, because it is the specification a reviewer signs off on. The next step has you write exactly that, as data.

## Reflect

Design a 3-stage pipeline to (1) build a monitoring agent, (2) add observability hooks, (3) compliance-check the observability data flows. For each stage name the role, the task, and the topic you would expect it to emit for the next stage. Which stage would you insist a human approves before the result ships, and which ACE gate would actually enforce that?
