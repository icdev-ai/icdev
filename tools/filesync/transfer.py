#!/usr/bin/env python3
# CUI // SP-CTI
"""File transfer executor — ThreadPoolExecutor with bandwidth throttling (D-SYNC-6, D-SYNC-10).

Pattern: tools/databridge/scale/engine.py for ThreadPoolExecutor orchestration.
Bandwidth throttle via time.sleep() between chunks (zero deps).
Pre-transfer compression via zlib/gzip (D-SYNC-14).
"""

import gzip
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from tools.filesync.providers.base import SyncTargetProvider


class TransferResult:
    """Result of a single file transfer operation."""

    __slots__ = ("relative_path", "success", "bytes_transferred", "duration_ms", "error", "action")

    def __init__(
        self,
        relative_path: str,
        success: bool,
        bytes_transferred: int = 0,
        duration_ms: int = 0,
        error: str = "",
        action: str = "copy",
    ):
        self.relative_path = relative_path
        self.success = success
        self.bytes_transferred = bytes_transferred
        self.duration_ms = duration_ms
        self.error = error
        self.action = action

    def to_dict(self) -> Dict:
        return {
            "relative_path": self.relative_path,
            "success": self.success,
            "bytes_transferred": self.bytes_transferred,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "action": self.action,
        }


def _compress(data: bytes, method: str = "none", level: int = 6) -> bytes:
    """Compress data before transfer (decompress at dest is transparent — we write original).

    This compresses for bandwidth estimation only when source and dest are remote.
    For local-to-local, compression is skipped. The actual bytes written are always
    the original uncompressed data (dest gets the real file).

    For bandwidth throttling purposes, we compute throttle delay based on compressed size.
    """
    if method == "zlib":
        return zlib.compress(data, level)
    elif method == "gzip":
        return gzip.compress(data, compresslevel=level)
    return data


def transfer_file(
    source_provider: SyncTargetProvider,
    source_base: str,
    dest_provider: SyncTargetProvider,
    dest_base: str,
    relative_path: str,
    verify: bool = True,
    bandwidth_limit_kbps: int = 0,
    compression: str = "none",
    compression_level: int = 6,
) -> TransferResult:
    """Transfer a single file from source to destination.

    Args:
        source_provider: Source backend.
        source_base: Source root path.
        dest_provider: Destination backend.
        dest_base: Destination root path.
        relative_path: File path relative to base.
        verify: If True, re-read after write and verify hash matches.
        bandwidth_limit_kbps: Bandwidth limit in KB/s (0 = unlimited).
        compression: Pre-transfer compression method (none, zlib, gzip).
        compression_level: Compression level 1-9.

    Returns:
        TransferResult with success/error details.
    """
    t0 = time.monotonic()
    try:
        data = source_provider.read_file(source_base, relative_path)
        if data is None:
            return TransferResult(relative_path, False, error="Source file not found")

        original_size = len(data)

        # Bandwidth throttle: use compressed size for throttle calculation
        if bandwidth_limit_kbps > 0:
            throttle_data = _compress(data, compression, compression_level) if compression != "none" else data
            bytes_per_sec = bandwidth_limit_kbps * 1024
            transfer_time = len(throttle_data) / bytes_per_sec
            if transfer_time > 0:
                time.sleep(transfer_time)

        # Always write original uncompressed data to dest
        ok = dest_provider.write_file(dest_base, relative_path, data)
        if not ok:
            return TransferResult(relative_path, False, error="Write failed")

        # Verify transfer
        if verify:
            import hashlib

            src_hash = hashlib.sha256(data).hexdigest()
            written = dest_provider.read_file(dest_base, relative_path)
            if written is None:
                return TransferResult(relative_path, False, error="Verify read-back failed")
            dst_hash = hashlib.sha256(written).hexdigest()
            if src_hash != dst_hash:
                return TransferResult(
                    relative_path, False, error=f"Hash mismatch after transfer: {src_hash} != {dst_hash}"
                )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return TransferResult(
            relative_path, True, bytes_transferred=original_size, duration_ms=elapsed_ms, action="copy"
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return TransferResult(relative_path, False, duration_ms=elapsed_ms, error=str(e))


def delete_file(provider: SyncTargetProvider, base_path: str, relative_path: str) -> TransferResult:
    """Delete a file from destination."""
    t0 = time.monotonic()
    try:
        ok = provider.delete_file(base_path, relative_path)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if ok:
            return TransferResult(relative_path, True, duration_ms=elapsed_ms, action="delete")
        return TransferResult(
            relative_path, False, duration_ms=elapsed_ms, error="Delete returned false", action="delete"
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return TransferResult(relative_path, False, duration_ms=elapsed_ms, error=str(e), action="delete")


def rename_file(provider: SyncTargetProvider, base_path: str, old_path: str, new_path: str) -> TransferResult:
    """Rename a file (for conflict resolution)."""
    t0 = time.monotonic()
    try:
        ok = provider.rename_file(base_path, old_path, new_path)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if ok:
            return TransferResult(old_path, True, duration_ms=elapsed_ms, action="rename")
        return TransferResult(old_path, False, duration_ms=elapsed_ms, error="Rename returned false", action="rename")
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return TransferResult(old_path, False, duration_ms=elapsed_ms, error=str(e), action="rename")


def execute_actions(
    actions: List[Dict],
    source_provider: SyncTargetProvider,
    source_base: str,
    dest_provider: SyncTargetProvider,
    dest_base: str,
    max_workers: int = 4,
    verify: bool = True,
    bandwidth_limit_kbps: int = 0,
    dry_run: bool = False,
    progress_callback: Optional[Callable] = None,
    compression: str = "none",
    compression_level: int = 6,
) -> List[TransferResult]:
    """Execute a list of sync actions using ThreadPoolExecutor.

    Args:
        actions: Action list from change_detector/conflict_resolver.
        source_provider: Source backend.
        source_base: Source root path.
        dest_provider: Destination backend.
        dest_base: Destination root path.
        max_workers: Thread pool size.
        verify: Verify transfers with hash comparison.
        bandwidth_limit_kbps: Bandwidth limit in KB/s.
        dry_run: If True, return planned results without executing.
        progress_callback: Optional callback(result: TransferResult) per completed file.

    Returns:
        List of TransferResult objects.
    """
    if dry_run:
        return [
            TransferResult(a["relative_path"], True, bytes_transferred=a.get("size", 0), action=a["action"])
            for a in actions
            if a["action"] not in ("skip", "unchanged")
        ]

    results = []
    futures = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for action in actions:
            act = action["action"]
            path = action["relative_path"]

            if act in ("copy", "update"):
                direction = action.get("direction", "source_to_dest")
                if direction == "dest_to_source":
                    future = executor.submit(
                        transfer_file,
                        dest_provider,
                        dest_base,
                        source_provider,
                        source_base,
                        path,
                        verify,
                        bandwidth_limit_kbps,
                        compression,
                        compression_level,
                    )
                else:
                    future = executor.submit(
                        transfer_file,
                        source_provider,
                        source_base,
                        dest_provider,
                        dest_base,
                        path,
                        verify,
                        bandwidth_limit_kbps,
                        compression,
                        compression_level,
                    )
                futures[future] = action

            elif act == "delete":
                direction = action.get("direction", "source_to_dest")
                if direction == "dest_to_source":
                    future = executor.submit(delete_file, source_provider, source_base, path)
                else:
                    future = executor.submit(delete_file, dest_provider, dest_base, path)
                futures[future] = action

            elif act == "rename":
                new_path = action.get("new_path", path)
                direction = action.get("direction", "source_to_dest")
                if direction == "dest_to_source":
                    future = executor.submit(rename_file, source_provider, source_base, path, new_path)
                else:
                    future = executor.submit(rename_file, dest_provider, dest_base, path, new_path)
                futures[future] = action

            elif act == "skip":
                results.append(TransferResult(path, True, action="skip"))

        # Collect results
        for future in as_completed(futures):
            result = future.result()
            result.action = futures[future]["action"]
            results.append(result)
            if progress_callback:
                progress_callback(result)

    return results
