"""Parse Keep-a-Changelog formatted CHANGELOG.md files."""

import re
from pathlib import Path


_VERSION_RE = re.compile(r"^##\s+\[([^\]]+)\]\s*[-–]\s*(\S+)", re.MULTILINE)
_SECTION_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^[-*]\s+(.+)", re.MULTILINE)


def parse_changelog(path: str | Path) -> list[dict]:
    """Return list of {version, date, sections} dicts from a Keep-a-Changelog file.

    Sections is a dict mapping heading name (e.g. 'Added') to list of strings.
    Unreleased block is included if present as version='Unreleased', date=None.
    """
    text = Path(path).read_text(encoding="utf-8")

    version_spans: list[tuple[str, str, int, int]] = []
    for m in _VERSION_RE.finditer(text):
        version_spans.append((m.group(1), m.group(2), m.start(), m.end()))

    releases: list[dict] = []
    for i, (version, date, _, block_start) in enumerate(version_spans):
        block_end = version_spans[i + 1][2] if i + 1 < len(version_spans) else len(text)
        block = text[block_start:block_end]

        sections: dict[str, list[str]] = {}
        section_matches = list(_SECTION_RE.finditer(block))
        for j, sec_m in enumerate(section_matches):
            sec_name = sec_m.group(1).strip()
            sec_end = section_matches[j + 1].start() if j + 1 < len(section_matches) else len(block)
            sec_body = block[sec_m.end():sec_end]
            bullets = [m.group(1).strip() for m in _BULLET_RE.finditer(sec_body)]
            if bullets:
                sections[sec_name] = bullets

        releases.append({
            "version": version,
            "date": None if version.lower() == "unreleased" else date,
            "sections": sections,
        })

    return releases
