"""Exception hierarchy for the audit pipeline."""

from __future__ import annotations


class CatiaDiffError(Exception):
    """Base class for every error raised by this package."""


class UnsupportedFormatError(CatiaDiffError):
    """No extractor is registered for the given file."""


class ExtractionError(CatiaDiffError):
    """The source file could not be parsed."""


class MissingDependencyError(CatiaDiffError):
    """An optional dependency required by the requested feature is not installed."""

    def __init__(self, package: str, feature: str, extra: str | None = None) -> None:
        hint = f"pip install 'catia-diff[{extra}]'" if extra else f"pip install {package}"
        super().__init__(f"{feature} requires the '{package}' package. Install it with: {hint}")
        self.package = package
        self.feature = feature


class VisionUnavailableError(CatiaDiffError):
    """A vision backend was requested but cannot be used (no SDK, no credentials)."""


class RuleConfigurationError(CatiaDiffError):
    """A rule was registered twice or referenced but unknown."""
