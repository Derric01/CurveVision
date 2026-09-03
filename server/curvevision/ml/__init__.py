"""Model providers.

Registering a provider is one call. The core never imports a vendor SDK; a provider that
needs one lives in its own optional package.
"""

from __future__ import annotations

from curvevision.core.errors import ProviderError
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

__all__ = [
    "HttpModelProvider",
    "InferenceFrame",
    "InferenceRequest",
    "InferenceResult",
    "ModelDescriptor",
    "ModelProvider",
    "PredictedShape",
    "PredictedTag",
    "all_providers",
    "get_provider",
    "register_provider",
]
