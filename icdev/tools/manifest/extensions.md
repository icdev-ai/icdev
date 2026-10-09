# Extensions (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Extensions (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Workflow Loop Chat Hook | tools/extensions/builtins/030_workflow_loop_chat.py | Chat hook: workflow loop status advisory | (hook) | Advisory message |
| Graph Execution Chat Hook | tools/extensions/builtins/031_graph_execution_chat.py | Chat hook: Studio graph run node/barrier/gate status | (hook) | Advisory message |
| Bayesian Learning Chat Hook | tools/extensions/builtins/040_bayesian_learning_chat.py | Chat hook: Bayesian teaching integration | (hook) | Learning context |
| RAG Context Chat Hook | tools/extensions/builtins/050_rag_context_chat.py | Chat hook: RAG context injection | (hook) | Injected context |
| Code Quality Chat Hook | tools/extensions/builtins/060_code_quality_chat.py | Chat hook: code quality advisory | (hook) | Quality advisory |
| Genesis Status Chat Hook | tools/extensions/builtins/070_genesis_status_chat.py | Chat hook: Genesis daemon status | (hook) | Status message |
| Intake Enrichment Chat Hook | tools/extensions/builtins/080_intake_enrichment_chat.py | Chat hook: intake session enrichment | (hook) | Enrichment context |

| Extension Point Liveness | tools/extensions/liveness.py | Per declared ExtensionPoint: does anything dispatch it, and is any handler registered? Reports the points that cannot fire; `--gate` fails on a dead point not enumerated in args/extension_liveness.yaml (hcx-live-03) | `--json`, `--dead`, `--gate`, `--root <path>` | Liveness report + census verdict |
