"""Updates engine — parses the changelog and serves release data."""
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def get_releases() -> list[dict[str, Any]]:
    """Return parsed releases from CHANGELOG.md."""
    try:
        from tools.dashboard.changelog import parse_changelog
        changelog_path = _ROOT / "CHANGELOG.md"
        if not changelog_path.exists():
            return []
        releases = parse_changelog(str(changelog_path))
        return [
            {
                "version": r.get("version", ""),
                "date": r.get("date", ""),
                "sections": r.get("sections", {}),
                "item_count": sum(len(v) for v in r.get("sections", {}).values()),
            }
            for r in (releases or [])
        ]
    except Exception:
        return []
