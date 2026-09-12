"""The merge engine, at the unit level.

`test_overlapping_jobs.py` proves the feature over HTTP. This proves the rules, including
the ones that are easier to get wrong than to notice: that merging is confined to the frames
two jobs actually share, that a disagreement is never collapsed into agreement, and that the
optimal assignment really is used where greedy would lose a pair.
"""

from __future__ import annotations

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.formats.base import ShapeRecord
from curvevision.services import merge


def box(
    x: float,
    y: float,
    width: float = 20,
    height: float = 20,
    *,
    label: str = "car",
    track_id: int | None = None,
) -> ShapeRecord:
    return ShapeRecord(
        label=label,
        shape_type=ShapeType.RECTANGLE,
        points=[x, y, x + width, y + height],
        track_id=track_id,
    )


def counts(frames: dict[int, list[ShapeRecord]]) -> dict[int, int]:
    return {frame: len(shapes) for frame, shapes in frames.items()}


def _greedy_matches(similarities: list[list[float]]) -> int:
    """How many pairs the obvious greedy matcher would accept. Used as a foil, not a tool."""
    pairs = sorted(
        (
            (value, row, column)
            for row, values in enumerate(similarities)
            for column, value in enumerate(values)
        ),
        key=lambda item: -item[0],
    )
    used_rows: set[int] = set()
    used_columns: set[int] = set()
    matched = 0
    for value, row, column in pairs:
        if row in used_rows or column in used_columns or value < merge.SIMILARITY_FLOOR:
            continue
        used_rows.add(row)
        used_columns.add(column)
        matched += 1
    return matched


class TestSimilarity:
    def test_identical_boxes_are_one(self):
        assert merge.similarity(box(0, 0), box(0, 0)) == pytest.approx(1.0)

    def test_distant_boxes_are_zero(self):
        assert merge.similarity(box(0, 0), box(500, 500)) == 0.0

    def test_a_different_label_is_never_similar(self):
        """Two people disagreeing about the label is a disagreement to review."""
        assert merge.similarity(box(0, 0), box(0, 0, label="pedestrian")) == 0.0

    def test_a_different_shape_type_is_never_similar(self):
        polygon = ShapeRecord(
            label="car", shape_type=ShapeType.POLYGON, points=[0, 0, 20, 0, 20, 20, 0, 20]
        )
        assert merge.similarity(box(0, 0), polygon) == 0.0

    def test_a_polyline_is_never_similar_to_another(self):
        """It encloses no area, so IoU says nothing. Two of them stay two objects."""
        line = ShapeRecord(label="car", shape_type=ShapeType.POLYLINE, points=[0, 0, 10, 0])
        assert merge.similarity(line, line) == 0.0

    def test_the_floor_sits_where_a_reviewer_would_put_it(self):
        """A box offset by a fifth of its width is the same car annotated twice."""
        assert merge.similarity(box(0, 0), box(2, 0)) > merge.SIMILARITY_FLOOR
        # Half-overlapping boxes are ambiguous enough that merging would be a guess.
        assert merge.similarity(box(0, 0), box(10, 0)) < merge.SIMILARITY_FLOOR


class TestMergeFrames:
    def test_a_duplicate_on_a_shared_frame_is_dropped(self):
        accumulated = {3: [box(0, 0)]}
        outcome = merge.merge_frames(accumulated, {3: [box(1, 0)]}, shared_frames={3})

        assert counts(accumulated) == {3: 1}
        assert outcome.duplicates_removed == 1
        assert outcome.frames_reconciled == 1
        assert outcome.changed

    def test_the_earlier_job_wins(self):
        """Deterministic, and it matches the upstream tie-break."""
        accumulated = {3: [box(0, 0)]}
        merge.merge_frames(accumulated, {3: [box(1, 0)]}, shared_frames={3})

        assert accumulated[3][0].points == [0, 0, 20, 20]

    def test_two_real_objects_both_survive(self):
        accumulated = {3: [box(0, 0)]}
        outcome = merge.merge_frames(accumulated, {3: [box(300, 300)]}, shared_frames={3})

        assert counts(accumulated) == {3: 2}
        assert outcome.duplicates_removed == 0

    def test_a_frame_outside_the_overlap_is_never_reconciled(self):
        """Two close boxes one annotator drew are their business, not ours."""
        accumulated = {3: [box(0, 0)]}
        outcome = merge.merge_frames(accumulated, {3: [box(1, 0)]}, shared_frames=set())

        assert counts(accumulated) == {3: 2}, "frame 3 is not shared, so nothing to reconcile"
        assert not outcome.changed

    def test_a_frame_only_the_incoming_job_covers_is_added_whole(self):
        accumulated: dict[int, list[ShapeRecord]] = {}
        merge.merge_frames(accumulated, {9: [box(0, 0), box(1, 0)]}, shared_frames={9})

        assert counts(accumulated) == {9: 2}

    def test_the_optimal_assignment_is_used_where_greedy_would_fail(self):
        """The reason this module does not use the four-line greedy matcher.

        Two cars close together, annotated by two people. The similarity matrix is::

                     X      Y
            A      0.951  0.860
            B      0.818  0.667

        Greedy takes the best pair first — A-X at 0.951 — which leaves B only Y at 0.667,
        below the floor. B is then kept as a new object and the export ships a duplicate.
        The optimal assignment is A-Y plus B-X, and it matches both.

        The greedy answer is computed here rather than described, so this test fails if
        somebody swaps the solver for the simpler one.
        """
        existing = [box(0, 0), box(2, 0)]
        incoming = [box(0.5, 0), box(-2, 0)]
        matrix = [[merge.similarity(new, old) for old in existing] for new in incoming]
        assert [[round(value, 3) for value in row] for row in matrix] == [
            [0.951, 0.860],
            [0.818, 0.667],
        ], "the fixture drifted; this test only means something for this matrix"

        assert _greedy_matches(matrix) == 1, "greedy strands one real correspondence here"

        accumulated = {5: list(existing)}
        outcome = merge.merge_frames(accumulated, {5: incoming}, shared_frames={5})

        assert outcome.duplicates_removed == 2, "both are the same cars, seen twice"
        assert counts(accumulated) == {5: 2}

    def test_one_incoming_shape_cannot_absorb_two(self):
        """A shape matched to one partner must not also be matched to another."""
        accumulated = {5: [box(0, 0), box(1, 0)]}
        outcome = merge.merge_frames(accumulated, {5: [box(0, 0)]}, shared_frames={5})

        assert counts(accumulated) == {5: 2}
        assert outcome.duplicates_removed == 1


class TestTrackIdentity:
    def test_a_matched_pair_unifies_their_track_ids(self):
        accumulated = {3: [box(0, 0, track_id=7)], 4: [box(5, 0, track_id=7)]}
        incoming = {3: [box(1, 0, track_id=9)], 5: [box(10, 0, track_id=9)]}

        outcome = merge.merge_frames(accumulated, incoming, shared_frames={3})

        assert outcome.tracks_unified == 1
        assert {shape.track_id for shapes in accumulated.values() for shape in shapes} == {7}

    def test_the_lower_id_survives(self):
        """So the result does not depend on the order pairs were discovered in."""
        accumulated = {3: [box(0, 0, track_id=2)]}
        incoming = {3: [box(1, 0, track_id=5)], 4: [box(5, 0, track_id=5)]}

        merge.merge_frames(accumulated, incoming, shared_frames={3})

        assert accumulated[4][0].track_id == 2

    def test_unrelated_tracks_keep_their_own_ids(self):
        accumulated = {3: [box(0, 0, track_id=1)]}
        incoming = {3: [box(400, 400, track_id=2)]}

        outcome = merge.merge_frames(accumulated, incoming, shared_frames={3})

        assert outcome.tracks_unified == 0
        assert sorted(shape.track_id for shape in accumulated[3]) == [1, 2]

    def test_a_plain_shape_without_a_track_is_not_given_one(self):
        accumulated = {3: [box(0, 0)]}
        merge.merge_frames(accumulated, {3: [box(1, 0, track_id=4)]}, shared_frames={3})

        assert accumulated[3][0].track_id is None


class TestTags:
    def test_the_same_tag_twice_on_a_shared_frame_is_once(self):
        accumulated = {2: ["daytime"]}
        dropped = merge.merge_tags(accumulated, {2: ["daytime"]}, shared_frames={2})

        assert accumulated == {2: ["daytime"]}
        assert dropped == 1

    def test_different_tags_both_survive(self):
        accumulated = {2: ["daytime"]}
        merge.merge_tags(accumulated, {2: ["raining"]}, shared_frames={2})

        assert accumulated == {2: ["daytime", "raining"]}

    def test_a_frame_outside_the_overlap_keeps_both(self):
        accumulated = {2: ["daytime"]}
        dropped = merge.merge_tags(accumulated, {2: ["daytime"]}, shared_frames=set())

        assert accumulated == {2: ["daytime", "daytime"]}
        assert dropped == 0


class TestOverlappingFrames:
    def test_no_previous_range_shares_nothing(self):
        assert merge.overlapping_frames([], (0, 5)) == set()

    def test_adjacent_ranges_do_not_overlap(self):
        assert merge.overlapping_frames([(0, 4)], (5, 9)) == set()

    def test_a_two_frame_seam(self):
        assert merge.overlapping_frames([(0, 4)], (3, 7)) == {3, 4}

    def test_several_previous_ranges_are_all_considered(self):
        assert merge.overlapping_frames([(0, 4), (3, 7)], (6, 10)) == {6, 7}
