"""Report generator agent: normalise, prioritise, render."""

from __future__ import annotations

import logging
from pathlib import Path

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.models.findings import AuditReport, Finding
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind
from catia_diff.reporting.normalize import normalize_findings
from catia_diff.reporting.overlay import render_overlays
from catia_diff.reporting.render import write_reports

logger = logging.getLogger("catia_diff.agents.reporting")


class ReportAgent(Agent):
    name = "reporting"
    accepts = (TaskKind.REPORT,)

    def run(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        document = ctx.require_document()
        raw: list[Finding] = list(task.payload.get("findings", []))
        findings = normalize_findings(raw, ctx.config)

        report = AuditReport(
            document=document.source_path,
            source_format=document.source_format.value,
            profile=ctx.config.profile.value,
            language=ctx.config.language,
            findings=findings,
            document_stats=document.counts(),
            warnings=list(document.warnings),
            rules_executed=int(task.payload.get("rules_executed", 0)),
            # Traces of the agents that ran before this one; the orchestrator
            # appends this agent's own trace to the in-memory report.
            agent_traces=list(task.payload.get("traces", [])),
            duration_ms=float(task.payload.get("elapsed_ms", 0.0)),
        )
        report.warnings.extend(task.payload.get("warnings", []))

        warnings: list[str] = []
        if ctx.config.render_overlay:
            try:
                report.overlays = render_overlays(document, report, ctx.config)
            except Exception as exc:  # overlays are a nice-to-have, never fatal
                warnings.append(f"overlay rendering skipped: {exc}")
                logger.debug("overlay rendering failed", exc_info=True)

        written: list[Path] = []
        try:
            written = write_reports(report, ctx.config)
        except Exception as exc:
            warnings.append(f"report rendering failed: {exc}")
            logger.debug("report rendering failed", exc_info=True)

        return self.ok(
            task,
            artifacts={"report": report, "files": written},
            warnings=warnings,
            status=AgentStatus.PARTIAL if warnings else AgentStatus.OK,
        )
