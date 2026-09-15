"""Rule-running agents.

Each checker owns a slice of the rule catalogue.  They share one
implementation because the difference between them *is* the rule selection -
duplicating the loop per agent would add nothing.
"""

from __future__ import annotations

from typing import ClassVar

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.models.findings import Category
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind
from catia_diff.rules.base import rules_for, run_rules


class RuleAgent(Agent):
    """Runs every enabled rule of :attr:`categories` over the document."""

    categories: ClassVar[tuple[Category, ...]] = ()

    def run(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        document = ctx.require_document()
        rule_ctx = ctx.rule_context()
        rules = rules_for(ctx.config, categories=self.categories)
        if not rules:
            return self.ok(task, status=AgentStatus.SKIPPED, artifacts={"rules": 0})
        findings = [
            finding
            for finding in run_rules(rules, document, rule_ctx)
            if ctx.config.keeps(finding.severity)
        ]
        for finding in findings:
            if finding.agent in {"", "unknown"}:
                finding.agent = self.name
        return self.ok(
            task,
            findings=findings,
            artifacts={"rules": len(rules), "rule_ids": [rule.meta.id for rule in rules]},
        )


class DimensioningAgent(RuleAgent):
    """Dimensioning, tolerancing, GD&T and drawing symbols."""

    name = "dimensioning"
    accepts = (TaskKind.CHECK_DIMENSIONS,)
    categories = (
        Category.DIMENSIONING,
        Category.CROSS_VIEW,
        Category.TOLERANCING,
        Category.GDT,
        Category.SYMBOLS,
    )


class TitleBlockAgent(RuleAgent):
    """Title block and document metadata."""

    name = "title_block"
    accepts = (TaskKind.CHECK_TITLE_BLOCK,)
    categories = (Category.TITLE_BLOCK,)


class ConsistencyAgent(RuleAgent):
    """Cross-sheet consistency and extraction-quality reporting."""

    name = "consistency"
    accepts = (TaskKind.CHECK_CONSISTENCY,)
    categories = (Category.CONSISTENCY, Category.EXTRACTION, Category.REFERENCE)
