"""Any service that speaks the OpenAI Chat Completions shape (ADR 0010).

Registered with `provider: "openai_compatible"` and
`config: {"base_url": "...", "api_key": "...", "model": "..."}`. One request shape covers
OpenAI, xAI, OpenRouter, Google Gemini's compatibility endpoint, Mistral, Groq, Together
and a local Ollama; the Models page holds the base URL for each, and *Other* takes any.

Plain HTTP rather than a vendor SDK: this is a protocol many vendors implement, not one
vendor's API, and no one SDK is the supported way to call all of them. Only the parts every
implementation accepts are sent — no `response_format`, `temperature` or token limit, each
of which some of them reject — and the answer is read leniently (`vision_llm.read_answer`).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from curvevision.core.errors import ProviderError
from curvevision.domain.enums import ModelKind, ShapeType
from curvevision.ml.base import (
    InferenceFrame,
    InferenceRequest,
    InferenceResult,
    ModelDescriptor,
)
from curvevision.ml.vision_llm import (
    SYSTEM_PROMPT,
    as_base64,
    detect_frames,
    detection_prompt,
    prepared_image,
)

DEFAULT_TIMEOUT = 120.0
DISCOVERY_TIMEOUT = 20.0
#: Model ids that are plainly not chat models, for services whose model list does not say
#: which take images (OpenAI's does not). A filter of convenience, not a promise: a model
#: it lets through may still refuse images, and the first run says so.
NOT_CHAT = re.compile(
    r"embed|whisper|tts|dall-e|moderation|davinci|babbage|transcribe|audio|realtime"
    r"|speech|rerank|guard",
    re.IGNORECASE,
)


class OpenAICompatibleProvider:
    id = "openai_compatible"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def list_models(self, config: dict[str, Any]) -> list[ModelDescriptor]:
        base = _base_url(config)
        try:
            async with self._session(config) as client:
                response = await client.get(
                    f"{base}/models", headers=_headers(config), timeout=DISCOVERY_TIMEOUT
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {base}: {exc}") from exc
        if response.status_code == 404:
            raise ProviderError(f"{base} has no model list (404). Check the base URL.")
        _raise_for(response, base)
        try:
            entries = response.json()["data"]
            listed = [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                f'{base}/models did not answer with a model list (`{{"data": [...]}}`)'
            ) from exc
        return [
            ModelDescriptor(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                kind=ModelKind.DETECTOR,
                shape_types=(ShapeType.RECTANGLE,),
                open_vocabulary=True,
            )
            for entry in listed
            if _takes_images(entry)
        ]

    async def infer(self, request: InferenceRequest, config: dict[str, Any]) -> InferenceResult:
        base = _base_url(config)
        model = str(config.get("model") or request.model)
        prompt = detection_prompt(request.classes)
        async with self._session(config) as client:

            async def ask(frame: InferenceFrame) -> str:
                return await _ask(client, config, base, model, frame, prompt)

            return await detect_frames(request, ask, service=model)

    def _session(self, config: dict[str, Any]) -> httpx.AsyncClient:
        if self._client is not None:
            return _NonClosing(self._client)  # type: ignore[return-value]
        return httpx.AsyncClient(timeout=float(config.get("timeout_seconds", DEFAULT_TIMEOUT)))


async def _ask(
    client: httpx.AsyncClient,
    config: dict[str, Any],
    base: str,
    model: str,
    frame: InferenceFrame,
    prompt: str,
) -> str:
    data, media_type = prepared_image(frame)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{as_base64(data)}"},
                    },
                ],
            },
        ],
    }
    try:
        response = await client.post(
            f"{base}/chat/completions", json=body, headers=_headers(config)
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach {base}: {exc}") from exc
    if response.status_code == 404:
        raise ProviderError(f"{base} has no model {model!r} (404).")
    _raise_for(response, base)
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("the service's reply had no message in it") from exc
    if message.get("refusal"):
        raise ValueError(f"the model declined to answer ({str(message['refusal'])[:200]})")
    content = message.get("content")
    if isinstance(content, list):  # Some services answer in parts, as requests are sent.
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _raise_for(response: httpx.Response, base: str) -> None:
    if response.status_code in (401, 403):
        raise ProviderError(f"{base} refused the API key ({response.status_code}). Check the key.")
    if response.status_code == 429:
        raise ProviderError(f"{base} is rate-limiting this key, or it is out of credit (429).")
    if response.is_error:
        raise ProviderError(f"{base} returned {response.status_code}: {response.text[:400]}")


def _base_url(config: dict[str, Any]) -> str:
    base = str(config.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise ProviderError("This model has no base URL configured")
    return base


def _headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {str(k): str(v) for k, v in config.get("headers", {}).items()}
    if key := str(config.get("api_key") or "").strip():
        headers["authorization"] = f"Bearer {key}"
    return headers


def _takes_images(entry: dict[str, Any]) -> bool:
    """Whether a listed model takes images, where the service says (OpenRouter does)."""
    architecture = entry.get("architecture")
    if isinstance(architecture, dict) and isinstance(architecture.get("input_modalities"), list):
        return "image" in architecture["input_modalities"]
    return not NOT_CHAT.search(str(entry.get("id", "")))


class _NonClosing:
    """An injected client (the tests') that `async with` must not close."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        return None


__all__ = ["OpenAICompatibleProvider"]
