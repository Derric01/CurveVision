"""Model providers.

Registering a provider is one call. Three are built in: `http`, CurveVision's own inference
contract for a model server somebody runs (ADR 0005), and `anthropic` and
`openai_compatible`, the hosted vision-language services a key is enough for (ADR 0010).
"""

from __future__ import annotations

from curvevision.core.errors import ProviderError
from curvevision.ml.anthropic_provider import AnthropicProvider
from curvevision.ml.base import (
    InferenceFrame,
    InferenceRequest,
    InferenceResult,
    ModelDescriptor,
    ModelProvider,
    PredictedShape,
    PredictedTag,
)
from curvevision.ml.http_provider import HttpModelProvider
from curvevision.ml.openai_compatible import OpenAICompatibleProvider

_PROVIDERS: dict[str, ModelProvider] = {}


def register_provider(provider: ModelProvider) -> ModelProvider:
    _PROVIDERS[provider.id] = provider
    return provider


def get_provider(provider_id: str) -> ModelProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ProviderError(
            f"Unknown model provider {provider_id!r}. Available: {available}"
        ) from exc


def all_providers() -> list[str]:
    return sorted(_PROVIDERS)


register_provider(HttpModelProvider())
register_provider(AnthropicProvider())
register_provider(OpenAICompatibleProvider())

__all__ = [
    "AnthropicProvider",
    "HttpModelProvider",
    "InferenceFrame",
    "InferenceRequest",
    "InferenceResult",
    "ModelDescriptor",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "PredictedShape",
    "PredictedTag",
    "all_providers",
    "get_provider",
    "register_provider",
]
