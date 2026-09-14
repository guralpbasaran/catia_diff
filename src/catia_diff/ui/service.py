"""What the dashboard does when a button is pressed.

File decoding, running the pipeline and turning a report into downloadable
bytes.  Dash is not imported here either: the callbacks in
:mod:`catia_diff.ui.app` are one-liners over these functions, so the behaviour
can be tested without a browser.
"""

from __future__ import annotations

import base64
import binascii
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from catia_diff.agents.orchestrator import Orchestrator
from catia_diff.config import AuditConfig, Profile
from catia_diff.errors import CatiaDiffError, UnsupportedFormatError
from catia_diff.extract.registry import SUPPORTED_SUFFIXES, get_extractor
from catia_diff.models.findings import AuditReport
from catia_diff.reporting.render import to_html, to_json, to_markdown

#: Upload ceiling.  A drawing that does not fit is a problem to report, not to
#: silently truncate.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

#: How many run directories are kept before the oldest are removed.
KEEP_RUNS = 5

_MIME = {"json": "application/json", "md": "text/markdown", "html": "text/html"}


class UploadError(CatiaDiffError):
    """The uploaded payload cannot be turned into a drawing file."""


@dataclass(frozen=True)
class AuditOutcome:
    """Everything one audit run hands back to the screen."""

    report: AuditReport | None
    report_json: str | None
    overlay_uri: str | None
    message: str
    ok: bool


# ---------------------------------------------------------------------------
def workspace() -> Path:
    """Root directory for this process' run outputs."""
    root = Path(tempfile.gettempdir()) / "catia-diff-ui"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_run_dir(root: Path | None = None) -> Path:
    root = root or workspace()
    run_dir = root / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True, exist_ok=True)
    prune_runs(root)
    return run_dir


def prune_runs(root: Path, keep: int = KEEP_RUNS) -> None:
    """Keep the newest ``keep`` run directories; drop the rest."""
    runs = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def decode_upload(contents: str, filename: str, dest_dir: Path) -> Path:
    """Write a ``dcc.Upload`` payload to disk, or say exactly why it cannot be.

    The suffix is checked with the real extractor registry, so an unsupported
    file gets the same message (DWG hint included) as on the command line.
    """
    if not filename:
        raise UploadError("The upload carries no file name.")
    name = Path(filename).name
    get_extractor(Path(name))  # raises UnsupportedFormatError with the CLI's wording

    if not contents or "," not in contents:
        raise UploadError(f"'{name}' arrived empty or malformed.")
    payload = contents.split(",", 1)[1]
    try:
        blob = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UploadError(f"'{name}' is not valid base64 content.") from exc
    if not blob:
        raise UploadError(f"'{name}' is empty.")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"'{name}' is {len(blob) / 1e6:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB."
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    path.write_bytes(blob)
    return path


def ui_config(
    *,
    profile: str = Profile.ISO.value,
    language: str = "tr",
    output_dir: Path,
) -> AuditConfig:
    """Audit configuration for a screen run: overlay yes, report files no.

    Nothing is written next to the user's drawing - the downloads are rendered
    from the report object on demand.
    """
    return AuditConfig(
        profile=Profile(profile),
        language=language,
        output_dir=output_dir,
        formats=(),
        render_overlay=True,
    )


def audit_path(path: Path, config: AuditConfig) -> AuditReport:
    return Orchestrator(config).audit(path)


def run_audit(
    *,
    contents: str | None = None,
    filename: str | None = None,
    path: Path | None = None,
    profile: str = Profile.ISO.value,
    language: str = "tr",
    root: Path | None = None,
) -> AuditOutcome:
    """Audit an upload (or a local file) and package the result for the screen.

    Every failure mode comes back as an outcome with ``ok=False`` and a message
    the user can act on; nothing is swallowed and nothing is guessed.
    """
    run_dir = new_run_dir(root)
    try:
        if path is None:
            if contents is None or filename is None:
                raise UploadError("No file was uploaded.")
            path = decode_upload(contents, filename, run_dir)
        elif not path.exists():
            raise UploadError(f"File not found: {path}")

        config = ui_config(profile=profile, language=language, output_dir=run_dir)
        report = audit_path(path, config)
    except CatiaDiffError as exc:
        return AuditOutcome(None, None, None, str(exc), ok=False)

    if report.extraction_failed:
        detail = "; ".join(report.warnings) or "the file could not be read"
        return AuditOutcome(report, report.model_dump_json(), None, detail, ok=False)

    return AuditOutcome(
        report=report,
        report_json=report.model_dump_json(),
        overlay_uri=overlay_data_uri(report.overlays[0]) if report.overlays else None,
        message=report.summary_line(language),
        ok=True,
    )


def parse_report(payload: str | None) -> AuditReport | None:
    """Rebuild the report a callback stored in the browser."""
    if not payload:
        return None
    return AuditReport.model_validate_json(payload)


def overlay_data_uri(path: Path) -> str:
    """Inline the marked-up sheet so the page needs no static file route."""
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def report_download(report: AuditReport, fmt: str, lang: str = "tr") -> tuple[str, str, str]:
    """``(filename, content, mime)`` for one download button."""
    stem = report.document.stem or "drawing"
    fmt = fmt.lower()
    if fmt == "json":
        return f"{stem}_audit.json", to_json(report), _MIME["json"]
    if fmt in {"md", "markdown"}:
        return f"{stem}_audit.md", to_markdown(report, lang), _MIME["md"]
    if fmt == "html":
        return f"{stem}_audit.html", to_html(report, lang), _MIME["html"]
    raise ValueError(f"unknown report format: {fmt}")


def sample_drawing() -> Path | None:
    """The bundled example drawing, when the package is used from a checkout."""
    candidate = Path(__file__).resolve().parents[3] / "examples" / "sample_plate.dxf"
    return candidate if candidate.exists() else None


def supported_suffixes() -> tuple[str, ...]:
    return SUPPORTED_SUFFIXES


__all__ = [
    "MAX_UPLOAD_BYTES",
    "AuditOutcome",
    "UnsupportedFormatError",
    "UploadError",
    "decode_upload",
    "overlay_data_uri",
    "parse_report",
    "report_download",
    "run_audit",
    "sample_drawing",
    "supported_suffixes",
    "ui_config",
]
