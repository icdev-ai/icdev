# [TEMPLATE: CUI // SP-CTI]
"""audit_trail.event_type: the constant, the constraint, and the writers agree.

Three copies of the same list existed and all three drifted. VALID_EVENT_TYPES
held 221 entries, the deployed CHECK admitted 189, and neither included the
govcon.* types tools/govcon writes — so every govcon audit INSERT was rejected
and swallowed by its caller's bare ``except``. The audit trail is the NIST AU
record of truth; a write that silently does nothing is the worst failure mode
it has, because the table still looks healthy.

These tests close each way the three can separate again:

* the DDL must be generated from the constant, not respelled beside it
* the deployed constraint must admit exactly the constant
* the icdev/ mirror of the constant must equal the tools/ original
* every govcon audit INSERT must name real columns, balance its parameters,
  and use an event type the constraint admits
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from tools.audit.audit_logger import VALID_EVENT_TYPES, event_type_check_sql

REPO_ROOT = Path(__file__).resolve().parent.parent

# audit_trail's real column set. Anything else in an INSERT is a typo that the
# caller's best-effort except will hide forever (this is how `recorded_at` and
# `resource` survived in tools/govcon/procurement_vehicles.py).
AUDIT_TRAIL_COLUMNS = {
    "id",
    "project_id",
    "event_type",
    "actor",
    "action",
    "details",
    "affected_files",
    "classification",
    "ip_address",
    "session_id",
    "created_at",
    "hash",
    "previous_hash",
    "signature",
}

GOVCON_DIRS = [REPO_ROOT / "tools" / "govcon", REPO_ROOT / "icdev" / "tools" / "govcon"]


def _cell(row):
    """First column of a row, whether it is a tuple or a dict-like DictRow."""
    if hasattr(row, "keys"):
        return list(dict(row).values())[0]
    return row[0]


def _types_in_check(sql: str) -> set:
    """The event types a CHECK(event_type IN (...)) clause admits."""
    match = re.search(
        r"event_type\s+(?:TEXT\s+NOT\s+NULL\s+)?CHECK\s*\(\s*event_type\s+IN\s*\((.*?)\)\s*\)",
        sql,
        re.S | re.I,
    )
    if not match:
        match = re.search(r"CHECK\s*\(\s*event_type\s+IN\s*\((.*?)\)\s*\)", sql, re.S | re.I)
    assert match, "no event_type CHECK found in the DDL"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _audit_trail_inserts(path: Path):
    """Yield (lineno, sql, n_params) for each literal INSERT INTO audit_trail."""
    src = path.read_text(encoding="utf-8", errors="replace")
    if "INSERT INTO audit_trail" not in src:
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover - a parse failure is another test's problem
        return
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if not (
            isinstance(node, ast.Call)
            and isinstance(func, ast.Attribute)
            and func.attr == "execute"
            and node.args
        ):
            continue
        try:
            sql = ast.literal_eval(node.args[0])
        except (ValueError, SyntaxError):
            continue
        if not isinstance(sql, str) or "INSERT INTO audit_trail" not in sql:
            continue
        n_params = None
        if len(node.args) > 1 and isinstance(node.args[1], (ast.Tuple, ast.List)):
            n_params = len(node.args[1].elts)
        yield node.lineno, sql, n_params


def _call_site_event_types(path: Path):
    """Yield (lineno, literal) for each event_type passed to a local _audit().

    Where _audit takes event_type as a parameter the literal is at the call
    site, so it has to be located by argument position rather than by name.
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    if "def _audit(" not in src:
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover - a parse failure is another test's problem
        return

    idx = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_audit":
            names = [a.arg for a in node.args.args]
            if "event_type" in names:
                idx = names.index("event_type")
            break
    if idx is None:
        return

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_audit"):
            continue
        if len(node.args) > idx and isinstance(node.args[idx], ast.Constant):
            value = node.args[idx].value
            if isinstance(value, str):
                yield node.lineno, value
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    yield node.lineno, kw.value.value


class TestConstantAndConstraintAgree:
    def test_generated_schema_check_matches_constant(self):
        """init_icdev_db must derive the CHECK, not respell the list beside it."""
        from tools.db.init_icdev_db import SCHEMA_SQL

        assert "@@AUDIT_EVENT_TYPES@@" not in SCHEMA_SQL, (
            "the audit event-type placeholder was left unsubstituted"
        )
        assert _types_in_check(SCHEMA_SQL) == set(VALID_EVENT_TYPES)

    def test_event_type_check_sql_admits_exactly_the_constant(self):
        assert _types_in_check(event_type_check_sql()) == set(VALID_EVENT_TYPES)

    def test_generated_check_is_accepted_by_sqlite(self, tmp_path):
        """The generated DDL must be valid SQL and actually enforce the list."""
        db = tmp_path / "audit.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE audit_trail ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  event_type TEXT NOT NULL,"
            "  actor TEXT NOT NULL,"
            "  action TEXT NOT NULL,"
            f"  {event_type_check_sql()})"
        )
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action) VALUES (?, ?, ?)",
            ("govcon.procurement_vehicle", "test", "probe"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_trail (event_type, actor, action) VALUES (?, ?, ?)",
                ("govcon.not_a_real_type", "test", "probe"),
            )
        conn.close()

    def test_deployed_constraint_matches_constant(self):
        """The live database must admit exactly VALID_EVENT_TYPES.

        This is the check that would have caught the original 221-vs-189 split.
        It runs against whatever backend is configured; adding an event type
        without a migration that calls rebuild_event_type_constraint() fails
        here rather than silently rejecting writes in production.
        """
        from tools.db.storage import get_connection

        try:
            conn = get_connection()
        except Exception as exc:  # pragma: no cover - no database configured
            pytest.skip(f"no database available: {exc}")
        try:
            if getattr(conn, "_backend", "") == "postgresql":
                row = conn.execute(
                    "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                    "WHERE conname = 'audit_trail_event_type_check'"
                ).fetchone()
                if not row:
                    pytest.skip("audit_trail_event_type_check not present")
                # PostgreSQL renders the list as 'value'::text inside an ARRAY.
                deployed = set(re.findall(r"'([^']+)'::text", _cell(row)))
            else:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_trail'"
                ).fetchone()
                if not row:
                    pytest.skip("audit_trail not present in this database")
                deployed = _types_in_check(_cell(row))
        finally:
            conn.close()

        missing = sorted(set(VALID_EVENT_TYPES) - deployed)
        extra = sorted(deployed - set(VALID_EVENT_TYPES))
        assert not missing and not extra, (
            f"audit_trail_event_type_check has drifted from VALID_EVENT_TYPES.\n"
            f"  admitted by the constant but not the constraint: {missing}\n"
            f"  admitted by the constraint but not the constant: {extra}\n"
            "Add a migration whose body calls "
            "tools.audit.audit_logger.rebuild_event_type_constraint(conn)."
        )

    def test_icdev_mirror_constant_matches(self):
        """icdev/ carries a second copy of the constant; it must not drift."""
        mirror = REPO_ROOT / "icdev" / "tools" / "audit" / "audit_logger.py"
        if not mirror.exists():  # pragma: no cover - mirror is expected to exist
            pytest.skip("icdev mirror not present")
        from icdev.tools.audit.audit_logger import (
            VALID_EVENT_TYPES as MIRROR_TYPES,
        )

        assert set(MIRROR_TYPES) == set(VALID_EVENT_TYPES)

    def test_no_migration_hardcodes_the_event_type_list(self):
        """A migration that respells the list becomes the next stale copy."""
        offenders = []
        for path in (REPO_ROOT / "tools" / "db" / "migrations").rglob("*"):
            if path.suffix not in {".py", ".sql"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "audit_trail_event_type_check" not in text:
                continue
            # A migration touching this constraint must import the constant.
            if "VALID_EVENT_TYPES" not in text and "rebuild_event_type_constraint" not in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, (
            "these migrations spell out the event_type CHECK without deriving "
            f"it from VALID_EVENT_TYPES: {offenders}"
        )


class TestGovconAuditWritesAreWellFormed:
    """Every govcon audit INSERT must be able to actually insert a row."""

    def test_columns_exist_and_parameters_balance(self):
        offenders = []
        for directory in GOVCON_DIRS:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.py")):
                for lineno, sql, n_params in _audit_trail_inserts(path):
                    where = f"{path.relative_to(REPO_ROOT)}:{lineno}"
                    match = re.search(r"INSERT INTO audit_trail\s*\(([^)]*)\)", sql, re.S)
                    if not match:
                        continue
                    cols = [c.strip() for c in match.group(1).split(",")]
                    unknown = [c for c in cols if c not in AUDIT_TRAIL_COLUMNS]
                    if unknown:
                        offenders.append(f"{where}: columns not on audit_trail: {unknown}")
                    if "event_type" not in cols:
                        offenders.append(f"{where}: omits NOT NULL event_type")
                    if "actor" not in cols:
                        offenders.append(f"{where}: omits NOT NULL actor")
                    if "VALUES" in sql:
                        n_ph = len(re.findall(r"%s|\?", sql.split("VALUES", 1)[1]))
                        if n_ph != len(cols):
                            offenders.append(
                                f"{where}: {len(cols)} columns but {n_ph} placeholders"
                            )
                        if n_params is not None and n_ph != n_params:
                            offenders.append(
                                f"{where}: {n_ph} placeholders but {n_params} parameters"
                            )
        assert not offenders, "broken audit_trail INSERTs:\n  " + "\n  ".join(offenders)

    def test_event_types_written_are_admitted_by_the_constraint(self):
        """A literal event type no constraint admits is a guaranteed dead write."""
        offenders = []
        valid = set(VALID_EVENT_TYPES)
        for directory in GOVCON_DIRS:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.py")):
                src = path.read_text(encoding="utf-8", errors="replace")
                for literal in set(re.findall(r"[\"'](govcon\.[a-z0-9_]+)[\"']", src)):
                    if literal not in valid:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {literal}")
        assert not offenders, (
            "govcon.* event types written but not in VALID_EVENT_TYPES "
            f"(the constraint will reject them): {sorted(set(offenders))}"
        )

    def test_call_site_event_types_are_admitted(self):
        """The types passed where _audit takes event_type as a parameter.

        Twelve govcon modules parameterise event_type, so the literal lives at
        the call site and carries no govcon. prefix -- ``igce.created``,
        ``teaming.add_partner``, ``pwin.compute``. Twenty-eight of them were
        rejected by the constraint while the govcon.*-only check above passed,
        which is exactly how they stayed invisible. Scanning by prefix cannot
        find these; scanning by argument position can.
        """
        offenders = []
        valid = set(VALID_EVENT_TYPES)
        for directory in GOVCON_DIRS:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.py")):
                for lineno, literal in _call_site_event_types(path):
                    if literal not in valid:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{lineno}: {literal}"
                        )
        assert not offenders, (
            "event types passed to _audit() but absent from VALID_EVENT_TYPES "
            "(every such write is rejected and swallowed):\n  "
            + "\n  ".join(sorted(set(offenders)))
        )

    def test_call_site_scan_finds_the_parameterised_modules(self):
        """Guard the scanner itself: a no-op scan would make the test above vacuous."""
        found = set()
        for directory in GOVCON_DIRS:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.py")):
                if any(True for _ in _call_site_event_types(path)):
                    found.add(path.stem)
        assert {"igce_estimator", "teaming_hub", "bayesian_bid_scorer"} <= found, (
            f"the call-site scanner stopped finding known parameterised modules: {sorted(found)}"
        )
