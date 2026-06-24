"""IQE adapter for Platform Updates canvas."""
from __future__ import annotations

from tools.iqe.executor import register_collection


def _updates_releases(query_ast, conn=None):
    """Return parsed CHANGELOG.md releases as IQE rows."""
    try:
        import os
        from tools.dashboard.changelog import parse_changelog
        changelog_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "CHANGELOG.md"
        )
        releases = parse_changelog(changelog_path)
        rows = []
        for rel in releases:
            rows.append({
                "version": rel.get("version", ""),
                "date": rel.get("date", ""),
                "sections": list(rel.get("sections", {}).keys()),
                "item_count": sum(len(v) for v in rel.get("sections", {}).values()),
            })
        return rows
    except Exception:
        return []


register_collection("updates.releases", _updates_releases)
