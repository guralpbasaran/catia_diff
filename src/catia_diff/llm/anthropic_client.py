"""Claude Vision backend.

Structured output is requested with ``output_config.format`` (JSON schema) so
the response is guaranteed to be parseable, and the request opts into
server-side refusal fallbacks - a drawing that trips a safety classifier then
still returns a usable answer from a fallback model instead of an exception.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from catia_diff.config import VisionConfig
from catia_diff.errors import VisionUnavailableError
from catia_diff.llm.base import ImageRef, VisionModel
from catia_diff.llm.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Server-side refusal fallbacks (routes by refusal category).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicVisionModel(VisionModel):
    """Multimodal extraction through the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, config: VisionConfig | None = None, client: Any | None = None) -> None:
        self.config = config or VisionConfig()
        self._client = client
        self._beta_supported = True

    # ------------------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise VisionUnavailableError(
                    "the 'anthropic' package is not installed - "
                    "install it with: pip install 'catia-diff[llm]'"
                ) from exc
            try:
                self._client = anthropic.Anthropic(timeout=self.config.timeout_s)
            except Exception as exc:  # missing credentials, bad proxy, ...
                raise VisionUnavailableError(f"cannot create an Anthropic client: {exc}") from exc
        return self._client

    @property
    def available(self) -> bool:
        try:
            _ = self.client
        except VisionUnavailableError:
            return False
        return True

    # ------------------------------------------------------------------
    def extract(
        self,
        images: Sequence[ImageRef],
        instruction: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        if not images:
            raise ValueError("at least one image is required")
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image.media_type, "data": image.data},
            }
            for image in images
        ]
        content.append({"type": "text", "text": instruction})

        payload = self._request(
            system=system or SYSTEM_PROMPT,
            content=content,
            schema=schema,
        )
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise VisionUnavailableError(
                f"the model returned data that does not match {schema.__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    def _request(self, *, system: str, content: list[dict[str, Any]], schema: type[T]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _strict_schema(schema),
                },
                "effort": self.config.effort,
            },
        }
        response = self._send(kwargs)
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise VisionUnavailableError(
                "the model declined to process this drawing "
                f"(category: {getattr(details, 'category', 'unknown')})"
            )
        return _first_json(response)

    def _send(self, kwargs: dict[str, Any]) -> Any:
        import anthropic

        if self._beta_supported:
            try:
                return self.client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            except (TypeError, AttributeError) as exc:
                logger.debug("beta refusal-fallback path unavailable (%s); using plain path", exc)
                self._beta_supported = False
            except anthropic.APIStatusError as exc:
                if exc.status_code != 400:
                    raise
                logger.debug("beta refusal-fallback rejected (%s); using plain path", exc)
                self._beta_supported = False
        try:
            return self.client.messages.create(**kwargs)
        except anthropic.APIConnectionError as exc:
            raise VisionUnavailableError(f"cannot reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise VisionUnavailableError(
                f"Anthropic API error {exc.status_code}: {getattr(exc, 'message', exc)}"
            ) from exc


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with ``additionalProperties: false`` everywhere (strict mode)."""
    raw = schema.model_json_schema()
    _harden(raw)
    return raw


def _harden(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for value in node.values():
            _harden(value)
    elif isinstance(node, list):
        for item in node:
            _harden(item)


def _first_json(response: Any) -> Any:
    """Read the JSON document out of a structured-output response."""
    parsed = getattr(response, "parsed_output", None)
    if parsed is not None:
        return parsed
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = block.text.strip()
            if text:
                return json.loads(text)
    raise VisionUnavailableError("the model returned no text block")
