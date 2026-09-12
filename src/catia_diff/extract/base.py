"""Extractor interface and shared helpers."""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from catia_diff.config import AuditConfig
from catia_diff.models.drawing import DrawingDocument


class SourceExtractor(ABC):
    """Turns one source file into a :class:`DrawingDocument`."""

    name: ClassVar[str] = "extractor"
    suffixes: ClassVar[tuple[str, ...]] = ()

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    @abstractmethod
    def extract(self, path: Path, config: AuditConfig) -> DrawingDocument:
        """Parse ``path``.  Implementations must never raise for recoverable
        problems - they append to ``DrawingDocument.warnings`` instead."""

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def id_factory(prefix: str):
        counter = itertools.count(1)
        return lambda: f"{prefix}{next(counter):04d}"
