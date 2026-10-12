"""Claude, through Anthropic's own Python SDK (ADR 0010).

Registered with `provider: "anthropic"` and `config: {"api_key": "...", "model": "..."}`.
The model list comes from Anthropic's Models API, narrowed to the models that take images,
so no model name is written down here to go stale.

Three things the API offers are used where the chosen model supports them:

* **Structured outputs** (`output_config.format`), when the Models API says the model has
  them: the answer is then held to `vision_llm.ANSWER_SCHEMA` by the service, not by hope.
* **Server-side fallback** (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`)
  on the models that support it: a frame a safety classifier declines is re-run on
  Anthropic's recommended substitute, inside the same call, rather than coming back empty.
* **Refusals** are read from `stop_reason` before the content, and reported per frame.
"""

from __future__ import annotations

from typing import Any

import anthropic

from curvevision.core.errors import ProviderError
from curvevision.domain.enums import ModelKind, ShapeType
from curvevision.ml.base import (
    InferenceFrame,
    InferenceRequest,
    InferenceResult,
    ModelDescriptor,
)
from curvevision.ml.vision_llm import (
    ANSWER_SCHEMA,
    SYSTEM_PROMPT,
    as_base64,
    detect_frames,
    detection_prompt,
    prepared_image,
)

#: Models the `fallbacks: "default"` form is offered on, from Anthropic's model notes. A
#: model not listed is called without it: the parameter is a 400 where it is unsupported.
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Room for a frame crowded with objects; an answer cut off mid-JSON is lost whole.
MAX_TOKENS = 16000
DEFAULT_TIMEOUT = 120.0


class AnthropicProvider:
    id = "anthropic"

    def __init__(self, http_client: Any | None = None) -> None:
        #: The tests' `DefaultAsyncHttpxClient` over a mock transport; None in production,
        #: where each call builds and closes its own client.
        self._http_client = http_client

    async def list_models(self, config: dict[str, Any]) -> list[ModelDescriptor]:
        """The Claude models this key can use that take images."""
        client = self._client(config)
        try:
            models = [info async for info in client.models.list()]
        except anthropic.APIError as exc:
            raise _provider_error(exc) from exc
        finally:
            await self._release(client)
        return [
            ModelDescriptor(
                id=info.id,
                name=info.display_name or info.id,
                kind=ModelKind.DETECTOR,
                shape_types=(ShapeType.RECTANGLE,),
                open_vocabulary=True,
                description="Claude, asked for boxes around the project's labels",
            )
            for info in models
            if _supported(info, "image_input") is not False
        ]

    async def infer(self, request: InferenceRequest, config: dict[str, Any]) -> InferenceResult:
        model = str(config.get("model") or request.model)
        client = self._client(config)
        try:
            structured = await self._structured_outputs(client, model)
            prompt = detection_prompt(request.classes)

            async def ask(frame: InferenceFrame) -> str:
                return await self._ask(client, model, frame, prompt, structured=structured)

            return await detect_frames(request, ask, service="Claude")
        finally:
            await self._release(client)

    async def _ask(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        frame: InferenceFrame,
        prompt: str,
        *,
        structured: bool,
    ) -> str:
        data, media_type = prepared_image(frame)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": as_base64(data),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        if structured:
            params["output_config"] = {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}}
        try:
            if model in FALLBACK_MODELS:
                message: Any = await client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                message = await client.messages.create(**params)
        except anthropic.APIError as exc:
            raise _provider_error(exc, model) from exc

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ValueError("Claude declined to answer" + (f" ({category})" if category else ""))
        if message.stop_reason == "max_tokens":
            raise ValueError("Claude's answer ran past the length limit")
        return "".join(block.text for block in message.content if block.type == "text")

    async def _structured_outputs(self, client: anthropic.AsyncAnthropic, model: str) -> bool:
        """Whether the model can be held to a schema; also where a bad model name surfaces."""
        try:
            info = await client.models.retrieve(model)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise _provider_error(exc, model) from exc
        except anthropic.NotFoundError as exc:
            raise _provider_error(exc, model) from exc
        except anthropic.APIError:
            return False  # The request itself will say what is wrong, if anything is.
        return _supported(info, "structured_outputs") is True

    def _client(self, config: dict[str, Any]) -> anthropic.AsyncAnthropic:
        key = str(config.get("api_key") or "").strip()
        if not key:
            raise ProviderError("Claude needs an Anthropic API key; add one to this model")
        return anthropic.AsyncAnthropic(
            api_key=key,
            base_url=str(config["base_url"]) if config.get("base_url") else None,
            http_client=self._http_client,
            max_retries=2,
            timeout=float(config.get("timeout_seconds", DEFAULT_TIMEOUT)),
        )

    async def _release(self, client: anthropic.AsyncAnthropic) -> None:
        if self._http_client is None:
            await client.close()


def _supported(info: Any, capability: str) -> bool | None:
    """A Models API capability leaf, or None when the response does not say."""
    capabilities = getattr(info, "capabilities", None)
    if capabilities is None:
        return None
    node = (
        capabilities.get(capability)
        if isinstance(capabilities, dict)
        else getattr(capabilities, capability, None)
    )
    if node is None:
        return None
    supported = (
        node.get("supported") if isinstance(node, dict) else getattr(node, "supported", None)
    )
    return bool(supported) if supported is not None else None


def _provider_error(exc: anthropic.APIError, model: str | None = None) -> ProviderError:
    """What went wrong, in words the Models page and the editor can show as they are."""
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError("Anthropic refused the API key (401). Check the key.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError("Anthropic refused this key access (403). Check its permissions.")
    if isinstance(exc, anthropic.NotFoundError):
        named = f" {model!r}" if model else ""
        return ProviderError(f"Anthropic has no model{named} for this key (404).")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(
            "Anthropic is rate-limiting this key (429). Run fewer frames at a time, or wait."
        )
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError(f"Anthropic returned {exc.status_code}: {exc.message}"[:500])
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"Could not reach Anthropic: {exc}")
    return ProviderError(f"Anthropic: {exc}"[:500])


__all__ = ["AnthropicProvider"]
