"""
agentilluminator.core.events
Core event schema for Agent Illuminator traces.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    AGENT_WAKE = "agent_wake"
    AGENT_CLOSE = "agent_close"
    FILE_WRITE = "file_write"
    FILE_READ = "file_read"
    TOOL_INVOCATION = "tool_invocation"
    CRON_FIRE = "cron_fire"
    CRON_MISS = "cron_miss"
    TASK_TRANSITION = "task_transition"
    SUPERVISOR_VERDICT = "supervisor_verdict"
    DELIVERY_RECEIPT = "delivery_receipt"
    ASSERTION = "assertion"
    GAP_DETECTED = "gap_detected"
    ANNOTATION = "annotation"


class EventStatus(str, Enum):
    OK = "ok"
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIP = "skip"
    UNKNOWN = "unknown"


class RunStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    FAILED = "failed"
    ORPHANED = "orphaned"
    TIMEOUT = "timeout"


@dataclass
class TraceEvent:
    event_type: EventType
    summary: str
    agent_id: str = ""
    run_id: str = ""
    status: EventStatus = EventStatus.OK
    detail: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "summary": self.summary,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "detail": self.detail,
            "tags": self.tags,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AgentRun:
    run_id: str
    agent_id: str
    triggered_by: str = ""
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    status: RunStatus = RunStatus.OPEN
    model: str = ""
    pipeline_run_id: str = ""
    tags: list[str] = field(default_factory=list)
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def duration_sec(self) -> int | None:
        if self.closed_at:
            return int((self.closed_at - self.triggered_at).total_seconds())
        return None

    def add_event(self, event: TraceEvent) -> None:
        event.run_id = self.run_id
        event.agent_id = self.agent_id
        self.events.append(event)

    def assertions(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type == EventType.ASSERTION]

    def gaps(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type == EventType.GAP_DETECTED]

    def has_delivery_receipt(self) -> bool:
        return any(e.event_type == EventType.DELIVERY_RECEIPT for e in self.events)


@dataclass
class PipelineRun:
    run_id: str
    pipeline_id: str
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    status: RunStatus = RunStatus.OPEN
    agent_run_ids: list[str] = field(default_factory=list)
    verdict: str = ""
    tags: list[str] = field(default_factory=list)
