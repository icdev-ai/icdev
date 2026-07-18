
# Auto-grader — ACF: novelty gate + CoD + task-graph seed

# ── novelty_score ─────────────────────────────────────────────────────────────
cat = [{"a", "b", "c"}, {"x", "y"}]
# identical to a catalog entry -> similarity 1.0 -> novelty 0.0
assert novelty_score({"a", "b", "c"}, cat) == 0.0
# disjoint from everything -> similarity 0.0 -> novelty 1.0
assert novelty_score({"p", "q"}, cat) == 1.0
# empty catalog -> fully novel
assert novelty_score({"a"}, []) == 1.0
# empty capability -> not novel
assert novelty_score(set(), cat) == 0.0
# partial overlap: {a,b} vs {a,b,c} = 2/3 similarity -> novelty 1 - 0.6667 = 0.3333
n = novelty_score({"a", "b"}, [{"a", "b", "c"}])
assert n == 0.3333, f"expected 0.3333, got {n}"

# ── apply_novelty_gate ────────────────────────────────────────────────────────
# near-identical concept -> duplicate
g_dup = apply_novelty_gate({"a", "b", "c"}, cat)
assert g_dup["verdict"] == "duplicate", f"expected duplicate, got {g_dup}"
assert g_dup["max_similarity"] == 1.0

# 2/3 overlap: novelty 0.3333 < default min_novelty 0.35, sim 0.6667 < 0.8 -> low_novelty
g_low = apply_novelty_gate({"a", "b"}, [{"a", "b", "c"}])
assert g_low["verdict"] == "low_novelty", f"expected low_novelty, got {g_low}"

# fully disjoint -> pass
g_ok = apply_novelty_gate({"p", "q", "r"}, cat)
assert g_ok["verdict"] == "pass", f"expected pass, got {g_ok}"
assert g_ok["novelty"] == 1.0

# ── cod_go_no_go ──────────────────────────────────────────────────────────────
assert cod_go_no_go(0.72) == "go"
assert cod_go_no_go(0.60) == "go", "threshold is inclusive"
assert cod_go_no_go(0.59) == "no_go"
assert cod_go_no_go(0.9, min_composite=0.95) == "no_go"

# ── seed_task_graph ───────────────────────────────────────────────────────────
tasks = seed_task_graph("acme")
assert len(tasks) == 8, f"default is one task per epic (8), got {len(tasks)}"
assert [t["epic"] for t in tasks] == ["db", "core", "engine", "dash", "mcp", "reflex", "doc", "vv"]
assert tasks[0]["id"] == "acme-db-01"
assert tasks[0]["depends_on"] is None, "first task depends on nothing"
assert tasks[1]["id"] == "acme-core-01"
assert tasks[1]["depends_on"] == "acme-db-01", "linear dependency chain"
# build epics carry the SIPA integrity gate; doc + vv do not
gate = {t["epic"]: t["integrity_gate"] for t in tasks}
assert gate["db"] is True and gate["engine"] is True and gate["reflex"] is True
assert gate["doc"] is False and gate["vv"] is False

# counts override: two db tasks, one everything else
multi = seed_task_graph("acme", counts={"db": 2})
db_ids = [t["id"] for t in multi if t["epic"] == "db"]
assert db_ids == ["acme-db-01", "acme-db-02"], f"count override wrong: {db_ids}"
assert len(multi) == 9

print("PASS: ACF novelty gate, CoD go/no-go, and task-graph seeding all verified.")
