# CUI // SP-CTI
"""Shared types for IDC snapshot importers.

Each importer (tf_state, pulumi_state, …) converts provider-native state
into a list[SnapshotRow] that maps 1-to-1 with the infra_snapshots table.
"""
from dataclasses import dataclass, field


@dataclass
class SnapshotRow:
    """One row destined for the infra_snapshots table."""

    snapshot_id: str
    project_id: str
    csp: str
    region: str
    resource_type: str
    resource_id: str
    config_json: str = field(default="{}")
    classification: str = field(default="CUI")
    tags_json: str = field(default="{}")
    taken_at: str = field(default="")
