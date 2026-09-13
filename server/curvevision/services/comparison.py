# Portions of this file follow the comparison strategy used by CVAT's
# `cvat/apps/quality_control/annotation_matching.py`, at commit
# 1d0c39576c3239dcaf8ba7baee71a1b8de496c0e.
#
#   Copyright (C) CVAT.ai Corporation
#   SPDX-License-Identifier: MIT
#
# Copyright (C) CurveVision contributors, for the implementation.
# SPDX-License-Identifier: MIT
#
# What is taken: the *shape* of annotation comparison -- compare frame by frame and label by
# label, match by overlap above a threshold, and classify what is left over into distinct
# conflict kinds rather than one "mismatch" bucket. The distinction between a missing
# annotation, a spurious one and a correctly-placed one carrying the wrong label is the part
# a fresh implementation reliably gets wrong, because it is tempting to collapse them.
#
# What is different, and why this is an implementation rather than an adaptation: the
# upstream file computes overlap through Datumaro and solves the assignment with
# `scipy.optimize.linear_sum_assignment`. CurveVision depends on neither, and
# [ADR 0004](../../../docs/adr/0004-streaming-format-registry.md) rejected Datumaro
# deliberately. Matching here is greedy in descending overlap -- the same rule COCO's own
# evaluator uses -- and the geometry is exact and dependency-free: Sutherland-Hodgman
# clipping plus the shoelace formula, which is a few dozen lines and needs no NumPy.
"""Comparing annotated work against ground truth.

This is what turns a `QualityReport` from a row with zeroes in it into a measurement. Given
two jobs over the same frames -- one annotated, one ground truth -- it answers: what did the
annotator get right, what did they miss, what did they invent, and what did they label
wrongly.

Three decisions worth knowing about, because each has a wrong answer that looks reasonable:

**Conflicts are classified, not counted.** "12 mismatches" tells an annotator nothing.
*Missing*, *extra*, *wrong label* and *poor overlap* are four different mistakes with four
different fixes, and a reviewer needs to know which they are looking at.

**Matching is greedy in descending overlap**, per frame and per label. The optimal
assignment (Hungarian) differs only when two annotations compete for the same ground-truth
object at similar overlap, which on real annotation data is rare and, when it happens, means
a duplicate that should be reported rather than optimally paired away.

**Geometry is exact.** Bounding-box IoU on a polygon would report agreement that is not
there -- two quite different polygons can share a bounding box. Rectangles, polygons,
ellipses and masks are compared as the areas they actually cover.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from curvevision.domain.enums import ShapeType

#: How finely an ellipse is approximated when computing its area. 64 segments puts the area
#: error under 0.2%, which is far below the noise in any human annotation.
ELLIPSE_SEGMENTS = 64

Point = tuple[float, float]
Polygon = list[Point]


class ComparableShape(Protocol):
    """The five fields comparison needs, and nothing else.

    A protocol rather than the ORM `Shape` on purpose: this module is pure geometry and
    arithmetic, and importing the ORM would tie a pure function to a database session. It
    also lets a track be compared at an interpolated position (see `services.quality`) and
    lets the tests use a plain object, which is how the geometry gets checked against
    hand-computed areas rather than against a fixture.
    """

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def frame(self) -> int: ...

    @property
    def label_id(self) -> uuid.UUID: ...

    @property
    def shape_type(self) -> ShapeType: ...

    @property
    def points(self) -> Sequence[float]: ...


class ConflictKind(StrEnum):
    """What went wrong, specifically."""

    #: In the ground truth, absent from the annotation. The annotator missed an object.
    MISSING = "missing"
    #: In the annotation, absent from the ground truth. The annotator invented an object.
    EXTRA = "extra"
    #: Same object, different label.
    WRONG_LABEL = "wrong_label"
    #: Same object, same label, but the geometry overlaps by less than the threshold.
    POOR_OVERLAP = "poor_overlap"


@dataclass(frozen=True, slots=True)
class Conflict:
    kind: ConflictKind
    frame: int
    #: The label on the **annotated** shape, or None when nothing was annotated (`missing`).
    #:
    #: One meaning on every kind, deliberately. This field used to carry the ground truth's
    #: label on `missing` and `poor_overlap` and the annotator's on the other two, which
    #: forced every reader to special-case the kind and let a `poor_overlap` conflict report
    #: a label the shape it names does not have. A reviewer reading "loose geometry: car"
    #: and clicking through to a box labelled "person" has been told something false.
    label_id: uuid.UUID | None
    #: The label the **ground truth** gives, or None when the ground truth has nothing there
    #: (`extra`). Set on every other kind, so both sides are always visible.
    expected_label_id: uuid.UUID | None = None
    shape_id: uuid.UUID | None = None
    ground_truth_shape_id: uuid.UUID | None = None
    iou: float | None = None


@dataclass(slots=True)
class LabelScore:
    matched: int = 0
    missing: int = 0
    extra: int = 0
    #: Summed IoU over matches, for the mean.
    iou_total: float = 0.0

    @property
    def precision(self) -> float:
        found = self.matched + self.extra
        return self.matched / found if found else 0.0

    @property
    def recall(self) -> float:
        expected = self.matched + self.missing
        return self.matched / expected if expected else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def mean_iou(self) -> float:
        return self.iou_total / self.matched if self.matched else 0.0


@dataclass(slots=True)
class ComparisonResult:
    iou_threshold: float
    overall: LabelScore = field(default_factory=LabelScore)
    per_label: dict[uuid.UUID, LabelScore] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    #: Frames present in one job's range and not the other. Compared frames only, so a
    #: partial overlap does not silently score the missing part as perfect.
    compared_frames: int = 0

    def as_details(self) -> dict[str, object]:
        """The JSON blob stored on `QualityReport.details`."""
        return {
            "compared_frames": self.compared_frames,
            "matched": self.overall.matched,
            "missing": self.overall.missing,
            "extra": self.overall.extra,
            "mean_iou": round(self.overall.mean_iou, 4),
            "per_label": {
                str(label_id): {
                    "matched": score.matched,
                    "missing": score.missing,
                    "extra": score.extra,
                    "precision": round(score.precision, 4),
                    "recall": round(score.recall, 4),
                    "f1": round(score.f1, 4),
                    "mean_iou": round(score.mean_iou, 4),
                }
                for label_id, score in self.per_label.items()
            },
            "conflicts": [
                {
                    "kind": str(conflict.kind),
                    "frame": conflict.frame,
                    "label_id": str(conflict.label_id) if conflict.label_id else None,
                    "expected_label_id": (
                        str(conflict.expected_label_id) if conflict.expected_label_id else None
                    ),
                    "shape_id": str(conflict.shape_id) if conflict.shape_id else None,
                    "ground_truth_shape_id": (
                        str(conflict.ground_truth_shape_id)
                        if conflict.ground_truth_shape_id
                        else None
                    ),
                    "iou": round(conflict.iou, 4) if conflict.iou is not None else None,
                }
                for conflict in self.conflicts
            ],
        }


# ------------------------------------------------------------------------------ geometry


def _pairs(points: Sequence[float]) -> Polygon:
    return [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]


#: Shapes that enclose an area and can therefore be compared by overlap. A polyline, a set
#: of points and a skeleton do not: they are curves and vertices. Returning their vertices
#: from `to_polygon` would let the shoelace formula close them into a phantom triangle — a
#: three-point polyline would report an area of 25 and could "match" a rectangle, inventing
#: agreement that is not there. `test_a_polyline_encloses_nothing` caught exactly that.
AREA_SHAPES = frozenset(
    {
        ShapeType.RECTANGLE,
        ShapeType.ROTATED_RECTANGLE,
        ShapeType.POLYGON,
        ShapeType.ELLIPSE,
        ShapeType.MASK,
        ShapeType.CUBOID,
    }
)


def to_polygon(shape_type: ShapeType, points: Sequence[float]) -> Polygon:
    """The closed outline a shape covers, as a list of vertices.

    Empty for any shape that encloses nothing, which is what keeps a polyline from being
    compared by overlap at all — see `AREA_SHAPES`.
    """
    if shape_type not in AREA_SHAPES:
        return []

    if shape_type is ShapeType.RECTANGLE:
        if len(points) < 4:
            return []
        x1, y1, x2, y2 = points[0], points[1], points[2], points[3]
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    if shape_type is ShapeType.ELLIPSE:
        if len(points) < 4:
            return []
        cx, cy, rx, ry = points[0], points[1], abs(points[2]), abs(points[3])
        return [
            (
                cx + rx * math.cos(2 * math.pi * i / ELLIPSE_SEGMENTS),
                cy + ry * math.sin(2 * math.pi * i / ELLIPSE_SEGMENTS),
            )
            for i in range(ELLIPSE_SEGMENTS)
        ]

    # Polygons, rotated rectangles, masks and cuboids all arrive as an outline already.
    return _pairs(points)


def polygon_area(polygon: Polygon) -> float:
    """Unsigned area, by the shoelace formula. Zero for anything degenerate."""
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _inside(point: Point, edge_start: Point, edge_end: Point) -> bool:
    """Whether `point` is on the inward side of the directed edge."""
    return (edge_end[0] - edge_start[0]) * (point[1] - edge_start[1]) - (
        edge_end[1] - edge_start[1]
    ) * (point[0] - edge_start[0]) >= 0


def _intersect(a: Point, b: Point, edge_start: Point, edge_end: Point) -> Point:
    """Where segment `a`-`b` crosses the infinite line through the edge."""
    x1, y1 = a
    x2, y2 = b
    x3, y3 = edge_start
    x4, y4 = edge_end
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denominator == 0:  # pragma: no cover - parallel; the caller has already clipped
        return b
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _orient(polygon: Polygon) -> Polygon:
    """Counter-clockwise winding, which `_inside` assumes."""
    signed = 0.0
    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(i + 1) % len(polygon)]
        signed += x1 * y2 - x2 * y1
    return polygon if signed >= 0 else list(reversed(polygon))


def intersection_area(subject: Polygon, clip: Polygon) -> float:
    """Area shared by two polygons, by Sutherland-Hodgman clipping.

    **The clip polygon must be convex** for this algorithm to be exact, which is why a
    concave subject is fine but a concave clip is not. `shape_iou` handles that by clipping
    against whichever shape is a rectangle when one of them is, and both annotation
    rectangles and the common case are covered. For two concave polygons the result is a
    close approximation rather than exact, and `POLYGON_APPROXIMATION` records that.
    """
    if len(subject) < 3 or len(clip) < 3:
        return 0.0

    output = _orient(list(subject))
    clip = _orient(list(clip))

    for i in range(len(clip)):
        if not output:
            return 0.0
        edge_start, edge_end = clip[i], clip[(i + 1) % len(clip)]
        current, output = output, []
        previous = current[-1]
        for point in current:
            if _inside(point, edge_start, edge_end):
                if not _inside(previous, edge_start, edge_end):
                    output.append(_intersect(previous, point, edge_start, edge_end))
                output.append(point)
            elif _inside(previous, edge_start, edge_end):
                output.append(_intersect(previous, point, edge_start, edge_end))
            previous = point

    return polygon_area(output)


def convex(polygon: Polygon) -> bool:
    """Whether every turn goes the same way. Sutherland-Hodgman needs a convex clip."""
    if len(polygon) < 4:
        return True
    sign = 0
    for i in range(len(polygon)):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % len(polygon)]
        cx, cy = polygon[(i + 2) % len(polygon)]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross == 0:
            continue
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif sign != current:
            return False
    return True


def shape_iou(
    a_type: ShapeType, a_points: Sequence[float], b_type: ShapeType, b_points: Sequence[float]
) -> float:
    """Intersection over union of two shapes, as areas.

    Zero when either shape encloses no area — a polyline against a rectangle has nothing to
    overlap, and reporting anything else would invent agreement.
    """
    a = to_polygon(a_type, a_points)
    b = to_polygon(b_type, b_points)
    area_a, area_b = polygon_area(a), polygon_area(b)
    if area_a <= 0 or area_b <= 0:
        return 0.0

    # Clip against the convex one where there is a choice: Sutherland-Hodgman is exact only
    # for a convex clip polygon.
    if convex(b):
        overlap = intersection_area(a, b)
    elif convex(a):
        overlap = intersection_area(b, a)
    else:
        overlap = intersection_area(a, b)

    union = area_a + area_b - overlap
    return overlap / union if union > 0 else 0.0


# ------------------------------------------------------------------------------ matching


@dataclass(frozen=True, slots=True)
class _Candidate:
    iou: float
    annotated: ComparableShape
    truth: ComparableShape


def _compare_frame(
    annotated: Sequence[ComparableShape],
    truth: Sequence[ComparableShape],
    threshold: float,
    result: ComparisonResult,
    frame: int,
) -> None:
    """Match one frame's shapes and record what did not match."""
    candidates: list[_Candidate] = []
    for shape in annotated:
        for reference in truth:
            iou = shape_iou(shape.shape_type, shape.points, reference.shape_type, reference.points)
            if iou > 0:
                candidates.append(_Candidate(iou, shape, reference))

    # Greedy, best overlap first. Ties broken by id so a run is reproducible.
    candidates.sort(key=lambda c: (-c.iou, str(c.annotated.id), str(c.truth.id)))

    used_annotated: set[uuid.UUID] = set()
    used_truth: set[uuid.UUID] = set()

    for candidate in candidates:
        if candidate.annotated.id in used_annotated or candidate.truth.id in used_truth:
            continue
        if candidate.iou < threshold:
            continue
        used_annotated.add(candidate.annotated.id)
        used_truth.add(candidate.truth.id)

        label = candidate.truth.label_id
        score = result.per_label.setdefault(label, LabelScore())

        if candidate.annotated.label_id != candidate.truth.label_id:
            # The object was found; the label is wrong. Counted against both labels rather
            # than as a match, because neither label's score should be flattered by it.
            result.conflicts.append(
                Conflict(
                    kind=ConflictKind.WRONG_LABEL,
                    frame=frame,
                    label_id=candidate.annotated.label_id,
                    expected_label_id=candidate.truth.label_id,
                    shape_id=candidate.annotated.id,
                    ground_truth_shape_id=candidate.truth.id,
                    iou=candidate.iou,
                )
            )
            score.missing += 1
            result.overall.missing += 1
            wrong = result.per_label.setdefault(candidate.annotated.label_id, LabelScore())
            wrong.extra += 1
            result.overall.extra += 1
            continue

        score.matched += 1
        score.iou_total += candidate.iou
        result.overall.matched += 1
        result.overall.iou_total += candidate.iou

    #: Annotated shapes already named in a poor-overlap conflict below. They still count as
    #: false positives; this only stops one mistake being *reported* twice.
    named: set[uuid.UUID] = set()

    for reference in truth:
        if reference.id in used_truth:
            continue
        score = result.per_label.setdefault(reference.label_id, LabelScore())
        score.missing += 1
        result.overall.missing += 1
        # A ground-truth object the annotator did overlap, but not enough, is a different
        # mistake from one they never drew at all: "you were close" versus "you missed it".
        best = max(
            (
                c
                for c in candidates
                if c.truth.id == reference.id and c.annotated.id not in used_annotated
            ),
            key=lambda c: c.iou,
            default=None,
        )
        if best is not None:
            named.add(best.annotated.id)
        result.conflicts.append(
            Conflict(
                kind=ConflictKind.POOR_OVERLAP if best else ConflictKind.MISSING,
                frame=frame,
                # The annotated shape's own label, which on a poor overlap need not be the
                # ground truth's: candidates are paired by geometry, not by label.
                label_id=best.annotated.label_id if best else None,
                expected_label_id=reference.label_id,
                shape_id=best.annotated.id if best else None,
                ground_truth_shape_id=reference.id,
                iou=best.iou if best else None,
            )
        )

    for shape in annotated:
        if shape.id in used_annotated:
            continue
        # Unmatched is unmatched. A box that overlaps the object by less than the threshold
        # is, by the threshold's own definition, not that object, so it costs precision
        # exactly as an invented box does -- otherwise an annotator who draws everything
        # loosely scores "everything you drew was right", which is the number a team would
        # act on and the opposite of the truth. This mirrors the wrong-label branch above,
        # which likewise counts one mistake on both sides.
        score = result.per_label.setdefault(shape.label_id, LabelScore())
        score.extra += 1
        result.overall.extra += 1
        if shape.id in named:
            continue
        result.conflicts.append(
            Conflict(
                kind=ConflictKind.EXTRA,
                frame=frame,
                label_id=shape.label_id,
                shape_id=shape.id,
            )
        )


def compare_annotations(
    annotated: Iterable[ComparableShape],
    truth: Iterable[ComparableShape],
    *,
    iou_threshold: float = 0.5,
    frames: Iterable[int] | None = None,
) -> ComparisonResult:
    """Score one job's shapes against a ground truth.

    `frames` restricts the comparison to frames both jobs actually cover. Without it, a
    ground-truth job covering half a task would score the unchecked half as perfect — which
    is the single most dangerous way for a quality number to be wrong, because it is wrong
    in the flattering direction.
    """
    if not 0.0 < iou_threshold <= 1.0:
        from curvevision.core.errors import ValidationError

        raise ValidationError("The IoU threshold must be greater than 0 and at most 1")

    by_frame_annotated: dict[int, list[ComparableShape]] = {}
    by_frame_truth: dict[int, list[ComparableShape]] = {}
    for shape in annotated:
        by_frame_annotated.setdefault(shape.frame, []).append(shape)
    for shape in truth:
        by_frame_truth.setdefault(shape.frame, []).append(shape)

    considered = (
        set(frames) if frames is not None else set(by_frame_annotated) | set(by_frame_truth)
    )

    result = ComparisonResult(iou_threshold=iou_threshold)
    result.compared_frames = len(considered)
    for frame in sorted(considered):
        _compare_frame(
            by_frame_annotated.get(frame, []),
            by_frame_truth.get(frame, []),
            iou_threshold,
            result,
            frame,
        )
    return result
