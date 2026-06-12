# IQE — ICDEV Query Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IQE — ICDEV Query Engine (Phase: dt-iqe)

SQL-like DSL over the 7 ICDEV canvas DBs. Grammar: `foreach <var> in <collection> [where <predicate>]* select <projection>` (Forward NQE-compatible).

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AST Nodes | tools/iqe/ast_nodes.py | Dataclasses: ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal | (library) | Typed AST nodes |
| Grammar | tools/iqe/grammar.lark | Lark LALR grammar for IQE DSL; and/or predicate precedence | (Lark grammar file) | Parse tree |
| Parser | tools/iqe/parser.py | `parse(query_str) → ForeachNode`; raises `IQESyntaxError(line, col)` on invalid input | query string | ForeachNode AST |
| AST (typed nodes) | tools/iqe/ast.py | Alternative typed AST node definitions (QueryNode, TermNode, etc.) used by the IQE intent query tree — separate from ast_nodes.py dataclasses | (library) | IQENode instances |
| Cache Savings Adapter | tools/iqe/adapters/cache_savings.py | Registers `cache.stats` (per-function hit rate, avoided calls, cost saved) and `cache.entries` (raw llm_response_cache rows) as IQE collections | (auto-registered) | list[dict] |
