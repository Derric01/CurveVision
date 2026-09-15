"""How long scoring a job against a ground truth actually takes.

`handoff.md` carried an item for several iterations saying the comparison should move to the
unused `quality` queue, because "a ground truth over thousands of frames changes that
calculus". That was a prediction, and `AGENTS.md` says not to invent a benchmark. This
measures it instead, so the decision to move it -- or to leave it inline and say why -- rests
on a number somebody can reproduce:

    pytest -m benchmark -s server/tests/benchmarks/test_comparison_scale.py

Excluded from the default run, like the other benchmarks: these are measurements, and the
assertions are loose ceilings that catch an order-of-magnitude regression without turning a
slow CI runner into a red build.

**What the shape of the algorithm predicts, so the measurement can confirm or refute it.**
`compare_annotations` buckets both sides by frame and then, per frame, computes an IoU for
every (annotated, truth) pair before sorting the candidates. So the cost is linear in frames
and *quadratic in shapes per frame* -- a hundred thousand frames of two objects each should
be far cheaper than a thousand frames of two hundred. The parameters below vary those two
independently, because a single "shapes" number would hide which one matters.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.services.comparison import compare_annotations

pytestmark = pytest.mark.benchmark


@dataclass(frozen=True)
class Box:
    """A `ComparableShape` with no ORM behind it -- the protocol is all this needs."""

    id: uuid.UUID
    frame: int
    label_id: uuid.UUID
    shape_type: ShapeType
    points: Sequence[float]


LABEL = uuid.uuid4()


def scene(frames: int, per_frame: int, *, offset: float = 0.0) -> list[Box]:
    """A grid of boxes on every frame, optionally nudged so the IoU is under 1.

    The offset matters more than it looks: identical geometry would let the matcher pair
    everything on the first candidate it tries, which is the *fastest* possible case and not
    the one a real annotator produces. Nudging every box makes the overlaps partial, so the
    candidate list is the length the algorithm actually has to sort.
    """
    boxes: list[Box] = []
    columns = max(1, int(per_frame**0.5))
    for frame in range(frames):
        for index in range(per_frame):
            column, row = index % columns, index // columns
            x = column * 40.0 + offset
            y = row * 40.0 + offset
            boxes.append(
                Box(
                    id=uuid.uuid4(),
                    frame=frame,
                    label_id=LABEL,
                    shape_type=ShapeType.RECTANGLE,
                    points=[x, y, x + 30.0, y + 30.0],
                )
            )
    return boxes


def _report(label: str, frames: int, shapes: int, seconds: float) -> None:
    rate = shapes / seconds if seconds else float("inf")
    print(
        f"  {label:<34} {frames:>7,} frames  {shapes:>8,} shapes  "
        f"{seconds:7.3f}s  ({rate:>10,.0f} shapes/s)"
    )


# How many frames, holding objects-per-frame at a realistic two.
@pytest.mark.parametrize("frames", (1_000, 10_000, 100_000))
def test_comparison_scales_with_frames(frames: int) -> None:
    """The dimension the handoff item was worried about."""
    annotated = scene(frames, 2, offset=3.0)
    truth = scene(frames, 2)

    started = time.perf_counter()
    result = compare_annotations(annotated, truth, frames=range(frames))
    elapsed = time.perf_counter() - started

    _report("compare (2 objects/frame)", frames, len(annotated), elapsed)
    assert result.compared_frames == frames
    # A loose ceiling: ten times the slowest reading seen while writing this.
    assert elapsed < 60.0


# How many objects on one frame, holding the frame count low. This is the quadratic one.
@pytest.mark.parametrize("per_frame", (10, 50, 200))
def test_comparison_scales_with_objects_per_frame(per_frame: int) -> None:
    """A crowd scene, which is where the pair-wise matching is supposed to hurt."""
    frames = 100
    annotated = scene(frames, per_frame, offset=3.0)
    truth = scene(frames, per_frame)

    started = time.perf_counter()
    result = compare_annotations(annotated, truth, frames=range(frames))
    elapsed = time.perf_counter() - started

    _report(f"compare ({per_frame} objects/frame)", frames, len(annotated), elapsed)
    assert result.compared_frames == frames
    assert elapsed < 60.0


def test_a_realistic_worst_case_a_reviewer_would_actually_hit() -> None:
    """Ten thousand frames of a busy street: 20 objects each, 200,000 shapes a side.

    Bigger than any ground truth anybody is likely to annotate by hand, which is the point --
    if this is comfortable, the inline path is comfortable.
    """
    frames, per_frame = 10_000, 20
    annotated = scene(frames, per_frame, offset=3.0)
    truth = scene(frames, per_frame)

    started = time.perf_counter()
    result = compare_annotations(annotated, truth, frames=range(frames))
    elapsed = time.perf_counter() - started

    _report("compare (busy street)", frames, len(annotated), elapsed)
    assert result.compared_frames == frames
    assert elapsed < 120.0
