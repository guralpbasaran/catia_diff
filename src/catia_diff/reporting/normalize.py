"""Finding normalisation: dedupe, prioritise, cap."""

from __future__ import annotations

from collections import defaultdict

from catia_diff.config import AuditConfig
from catia_diff.models.findings import Category, Evidence, Finding, Severity


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop findings that describe the same defect twice.

    Two findings collide when they share the rule, the sheet and either the
    referenced objects or the exact message; the more confident one wins.
    """
    best: dict[tuple, Finding] = {}
    for finding in findings:
        key = (
            finding.rule_id,
            finding.evidence.sheet_index,
            tuple(sorted(finding.evidence.object_ids)) or finding.message,
        )
        current = best.get(key)
        if current is None or finding.confidence > current.confidence:
            best[key] = finding
    return list(best.values())


def cap_per_rule(findings: list[Finding], limit: int) -> list[Finding]:
    """Keep at most ``limit`` findings per rule, summarising the remainder."""
    if limit <= 0:
        return findings
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.rule_id].append(finding)

    out: list[Finding] = []
    for rule_id, group in grouped.items():
        group.sort(key=lambda f: (f.severity.rank, -f.confidence))
        out.extend(group[:limit])
        overflow = len(group) - limit
        if overflow > 0:
            head = group[0]
            out.append(
                Finding(
                    rule_id=rule_id,
                    severity=Severity.INFO,
                    category=head.category,
                    title=f"{head.title} - {overflow} more occurrence(s)",
                    title_tr=f"{head.title_tr} - {overflow} tekrar daha",
                    message=(
                        f"{overflow} further occurrence(s) of {rule_id} were found and omitted "
                        "from the detailed list."
                    ),
                    message_tr=(
                        f"{rule_id} kuralına ait {overflow} bulgu daha tespit edildi ve ayrıntılı "
                        "listeye eklenmedi."
                    ),
                    standards=head.standards,
                    evidence=Evidence(sheet_index=head.evidence.sheet_index),
                    agent=head.agent,
                    confidence=head.confidence,
                )
            )
    return out


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: f.sort_key())


def normalize_findings(findings: list[Finding], config: AuditConfig) -> list[Finding]:
    """Full normalisation pipeline used by the report agent."""
    kept = [f for f in findings if config.keeps(f.severity)]
    kept = dedupe(kept)
    kept = cap_per_rule(kept, config.max_findings_per_rule)
    return sort_findings(kept)


def group_by_category(findings: list[Finding]) -> dict[Category, list[Finding]]:
    grouped: dict[Category, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.category].append(finding)
    return dict(grouped)
