---
ontology_id: icdev:mission:m-iqe-01-collections-adapters:step:1
step_class: icdev:Lab
---

# IQE — the In-App Query Engine

Every canvas in ICDEV has a **mini query bar**: type a plain-language question and get an
answer scoped to the data you're looking at. The engine behind it is **IQE**, and it is
built from three parts.

## Collections + adapters

- A **collection** is a named data source bound to an **adapter function**
  `adapter_fn(conn) -> list[dict]` (e.g. the Network canvas exposes its nodes and links;
  Kanban exposes its tasks). IQE also has built-in `union(...)` / `join(...)` pseudo-collections.
- An **adapter** (`tools/iqe/adapters/<canvas>.py`) is the module that **registers** a
  canvas's collections by calling `register_collection(name, adapter_fn)` at import time
  (see `tools/iqe/adapters/cortex.py`, which registers `cortex.chat_sessions` etc., or
  `tools/iqe/adapters/core_kanban.py`, which registers `kanban.tasks` and `kanban.epics`).
  Adding IQE to a canvas means writing an adapter, not editing the engine.
- **Seed queries** live in `context/iqe/queries/<canvas>/` — example questions that
  teach the engine (and the user) what a canvas can answer.

## How a query reaches the right canvas

1. The mini-bar picks the canvas by regex-matching the current URL path against
   **`window.__ICDEV_PATH_CANVAS__`** — a list of `[regex, canvas_key]` pairs that
   `tools/dashboard/templates/base.html` injects from the component registry
   (`ComponentRegistry.get_iqe_path_canvas`, first match wins).
2. It posts the `question` and `canvas` to **`POST /api/iqe/dispatch`** (also bound at
   `/api/iqe-query`) — the `iqe_dispatch()` view in `tools/dashboard/app.py`.
3. `iqe_dispatch()` looks the canvas up in **`_IQE_CANVAS_MAP`** (`app.py`), which maps a
   canvas key to `(adapter_module, [collections])`, imports the adapter module (which
   registers its collections), then runs `nl_to_iqe(question, collections)` → `parse()`
   → `execute_query()` and returns the rows.

Wiring IQE into a new canvas is a fixed checklist: an adapter, the IQE query route, the
query widget in the template, an IQE entry in the canvas's `args/component_registry.yaml`
block (which feeds both `_IQE_CANVAS_MAP` and the path map), and at least three seed queries.

## What you'll build

A miniature IQE, with the stdlib only:

1. `IQERegistry.register()` / `names()` — hold named collections (adapters may register
   in batches, so re-registering a name **extends** it).
2. `register_kanban_collections()` — an **adapter** that registers a `tasks` collection.
3. `canvas_for_path()` — resolve the canvas from a URL path via a `PATH_CANVAS` table
   (simplified here to path *prefixes*; the real map uses regexes).
4. `iqe_query()` — dispatch a filtered query against a collection.

Open `step1_starter.py` and implement the `TODO`s.
