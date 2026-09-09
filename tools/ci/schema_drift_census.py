#!/usr/bin/env python3
# CUI // SP-CTI
"""One table, one shape: catch a CREATE TABLE that disagrees with the schema of record.

THE DEFECT THIS EXISTS FOR, measured four times in two days on this repo and
each time repaired by hand:

    NOT NULL constraint failed: dic_chunk_links.doc_id          (PR #2170)
    NOT NULL constraint failed: dic_chunk_links.chunk_index     (PR #2175)
    NOT NULL constraint failed: dic_sections.doc_id             (PR #2213)
    no such column: dic_presence_sessions.expires_at            (PR #2195)

Every one is the same mechanism. `CREATE TABLE IF NOT EXISTS` **never alters an
existing table**, so when two definitions of one table disagree, whichever runs
FIRST wins and the loser's columns simply never exist. On a workstation whose
table predates the drift the strict definition is already there and everything
passes; on a fresh database the weak one wins and the code that reads the
missing column fails. That is why all four were green locally and red only in
CI, and why patching the single column CI named fixed nothing -- the next
missing column was already loaded behind it.

Three of the four were test fixtures. THE FOURTH WAS PRODUCTION
(`document_intelligence/db/init_db.py` declared `dic_presence_sessions` with a
different primary key and no `expires_at`), so a fresh SQLite install got a
presence table the presence feature could not use. This checks both trees for
that reason: the defect is not a testing problem, it is a "two writers, one
table" problem that happens to surface in tests first.

WHAT COUNTS AS THE SCHEMA OF RECORD. `tools/db/schema/pg_consolidated.sql` is a
pg_dump of the live PostgreSQL and is authoritative wherever it names a table.
It is the only anchor used: an anchor chosen by "whichever file looks most
official" would itself be a judgement call that drifts.

WHAT IS REPORTED, and why it is narrow on purpose. A definition is an OFFENDER
only when it would produce a table the schema of record's own writers cannot
use:

  * it OMITS a column the record declares ``NOT NULL`` with no default -- an
    INSERT written against the record fails on it;
  * it declares a DIFFERENT PRIMARY KEY -- the `dic_presence_sessions` shape,
    and the one thing no ALTER can repair on SQLite.

Declaring FEWER nullable columns is NOT reported. A fixture that needs three
columns of a forty-column table and lists three is doing the right thing, and a
gate that demanded all forty would be ignored within a week.

USAGE
    python tools/ci/schema_drift_census.py --check      # gate: 0 clean / 1 new drift / 2 cannot run
    python tools/ci/schema_drift_census.py --list       # every offender, grouped by table
    python tools/ci/schema_drift_census.py --table dic_sections
    python tools/ci/schema_drift_census.py --write      # regenerate the census after FIXING one

THE CENSUS ONLY SHRINKS. ``args/schema_drift_census.txt`` enumerates the known
offenders by identity, not by count -- a bare count can be held constant while
the set churns, which is the regression this file exists to prevent. `--check`
fails on any offender not already named there. Removing a name is what fixing
one looks like; adding a name is a deliberate, reviewable act.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from icdev.core.paths import repo_root

# The ONE resolver (tools/ci/self_root_census.py). Walking `parents[2]` from
# this file is a hard-coded claim about where it sits that breaks silently the
# moment it moves -- and this module has no sys.path bootstrap for the import to
# sit above, because it needs nothing from `tools`.
REPO_ROOT = repo_root(__file__)
RECORD_SQL = REPO_ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"
CENSUS_FILE = REPO_ROOT / "args" / "schema_drift_census.txt"

#: Trees whose CREATE TABLE statements are checked. `icdev/` is the packaged
#: mirror of `tools/`; its copies are byte-identical by mirror_parity, so
#: reporting both would double every finding.
SEARCH_DIRS = ("tools", "tests", "migrations")
SKIP_PARTS = ("__pycache__", ".tmp", "node_modules", "/icdev/tools", "\\icdev\\tools")

#: Only the HEAD of the statement is matched by regex. The BODY is taken by
#: counting parentheses, because a non-greedy `(.*?)` stops at the first `)` it
#: meets -- and the first `)` in real DDL is almost always inside a type
#: (`VARCHAR(255)`, `NUMERIC(12,2)`). The first draft of this file did exactly
#: that, reported ZERO definitions across 12,372 CREATE TABLE sites, and looked
#: clean. A census that cannot see its subject is worse than no census.
_CREATE_HEAD = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>\"[^\"]+\"|[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)\s*\(",
    re.IGNORECASE,
)


def _balanced_body(text: str, open_paren: int) -> Optional[str]:
    """The text between ``open_paren`` and its matching ``)``, or None.

    Quote- and comment-aware: a ``)`` inside a string literal or after ``--``
    closes nothing.
    """
    depth, i, n = 0, open_paren, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
        elif text[i:i + 2] == "--":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
        i += 1
    return None
_NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
_DEFAULT = re.compile(r"\bDEFAULT\b", re.IGNORECASE)
_PK_INLINE = re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE)
_PK_TABLE = re.compile(r"^\s*PRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)", re.IGNORECASE)
_CONSTRAINT_START = re.compile(
    r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|EXCLUDE|LIKE)\b",
    re.IGNORECASE,
)


def _rel(path: Path) -> str:
    """Repo-relative when it can be, the full path when it cannot.

    `Path.relative_to` RAISES for a path outside the repo, and every use of it
    here is inside a message -- including the "cannot run" message for a missing
    schema of record, which is exactly when the path is most likely to be
    somewhere unexpected. A diagnostic that raises while reporting a problem
    replaces the problem with its own traceback.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass
class Column:
    name: str
    not_null: bool = False
    has_default: bool = False
    primary_key: bool = False

    @property
    def required(self) -> bool:
        """NOT NULL with nothing to fall back on — an INSERT must supply it."""
        return self.not_null and not self.has_default and not self.primary_key


@dataclass
class TableDef:
    table: str
    path: str
    line: int
    columns: Dict[str, Column] = field(default_factory=dict)
    pk: Tuple[str, ...] = ()

    #: 0-based index among definitions of the SAME table in the SAME file.
    #: `init_db.py` files here carry the same DDL twice, so the pair needs
    #: telling apart without using the line number.
    ordinal: int = 0

    @property
    def site(self) -> str:
        """Identity, and deliberately WITHOUT the line number.

        A census keyed on `path:line` renames every entry below any edit, so an
        unrelated one-line change reports a screenful of "new drift" and the
        gate gets switched off within a week. `path::table` is stable under
        every edit that does not add or remove a definition -- which is exactly
        the event this census should notice.
        """
        suffix = f"#{self.ordinal}" if self.ordinal else ""
        return f"{self.path}::{self.table}{suffix}"


def _split_columns(body: str) -> List[str]:
    """Split a CREATE TABLE body on top-level commas only."""
    parts, depth, buf = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_table(name: str, body: str, path: str, line: int) -> Optional[TableDef]:
    """Turn one CREATE TABLE body into a TableDef, or None if it is unreadable.

    Deliberately tolerant: SQL embedded in Python carries f-string braces,
    comments and continuations, and a parser that raised on any of them would
    report a repo full of false 'cannot read' rather than the drift it is for.
    """
    table = name.strip().strip('"').split(".")[-1].lower()
    if not table:
        return None
    td = TableDef(table=table, path=path, line=line)
    for raw in _split_columns(body):
        clean = re.sub(r"--[^\n]*", "", raw).strip()
        if not clean:
            continue
        m = _PK_TABLE.match(clean)
        if m:
            td.pk = tuple(
                c.strip().strip('"').lower() for c in m.group("cols").split(",") if c.strip()
            )
            continue
        if _CONSTRAINT_START.match(clean):
            continue
        tok = clean.split()
        if not tok:
            continue
        col = tok[0].strip('"').lower()
        if not re.fullmatch(r"[a-z_][\w]*", col):
            continue
        c = Column(
            name=col,
            not_null=bool(_NOT_NULL.search(clean)),
            has_default=bool(_DEFAULT.search(clean)),
            primary_key=bool(_PK_INLINE.search(clean)),
        )
        td.columns[col] = c
        if c.primary_key and not td.pk:
            td.pk = (col,)
    return td if td.columns else None


def _iter_files() -> List[Path]:
    out: List[Path] = []
    for d in SEARCH_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for ext in ("*.py", "*.sql"):
            for p in base.rglob(ext):
                # RELATIVE, not absolute. Matching SKIP_PARTS against the
                # absolute path made every file match `.tmp` when the checkout
                # itself lives under a `.tmp` worktree, and the census reported
                # a clean tree because it had read nothing.
                rel = p.relative_to(REPO_ROOT).as_posix()
                if any(part in rel for part in SKIP_PARTS):
                    continue
                out.append(p)
    return sorted(out)


def scan(paths: Optional[Sequence[Path]] = None) -> List[TableDef]:
    defs: List[TableDef] = []
    for p in paths if paths is not None else _iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "CREATE TABLE" not in text.upper():
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        seen: Dict[str, int] = {}
        for m in _CREATE_HEAD.finditer(text):
            body = _balanced_body(text, m.end() - 1)
            if body is None:
                continue
            line = text.count("\n", 0, m.start()) + 1
            td = parse_table(m.group("name"), body, rel, line)
            if td is not None:
                td.ordinal = seen.get(td.table, 0)
                seen[td.table] = td.ordinal + 1
                defs.append(td)
    return defs


def record_schema() -> Dict[str, TableDef]:
    """The schema of record, keyed by bare table name."""
    if not RECORD_SQL.exists():
        return {}
    out: Dict[str, TableDef] = {}
    for td in scan([RECORD_SQL]):
        out.setdefault(td.table, td)
    return out


@dataclass
class Offence:
    site: str
    table: str
    path: str
    line: int
    missing_required: Tuple[str, ...]
    pk_expected: Tuple[str, ...]
    pk_found: Tuple[str, ...]

    @property
    def pk_conflict(self) -> bool:
        """A primary key that CONTRADICTS the record, not merely one the record
        does not state.

        `pg_dump` writes the key as a separate ``ALTER TABLE ... ADD CONSTRAINT``,
        so the record's inline pk is empty for most tables. Reporting "pk differs"
        against an empty expectation labelled 493 of 584 findings with a cause
        that was not theirs.
        """
        return bool(self.pk_expected) and bool(self.pk_found) and (
            self.pk_found != self.pk_expected
        )

    @property
    def kind(self) -> str:
        if self.missing_required and self.pk_conflict:
            return "missing_required+pk"
        return "missing_required" if self.missing_required else "pk"

    def reason(self) -> str:
        bits = []
        if self.missing_required:
            bits.append("omits NOT NULL " + ", ".join(self.missing_required))
        if self.pk_conflict:
            bits.append(
                f"primary key {'/'.join(self.pk_found)} "
                f"!= {'/'.join(self.pk_expected)}"
            )
        return "; ".join(bits)


def find_offences(defs: Sequence[TableDef], record: Dict[str, TableDef]) -> List[Offence]:
    out: List[Offence] = []
    for td in defs:
        if td.path == _rel(RECORD_SQL):
            continue
        rec = record.get(td.table)
        if rec is None:
            continue          # no schema of record for this table: nothing to compare
        missing = tuple(
            sorted(c.name for c in rec.columns.values()
                   if c.required and c.name not in td.columns)
        )
        pk_bad = bool(rec.pk) and bool(td.pk) and tuple(td.pk) != tuple(rec.pk)
        if not missing and not pk_bad:
            continue
        out.append(Offence(
            site=td.site, table=td.table, path=td.path, line=td.line,
            missing_required=missing,
            pk_expected=tuple(rec.pk), pk_found=tuple(td.pk),
        ))
    return sorted(out, key=lambda o: o.site)


def load_census() -> List[str]:
    if not CENSUS_FILE.exists():
        return []
    out = []
    for ln in CENSUS_FILE.read_text(encoding="utf-8").splitlines():
        s = ln.split("  #", 1)[0].strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _census_header(n: int) -> str:
    return f"""# CUI // SP-CTI
# Schema-drift census — CREATE TABLE sites that disagree with the schema of
# record (tools/db/schema/pg_consolidated.sql).
#
# Written by tools/ci/schema_drift_census.py. Each line is a SITE identity
# (path:line::table), not a count: a bare count can be held constant while the
# set churns, which is exactly the regression this census prevents.
#
# THIS FILE ONLY SHRINKS. `--check` fails on any offender not named here, so a
# NEW drift is a red build. Deleting a line is what fixing one looks like;
# adding a line is a deliberate act that belongs in its own PR with a reason.
#
# What an entry means: that CREATE TABLE would produce a table the schema of
# record's own writers cannot use — it omits a NOT NULL column with no default,
# or declares a different primary key. `CREATE TABLE IF NOT EXISTS` never
# ALTERS an existing table, so whichever definition runs first wins and the
# loser's columns never exist. That is green on a machine whose table predates
# the drift and red on a fresh database.
#
# To fix one: copy the canonical definition from pg_consolidated.sql, supply
# every required column at the insert sites, then run `--write`.
#
# {n} site(s) at adoption.
"""


def write_census(offences: Sequence[Offence]) -> None:
    lines = [_census_header(len(offences))]
    for o in offences:
        lines.append(f"{o.site}  # {o.reason()}")
    CENSUS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="gate on NEW drift")
    ap.add_argument("--list", action="store_true", help="list every offender")
    ap.add_argument("--write", action="store_true", help="regenerate the census")
    ap.add_argument("--table", metavar="NAME", help="only this table")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    record = record_schema()
    if not record:
        print(f"::error::schema drift census: no schema of record at "
              f"{_rel(RECORD_SQL)} — cannot run", file=sys.stderr)
        return 2

    offences = find_offences(scan(), record)
    if ns.table:
        want = ns.table.strip().lower()
        offences = [o for o in offences if o.table == want]

    if ns.json:
        print(json.dumps({
            "tables_in_record": len(record),
            "offences": [o.__dict__ | {"reason": o.reason()} for o in offences],
        }, indent=1, default=str))

    if ns.write:
        write_census(offences)
        print(f"schema drift census: wrote {len(offences)} site(s) to "
              f"{_rel(CENSUS_FILE)}")
        return 0

    if ns.list or ns.table:
        by_table: Dict[str, List[Offence]] = {}
        for o in offences:
            by_table.setdefault(o.table, []).append(o)
        for t in sorted(by_table):
            print(f"\n{t}  ({len(by_table[t])} site(s))")
            for o in by_table[t]:
                print(f"   {o.path}:{o.line}  {o.reason()}")
        print(f"\n{len(offences)} offending site(s) across {len(by_table)} table(s); "
              f"{len(record)} table(s) in the schema of record.")
        return 0

    known = set(load_census())
    new = [o for o in offences if o.site not in known]
    fixed = sorted(known - {o.site for o in offences})

    if ns.check:
        if new:
            print(f"::error::schema drift census: {len(new)} CREATE TABLE site(s) "
                  f"disagree with the schema of record and are not in the census. "
                  f"`CREATE TABLE IF NOT EXISTS` never ALTERS an existing table, so "
                  f"whichever definition runs first wins — this is green on your "
                  f"machine and red on a fresh database. Copy the canonical "
                  f"definition from "
                  f"{_rel(RECORD_SQL)}:", file=sys.stderr)
            for o in new:
                print(f"  {o.path}:{o.line}  {o.table}: {o.reason()}", file=sys.stderr)
            return 1
        msg = (f"Schema drift census: {len(offences)} known site(s), 0 new "
               f"({len(record)} tables in the schema of record).")
        if fixed:
            msg += (f" {len(fixed)} census entr(y/ies) no longer drift — "
                    f"run --write to remove them.")
        print(msg)
        return 0

    print(f"Schema drift census: {len(offences)} offending site(s), "
          f"{len(new)} not in the census, {len(record)} tables in the record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
