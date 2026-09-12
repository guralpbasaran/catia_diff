import json

from catia_diff.agents.base import Agent, AuditContext
from catia_diff.agents.orchestrator import Orchestrator, audit_file
from catia_diff.config import AuditConfig, Profile, VisionConfig, VisionMode
from catia_diff.models.findings import Severity
from catia_diff.models.messages import AgentResult, AgentStatus, AgentTask, TaskKind


def test_end_to_end_audit_of_the_sample_drawing(sample_dxf, config):
    report = Orchestrator(config).audit(sample_dxf)

    found = {finding.rule_id for finding in report.findings}
    assert {"DIM001", "DIM003", "DIM004", "TOL001", "TOL002", "GDT001", "GDT004", "TB003"} <= found

    assert report.counts_by_severity()[Severity.CRITICAL] >= 3
    assert report.worst_severity is Severity.CRITICAL
    assert not report.is_clean(Severity.MAJOR)
    assert report.document_stats["dimensions"] == 9
    assert report.rules_executed > 40
    assert report.duration_ms > 0

    names = {path.name for path in config.output_dir.iterdir()}
    assert {"TD-1001_sample_audit.json", "TD-1001_sample_audit.md", "TD-1001_sample_audit.html"} <= names


def test_iso2768_rules_fire_end_to_end(sample_dxf_iso2768, config):
    report = Orchestrator(config).audit(sample_dxf_iso2768)
    found = {finding.rule_id for finding in report.findings}
    assert {"TOL007", "TOL008", "TOL009", "TOL010", "GDT011"} <= found

    stack = next(f for f in report.findings if f.rule_id == "TOL010")
    assert "±0.7" in stack.message and "±0.3" in stack.message
    looser = next(f for f in report.findings if f.rule_id == "TOL008")
    assert "ISO 2768-mK" in looser.message

    # the sheet now states a general tolerance, so TB008 must stay quiet
    assert "TB008" not in found


def test_general_tolerance_note_is_not_a_surface_symbol(sample_dxf_iso2768, config):
    """Regression: 'GENEL TOLERANSLAR' contains 'RA' but is not an Ra callout."""
    report = Orchestrator(config).audit(sample_dxf_iso2768)
    surface_findings = [f for f in report.findings if f.rule_id == "SYM001"]
    # only the intentional bare "√" symbol, never the note
    assert len(surface_findings) == 1
    assert report.document_stats["surface_finishes"] == 1


def test_written_json_carries_the_agent_trace(sample_dxf, config):
    Orchestrator(config).audit(sample_dxf)
    payload = json.loads((config.output_dir / "TD-1001_sample_audit.json").read_text())

    agents = {trace["agent"] for trace in payload["agent_traces"]}
    assert {"extraction", "dimensioning", "title_block", "consistency"} <= agents
    assert payload["summary"]["worst_severity"] == "critical"
    assert payload["findings"]


def test_findings_are_localised(sample_dxf, tmp_path):
    config = AuditConfig(
        language="tr",
        output_dir=tmp_path / "tr",
        formats=("md",),
        render_overlay=False,
        vision=VisionConfig(mode=VisionMode.OFF),
    )
    report = Orchestrator(config).audit(sample_dxf)
    finding = next(f for f in report.findings if f.rule_id == "DIM004")
    assert "ölçü" in finding.localized_message("tr").lower()
    assert finding.localized_message("en") != finding.localized_message("tr")

    markdown = (tmp_path / "tr" / "TD-1001_sample_audit.md").read_text()
    assert "Kritik" in markdown


def test_sequential_and_parallel_runs_agree(sample_dxf, tmp_path):
    def run(parallel: bool):
        config = AuditConfig(
            language="en",
            output_dir=tmp_path / f"out_{parallel}",
            formats=(),
            render_overlay=False,
            parallel_agents=parallel,
            vision=VisionConfig(mode=VisionMode.OFF),
        )
        return sorted(f.id for f in Orchestrator(config).audit(sample_dxf).findings)

    assert run(True) == run(False)


def test_rule_selection_flags(sample_dxf, tmp_path):
    config = AuditConfig(
        language="en",
        output_dir=tmp_path / "filtered",
        formats=(),
        render_overlay=False,
        enabled_rules=frozenset({"DIM001"}),
        vision=VisionConfig(mode=VisionMode.OFF),
    )
    report = Orchestrator(config).audit(sample_dxf)
    assert {f.rule_id for f in report.findings} == {"DIM001"}

    config = config.model_copy(update={"enabled_rules": None, "disabled_rules": frozenset({"TOL001"})})
    report = Orchestrator(config).audit(sample_dxf)
    assert "TOL001" not in {f.rule_id for f in report.findings}


def test_min_severity_filter(sample_dxf, tmp_path):
    config = AuditConfig(
        language="en",
        output_dir=tmp_path / "critical",
        formats=(),
        render_overlay=False,
        min_severity=Severity.CRITICAL,
        vision=VisionConfig(mode=VisionMode.OFF),
    )
    report = Orchestrator(config).audit(sample_dxf)
    assert report.findings
    assert {f.severity for f in report.findings} == {Severity.CRITICAL}


def test_asme_profile_runs(sample_dxf, tmp_path):
    config = AuditConfig(
        profile=Profile.ASME,
        language="en",
        output_dir=tmp_path / "asme",
        formats=(),
        render_overlay=False,
        vision=VisionConfig(mode=VisionMode.OFF),
    )
    report = Orchestrator(config).audit(sample_dxf)
    assert report.profile == "ASME"
    assert report.findings


class _ExplodingAgent(Agent):
    name = "dimensioning"
    accepts = (TaskKind.CHECK_DIMENSIONS,)

    def run(self, task: AgentTask, ctx: AuditContext) -> AgentResult:
        raise RuntimeError("boom")


def test_a_failing_agent_degrades_the_report_instead_of_the_run(sample_dxf, config):
    from catia_diff.agents.checkers import ConsistencyAgent, TitleBlockAgent
    from catia_diff.agents.extraction import ExtractionAgent
    from catia_diff.agents.reporting import ReportAgent

    orchestrator = Orchestrator(
        config,
        agents=[
            ExtractionAgent(),
            _ExplodingAgent(),
            TitleBlockAgent(),
            ConsistencyAgent(),
            ReportAgent(),
        ],
    )
    report = orchestrator.audit(sample_dxf)

    assert any("boom" in warning for warning in report.warnings)
    assert {f.rule_id for f in report.findings}  # the other agents still reported
    failed = [trace for trace in report.agent_traces if trace["status"] == AgentStatus.FAILED.value]
    assert failed and failed[0]["agent"] == "dimensioning"


def test_unreadable_file_produces_a_report_not_an_exception(tmp_path, config):
    broken = tmp_path / "broken.dxf"
    broken.write_text("this is not a DXF file")
    report = Orchestrator(config).audit(broken)
    assert report.findings == []
    assert report.warnings


def test_audit_file_helper(sample_dxf, config):
    report = audit_file(sample_dxf, config)
    assert report.findings
