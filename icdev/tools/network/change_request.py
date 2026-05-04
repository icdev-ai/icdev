# [CUI // SP-CTI]
"""ICDEV Network Design Canvas — Change Request Markup Engine.

Pure functions for managing change request markup on network topologies.
Engineers mark proposed changes in three action types:
  - add    (green)  : new nodes/edges to be added
  - remove (red)    : existing nodes/edges to be removed
  - modify (yellow) : existing nodes/edges to be changed

Generates structured CAB (Change Advisory Board) review documents with
before/after diffs for each proposed change.

No Flask dependency. All functions are deterministic and testable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


# ── Action Type Constants ──────────────────────────────────────────────────────

ACTION_TYPES = {
    "add": {
        "label": "Add",
        "color": "#27ae60",
        "bg": "#d5f5e3",
        "description": "Proposed new node or link to be added to the topology.",
    },
    "remove": {
        "label": "Remove",
        "color": "#e74c3c",
        "bg": "#fadbd8",
        "description": "Existing node or link proposed for removal.",
    },
    "modify": {
        "label": "Modify",
        "color": "#f39c12",
        "bg": "#fef9e7",
        "description": "Existing node or link with proposed attribute changes.",
    },
}

ENTITY_TYPES = ("node", "edge", "group", "boundary")
CR_STATUSES = ("draft", "submitted", "approved", "rejected", "withdrawn")


# ── Diff Helpers ───────────────────────────────────────────────────────────────


def _flatten_attrs(obj: dict[str, Any]) -> dict[str, Any]:
    """Return a flat key→value dict of meaningful display attributes."""
    flat: dict[str, Any] = {}
    skip = {"id", "source", "target", "vertices", "z", "angle"}
    for k, v in obj.items():
        if k in skip:
            continue
        if isinstance(v, dict):
            for subk, subv in v.items():
                if not isinstance(subv, (dict, list)):
                    flat[f"{k}.{subk}"] = subv
        elif not isinstance(v, list):
            flat[k] = v
    return flat


def compute_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return a list of field-level diff entries between before and after.

    Each entry: {"field": str, "before": any, "after": any, "change": "added|removed|changed"}
    """
    diffs: list[dict[str, Any]] = []
    b_flat = _flatten_attrs(before or {})
    a_flat = _flatten_attrs(after or {})
    all_keys = set(b_flat) | set(a_flat)
    for key in sorted(all_keys):
        b_val = b_flat.get(key)
        a_val = a_flat.get(key)
        if b_val == a_val:
            continue
        if b_val is None:
            change = "added"
        elif a_val is None:
            change = "removed"
        else:
            change = "changed"
        diffs.append({"field": key, "before": b_val, "after": a_val, "change": change})
    return diffs


# ── Change Request Document Generator ─────────────────────────────────────────


def generate_cr_document(
    cr: dict[str, Any],
    items: list[dict[str, Any]],
    topology_name: str,
    topology_classification: str = "CUI // SP-CTI",
) -> dict[str, Any]:
    """Generate a structured CAB review document from a change request and its items.

    Returns a dict with:
      - metadata: CR header info
      - summary: counts by action type
      - sections: one section per action type (add/remove/modify)
      - impact_analysis: risk flags
      - approval_block: signature placeholder fields
      - markdown: rendered Markdown string of the full document
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    adds = [i for i in items if i.get("action_type") == "add"]
    removes = [i for i in items if i.get("action_type") == "remove"]
    modifies = [i for i in items if i.get("action_type") == "modify"]

    # ── Impact analysis flags ──────────────────────────────────────────────
    impact_flags: list[str] = []
    if len(removes) > 0:
        impact_flags.append(f"{len(removes)} element(s) marked for removal — verify downstream dependencies.")
    if any(
        "firewall" in (i.get("entity_label") or "").lower() or "fw" in (i.get("entity_label") or "").lower()
        for i in removes + modifies
    ):
        impact_flags.append("Security boundary element (firewall) affected — ISSM review required.")
    if any(i.get("entity_type") == "edge" and i.get("action_type") == "remove" for i in items):
        impact_flags.append("Link removal detected — assess routing/redundancy impact.")
    if len(items) > 20:
        impact_flags.append("Large change set (>20 items) — consider phased implementation.")

    # ── Build sections ─────────────────────────────────────────────────────
    def _render_section(action_items: list[dict[str, Any]], action_type: str) -> dict[str, Any]:
        entries = []
        for item in action_items:
            before_data = item.get("before_json") or {}
            after_data = item.get("after_json") or {}
            if isinstance(before_data, str):
                try:
                    before_data = json.loads(before_data)
                except Exception:
                    before_data = {}
            if isinstance(after_data, str):
                try:
                    after_data = json.loads(after_data)
                except Exception:
                    after_data = {}
            diff = compute_diff(
                before_data if action_type != "add" else None,
                after_data if action_type != "remove" else None,
            )
            entries.append(
                {
                    "item_id": item.get("id"),
                    "entity_id": item.get("entity_id"),
                    "entity_type": item.get("entity_type", "node"),
                    "entity_label": item.get("entity_label", item.get("entity_id", "unknown")),
                    "justification": item.get("justification", ""),
                    "before": before_data,
                    "after": after_data,
                    "diff": diff,
                    "created_at": item.get("created_at", ""),
                    "created_by": item.get("created_by", ""),
                }
            )
        return {
            "action_type": action_type,
            "label": ACTION_TYPES[action_type]["label"],
            "color": ACTION_TYPES[action_type]["color"],
            "count": len(entries),
            "entries": entries,
        }

    sections = [
        _render_section(adds, "add"),
        _render_section(removes, "remove"),
        _render_section(modifies, "modify"),
    ]

    # ── Approval block ─────────────────────────────────────────────────────
    approval_block = {
        "submitter": {
            "name": cr.get("submitter_name", ""),
            "date": cr.get("submitted_at", ""),
            "signature": "___________________________",
        },
        "technical_reviewer": {
            "name": "",
            "date": "",
            "signature": "___________________________",
        },
        "cab_chair": {
            "name": "",
            "date": "",
            "signature": "___________________________",
        },
        "issm": {
            "name": "",
            "date": "",
            "signature": "___________________________",
        },
    }

    # ── Markdown render ────────────────────────────────────────────────────
    md_lines: list[str] = []
    md_lines.append(f"<!-- classification: {topology_classification} -->")
    md_lines.append(f"# Change Request: {cr.get('title', 'Untitled CR')}")
    md_lines.append("")
    md_lines.append(f"**CR ID:** `{cr.get('id', '')}`  ")
    md_lines.append(f"**Topology:** {topology_name}  ")
    md_lines.append(f"**Classification:** {topology_classification}  ")
    md_lines.append(f"**Status:** {cr.get('status', 'draft').upper()}  ")
    md_lines.append(f"**Submitted by:** {cr.get('submitter_name', '—')}  ")
    md_lines.append(f"**Generated:** {now_str}  ")
    if cr.get("description"):
        md_lines.append("")
        md_lines.append(f"**Description:** {cr['description']}")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## Summary")
    md_lines.append("")
    md_lines.append("| Action | Count |")
    md_lines.append("|--------|-------|")
    md_lines.append(f"| 🟢 Add | {len(adds)} |")
    md_lines.append(f"| 🔴 Remove | {len(removes)} |")
    md_lines.append(f"| 🟡 Modify | {len(modifies)} |")
    md_lines.append(f"| **Total** | **{len(items)}** |")
    md_lines.append("")

    if impact_flags:
        md_lines.append("## Impact Analysis")
        md_lines.append("")
        for flag in impact_flags:
            md_lines.append(f"- ⚠ {flag}")
        md_lines.append("")

    for section in sections:
        if section["count"] == 0:
            continue
        action_emoji = {"add": "🟢", "remove": "🔴", "modify": "🟡"}[section["action_type"]]
        md_lines.append(f"## {action_emoji} {section['label']} ({section['count']})")
        md_lines.append("")
        for entry in section["entries"]:
            label = entry["entity_label"]
            etype = entry["entity_type"]
            md_lines.append(f"### {label} *(type: {etype})*")
            if entry.get("justification"):
                md_lines.append(f"> **Justification:** {entry['justification']}")
                md_lines.append("")
            if section["action_type"] == "modify" and entry["diff"]:
                md_lines.append("**Before → After:**")
                md_lines.append("")
                md_lines.append("| Field | Before | After |")
                md_lines.append("|-------|--------|-------|")
                for d in entry["diff"]:
                    md_lines.append(f"| `{d['field']}` | `{d['before']}` | `{d['after']}` |")
                md_lines.append("")
            elif section["action_type"] == "add" and entry["after"]:
                md_lines.append("**New attributes:**")
                md_lines.append("")
                flat = _flatten_attrs(entry["after"])
                for k, v in list(flat.items())[:10]:
                    md_lines.append(f"- `{k}`: `{v}`")
                md_lines.append("")
            elif section["action_type"] == "remove" and entry["before"]:
                md_lines.append("**Removed attributes:**")
                md_lines.append("")
                flat = _flatten_attrs(entry["before"])
                for k, v in list(flat.items())[:10]:
                    md_lines.append(f"- `{k}`: `{v}`")
                md_lines.append("")
            if entry.get("created_by"):
                md_lines.append(f"*Marked by: {entry['created_by']} on {entry['created_at'][:10]}*")
                md_lines.append("")

    md_lines.append("---")
    md_lines.append("")
    md_lines.append("## Approval Signatures")
    md_lines.append("")
    md_lines.append("| Role | Name | Signature | Date |")
    md_lines.append("|------|------|-----------|------|")
    md_lines.append("| Submitter | | ___________________ | |")
    md_lines.append("| Technical Reviewer | | ___________________ | |")
    md_lines.append("| CAB Chair | | ___________________ | |")
    md_lines.append("| ISSM | | ___________________ | |")
    md_lines.append("")
    md_lines.append(f"*{topology_classification}*")

    # ── Checksum ───────────────────────────────────────────────────────────
    doc_body = "\n".join(md_lines)
    checksum = hashlib.sha256(doc_body.encode("utf-8")).hexdigest()[:16]

    return {
        "cr_id": cr.get("id", ""),
        "title": cr.get("title", "Untitled CR"),
        "topology_name": topology_name,
        "classification": topology_classification,
        "generated_at": now_str,
        "status": cr.get("status", "draft"),
        "summary": {
            "add": len(adds),
            "remove": len(removes),
            "modify": len(modifies),
            "total": len(items),
        },
        "sections": sections,
        "impact_flags": impact_flags,
        "approval_block": approval_block,
        "markdown": doc_body,
        "checksum": checksum,
    }


# ── Markup Item Validation ─────────────────────────────────────────────────────


def validate_markup_item(item: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings, or [] if valid."""
    errors: list[str] = []
    if item.get("action_type") not in ACTION_TYPES:
        errors.append(f"action_type must be one of: {', '.join(ACTION_TYPES)}")
    if not item.get("entity_id"):
        errors.append("entity_id is required.")
    if item.get("entity_type") and item["entity_type"] not in ENTITY_TYPES:
        errors.append(f"entity_type must be one of: {', '.join(ENTITY_TYPES)}")
    if item.get("action_type") == "modify":
        before = item.get("before_json")
        after = item.get("after_json")
        if not before and not after:
            errors.append("modify action requires at least one of before_json or after_json.")
    return errors
