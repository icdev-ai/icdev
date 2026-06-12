# IQE v0.1 — ICDEV Query Engine

**Phase:** iqe-v0-1
**Date:** 2026-04-18
**Author:** Sovanna Chuon
**Status:** Shipped

---

## Overview

IQE (ICDEV Query Engine) is a declarative, SQL-like domain-specific language that runs
compliance and network-health checks over ICDEV's seven design-canvas databases.  It is
the shared query layer that lets every canvas twin express its violation checks in a
human-readable, version-controlled, review-gated format instead of ad-hoc Python.

The design is modeled after Forward Networks' NQE (Network Query Engine): a
`foreach / where / select` grammar that reads like English, compiles to a typed AST,
and dispatches to either registered in-process adapters or a direct SQLite / PostgreSQL
`SELECT *` fallback.

---

## Motivation

ICDEV's seven canvases — Network (NDC), Security (SDC), Pipeline (PDC), Boundary/ATO
(BDC), Data (DDC), Observability (ODC), and Infrastructure (IDC) — each need to run
continuous violation checks against their own databases.  Without a shared abstraction,
every canvas re-implements the same filter/project loop in bespoke Python, query logic
is scattered across dozens of files, and the Engineering Review Board has no
single surface to gate on.

IQE solves this with three components:

1. **Grammar + parser** — a hand-rolled recursive-descent tokenizer/parser with zero
   external dependencies, producing a typed AST.
2. **Executor** — an adapter-dispatching engine that runs the AST against any registered
   collection or falls back to a safe-SQL `SELECT *` over SQLite/PostgreSQL.
3. **Seed query library** — a version-controlled directory of `.iqe` files that encode
   the community-authored checks across all seven canvases.

---

## Grammar Specification

```
query      := "foreach" var "in" collection where_clause* "select" select_expr
collection := IDENT ("." IDENT)*
var        := IDENT
where_clause := "where" predicate
predicate  := or_expr
or_expr    := and_expr ("or" and_expr)*
and_expr   := not_expr ("and" not_expr)*
not_expr   := "not" not_expr | comparison
comparison := attr_ref op value
             | attr_ref "contains" value
             | attr_ref "startswith" value
op         := "==" | "!=" | ">" | "<" | ">=" | "<="
attr_ref   := IDENT ("." IDENT)*
value      := STRING | NUMBER | "true" | "false" | "null" | attr_ref
select_expr := "*" | attr_ref ("," attr_ref)*
```

### Keywords

`foreach`, `in`, `where`, `select`, `and`, `or`, `not`, `contains`, `startswith`,
`true`, `false`, `null`

### Example queries

```iqe
# Vendor inventory — all devices, no filter
foreach d in network.devices
  select d.hostname, d.vendor, d.model, d.os_version
```

```iqe
# BGP flap detector
foreach p in network.bgp_peers
  where p.session_state != "established"
  select p.device, p.peer_ip, p.local_as, p.remote_as, p.session_state
```

```iqe
# Admin-up / oper-down mismatch (potential hardware failure)
foreach i in network.interfaces
  where i.admin_status == "up"
  where i.oper_status == "down"
  select i.device, i.name, i.description, i.admin_status, i.oper_status
```

```iqe
# CAT I STIG open findings
foreach f in network.findings
  where f.severity == "CAT1"
  where f.status == "open"
  select f.device, f.rule_id, f.vuln_id, f.title, f.remediation
```

```iqe
# Capacity threshold alert — interfaces above 80 % utilization
foreach i in network.interfaces
  where i.utilization_pct > 80
  select i.device, i.name, i.speed_mbps, i.utilization_pct, i.direction
```

Multiple `where` clauses on consecutive lines are ANDed together.  Inline `and` / `or`
are also supported for compound predicates in a single `where` clause.

---

## AST Node Types

Defined in `tools/iqe/ast_nodes.py`:

| Class | Description |
|-------|-------------|
| `ForeachNode` | Root node — holds `var`, `collection`, `where_clauses`, `select` |
| `WhereNode` | One `where` predicate (a single `BinOp` or compound expression) |
| `SelectNode` | Projection — list of `AttrRef` fields or `wildcard=True` for `*` |
| `AttrRef` | Dotted attribute path: `["device", "vendor"]` → `device.vendor` |
| `BinOp` | Binary comparison or logical op (`==`, `!=`, `>`, `contains`, `and`, `or`, `not`) |
| `Literal` | Scalar value: `str`, `int`, `float`, `bool`, or `None` |

All nodes are `dataclass`es with no mutable defaults — safe to pickle and cache.

---

## Parser Implementation

`tools/iqe/parser.py` — hand-rolled recursive-descent, no ANTLR/Lark dependency.

**Tokenizer** uses a single compiled regex with named groups for each token type
(`STRING`, `NUMBER`, `OP`, `COMMA`, `DOT`, `STAR`, `LPAREN`, `RPAREN`, `IDENT`,
`NL`, `SKIP`, `MISMATCH`).  Whitespace, tabs, carriage returns, and `#`-prefixed
comments are silently skipped.  Keywords are identified by membership in a frozenset
at token-classification time.

**Parser** walks the token stream with a position cursor:
- `_expect_kw` / `_expect_type` — consume-or-raise helpers that include line/col in
  error messages.
- `_parse_or` / `_parse_and` / `_parse_not` — standard precedence chain.
- `_parse_comparison` — handles `OP` tokens and `contains` / `startswith` keywords.
- `_parse_attr_ref` — greedily consumes `IDENT.IDENT.…` sequences.
- `_parse_select` — handles `*` wildcard or a comma-separated list of `attr_ref`s.

**Error class:** `IQESyntaxError(SyntaxError)` — carries `msg`, `line`, `col`.
`__str__` formats as `"<msg> (line N, col M)"` for human-readable test output.

**Public API:**

```python
from tools.iqe.parser import parse, IQESyntaxError

ast = parse("foreach d in network.devices select d.hostname")
```

---

## Executor and Adapter Pattern

`tools/iqe/executor.py` implements the `Executor` class and module-level convenience
functions.

### Adapter registration

Any collection can be backed by a custom Python callable instead of a raw DB table:

```python
from tools.iqe.executor import register_collection

def _fetch_devices(conn):
    cursor = conn.execute("SELECT * FROM ndc_devices")
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

register_collection("network.devices", _fetch_devices)
```

When `Executor.run()` encounters a collection name that has a registered adapter, it
calls `adapter_fn(conn)` and uses the returned `list[dict]`.  If no adapter is
registered, it falls back to a direct `SELECT * FROM <table>` where the table name is
the last dotted segment of the collection path.

**SQL injection protection:** the table name extracted for the fallback is validated
against `^[A-Za-z_][A-Za-z0-9_]*$` before interpolation.  Any name that fails this
check raises `ValueError` — no query is executed.

### Execution pipeline

```
parse(query_str) → ForeachNode
  └─ Executor.run(ast, conn)
       ├─ _fetch(collection_name, conn)   # adapter or SQL fallback
       ├─ _filter(rows, var, where_clauses)  # eval all predicates
       └─ _project(rows, var, select_node)   # strip var prefix, pick fields
```

### Predicate evaluation

`_eval` dispatches on AST node type:
- `BinOp` with `op in {"and","or","not"}` → short-circuit boolean
- `BinOp` with comparison op → `_compare` (handles `contains` / `startswith`)
- `AttrRef` → resolve nested dict key path, cast to bool
- `Literal` → cast to bool

`_resolve` walks a `list[str]` path against `dict` or object via `getattr`.  Missing
keys return `None` without raising.

### Module-level convenience API

```python
from tools.iqe.executor import execute_query
from tools.iqe.parser import parse

results = execute_query(parse(query_str), conn)
```

`execute_query` / `register_collection` delegate to a module-singleton `Executor`
instance so callers don't need to instantiate one.

---

## Seed Query Library

Location: `context/iqe/queries/{canvas}/*.iqe`

Each `.iqe` file carries a header comment block with:
- **Intent** — what the query detects
- **Expected violation shape** — what a non-empty result means

### NDC seed library (v0.1 — 5 queries)

| File | Check |
|------|-------|
| `vendor_inventory.iqe` | Full device inventory; nulls flag unregistered assets |
| `bgp_peer_asymmetry.iqe` | BGP sessions not in `established` state |
| `iface_admin_oper_mismatch.iqe` | Admin-up / oper-down interface mismatch |
| `stig_check.iqe` | Open CAT I STIG findings across all devices |
| `capacity_threshold.iqe` | Interfaces above 80 % utilization |

### Query authorship rules

1. Every query file MUST have an `# Intent:` and `# Expected violation shape:` comment.
2. A fixture-based unit test MUST prove the query returns expected rows given known input.
3. New queries require Engineering Review Board approval before merging to `main`
   (`tools/eng_review_board/` — existing gate).
4. No query may issue a raw `SELECT` directly — all DB access must go through `Executor`.

---

## Testing

Three test files cover IQE end-to-end:

| File | Coverage |
|------|----------|
| `tests/test_iqe_parser.py` | 8 tests — `ForeachNode`, `WhereNode`, `BinOp`, error paths |
| `tests/test_iqe_executor.py` | Executor dispatch, adapter registration, SQL fallback, projection |
| `tests/test_iqe_seed_queries.py` | Round-trip: parse every `.iqe` seed file, assert valid AST |

Run the suite:

```bash
python -m pytest tests/test_iqe_parser.py tests/test_iqe_executor.py tests/test_iqe_seed_queries.py -v
```

---

## Non-Goals (v0.1)

- **No `GROUP BY` / aggregation** — queries return flat row lists only.
- **No cross-canvas joins** — each query operates on one collection; cross-DB joins
  are deferred to v0.2 (Python-level join in the executor).
- **No LLM-assisted query authoring** — deterministic engine ships first; AI Assist is
  a follow-on after the deterministic baseline is production-validated.
- **No persistent query store** — queries live as files; scheduled execution is wired
  through the Genesis reflex system (separate task).

---

## Future Work

| Milestone | Description |
|-----------|-------------|
| `iqe-v0-2` | Cross-canvas joins via Python-level merge; `GROUP BY` aggregation |
| `iqe-v0-3` | Batfish import adapter for NDC snapshot ingestion |
| `iqe-v1-0` | BDC compliance queries (`foreach ctrl in framework('FedRAMP Moderate').controls …`) |
| `iqe-ai-assist` | Natural-language → IQE translation using Claude (after deterministic baseline stable) |

---

## File Inventory

```
tools/iqe/
├── __init__.py
├── ast_nodes.py       # ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal
├── executor.py        # Executor class + module-level execute_query / register_collection
└── parser.py          # Tokenizer + recursive-descent parser + IQESyntaxError

context/iqe/queries/network/
├── bgp_peer_asymmetry.iqe
├── capacity_threshold.iqe
├── iface_admin_oper_mismatch.iqe
├── stig_check.iqe
└── vendor_inventory.iqe

tests/
├── test_iqe_parser.py
├── test_iqe_executor.py
└── test_iqe_seed_queries.py
```
