# CUI // SP-CTI

# Loop vs. Graph — when to reach for which runtime

> **The one-line rule:** if you can enumerate the steps before you start, author
> a **graph**. If you cannot, run a plain **agent loop** and only add graph
> structure once a step has proven it is always there.
>
> Design rule established under HGX (hgx-doc-02). Applies to every ICDEV™
> surface that executes work on a model's behalf.

ICDEV™ ships two execution runtimes, and picking the wrong one is the most
expensive design mistake available in this repo — not because either is broken,
but because a graph forced onto exploratory work costs materially more tokens
and delivers a worse answer than the loop it replaced.

| | Agent loop | Graph (DAG) |
|---|---|---|
| Runtime | `icdev.tools.llm.agent_loop.run_agent_loop`, wrapped by `AgentRuntime` (`icdev chat`) | `tools/studio/workflow_runner.py` |
| Control flow | the model decides the next tool call, every turn | authored in YAML, `depends_on` edges, resolved by `graphlib` |
| Shape of the work | unknown until you are partway through it | known before the first step runs |
| Concurrency | one turn at a time | `max_parallel` slots, several steps (and therefore several models) at once |
| Durability | session resume (`agent_loop_sessions`) | restart-safe run resume, human gates, per-step records |
| Token cost | one context, grown incrementally | N contexts, one per concurrent node — **the multiplier is the point** |

---

## The decision tree

```
Are the steps known ahead of time?
│
├── YES — you can write them down, in order, before starting
│   │
│   ├── Do any of them need to run at the same time,
│   │   need a human approval in the middle,
│   │   or need to survive a process restart?
│   │   │
│   │   ├── YES ──────────────────────────► GRAPH
│   │   │                                   (Studio workflow, max_parallel,
│   │   │                                    node_type: human, resume_run)
│   │   │
│   │   └── NO  ──────────────────────────► GRAPH, max_parallel unset
│   │                                       (still the right home: the steps
│   │                                        are data, not a prompt)
│   │
└── NO — the work is exploratory; step 3 depends on what step 2 finds
    │
    ├── Start with a plain AGENT LOOP.
    │
    └── Later, when ONE stage has proven it always happens, in the same
        place, with the same inputs — lift THAT stage into a graph node
        (`node_type: agent`) and leave the rest of the work in the loop.
```

### Read the branches out loud

**"Known ahead of time" means you could write the YAML now.** Not "I know
roughly what has to happen" — that is every task. A SBOM generation, a FedRAMP
evidence sweep, a three-COA comparison that always produces three COAs: those
are graphs. The step list does not change based on what step 1 returns.

**"Open-ended" means the plan is an output, not an input.** Debugging a failure,
auditing a subsystem nobody has read recently, answering "why is this slow" —
the second step is chosen by what the first one found. Authoring that as a graph
means authoring every branch you can imagine, and the value of the work is
mostly in the branches you could not.

**Structure has to earn its place.** The migration path is one-way and
incremental: loop first, then promote the parts that stopped surprising you.
Going the other direction — starting with a graph and loosening it — means you
already paid the authoring cost for edges that turned out to be wrong.

**Never force a graph onto exploratory work.** The failure is not that it errors;
it is that it succeeds while producing a worse answer. A graph node cannot
choose to look somewhere the author did not name, so the DAG quietly converts
"find out what is wrong" into "confirm the author's hypothesis". You get a
confident, well-structured, wrong report.

---

## Why a graph costs materially more

A graph with `max_parallel: 4` is not one agent doing four things. It is **four
concurrent contexts**, each carrying its own system prompt, its own project
context block, its own tool schemas, and its own transcript. The token cost is
roughly the fan-out multiplied by the per-node context floor, and the per-node
floor is not small — `project_context.py` alone budgets up to 25% of the
available input window before the task text is added.

That is a real cost to buy, and it buys real things: wall-clock (four branches
finish in the time of the slowest, not the sum), isolation (a poisoned context in
one branch does not contaminate the others), and diversity (four reviewers with
different lenses see different defects). Buy it when you want those. Do not buy
it because a diagram looks tidier than a conversation.

Two corollaries that follow directly:

- **Concurrency is opt-in and stays opt-in.** `max_parallel` defaults to `1`,
  which is why all 61 shipped templates run byte-for-byte sequentially. Raising
  it is a deliberate spend, per template.
- **Fan-out without a join wastes the spend.** If four branches produce four
  reports nobody merges, you paid 4× for a list. A fan-out earns its cost only
  when something downstream reads all of it — a synthesis node, a vote, a dedup.

---

## Hybrid: the case that is actually most common

The two runtimes are not rivals; the graph can *contain* loops. `node_type:
agent` (hgx-agent-01) runs a bounded agent loop as a single node, with its tools
narrowed to declared `agent_tools` bundles and re-checked per call by the
`AGENT-WF-001` gate.

That is the shape most real work wants:

- **The skeleton is known** — gather, analyze, review, report. Author it as a
  graph.
- **One stage is not** — "review this diff for defects" has no enumerable step
  list. Make that stage a `node_type: agent` node and let the model drive inside
  it.

The graph supplies determinism, durability, approval gates and audit at the
seams; the loop supplies judgment inside a node. Neither is asked to do the
other's job.

---

## Escape hatches inside a graph (before you conclude you need a loop)

A graph is more expressive than "a fixed line of steps". If the only reason you
are reaching for a loop is branching, check these first:

- **`when:`** (hgx-cond-01) — a conditional edge. A step declaring `when:` runs
  only if its condition holds against the predecessor's recorded result
  (`status`, `exit_code`, `output.<path>` from parsed stdout, or
  `steps.<id>.<field>` to address one branch of a join). This covers
  "run the remediation step only if the scan failed" without a loop.
- **`node_type: human`** — park the run for a decision instead of guessing at
  one. The run survives the wait; `resume_run()` re-attaches.
- **`max_parallel`** — a fan-out of independent analyses is still a graph, not
  an open-ended task.

If none of those fit because you genuinely do not know what comes next: that is
the loop's case. Take it.

---

## Anti-patterns

| Smell | What it actually means |
|---|---|
| The YAML has a step named `investigate` or `figure_out` | That step is a loop. Make it `node_type: agent`, or move the whole thing to a loop. |
| Every edge carries a `when:` and half of them are unreachable | The step list was not known ahead of time. You authored a decision tree the model should have made. |
| `max_parallel` raised so the run "feels faster", branches never joined | Paying N× tokens for a list. Add the synthesis node or drop the fan-out. |
| A loop re-derives the same five steps every session | Those five steps are known ahead of time. Promote them to a graph. |
| A graph node's prompt says "decide which of these to do next" | The graph is asking the model to be the scheduler it already is. |

---

## Where this rule is enforced and referenced

- **Manifest:** `tools/manifest/standalone-agent-runtime.md` (loop side) and
  `tools/manifest/icdev-studio-low-code-no-code-platform.md` (graph side) both
  link here.
- **Linter:** `python tools/studio/template_linter.py` validates `node_type`
  against `VALID_NODE_TYPES` (`tool`, `human`, `approval`, `mcp`, `agent`) and
  lints `when:` / `agent` steps — it checks that a graph is *well-formed*, not
  that a graph was the right choice. That judgment is this document's.
- **Loop runtime:** `python -m tools.agent_runtime.runtime`, or `icdev chat`.

## Related

- [docs/features/dwo-durable-workflow-orchestration.md](../features/dwo-durable-workflow-orchestration.md) — parallel dispatch (hgx-par-01), conditional edges (hgx-cond-01)
- [docs/features/phase-hgx-agent-01-studio-agent-node.md](../features/phase-hgx-agent-01-studio-agent-node.md) — `node_type: agent`
- [docs/features/phase-hgx-agent-02-agent-tool-gate.md](../features/phase-hgx-agent-02-agent-tool-gate.md) — per-node tool authorization (AGENT-WF-001)
- [docs/features/phase-sag-standalone-agent.md](../features/phase-sag-standalone-agent.md) — the agent loop runtime
