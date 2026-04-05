#!/usr/bin/env python3
# CUI // SP-CTI
"""Change detector — diff source/dest manifests to produce sync action plan.

Compares two manifests from scanner.scan_directory() and generates a list
of actions needed to bring destination in sync with source.
"""

from typing import Dict, List

# Action types
ACTION_COPY = "copy"  # New file: exists in source, not in dest
ACTION_UPDATE = "update"  # Modified: exists in both, hash differs
ACTION_DELETE = "delete"  # Removed: exists in dest, not in source
ACTION_CONFLICT = "conflict"  # Bidirectional: changed on both sides
ACTION_UNCHANGED = "unchanged"  # No change needed


def detect_changes_push(
    source_manifest: Dict[str, Dict], dest_manifest: Dict[str, Dict], delete_orphans: bool = False
) -> List[Dict]:
    """Detect changes for one-way push (source → dest).

    Args:
        source_manifest: {path: {hash, size, mtime_epoch}} from source scan.
        dest_manifest: {path: {hash, size, mtime_epoch}} from dest scan.
        delete_orphans: If True, mark dest-only files for deletion.

    Returns:
        List of action dicts: {action, relative_path, reason, source_hash, dest_hash, size}.
    """
    actions = []

    # Files in source
    for path, src_info in source_manifest.items():
        if path not in dest_manifest:
            actions.append(
                {
                    "action": ACTION_COPY,
                    "relative_path": path,
                    "reason": "new_file",
                    "source_hash": src_info["hash"],
                    "dest_hash": None,
                    "size": src_info["size"],
                }
            )
        else:
            dst_info = dest_manifest[path]
            if src_info["hash"] != dst_info["hash"]:
                actions.append(
                    {
                        "action": ACTION_UPDATE,
                        "relative_path": path,
                        "reason": "content_changed",
                        "source_hash": src_info["hash"],
                        "dest_hash": dst_info["hash"],
                        "size": src_info["size"],
                    }
                )
            # else: unchanged, skip

    # Files only in dest (orphans)
    if delete_orphans:
        for path in dest_manifest:
            if path not in source_manifest:
                actions.append(
                    {
                        "action": ACTION_DELETE,
                        "relative_path": path,
                        "reason": "orphan_in_dest",
                        "source_hash": None,
                        "dest_hash": dest_manifest[path]["hash"],
                        "size": dest_manifest[path]["size"],
                    }
                )

    return actions


def detect_changes_bidirectional(
    source_manifest: Dict[str, Dict],
    dest_manifest: Dict[str, Dict],
    source_prev: Dict[str, Dict],
    dest_prev: Dict[str, Dict],
) -> List[Dict]:
    """Detect changes for bidirectional sync.

    Requires previous state (from last successful sync) to determine
    which side changed. If both changed → conflict.

    Args:
        source_manifest: Current source state.
        dest_manifest: Current dest state.
        source_prev: Source state from last sync.
        dest_prev: Dest state from last sync.

    Returns:
        List of action dicts with additional 'direction' field (source_to_dest / dest_to_source).
    """
    actions = []
    all_paths = set(source_manifest.keys()) | set(dest_manifest.keys())

    for path in all_paths:
        src_now = source_manifest.get(path)
        dst_now = dest_manifest.get(path)
        src_old = source_prev.get(path)
        dst_old = dest_prev.get(path)

        src_changed = _has_changed(src_now, src_old)
        dst_changed = _has_changed(dst_now, dst_old)

        if src_changed and dst_changed:
            # Both sides changed — conflict
            actions.append(
                {
                    "action": ACTION_CONFLICT,
                    "relative_path": path,
                    "reason": "both_sides_changed",
                    "source_hash": src_now["hash"] if src_now else None,
                    "dest_hash": dst_now["hash"] if dst_now else None,
                    "source_mtime": src_now["mtime_epoch"] if src_now else None,
                    "dest_mtime": dst_now["mtime_epoch"] if dst_now else None,
                    "source_size": src_now["size"] if src_now else None,
                    "dest_size": dst_now["size"] if dst_now else None,
                    "size": (src_now or dst_now or {}).get("size", 0),
                    "direction": None,
                }
            )
        elif src_changed and not dst_changed:
            # Source changed, propagate to dest
            if src_now and not dst_now:
                act = ACTION_COPY
            elif src_now and dst_now:
                act = ACTION_UPDATE
            else:
                act = ACTION_DELETE
            actions.append(
                {
                    "action": act,
                    "relative_path": path,
                    "reason": "source_changed",
                    "source_hash": src_now["hash"] if src_now else None,
                    "dest_hash": dst_now["hash"] if dst_now else None,
                    "size": (src_now or {}).get("size", 0),
                    "direction": "source_to_dest",
                }
            )
        elif dst_changed and not src_changed:
            # Dest changed, propagate to source
            if dst_now and not src_now:
                act = ACTION_COPY
            elif dst_now and src_now:
                act = ACTION_UPDATE
            else:
                act = ACTION_DELETE
            actions.append(
                {
                    "action": act,
                    "relative_path": path,
                    "reason": "dest_changed",
                    "source_hash": src_now["hash"] if src_now else None,
                    "dest_hash": dst_now["hash"] if dst_now else None,
                    "size": (dst_now or {}).get("size", 0),
                    "direction": "dest_to_source",
                }
            )
        # else: neither changed, skip

    return actions


def _has_changed(current: dict, previous: dict) -> bool:
    """Check if a file has changed between two states."""
    if current is None and previous is None:
        return False
    if current is None or previous is None:
        return True  # File added or removed
    return current.get("hash") != previous.get("hash")


def summarize_actions(actions: List[Dict]) -> Dict:
    """Summarize action plan into counts."""
    summary = {
        "total": len(actions),
        "copy": 0,
        "update": 0,
        "delete": 0,
        "conflict": 0,
        "bytes_to_transfer": 0,
    }
    for a in actions:
        act = a["action"]
        if act in summary:
            summary[act] += 1
        if act in (ACTION_COPY, ACTION_UPDATE):
            summary["bytes_to_transfer"] += a.get("size", 0)
    return summary
