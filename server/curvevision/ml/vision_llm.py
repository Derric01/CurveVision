"""Asking a hosted vision-language model to find objects, and reading what it answers.

What every hosted provider shares (ADR 0010): the prompt, the answer's shape, the reading of
that answer into boxes, and the image as it is sent. The providers differ only in how a
request is made — `anthropic_provider` through Anthropic's SDK, `openai_compatible` over
the Chat Completions shape — so none of this is duplicated between them.

**Coordinates are normalised to 0 to 1000** on both axes, `[x_min, y_min, x_max, y_max]`.
Every service resizes an image before its model sees it, by rules the caller cannot
observe, so pixel coordinates would be in a size nobody here knows. A fraction of the frame
survives any resize.

**A model's answer is read leniently and kept strictly.** Code fences and prose around the
JSON are tolerated, because many models add them whatever they are told; a label the
request did not ask for, a box with no area, or coordinates that are not numbers are each
dropped and counted, never guessed at.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from curvevision.core.errors import ProviderError
from curvevision.domain.enums import ShapeType
from curvevision.ml.base import InferenceFrame, InferenceRequest, InferenceResult, PredictedShape

#: The scale coordinates are asked for in.
SCALE = 1000
#: Longest edge an image is sent at. Large enough to keep small objects findable; small
#: enough that no service resizes it again and the upload stays well under every limit.
MAX_EDGE = 1568
#: Total pixels an image is sent at, at most (Anthropic resizes past about 1.15 megapixels).
MAX_PIXELS = 1_150_000

SYSTEM_PROMPT = (
    "You are an object detector for an image annotation tool. You find instances of the "
    "requested classes in one image and report a tight bounding box for each. You answer "
    "with JSON only."
)

#: The answer's shape, for services that can be held to a schema.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}},
                    "confidence": {"type": "number"},
                },
                "required": ["label", "box", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["objects"],
    "additionalProperties": False,
}


def detection_prompt(classes: list[str]) -> str:
    """The instruction for one frame: what to find, and exactly how to answer."""
    names = ", ".join(json.dumps(name) for name in classes)
    return (
        f"Find every visible instance of these classes: {names}.\n"
        "Answer with one JSON object and nothing else:\n"
        '{"objects": [{"label": "<one of the classes, spelled exactly as given>", '
        '"box": [x_min, y_min, x_max, y_max], "confidence": <0 to 1>}]}\n'
        f"Coordinates are integers from 0 to {SCALE}, as a fraction of the image's width "
        f"(x) and height (y): 0 is the left or top edge, {SCALE} the right or bottom edge. "
        "Make each box tight around the visible part of the object. Report each object "
        'once. If none of the classes is visible, answer {"objects": []}.'
    )


@dataclass(slots=True)
class Detections:
    shapes: list[PredictedShape] = field(default_factory=list)
    #: What was dropped from this frame's answer, and why — for the run's warnings.
    dropped: list[str] = field(default_factory=list)


def read_answer(text: str, frame: InferenceFrame, classes: list[str]) -> Detections:
    """A model's answer for one frame, as boxes in that frame's pixels.

    Raises `ValueError` when the answer holds no JSON object at all; a frame like that is
    reported, not silently treated as empty.
    """
    document = _json_object(text)
    entries = document.get("objects")
    if not isinstance(entries, list):
        raise ValueError('the answer has no "objects" list')

    width, height = frame.width, frame.height
    if not width or not height:
        raise ValueError("the frame's size is unknown, so its boxes cannot be placed")
    canonical = {name.casefold(): name for name in classes}
    result = Detections()
    for entry in entries:
        if not isinstance(entry, dict):
            result.dropped.append("an entry that is not an object")
            continue
        label = canonical.get(str(entry.get("label", "")).strip().casefold())
        if label is None:
            result.dropped.append(f"a {str(entry.get('label'))!r}, which was not asked for")
            continue
        box = _box(entry.get("box"), width, height)
        if box is None:
            result.dropped.append(f"a {label} with an unusable box {entry.get('box')!r}")
            continue
        result.shapes.append(
            PredictedShape(
                frame=frame.frame,
                label=label,
                shape_type=ShapeType.RECTANGLE,
                points=box,
                confidence=_confidence(entry.get("confidence")),
            )
        )
    return result


#: Frames asked about at once. A run over a job is one request per frame, and services
#: rate-limit per key; four keeps a 100-frame run to minutes without tripping most limits.
CONCURRENCY = 4


async def detect_frames(
    request: InferenceRequest,
    ask: Callable[[InferenceFrame], Awaitable[str]],
    *,
    service: str,
) -> InferenceResult:
    """Ask about each frame, and read every answer into one result.

    `ask` sends one frame and returns the model's text, raising `ProviderError` for what
    stops the whole run (a refused key, an unknown model, a service that cannot be
    reached). An answer that cannot be read stops nothing: the frame is reported in the
    warnings and the rest are kept.
    """
    if not request.classes:
        raise ProviderError(f"{service} was given nothing to look for")
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(frame: InferenceFrame) -> tuple[InferenceFrame, str | Exception]:
        async with gate:
            try:
                return frame, await ask(frame)
            except ProviderError:
                raise
            except ValueError as exc:
                return frame, exc

    answers = await asyncio.gather(*(one(frame) for frame in request.frames))
    result = InferenceResult()
    dropped: list[str] = []
    for frame, answer in answers:
        if isinstance(answer, Exception):
            result.warnings.append(f"Frame {frame.frame}: {answer}; nothing kept from it")
            continue
        try:
            detections = read_answer(answer, frame, request.classes)
        except ValueError as exc:
            result.warnings.append(f"Frame {frame.frame}: {exc}; nothing kept from it")
            continue
        result.shapes.extend(
            shape for shape in detections.shapes if shape.confidence >= request.confidence_threshold
        )
        dropped.extend(f"frame {frame.frame}: {reason}" for reason in detections.dropped)
    if dropped:
        shown = "; ".join(dropped[:5])
        more = f" (and {len(dropped) - 5} more)" if len(dropped) > 5 else ""
        result.warnings.append(f"{service} answered with boxes that were not kept — {shown}{more}")
    return result


def prepared_image(frame: InferenceFrame) -> tuple[bytes, str]:
    """The frame as it is sent: within `MAX_EDGE` and `MAX_PIXELS`, as JPEG where possible.

    Without Pillow (the `media` extra) the bytes go as they are, which every service
    accepts for JPEG and PNG up to its own size limit.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships with the desktop and server images
        return frame.image, frame.content_type or "image/jpeg"

    with Image.open(io.BytesIO(frame.image)) as opened:
        opened.load()
        image: Image.Image = opened
        scale = min(
            1.0,
            MAX_EDGE / max(image.width, image.height),
            (MAX_PIXELS / (image.width * image.height)) ** 0.5,
        )
        if scale < 1.0:
            image = image.resize(
                # Floored: rounding up can land a few pixels over `MAX_PIXELS`.
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=88)
    return buffer.getvalue(), "image/jpeg"


def as_base64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def _json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer, tolerating fences and prose around it."""
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the answer holds no JSON object") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"the answer's JSON does not parse ({exc.msg})") from None
    if not isinstance(value, dict):
        raise ValueError("the answer is JSON but not an object")
    return value


def _box(value: object, width: int, height: int) -> list[float] | None:
    """A normalised box as pixels, clamped to the frame; None when it has no area."""
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(number) for number in value)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))

    def clamp(number: float, size: int) -> float:
        return round(min(max(number, 0.0), float(SCALE)) / SCALE * size, 2)

    left, right = clamp(x1, width), clamp(x2, width)
    top, bottom = clamp(y1, height), clamp(y2, height)
    if right - left < 1 or bottom - top < 1:
        return None
    return [left, top, right, bottom]


def _confidence(value: object) -> float:
    """The model's own estimate, clamped; 0.5 when it gave none it could be held to.

    It is not calibrated — these models were not trained to report one — so it orders
    suggestions at best; the run's threshold still applies to it, as to any model's.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    if number != number:  # NaN
        return 0.5
    if number > 1.0 and number <= 100.0:
        number /= 100.0  # A percentage, which models answer with often enough to expect.
    return min(max(number, 0.0), 1.0)


__all__ = [
    "ANSWER_SCHEMA",
    "CONCURRENCY",
    "MAX_EDGE",
    "SCALE",
    "SYSTEM_PROMPT",
    "Detections",
    "as_base64",
    "detect_frames",
    "detection_prompt",
    "prepared_image",
    "read_answer",
]
