
"""
Tier 2 — IQE: the In-App Query Engine
Goal: Register a canvas's data as IQE *collections* through an *adapter*, resolve the
      canvas from a URL path (the mini-bar wiring), and dispatch a filtered query.

IQE lets a user ask any canvas a question from the mini query bar. Each canvas ships an
adapter under tools/iqe/adapters/<canvas>.py that calls register_collection(name,
adapter_fn) — binding a named *collection* to an adapter_fn(conn) -> list[dict]. Seed
queries live in context/iqe/queries/<canvas>/. The dashboard routes a query with
POST /api/iqe/dispatch (iqe_dispatch in app.py), resolving the canvas via
_IQE_CANVAS_MAP; the mini-bar regex-matches the URL path against PATH_CANVAS
(injected from base.html). This exercise models registry + adapter + path resolution +
query dispatch with the stdlib (our collections hold plain record lists, not adapter_fns).
"""

# Maps a URL path prefix to a canvas key (mirrors the mini-bar PATH_CANVAS map).
PATH_CANVAS = {
    "/network": "ndc",
    "/security": "sdc",
    "/kanban": "kanban",
    "/observability": "odc",
}


# ── Step 1: The collection registry ───────────────────────────────────────────

class IQERegistry:
    """Holds named collections (each a list of record dicts) for a canvas."""

    def __init__(self):
        self.collections = {}

    def register(self, name: str, records: list) -> None:
        """TODO: Register a collection under `name`.

        Store records in self.collections[name]. If the name already exists,
        EXTEND the existing list (adapters may register in batches).
        """
        # YOUR CODE HERE
        pass

    def names(self) -> list:
        """TODO: Return the sorted list of registered collection names."""
        # YOUR CODE HERE
        pass


# ── Step 2: An adapter registers this canvas's collections ────────────────────

def register_kanban_collections(registry: "IQERegistry") -> None:
    """TODO: Adapter entry point — register the 'tasks' collection.

    Real adapters (tools/iqe/adapters/<canvas>.py) expose a register(registry)
    function. Register a collection named "tasks" with these records:
        [
            {"id": "k-1", "status": "done",        "epic": "iqe"},
            {"id": "k-2", "status": "in_progress", "epic": "iqe"},
            {"id": "k-3", "status": "done",        "epic": "dic"},
        ]
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Path resolution + query dispatch ──────────────────────────────────

def canvas_for_path(path: str) -> str | None:
    """TODO: Resolve the canvas key for a URL path via PATH_CANVAS.

    Return the canvas whose PATH_CANVAS prefix the path STARTS WITH
    (e.g. "/kanban/board" -> "kanban"). If none match, return None.
    """
    # YOUR CODE HERE
    pass


def iqe_query(registry: "IQERegistry", collection: str, where: dict) -> list:
    """TODO: Return records in `collection` matching ALL key==value pairs in `where`.

    * Unknown collection -> [].
    * Empty `where` -> all records in the collection.
    * A record matches only if it has every key in `where` with the equal value.
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    reg = IQERegistry()
    register_kanban_collections(reg)
    print("collections:", reg.names())
    print("canvas for /kanban/board:", canvas_for_path("/kanban/board"))
    print("done tasks:", iqe_query(reg, "tasks", {"status": "done"}))
