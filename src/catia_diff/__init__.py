"""catia_diff - multi-agent audit of 2D technical drawings.

Typical use::

    from catia_diff import AuditConfig, Profile, audit_file

    report = audit_file("part.dxf", AuditConfig(profile=Profile.ISO, language="tr"))
    print(report.summary_line("tr"))
"""

from catia_diff.agents.orchestrator import Orchestrator, audit_file
from catia_diff.config import AuditConfig, Profile, VisionConfig, VisionMode
from catia_diff.models.findings import AuditReport, Category, Finding, Severity

__version__ = "0.1.0"

__all__ = [
    "AuditConfig",
    "AuditReport",
    "Category",
    "Finding",
    "Orchestrator",
    "Profile",
    "Severity",
    "VisionConfig",
    "VisionMode",
    "__version__",
    "audit_file",
]
