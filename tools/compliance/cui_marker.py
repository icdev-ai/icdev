#!/usr/bin/env python3
# CUI // SP-CTI
"""Apply CUI classification markings to files.
Loads marking configuration from args/cui_markings.yaml and applies
appropriate banners/headers based on file type."""

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ARGS_DIR = BASE_DIR / "args"
CUI_CONFIG_PATH = ARGS_DIR / "cui_markings.yaml"

# Language-specific comment styles keyed by file extension
COMMENT_STYLES = {
    ".py":   {"line": "#",  "block_start": None, "block_end": None},
    ".js":   {"line": "//", "block_start": "/*", "block_end": "*/"},
    ".ts":   {"line": "//", "block_start": "/*", "block_end": "*/"},
    ".java": {"line": "//", "block_start": "/*", "block_end": "*/"},
    ".yaml": {"line": "#",  "block_start": None, "block_end": None},
    ".yml":  {"line": "#",  "block_start": None, "block_end": None},
    ".tf":   {"line": "#",  "block_start": "/*", "block_end": "*/"},
    ".go":   {"line": "//", "block_start": "/*", "block_end": "*/"},
    ".rs":   {"line": "//", "block_start": "/*", "block_end": "*/"},
    ".rb":   {"line": "#",  "block_start": None, "block_end": None},
    ".sh":   {"line": "#",  "block_start": None, "block_end": None},
    ".sql":  {"line": "--", "block_start": "/*", "block_end": "*/"},
    ".css":  {"line": None, "block_start": "/*", "block_end": "*/"},
}

# Document extensions that get banner treatment (top + bottom banners)
DOCUMENT_EXTENSIONS = {".md", ".txt", ".rst", ".adoc", ".html"}


def load_cui_config(config_path=None):
    """Load CUI marking configuration from YAML file.
    Falls back to built-in defaults if file is missing or pyyaml unavailable."""
    path = config_path or CUI_CONFIG_PATH

    # Default config matching the args/cui_markings.yaml structure
    default_config = {
        "banner_top": "CUI // SP-CTI",
        "banner_bottom": "CUI // SP-CTI",
        "designation_indicator": {
            "controlled_by": "Department of Defense",
            "categories": "CTI",
            "distribution": "Distribution D",
            "poc": "ICDEV System Administrator",
        },
        "portion_marking": "(CUI)",
        "decontrol_instructions": "Decontrol on: 10 years from creation date",
        "code_header": (
            "CUI // SP-CTI\n"
            "Controlled by: Department of Defense\n"
            "CUI Category: CTI\n"
            "Distribution: D\n"
            "POC: ICDEV System Administrator"
        ),
        "document_header": (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        ),
        "document_footer": (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        ),
    }

    if not path.exists():
        return default_config

    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if loaded:
            # Merge loaded values over defaults
            for key, value in loaded.items():
                default_config[key] = value
    except ImportError:
        # pyyaml not available -- parse the YAML manually for simple keys
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            _parse_simple_yaml(content, default_config)
        except Exception:
            pass
    except Exception:
        pass

    return default_config


def _parse_simple_yaml(content, config):
    """Minimal YAML-like parser for flat key: value and multiline | blocks."""
    lines = content.split("\n")
    current_key = None
    multiline_buf = []
    in_multiline = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if in_multiline:
                multiline_buf.append("")
            continue

        if in_multiline:
            if line and not line[0].isspace():
                # End of multiline block
                config[current_key] = "\n".join(multiline_buf).strip()
                in_multiline = False
                multiline_buf = []
            else:
                multiline_buf.append(line.strip())
                continue

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "|":
                current_key = key
                in_multiline = True
                multiline_buf = []
            elif value and not value.startswith("{"):
                value = value.strip('"').strip("'")
                config[key] = value

    if in_multiline and multiline_buf:
        config[current_key] = "\n".join(multiline_buf).strip()


def _build_code_header(config, ext):
    """Build the CUI header comment block for a source code file."""
    style = COMMENT_STYLES.get(ext)
    if not style:
        return None

    header_text = config.get("code_header", "CUI // SP-CTI")
    header_lines = header_text.strip().split("\n")

    line_comment = style["line"]
    block_start = style["block_start"]
    block_end = style["block_end"]

    result_lines = []

    if line_comment:
        # Use line-comment style
        separator = f"{line_comment} {'/' * 66}"
        result_lines.append(separator)
        for hl in header_lines:
            result_lines.append(f"{line_comment} {hl.strip()}")
        result_lines.append(separator)
    elif block_start and block_end:
        # Use block-comment style (e.g. CSS)
        result_lines.append(f"{block_start}")
        result_lines.append(f" * {'/' * 64}")
        for hl in header_lines:
            result_lines.append(f" * {hl.strip()}")
        result_lines.append(f" * {'/' * 64}")
        result_lines.append(f" {block_end}")

    return "\n".join(result_lines) + "\n"


def _has_cui_marking(content, config):
    """Check if content already contains a CUI marking."""
    banner = config.get("banner_top", "CUI // SP-CTI")
    return banner in content


def mark_document(file_path, config=None, dry_run=False):
    """Add CUI banners to a markdown or text document.
    Returns the path of the marked file, or None if already marked."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if config is None:
        config = load_cui_config()

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if _has_cui_marking(content, config):
        print(f"[SKIP] Already marked: {file_path}")
        return None

    header = config.get("document_header", "").strip()
    footer = config.get("document_footer", "").strip()

    # Handle shebang lines -- preserve them at top
    if content.startswith("#!"):
        first_newline = content.index("\n") + 1
        shebang = content[:first_newline]
        rest = content[first_newline:]
        marked = f"{shebang}\n{header}\n\n{rest.strip()}\n\n{footer}\n"
    else:
        marked = f"{header}\n\n{content.strip()}\n\n{footer}\n"

    if dry_run:
        print(f"[DRY RUN] Would mark: {file_path}")
        return file_path

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(marked)

    print(f"[MARKED] Document: {file_path}")
    return file_path


def mark_code_file(file_path, config=None, dry_run=False):
    """Add CUI header comment to a source code file.
    Detects language by file extension. Returns the path or None if already marked."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = file_path.suffix.lower()
    if ext not in COMMENT_STYLES:
        print(f"[SKIP] Unsupported extension: {ext} ({file_path})")
        return None

    if config is None:
        config = load_cui_config()

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if _has_cui_marking(content, config):
        print(f"[SKIP] Already marked: {file_path}")
        return None

    header_block = _build_code_header(config, ext)
    if header_block is None:
        print(f"[SKIP] Could not build header for: {file_path}")
        return None

    # Preserve shebang and encoding declarations
    lines = content.split("\n")
    prefix_lines = []
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped.startswith("#!"):
            prefix_lines.append(line)
            body_start = i + 1
        elif i <= 1 and stripped.startswith("# -*- coding"):
            prefix_lines.append(line)
            body_start = i + 1
        else:
            break

    prefix = "\n".join(prefix_lines)
    body = "\n".join(lines[body_start:])

    if prefix:
        marked = f"{prefix}\n{header_block}\n{body}"
    else:
        marked = f"{header_block}\n{body}"

    if dry_run:
        print(f"[DRY RUN] Would mark: {file_path}")
        return file_path

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(marked)

    print(f"[MARKED] Code file: {file_path}")
    return file_path


def mark_directory(dir_path, extensions=None, config=None, dry_run=False):
    """Recursively mark all matching files in a directory.
    Returns a list of paths that were marked."""
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    if config is None:
        config = load_cui_config()

    if extensions is None:
        # Default: all supported code + document extensions
        extensions = set(COMMENT_STYLES.keys()) | DOCUMENT_EXTENSIONS
    else:
        extensions = {e if e.startswith(".") else f".{e}" for e in extensions}

    marked_files = []
    skipped = 0
    errors = 0

    for root, dirs, files in os.walk(dir_path):
        # Skip hidden directories and common non-project directories
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   {"node_modules", "__pycache__", ".git", "venv", "env", ".tox", ".tmp"}]

        for fname in files:
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()

            if ext not in extensions:
                continue

            try:
                if ext in DOCUMENT_EXTENSIONS:
                    result = mark_document(fpath, config=config, dry_run=dry_run)
                elif ext in COMMENT_STYLES:
                    result = mark_code_file(fpath, config=config, dry_run=dry_run)
                else:
                    continue

                if result:
                    marked_files.append(str(result))
                else:
                    skipped += 1
            except Exception as e:
                print(f"[ERROR] {fpath}: {e}")
                errors += 1

    print(f"\nSummary: {len(marked_files)} marked, {skipped} skipped, {errors} errors")
    return marked_files


def main():
    parser = argparse.ArgumentParser(
        description="Apply CUI classification markings to files"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=str, help="Path to a single file to mark")
    group.add_argument("--directory", type=str, help="Path to a directory to recursively mark")
    parser.add_argument(
        "--extensions", type=str, default=None,
        help="Comma-separated list of extensions to process (e.g., .py,.js,.md)"
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to CUI markings YAML config (default: args/cui_markings.yaml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    config = load_cui_config(config_path)

    if args.file:
        file_path = Path(args.file)
        ext = file_path.suffix.lower()
        if ext in DOCUMENT_EXTENSIONS:
            result = mark_document(file_path, config=config, dry_run=args.dry_run)
        elif ext in COMMENT_STYLES:
            result = mark_code_file(file_path, config=config, dry_run=args.dry_run)
        else:
            print(f"[ERROR] Unsupported file type: {ext}")
            sys.exit(1)

        if result:
            print(f"Successfully marked: {result}")
        else:
            print("File was already marked or could not be processed.")

    elif args.directory:
        extensions = None
        if args.extensions:
            extensions = [e.strip() for e in args.extensions.split(",")]
        marked = mark_directory(
            args.directory, extensions=extensions, config=config, dry_run=args.dry_run
        )
        print(f"\nTotal files marked: {len(marked)}")
        for fp in marked:
            print(f"  {fp}")


if __name__ == "__main__":
    main()
