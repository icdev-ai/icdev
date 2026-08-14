# IQE — Internal Query Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IQE — Internal Query Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AST Nodes | tools/iqe/ast_nodes.py | Dataclasses for IQE query AST: ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal, CollectionCall | — (imported) | Typed AST node instances |
| Parser | tools/iqe/parser.py | Zero-dependency recursive-descent parser; tokenizes IQE query strings and returns a ForeachNode AST root | query_str: str | ForeachNode |
| Executor | tools/iqe/executor.py | Dispatches ForeachNode AST to registered collection adapters or SQLite conn; filters and projects rows | ForeachNode, conn | list[dict] |
| Executor (completeness) | tools/iqe/executor.py | `execute_query_with_meta()` returns rows PLUS why they may be incomplete (adapter row cap, failed union sub-fetch). Adapters report a cap by returning `capped_rows(rows, collection, limit)`; a plain list means complete. Callers that show the answer to a human or count it must use this, not `execute_query` — where clauses are applied in Python AFTER the fetch, so a capped scan is a lower bound (ctx-trust-04) | ForeachNode, conn | ExecutionResult(rows, incomplete) |
| Data Adapter | tools/iqe/adapters/data.py | IQE data collection adapters — registers `data.lineage.edges` and related data-canvas collections on the Executor for cross-canvas lineage queries | (import to register) | Registered adapters |
| Pipeline Adapter | tools/iqe/adapters/pipeline.py | IQE pipeline collection adapters — registers `pipeline.snapshots` and related PDC collections on the Executor for pipeline DAG queries | (import to register) | Registered adapters |
| Security Adapter | tools/iqe/adapters/security.py | IQE security collection adapters — registers `attack.nodes` and related SDC collections on the Executor for attack-graph and STIG queries | (import to register) | Registered adapters |
| Observability Adapter | tools/iqe/adapters/observability.py | IQE observability collection adapters — registers `mitre.techniques`, `mitre.coverage`, `mitre.gaps` on the Executor for ODC MITRE ATT&CK coverage queries; filters per design_id | (import to register) | Registered adapters |
| IQE CLI Runner | tools/iqe/run.py | CLI executor — parse .iqe files or inline query strings and emit JSON arrays or human-readable tables; exit codes 0/1/2 for success/parse-error/exec-error | --query <file>, --query-string <str>, --json, --human | JSON rows or table output |
| SIPA IQE Adapter | tools/iqe/adapters/integrity.py | Registers 4 read-only IQE collections over the integrity_* tables: `integrity.assessments` (staged sources + verdict/status), `integrity.capabilities` (detected capability manifest), `integrity.findings` (scanner findings), `integrity.verdicts` (recorded dispositions). Opens RLS-aware get_connection(); degrades to empty list when migration 179 is not yet applied. | (import to register) | Registered adapters |
| Logs IQE Adapter | tools/iqe/adapters/logs.py | Registers one read-only IQE collection `logs.entries` over the append-only `centralized_logs` table (migration 181). Newest-first, RLS-filtered by tenant+classification, degrades gracefully when schema is absent. | (import to register) | Registered adapters |
| Co-Worker Engine IQE Adapter (planned) | tools/iqe/adapters/cwk.py | (planned) IQE collection adapters for the ACE Co-Worker Engine (cwk) canvas tables. To be created alongside the ACE canvas IQE wiring task. | (import to register) | Registered adapters |
