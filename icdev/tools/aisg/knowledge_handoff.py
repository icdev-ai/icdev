"""
AISG knowledge package extractor.

Bundles a user's audit history, fine-tuning patterns, and args overrides into
a portable ZIP for handoff or compliance archival.
"""

from __future__ import annotations

import json
import subprocess
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.db.storage import get_connection, table_exists

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARGS_DIR = _REPO_ROOT / "args"
_AUDIT_WINDOW_DAYS = 90


def export_package(user_email: str, output_path: Path) -> dict:
    """
    Build a knowledge_package.zip at output_path for user_email.

    Returns a summary dict with counts of extracted records.
    """
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    zip_path = output_path / "knowledge_package.zip"

    audit_records = _extract_audit(user_email)
    patterns = _extract_patterns(user_email)
    args_snapshot = _extract_args_snapshot(user_email)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("audit_summary.json", json.dumps(audit_records, indent=2, default=str))
        zf.writestr("patterns.json", json.dumps(patterns, indent=2, default=str))
        zf.writestr("args_snapshot.yaml", yaml.dump(args_snapshot, default_flow_style=False))

    return {
        "zip_path": str(zip_path),
        "audit_entries": len(audit_records),
        "pattern_entries": len(patterns),
        "args_files": len(args_snapshot),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Import (round-trip counterpart of export_package)
# ---------------------------------------------------------------------------

def import_package(zip_path: str | Path, new_user_email: str) -> dict:
    """Import a knowledge_package.zip produced by :func:`export_package`.

    Only the fine-tuning ``patterns.json`` payload is *restored* into the
    database: it is re-materialised as a new ``ft_datasets`` row owned by
    ``new_user_email`` plus its ``ft_training_pairs``. This is genuinely round-
    trip compatible — a subsequent ``export_package(new_user_email)`` re-emits
    the same pairs.

    The other two members are intentionally NOT written back:

    * ``audit_summary.json`` — the ``audit_trail`` is append-only / immutable
      (NIST AU). Re-inserting historical rows under a different actor would
      forge another user's audit history, so it is counted and reported only.
    * ``args_snapshot.yaml`` — repository config, data-only. It is not persisted
      to the DB; the count is reported so the caller sees what the package held.

    Returns a summary dict. Raises ``FileNotFoundError`` if the zip is missing
    and ``ValueError`` if it is not a readable knowledge package.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists() or not zip_path.is_file():
        raise FileNotFoundError(f"Knowledge package not found: {zip_path}")
    if not (new_user_email or "").strip():
        raise ValueError("new_user_email is required")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            patterns = (
                json.loads(zf.read("patterns.json").decode("utf-8"))
                if "patterns.json" in names else []
            )
            audit = (
                json.loads(zf.read("audit_summary.json").decode("utf-8"))
                if "audit_summary.json" in names else []
            )
            args_snapshot = (
                yaml.safe_load(zf.read("args_snapshot.yaml").decode("utf-8"))
                if "args_snapshot.yaml" in names else {}
            )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Not a valid knowledge package: {exc}") from exc

    if not isinstance(patterns, list):
        raise ValueError("patterns.json is malformed (expected a list)")

    dataset_id = f"ds-handoff-{uuid.uuid4().hex[:12]}"
    imported = _import_patterns(new_user_email, dataset_id, patterns)

    return {
        "status": "ok",
        "new_user_email": new_user_email,
        "dataset_id": dataset_id if imported else None,
        "imported_patterns": imported,
        # append-only audit trail is never re-written — reported, not imported
        "skipped_audit_entries": len(audit) if isinstance(audit, list) else 0,
        # config snapshot is data-only — not persisted to the DB
        "args_files_in_package": len(args_snapshot) if isinstance(args_snapshot, dict) else 0,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


def _import_patterns(new_user_email: str, dataset_id: str, patterns: list) -> int:
    """Persist exported training pairs under a new dataset owned by the user.

    Returns the number of ``ft_training_pairs`` rows inserted.
    """
    if not patterns:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        if not table_exists(conn, "ft_training_pairs"):
            return 0
        _ensure_dataset(conn, dataset_id, new_user_email, len(patterns), now)
        inserted = 0
        for p in patterns:
            if not isinstance(p, dict):
                continue
            conn.execute(
                "INSERT INTO ft_training_pairs "
                "(id, dataset_id, instruction, input_text, output_text, "
                " purpose, approved, source, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"ftp-{uuid.uuid4().hex[:12]}",
                    dataset_id,
                    p.get("instruction", "") or "",
                    p.get("input_text", "") or "",
                    p.get("output_text", "") or "",
                    p.get("purpose", "general") or "general",
                    int(p.get("approved") or 0),
                    f"handoff:{new_user_email}",
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def _ensure_dataset(
    conn: Any, dataset_id: str, new_user_email: str, count: int, now: str
) -> None:
    """Create the owning ft_datasets row so a re-export finds the pairs.

    Best-effort: if the ft_datasets table is absent the pairs are still imported
    (they simply won't be re-discoverable via ``created_by`` on re-export).
    """
    if not table_exists(conn, "ft_datasets"):
        return
    conn.execute(
        "INSERT INTO ft_datasets "
        "(id, name, description, purpose, created_by, example_count, "
        " status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            dataset_id,
            f"Imported handoff for {new_user_email}",
            "Fine-tuning patterns restored from a knowledge handoff package.",
            "general",
            new_user_email,
            count,
            "draft",
            now,
            now,
        ),
    )


def _extract_audit(user_email: str) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_AUDIT_WINDOW_DAYS)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, project_id, event_type, actor, action, details, "
            "affected_files, classification, session_id, created_at "
            "FROM audit_trail "
            "WHERE actor = %s AND created_at >= %s "
            "ORDER BY created_at DESC",
            (user_email, cutoff.isoformat()),
        ).fetchall()
        keys = [
            "id", "project_id", "event_type", "actor", "action",
            "details", "affected_files", "classification", "session_id", "created_at",
        ]
        return [dict(zip(keys, row)) for row in rows]
    finally:
        conn.close()


def _extract_patterns(user_email: str) -> list[dict[str, Any]]:
    """
    Return fine-tuning patterns linked to user sessions.

    Joins ft_datasets (created_by) → ft_training_pairs (dataset_id) and also
    falls back to ft_training_pairs rows whose source matches the email or whose
    dataset_id appears in the user's audit trail session activity.
    """
    conn = get_connection()
    try:
        session_ids = _user_session_ids(conn, user_email)

        rows: list[tuple] = []

        # Primary: datasets the user created
        ds_rows = conn.execute(
            "SELECT id FROM ft_datasets WHERE created_by = %s",
            (user_email,),
        ).fetchall()
        dataset_ids = [r[0] for r in ds_rows]

        # Also collect dataset_ids referenced in the user's audit sessions
        if session_ids:
            placeholders = ",".join("?" * len(session_ids))
            audit_details = conn.execute(
                "SELECT details FROM audit_trail WHERE session_id IN (" + placeholders + ")",  # nosec B608
                session_ids,
            ).fetchall()
            for (details_json,) in audit_details:
                try:
                    d = json.loads(details_json or "{}")
                    if isinstance(d.get("dataset_id"), str):
                        dataset_ids.append(d["dataset_id"])
                except (json.JSONDecodeError, TypeError):
                    pass

        dataset_ids = list(dict.fromkeys(dataset_ids))  # deduplicate, preserve order

        if dataset_ids:
            placeholders = ",".join("?" * len(dataset_ids))
            rows = conn.execute(
                "SELECT id, dataset_id, instruction, input_text, output_text, "
                "purpose, approved, source, created_at "
                "FROM ft_training_pairs WHERE dataset_id IN (" + placeholders + ")",  # nosec B608
                dataset_ids,
            ).fetchall()

        keys = [
            "id", "dataset_id", "instruction", "input_text", "output_text",
            "purpose", "approved", "source", "created_at",
        ]
        return [dict(zip(keys, row)) for row in rows]
    finally:
        conn.close()


def _user_session_ids(conn: Any, user_email: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM audit_trail "
        "WHERE actor = %s AND session_id IS NOT NULL AND session_id != ''",
        (user_email,),
    ).fetchall()
    return [r[0] for r in rows]


def _extract_args_snapshot(user_email: str) -> dict[str, Any]:
    """
    Return a dict mapping args/*.yaml filenames to their parsed content,
    limited to files touched by user_email in git history.
    """
    touched = _args_files_by_author(user_email)
    snapshot: dict[str, Any] = {}
    for fname in touched:
        fpath = _ARGS_DIR / fname
        if fpath.exists():
            try:
                with fpath.open(encoding="utf-8") as fh:
                    snapshot[fname] = yaml.safe_load(fh) or {}
            except Exception:
                snapshot[fname] = None
    return snapshot


def _args_files_by_author(user_email: str) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--author={user_email}",
                "--name-only",
                "--pretty=format:",
                "--", "args/*.yaml",
            ],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        files: list[str] = []
        seen: set[str] = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("args/") and line.endswith(".yaml"):
                fname = line[len("args/"):]
                if fname not in seen:
                    seen.add(fname)
                    files.append(fname)
        return files
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export AISG knowledge package")
    parser.add_argument("--email", required=True, help="User email to extract data for")
    parser.add_argument("--output", default=".", help="Directory to write knowledge_package.zip")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args()

    result = export_package(args.email, Path(args.output))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Package written to: {result['zip_path']}")
        print(f"  Audit entries : {result['audit_entries']}")
        print(f"  Pattern entries: {result['pattern_entries']}")
        print(f"  Args files     : {result['args_files']}")
