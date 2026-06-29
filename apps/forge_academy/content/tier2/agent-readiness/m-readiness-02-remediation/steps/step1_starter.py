"""STIG marker injector — adds V-ID comments to relevant functions."""
import pathlib
import sys

# Common STIG V-IDs mapped to code patterns
STIG_MAPPINGS = {
    "auth":     "# STIG V-220132: Application must enforce account lockout",
    "session":  "# STIG V-220133: Application must enforce session timeout",
    "password": "# STIG V-220160: Application must not store plaintext credentials",
    "input":    "# STIG V-220141: Application must validate all input",
    "audit":    "# STIG V-220138: Application must generate audit records",
    "encrypt":  "# STIG V-220167: Application must use FIPS 140-2 encryption",
}

PATTERN_KEYWORDS = {
    "auth":     {"login", "authenticate", "auth", "credential"},
    "session":  {"session", "token", "timeout", "expire"},
    "password": {"password", "passwd", "hash_password", "check_password"},
    "input":    {"validate", "sanitize", "parse_input", "clean"},
    "audit":    {"audit", "log_event", "record", "append_audit"},
    "encrypt":  {"encrypt", "decrypt", "cipher", "fernet"},
}


def find_functions_needing_markers(filepath: pathlib.Path) -> list:
    """Return list of {line, name, stig_comment} for functions that need markers."""
    # TODO: parse AST, check function names against PATTERN_KEYWORDS
    # TODO: return list of dicts with line number, function name, and STIG comment to add
    return []


def inject_markers(filepath: pathlib.Path, dry_run: bool = True) -> int:
    """Inject STIG markers. Returns number of markers added."""
    targets = find_functions_needing_markers(filepath)
    if dry_run:
        for t in targets:
            print(f"  Line {t['line']}: {t['name']} → {t['stig_comment']}")
        return len(targets)
    # TODO: write markers to file
    return 0


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
    dry_run = "--apply" not in sys.argv
    total = 0
    for py_file in target.rglob("*.py"):
        count = inject_markers(py_file, dry_run=dry_run)
        if count:
            print(f"{py_file}: {count} markers {'(dry run)' if dry_run else 'added'}")
            total += count
    print(f"\nTotal: {total} markers {'would be added' if dry_run else 'added'}")
