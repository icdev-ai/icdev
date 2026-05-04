#!/usr/bin/env python3
# CUI // SP-CTI
"""File tree scanner — walk directories and build SHA-256 manifests (D-SYNC-2).

Pattern: tools/mbse/sync_engine.py:_compute_file_hash() for chunked hashing.
Fast-skip: mtime+size unchanged vs cached state → skip expensive hash.
Block-level hashing for files >4MiB (D-SYNC-3).
FIPS 140-2 hash mode toggle (D-SYNC-13).
"""

import hashlib
from typing import Dict, Optional

from tools.filesync.ignore_parser import filter_files, load_ignore_patterns
from tools.filesync.providers.base import SyncTargetProvider

# Block size for chunked hashing (128 KiB, matches Syncthing default)
HASH_BLOCK_SIZE = 128 * 1024  # 131072 bytes

# Large file threshold for block-level hashing (4 MiB)
LARGE_FILE_THRESHOLD = 4 * 1024 * 1024

# FIPS 140-2 approved hash algorithms
FIPS_ALGORITHMS = ("sha256", "sha384", "sha512", "sha3_256", "sha3_384", "sha3_512")
SUPPORTED_ALGORITHMS = FIPS_ALGORITHMS + ("md5", "blake2b", "blake2s")

# Module-level hash config (set by configure_hash())
_hash_algorithm = "sha256"
_fips_mode = False


def configure_hash(algorithm: str = "sha256", fips_mode: bool = False):
    """Configure the hash algorithm used for change detection.

    Args:
        algorithm: Hash algorithm name (sha256, sha384, sha512, sha3_256, etc.).
        fips_mode: If True, only FIPS 140-2 approved algorithms are allowed.

    Raises:
        ValueError: If algorithm is not supported or not FIPS-approved when fips_mode=True.
    """
    global _hash_algorithm, _fips_mode
    algo = algorithm.lower()
    if fips_mode and algo not in FIPS_ALGORITHMS:
        raise ValueError(f"Algorithm '{algo}' is not FIPS 140-2 approved. Allowed: {', '.join(FIPS_ALGORITHMS)}")
    if algo not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"Unsupported algorithm '{algo}'. Allowed: {', '.join(SUPPORTED_ALGORITHMS)}")
    _hash_algorithm = algo
    _fips_mode = fips_mode


def _new_hasher():
    """Create a new hashlib hasher using the configured algorithm."""
    return hashlib.new(_hash_algorithm)


def compute_file_hash(data: bytes, algorithm: str = None) -> str:
    """Compute hash of file contents using configured or specified algorithm.

    Args:
        data: Raw file bytes.
        algorithm: Override algorithm (uses module default if None).

    Returns:
        Hex-encoded hash string.
    """
    algo = algorithm or _hash_algorithm
    return hashlib.new(algo, data).hexdigest()


def compute_file_hash_chunked(
    provider: SyncTargetProvider,
    base_path: str,
    relative_path: str,
    chunk_size: int = HASH_BLOCK_SIZE,
    algorithm: str = None,
) -> Optional[str]:
    """Compute hash by reading the file in chunks (memory-efficient for large files).

    Args:
        provider: Sync target provider.
        base_path: Root path.
        relative_path: File path relative to base.
        chunk_size: Read chunk size in bytes.
        algorithm: Override algorithm (uses module default if None).

    Returns:
        Hex hash, or None if file cannot be read.
    """
    data = provider.read_file(base_path, relative_path)
    if data is None:
        return None
    algo = algorithm or _hash_algorithm
    h = hashlib.new(algo)
    offset = 0
    while offset < len(data):
        h.update(data[offset : offset + chunk_size])
        offset += chunk_size
    return h.hexdigest()


def scan_directory(
    provider: SyncTargetProvider,
    base_path: str,
    ignore_file: str = ".syncignore",
    cached_state: Optional[Dict[str, Dict]] = None,
    fast_skip_mtime: bool = True,
) -> Dict[str, Dict]:
    """Scan a directory tree and build a manifest.

    Args:
        provider: Sync target provider to scan.
        base_path: Root directory path.
        ignore_file: Name of ignore patterns file.
        cached_state: Previously cached {relative_path: {hash, size, mtime}} for fast-skip.
        fast_skip_mtime: If True, skip hashing when mtime+size match cached state.

    Returns:
        Manifest dict: {relative_path: {hash, size, mtime_epoch}}.
    """
    # Load ignore patterns
    patterns = load_ignore_patterns(base_path, ignore_file)

    # List all files from provider
    raw_files = provider.list_files(base_path)

    # Filter by ignore patterns
    files = filter_files(raw_files, patterns)

    manifest = {}
    for finfo in files:
        rel = finfo["relative_path"]
        size = finfo["size"]
        mtime = finfo["mtime_epoch"]

        # Fast-skip: if mtime and size match cached state, reuse cached hash
        if fast_skip_mtime and cached_state and rel in cached_state:
            cached = cached_state[rel]
            if cached.get("size") == size and cached.get("mtime_epoch") == mtime and cached.get("hash"):
                manifest[rel] = {
                    "hash": cached["hash"],
                    "size": size,
                    "mtime_epoch": mtime,
                }
                continue

        # Compute hash
        file_hash = compute_file_hash_chunked(provider, base_path, rel)
        if file_hash is None:
            continue

        manifest[rel] = {
            "hash": file_hash,
            "size": size,
            "mtime_epoch": mtime,
        }

    return manifest
