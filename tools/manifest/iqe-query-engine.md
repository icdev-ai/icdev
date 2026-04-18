# IQE — Internal Query Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IQE — Internal Query Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AST Nodes | tools/iqe/ast_nodes.py | Dataclasses for IQE query AST: ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal, CollectionCall | — (imported) | Typed AST node instances |
| Parser | tools/iqe/parser.py | Zero-dependency recursive-descent parser; tokenizes IQE query strings and returns a ForeachNode AST root | query_str: str | ForeachNode |
| Executor | tools/iqe/executor.py | Dispatches ForeachNode AST to registered collection adapters or SQLite conn; filters and projects rows | ForeachNode, conn | list[dict] |
