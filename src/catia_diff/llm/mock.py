"""Deterministic vision backends for tests, demos and ``--vision off`` runs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from catia_diff.errors import VisionUnavailableError
from catia_diff.llm.base import ImageRef, VisionModel

T = TypeVar("T", bound=BaseModel)


class MockVisionModel(VisionModel):
    """Replays canned responses; records every call for assertions."""

    name = "mock"

    def __init__(
        self,
        responses: Sequence[BaseModel] | Callable[[Sequence[ImageRef], str], BaseModel] | None = None,
    ) -> None:
        self._responses = list(responses) if isinstance(responses, Sequence) else None
        self._factory = responses if callable(responses) else None
        self.calls: list[tuple[int, str]] = []

    def extract(
        self,
        images: Sequence[ImageRef],
        instruction: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        self.calls.append((len(images), instruction))
        if self._factory is not None:
            result = self._factory(images, instruction)
        elif self._responses:
            result = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        else:
            result = schema()
        if not isinstance(result, schema):
            raise TypeError(f"mock returned {type(result).__name__}, expected {schema.__name__}")
        return result


class NullVisionModel(VisionModel):
    """Used when vision is switched off: every call is a hard error."""

    name = "null"

    @property
    def available(self) -> bool:
        return False

    def extract(self, images, instruction, schema, *, system=None):  # type: ignore[override]
        raise VisionUnavailableError("vision extraction is disabled (--vision off)")
