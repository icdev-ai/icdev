#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""SBOM Frequency and Accommodation of Updates — the 2026 practices (sbx-prc-02).

Two of the seven Practices and Processes elements live here, because they are the
same mechanism seen from two directions.

FREQUENCY

    "Every software version or update has an associated SBOM. A new build or
    release means a new SBOM, including builds that merely integrate updated
    dependencies."

What ICDEV enforced before this module was a 30-day file-mtime threshold
(``sbom_max_age_days: 30``, under both ``sbd`` and ``swft`` in
``args/security_gates.yaml``) while CLAUDE.md separately claimed "SBOM
regenerated on every build". Those are different rules and the weaker one was
the one with teeth: a project could ship six releases in a fortnight off one
SBOM and pass. Age is a *backstop* for evidence rot, not a statement about
builds, and it cannot become one without recording which build each SBOM came
from — hence ``sbom_records.source_revision``, and hence
:func:`evaluate_frequency`, which reports the per-build answer first and falls
back to age only where build identity is genuinely unavailable.

ACCOMMODATION OF UPDATES (major update from 2021's "Accommodation of Mistakes")

    "Accommodate updates including corrections; correct errors promptly."

The 2021 tolerance for immature data is explicitly withdrawn — recipients may
now weigh SBOM errors in their risk decisions *about the producer*. So a
correction has to be a first-class, recorded act, not a quiet overwrite.

    A correction NEVER mutates the SBOM it corrects.

:func:`apply_correction` inserts a *successor* row carrying
``supersedes_sbom_id`` pointing back at the row it replaces, and appends an
``audit_trail`` event. The predecessor's own columns are not touched — not even
a "superseded" flag, because flipping one would rewrite history that a recipient
may already hold a copy of, and because the flag is derivable: a row is
superseded exactly when some other row points at it. :func:`revision_chain`
computes that at read time and marks it there. That is the whole reason the
linkage is a forward pointer on the successor rather than a status column on the
predecessor.

CONTENT DIGEST, AND WHY IT IS NOT THE FILE HASH

Every regeneration mints a new ``serialNumber`` and a new ``metadata.timestamp``
even when the component set is byte-for-byte the same, so the file hash of two
SBOMs of one unchanged tree never matches. :func:`content_digest` strips exactly
the fields that turn over per emission and digests what is left. That makes
"did the bill of materials actually change?" answerable, which is what
distinguishes a *revision* (new components, corrected data) from a *re-issue*
(same content, new build). Frequency requires a new SBOM for both; only the
first is a substantive change, and only the digest can tell them apart.

Note the division of labour with sbx-fld-01, which owns the SBOM Version
element: the version bump on regeneration is the generator's, computed there.
:func:`next_sbom_version` exists for the one path fld-01 does not cover — a
correction, which is a *patch* bump of an existing version rather than the next
revision — and it reads both the legacy ``"3.0"`` float spelling and the
``"1.2.0"`` semver spelling so it works either side of that merge.

Usage:
  python tools/compliance/sbom_revision.py --project-id proj-test --chain --json
  python tools/compliance/sbom_revision.py --project-id proj-test --frequency --json
  python tools/compliance/sbom_revision.py --project-id proj-test --correct \\
      --reason "component producer was wrong" --sbom path/to/corrected.cdx.json --json
"""

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 -- git metadata read, fixed argv, no shell
import sys
from tools.db.storage import column_exists, get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# The major version of an SBOM following the 2026 elements "should be 1"
# (§ SBOM Version). Kept identical to sbx-fld-01's constant of the same name so
# the two spellings of a version agree whichever lands first.
SBOM_VERSION_MAJOR = 1

# Why a given sbom_records row exists. The DDL carries NO CHECK constraint
# against this list on purpose — the house rule is that a CHECK vocabulary is
# derived from a Python constant rather than hand-written in DDL, and this is
# that constant. Validated at the single writer instead.
REVISION_INITIAL = "initial"
REVISION_NEW_BUILD = "new_build"
REVISION_DEPENDENCY_CHANGE = "dependency_change"
REVISION_CORRECTION = "correction"
REVISION_DETAIL_DISCOVERED = "detail_discovered"

REVISION_REASONS = (
    REVISION_INITIAL,
    REVISION_NEW_BUILD,
    REVISION_DEPENDENCY_CHANGE,
    REVISION_CORRECTION,
    REVISION_DETAIL_DISCOVERED,
)

# Reasons that describe a *revision* of existing data rather than a re-issue for
# a new build. The standard's sentence "newly discovered component detail, or a
# correction to existing data, means a REVISED SBOM" is exactly this set.
REVISION_REASONS_CORRECTIVE = (
    REVISION_CORRECTION,
    REVISION_DETAIL_DISCOVERED,
)

# Columns this task added to sbom_records (migration
# 20260808063350_sbom_revision_frequency). Writers guard on column_exists so a
# database that has not migrated still records its row.
SBOM_RECORD_REVISION_COLUMNS = (
    "content_digest",
    "source_revision",
    "revision_reason",
    "supersedes_sbom_id",
)

# Document fields that turn over on every emission regardless of content. Removed
# before digesting — see the module docstring.
VOLATILE_TOP_LEVEL = ("serialNumber", "version")
VOLATILE_METADATA = ("timestamp",)
VOLATILE_PROPERTIES = (
    "icdev:sbom:version",
    "icdev:sbom:timestamp",
    "icdev:sbom:serial-number",
)

# Gate condition names, matching args/security_gates.yaml. Returned by
# evaluate_frequency so a caller reports the same strings the config declares.
CONDITION_MISSING = "sbom_missing"
CONDITION_NOT_CURRENT_BUILD = "sbom_not_regenerated_for_current_build"
CONDITION_STALE = "sbom_stale_over_30_days"
CONDITION_BUILD_UNKNOWN = "sbom_build_identity_unknown"

DEFAULT_MAX_AGE_DAYS = 30


# -----------------------------------------------------------------
# Content identity
# -----------------------------------------------------------------


def content_digest(sbom_document):
    """Digest an SBOM's *content*, ignoring what changes on every emission.

    Returns ``"sha256:<hex>"``. Two SBOMs of the same dependency tree produce the
    same digest even though their serial numbers, timestamps and versions all
    differ; changing one component name, version, producer, hash or license
    changes it.

    Not the file hash. The file hash of two SBOMs of one unchanged tree never
    matches, so it cannot answer the question this exists to answer. Byte-level
    integrity is the SBOM Author Signature's job (sbx-sig-01), which digests the
    artifact itself.
    """
    doc = json.loads(json.dumps(sbom_document))  # deep copy, JSON-shaped input only

    for field in VOLATILE_TOP_LEVEL:
        doc.pop(field, None)

    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        for field in VOLATILE_METADATA:
            metadata.pop(field, None)
        properties = metadata.get("properties")
        if isinstance(properties, list):
            metadata["properties"] = [
                p for p in properties if not (isinstance(p, dict) and p.get("name") in VOLATILE_PROPERTIES)
            ]

    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_revision(project_dir=None, build_id=None):
    """Identify the build this SBOM describes, or None if it cannot be known.

    Precedence: an explicit caller-supplied ``build_id`` (a CI pipeline id is
    more meaningful than a commit when the two disagree), then the git commit of
    ``project_dir``, then None.

    None is a real answer and is reported as ``sbom_build_identity_unknown``
    rather than being smoothed into "current". A gate that guessed here would be
    asserting per-build conformance it cannot see.
    """
    if build_id:
        return str(build_id)

    env_build = os.environ.get("ICDEV_BUILD_ID")
    if env_build:
        return env_build

    if not project_dir:
        return None
    directory = Path(project_dir)
    if not directory.is_dir():
        return None

    try:
        result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell, git only
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def next_sbom_version(prior_version, *, correction=False):
    """The SBOM Version element that follows ``prior_version``.

    ``correction=True`` bumps the patch — a correction revises a version that was
    already published for a build, so it is not a new revision of the software.
    Otherwise the minor bumps, which is the per-build/per-release case.

    Both spellings of a prior version are read: the semver ``"1.<minor>.<patch>"``
    that sbx-fld-01 writes, and the legacy ``"<N>.0"`` float that predates it.
    The major is pinned to 1 per the standard's guidance for SBOMs following
    these elements.
    """
    text = "" if prior_version is None else str(prior_version).strip()
    parts = text.split(".") if text else []

    minor, patch = 0, 0
    try:
        if len(parts) >= 3 and int(parts[0]) == SBOM_VERSION_MAJOR:
            minor, patch = int(parts[1]), int(parts[2])
        elif parts:
            # Legacy "3.0" stands for revision 3, i.e. semver minor 2.
            minor = max(int(float(text)) - 1, 0)
    except (TypeError, ValueError):
        minor, patch = 0, 0

    if correction:
        patch += 1
    else:
        minor += 1
        patch = 0
    return f"{SBOM_VERSION_MAJOR}.{minor}.{patch}"


# -----------------------------------------------------------------
# Reading the chain
# -----------------------------------------------------------------


def _record_columns(conn):
    """Which of this task's columns the live sbom_records actually has."""
    return {col: column_exists(conn, "sbom_records", col) for col in SBOM_RECORD_REVISION_COLUMNS}


def _select_records(conn, project_id):
    """All sbom_records rows for a project, oldest first, as plain dicts."""
    available = _record_columns(conn)
    columns = ["id", "project_id", "version", "format", "file_path", "component_count", "generated_at"]
    for col in ("sbom_version", "serial_number"):
        if column_exists(conn, "sbom_records", col):
            columns.append(col)
    columns.extend(col for col, present in available.items() if present)

    # The column list is built from module constants filtered through
    # column_exists; no user input reaches the SQL text, and project_id is bound.
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM sbom_records WHERE project_id = %s ORDER BY id",  # nosec B608
        (project_id,),
    ).fetchall()
    return [{col: row[col] for col in columns} for row in rows]


def revision_chain(conn, project_id):
    """Every SBOM for a project, each marked with its place in the chain.

    ``superseded`` and ``superseded_by_id`` are computed here, at read time, from
    the successors' ``supersedes_sbom_id``. They are not columns. Marking a
    predecessor by writing to it would rewrite a document a recipient may already
    hold — see the module docstring — so the chain is append-only and the mark is
    derived.

    ``is_head`` is the row nothing supersedes and which is the newest such row:
    the SBOM currently in force.
    """
    records = _select_records(conn, project_id)
    superseded_by = {}
    for record in records:
        predecessor = record.get("supersedes_sbom_id")
        if predecessor:
            superseded_by[int(predecessor)] = record["id"]

    for record in records:
        successor = superseded_by.get(int(record["id"]))
        record["superseded"] = successor is not None
        record["superseded_by_id"] = successor

    live = [r for r in records if not r["superseded"]]
    head_id = live[-1]["id"] if live else None
    for record in records:
        record["is_head"] = record["id"] == head_id

    return records


def latest_record(conn, project_id):
    """The SBOM currently in force for a project, or None if there is none."""
    for record in reversed(revision_chain(conn, project_id)):
        if record["is_head"]:
            return record
    return None


# -----------------------------------------------------------------
# Writing the chain
# -----------------------------------------------------------------


def plan_revision(conn, project_id, sbom_document, *, reason=None, project_dir=None, build_id=None):
    """Work out how a newly built SBOM relates to what is already recorded.

    Returns a dict the generator drops straight into its INSERT plus enough
    context to describe the event:

      ``supersedes_sbom_id`` — the row this one replaces (the current head), or
      None for a project's first SBOM.
      ``content_digest`` / ``source_revision`` — as recorded on the new row.
      ``revision_reason`` — inferred when the caller does not state one:
      ``initial`` for the first SBOM, ``dependency_change`` when the content
      digest moved, ``new_build`` when it did not. Frequency requires a new SBOM
      either way; the reason is what tells a reader which happened.
      ``content_changed`` — False for a re-issue of identical content.
      ``predecessor_digest`` — for reporting the comparison that was made.
    """
    predecessor = latest_record(conn, project_id)
    digest = content_digest(sbom_document)
    predecessor_digest = predecessor.get("content_digest") if predecessor else None

    # A predecessor with no recorded digest predates this migration, so "changed"
    # is unknowable — treat it as changed rather than silently claiming a re-issue.
    content_changed = True if predecessor_digest is None else predecessor_digest != digest

    if reason is None:
        if predecessor is None:
            reason = REVISION_INITIAL
        elif content_changed:
            reason = REVISION_DEPENDENCY_CHANGE
        else:
            reason = REVISION_NEW_BUILD
    if reason not in REVISION_REASONS:
        raise ValueError(f"Unknown revision reason '{reason}'. Expected one of: {', '.join(REVISION_REASONS)}")

    return {
        "supersedes_sbom_id": predecessor["id"] if predecessor else None,
        "content_digest": digest,
        "source_revision": source_revision(project_dir=project_dir, build_id=build_id),
        "revision_reason": reason,
        "content_changed": content_changed,
        "predecessor_digest": predecessor_digest,
    }


def revision_insert_fields(conn, plan):
    """Split a revision plan into (columns, values) for the columns that exist.

    Returns ``(columns, values, unpersisted)``. A database that has not run
    migration 20260808063350_sbom_revision_frequency still records its row; the
    caller reports ``unpersisted`` rather than raising, which is the same
    degradation sbx-fld-01 applies to the metadata block.
    """
    available = _record_columns(conn)
    columns, values, unpersisted = [], [], []
    for col in SBOM_RECORD_REVISION_COLUMNS:
        if available[col]:
            columns.append(col)
            values.append(plan.get(col))
        elif plan.get(col) is not None:
            unpersisted.append(col)
    return columns, values, unpersisted


def log_revision_event(conn, project_id, plan, record_id, *, event_type="sbom_revised", extra=None):
    """Append the revision to audit_trail. Append-only: never updates a prior row."""
    details = {
        "sbom_record_id": record_id,
        "supersedes_sbom_id": plan.get("supersedes_sbom_id"),
        "revision_reason": plan.get("revision_reason"),
        "content_digest": plan.get("content_digest"),
        "predecessor_digest": plan.get("predecessor_digest"),
        "content_changed": plan.get("content_changed"),
        "source_revision": plan.get("source_revision"),
    }
    if extra:
        details.update(extra)
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                event_type,
                "icdev-compliance-engine",
                f"SBOM revision recorded ({plan.get('revision_reason')})",
                json.dumps(details),
                json.dumps([]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:  # audit logging must not break the artifact path
        print(f"Warning: Could not log SBOM revision event: {e}", file=sys.stderr)


def apply_correction(
    conn,
    project_id,
    corrected_document,
    *,
    reason,
    output_path=None,
    corrections=None,
    reason_code=REVISION_CORRECTION,
    project_dir=None,
    build_id=None,
):
    """Record a corrected SBOM as a successor to the one it corrects.

    This is the Accommodation of Updates element's write path. It inserts a new
    ``sbom_records`` row whose ``supersedes_sbom_id`` points at the current head
    and appends an ``sbom_corrected`` audit event. **It issues no UPDATE and no
    DELETE against sbom_records** — the corrected row keeps every value it had,
    including its file path, signature and component count, because a recipient
    may hold that exact document and its record has to keep describing it.

    Args:
      corrected_document: the fixed SBOM, as a dict.
      reason: free text — what was wrong. Goes to the audit trail.
      output_path: where to write the corrected document. Defaults to a sibling
        of the predecessor's file, suffixed with the new version.
      corrections: optional list of ``{"field", "was", "now"}`` descriptors for
        the audit record. The standard asks that corrections be accommodated and
        made promptly; it does not prescribe a schema for describing them.
      reason_code: ``correction`` (an error is being fixed) or
        ``detail_discovered`` (new detail, nothing was wrong).

    Returns a dict describing the new row, including ``superseded_sbom_id`` and
    the new ``sbom_version``.
    """
    if reason_code not in REVISION_REASONS_CORRECTIVE:
        raise ValueError(
            f"apply_correction reason_code must be one of {', '.join(REVISION_REASONS_CORRECTIVE)}; got '{reason_code}'"
        )
    if not reason or not str(reason).strip():
        raise ValueError("A correction must state what was wrong; 'reason' is required.")

    predecessor = latest_record(conn, project_id)
    if predecessor is None:
        raise ValueError(
            f"No SBOM recorded for project '{project_id}' — there is nothing to correct. "
            "Run: python tools/compliance/sbom_generator.py --project-id " + project_id
        )

    prior_version = predecessor.get("sbom_version") or predecessor.get("version")
    new_version = next_sbom_version(prior_version, correction=True)

    if output_path:
        out_file = Path(output_path)
    else:
        prior_path = Path(str(predecessor["file_path"]))
        suffix = "".join(prior_path.suffixes) or ".json"
        stem = prior_path.name[: -len(suffix)] if suffix and prior_path.name.endswith(suffix) else prior_path.stem
        out_file = prior_path.with_name(f"{stem}.rev{new_version}{suffix}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(corrected_document, f, indent=2)

    plan = plan_revision(
        conn,
        project_id,
        corrected_document,
        reason=reason_code,
        project_dir=project_dir,
        build_id=build_id,
    )
    # plan_revision resolves the head itself; assert it agreed, so a concurrent
    # writer cannot silently make this correction supersede the wrong row.
    if plan["supersedes_sbom_id"] != predecessor["id"]:
        raise RuntimeError(
            f"SBOM head moved while the correction was being prepared "
            f"(expected {predecessor['id']}, found {plan['supersedes_sbom_id']}). Retry."
        )

    # `version` is the legacy revision counter and `sbom_version` is the 2026 SBOM
    # Version element; a correction moves the second, not the first. It carries the
    # predecessor's `version` VERBATIM for two reasons. Semantically, a correction
    # revises a document already published for a build — it is not a new revision of
    # the software, so it belongs to the same counter position. Mechanically, the
    # generator derives the next counter value from this column: on PostgreSQL that
    # is still `MAX(CAST(version AS REAL))` until sbx-fld-01 replaces it, and writing
    # a three-part "1.1.1" here would make CAST raise on the very next generation.
    columns = ["project_id", "version", "format", "file_path", "component_count", "vulnerability_count"]
    values = [
        project_id,
        predecessor.get("version"),
        predecessor.get("format") or "cyclonedx",
        str(out_file),
        len(corrected_document.get("components") or []),
        0,
    ]
    if column_exists(conn, "sbom_records", "sbom_version"):
        columns.append("sbom_version")
        values.append(new_version)
    if column_exists(conn, "sbom_records", "serial_number"):
        columns.append("serial_number")
        values.append(corrected_document.get("serialNumber"))

    revision_columns, revision_values, unpersisted = revision_insert_fields(conn, plan)
    columns.extend(revision_columns)
    values.extend(revision_values)
    if unpersisted:
        print(
            f"Warning: sbom_records is missing {', '.join(unpersisted)} — the correction was "
            "recorded but its link to the SBOM it supersedes was not. "
            "Run: python tools/db/migrate.py",
            file=sys.stderr,
        )

    placeholders = ", ".join(["%s"] * len(columns))
    # The column list is module constants filtered through column_exists; every
    # value is bound, not interpolated.
    cursor = conn.execute(
        f"INSERT INTO sbom_records ({', '.join(columns)}) VALUES ({placeholders})",  # nosec B608
        tuple(values),
    )
    conn.commit()

    new_id = getattr(cursor, "lastrowid", None)
    if not new_id:
        row = conn.execute(
            "SELECT id FROM sbom_records WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        new_id = row["id"] if row else None

    log_revision_event(
        conn,
        project_id,
        plan,
        new_id,
        event_type="sbom_corrected",
        extra={
            "correction_reason": str(reason),
            "corrections": corrections or [],
            "corrected_sbom_version": prior_version,
            "sbom_version": new_version,
            "file_path": str(out_file),
        },
    )

    return {
        "sbom_record_id": new_id,
        "superseded_sbom_id": predecessor["id"],
        "sbom_version": new_version,
        "corrected_sbom_version": prior_version,
        "file_path": str(out_file),
        "content_digest": plan["content_digest"],
        "content_changed": plan["content_changed"],
        "revision_reason": reason_code,
        "reason": str(reason),
        "corrections": corrections or [],
    }


# -----------------------------------------------------------------
# Frequency evaluation — what the gate actually asks
# -----------------------------------------------------------------


def gate_threshold(key, gate="sbd", default=None):
    """Read one threshold out of args/security_gates.yaml.

    Read rather than hardcoded because the value is configuration, and it was
    previously duplicated as a literal 30 inside the very check that enforced it
    — which is how the config and the code were free to drift apart.

    Three nesting shapes are in use in that file and the right one depends on the
    gate: `sbd`'s thresholds live under the file-level `thresholds:` block, while
    `swft` carries its own nested `thresholds:`. Both are tried, plus the flat
    form, because guessing one and falling through to the default is
    indistinguishable from reading the config and finding the default there.
    """
    try:
        import yaml

        with open(BASE_DIR / "args" / "security_gates.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return default

    section = config.get(gate) or {}
    for candidate in (
        (config.get("thresholds") or {}).get(gate) or {},  # thresholds.sbd.*
        section.get("thresholds") or {},  # swft.thresholds.*
        section,  # sbd.*
    ):
        if isinstance(candidate, dict) and key in candidate:
            return candidate[key]
    return default


def _load_max_age_days(gate="sbd"):
    """sbom_max_age_days for a gate, or DEFAULT_MAX_AGE_DAYS if unconfigured."""
    value = gate_threshold("sbom_max_age_days", gate=gate)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_DAYS


def _parse_timestamp(value):
    """Parse a DB timestamp to an aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluate_frequency(conn, project_id, *, project_dir=None, build_id=None, max_age_days=None, gate="sbd"):
    """Does this project satisfy the Frequency element right now?

    The per-build question is asked first and the age question second, which is
    the reconciliation this task exists to make: "a new build or release means a
    new SBOM" is the requirement, and 30-day age is the backstop for evidence
    that has simply gone stale.

    Returns a dict with ``status`` (``satisfied`` / ``partially_satisfied`` /
    ``not_satisfied``), ``conditions`` (gate condition names from
    ``args/security_gates.yaml``), plus the evidence a report can print.

    A build whose identity cannot be determined yields
    ``sbom_build_identity_unknown`` and *partially* satisfied at best — it is not
    a pass, because per-build conformance was not observed, and not a failure,
    because nothing observed contradicts it.
    """
    threshold = int(max_age_days) if max_age_days is not None else _load_max_age_days(gate)
    head = latest_record(conn, project_id)

    if head is None:
        return {
            "status": "not_satisfied",
            "conditions": [CONDITION_MISSING],
            "max_age_days": threshold,
            "evidence": "No SBOM recorded for this project.",
            "details": (
                "The 2026 Minimum Elements require an SBOM for every software version "
                "or update. Run: python tools/compliance/sbom_generator.py "
                f"--project-id {project_id}"
            ),
            "record": None,
        }

    conditions = []
    notes = []

    current_revision = source_revision(project_dir=project_dir, build_id=build_id)
    recorded_revision = head.get("source_revision")
    if current_revision and recorded_revision:
        if current_revision == recorded_revision:
            notes.append(f"SBOM v{head.get('sbom_version') or head.get('version')} matches build {current_revision[:12]}.")
        else:
            conditions.append(CONDITION_NOT_CURRENT_BUILD)
            notes.append(
                f"Latest SBOM was generated from build {str(recorded_revision)[:12]}, "
                f"but the current build is {current_revision[:12]}."
            )
    else:
        conditions.append(CONDITION_BUILD_UNKNOWN)
        if not recorded_revision:
            notes.append("The latest SBOM has no recorded source revision, so per-build conformance cannot be shown.")
        else:
            notes.append("The current build could not be identified, so per-build conformance cannot be shown.")

    generated_at = _parse_timestamp(head.get("generated_at"))
    age_days = None
    if generated_at is None:
        notes.append("The latest SBOM has no usable generation timestamp.")
    else:
        age_days = (datetime.now(timezone.utc) - generated_at).days
        if age_days > threshold:
            conditions.append(CONDITION_STALE)
            notes.append(f"Latest SBOM is {age_days} days old (threshold {threshold}).")
        else:
            notes.append(f"Latest SBOM is {age_days} days old (threshold {threshold}).")

    if CONDITION_NOT_CURRENT_BUILD in conditions or CONDITION_STALE in conditions:
        status = "not_satisfied"
    elif CONDITION_BUILD_UNKNOWN in conditions:
        status = "partially_satisfied"
    else:
        status = "satisfied"

    chain = revision_chain(conn, project_id)
    return {
        "status": status,
        "conditions": conditions,
        "max_age_days": threshold,
        "age_days": age_days,
        "current_revision": current_revision,
        "recorded_revision": recorded_revision,
        "sbom_count": len(chain),
        "superseded_count": sum(1 for r in chain if r["superseded"]),
        "evidence": (
            f"{len(chain)} SBOM(s) recorded; latest is "
            f"v{head.get('sbom_version') or head.get('version')} "
            f"({head.get('revision_reason') or 'reason not recorded'})."
        ),
        "details": " ".join(notes),
        "record": head,
    }


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------


def _get_connection(db_path=None):
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    return get_connection(db_path=str(path))


def main():
    parser = argparse.ArgumentParser(
        description="SBOM Frequency and Accommodation of Updates (2026 Minimum Elements, sbx-prc-02)"
    )
    parser.add_argument("--project-id", "--project", required=True, dest="project_id", help="Project ID")
    parser.add_argument("--chain", action="store_true", help="Show the SBOM revision chain")
    parser.add_argument("--frequency", action="store_true", help="Evaluate the Frequency element")
    parser.add_argument("--correct", action="store_true", help="Record a corrected SBOM superseding the current one")
    parser.add_argument("--sbom", help="Path to the corrected SBOM document (with --correct)")
    parser.add_argument("--reason", help="What was wrong (required with --correct)")
    parser.add_argument(
        "--reason-code",
        default=REVISION_CORRECTION,
        choices=list(REVISION_REASONS_CORRECTIVE),
        help="correction (an error is fixed) or detail_discovered (new detail)",
    )
    parser.add_argument("--project-dir", help="Project directory, for git build identity")
    parser.add_argument("--build-id", help="Explicit build identifier, overriding git")
    parser.add_argument("--output", help="Where to write the corrected SBOM (with --correct)")
    parser.add_argument("--db", help="Database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if not (args.chain or args.frequency or args.correct):
        args.chain = True

    conn = _get_connection(args.db)
    try:
        result = {}
        if args.correct:
            if not args.sbom:
                parser.error("--correct requires --sbom (the corrected SBOM document)")
            if not args.reason:
                parser.error("--correct requires --reason (what was wrong)")
            with open(args.sbom, encoding="utf-8") as f:
                corrected = json.load(f)
            result["correction"] = apply_correction(
                conn,
                args.project_id,
                corrected,
                reason=args.reason,
                reason_code=args.reason_code,
                output_path=args.output,
                project_dir=args.project_dir,
                build_id=args.build_id,
            )
        if args.chain:
            result["chain"] = revision_chain(conn, args.project_id)
        if args.frequency:
            result["frequency"] = evaluate_frequency(
                conn,
                args.project_id,
                project_dir=args.project_dir,
                build_id=args.build_id,
            )

        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "correction" in result:
                c = result["correction"]
                print(f"Correction recorded as SBOM v{c['sbom_version']} (row {c['sbom_record_id']}).")
                print(f"  Supersedes: row {c['superseded_sbom_id']} (v{c['corrected_sbom_version']})")
                print(f"  File:       {c['file_path']}")
                print(f"  Reason:     {c['reason']}")
            for record in result.get("chain", []):
                mark = "HEAD" if record["is_head"] else ("superseded" if record["superseded"] else "-")
                print(
                    f"  [{mark:>10}] row {record['id']:>4}  "
                    f"v{record.get('sbom_version') or record.get('version')}  "
                    f"{record.get('revision_reason') or 'reason not recorded'}  "
                    f"{record.get('generated_at')}"
                )
            if "frequency" in result:
                freq = result["frequency"]
                print(f"\nFrequency: {freq['status'].upper()}")
                print(f"  {freq['evidence']}")
                print(f"  {freq['details']}")
                if freq["conditions"]:
                    print(f"  Conditions: {', '.join(freq['conditions'])}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
