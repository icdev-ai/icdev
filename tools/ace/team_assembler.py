# CUI // SP-CTI
"""ACE TeamAssembler — converts a TeamManifest into a persisted TeamInstance.

Usage::

    from icdev.tools.ace.team_assembler import TeamAssembler
    from icdev.tools.ace.problem_classifier import TeamManifest, RoleSlot

    manifest = TeamManifest(slots=[RoleSlot("ai_developer", 1), RoleSlot("qa_manager", 1)])
    assembler = TeamAssembler()
    instance = assembler.assemble(manifest, instance_id="inst-abc", context={"problem_text": "..."})
    # instance.specs: list[CoWorkerSpec]
    # instance.workflow_id: str
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from icdev.tools.ace.constants import MAX_TEAM_SIZE, TRUST_TIER_DEFAULT
from icdev.tools.ace.problem_classifier import TeamManifest
from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError

# Ordered from most to least restrictive (lower index = more restrictive)
_TIER_ORDER: dict[str, int] = {"red": 0, "orange": 1, "yellow": 2, "green": 3}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CoWorkerSpec:
    """Fully-resolved specification for a single ACE coworker."""

    coworker_id: str
    role_id: str
    role_slot: str          # unique label within the instance, e.g. "ai_developer-0"
    mailbox_id: str         # A2A mailbox address
    llm_function: str       # e.g. "code_generation" — used to route LLM calls
    tool_permissions: list[str] = field(default_factory=list)
    trust_tier: str = TRUST_TIER_DEFAULT
    description: str = ""
    # Phase 2: filesystem + routine access scope (populated from role YAML)
    folder_access: list[dict] = field(default_factory=list)
    icdev_tools: list[str] = field(default_factory=list)


@dataclass
class TeamInstance:
    """A fully assembled team, ready for orchestration."""

    instance_id: str
    specs: list[CoWorkerSpec]
    workflow_id: str        # FK to ace_agent_workflows.id


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TeamSizeError(ValueError):
    """Raised when the manifest exceeds MAX_TEAM_SIZE."""


class AssemblyError(RuntimeError):
    """Raised when DB persistence fails."""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class TeamAssembler:
    """Converts a TeamManifest to a persisted TeamInstance.

    Steps:
      1. Validate total coworker count against MAX_TEAM_SIZE.
      2. Register session via coordination/session_registry.
      3. Build CoWorkerSpec list by resolving each slot against RoleLoader.
      4. Persist ace_instances (state=assembling), ace_coworkers (state=idle),
         and ace_agent_workflows rows via get_canvas_connection().
      5. Return TeamInstance(instance_id, specs, workflow_id).
    """

    def __init__(self, role_loader: RoleLoader | None = None) -> None:
        self._role_loader = role_loader or RoleLoader()

    def assemble(
        self,
        manifest: TeamManifest,
        instance_id: str,
        context: dict[str, Any],
    ) -> TeamInstance:
        """Assemble and persist a team from a TeamManifest.

        Args:
            manifest:    Proposed team composition (list of RoleSlots).
            instance_id: Caller-supplied unique ID for this ACE run.
            context:     Arbitrary context dict; must contain 'problem_text' for
                         meaningful session registration.

        Returns:
            TeamInstance with persisted DB rows.

        Raises:
            TeamSizeError:  If total coworkers exceed MAX_TEAM_SIZE.
            AssemblyError:  If the DB write fails.
        """
        total = sum(slot.count for slot in manifest.slots)
        if total > MAX_TEAM_SIZE:
            raise TeamSizeError(
                f"Manifest requests {total} coworkers but MAX_TEAM_SIZE={MAX_TEAM_SIZE}"
            )

        self._register_session(context, instance_id)

        specs = self._build_specs(manifest, instance_id)
        workflow_id = self._persist(instance_id, manifest, specs, context)

        return TeamInstance(instance_id=instance_id, specs=specs, workflow_id=workflow_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register_session(self, context: dict[str, Any], instance_id: str) -> None:
        problem_text = context.get("problem_text", "")
        intent = (problem_text[:100] if problem_text else f"ace:{instance_id}")
        try:
            from tools.coordination import session_registry as reg
            reg.register(intent=intent)
        except Exception:
            pass  # coordination is best-effort; never block assembly

    def _build_specs(self, manifest: TeamManifest, instance_id: str) -> list[CoWorkerSpec]:
        specs: list[CoWorkerSpec] = []
        for slot in manifest.slots:
            try:
                role = self._role_loader.get_role(slot.role_id)
                llm_function = role.llm_function
                tool_permissions = list(role.tool_permissions)
                trust_tier = role.trust_tier
                folder_access = list(role.folder_access)
                icdev_tools = list(role.icdev_tools)
            except RoleNotFoundError:
                llm_function = ""
                tool_permissions = []
                trust_tier = TRUST_TIER_DEFAULT
                folder_access = []
                icdev_tools = []

            for i in range(slot.count):
                role_slot = f"{slot.role_id}-{i}" if slot.count > 1 else slot.role_id
                coworker_id = f"{instance_id}-{role_slot}-{uuid.uuid4().hex[:8]}"
                specs.append(
                    CoWorkerSpec(
                        coworker_id=coworker_id,
                        role_id=slot.role_id,
                        role_slot=role_slot,
                        mailbox_id=f"mailbox:{coworker_id}",
                        llm_function=llm_function,
                        tool_permissions=tool_permissions,
                        trust_tier=trust_tier,
                        folder_access=folder_access,
                        icdev_tools=icdev_tools,
                    )
                )
        return specs

    def _persist(
        self,
        instance_id: str,
        manifest: TeamManifest,
        specs: list[CoWorkerSpec],
        context: dict[str, Any],
    ) -> str:
        from icdev.tools.db.storage import get_canvas_connection
        from icdev.tools.ace.db.init_db import init as _init_ace_db

        _init_ace_db()  # idempotent — ensures schema including ace_agent_workflows

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
        primary_role = manifest.slots[0].role_id if manifest.slots else ""
        instance_tier = _most_restrictive_tier(specs)

        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            # If a pending stub row was pre-inserted by launch(), update it in-place
            # so the browser page never 404s during the async assembly phase.
            existing = conn.execute(
                "SELECT id FROM ace_instances WHERE id = %s", (instance_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE ace_instances SET name=%s, role_id=%s, state='assembling', "
                    "trust_tier=%s, config_json=%s, updated_at=%s WHERE id=%s",
                    (
                        context.get("name", instance_id),
                        primary_role,
                        instance_tier,
                        json.dumps(context),
                        now,
                        instance_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO ace_instances "
                    "(id, name, role_id, state, trust_tier, config_json, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 'assembling', %s, %s, %s, %s)",
                    (
                        instance_id,
                        context.get("name", instance_id),
                        primary_role,
                        instance_tier,
                        json.dumps(context),
                        now,
                        now,
                    ),
                )

            for spec in specs:
                conn.execute(
                    "INSERT INTO ace_coworkers "
                    "(id, instance_id, role_id, display_name, state, trust_tier, created_at) "
                    "VALUES (%s, %s, %s, %s, 'idle', %s, %s)",
                    (
                        spec.coworker_id,
                        instance_id,
                        spec.role_id,
                        spec.role_slot,
                        spec.trust_tier,
                        now,
                    ),
                )

            workflow_name = context.get("workflow_name", "default")
            conn.execute(
                "INSERT INTO ace_agent_workflows "
                "(id, instance_id, name, state, config_json, created_at) "
                "VALUES (%s, %s, %s, 'pending', '{}', %s)",
                (workflow_id, instance_id, workflow_name, now),
            )

            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise AssemblyError(f"Failed to persist team {instance_id}: {exc}") from exc
        finally:
            conn.close()

        return workflow_id


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _most_restrictive_tier(specs: list[CoWorkerSpec]) -> str:
    """Return the most restrictive trust tier across all specs."""
    if not specs:
        return TRUST_TIER_DEFAULT
    return min(specs, key=lambda s: _TIER_ORDER.get(s.trust_tier, 2)).trust_tier
