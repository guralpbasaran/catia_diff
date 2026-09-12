"""Agents: one responsibility each, coordinated by the orchestrator."""

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.agents.checkers import ConsistencyAgent, DimensioningAgent, TitleBlockAgent
from catia_diff.agents.extraction import ExtractionAgent
from catia_diff.agents.orchestrator import Orchestrator, audit_file
from catia_diff.agents.reporting import ReportAgent

__all__ = [
    "Agent",
    "AuditContext",
    "ConsistencyAgent",
    "DimensioningAgent",
    "ExtractionAgent",
    "Orchestrator",
    "ReportAgent",
    "TitleBlockAgent",
    "audit_file",
]
