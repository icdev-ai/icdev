#!/usr/bin/env python3
# CUI // SP-CTI
"""Conflict resolver — strategy pattern for sync conflicts (D-SYNC-5).

Strategies:
  - last_write_wins: newer mtime wins
  - rename_both: rename both files with .conflict-{timestamp} suffix
  - source_wins: source always overwrites (default for push mode)
  - skip: skip conflicting files, log them
"""

import time
from typing import Dict, List

# Strategy constants
STRATEGY_LAST_WRITE_WINS = "last_write_wins"
STRATEGY_RENAME_BOTH = "rename_both"
STRATEGY_SOURCE_WINS = "source_wins"
STRATEGY_NEWEST_WINS = "newest_wins"  # Alias for last_write_wins
STRATEGY_SKIP = "skip"

VALID_STRATEGIES = {
    STRATEGY_LAST_WRITE_WINS,
    STRATEGY_RENAME_BOTH,
    STRATEGY_SOURCE_WINS,
    STRATEGY_NEWEST_WINS,
    STRATEGY_SKIP,
}


def resolve_conflicts(actions: List[Dict], strategy: str = STRATEGY_SOURCE_WINS) -> List[Dict]:
    """Resolve conflict actions according to the configured strategy.

    Args:
        actions: Action list from change_detector (may contain ACTION_CONFLICT entries).
        strategy: Conflict resolution strategy name.

    Returns:
        Modified action list with conflicts resolved into concrete actions.
    """
    if strategy == STRATEGY_NEWEST_WINS:
        strategy = STRATEGY_LAST_WRITE_WINS

    if strategy not in VALID_STRATEGIES:
        strategy = STRATEGY_SOURCE_WINS

    resolved = []
    for action in actions:
        if action["action"] != "conflict":
            resolved.append(action)
            continue

        resolution = _resolve_single(action, strategy)
        resolved.extend(resolution)

    return resolved


def _resolve_single(conflict: Dict, strategy: str) -> List[Dict]:
    """Resolve a single conflict action.

    Returns one or more replacement actions.
    """
    path = conflict["relative_path"]
    src_hash = conflict.get("source_hash")
    dst_hash = conflict.get("dest_hash")
    src_mtime = conflict.get("source_mtime", 0)
    dst_mtime = conflict.get("dest_mtime", 0)
    size = conflict.get("size", 0)

    if strategy == STRATEGY_SOURCE_WINS:
        return [
            {
                "action": "update",
                "relative_path": path,
                "reason": "conflict_resolved_source_wins",
                "source_hash": src_hash,
                "dest_hash": dst_hash,
                "size": size,
                "direction": "source_to_dest",
                "resolution": "source_wins",
            }
        ]

    elif strategy == STRATEGY_LAST_WRITE_WINS:
        if src_mtime >= dst_mtime:
            return [
                {
                    "action": "update",
                    "relative_path": path,
                    "reason": "conflict_resolved_newer_source",
                    "source_hash": src_hash,
                    "dest_hash": dst_hash,
                    "size": size,
                    "direction": "source_to_dest",
                    "resolution": "source_wins",
                }
            ]
        else:
            return [
                {
                    "action": "update",
                    "relative_path": path,
                    "reason": "conflict_resolved_newer_dest",
                    "source_hash": src_hash,
                    "dest_hash": dst_hash,
                    "size": size,
                    "direction": "dest_to_source",
                    "resolution": "dest_wins",
                }
            ]

    elif strategy == STRATEGY_RENAME_BOTH:
        ts = int(time.time())
        # Split path into name and extension
        if "." in path.split("/")[-1]:
            base, ext = path.rsplit(".", 1)
            src_renamed = f"{base}.conflict-source-{ts}.{ext}"
            dst_renamed = f"{base}.conflict-dest-{ts}.{ext}"
        else:
            src_renamed = f"{path}.conflict-source-{ts}"
            dst_renamed = f"{path}.conflict-dest-{ts}"

        return [
            {
                "action": "rename",
                "relative_path": path,
                "new_path": src_renamed,
                "reason": "conflict_rename_source",
                "source_hash": src_hash,
                "dest_hash": dst_hash,
                "size": size,
                "direction": "source_to_dest",
                "resolution": "renamed",
            },
            {
                "action": "rename",
                "relative_path": path,
                "new_path": dst_renamed,
                "reason": "conflict_rename_dest",
                "source_hash": src_hash,
                "dest_hash": dst_hash,
                "size": size,
                "direction": "dest_to_source",
                "resolution": "renamed",
            },
        ]

    elif strategy == STRATEGY_SKIP:
        return [
            {
                "action": "skip",
                "relative_path": path,
                "reason": "conflict_skipped",
                "source_hash": src_hash,
                "dest_hash": dst_hash,
                "size": size,
                "resolution": "skipped",
            }
        ]

    # Fallback: source wins
    return [
        {
            "action": "update",
            "relative_path": path,
            "reason": "conflict_resolved_fallback",
            "source_hash": src_hash,
            "dest_hash": dst_hash,
            "size": size,
            "direction": "source_to_dest",
            "resolution": "source_wins",
        }
    ]
