"""Inter-agent message envelopes.

Agents never call each other directly: the orchestrator hands every agent an
:class:`AgentTask` and collects an :class:`AgentResult`.  Keeping the protocol
explicit (instead of passing bare objects around) makes the pipeline
serialisable, traceable and trivially testable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from catia_diff.models.findings import Finding


class TaskKind(str, Enum):
    EXTRACT = "extract"
    CHECK_DIMENSIONS = "check_dimensions"
    CHECK_TITLE_BLOCK = "check_title_block"
    CHECK_CONSISTENCY = "check_consistency"
    REPORT = "report"


class AgentStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"  # produced output but degraded (e.g. vision unavailable)
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentTask(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: TaskKind
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_task_id: str | None = None

    def child(self, kind: TaskKind, **payload: Any) -> AgentTask:
        return AgentTask(kind=kind, payload=payload, parent_task_id=self.task_id)


class AgentResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    agent: str
    status: AgentStatus = AgentStatus.OK
    findings: list[Finding] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in {AgentStatus.OK, AgentStatus.PARTIAL}

    def trace(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "status": self.status.value,
            "findings": len(self.findings),
            "duration_ms": round(self.duration_ms, 2),
            "warnings": list(self.warnings),
            "error": self.error,
        }
