#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the ICDEV-side evidence collector (xbm-cmp-01-d1).

``tools/innovation/icdev_evidence.py`` answers "what does ICDEV itself hold for
this subsystem?" so a scout finding can be compared rather than reported alone.

What these pin, and why each one is here rather than being obvious:

1. **The verdict vocabulary is honest.** ``unmeasured`` must never collapse to
   ``inert``. A DB that was not opened and a subsystem with no rows are
   different answers, and the second one is a finding — so a bug that merges
   them manufactures findings out of a cold worktree.

2. **The map is config.** A new subsystem is a YAML edit. A test that only read
   the shipped file would pass even against a hardcoded map, so the additive
   cases build their own inventory and their own database.

3. **The shipped inventory covers the tags in use.** ``competitors.yaml`` tags
   every target with a subsystem. A tag with no inventory entry raises, which is
   correct — but only if nobody ships one, so that is checked directly.

Offline and deterministic: temp files, an in-memory SQLite database, and no
network. Nothing here reads data/icdev.db, so a cold worktree and a populated
one give the same result.
"""

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import icdev_evidence as ev

INVENTORY = BASE_DIR / "args" / "xbm_subsystem_inventory.yaml"
WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"


# ── fixtures ─────────────────────────────────────────────────────────────


class _Rows(list):
    """sqlite3.Row is already mapping-like; the module calls dict() on it."""


class FakeConn:
    """A real SQLite database behind the connection surface this module uses.

    Real SQL, not a stub returning canned dicts — a stub would let a broken
    COUNT or a mangled identifier pass. Reported as non-PG so the module takes
    the sqlite_master catalog branch.
    """

    def __init__(self, tables: dict):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        for name, rows in tables.items():
            self._db.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
            for i in range(rows):
                self._db.execute(f"INSERT INTO {name} (id) VALUES ({i + 1})")
        self._db.commit()
        self.closed = False
        self._backend = "sqlite"

    def execute(self, sql, params=None):
        return self._db.execute(sql, params or ())

    def close(self):
        self.closed = True
        self._db.close()


class BrokenConn:
    """Raises on catalog read — an unreachable or half-migrated database."""

    _backend = "sqlite"

    def execute(self, sql, params=None):
        raise RuntimeError("could not connect to server")

    def close(self):
        pass


@pytest.fixture
def repo(tmp_path):
    """A tiny fake repo tree, so module counts are exact rather than ambient."""
    for rel, count in (("tools/alpha", 3), ("tools/beta", 2)):
        d = tmp_path / rel
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"mod_{i}.py").write_text("# CUI // SP-CTI\n", encoding="utf-8")
    (tmp_path / "tools" / "alpha" / "notes.md").write_text("not a module\n", encoding="utf-8")
    return tmp_path


def _inventory(**subsystems):
    return subsystems


def write_inventory(tmp_path: Path, subsystems: dict) -> Path:
    path = tmp_path / "xbm_subsystem_inventory.yaml"
    path.write_text(yaml.safe_dump({"subsystems": subsystems}), encoding="utf-8")
    return path


# ── the acceptance criterion ─────────────────────────────────────────────


def test_returns_module_count_row_count_and_status(repo):
    """The shape the comparison engine consumes: three fields, one call."""
    inv = _inventory(
        demo={"title": "Demo", "modules": ["tools/alpha/*.py"], "tables": ["demo_*"]}
    )
    conn = FakeConn({"demo_events": 4, "demo_runs": 3, "unrelated": 9})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=conn)

    assert result["module_count"] == 3          # the .md is not counted
    assert result["table_row_count"] == 7       # unrelated table excluded
    assert result["status"] == ev.STATUS_LIVE
    assert result["tables"] == {"demo_events": 4, "demo_runs": 3}
    assert result["modules"] == {"tools/alpha/*.py": 3}
    assert result["db_measured"] is True


def test_a_caller_supplied_connection_is_left_open(repo):
    """A whole-platform sweep batches onto one connection; closing it after the
    first subsystem would make every later one report unmeasured."""
    conn = FakeConn({"demo_events": 1})
    inv = _inventory(demo={"modules": [], "tables": ["demo_*"]})

    ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=conn)

    assert conn.closed is False


# ── the verdict vocabulary ───────────────────────────────────────────────


def test_located_tables_holding_no_rows_are_inert(repo):
    """The sweep's §4 state: shipped, reachable, and empty."""
    inv = _inventory(demo={"modules": ["tools/alpha/*.py"], "tables": ["demo_*"]})
    conn = FakeConn({"demo_events": 0, "demo_runs": 0})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=conn)

    assert result["status"] == ev.STATUS_INERT
    assert result["table_count"] == 2
    assert result["table_row_count"] == 0


def test_an_unreachable_database_is_unmeasured_not_inert(repo):
    """The whole point of the distinction. Reporting `inert` here would turn a
    cold worktree into a platform-wide finding, and the sweep's own stated
    limitation was exactly this confusion."""
    inv = _inventory(demo={"modules": ["tools/alpha/*.py"], "tables": ["demo_*"]})

    result = ev.gather_subsystem_evidence(
        "demo", inventory=inv, base_dir=repo, conn=BrokenConn()
    )

    assert result["status"] == ev.STATUS_UNMEASURED
    assert result["status"] != ev.STATUS_INERT
    assert result["db_measured"] is False
    assert "could not connect" in result["db_error"]
    # Module evidence is still real — half the answer is not no answer.
    assert result["module_count"] == 3


def test_declared_tables_that_do_not_exist_yet_are_not_counted(repo):
    """A subsystem whose migration has not run has no tables to find. It is not
    live, and it is not inert either — nothing of its own was located."""
    inv = _inventory(demo={"modules": ["tools/alpha/*.py"], "tables": ["demo_*"]})
    conn = FakeConn({"something_else": 5})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=conn)

    assert result["table_count"] == 0
    assert result["status"] == ev.STATUS_CODE_ONLY


def test_a_subsystem_with_modules_and_no_tables_is_code_only(repo):
    """The sweep's rule 4: several components legitimately hold no tables. This
    must not read as a defect, and it must not need a database to decide."""
    inv = _inventory(demo={"modules": ["tools/beta/*.py"], "tables": []})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=None)

    assert result["status"] == ev.STATUS_CODE_ONLY
    assert result["module_count"] == 2
    assert result["db_measured"] is False  # never opened; it could not matter


def test_a_subsystem_with_nothing_behind_it_is_absent(repo):
    """`embedded` is this case: the ESP-IDF targets benchmark a separate tree.
    `absent` is a legitimate comparison input, not a gap in the collector."""
    inv = _inventory(demo={"modules": [], "tables": []})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo)

    assert result["status"] == ev.STATUS_ABSENT
    assert result["module_count"] == 0
    assert result["table_row_count"] == 0


# ── counting rules ───────────────────────────────────────────────────────


def test_overlapping_patterns_count_a_table_once(repo):
    """`llm_*` and `llm_judge_evaluations` both match in the shipped inventory."""
    inv = _inventory(demo={"modules": [], "tables": ["demo_*", "demo_events"]})
    conn = FakeConn({"demo_events": 6})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo, conn=conn)

    assert result["table_row_count"] == 6


def test_overlapping_module_globs_count_a_file_once(repo):
    inv = _inventory(demo={"modules": ["tools/alpha/*.py", "tools/alpha/mod_0.py"]})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo)

    assert result["module_count"] == 3
    # The per-pattern breakdown still reports each glob's own matches.
    assert result["modules"]["tools/alpha/mod_0.py"] == 1


def test_a_dead_module_glob_reports_zero_for_that_pattern(repo):
    """A moved directory shows as one zero rather than a quietly smaller total."""
    inv = _inventory(demo={"modules": ["tools/alpha/*.py", "tools/gone/*.py"]})

    result = ev.gather_subsystem_evidence("demo", inventory=inv, base_dir=repo)

    assert result["modules"] == {"tools/alpha/*.py": 3, "tools/gone/*.py": 0}
    assert result["module_count"] == 3


# ── the map is config ────────────────────────────────────────────────────


def test_a_new_subsystem_needs_no_code_change(repo, tmp_path):
    """The acceptance criterion for a config-driven map."""
    path = write_inventory(
        tmp_path,
        {
            "brand_new": {
                "title": "Invented after this module was written",
                "section": None,
                "modules": ["tools/beta/*.py"],
                "tables": ["fresh_*"],
            }
        },
    )
    conn = FakeConn({"fresh_rows": 2})

    inv = ev.load_inventory(path)
    result = ev.gather_subsystem_evidence("brand_new", inventory=inv, base_dir=repo, conn=conn)

    assert result["module_count"] == 2
    assert result["table_row_count"] == 2
    assert result["status"] == ev.STATUS_LIVE
    assert result["title"] == "Invented after this module was written"


def test_an_unknown_subsystem_raises_rather_than_comparing_against_zero():
    """A finding tagged with a subsystem nobody declared must be loud. Returning
    an empty result would report ICDEV as having nothing in that area."""
    with pytest.raises(KeyError, match="not in the inventory"):
        ev.gather_subsystem_evidence("no_such_subsystem", inventory={"demo": {}})


def test_a_missing_inventory_raises_rather_than_reporting_everything_absent(tmp_path):
    with pytest.raises(ev.InventoryError):
        ev.inventory_path((tmp_path / "nope.yaml",))


def test_malformed_inventory_yaml_raises(tmp_path):
    path = tmp_path / "inv.yaml"
    path.write_text("subsystems: {{{\n", encoding="utf-8")
    with pytest.raises(ev.InventoryError):
        ev.load_inventory(path)


def test_an_inventory_declaring_no_subsystems_raises(tmp_path):
    path = tmp_path / "inv.yaml"
    path.write_text("subsystems: {}\n", encoding="utf-8")
    with pytest.raises(ev.InventoryError):
        ev.load_inventory(path)


# ── whole-platform sweep ─────────────────────────────────────────────────


def test_gather_all_covers_every_subsystem_on_one_connection(repo):
    inv = _inventory(
        one={"modules": ["tools/alpha/*.py"], "tables": ["demo_*"]},
        two={"modules": ["tools/beta/*.py"], "tables": ["empty_*"]},
        three={"modules": [], "tables": []},
    )
    conn = FakeConn({"demo_events": 5, "empty_log": 0})

    result = ev.gather_all(inventory=inv, base_dir=repo, conn=conn)

    assert result["total_subsystems"] == 3
    assert result["subsystems"]["one"]["status"] == ev.STATUS_LIVE
    assert result["subsystems"]["two"]["status"] == ev.STATUS_INERT
    assert result["subsystems"]["three"]["status"] == ev.STATUS_ABSENT
    assert result["status_counts"] == {
        ev.STATUS_LIVE: 1,
        ev.STATUS_INERT: 1,
        ev.STATUS_ABSENT: 1,
    }


def test_gather_all_degrades_to_unmeasured_without_a_database(repo):
    """A sweep on a machine with no DB reports what it could not do, and still
    reports the module evidence it could."""
    inv = _inventory(one={"modules": ["tools/alpha/*.py"], "tables": ["demo_*"]})

    result = ev.gather_all(inventory=inv, base_dir=repo, conn=BrokenConn())

    assert result["subsystems"]["one"]["status"] == ev.STATUS_UNMEASURED
    assert result["subsystems"]["one"]["module_count"] == 3
    assert "db_error" in result


def test_audit_names_the_patterns_that_match_nothing(repo):
    """The inventory's rot detector. A glob pointing at a renamed table shrinks a
    subsystem's evidence to zero and reads as a real finding."""
    inv = _inventory(one={"modules": ["tools/alpha/*.py", "tools/gone/*.py"],
                          "tables": ["demo_*", "renamed_*"]})
    conn = FakeConn({"demo_events": 1})

    result = ev.audit_inventory(inventory=inv, base_dir=repo, conn=conn)

    assert result["unmatched_module_patterns"] == {"one": ["tools/gone/*.py"]}
    assert result["unmatched_table_patterns"] == {"one": ["renamed_*"]}


# ── the shipped inventory ────────────────────────────────────────────────


def test_every_subsystem_the_watchlist_tags_has_an_inventory_entry():
    """The routing guarantee. A scout finding tagged with a subsystem that has no
    inventory entry raises at comparison time — which is right, but only if the
    two files cannot drift apart unnoticed in the first place."""
    targets = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8"))["targets"]
    tagged = {t["subsystem"] for t in targets if t.get("subsystem")}
    declared = set(ev.load_inventory(INVENTORY))

    missing = tagged - declared
    assert not missing, f"watchlist tags with no ICDEV inventory: {sorted(missing)}"


def test_every_shipped_module_glob_still_matches_something():
    """Directories move. A glob that has stopped matching reports a real
    subsystem as smaller than it is, and nothing else would catch it."""
    inv = ev.load_inventory(INVENTORY)
    dead = {}
    for tag, spec in inv.items():
        patterns = (spec or {}).get("modules") or []
        _, per_pattern = ev.count_modules(patterns, BASE_DIR)
        empty = [p for p, n in per_pattern.items() if n == 0]
        if empty:
            dead[tag] = empty
    assert not dead, f"module globs matching nothing: {dead}"


def test_the_shipped_inventory_reproduces_the_benchmark_map_counts():
    """The map's §5 and §9 quote these directly, and §9's gap — 2 evaluation
    modules against 38 in rag — is the comparison's headline finding. If these
    drift, the map and the collector are measuring different things."""
    assert ev.count_modules(["tools/rag/*.py"], BASE_DIR)[0] == 38
    assert ev.count_modules(["tools/knowledge_graph/*.py"], BASE_DIR)[0] == 16
    assert ev.count_modules(["tools/evaluation/*.py"], BASE_DIR)[0] == 2
    assert ev.count_modules(["tools/data_canvas/*.py"], BASE_DIR)[0] == 18


def test_embedded_is_declared_empty_on_purpose():
    """Left undeclared it would raise; mapped to something adjacent it would lie.
    Declared empty, it reports `absent`, which is the true answer."""
    inv = ev.load_inventory(INVENTORY)
    assert inv["embedded"]["modules"] == []
    assert inv["embedded"]["tables"] == []
    assert "SparkPilot" in inv["embedded"]["note"]

    result = ev.gather_subsystem_evidence("embedded", inventory=inv)
    assert result["status"] == ev.STATUS_ABSENT


def test_the_promoter_and_the_inventory_share_one_subsystem_vocabulary():
    """args/innovation_promoter.yaml already keys its promotion rules on the same
    tags, with the benchmark map's verdicts copied in by hand. Those verdicts are
    what xbm-cmp-01-d3 replaces with computed ones — which only works if the two
    files name the same subsystems. Two files keyed on almost-the-same vocabulary
    is the failure this catches."""
    promoter = BASE_DIR / "args" / "innovation_promoter.yaml"
    declared = set(ev.load_inventory(INVENTORY))
    promoted = set(
        yaml.safe_load(promoter.read_text(encoding="utf-8")).get("benchmark_subsystems") or {}
    )

    missing = promoted - declared
    assert not missing, f"promoter subsystems with no ICDEV inventory entry: {sorted(missing)}"


def test_the_shipped_inventory_resolves_without_an_argument():
    assert ev.inventory_path() == INVENTORY
