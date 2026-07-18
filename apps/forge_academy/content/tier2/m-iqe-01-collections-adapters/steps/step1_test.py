
# Auto-grader — IQE collections + adapters + dispatch

# ── IQERegistry.register / names ──────────────────────────────────────────────
reg = IQERegistry()
reg.register("alpha", [{"id": 1}])
reg.register("beta", [{"id": 2}])
assert reg.names() == ["alpha", "beta"], f"names not sorted: {reg.names()}"
# re-registering the same name extends
reg.register("alpha", [{"id": 3}])
assert reg.collections["alpha"] == [{"id": 1}, {"id": 3}], "register must extend existing"

# ── adapter ───────────────────────────────────────────────────────────────────
reg2 = IQERegistry()
register_kanban_collections(reg2)
assert reg2.names() == ["tasks"], f"adapter should register 'tasks': {reg2.names()}"
assert len(reg2.collections["tasks"]) == 3

# ── canvas_for_path (mini-bar PATH_CANVAS) ────────────────────────────────────
assert canvas_for_path("/kanban/board") == "kanban"
assert canvas_for_path("/network") == "ndc"
assert canvas_for_path("/security/zig/overview") == "sdc"
assert canvas_for_path("/unknown/page") is None

# ── iqe_query ─────────────────────────────────────────────────────────────────
done = iqe_query(reg2, "tasks", {"status": "done"})
assert len(done) == 2, f"expected 2 done tasks, got {len(done)}"
assert {r["id"] for r in done} == {"k-1", "k-3"}

both = iqe_query(reg2, "tasks", {"status": "done", "epic": "iqe"})
assert [r["id"] for r in both] == ["k-1"], f"multi-key filter wrong: {both}"

# empty filter → all records
assert len(iqe_query(reg2, "tasks", {})) == 3

# unknown collection → []
assert iqe_query(reg2, "nope", {}) == []

print("PASS: IQE registry + adapter + path resolution + filtered dispatch all verified.")
