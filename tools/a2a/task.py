#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A Task Model — dataclasses for the Agent-to-Agent protocol.

Defines Task, Artifact, StatusEvent, and TaskStatus enum
with full serialization support (to_dict / from_dict).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(Enum):
    """Valid task lifecycle states per A2A protocol."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {s.value for s in cls}

    @classmethod
    def terminal_states(cls) -> set:
        return {cls.COMPLETED, cls.FAILED, cls.CANCELED}


@dataclass
class Artifact:
    """An output artifact produced by a task."""
    name: str
    content_type: str
    data: Any = None
    classification: str = "CUI"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "content_type": self.content_type,
            "data": self.data,
            "classification": self.classification,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Artifact":
        return cls(
            name=d["name"],
            content_type=d["content_type"],
            data=d.get("data"),
            classification=d.get("classification", "CUI"),
        )


@dataclass
class StatusEvent:
    """A status change event in the task lifecycle."""
    status: str
    timestamp: str = ""
    message: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StatusEvent":
        return cls(
            status=d["status"],
            timestamp=d.get("timestamp", ""),
            message=d.get("message", ""),
        )


@dataclass
class Task:
    """An A2A task with full lifecycle tracking."""
    id: str = ""
    status: str = "submitted"
    skill_id: str = ""
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    artifacts: List[Artifact] = field(default_factory=list)
    history: List[StatusEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    # Multi-agent orchestration fields (Phase B)
    workflow_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    dependency_task_ids: List[str] = field(default_factory=list)
    collaboration_type: Optional[str] = None  # review, debate, consensus, veto, delegation
    reviewer_agent_id: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        # Record initial status in history if empty
        if not self.history:
            self.history.append(StatusEvent(
                status=self.status,
                message="Task created",
            ))

    def update_status(self, new_status: str, message: str = "") -> None:
        """Transition to a new status with history tracking."""
        if not TaskStatus.is_valid(new_status):
            raise ValueError(f"Invalid status '{new_status}'. Valid: {[s.value for s in TaskStatus]}")
        self.status = new_status
        self.updated_at = datetime.utcnow().isoformat() + "Z"
        self.history.append(StatusEvent(
            status=new_status,
            message=message,
        ))

    def add_artifact(self, name: str, content_type: str, data: Any = None,
                     classification: str = "CUI") -> Artifact:
        """Add an output artifact to the task."""
        artifact = Artifact(
            name=name,
            content_type=content_type,
            data=data,
            classification=classification,
        )
        self.artifacts.append(artifact)
        self.updated_at = datetime.utcnow().isoformat() + "Z"
        return artifact

    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        try:
            return TaskStatus(self.status) in TaskStatus.terminal_states()
        except ValueError:
            return False

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "status": self.status,
            "skill_id": self.skill_id,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "history": [h.to_dict() for h in self.history],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        # Multi-agent orchestration fields
        if self.workflow_id:
            d["workflow_id"] = self.workflow_id
        if self.assigned_agent_id:
            d["assigned_agent_id"] = self.assigned_agent_id
        if self.dependency_task_ids:
            d["dependency_task_ids"] = self.dependency_task_ids
        if self.collaboration_type:
            d["collaboration_type"] = self.collaboration_type
        if self.reviewer_agent_id:
            d["reviewer_agent_id"] = self.reviewer_agent_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        artifacts = [Artifact.from_dict(a) for a in d.get("artifacts", [])]
        history = [StatusEvent.from_dict(h) for h in d.get("history", [])]
        task = cls.__new__(cls)
        task.id = d.get("id", str(uuid.uuid4()))
        task.status = d.get("status", "submitted")
        task.skill_id = d.get("skill_id", "")
        task.input_data = d.get("input_data")
        task.output_data = d.get("output_data")
        task.artifacts = artifacts
        task.history = history
        task.metadata = d.get("metadata", {})
        task.created_at = d.get("created_at", "")
        task.updated_at = d.get("updated_at", "")
        # Multi-agent orchestration fields
        task.workflow_id = d.get("workflow_id")
        task.assigned_agent_id = d.get("assigned_agent_id")
        task.dependency_task_ids = d.get("dependency_task_ids", [])
        task.collaboration_type = d.get("collaboration_type")
        task.reviewer_agent_id = d.get("reviewer_agent_id")
        return task
