"""Agent framework: shared context, timing, error containment."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from catia_diff.config import AuditConfig
from catia_diff.llm.base import VisionModel
from catia_diff.models.drawing import DrawingDocument
from catia_diff.models.findings import Finding
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind
from catia_diff.rules.base import RuleContext

logger = logging.getLogger("catia_diff.agents")


@dataclass
class AuditContext:
    """Blackboard shared by the agents of one audit run."""

    config: AuditConfig
    source_path: Path
    workdir: Path
    document: DrawingDocument | None = None
    vision_model: VisionModel | None = None
    artifacts: dict[str, object] = field(default_factory=dict)

    def require_document(self) -> DrawingDocument:
        if self.document is None:
            raise RuntimeError("no document has been extracted yet")
        return self.document

    def rule_context(self) -> RuleContext:
        return RuleContext(self.require_document(), self.config)

    def sub_dir(self, name: str) -> Path:
        path = self.workdir / name
        path.mkdir(parents=True, exist_ok=True)
        return path


class Agent(ABC):
    """One responsibility, one agent.

    Subclasses implement :meth:`run`; :meth:`execute` adds timing, logging and
    error containment so a single failing agent degrades the report instead of
    aborting the pipeline.
    """

    name: ClassVar[str] = "agent"
    accepts: ClassVar[tuple[TaskKind, ...]] = ()

    def handles(self, task: AgentTask) -> bool:
        return task.kind in self.accepts

    @abstractmethod
    def run(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        """Do the work and return a result."""

    def execute(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        started = time.perf_counter()
        logger.debug("%s: starting %s (%s)", self.name, task.kind.value, task.task_id)
        try:
            result = self.run(task, ctx)
        except Exception as exc:  # noqa: BLE001 - intentional containment
            logger.exception("%s failed", self.name)
            result = AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=AgentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        logger.debug(
            "%s: %s in %.1f ms (%d findings)",
            self.name,
            result.status.value,
            result.duration_ms,
            len(result.findings),
        )
        return result

    # -- helpers -----------------------------------------------------------
    def ok(
        self,
        task: AgentTask,
        *,
        findings: list[Finding] | None = None,
        artifacts: dict | None = None,
        warnings: list[str] | None = None,
        status: AgentStatus = AgentStatus.OK,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=status,
            findings=findings or [],
            artifacts=artifacts or {},
            warnings=warnings or [],
        )
