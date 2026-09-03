"""Track interpolation.

The trickiest correctness surface in video annotation, so it gets the most thorough unit
coverage in the codebase.
"""

from __future__ import annotations

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.services.interpolation import (
    Keyframe,
    interpolate_points,
    interpolate_track,
    resample,
    track_frame_range,
)


def kf(frame: int, points: list[float], **kwargs: object) -> Keyframe:
    return Keyframe(frame=frame, points=points, **kwargs)  # type: ignore[arg-type]


class TestInterpolatePoints:
    def test_midpoint_of_a_rectangle(self) -> None:
        result = interpolate_points([0, 0, 10, 10], [10, 10, 20, 20], 0.5, ShapeType.RECTANGLE)
        assert result == [5, 5, 15, 15]

    def test_endpoints_are_returned_exactly(self) -> None:
        start, end = [0.0, 0.0, 4.0, 4.0], [8.0, 8.0, 12.0, 12.0]
        assert interpolate_points(start, end, 0.0, ShapeType.RECTANGLE) == start
        assert interpolate_points(start, end, 1.0, ShapeType.RECTANGLE) == end

    def test_polygons_with_equal_vertex_counts_pair_positionally(self) -> None:
        start = [0, 0, 10, 0, 10, 10]
        end = [10, 10, 20, 10, 20, 20]
        assert interpolate_points(start, end, 0.5, ShapeType.POLYGON) == [5, 5, 15, 5, 15, 15]

    def test_polygons_with_different_vertex_counts_are_resampled(self) -> None:
        """A vertex added mid-track must not scramble the geometry.

        Pairing coordinates by index -- the obvious implementation -- would interpolate
        vertex 3 of one shape towards vertex 3 of a shape that has a different meaning
        there, producing visibly wrong geometry.
        """
        triangle = [0, 0, 10, 0, 5, 10]
        square = [0, 0, 10, 0, 10, 10, 0, 10]

        result = interpolate_points(triangle, square, 0.5, ShapeType.POLYGON)

        assert len(result) == len(square)
        assert len(result) % 2 == 0
        xs, ys = result[0::2], result[1::2]
        # The blend must stay inside the union of both shapes' bounds.
        assert min(xs) >= -0.001 and max(xs) <= 10.001
        assert min(ys) >= -0.001 and max(ys) <= 10.001

    def test_mismatched_positional_geometry_holds_the_start(self) -> None:
        result = interpolate_points([0, 0, 1, 1], [0, 0], 0.5, ShapeType.RECTANGLE)
        assert result == [0, 0, 1, 1]


class TestResample:
    def test_open_path_keeps_both_endpoints(self) -> None:
        result = resample([0, 0, 10, 0], 3, closed=False)
        assert result[:2] == [0.0, 0.0]
        assert result[-2:] == [10.0, 0.0]
        assert len(result) == 6

    def test_closed_path_does_not_repeat_the_first_vertex(self) -> None:
        square = [0, 0, 10, 0, 10, 10, 0, 10]
        result = resample(square, 4, closed=True)
        assert len(result) == 8
        assert result[-2:] != result[:2]

    def test_degenerate_path_is_replicated(self) -> None:
        assert resample([5, 5], 3, closed=False) == [5, 5, 5, 5, 5, 5]

    def test_zero_length_path_does_not_divide_by_zero(self) -> None:
        result = resample([2, 2, 2, 2], 4, closed=False)
        assert result == [2, 2, 2, 2, 2, 2, 2, 2]


class TestInterpolateTrack:
    def test_exact_keyframe_is_returned_unchanged(self) -> None:
        keyframes = [kf(0, [0, 0, 10, 10]), kf(10, [100, 100, 110, 110])]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 0)
        assert result is not None
        assert result.points == [0, 0, 10, 10]
        assert result.keyframe is True

    def test_interpolated_frame_is_flagged_as_not_a_keyframe(self) -> None:
        keyframes = [kf(0, [0, 0, 10, 10]), kf(10, [100, 100, 110, 110])]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 5)
        assert result is not None
        assert result.points == [50, 50, 60, 60]
        assert result.keyframe is False

    def test_before_the_first_keyframe_the_object_does_not_exist(self) -> None:
        keyframes = [kf(5, [0, 0, 10, 10])]
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 4) is None

    def test_after_the_last_keyframe_the_position_holds(self) -> None:
        keyframes = [kf(0, [0, 0, 10, 10]), kf(5, [50, 50, 60, 60])]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 99)
        assert result is not None
        assert result.points == [50, 50, 60, 60]

    def test_outside_keyframe_removes_the_object(self) -> None:
        keyframes = [
            kf(0, [0, 0, 10, 10]),
            kf(5, [50, 50, 60, 60], outside=True),
            kf(10, [90, 90, 100, 100]),
        ]
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 5) is None
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 7) is None
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 10) is not None

    def test_approaching_an_outside_keyframe_holds_position(self) -> None:
        """Do not interpolate towards an `outside` keyframe's coordinates.

        Those coordinates are typically a stale copy of the last position rather than a
        meaningful destination, so drifting towards them would animate the object to a
        place it never was.
        """
        keyframes = [kf(0, [0, 0, 10, 10]), kf(10, [500, 500, 510, 510], outside=True)]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 5)
        assert result is not None
        assert result.points == [0, 0, 10, 10]

    def test_occlusion_is_carried_forward(self) -> None:
        keyframes = [
            kf(0, [0, 0, 10, 10], occluded=True),
            kf(10, [100, 100, 110, 110], occluded=False),
        ]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 5)
        assert result is not None
        assert result.occluded is True

    def test_mutable_attributes_hold_until_the_next_keyframe(self) -> None:
        keyframes = [
            kf(0, [0, 0, 10, 10], attributes={"parked": True}),
            kf(10, [10, 10, 20, 20], attributes={"parked": False}),
        ]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 4)
        assert result is not None
        assert result.attributes == {"parked": True}

    def test_rotation_takes_the_short_way_round(self) -> None:
        keyframes = [
            kf(0, [0, 0, 10, 10], rotation=350.0),
            kf(10, [0, 0, 10, 10], rotation=10.0),
        ]
        result = interpolate_track(keyframes, ShapeType.RECTANGLE, 5)
        assert result is not None
        # 350 -> 10 must go forward through 0 (=360), not backwards through 180.
        assert result.rotation == pytest.approx(360.0)

    def test_empty_track_yields_nothing(self) -> None:
        assert interpolate_track([], ShapeType.RECTANGLE, 0) is None

    def test_single_keyframe_track_persists_forward(self) -> None:
        keyframes = [kf(3, [1, 1, 2, 2])]
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 2) is None
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 3) is not None
        assert interpolate_track(keyframes, ShapeType.RECTANGLE, 300) is not None


class TestTrackFrameRange:
    def test_range_spans_first_present_to_last_keyframe(self) -> None:
        keyframes = [kf(2, [0, 0, 1, 1]), kf(8, [0, 0, 1, 1], outside=True)]
        assert track_frame_range(keyframes) == (2, 8)

    def test_never_present_track_has_no_range(self) -> None:
        assert track_frame_range([kf(0, [0, 0, 1, 1], outside=True)]) is None

    def test_empty_track_has_no_range(self) -> None:
        assert track_frame_range([]) is None
