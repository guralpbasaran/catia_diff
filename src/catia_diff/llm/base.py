"""Vision backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from catia_diff.extract.raster import encode_image

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ImageRef:
    """An image handed to the model, with the label used in the prompt."""

    media_type: str
    data: str  # base64, no newlines
    label: str = "sheet"

    @classmethod
    def from_path(cls, path: Path, label: str | None = None) -> ImageRef:
        media_type, data = encode_image(path)
        return cls(media_type=media_type, data=data, label=label or path.name)


class VisionModel(ABC):
    """Anything that can turn images into a validated pydantic model."""

    name: str = "vision"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def extract(
        self,
        images: Sequence[ImageRef],
        instruction: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        """Run the model and return an instance of ``schema``."""

    def close(self) -> None:  # pragma: no cover - optional hook
        return None
