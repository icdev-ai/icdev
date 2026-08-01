# CUI // SP-CTI
"""Conflict timeline visualization engine.

Renders a play-by-play timeline of conflict events filtered for:
  - Encirclement tactics (territorial isolation / siege maneuvers)
  - Casualty events (killed / wounded)
  - Capture incidents (POW / detention)

Data source: sg_conflict_events (populated by acled_importer, oryx_importer, etc.)
Output: structured JSON for front-end chart rendering + HTML template fragment.

NIST 800-53: AU-9, SI-12
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.conflict_timeline")

# ── Marker filter definitions ─────────────────────────────────────────────
ENCIRCLEMENT_KEYWORDS = frozenset([
    "encirclement", "encircle", "encircled", "siege", "besieged", "surrounded",
    "cut off", "cordon", "blockade", "isolation", "isolate", "pocket", "cauldron",
    "maneuver", "flanking", "envelopment",
])

CASUALTY_KEYWORDS = frozenset([
    "killed", "dead", "death", "fatality", "fatalities", "wounded", "injured",
    "casualties", "casualty", "kia", "wia", "struck", "airstrike", "artillery",
    "bombardment", "shelling",
])

CAPTURE_KEYWORDS = frozenset([
    "captured", "capture", "prisoner", "pow", "detained", "detention",
    "surrendered", "surrender", "taken prisoner", "apprehended",
])

_MARKER_TYPE_MAP = {
    "encirclement": ENCIRCLEMENT_KEYWORDS,
    "casualty": CASUALTY_KEYWORDS,
    "capture": CAPTURE_KEYWORDS,
}

# ACLED sub-event types that always map to a marker
_SUB_EVENT_TYPE_MAP: dict[str, str] = {
    "shelling/artillery/missile attack": "casualty",
    "air/drone strike": "casualty",
    "remote explosive/landmine/ied": "casualty",
    "non-state actor overtakes territory": "encirclement",
    "government regains territory": "encirclement",
    "armed clash": "casualty",
    "abduction/forced disappearance": "capture",
}


def _classify_marker(event: dict[str, Any]) -> str | None:
    """Return the first matching marker type or None."""
    sub = (event.get("sub_event_type") or "").lower()
    if sub in _SUB_EVENT_TYPE_MAP:
        return _SUB_EVENT_TYPE_MAP[sub]

    blob = " ".join([
        str(event.get("event_type") or ""),
        str(event.get("description") or ""),
        str(event.get("notes") or ""),
        sub,
    ]).lower()

    for marker_type, kws in _MARKER_TYPE_MAP.items():
        if any(kw in blob for kw in kws):
            return marker_type
    return None


def fetch_timeline(
    theater_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Fetch and classify conflict events into timeline markers.

    Returns:
        {
          "markers": [{"id", "event_date", "marker_type", "description",
                       "lat", "lon", "fatalities", "source"}, ...],
          "summary": {"encirclement": N, "casualty": N, "capture": N, "total": N}
        }
    """
    clauses: list[str] = []
    params: list[Any] = []

    if theater_id:
        clauses.append("theater_id = ?")
        params.append(theater_id)
    if start_date:
        clauses.append("event_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("event_date <= ?")
        params.append(end_date)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT id, theater_id, event_type, sub_event_type, event_date, "
        f"latitude, longitude, fatalities, description, source "
        f"FROM sg_conflict_events {where} "
        f"ORDER BY event_date ASC, id ASC LIMIT ?"
    )
    params.append(limit)

    markers: list[dict[str, Any]] = []
    summary: dict[str, int] = {"encirclement": 0, "casualty": 0, "capture": 0, "total": 0}

    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception:
        logger.exception("Failed to query sg_conflict_events")
        return {"markers": markers, "summary": summary}

    for row in rows:
        ev = {
            "id": row[0],
            "theater_id": row[1],
            "event_type": row[2],
            "sub_event_type": row[3],
            "event_date": row[4],
            "lat": row[5],
            "lon": row[6],
            "fatalities": row[7] or 0,
            "description": row[8] or "",
            "source": row[9],
        }
        marker_type = _classify_marker(ev)
        if marker_type is None:
            continue

        markers.append({
            "id": ev["id"],
            "event_date": ev["event_date"],
            "marker_type": marker_type,
            "description": ev["description"],
            "lat": ev["lat"],
            "lon": ev["lon"],
            "fatalities": ev["fatalities"],
            "source": ev["source"],
        })
        summary[marker_type] = summary.get(marker_type, 0) + 1
        summary["total"] += 1

    return {"markers": markers, "summary": summary}


def render_timeline_html(timeline: dict[str, Any]) -> str:
    """Render timeline data as an HTML fragment for embedding in a dashboard template."""
    markers = timeline.get("markers", [])
    summary = timeline.get("summary", {})

    rows_html = ""
    for m in markers:
        badge_color = {
            "encirclement": "#ff6b35",
            "casualty": "#e63946",
            "capture": "#457b9d",
        }.get(m["marker_type"], "#888")

        rows_html += (
            f'<tr data-marker="{m["marker_type"]}">'
            f'<td>{m["event_date"]}</td>'
            f'<td><span class="badge" style="background:{badge_color}">'
            f'{m["marker_type"].upper()}</span></td>'
            f'<td>{m.get("description","")[:120]}</td>'
            f'<td class="text-right">{m.get("fatalities",0)}</td>'
            f'</tr>\n'
        )

    return f"""<!-- CUI // SP-CTI -->
<div class="conflict-timeline" id="conflict-timeline">
  <div class="timeline-summary mb-2">
    <span class="badge-stat encirclement">Encirclement: {summary.get('encirclement',0)}</span>
    <span class="badge-stat casualty">Casualties: {summary.get('casualty',0)}</span>
    <span class="badge-stat capture">Captures: {summary.get('capture',0)}</span>
    <span class="badge-stat total">Total: {summary.get('total',0)}</span>
  </div>
  <div class="timeline-filter mb-2">
    <button class="btn btn-sm" onclick="filterTimeline('all')">All</button>
    <button class="btn btn-sm" onclick="filterTimeline('encirclement')">Encirclement</button>
    <button class="btn btn-sm" onclick="filterTimeline('casualty')">Casualties</button>
    <button class="btn btn-sm" onclick="filterTimeline('capture')">Captures</button>
  </div>
  <table class="table table-sm timeline-table">
    <thead><tr><th>Date</th><th>Type</th><th>Description</th><th>Fatalities</th></tr></thead>
    <tbody id="timeline-tbody">
{rows_html}    </tbody>
  </table>
</div>
<script>
function filterTimeline(type) {{
  document.querySelectorAll('#timeline-tbody tr').forEach(function(row) {{
    row.style.display = (type === 'all' || row.dataset.marker === type) ? '' : 'none';
  }});
}}
</script>"""


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Conflict timeline engine")
    ap.add_argument("--theater", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--html", action="store_true")
    args = ap.parse_args()

    tl = fetch_timeline(args.theater, args.start, args.end, args.limit)
    if args.html:
        print(render_timeline_html(tl))
    else:
        print(json.dumps(tl, indent=2))
