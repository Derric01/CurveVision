"""HTTP inference provider -- the default way to attach a model to CurveVision.

The contract, deliberately small enough to implement in a 30-line FastAPI script:

``POST <endpoint>`` with::

    {
      "model": "yolo-v8n",
      "confidence_threshold": 0.5,
      "classes": ["forklift", "pallet"],
      "prompts": {...},
      "options": {...},
      "frames": [
        {"frame": 0, "width": 1920, "height": 1080,
         "image": "<base64>", "content_type": "image/jpeg"}
      ]
    }

``classes`` is what to look for, by name, for an **open-vocabulary** model -- YOLO-World,
Grounding DINO, OWL-ViT and their like, which take the class list as text at inference time
rather than having one baked in. A closed-vocabulary server can ignore the key; CurveVision
will not send a non-empty one to a model that did not declare itself open-vocabulary.

Response::

    {
      "shapes": [
        {"frame": 0, "label": "person", "type": "rectangle",
         "points": [10, 20, 110, 220], "confidence": 0.93}
      ],
      "tags": [{"frame": 0, "label": "daytime", "confidence": 0.8}],
      "warnings": []
    }

Coordinates are **absolute pixels** in the frame's own coordinate space. A provider that
returns normalised coordinates should say so via ``"normalised": true`` on the response,
and this adapter rescales.

``GET <endpoint>/models`` optionally returns ``{"models": [...]}`` for discovery; when it
is absent, the labels configured on the registration are used instead. A model entry may
carry ``"open_vocabulary": true``, which is how a server says it reads ``classes``; without
it CurveVision treats the model's ``labels`` as the whole of what it can find, and refuses
to ask it for anything else rather than sending a prompt that would be silently dropped.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from curvevision.core.errors import ProviderError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import ModelKind, ShapeType
from curvevision.ml.base import (
    InferenceRequest,
    InferenceResult,
    ModelDescriptor,
    PredictedShape,
    PredictedTag,
)

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120.0


class HttpModelProvider:
    id = "http"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def list_models(self, config: dict[str, Any]) -> list[ModelDescriptor]:
        endpoint = _endpoint(config)
        try:
            async with self._session(config) as client:
                response = await client.get(f"{endpoint.rstrip('/')}/models")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Discovery is optional: a provider that does not implement it is not broken.
            logger.info(
                "model discovery unavailable; using configured labels",
                extra={"endpoint": endpoint, "error": str(exc)},
            )
            return []

        return [
            ModelDescriptor(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", entry.get("id", ""))),
                kind=_parse_kind(entry.get("kind")),
                labels=tuple(entry.get("labels", ())),
                shape_types=tuple(
                    _parse_shape_type(value) for value in entry.get("shape_types", ["rectangle"])
                ),
                description=entry.get("description"),
                open_vocabulary=bool(entry.get("open_vocabulary", False)),
                metadata=dict(entry.get("metadata", {})),
            )
            for entry in payload.get("models", [])
        ]

    async def infer(self, request: InferenceRequest, config: dict[str, Any]) -> InferenceResult:
        endpoint = _endpoint(config)
        body = {
            "model": request.model,
            "confidence_threshold": request.confidence_threshold,
            "classes": request.classes,
            "prompts": request.prompts,
            "options": {**config.get("options", {}), **request.options},
            "frames": [
                {
                    "frame": frame.frame,
                    "width": frame.width,
                    "height": frame.height,
                    "content_type": frame.content_type,
                    "image": base64.b64encode(frame.image).decode("ascii"),
                }
                for frame in request.frames
            ],
        }

        try:
            async with self._session(config) as client:
                response = await client.post(endpoint, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Inference endpoint returned {exc.response.status_code}: {exc.response.text[:400]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach the inference endpoint: {exc}") from exc
        except ValueError as exc:
            raise ProviderError("Inference endpoint returned a non-JSON response") from exc

        return self._parse_result(payload, request)

    def _parse_result(self, payload: dict[str, Any], request: InferenceRequest) -> InferenceResult:
        if not isinstance(payload, dict):
            raise ProviderError("Inference response must be a JSON object")

        normalised = bool(payload.get("normalised", False))
        sizes = {frame.frame: (frame.width, frame.height) for frame in request.frames}

        shapes: list[PredictedShape] = []
        for entry in payload.get("shapes", []):
            try:
                frame = int(entry["frame"])
                label = str(entry["label"])
                shape_type = _parse_shape_type(entry.get("type", "rectangle"))
                points = [float(value) for value in entry.get("points", [])]
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(f"Malformed shape in inference response: {entry!r}") from exc

            if normalised:
                width, height = sizes.get(frame, (None, None))
                if not width or not height:
                    raise ProviderError(
                        "Provider returned normalised coordinates but the frame size is "
                        "unknown, so they cannot be converted to pixels"
                    )
                points = [
                    value * (width if index % 2 == 0 else height)
                    for index, value in enumerate(points)
                ]

            confidence = float(entry.get("confidence", 1.0))
            if confidence < request.confidence_threshold:
                continue

            shapes.append(
                PredictedShape(
                    frame=frame,
                    label=label,
                    shape_type=shape_type,
                    points=points,
                    confidence=confidence,
                    attributes=dict(entry.get("attributes", {})),
                    mask=entry.get("mask"),
                    elements=list(entry.get("elements", [])),
                    track_id=entry.get("track_id"),
                )
            )

        tags = [
            PredictedTag(
                frame=int(entry["frame"]),
                label=str(entry["label"]),
                confidence=float(entry.get("confidence", 1.0)),
            )
            for entry in payload.get("tags", [])
            if float(entry.get("confidence", 1.0)) >= request.confidence_threshold
        ]

        return InferenceResult(
            shapes=shapes,
            tags=tags,
            warnings=[str(item) for item in payload.get("warnings", [])],
            duration_ms=payload.get("duration_ms"),
        )

    def _session(self, config: dict[str, Any]) -> httpx.AsyncClient:
        if self._client is not None:
            return _NonClosing(self._client)  # type: ignore[return-value]
        return httpx.AsyncClient(
            timeout=float(config.get("timeout_seconds", DEFAULT_TIMEOUT)),
            headers=_headers(config),
        )


class _NonClosing:
    """Wrap an injected client so ``async with`` does not close it.

    Tests inject a client bound to an ASGI transport and reuse it across calls.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _endpoint(config: dict[str, Any]) -> str:
    endpoint = config.get("endpoint")
    if not endpoint:
        raise ProviderError("This model registration has no 'endpoint' configured")
    return str(endpoint)


def _headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    headers.update({str(k): str(v) for k, v in config.get("headers", {}).items()})
    if token := config.get("api_key"):
        headers["authorization"] = f"Bearer {token}"
    return headers


def _parse_kind(value: object) -> ModelKind:
    try:
        return ModelKind(str(value))
    except ValueError:
        return ModelKind.DETECTOR


def _parse_shape_type(value: object) -> ShapeType:
    try:
        return ShapeType(str(value))
    except ValueError as exc:
        raise ProviderError(f"Unknown shape type in inference response: {value!r}") from exc
