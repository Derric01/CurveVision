# Portions of this file follow the annotation-merging design used by CVAT's
# `cvat/apps/dataset_manager/annotation.py` (`ObjectManager`, `ShapeManager`,
# `TagManager`), at commit 1d0c39576c3239dcaf8ba7baee71a1b8de496c0e.
#
#   Copyright (C) 2018-2022 Intel Corporation
#   Copyright (C) CVAT.ai Corporation
#   SPDX-License-Identifier: MIT
#
# Copyright (C) CurveVision contributors, for the implementation.
# SPDX-License-Identifier: MIT
#
# What is taken: the strategy for reconciling two annotators' work on the frames their jobs
# share -- only objects inside the overlap are candidates, one cost matrix per frame with
# cost = 1 - similarity, an optimal one-to-one assignment, a similarity floor below which a
# pair is two objects rather than one, and "the earlier job wins" as the tie-break. Also
# their cost threshold of 0.25, which is a tuned constant rather than a derived one and is
# worth inheriting rather than re-guessing.
#
# What is not taken, and why:
#
# * Their similarity function returns 0 for points, polylines, ellipses and 2D cuboids, with
#   a FIXME saying so. CurveVision already computes exact IoU for every area shape in
#   `comparison.py`, so this uses that instead and merges shape types theirs cannot.
# * They solve the assignment with `scipy.optimize.linear_sum_assignment`. CurveVision ships
#   as a single desktop executable, where SciPy is tens of megabytes for one function;
#   `services/matching.py` is the same algorithm without the dependency.
# * They merge at the annotation-IR level, tracks as tracks. CurveVision flattens tracks to
#   per-frame shapes before export, so this merges flattened shapes and then reunites track
#   identity afterwards -- see `_unify_track_ids`.
"""Reconciling the work of two annotators on the frames their jobs share.

A task with `overlap > 0` deliberately hands the same frames to two people: it is what lets
a track stay continuous across a job seam, because whoever annotates job 2 can see where the
object was at the end of job 1. The cost is that those frames get annotated twice, and an
export that simply concatenates every job's shapes ships each object in the seam twice.

That failure is invisible. The archive is well-formed, the frame count is right, and a model
trained on it learns from duplicated objects with nothing anywhere to complain. This module
is what stops it.

**The rule.** Two shapes on the same frame are the same object when they carry the same
label and the same shape type and their geometry agrees above a threshold. Anything else is
two objects, including two people disagreeing about the label -- that is a disagreement to
review, not a duplicate to silently collapse.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from curvevision.formats.base import ShapeRecord
from curvevision.services import matching
from curvevision.services.comparison import AREA_SHAPES, shape_iou

#: Below this similarity, two shapes on one frame are two objects rather than one. Inherited
#: from the upstream design as 1 - 0.25: a tuned constant, not a derived one. It is
#: deliberately far above the 0.5 used for *quality* scoring, because the questions differ --
#: "did this annotator find the object" tolerates a loose box, while "are these two people
#: describing one object" should not merge anything a reviewer would call two.
SIMILARITY_FLOOR = 0.75


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """What a merge did, so a caller can report it rather than guess."""

    #: Shapes dropped because another job had already described the same object.
    duplicates_removed: int = 0
    #: Frames on which at least one duplicate was found.
    frames_reconciled: int = 0
    #: Pairs of track ids found to describe one object across a seam.
    tracks_unified: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.duplicates_removed or self.tracks_unified)


def similarity(left: ShapeRecord, right: ShapeRecord) -> float:
    """How strongly two shapes claim to be the same object, in [0, 1].

    Zero whenever the label or the shape type differs: merging across either would pick one
    annotator's answer over the other's and destroy the evidence that they disagreed.
    """
    if left.label != right.label or left.shape_type is not right.shape_type:
        return 0.0
    if left.shape_type not in AREA_SHAPES:
        # A polyline, a point set or a skeleton encloses nothing, so IoU is meaningless for
        # it. Upstream returns 0 here too. Two of them are left as two objects rather than
        # merged on a guess -- a visible duplicate beats an invented match.
        return 0.0
    return shape_iou(left.shape_type, left.points, right.shape_type, right.points)


def _merge_one_frame(
    existing: list[ShapeRecord], incoming: Sequence[ShapeRecord]
) -> tuple[list[ShapeRecord], list[tuple[int, int]], int]:
    """Fold `incoming` into `existing` for a single frame.

    Returns the shapes to keep, the track-id pairs discovered to be one object, and how many
    incoming shapes were dropped as duplicates.
    """
    if not existing or not incoming:
        return [*existing, *incoming], [], 0

    cost = [[1.0 - similarity(new, old) for old in existing] for new in incoming]
    assignment = matching.solve(cost)

    kept: list[ShapeRecord] = []
    pairs: list[tuple[int, int]] = []
    dropped = 0
    for index, shape in enumerate(incoming):
        column = assignment[index]
        if column != matching.UNMATCHED and cost[index][column] <= 1.0 - SIMILARITY_FLOOR:
            # The same object, described twice. Keep the copy that was already there: it
            # came from the earlier job, which is the same tie-break upstream makes, and it
            # keeps a merge deterministic rather than dependent on job iteration order.
            partner = existing[column]
            if shape.track_id is not None and partner.track_id is not None:
                pairs.append((shape.track_id, partner.track_id))
            dropped += 1
            continue
        kept.append(shape)

    return [*existing, *kept], pairs, dropped


class _TrackAliases:
    """Union-find over track ids, so a track that crosses a seam keeps one identity.

    The overlap exists *to* keep tracks continuous across a job boundary. If the export then
    emits the seam's two halves under two different track ids, the feature has delivered
    nothing -- the duplicate is gone but the object still appears to vanish and be replaced.
    Merging per frame tells us which pairs of ids are really one object; this makes that
    stick across every frame either id appears on.
    """

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def union(self, left: int, right: int) -> bool:
        a, b = self.find(left), self.find(right)
        if a == b:
            return False
        # Lower id wins, so the surviving id is the one from the earlier job and the result
        # does not depend on the order pairs were discovered in.
        low, high = (a, b) if a < b else (b, a)
        self._parent[high] = low
        return True

    def find(self, value: int) -> int:
        root = value
        while self._parent.get(root, root) != root:
            root = self._parent[root]
        # Path compression, so a long seam does not become a long walk per lookup.
        while self._parent.get(value, value) != value:
            self._parent[value], value = root, self._parent[value]
        return root

    def __bool__(self) -> bool:
        return bool(self._parent)


def _unify_track_ids(by_frame: dict[int, list[ShapeRecord]], aliases: _TrackAliases) -> None:
    if not aliases:
        return
    for shapes in by_frame.values():
        for index, shape in enumerate(shapes):
            if shape.track_id is None:
                continue
            canonical = aliases.find(shape.track_id)
            if canonical != shape.track_id:
                shapes[index] = _with_track_id(shape, canonical)


def _with_track_id(shape: ShapeRecord, track_id: int) -> ShapeRecord:
    return ShapeRecord(
        label=shape.label,
        shape_type=shape.shape_type,
        points=list(shape.points),
        rotation=shape.rotation,
        occluded=shape.occluded,
        z_order=shape.z_order,
        group=shape.group,
        track_id=track_id,
        attributes=dict(shape.attributes),
        source=shape.source,
        confidence=shape.confidence,
        mask=shape.mask,
        elements=list(shape.elements),
    )


def merge_frames(
    accumulated: dict[int, list[ShapeRecord]],
    incoming: dict[int, list[ShapeRecord]],
    *,
    shared_frames: Iterable[int],
) -> MergeOutcome:
    """Fold one job's flattened shapes into the running total, in place.

    Only `shared_frames` are reconciled. A frame only one job covers cannot contain a
    cross-job duplicate, so comparing its shapes would be pure cost and — worse — would
    start merging two boxes one annotator deliberately drew close together, which is their
    business and not ours.
    """
    shared = set(shared_frames)
    aliases = _TrackAliases()
    dropped = 0
    reconciled = 0

    for frame, shapes in incoming.items():
        if frame not in shared or frame not in accumulated:
            accumulated.setdefault(frame, []).extend(shapes)
            continue
        kept, pairs, frame_dropped = _merge_one_frame(accumulated[frame], shapes)
        accumulated[frame] = kept
        for left, right in pairs:
            aliases.union(left, right)
        if frame_dropped:
            dropped += frame_dropped
            reconciled += 1

    unified = 0
    if aliases:
        renamed = {
            shape.track_id
            for shapes in accumulated.values()
            for shape in shapes
            if shape.track_id is not None and aliases.find(shape.track_id) != shape.track_id
        }
        unified = len(renamed)
        _unify_track_ids(accumulated, aliases)

    return MergeOutcome(
        duplicates_removed=dropped, frames_reconciled=reconciled, tracks_unified=unified
    )


def merge_tags(
    accumulated: dict[int, list[str]],
    incoming: dict[int, list[str]],
    *,
    shared_frames: Iterable[int],
) -> int:
    """Fold one job's tags in, dropping a tag a shared frame already carries.

    A tag is a label on a whole frame, so "the same tag twice" has no useful reading: two
    annotators both saying the frame is `daytime` is agreement, not two facts. Returns how
    many were dropped.
    """
    shared = set(shared_frames)
    dropped = 0
    for frame, names in incoming.items():
        target = accumulated.setdefault(frame, [])
        if frame not in shared:
            target.extend(names)
            continue
        for name in names:
            if name in target:
                dropped += 1
                continue
            target.append(name)
    return dropped


def overlapping_frames(ranges: Sequence[tuple[int, int]], candidate: tuple[int, int]) -> set[int]:
    """Frames `candidate` shares with any range already seen."""
    start, stop = candidate
    shared: set[int] = set()
    for other_start, other_stop in ranges:
        low, high = max(start, other_start), min(stop, other_stop)
        if high >= low:
            shared.update(range(low, high + 1))
    return shared


__all__ = [
    "SIMILARITY_FLOOR",
    "MergeOutcome",
    "merge_frames",
    "merge_tags",
    "overlapping_frames",
    "similarity",
]
