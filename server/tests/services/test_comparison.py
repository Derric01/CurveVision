"""Scoring annotated work against ground truth.

This is what a `QualityReport` is made of, and a quality number that is wrong in the
*flattering* direction is worse than no number at all: it tells a team their data is good
when it is not, and they find out from a model that will not train.

So the geometry is checked against hand-computed areas rather than against itself, and the
conflict classification is checked case by case — *missing*, *extra*, *wrong label* and
*poor overlap* are four different mistakes, and collapsing them is the failure this is
written to avoid.
"""

from __future__ import annotations

import math
import uuid

import pytest

from curvevision.core.errors import ValidationError
from curvevision.domain.enums import ShapeType
from curvevision.services.comparison import (
    ConflictKind,
    compare_annotations,
    convex,
    intersection_area,
    polygon_area,
    shape_iou,
    to_polygon,
)


class FakeShape:
    """Just the fields the comparison reads. The real `Shape` needs a session."""

    def __init__(self, frame, label_id, shape_type, points, shape_id=None):
        self.id = shape_id or uuid.uuid4()
        self.frame = frame
        self.label_id = label_id
        self.shape_type = shape_type
        self.points = points


CAR = uuid.uuid4()
PERSON = uuid.uuid4()


def rect(frame, label, x1, y1, x2, y2, shape_id=None):
    return FakeShape(frame, label, ShapeType.RECTANGLE, [x1, y1, x2, y2], shape_id)


# ------------------------------------------------------------------------------ geometry


class TestGeometry:
    def test_rectangle_area_is_exact(self):
        assert polygon_area(to_polygon(ShapeType.RECTANGLE, [0, 0, 10, 20])) == 200.0

    def test_a_rectangle_given_backwards_is_still_that_rectangle(self):
        # Drag up-and-left and the points arrive reversed; the area must not go negative
        # or zero, which would silently score every such annotation as unmatched.
        assert polygon_area(to_polygon(ShapeType.RECTANGLE, [10, 20, 0, 0])) == 200.0

    def test_polygon_area_matches_the_shoelace_result(self):
        # A 4-3-5 triangle: area 6.
        assert polygon_area(to_polygon(ShapeType.POLYGON, [0, 0, 4, 0, 0, 3])) == 6.0

    def test_a_concave_polygon_is_measured_correctly(self):
        # An L: 10x10 square minus a 6x6 bite = 64.
        points = [0, 0, 10, 0, 10, 4, 4, 4, 4, 10, 0, 10]
        assert polygon_area(to_polygon(ShapeType.POLYGON, points)) == 64.0

    def test_ellipse_area_approaches_pi_r_squared(self):
        area = polygon_area(to_polygon(ShapeType.ELLIPSE, [0, 0, 10, 5]))
        assert area == pytest.approx(math.pi * 10 * 5, rel=0.002)

    def test_a_polyline_encloses_nothing(self):
        # Otherwise a polyline would "overlap" a box and score as a match.
        assert polygon_area(to_polygon(ShapeType.POLYLINE, [0, 0, 10, 0, 20, 5])) == 0.0

    def test_intersection_of_two_overlapping_squares(self):
        a = to_polygon(ShapeType.RECTANGLE, [0, 0, 10, 10])
        b = to_polygon(ShapeType.RECTANGLE, [5, 5, 15, 15])
        assert intersection_area(a, b) == pytest.approx(25.0)

    def test_disjoint_shapes_intersect_in_nothing(self):
        a = to_polygon(ShapeType.RECTANGLE, [0, 0, 10, 10])
        b = to_polygon(ShapeType.RECTANGLE, [50, 50, 60, 60])
        assert intersection_area(a, b) == 0.0

    def test_a_contained_shape_intersects_entirely(self):
        outer = to_polygon(ShapeType.RECTANGLE, [0, 0, 100, 100])
        inner = to_polygon(ShapeType.RECTANGLE, [10, 10, 20, 20])
        assert intersection_area(inner, outer) == pytest.approx(100.0)

    def test_convexity_is_detected(self):
        assert convex(to_polygon(ShapeType.RECTANGLE, [0, 0, 10, 10]))
        assert not convex(to_polygon(ShapeType.POLYGON, [0, 0, 10, 0, 10, 4, 4, 4, 4, 10, 0, 10]))


class TestIoU:
    def test_identical_shapes_score_one(self):
        assert shape_iou(
            ShapeType.RECTANGLE, [0, 0, 10, 10], ShapeType.RECTANGLE, [0, 0, 10, 10]
        ) == pytest.approx(1.0)

    def test_half_overlapping_boxes(self):
        # 10x10 and 10x10 sharing 5x10: intersection 50, union 150.
        assert shape_iou(
            ShapeType.RECTANGLE, [0, 0, 10, 10], ShapeType.RECTANGLE, [5, 0, 15, 10]
        ) == pytest.approx(50 / 150)

    def test_disjoint_shapes_score_zero(self):
        assert (
            shape_iou(ShapeType.RECTANGLE, [0, 0, 10, 10], ShapeType.RECTANGLE, [20, 20, 30, 30])
            == 0.0
        )

    def test_a_polygon_is_not_compared_by_its_bounding_box(self):
        """The reason this file does real geometry rather than box overlap.

        Two triangles occupying opposite halves of the same square share a bounding box
        exactly, so a bbox comparison would call them a perfect match. They touch only on
        the diagonal.
        """
        lower = [0, 0, 10, 0, 10, 10]
        upper = [0, 0, 10, 10, 0, 10]
        iou = shape_iou(ShapeType.POLYGON, lower, ShapeType.POLYGON, upper)
        assert iou < 0.02, f"bounding-box comparison would have said ~1.0, got {iou}"

    def test_a_shape_with_no_area_never_matches(self):
        assert (
            shape_iou(ShapeType.POLYLINE, [0, 0, 10, 10], ShapeType.RECTANGLE, [0, 0, 10, 10])
            == 0.0
        )


# ------------------------------------------------------------------------------ scoring


class TestComparison:
    def test_a_perfect_job_scores_one(self):
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(1, PERSON, 5, 5, 15, 15)]
        annotated = [rect(0, CAR, 0, 0, 10, 10), rect(1, PERSON, 5, 5, 15, 15)]

        result = compare_annotations(annotated, truth)

        assert result.overall.precision == 1.0
        assert result.overall.recall == 1.0
        assert result.overall.f1 == 1.0
        assert result.conflicts == []

    def test_a_missed_object_is_missing_not_extra(self):
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(0, CAR, 50, 50, 60, 60)]
        annotated = [rect(0, CAR, 0, 0, 10, 10)]

        result = compare_annotations(annotated, truth)

        assert result.overall.matched == 1
        assert result.overall.missing == 1
        assert result.overall.extra == 0
        assert result.overall.recall == 0.5
        assert result.overall.precision == 1.0
        assert [c.kind for c in result.conflicts] == [ConflictKind.MISSING]

    def test_an_invented_object_is_extra_not_missing(self):
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 0, 0, 10, 10), rect(0, CAR, 50, 50, 60, 60)]

        result = compare_annotations(annotated, truth)

        assert result.overall.matched == 1
        assert result.overall.extra == 1
        assert result.overall.precision == 0.5
        assert result.overall.recall == 1.0
        assert [c.kind for c in result.conflicts] == [ConflictKind.EXTRA]

    def test_the_right_box_with_the_wrong_label_is_neither_a_match_nor_a_miss(self):
        """The classification that is easiest to get wrong.

        The annotator found the object — so calling it *missing* hides that they were
        looking in the right place — but they called it something else, so counting it as a
        match would flatter both labels. It counts against `car`'s recall and against
        `person`'s precision, and the conflict names both sides.
        """
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, PERSON, 0, 0, 10, 10)]

        result = compare_annotations(annotated, truth)

        assert result.overall.matched == 0
        conflict = result.conflicts[0]
        assert conflict.kind is ConflictKind.WRONG_LABEL
        assert conflict.expected_label_id == CAR
        assert conflict.label_id == PERSON
        assert result.per_label[CAR].recall == 0.0
        assert result.per_label[PERSON].precision == 0.0

    def test_a_near_miss_is_poor_overlap_not_missing(self):
        """ "You were close" and "you missed it" need different fixes."""
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 7, 0, 17, 10)]  # IoU 3/17, below 0.5

        result = compare_annotations(annotated, truth, iou_threshold=0.5)

        assert result.overall.matched == 0
        kinds = {c.kind for c in result.conflicts}
        assert ConflictKind.POOR_OVERLAP in kinds
        assert ConflictKind.MISSING not in kinds
        poor = next(c for c in result.conflicts if c.kind is ConflictKind.POOR_OVERLAP)
        assert poor.iou == pytest.approx(3 / 17, rel=1e-3)

    def test_a_near_miss_costs_precision_as_well_as_recall(self):
        """The flattering failure: sloppy boxes must not score precision 1.0.

        A box that overlaps the object by less than the threshold is, by the threshold's own
        definition, not that object. Counting it against recall but leaving it out of
        precision's denominator means an annotator who draws every box loosely scores
        "everything you drew was right" — which is the opposite of the truth, and is the
        number a team would act on.

        This is also what the wrong-label branch already does: one mistake, counted on both
        sides. The two must agree.
        """
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 7, 0, 17, 10)]  # IoU 3/17, below 0.5

        result = compare_annotations(annotated, truth, iou_threshold=0.5)

        assert result.overall.missing == 1
        assert result.overall.extra == 1, "the loose box is not evidence of correct work"
        assert result.overall.precision == 0.0
        assert result.overall.recall == 0.0
        assert len([c for c in result.conflicts if c.frame == 0]) == 1, (
            "one mistake is one conflict; a reviewer should not see it reported twice"
        )

    def test_one_loose_box_over_two_objects_is_counted_once(self):
        """It is one wrong box, however many things it half-covers."""
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(0, CAR, 9, 0, 19, 10)]
        annotated = [rect(0, CAR, 4, 0, 15, 10)]  # under 0.5 against both

        result = compare_annotations(annotated, truth, iou_threshold=0.5)

        assert result.overall.matched == 0
        assert result.overall.missing == 2
        assert result.overall.extra == 1, "one annotated shape can only be one false positive"

    def test_the_threshold_decides_what_counts_as_found(self):
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 2, 0, 12, 10)]  # IoU 8/12 = 0.667

        assert compare_annotations(annotated, truth, iou_threshold=0.5).overall.matched == 1
        assert compare_annotations(annotated, truth, iou_threshold=0.8).overall.matched == 0

    def test_each_object_is_matched_at_most_once(self):
        """Two annotations over one object is a duplicate, not two matches."""
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 0, 0, 10, 10), rect(0, CAR, 1, 1, 11, 11)]

        result = compare_annotations(annotated, truth)

        assert result.overall.matched == 1
        assert result.overall.extra == 1

    def test_the_best_overlap_wins_when_two_compete(self):
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        exact = rect(0, CAR, 0, 0, 10, 10, shape_id=uuid.uuid4())
        loose = rect(0, CAR, 3, 3, 13, 13, shape_id=uuid.uuid4())

        result = compare_annotations([loose, exact], truth)

        matched = [c for c in result.conflicts if c.kind is ConflictKind.EXTRA]
        assert result.overall.matched == 1
        assert matched[0].shape_id == loose.id, "the sloppier box should be the leftover"

    def test_frames_are_compared_independently(self):
        # The same box on the wrong frame is not a match.
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(5, CAR, 0, 0, 10, 10)]

        result = compare_annotations(annotated, truth)

        assert result.overall.matched == 0
        assert result.overall.missing == 1
        assert result.overall.extra == 1

    def test_frames_outside_the_ground_truth_are_not_scored_as_perfect(self):
        """The dangerous case, and the reason `frames` exists.

        A ground-truth job covering frames 0-1 says nothing about frame 9. Counting the
        annotator's frame-9 work as correct would inflate the score with unchecked data —
        wrong in the flattering direction, which is the direction that costs a team a model.
        """
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 0, 0, 10, 10), rect(9, CAR, 0, 0, 10, 10)]

        scored = compare_annotations(annotated, truth, frames=[0])

        assert scored.compared_frames == 1
        assert scored.overall.extra == 0, "frame 9 is outside the checked range"
        assert scored.overall.precision == 1.0

    def test_per_label_scores_are_kept_apart(self):
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(0, PERSON, 50, 50, 60, 60)]
        annotated = [rect(0, CAR, 0, 0, 10, 10)]

        result = compare_annotations(annotated, truth)

        assert result.per_label[CAR].recall == 1.0
        assert result.per_label[PERSON].recall == 0.0

    def test_an_empty_job_against_an_empty_truth_is_not_a_failure(self):
        result = compare_annotations([], [])
        assert result.overall.f1 == 0.0
        assert result.conflicts == []

    def test_mean_iou_averages_only_the_matches(self):
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(1, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, CAR, 0, 0, 10, 10), rect(1, CAR, 2, 0, 12, 10)]

        result = compare_annotations(annotated, truth, iou_threshold=0.5)

        assert result.overall.matched == 2
        assert result.overall.mean_iou == pytest.approx((1.0 + 8 / 12) / 2, rel=1e-3)

    def test_a_nonsense_threshold_is_refused(self):
        with pytest.raises(ValidationError):
            compare_annotations([], [], iou_threshold=0.0)
        with pytest.raises(ValidationError):
            compare_annotations([], [], iou_threshold=1.5)

    def test_the_details_blob_is_json_serialisable(self):
        import json

        truth = [rect(0, CAR, 0, 0, 10, 10), rect(0, PERSON, 50, 50, 60, 60)]
        annotated = [rect(0, PERSON, 0, 0, 10, 10)]

        details = compare_annotations(annotated, truth).as_details()

        assert json.loads(json.dumps(details))["conflicts"], "conflicts must survive storage"


class TestConflictLabelsMeanOneThing:
    """`label_id` is the annotation's side; `expected_label_id` is the ground truth's.

    The fields used to swap roles depending on the kind — the ground truth's label landed in
    `label_id` on `missing` and `poor_overlap`, and the annotator's on the other two. Every
    reader then had to special-case the kind, and the one that did not was wrong in a way
    that reads as authoritative: a reviewer shown "loose geometry: car" clicks through to a
    box labelled `person`, having been told something false about their own work.
    """

    def test_a_miss_carries_the_ground_truths_label_and_no_annotated_one(self):
        truth = [rect(0, CAR, 0, 0, 10, 10)]

        conflict = compare_annotations([], truth).conflicts[0]

        assert conflict.kind is ConflictKind.MISSING
        assert conflict.expected_label_id == CAR
        assert conflict.label_id is None, "nothing was annotated, so there is no label to name"

    def test_an_extra_carries_the_annotated_label_and_no_expected_one(self):
        annotated = [rect(0, PERSON, 0, 0, 10, 10)]

        conflict = compare_annotations(annotated, []).conflicts[0]

        assert conflict.kind is ConflictKind.EXTRA
        assert conflict.label_id == PERSON
        assert conflict.expected_label_id is None, "the ground truth has nothing there"

    def test_a_loose_box_reports_its_own_label_not_the_ground_truths(self):
        """Candidates are paired by geometry, so the two labels need not agree."""
        truth = [rect(0, CAR, 0, 0, 10, 10)]
        annotated = [rect(0, PERSON, 7, 0, 17, 10)]  # IoU 3/17 = 0.18, below threshold

        conflict = compare_annotations(annotated, truth).conflicts[0]

        assert conflict.kind is ConflictKind.POOR_OVERLAP
        assert conflict.label_id == PERSON, "this names the shape the annotator drew"
        assert conflict.expected_label_id == CAR, "and this what the ground truth says"

    def test_every_kind_names_at_least_one_side(self):
        """A conflict with neither label is unreadable whatever the reviewer clicks."""
        truth = [rect(0, CAR, 0, 0, 10, 10), rect(0, CAR, 50, 50, 60, 60)]
        annotated = [
            rect(0, PERSON, 0, 0, 10, 10),  # wrong label
            rect(0, CAR, 52, 50, 70, 60),  # poor overlap
            rect(0, PERSON, 90, 90, 99, 99),  # extra
        ]

        result = compare_annotations(annotated, truth)

        assert {conflict.kind for conflict in result.conflicts}
        for conflict in result.conflicts:
            assert conflict.label_id is not None or conflict.expected_label_id is not None
