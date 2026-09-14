"""Command line interface.

    catia-diff audit part.dxf --profile ISO --lang tr --out reports
    catia-diff rules --lang tr
    catia-diff ui --port 8050
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from catia_diff import __version__
from catia_diff.agents.orchestrator import Orchestrator
from catia_diff.config import AuditConfig, Profile, VisionConfig, VisionMode
from catia_diff.errors import CatiaDiffError
from catia_diff.models.findings import AuditReport, Category, Severity
from catia_diff.rules.base import all_rules

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

_ANSI = {
    Severity.CRITICAL: "\033[1;31m",
    Severity.MAJOR: "\033[1;33m",
    Severity.MINOR: "\033[1;36m",
    Severity.INFO: "\033[0;37m",
}
_RESET = "\033[0m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catia-diff",
        description="Audit 2D technical drawings for missing, wrong or inconsistent callouts.",
    )
    parser.add_argument("--version", action="version", version=f"catia-diff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="audit one or more drawing files")
    audit.add_argument("files", nargs="+", type=Path, help="DXF, PDF or image files")
    audit.add_argument("--profile", choices=[p.value for p in Profile], default=Profile.ISO.value)
    audit.add_argument("--lang", choices=["tr", "en"], default="tr")
    audit.add_argument("--out", type=Path, default=Path("reports"), help="output directory")
    audit.add_argument("--format", default="json,md,html", help="json,md,html (comma separated)")
    audit.add_argument("--vision", choices=[m.value for m in VisionMode], default=VisionMode.AUTO.value)
    audit.add_argument("--vision-model", default=None, help="Claude model id for the vision pass")
    audit.add_argument("--dpi", type=int, default=200, help="rasterisation DPI for scanned input")
    audit.add_argument(
        "--min-severity", choices=[s.value for s in Severity], default=Severity.INFO.value,
        help="drop findings below this severity",
    )
    audit.add_argument(
        "--fail-on", choices=[*[s.value for s in Severity], "never"], default=Severity.MAJOR.value,
        help="exit code 1 when a finding of this severity or worse remains",
    )
    audit.add_argument("--only", default=None, help="run only these rule ids (comma separated)")
    audit.add_argument("--disable", default=None, help="skip these rule ids (comma separated)")
    audit.add_argument(
        "--category", default=None,
        help=f"restrict to categories: {', '.join(c.value for c in Category)}",
    )
    audit.add_argument("--no-overlay", action="store_true", help="skip marked-up sheet images")
    audit.add_argument("--sequential", action="store_true", help="run checker agents one by one")
    audit.add_argument("--quiet", action="store_true", help="only print the summary line")
    audit.add_argument("-v", "--verbose", action="store_true")

    ui = sub.add_parser("ui", help="serve the web dashboard (needs the 'ui' extra)")
    ui.add_argument("--host", default="127.0.0.1", help="interface to bind")
    ui.add_argument("--port", type=int, default=8050)
    ui.add_argument("--profile", choices=[p.value for p in Profile], default=Profile.ISO.value)
    ui.add_argument("--lang", choices=["tr", "en"], default="tr")
    ui.add_argument("--debug", action="store_true", help="Dash hot reload and error pages")

    rules = sub.add_parser("rules", help="list the rule catalogue")
    rules.add_argument("--profile", choices=[p.value for p in Profile], default=None)
    rules.add_argument("--lang", choices=["tr", "en"], default="en")
    rules.add_argument("--category", default=None)

    sub.add_parser("formats", help="list supported input formats")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "rules":
        return _cmd_rules(args)
    if args.command == "formats":
        return _cmd_formats()
    if args.command == "ui":
        return _cmd_ui(args)
    return _cmd_audit(args)


# --------------------------------------------------------------------------
def _cmd_audit(args: argparse.Namespace) -> int:
    config = AuditConfig(
        profile=Profile(args.profile),
        language=args.lang,
        output_dir=args.out,
        formats=args.format,
        render_overlay=not args.no_overlay,
        raster_dpi=args.dpi,
        min_severity=Severity(args.min_severity),
        fail_on=Severity(args.fail_on) if args.fail_on != "never" else Severity.INFO,
        disabled_rules=frozenset(_split(args.disable)),
        enabled_rules=frozenset(_split(args.only)) or None,
        categories=frozenset(Category(value) for value in _split(args.category)) or None,
        parallel_agents=not args.sequential,
        verbose=args.verbose,
        vision=VisionConfig(
            mode=VisionMode(args.vision),
            **({"model": args.vision_model} if args.vision_model else {}),
        ),
    )

    exit_code = EXIT_OK
    for path in args.files:
        if not path.exists():
            print(f"error: file not found: {path}", file=sys.stderr)
            exit_code = max(exit_code, EXIT_ERROR)
            continue
        try:
            report = Orchestrator(config).audit(path)
        except CatiaDiffError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, EXIT_ERROR)
            continue
        if report.extraction_failed:
            for warning in report.warnings:
                print(f"error: {path.name}: {warning}", file=sys.stderr)
            exit_code = max(exit_code, EXIT_ERROR)
            continue
        _print_report(report, config, quiet=args.quiet)
        if args.fail_on != "never" and not report.is_clean(Severity(args.fail_on)):
            exit_code = max(exit_code, EXIT_FINDINGS)
    return exit_code


def _print_report(report: AuditReport, config: AuditConfig, *, quiet: bool) -> None:
    lang = config.language
    colored = sys.stdout.isatty()
    print(f"\n{report.document.name}  [{report.profile}]  {report.summary_line(lang)}")
    if not quiet:
        for finding in report.findings:
            prefix = _ANSI[finding.severity] if colored else ""
            suffix = _RESET if colored else ""
            label = finding.severity.label(lang).upper().ljust(8)
            sheet = finding.evidence.sheet_index + 1
            print(
                f"  {prefix}{label}{suffix} {finding.rule_id}  "
                f"[{'sayfa' if lang == 'tr' else 'sheet'} {sheet}] "
                f"{finding.localized_message(lang)}"
            )
    for path in report.overlays:
        print(f"  → {path}")
    if report.warnings and not quiet:
        for warning in report.warnings:
            print(f"  ! {warning}")


def _cmd_rules(args: argparse.Namespace) -> int:
    lang = args.lang
    for rule in all_rules():
        if args.profile and Profile(args.profile) not in rule.meta.profiles:
            continue
        if args.category and rule.meta.category.value != args.category:
            continue
        title = rule.meta.title_tr if lang == "tr" else rule.meta.title
        standards = f"  [{', '.join(rule.meta.standards)}]" if rule.meta.standards else ""
        print(
            f"{rule.meta.id:8s} {rule.meta.severity.label(lang):8s} "
            f"{rule.meta.category.value:13s} {title}{standards}"
        )
    return EXIT_OK


def _cmd_ui(args: argparse.Namespace) -> int:
    """Serve the dashboard; blocks until interrupted."""
    from catia_diff.ui import run as run_ui

    try:
        print(f"catia-diff dashboard: http://{args.host}:{args.port}  (Ctrl+C to stop)")
        run_ui(
            host=args.host,
            port=args.port,
            debug=args.debug,
            language=args.lang,
            profile=args.profile,
        )
    except CatiaDiffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    return EXIT_OK


def _cmd_formats() -> int:
    from catia_diff.extract.registry import SUPPORTED_SUFFIXES

    print("supported input formats: " + ", ".join(SUPPORTED_SUFFIXES))
    print("DWG: convert to DXF first (e.g. ODA File Converter)")
    return EXIT_OK


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
