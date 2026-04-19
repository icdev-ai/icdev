# IQE — Internal Query Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IQE — Internal Query Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AST Nodes | tools/iqe/ast_nodes.py | Dataclasses for IQE query AST: ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal, CollectionCall | — (imported) | Typed AST node instances |
| Parser | tools/iqe/parser.py | Zero-dependency recursive-descent parser; tokenizes IQE query strings and returns a ForeachNode AST root | query_str: str | ForeachNode |
| Executor | tools/iqe/executor.py | Dispatches ForeachNode AST to registered collection adapters or SQLite conn; filters and projects rows | ForeachNode, conn | list[dict] |
| Data Adapter | tools/iqe/adapters/data.py | IQE data collection adapters — registers `data.lineage.edges` and related data-canvas collections on the Executor for cross-canvas lineage queries | (import to register) | Registered adapters |
| Pipeline Adapter | tools/iqe/adapters/pipeline.py | IQE pipeline collection adapters — registers `pipeline.snapshots` and related PDC collections on the Executor for pipeline DAG queries | (import to register) | Registered adapters |
| Security Adapter | tools/iqe/adapters/security.py | IQE security collection adapters — registers `attack.nodes` and related SDC collections on the Executor for attack-graph and STIG queries | (import to register) | Registered adapters |
| Observability Adapter | tools/iqe/adapters/observability.py | IQE observability collection adapters — registers `mitre.techniques`, `mitre.coverage`, `mitre.gaps` on the Executor for ODC MITRE ATT&CK coverage queries; filters per design_id | (import to register) | Registered adapters |
| IQE CLI Runner | tools/iqe/run.py | CLI executor — parse .iqe files or inline query strings and emit JSON arrays or human-readable tables; exit codes 0/1/2 for success/parse-error/exec-error | --query <file>, --query-string <str>, --json, --human | JSON rows or table output |
