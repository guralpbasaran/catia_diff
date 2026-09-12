"""Orchestrator: the only component that knows the whole workflow.

    extract  ->  [dimensioning | title block | consistency]  ->  report

The checker stage is embarrassingly parallel (the agents only read the
extracted document), so it runs in a thread pool when enabled.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.agents.checkers import ConsistencyAgent, DimensioningAgent, TitleBlockAgent
from catia_diff.agents.extraction import ExtractionAgent
from catia_diff.agents.reporting import ReportAgent
from catia_diff.config import AuditConfig
from catia_diff.llm.base import VisionModel
from catia_diff.models.findings import AuditReport, Finding
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind

logger = logging.getLogger("catia_diff.orchestrator")

CHECKER_TASKS: tuple[TaskKind, ...] = (
    TaskKind.CHECK_DIMENSIONS,
    TaskKind.CHECK_TITLE_BLOCK,
    TaskKind.CHECK_CONSISTENCY,
)


class Orchestrator:
    """Drives one audit from a file path to an :class:`AuditReport`."""

    def __init__(
        self,
        config: AuditConfig | None = None,
        *,
        agents: Sequence[Agent] | None = None,
        vision_model: VisionModel | None = None,
        workdir: Path | None = None,
    ) -> None:
        self.config = config or AuditConfig()
        self.agents: list[Agent] = list(agents) if agents else self._default_agents()
        self.vision_model = vision_model
        self.workdir = workdir
        self.results: list[AgentResult] = []

    @staticmethod
    def _default_agents() -> list[Agent]:
        return [
            ExtractionAgent(),
            DimensioningAgent(),
            TitleBlockAgent(),
            ConsistencyAgent(),
            ReportAgent(),
        ]

    # ------------------------------------------------------------------
    def audit(self, path: str | Path) -> AuditReport:
        started = time.perf_counter()
        source = Path(path)
        workdir = self.workdir or Path(tempfile.mkdtemp(prefix="catia_diff_"))
        workdir.mkdir(parents=True, exist_ok=True)
        ctx = AuditContext(
            config=self.config,
            source_path=source,
            workdir=workdir,
            vision_model=self.vision_model,
        )
        self.results = []

        extraction = self._dispatch(AgentTask(kind=TaskKind.EXTRACT, payload={"path": source}), ctx)
        if extraction.status is AgentStatus.FAILED or ctx.document is None:
            return self._failed_report(source, extraction, started)

        checker_results = self._run_checkers(ctx)
        findings: list[Finding] = []
        rules_executed = 0
        for result in checker_results:
            findings.extend(result.findings)
            rules_executed += int(result.artifacts.get("rules", 0) or 0)

        report_result = self._dispatch(
            AgentTask(
                kind=TaskKind.REPORT,
                payload={
                    "findings": findings,
                    "rules_executed": rules_executed,
                    "traces": [result.trace() for result in self.results],
                    "warnings": self._agent_warnings(),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                },
            ),
            ctx,
        )
        report = report_result.artifacts.get("report")
        if not isinstance(report, AuditReport):
            return self._failed_report(source, report_result, started)

        report.agent_traces = [result.trace() for result in self.results]
        report.duration_ms = (time.perf_counter() - started) * 1000.0
        return report

    def _agent_warnings(self) -> list[str]:
        """Agent warnings and failures, flattened for the report header."""
        messages: list[str] = []
        for result in self.results:
            messages.extend(f"{result.agent}: {warning}" for warning in result.warnings)
            if result.error:
                messages.append(f"{result.agent} failed: {result.error}")
        return messages

    # ------------------------------------------------------------------
    def _run_checkers(self, ctx: AuditContext) -> list[AgentResult]:
        tasks = [AgentTask(kind=kind) for kind in CHECKER_TASKS if self._agent_for(kind)]
        if not tasks:
            return []
        if not self.config.parallel_agents or len(tasks) == 1:
            return [self._dispatch(task, ctx) for task in tasks]
        with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="catia-diff") as pool:
            results = list(pool.map(lambda task: self._dispatch(task, ctx), tasks))
        return results

    def _agent_for(self, kind: TaskKind) -> Agent | None:
        return next((agent for agent in self.agents if agent.handles(AgentTask(kind=kind))), None)

    def _dispatch(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        agent = self._agent_for(task.kind)
        if agent is None:
            result = AgentResult(
                task_id=task.task_id,
                agent="orchestrator",
                status=AgentStatus.SKIPPED,
                warnings=[f"no agent registered for {task.kind.value}"],
            )
        else:
            result = agent.execute(task, ctx)
        self.results.append(result)
        return result

    def _failed_report(self, source: Path, result: AgentResult, started: float) -> AuditReport:
        return AuditReport(
            document=source,
            profile=self.config.profile.value,
            language=self.config.language,
            warnings=[f"{result.agent}: {result.error or 'failed'}", *result.warnings],
            agent_traces=[item.trace() for item in self.results],
            duration_ms=(time.perf_counter() - started) * 1000.0,
            extraction_failed=True,
        )


def audit_file(path: str | Path, config: AuditConfig | None = None, **kwargs) -> AuditReport:
    """Convenience wrapper: audit one file with the default agent set."""
    return Orchestrator(config, **kwargs).audit(path)
