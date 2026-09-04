"""Track interpolation.

Pure functions over plain data: no ORM, no session, no I/O. That is what lets the tricky
part of video annotation be exhaustively unit-tested.

The rules:

* Between two keyframes, position is linearly interpolated.
* ``outside=True`` on a keyframe means the object is absent from that frame until the next
  keyframe, so nothing is emitted in that span.
* Before the first keyframe the object does not exist; after the last keyframe it holds its
  final position (an annotator who stops placing keyframes means "it stayed there"), unless
  that keyframe is ``outside``.
* Polygons and polylines whose endpoint keyframes have **different vertex counts** are
  resampled to a common arc-length parameterisation before interpolating. Pairing vertices
  by index -- the obvious implementation -- produces visibly wrong geometry as soon as an
  annotator inserts a vertex, and is a common bug in this class of tool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from curvevision.domain.enums import ShapeType

#: Geometries interpolated by pairing coordinates positionally.
_POSITIONAL = frozenset(
    {
        ShapeType.RECTANGLE,
        ShapeType.ROTATED_RECTANGLE,
        ShapeType.ELLIPSE,
        ShapeType.POINTS,
        ShapeType.CUBOID,
    }
)

#: Geometries that need arc-length resampling when vertex counts differ.
_PATH_LIKE = frozenset({ShapeType.POLYGON, ShapeType.POLYLINE})


@dataclass(slots=True)
class Keyframe:
    """One authored position of a track."""

    frame: int
    points: list[float]
    rotation: float = 0.0
    occluded: bool = False
    outside: bool = False
    keyframe: bool = True
    z_order: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)
    mask: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_angle(a: float, b: float, t: float) -> float:
    """Interpolate degrees along the shorter arc, so 350 -> 10 goes forward through 0."""
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return a + delta * t


def _as_pairs(points: list[float]) -> list[tuple[float, float]]:
    return [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]


def _flatten(pairs: list[tuple[float, float]]) -> list[float]:
    return [coord for pair in pairs for coord in pair]


def _cumulative_lengths(pairs: list[tuple[float, float]], closed: bool) -> list[float]:
    lengths = [0.0]
    ordered = [*pairs, pairs[0]] if closed else pairs
    for i in range(1, len(ordered)):
        dx = ordered[i][0] - ordered[i - 1][0]
        dy = ordered[i][1] - ordered[i - 1][1]
        lengths.append(lengths[-1] + math.hypot(dx, dy))
    return lengths


def resample(points: list[float], count: int, *, closed: bool) -> list[float]:
    """Resample a path to exactly ``count`` vertices, evenly spaced by arc length."""
    pairs = _as_pairs(points)
    if count <= 0 or not pairs:
        return []
    if len(pairs) == 1:
        return _flatten(pairs * count)

    lengths = _cumulative_lengths(pairs, closed)
    total = lengths[-1]
    ordered = [*pairs, pairs[0]] if closed else pairs
    if total == 0.0:
        return _flatten([pairs[0]] * count)

    # A closed path's last sample must not land back on the first vertex.
    divisor = count if closed else count - 1
    out: list[tuple[float, float]] = []
    segment = 0
    for i in range(count):
        target = total * (i / divisor)
        while segment < len(lengths) - 2 and lengths[segment + 1] < target:
            segment += 1
        span = lengths[segment + 1] - lengths[segment]
        t = 0.0 if span == 0.0 else (target - lengths[segment]) / span
        start, end = ordered[segment], ordered[segment + 1]
        out.append((_lerp(start[0], end[0], t), _lerp(start[1], end[1], t)))
    return _flatten(out)


def interpolate_points(
    start: list[float], end: list[float], t: float, shape_type: ShapeType
) -> list[float]:
    """Interpolate geometry between two keyframes at ``t`` in [0, 1]."""
    if t <= 0.0:
        return list(start)
    if t >= 1.0:
        return list(end)

    if shape_type in _PATH_LIKE and len(start) != len(end):
        closed = shape_type is ShapeType.POLYGON
        # Resample both to the denser of the two so no detail is thrown away mid-track.
        count = max(len(start), len(end)) // 2
        start = resample(start, count, closed=closed)
        end = resample(end, count, closed=closed)

    if len(start) != len(end):
        # Positional geometries with mismatched arity cannot be blended meaningfully;
        # holding the start position is the honest behaviour.
        return list(start)

    return [_lerp(start[i], end[i], t) for i in range(len(start))]


def interpolate_track(
    keyframes: list[Keyframe],
    shape_type: ShapeType,
    frame: int,
) -> Keyframe | None:
    """Return the track's shape on ``frame``, or ``None`` if it is not present there.

    ``keyframes`` must be sorted by frame; callers get that ordering from the database.
    """
    if not keyframes:
        return None

    first = keyframes[0]
    if frame < first.frame:
        return None

    # Exact keyframe hit.
    for kf in keyframes:
        if kf.frame == frame:
            return None if kf.outside else kf
        if kf.frame > frame:
            break

    previous: Keyframe | None = None
    following: Keyframe | None = None
    for kf in keyframes:
        if kf.frame < frame:
            previous = kf
        elif following is None:
            following = kf
            break

    if previous is None:
        return None
    if previous.outside:
        # The object left the frame at `previous` and has not returned yet.
        return None
    if following is None:
        # Past the last keyframe: hold the final position.
        return replace(previous, frame=frame, keyframe=False)

    if following.outside:
        # Between a present keyframe and a departure: hold position until it leaves. Not
        # interpolating towards the `outside` keyframe's coordinates matters, because those
        # coordinates are usually a stale copy rather than a meaningful destination.
        return replace(previous, frame=frame, keyframe=False)

    span = following.frame - previous.frame
    t = 0.0 if span == 0 else (frame - previous.frame) / span
    return Keyframe(
        frame=frame,
        points=interpolate_points(previous.points, following.points, t, shape_type),
        rotation=_lerp_angle(previous.rotation, following.rotation, t),
        occluded=previous.occluded,
        outside=False,
        keyframe=False,
        z_order=previous.z_order,
        # Mutable attribute values hold until the next keyframe changes them.
        attributes=dict(previous.attributes),
        mask=previous.mask,
        elements=list(previous.elements),
    )


def track_frame_range(keyframes: list[Keyframe]) -> tuple[int, int] | None:
    """First and last frame on which a track is present, or ``None`` if never present."""
    if not keyframes:
        return None
    present = [kf for kf in keyframes if not kf.outside]
    if not present:
        return None
    return present[0].frame, keyframes[-1].frame
