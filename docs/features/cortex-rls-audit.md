# CUI // SP-CTI

# Cortex Tenant-Context / RLS Threading Audit (ctx-expose-03)

**Status:** complete — zero unresolved Cortex-owned gaps.
**Task:** ctx-expose-03 (chore) — systematic dry-run audit that `tenant_id` and
`classification` flow correctly from every entry point through every backend.
RLS must be **designed-in, not retrofitted** (repo memory: retroactive RLS is a
recurring failure — [[rls-consideration-in-code-generation]]).

**PostgreSQL is THE backend for tenant isolation.** RLS predicates,
`classifications_dominated_by()` read-down, and native `FORCE ROW LEVEL
SECURITY` policies only exist on PG. SQLite proves nothing here, so the
acceptance proof (`tools/cortex/db/verify_tenant_isolation.py`) runs against the
live PG instance and **skips cleanly** when PG is unreachable — never a silent
pass.

---

## 1. The isolation control being audited

Every tenant-scoped read Cortex performs funnels through **one** primitive:
`StorageConnection.set_security_context(SecurityContext)` on the
`tools/db/storage.py` connection. When a context is attached, `StorageCursor.
_inject_rls()` rewrites each read as:

```sql
... WHERE tenant_id = <ctx.tenant_id>
        AND classification IN (<classifications_dominated_by(ctx.classification)>)
```

- **Tenant scope** — `tenant_id = ?` isolates one tenant's rows from another's.
- **Classification read-down (Bell-LaPadula)** — the predicate uses
  `classification IN (<dominated set>)`, **not** `classification = ?`. The
  dominated set is computed by
  `tools/security/security_context.py::classifications_dominated_by()` and
  contains every label the caller's clearance dominates and **nothing above
  it**. A SECRET caller reads CUI *and* SECRET; a CUI caller reads only CUI
  (read-up blocked). This is the same dominated-set `IN` semantics established
  by the sec-rls work ([[sec-rls-classification-read-down]]) — exact-match
  wrongly hid dominated rows from a higher-clearance caller.

Cortex's job in this audit is to **thread the `CortexContext`
(`tenant_id`/`classification`/`user_id`) into that primitive at every point
where Cortex opens or borrows a connection**, and to carry the same identity
into audit/provenance rows. Cortex does not re-implement the predicate — it
feeds the shared injector.

`CortexContext.tenant_id == ""` maps to `None` (unscoped) via
`analyst._build_security_context()` so the injector never filters on the literal
empty string.

---

## 2. Entry-point reality

The task names four nominal entry points (Python API, chat, REST, MCP). Their
**current** state in the codebase:

| Entry point | Cortex surface today | Notes |
|-------------|----------------------|-------|
| **Python API** | `tools.cortex.{api,analyst,search_service}` | **LIVE.** The only exposed Cortex surface. Callers pass a `CortexContext`. |
| **Chat** | *not built* | No chat route imports `tools.cortex.*`. When built, it MUST build the `CortexContext` from the authenticated session (see §5) and pass it into the facade — identical threading contract. |
| **REST** | *not built* | No `tools/cortex/blueprint.py` exists. Same contract when added. |
| **MCP** | *not built* | No `cortex_server.py` / registered `cortex_*` MCP tool. Same contract when added. |

The four nominal entry points therefore **collapse to the Python API today**.
Every path that leaves Cortex to read tenant-scoped rows goes through
`analyst.ask()` (IQE or NLQ), so proving the analyst paths proves the isolation
contract for the entire exposed surface. Chat/REST/MCP are documented here as a
**forward contract** so the threading is designed-in when those surfaces land,
not retrofitted.

---

## 3. Audit matrix — entry point × backend

Legend: **✓** threaded/honored · **n/a** backend does not read tenant rows ·
**⚠ known limitation** (backend-internal, documented — not silently fixed
cross-epic) · **⛔ not built**.

### 3a. Python API (live surface)

| Cortex call | Backend | Tenant threaded? | Classification read-down? | Notes |
|-------------|---------|------------------|---------------------------|-------|
| `analyst.ask` (IQE) | `get_connection()` via `_open_connection` → `_apply_security_context` | ✓ | ✓ (`IN` dominated set) | Connection opened by the analyst, context applied before `execute_query()`. Proven by `verify_tenant_isolation.py::analyst_iqe`. |
| `analyst.ask` (NLQ fallback) | `_execute_nlq_readonly` → `_apply_security_context` | ✓ | ✓ (`IN` dominated set) | **Gap found + fixed (see §4).** Was routed through the dashboard's context-free `execute_safely`. Proven by `verify_tenant_isolation.py::analyst_nlq`. |
| `search.search` → `search_rag` | `RAGRetriever(tenant_id=ctx.tenant_id)` | ✓ | ⚠ partial | Tenant filter passed into the retriever's vector-store query. Classification carried on the citation; read-down enforcement is the RAG backend's own vector filter, not the SQL injector. |
| `search.search` → `search_dic` | `DICSearchEngine(tenant_id=…)`, `clearance=ctx.classification` | ✓ | ✓ | DIC applies clearance-aware ranking and drops above-clearance docs before the cap; document classification preserved as `Citation.clearance_required`. |
| `search.search` → `search_graph` | `graph_rag.retrieve()` | ⚠ known limitation | ⚠ known limitation | `graph_rag` is **sqlite3-backed legacy** with no tenant/classification columns. Wrapped **read-only** behind the adapter. Documented limitation (§6), not silently fixed — PG-primary migration of the KG is out of scope for a Cortex-owned chore. |
| `search.search` → `search_kb` | `search_knowledge` keyword KB | n/a | n/a | Global first-party pattern library (`knowledge_patterns`); not tenant-scoped data. No tenant leakage surface. |
| `api.complete` / `classify` / `extract` | `LLMRouter.invoke` | ✓ | ✓ (as policy input) | `_build_request` threads `tenant_id` + `classification` into `LLMRequest`; the router/gateway use them for RLS-scoped retrieval, redaction impact-level, and cost/rate attribution. No direct DB read. |
| `search.rewrite_query` (CRAG) | `LLMRouter.invoke` | ✓ | ✓ (as policy input) | Same `_build_request` threading as the facade calls. |
| `governance` audit + provenance | `_gate_record_audit`, `register_citation` | ✓ | ✓ | Every audit row carries `tenant_id`, `user_id`, `classification`; provenance row sets `project_id=ctx.tenant_id`, `classification=ctx.classification`. |

### 3b. Chat / REST / MCP (forward contract)

| Entry point | All backends | Status |
|-------------|--------------|--------|
| Chat | (via `analyst`/`search`/`api`) | ⛔ not built — MUST derive `CortexContext` from the authenticated session and thread it (§5). |
| REST | (via `analyst`/`search`/`api`) | ⛔ not built — same contract. |
| MCP | (via `analyst`/`search`/`api`) | ⛔ not built — MCP param → `CortexContext` mapping MUST set `tenant_id`/`classification` from the bound caller identity, never from tool params supplied by the model. |

---

## 4. Gap found and fixed (Cortex-owned)

**Gap:** `analyst._ask_nlq()` executed generated SQL through the dashboard's
`nlq_processor.execute_safely()`, which opens its **own** connection with **no
security context**. On the NLQ fallback the `tenant_id` + classification
read-down that the IQE path applies via `_apply_security_context` was silently
lost — tenant A's question could read tenant B's rows.

**Fix:** new `analyst._execute_nlq_readonly(sql, ctx)` mirrors `execute_safely`'s
read-only row-cap contract (`MAX_ROWS`, decision D34) but threads the
`CortexContext` into the connection via `_apply_security_context` **before**
executing — exactly like the IQE path. PG session vars + app-level predicate
injection then filter the result set. The SQLite-only `busy_timeout` PRAGMA is
guarded off on the PG backend (it would abort the transaction).

**Tests:**
- `tests/cortex/test_tenant_isolation.py` — SQLite-runnable regression that the
  seam threads the caller's `tenant_id`/`classification`/`user_id` into the
  connection and returns the `execute_safely` row shape; PG guard skips the
  PRAGMA.
- `verify_tenant_isolation.py::analyst_nlq` — live-PG proof that tenant A's NLQ
  answer contains only tenant A rows.
- The existing analyst NLQ suites (`test_analyst_fallback`,
  `test_analyst_citations`, `test_analyst_e2e`) were updated to stub the new
  execution seam (`_execute_nlq_readonly`) instead of `execute_safely`.

No other Cortex-owned gaps were found: the facade (`api.py`), the search
adapters' tenant threading, and the governance audit/provenance rows already
carry the context.

---

## 5. Forward contract for chat / REST / MCP

When any of these surfaces is built, it MUST:

1. Build the `CortexContext` from the **authenticated caller identity** (session
   principal / bound MCP user), never from request/tool parameters the caller
   can spoof. `tenant_id` and `classification` are load-bearing security
   inputs.
2. Pass that context into every `tools.cortex.*` call. The threading downstream
   is already proven; the surface's only job is to populate the context
   correctly.
3. For MCP specifically: map the bound caller identity to `tenant_id`/
   `classification`; do **not** accept them as model-supplied tool arguments.

This keeps RLS designed-in for the not-yet-built surfaces rather than
retrofitted after a leak.

---

## 6. Known limitations (backend-internal, not Cortex-owned)

- **`graph` backend (`knowledge_graph/graph_rag.py`) is sqlite3-backed legacy**
  with no `tenant_id`/`classification` columns. It is wrapped read-only behind
  `search_graph`; results are entity/topology facts, not tenant document rows.
  Making the KG tenant-aware requires a PG-primary migration of the graph store
  — out of scope for this Cortex-owned chore and **not** silently patched here.
  Tracked as a cross-epic follow-up.
- **`rag` classification read-down** is enforced by the RAG retriever's own
  vector-store filter, not the SQL predicate injector. Cortex threads
  `tenant_id` and carries `classification` on the citation; hardening RAG's
  clearance filter to full dominated-set semantics is a RAG-owned concern.
- **`kb` backend** is a global first-party pattern library, intentionally not
  tenant-scoped; there is no tenant row to leak.

---

## 7. Acceptance evidence

Run from the repo root against the live PG instance:

```bash
ICDEV_STORAGE_BACKEND=postgresql \
    python tools/cortex/db/verify_tenant_isolation.py --json
# or, via pytest:
CORTEX_ISOLATION_PG=1 pytest tests/cortex/test_tenant_isolation.py -q
```

Live PG result (all checks passed):

```json
{
  "status": "passed",
  "checks": [
    {"name": "connection",   "passed": true,
     "tenant_a_saw": ["ctx-expose-03-tenant-a"],
     "tenant_b_saw": ["ctx-expose-03-tenant-b"],
     "cui_caller_classifications":    ["CUI"],
     "secret_caller_classifications": ["CUI", "SECRET"]},
    {"name": "analyst_iqe",  "passed": true,
     "tenant_a_saw": ["ctx-expose-03-tenant-a"],
     "tenant_b_saw": ["ctx-expose-03-tenant-b"]},
    {"name": "analyst_nlq",  "passed": true,
     "tenant_a_saw": ["ctx-expose-03-tenant-a"],
     "tenant_b_saw": ["ctx-expose-03-tenant-b"]}
  ]
}
```

This proves, on PostgreSQL, that:
- tenant A cannot retrieve tenant B's rows through the `connection` primitive,
  the analyst **IQE** entry point, or the analyst **NLQ** entry point;
- a CUI caller cannot read a SECRET row (read-up blocked);
- a SECRET caller reads both CUI and SECRET rows (read-down via dominated-set
  `IN`).

`pytest tests/cortex/test_tenant_isolation.py -q` alone (SQLite-forced conftest)
**skips** the PG proof and is **not** sufficient acceptance — the PG run above
is the acceptance evidence.
