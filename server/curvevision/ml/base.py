"""The ``ModelProvider`` abstraction.

CurveVision never bundles model weights and never imports a vendor SDK in its core. A
provider is a small adapter that turns "run this model on these frames" into whatever the
operator's inference stack speaks.

That choice matters for three reasons:

1. **Licensing.** Model weights frequently carry more restrictive terms than the code
   around them (several popular detection and segmentation checkpoints are
   non-commercial). Keeping them entirely on the operator's side of the boundary keeps the
   CurveVision core unencumbered.
2. **Choice.** Triton, TorchServe, BentoML, Ray Serve, KServe, a hand-written FastAPI
   script, or a hosted vendor all satisfy an HTTP contract. Bundling a serving platform
   would force one of them on every operator.
3. **Testability.** The whole inference path is exercised in CI against an in-process
   provider, with no network and no GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from curvevision.domain.enums import ModelKind, ShapeType


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    """What a model is and what it emits.

    ``labels`` is the model's own label space. CurveVision maps it onto the project's
    labels at run time, so nothing about the model is hard-coded into the platform.

    **Unless the model is open-vocabulary**, in which case there is no fixed label space at
    all -- see ``open_vocabulary``.
    """

    id: str
    name: str
    kind: ModelKind
    labels: tuple[str, ...] = ()
    shape_types: tuple[ShapeType, ...] = (ShapeType.RECTANGLE,)
    description: str | None = None
    #: The model takes the classes to look for as text, at inference time.
    #:
    #: YOLO-World, Grounding DINO and OWL-ViT work this way: you hand them
    #: ``["forklift", "pallet", "hi-vis vest"]`` and they detect those, with no retraining
    #: and no fixed class list. For such a model ``labels`` is a *suggestion* -- a sensible
    #: default, or what the operator happened to configure -- rather than the boundary of
    #: what it can find.
    #:
    #: The distinction has to be on the descriptor rather than inferred, because the two
    #: kinds fail in opposite directions. Sending classes to a closed-vocabulary model does
    #: nothing and the caller cannot tell; *not* sending them to an open-vocabulary one
    #: returns nothing at all, which looks like a broken model rather than an empty prompt.
    open_vocabulary: bool = False
    #: Free-form provider metadata (input size, device, version).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InferenceFrame:
    """One frame handed to a model."""

    frame: int
    image: bytes
    content_type: str = "image/jpeg"
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class InferenceRequest:
    model: str
    frames: list[InferenceFrame]
    confidence_threshold: float = 0.5
    #: What to look for, by name, for an open-vocabulary model.
    #:
    #: Empty means "whatever the model detects by default", which is the only sensible
    #: reading for a closed-vocabulary model and a deliberate choice for an open one.
    #: `services.inference` fills this from the project's own label names when the caller
    #: does not say otherwise, because a project that has declared `forklift` and `pallet`
    #: has already said what it cares about.
    classes: list[str] = field(default_factory=list)
    #: Interactive prompts for `interactor` models: click points, a box, a previous mask.
    #: Geometry, not text -- `classes` is where text belongs.
    prompts: dict[str, Any] = field(default_factory=dict)
    #: Provider-specific knobs the operator configured on the model registration.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PredictedShape:
    """One model prediction, in image pixel coordinates."""

    frame: int
    label: str
    shape_type: ShapeType
    points: list[float]
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    mask: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)
    #: Set by trackers, so predictions across frames can become one track.
    track_id: int | None = None


@dataclass(slots=True)
class PredictedTag:
    frame: int
    label: str
    confidence: float = 1.0


@dataclass(slots=True)
class InferenceResult:
    shapes: list[PredictedShape] = field(default_factory=list)
    tags: list[PredictedTag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Wall-clock milliseconds the provider reported, when it does.
    duration_ms: float | None = None


@runtime_checkable
class ModelProvider(Protocol):
    id: str

    async def list_models(self, config: dict[str, Any]) -> list[ModelDescriptor]:
        """Models this provider can serve under ``config``."""

    async def infer(self, request: InferenceRequest, config: dict[str, Any]) -> InferenceResult:
        """Run inference. Must raise ``ProviderError`` on an unusable response."""
